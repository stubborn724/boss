"""招聘 AI 对话状态的回归测试。"""

from pathlib import Path

from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore


def test_state_store_persists_compact_dialogue_snapshot(tmp_path: Path) -> None:
	"""状态只保存压缩事实和游标，供下一条消息构造 AI 输入。"""
	store = DialogueStateStore(tmp_path)
	state = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		facts={"city": "广州", "education": "本科"},
		conversation_summary="候选人愿意了解岗位。",
		last_assistant_message="请问您的 Java 项目经验有几年？",
		last_processed_message_id="message-7",
	)

	store.save(state)

	loaded = store.get("friend:42")
	assert loaded == state
	assert store.has_processed_message("friend:42", "message-7") is True
	assert store.has_processed_message("friend:42", "message-8") is False
	assert not (tmp_path / "recruiter" / "dialogue_states.json").read_text(encoding="utf-8").count("简历正文")


def test_map_for_job_reads_multiple_states_without_cross_job_leak(tmp_path: Path) -> None:
	"""批量读取对话账本只返回当前岗位候选人。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(candidate_key="job:java:friend:42", job_id="java"))
	store.save(CandidateDialogueState(candidate_key="job:support:friend:43", job_id="support"))

	assert set(store.map_for_job(job_id="java", friend_ids={42, 43})) == {42}


def test_recommendation_opening_rebinds_to_the_unique_returned_conversation(tmp_path: Path) -> None:
	"""推荐页打招呼后，回流会话必须保留推荐来源而不是重新当作普通沟通。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="recommendation:geek-42",
		job_id="job-1",
		stage=DialogueStage.OPENING_SENT,
		source="recommendation",
		source_reference="geek-42",
		candidate_name="张三",
		last_assistant_message="请问您是否能接受广州通勤？",
	))

	bound = store.bind_unique_recommendation(candidate_name="张三", friend_id=42)

	assert bound is not None
	assert bound.candidate_key == "job:job-1:friend:42"
	assert bound.source == "recommendation"
	assert bound.source_reference == "geek-42"
	assert bound.stage is DialogueStage.WAITING_CANDIDATE
	assert store.get("recommendation:geek-42") is None
	assert store.get("friend:42") == bound


def test_recommendation_opening_does_not_bind_when_name_is_ambiguous(tmp_path: Path) -> None:
	"""同名待回复候选人不能靠姓名猜测会话，必须继续等待平台的唯一映射。"""
	store = DialogueStateStore(tmp_path)
	for reference in ("geek-1", "geek-2"):
		store.save(CandidateDialogueState(
			candidate_key=f"recommendation:{reference}",
			job_id="job-1",
			stage=DialogueStage.OPENING_SENT,
			source="recommendation",
			source_reference=reference,
			candidate_name="张三",
		))

	assert store.bind_unique_recommendation(candidate_name="张三", friend_id=42) is None
	assert store.get("recommendation:geek-1") is not None
	assert store.get("recommendation:geek-2") is not None


def test_state_store_lists_only_the_requested_job_and_stage(tmp_path: Path) -> None:
	"""附件终审只能取得同一岗位中已完成双阶段问答的候选人。"""
	store = DialogueStateStore(tmp_path)
	ready = CandidateDialogueState(candidate_key="friend:1", job_id="job-1", stage=DialogueStage.READY_FOR_RESUME)
	store.save(ready)
	store.save(CandidateDialogueState(candidate_key="friend:2", job_id="job-1", stage=DialogueStage.WAITING_CANDIDATE))
	store.save(CandidateDialogueState(candidate_key="friend:3", job_id="job-2", stage=DialogueStage.READY_FOR_RESUME))

	assert store.list_by_job_stage(job_id="job-1", stage=DialogueStage.READY_FOR_RESUME) == [ready]

def test_dialogue_state_isolated_by_job_and_friend(tmp_path: Path) -> None:
	"""同一 BOSS 会话在不同岗位下必须保存两份独立状态。"""
	store = DialogueStateStore(tmp_path)
	java = CandidateDialogueState(
		candidate_key="job:java:friend:42",
		job_id="java",
		stage=DialogueStage.READY_FOR_RESUME,
	)
	support = CandidateDialogueState(
		candidate_key="job:support:friend:42",
		job_id="support",
		stage=DialogueStage.WAITING_CANDIDATE,
	)
	store.save(java)
	store.save(support)

	assert store.get_for_job(job_id="java", friend_id=42) == java
	assert store.get_for_job(job_id="support", friend_id=42) == support


def test_dialogue_state_only_uses_legacy_friend_key_for_matching_job(tmp_path: Path) -> None:
	"""历史键只可兼容原岗位，不能把旧 Java 状态借给售后岗位。"""
	store = DialogueStateStore(tmp_path)
	legacy = CandidateDialogueState(candidate_key="friend:42", job_id="java")
	store.save(legacy)

	assert store.get_for_job(job_id="java", friend_id=42) == legacy
	assert store.get_for_job(job_id="support", friend_id=42) is None


def test_recommendation_binding_is_scoped_to_job(tmp_path: Path) -> None:
	"""两个岗位出现同名推荐候选人时，只绑定当前岗位的回流会话。"""
	store = DialogueStateStore(tmp_path)
	for job_id in ("java", "support"):
		store.save(CandidateDialogueState(
			candidate_key=f"recommendation:{job_id}:张三",
			job_id=job_id,
			stage=DialogueStage.OPENING_SENT,
			source="recommendation",
			candidate_name="张三",
		))

	bound = store.bind_unique_recommendation(candidate_name="张三", friend_id=42, job_id="java")

	assert bound is not None
	assert bound.job_id == "java"
	assert bound.candidate_key == "job:java:friend:42"
	assert store.get("recommendation:support:张三") is not None
