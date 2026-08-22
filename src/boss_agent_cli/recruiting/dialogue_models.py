"""招聘 AI 对话的纯领域模型。

本模块刻意不依赖 Click、RPA 或 AI 客户端。它只描述每位候选人当前已确认的
职业事实和处理阶段，保证后续每轮模型调用读取的是紧凑状态，而不是完整聊天。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DialogueStage(StrEnum):
	"""候选人对话阶段，阶段变化只能由编排器显式推进。"""

	HARD_SCREENING = "hard_screening"
	OPENING_SENT = "opening_sent"
	WAITING_CANDIDATE = "waiting_candidate"
	READY_FOR_RESUME = "ready_for_resume"
	RESUME_FINALIZING = "resume_finalizing"
	REJECTED = "rejected"
	MANUAL_REVIEW = "manual_review"
	CANDIDATE_CREATED = "candidate_created"


class InterviewPhase(StrEnum):
	"""AI 面试阶段，基础信息与专业能力必须分开完成。"""

	BASIC = "basic"
	PROFESSIONAL = "professional"


@dataclass(frozen=True)
class CandidateDialogueState:
	"""一位候选人的最小对话记忆。

	``facts`` 和 ``conversation_summary`` 是模型可读取的压缩上下文；原始聊天和
	简历文本不属于状态账本，避免每轮请求重复传输个人信息和无关 Token。
	"""

	candidate_key: str
	job_id: str
	stage: DialogueStage = DialogueStage.HARD_SCREENING
	facts: dict[str, str] = field(default_factory=dict)
	conversation_summary: str = ""
	last_assistant_message: str = ""
	last_processed_message_id: str = ""
	ai_turn_count: int = 0
	interview_phase: InterviewPhase = InterviewPhase.BASIC
	basic_reply_count: int = 0
	professional_reply_count: int = 0
	scores: dict[str, int] = field(default_factory=dict)
	# 仅保存入口和平台定位摘要，使推荐牛人与沟通列表两条链路可追溯。
	source: str = "conversation"
	source_reference: str = ""
	candidate_name: str = ""
	# 发送后的等待时间用于异步调度，不作为自动淘汰依据。
	waiting_since: str = ""
	# 沟通列表卡片生成的不可逆版本摘要。它不保存消息正文，只用于补偿 BOSS
	# 在人工点开会话后清除未读红点的情况，避免候选人新回复被等待状态吞掉。
	conversation_version: str = ""

	def to_dict(self) -> dict[str, object]:
		"""将受控字段投影为可原子落盘的 JSON，不接受调用方扩展字段。"""
		return {
			"candidate_key": self.candidate_key,
			"job_id": self.job_id,
			"stage": self.stage.value,
			"facts": dict(self.facts),
			"conversation_summary": self.conversation_summary,
			"last_assistant_message": self.last_assistant_message,
			"last_processed_message_id": self.last_processed_message_id,
			"ai_turn_count": self.ai_turn_count,
			"interview_phase": self.interview_phase.value,
			"basic_reply_count": self.basic_reply_count,
			"professional_reply_count": self.professional_reply_count,
			"scores": dict(self.scores),
			"source": self.source,
			"source_reference": self.source_reference,
			"candidate_name": self.candidate_name,
			"waiting_since": self.waiting_since,
			"conversation_version": self.conversation_version,
		}

	@classmethod
	def from_dict(cls, value: object) -> CandidateDialogueState | None:
		"""从历史 JSON 恢复状态；损坏字段退化为空而不阻断其它候选人。"""
		if not isinstance(value, dict):
			return None
		candidate_key = str(value.get("candidate_key") or "").strip()
		job_id = str(value.get("job_id") or "").strip()
		if not candidate_key or not job_id:
			return None
		try:
			stage = DialogueStage(str(value.get("stage") or DialogueStage.HARD_SCREENING))
		except ValueError:
			stage = DialogueStage.HARD_SCREENING
		try:
			phase = InterviewPhase(str(value.get("interview_phase") or InterviewPhase.BASIC))
		except ValueError:
			phase = InterviewPhase.BASIC
		raw_facts = value.get("facts")
		facts = {
			str(key): str(item)
			for key, item in raw_facts.items()
			if isinstance(key, str) and isinstance(item, str) and key.strip() and item.strip()
		} if isinstance(raw_facts, dict) else {}
		turns = value.get("ai_turn_count")
		return cls(
			candidate_key=candidate_key,
			job_id=job_id,
			stage=stage,
			facts=facts,
			conversation_summary=str(value.get("conversation_summary") or ""),
			last_assistant_message=str(value.get("last_assistant_message") or ""),
			last_processed_message_id=str(value.get("last_processed_message_id") or ""),
			ai_turn_count=turns if isinstance(turns, int) and turns >= 0 else 0,
			interview_phase=phase,
			basic_reply_count=_safe_nonnegative_int(value.get("basic_reply_count")),
			professional_reply_count=_safe_nonnegative_int(value.get("professional_reply_count")),
			scores={str(key): item for key, item in (value.get("scores") or {}).items() if isinstance(key, str) and isinstance(item, int) and 0 <= item <= 100} if isinstance(value.get("scores"), dict) else {},
			source=_safe_source(value.get("source")),
			source_reference=str(value.get("source_reference") or "").strip()[:200],
			candidate_name=str(value.get("candidate_name") or "").strip()[:120],
			waiting_since=str(value.get("waiting_since") or "").strip()[:64],
			conversation_version=str(value.get("conversation_version") or "").strip()[:128],
		)


def _safe_nonnegative_int(value: object) -> int:
	"""恢复计数时拒绝负数和布尔值，兼容旧状态文件。"""
	return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_source(value: object) -> str:
	"""将历史或损坏数据收敛到受支持的招聘入口。"""
	return str(value) if value in {"conversation", "recommendation"} else "conversation"
