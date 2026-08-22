"""招聘自动化候选人队列的持久化与展示契约。"""

from pathlib import Path

from boss_agent_cli.recruiting.automation_queue import (
	AutomationCandidateStage,
	AutomationCandidateUpsert,
	AutomationQueueStore,
)


def test_sync_preserves_recommendation_source_and_is_idempotent(tmp_path: Path) -> None:
	"""推荐候选人回流沟通列表后必须复用同一会话记录且保留来源。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=7,
		job_id="job-java",
		candidate_name="张三",
		source="recommendation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
	)
	store.upsert_candidate(
		friend_id=7,
		job_id="job-java",
		candidate_name="张三",
		source="conversation",
		stage=AutomationCandidateStage.BASIC_DIALOGUE,
	)

	rows = store.list_for_job("job-java")

	assert len(rows) == 1
	assert rows[0].source == "recommendation"
	assert rows[0].stage is AutomationCandidateStage.BASIC_DIALOGUE


def test_sync_backfills_empty_candidate_name_from_later_conversation_list(tmp_path: Path) -> None:
	"""旧队列缺姓名时，下一次正确同步必须回填真实姓名。

	早期 RPA 字段映射会把候选人写成空姓名，页面因此显示“未命名候选人”。
	队列同步应保留阶段事实，但允许新列表补齐身份展示字段。
	"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(friend_id=7, job_id="job-java", candidate_name="", source="conversation")

	candidate = store.upsert_candidate(friend_id=7, job_id="job-java", candidate_name="王文川", source="conversation")

	assert candidate.candidate_name == "王文川"


def test_queue_replaces_unpaired_surrogate_from_platform_metadata(tmp_path: Path) -> None:
	"""候选人卡片异常字符不能阻断整批队列的原子写入。"""
	store = AutomationQueueStore(tmp_path)

	candidate = store.upsert_candidate(
		friend_id=7,
		job_id="job-java",
		candidate_name="张三\ud83d",
		source="conversation",
		last_action="附件等待\udc00",
	)

	loaded = store.list_for_job("job-java")[0]
	assert candidate.candidate_name == "张三\ud83d"
	assert loaded.candidate_name == "张三\ufffd"
	assert loaded.last_action == "附件等待\ufffd"


def test_bulk_sync_reads_and_writes_queue_once(tmp_path: Path, monkeypatch) -> None:
	"""大批量同步必须一次合并后落盘，不能每个候选人重复写整份队列文件。"""
	store = AutomationQueueStore(tmp_path)
	write_calls = 0
	original_write = store._write

	def counted_write(candidates):
		nonlocal write_calls
		write_calls += 1
		original_write(candidates)

	monkeypatch.setattr(store, "_write", counted_write)

	result = store.upsert_candidates([
		AutomationCandidateUpsert(friend_id=1, job_id="job-java", candidate_name="张三", source="conversation"),
		AutomationCandidateUpsert(friend_id=2, job_id="job-java", candidate_name="李四", source="conversation"),
		AutomationCandidateUpsert(friend_id=3, job_id="job-java", candidate_name="王五", source="conversation"),
	])

	assert [candidate.friend_id for candidate in result] == [1, 2, 3]
	assert write_calls == 1
	assert len(store.list_for_job("job-java")) == 3


def test_qualified_candidates_are_sorted_by_score_and_require_verified_attachment(tmp_path: Path) -> None:
	"""合格列表只输出达标、非空附件且已终审的候选人，并按分数降序排列。"""
	store = AutomationQueueStore(tmp_path)
	first = tmp_path / "first.pdf"
	second = tmp_path / "second.pdf"
	first.write_bytes(b"first")
	second.write_bytes(b"second")
	store.upsert_candidate(friend_id=1, job_id="job-java", candidate_name="低分", source="conversation")
	store.upsert_candidate(friend_id=2, job_id="job-java", candidate_name="高分", source="conversation")
	store.upsert_candidate(friend_id=3, job_id="job-java", candidate_name="无附件", source="conversation")
	store.record_final_review(friend_id=1, job_id="job-java", score=72, recommendation="review", resume_path=first)
	store.record_final_review(friend_id=2, job_id="job-java", score=88, recommendation="invite_to_interview", resume_path=second)
	store.record_final_review(friend_id=3, job_id="job-java", score=96, recommendation="invite_to_interview", resume_path=tmp_path / "missing.pdf")

	qualified = store.snapshot("job-java", qualified_threshold=70)["qualified"]

	assert [item["candidate_name"] for item in qualified] == ["高分", "低分"]
	assert qualified[0]["resume_path"] == str(second)
	assert qualified[0]["stage"] == AutomationCandidateStage.ANALYZED.value


def test_snapshot_can_follow_current_boss_sync_order_without_historical_candidates(tmp_path: Path) -> None:
	"""自动化执行队列只展示本次 BOSS 快照，并保持平台当前展示顺序。

	队列文件是长期事实存储，历史候选人不能因为仍在本地就继续冒充当前
	BOSS 列表。执行页传入本次同步得到的会话顺序后，候选人和合格候选人
	都必须按该快照投影，跨岗位候选人池则继续走独立的全量读取入口。
	"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(friend_id=99, job_id="job-java", candidate_name="历史候选人", source="conversation")
	store.upsert_candidate(friend_id=3, job_id="job-java", candidate_name="谭金武", source="conversation")
	store.upsert_candidate(friend_id=1, job_id="job-java", candidate_name="许辉燃", source="conversation")

	snapshot = store.snapshot("job-java", qualified_threshold=70, visible_friend_ids=(3, 1))

	assert [row["candidate_name"] for row in snapshot["candidates"]] == ["谭金武", "许辉燃"]
	assert all(row["friend_id"] in {1, 3} for row in snapshot["candidates"])
	assert [row["candidate_name"] for row in snapshot["qualified"]] == []


def test_visible_snapshot_keeps_qualified_candidates_score_ordered(tmp_path: Path) -> None:
	"""当前快照只限定候选人范围，合格名单仍按终审分数排序。"""
	store = AutomationQueueStore(tmp_path)
	low_resume = tmp_path / "low.pdf"
	high_resume = tmp_path / "high.pdf"
	low_resume.write_bytes(b"low")
	high_resume.write_bytes(b"high")
	store.upsert_candidate(friend_id=1, job_id="job-java", candidate_name="低分", source="conversation")
	store.upsert_candidate(friend_id=2, job_id="job-java", candidate_name="高分", source="conversation")
	store.record_final_review(friend_id=1, job_id="job-java", score=71, recommendation="review", resume_path=low_resume)
	store.record_final_review(friend_id=2, job_id="job-java", score=90, recommendation="invite_to_interview", resume_path=high_resume)

	snapshot = store.snapshot("job-java", qualified_threshold=70, visible_friend_ids=(1, 2))

	assert [row["candidate_name"] for row in snapshot["candidates"]] == ["低分", "高分"]
	assert [row["candidate_name"] for row in snapshot["qualified"]] == ["高分", "低分"]


def test_verified_resume_path_rejects_non_attachment_and_unknown_candidate(tmp_path: Path) -> None:
	"""本地打开能力只能解析队列已确认的附件，不能成为任意文件读取入口。"""
	store = AutomationQueueStore(tmp_path)
	resume = tmp_path / "resume.pdf"
	resume.write_bytes(b"attachment")
	candidate = store.upsert_candidate(friend_id=11, job_id="job-java", candidate_name="王五", source="conversation")
	store.record_final_review(friend_id=11, job_id="job-java", score=80, recommendation="review", resume_path=resume)

	assert store.verified_resume_path(candidate.candidate_key) == resume
	assert store.verified_resume_path("job:job-java:friend:404") is None


def test_same_boss_conversation_keeps_independent_final_reviews_per_job(tmp_path: Path) -> None:
	"""同一会话转投多个岗位时，评分和附件不能被后一个岗位覆盖。"""
	store = AutomationQueueStore(tmp_path)
	java_resume = tmp_path / "java.pdf"
	sales_resume = tmp_path / "sales.pdf"
	java_resume.write_bytes(b"java attachment")
	sales_resume.write_bytes(b"sales attachment")

	java = store.upsert_candidate(friend_id=42, job_id="job-java", candidate_name="张三", source="conversation")
	sales = store.upsert_candidate(friend_id=42, job_id="job-sales", candidate_name="张三", source="conversation")
	store.record_final_review(friend_id=42, job_id="job-java", score=91, recommendation="invite_to_interview", resume_path=java_resume)
	store.record_final_review(friend_id=42, job_id="job-sales", score=76, recommendation="review", resume_path=sales_resume)

	assert java.candidate_key != sales.candidate_key
	assert [row["score"] for row in store.snapshot("job-java", qualified_threshold=70)["qualified"]] == [91]
	assert [row["score"] for row in store.snapshot("job-sales", qualified_threshold=70)["qualified"]] == [76]
	assert store.verified_resume_path(java.candidate_key) == java_resume
	assert store.verified_resume_path(sales.candidate_key) == sales_resume
