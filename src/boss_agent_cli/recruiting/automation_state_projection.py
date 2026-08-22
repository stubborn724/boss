"""将既有沟通账本恢复到招聘自动化队列。

自动化队列是 Web 工作台的单一展示事实，而 AI 对话和附件终审分别在独立账本中
持久化。此模块只负责把这两份脱敏状态投影进队列，避免 ``web.py`` 直接解释领域
枚举，也避免历史已终审候选人在重新同步 BOSS 列表后被错误回退为“已同步”。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent_cli.recruiting.automation_coordinator import ConversationSeed
from boss_agent_cli.recruiting.automation_queue import (
	AutomationCandidate,
	AutomationCandidateStage,
	AutomationQueueStore,
)
from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage, InterviewPhase


def restore_candidate_from_persisted_states(
	*,
	queue: AutomationQueueStore,
	job_id: str,
	seed: ConversationSeed,
	dialogue_state: CandidateDialogueState | None,
	conversation_state: dict[str, Any],
) -> AutomationCandidate:
	"""恢复一位沟通列表候选人的历史阶段、来源与附件终审事实。

	BOSS 列表只提供候选人身份，不应覆盖本地已确认的处理结果。优先级为：真实且
	非空的附件终审最高，其次是 AI 对话阶段，最后才是普通同步状态。在线简历即使
	历史评分很高也只能进入人工复核，确保前端“合格候选人”永远满足附件约束。
	"""
	source = dialogue_state.source if dialogue_state is not None else seed.source
	candidate = queue.upsert_candidate(
		friend_id=seed.friend_id,
		job_id=job_id,
		candidate_name=seed.candidate_name or (dialogue_state.candidate_name if dialogue_state else ""),
		source=source if source in {"conversation", "recommendation"} else "conversation",
		stage=_dialogue_queue_stage(dialogue_state),
		last_message_id=seed.last_message_id,
		last_action="已同步 BOSS 沟通列表",
	)

	if str(conversation_state.get("stage") or "") != "analyzed":
		return candidate
	if str(conversation_state.get("resume_kind") or "") != "attachment":
		return _mark_manual_review(queue, candidate.candidate_key, "历史记录不是附件简历，待人工复核")

	resume_path = _non_empty_attachment_path(conversation_state.get("resume_path"))
	score = conversation_state.get("score")
	if resume_path is None or not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
		return _mark_manual_review(queue, candidate.candidate_key, "历史附件或终审评分不可用，待人工复核")
	return queue.record_final_review(
		friend_id=seed.friend_id,
		job_id=job_id,
		score=score,
		recommendation=str(conversation_state.get("recommendation") or "review"),
		resume_path=resume_path,
	)


def _dialogue_queue_stage(state: CandidateDialogueState | None) -> AutomationCandidateStage:
	"""把 AI 内部阶段映射为面向招聘人员的稳定工作台阶段。"""
	if state is None:
		return AutomationCandidateStage.SYNCED
	if state.stage is DialogueStage.REJECTED:
		return AutomationCandidateStage.HARD_REJECTED
	if state.stage in {DialogueStage.READY_FOR_RESUME, DialogueStage.RESUME_FINALIZING}:
		return AutomationCandidateStage.WAITING_ATTACHMENT
	if state.stage in {DialogueStage.MANUAL_REVIEW, DialogueStage.CANDIDATE_CREATED}:
		return AutomationCandidateStage.MANUAL_REVIEW
	if state.interview_phase is InterviewPhase.PROFESSIONAL:
		return AutomationCandidateStage.PROFESSIONAL_DIALOGUE
	if state.stage is DialogueStage.OPENING_SENT:
		return AutomationCandidateStage.BASIC_DIALOGUE
	if state.stage is DialogueStage.WAITING_CANDIDATE:
		return AutomationCandidateStage.WAITING_CANDIDATE
	return AutomationCandidateStage.SYNCED


def _non_empty_attachment_path(value: object) -> Path | None:
	"""仅接受实际存在且非空的本地附件，防止历史路径伪造合格候选人。"""
	if not isinstance(value, str) or not value.strip():
		return None
	path = Path(value)
	return path if path.is_file() and path.stat().st_size > 0 else None


def _mark_manual_review(queue: AutomationQueueStore, candidate_key: str, action: str) -> AutomationCandidate:
	"""将无法满足附件终审前提的历史记录明确留给人工，而非静默丢失。"""
	updated = queue.update_stage(candidate_key, stage=AutomationCandidateStage.MANUAL_REVIEW, last_action=action)
	if updated is None:
		raise RuntimeError("历史候选人未写入自动化队列")
	return updated
