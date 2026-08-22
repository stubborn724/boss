"""招聘自动化历史状态投影测试。"""

from pathlib import Path

from boss_agent_cli.recruiting.automation_coordinator import ConversationSeed
from boss_agent_cli.recruiting.automation_queue import (
	AutomationCandidateStage,
	AutomationQueueStore,
)
from boss_agent_cli.recruiting.automation_state_projection import (
	restore_candidate_from_persisted_states,
)
from boss_agent_cli.recruiting.dialogue_models import (
	CandidateDialogueState,
	DialogueStage,
	InterviewPhase,
)


def test_restore_analyzed_attachment_preserves_recommendation_source_and_score_order(tmp_path: Path) -> None:
	"""历史附件终审必须进入合格列表，并保留推荐入口和终审分数。"""
	queue = AutomationQueueStore(tmp_path)
	high_score_resume = tmp_path / "jiang.pdf"
	high_score_resume.write_bytes(b"jiang attachment")
	low_score_resume = tmp_path / "lin.pdf"
	low_score_resume.write_bytes(b"lin attachment")

	restore_candidate_from_persisted_states(
		queue=queue,
		job_id="job-java",
		seed=ConversationSeed(friend_id=101, candidate_name="江万粮"),
		dialogue_state=CandidateDialogueState(
			candidate_key="friend:101",
			job_id="job-java",
			candidate_name="江万粮",
			source="recommendation",
			stage=DialogueStage.CANDIDATE_CREATED,
			interview_phase=InterviewPhase.PROFESSIONAL,
		),
		conversation_state={
			"stage": "analyzed",
			"resume_kind": "attachment",
			"resume_path": str(high_score_resume),
			"score": 78,
			"recommendation": "invite_to_interview",
		},
	)
	restore_candidate_from_persisted_states(
		queue=queue,
		job_id="job-java",
		seed=ConversationSeed(friend_id=102, candidate_name="林煜升"),
		dialogue_state=CandidateDialogueState(
			candidate_key="friend:102",
			job_id="job-java",
			candidate_name="林煜升",
		),
		conversation_state={
			"stage": "analyzed",
			"resume_kind": "attachment",
			"resume_path": str(low_score_resume),
			"score": 75,
			"recommendation": "review",
		},
	)

	snapshot = queue.snapshot("job-java", qualified_threshold=70)

	assert [candidate["candidate_name"] for candidate in snapshot["qualified"]] == ["江万粮", "林煜升"]
	assert snapshot["qualified"][0]["source"] == "recommendation"
	assert snapshot["qualified"][0]["resume_path"] == str(high_score_resume.resolve())


def test_restore_online_resume_never_becomes_analyzed_candidate(tmp_path: Path) -> None:
	"""在线简历历史记录不得被迁移成附件终审完成状态。"""
	queue = AutomationQueueStore(tmp_path)

	candidate = restore_candidate_from_persisted_states(
		queue=queue,
		job_id="job-java",
		seed=ConversationSeed(friend_id=103, candidate_name="历史在线简历"),
		dialogue_state=None,
		conversation_state={
			"stage": "analyzed",
			"resume_kind": "online",
			"resume_path": str(tmp_path / "online.md"),
			"score": 99,
			"recommendation": "invite_to_interview",
		},
	)

	assert candidate.stage is AutomationCandidateStage.MANUAL_REVIEW
	assert queue.snapshot("job-java", qualified_threshold=70)["qualified"] == []
