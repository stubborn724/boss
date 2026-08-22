"""自动化 AI 对话时间线的本地持久化契约。"""

from pathlib import Path

from boss_agent_cli.recruiting.dialogue_transcript import DialogueTranscriptStore


def test_transcript_keeps_processed_turns_ordered_and_job_scoped(tmp_path: Path) -> None:
	"""候选人消息与确认发送的 AI 回复应按轮次展示，且不得跨岗位串联。"""
	store = DialogueTranscriptStore(tmp_path)
	store.record_candidate_message(
		job_id="job-java",
		friend_id=42,
		message_id="candidate-1",
		text="我有三年 Java 开发经验。",
	)
	store.record_recruiter_reply(
		job_id="job-java",
		friend_id=42,
		message_id="candidate-1",
		text="请介绍一个您负责过的 Spring Boot 模块？",
	)
	# 轮询重试同一消息时不能在时间线制造重复气泡。
	store.record_candidate_message(
		job_id="job-java",
		friend_id=42,
		message_id="candidate-1",
		text="我有三年 Java 开发经验。",
	)

	assert [entry["role"] for entry in store.list_for_candidate(job_id="job-java", friend_id=42)] == ["candidate", "recruiter"]
	assert [entry["text"] for entry in store.list_for_candidate(job_id="job-java", friend_id=42)] == [
		"我有三年 Java 开发经验。",
		"请介绍一个您负责过的 Spring Boot 模块？",
	]
	assert store.list_for_candidate(job_id="job-sales", friend_id=42) == []


def test_transcript_replaces_unpaired_surrogate_from_boss_message(tmp_path: Path) -> None:
	"""候选人消息的异常 Unicode 不能阻断对话时间线落盘。"""
	store = DialogueTranscriptStore(tmp_path)

	store.record_candidate_message(job_id="job-java", friend_id=42, message_id="m1", text="回复\ud83d")

	assert store.list_for_candidate(job_id="job-java", friend_id=42)[0]["text"] == "回复\ufffd"
