"""沟通状态事实账本测试。"""

from pathlib import Path

from boss_agent_cli.commands.recruiter.conversation_state import ConversationStateStore


def test_conversation_state_isolated_by_job(tmp_path: Path) -> None:
	"""附件事实必须按岗位与会话隔离，旧数字键仍保持兼容。"""
	store = ConversationStateStore(tmp_path)
	store.mark_resume_request_sent(42, job_id="java", message="请发送附件简历。")

	assert store.has_resume_request_sent(42, job_id="java") is True
	assert store.has_resume_request_sent(42, job_id="support") is False
	assert store.has_resume_request_sent(42) is False


def test_sent_fact_survives_store_recreation_and_retry_cleanup(tmp_path: Path) -> None:
	"""重启后仍能判断已发送，且状态不依赖待重试队列是否存在。"""
	first = ConversationStateStore(tmp_path)
	first.mark_resume_request_sent(42, message="可以看看你的简历吗？")

	second = ConversationStateStore(tmp_path)
	assert second.has_resume_request_sent(42) is True
	assert second.status_map()[42]["status"] == "waiting_for_resume"


def test_resume_request_claim_is_atomic_and_reusable_after_delivery_failure(tmp_path: Path) -> None:
	"""多个入口同时处理同一候选人时只能有一个入口获得索要简历发送资格。"""
	store = ConversationStateStore(tmp_path)

	assert store.claim_resume_request(42) is True
	assert store.claim_resume_request(42) is False

	store.release_resume_request_claim(42)
	assert store.claim_resume_request(42) is True
	store.mark_resume_request_sent(42, message="感谢回复，请发送附件简历。")
	assert store.claim_resume_request(42) is False


def test_downloaded_resume_is_visible_as_reusable_local_state(tmp_path: Path) -> None:
	"""已下载文件和阶段写入后，控制台可展示已下载而不是等待发送。"""
	resume_path = tmp_path / "resume.md"
	resume_path.write_text("# 简历", encoding="utf-8")
	store = ConversationStateStore(tmp_path)
	store.mark_resume_downloaded(42, path=str(resume_path), kind="attachment")
	store.mark_analyzed(42, score=86, recommendation="invite_to_interview")

	status = store.status_map()[42]
	assert status["status"] == "analyzed"
	assert status["score"] == 86
	assert store.get(42)["resume_path"] == str(resume_path)


def test_get_many_reads_job_scoped_attachment_states(tmp_path: Path) -> None:
	"""批量读取仍按岗位隔离附件事实。"""
	store = ConversationStateStore(tmp_path)
	store.mark_resume_request_sent(42, job_id="java", message="请发送附件简历")
	store.mark_resume_request_sent(43, job_id="support", message="请发送附件简历")

	assert set(store.get_many({42, 43}, job_id="java")) == {42}


def test_conversation_state_replaces_unpaired_surrogate_before_persisting(tmp_path: Path) -> None:
	"""平台消息包含孤立代理字符时不能让候选人状态写入失败。"""
	store = ConversationStateStore(tmp_path)

	store.mark_resume_request_sent(42, message="请发送简历\ud83d")

	assert store.get(42)["resume_request_message"] == "请发送简历\ufffd"
	state_file = tmp_path / "recruiter" / "conversation_states.json"
	assert state_file.read_text(encoding="utf-8").encode("utf-8")
