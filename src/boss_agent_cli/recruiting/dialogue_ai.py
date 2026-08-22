"""招聘 AI 对话的紧凑提示词与结构化决策。

模型不接收完整聊天或简历，而是基于岗位压缩卡、已确认事实、上一条助手消息和
最新候选人消息做一次决策。这样每条新消息只消耗一次小请求，且输出能直接由
编排器保存为下一轮状态。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Callable

from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState
from boss_agent_cli.recruiting.models import JobProfile
from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value, sanitize_unicode_text


ChatFunction = Callable[[list[dict[str, str]]], str]
_ALLOWED_ACTIONS = {"continue", "reject", "ready_for_resume", "manual_review"}
_QUESTION_PHASES = {"basic", "professional", "resume", "none"}
_MAX_REPLY_LENGTH = 240
_FORBIDDEN_REPLY_TERMS = ("保证录用", "保证 offer", "保证offer", "保证加薪", "承诺录用")
_PROFESSIONAL_QUESTION_TERMS = (
	"项目", "系统", "架构", "设计", "开发", "代码", "接口", "数据库", "缓存", "并发", "算法", "性能", "排查",
)
_RESUME_DEFERRAL_TERMS = ("简历上", "简历里", "简历中", "看简历", "见简历")
_EMBEDDED_RESUME_REQUEST_RE = re.compile(
	r"(?:另外|同时|此外|还有)?[，,。；;\s]*(?:请|麻烦|方便)?(?:发送|提供|上传)(?:一份)?(?:附件)?简历[^。！？!?]*[。！？!?]?"
)


@dataclass(frozen=True)
class DialogueDecision:
	"""一次 AI 调用的受控输出，包含事实更新和唯一的下一步动作。"""

	facts: dict[str, str]
	candidate_questions: tuple[str, ...]
	answers_current_question: bool
	summary: str
	reply: str
	next_question_phase: str
	next_action: str
	reason: str


def _compact_job(job: JobProfile) -> dict[str, object]:
	"""只向模型提供首轮沟通所需岗位字段，避免传递工作台内部状态。"""
	return {
		"name": job.name,
		"city": job.city,
		"salary_range": job.salary_range,
		"education_requirement": job.education_requirement,
		"min_experience_years": job.min_experience_years,
		"industry": job.industry,
		"skills": list(job.skills),
		"must_have": list(job.criteria.must_have),
		"nice_to_have": list(job.criteria.nice_to_have),
		"reject_if": list(job.criteria.reject_if),
	}


def build_dialogue_messages(
	*,
	job: JobProfile,
	state: CandidateDialogueState,
	candidate_message: str,
) -> list[dict[str, str]]:
	"""构造无历史原文的短提示词，保证对话轮数不会线性放大 Token。"""
	system = (
		"你是招聘首轮沟通助手。只能依据候选人的明确表述更新事实；不要使用年龄、性别、"
		"婚育、民族、户籍等敏感属性。候选人可能同时回答问题和提问，二者必须同时识别。"
		"不得承诺录用、薪资、晋升或面试结果。基础核验应在一条简短消息中合并确认当前地点/到岗、在读或毕业时间和可投入时长；"
		"已在 confirmed_facts 中存在的学历、城市、专业和薪资不得再次询问。"
		f"当前面试阶段是 {state.interview_phase.value}；basic 阶段只能问通勤、经历、学历、稳定性等非专业问题，professional 阶段才能问岗位专业问题。"
		"basic 阶段只发一次合并核验；候选人未明确回答时转人工复核，不反复拆分追问。"
		"岗位专属专业核验：professional 阶段必须从当前岗位的 name、industry、skills、must_have 或 nice_to_have 中选择一个焦点，"
		"围绕候选人实际项目、职责、技术方案、问题排查和结果提出一条详细的情境化问题；不得复用与岗位无关的通用技术题，也不得对同一能力点多次追问。"
		"若本条消息明确回答了 professional 阶段当前问题且已无关键缺口，回复应礼貌请求候选人发送附件简历并把 next_action 设为 ready_for_resume。"
		"候选人说“简历上有”“见简历”等并不是专业回答，answers_current_question 必须为 false，继续追问一个具体项目职责。"
		"只返回 JSON：facts、candidate_questions、answers_current_question、summary、reply、next_question_phase、next_action、reason。"
		"answers_current_question 仅在候选人明确回答上一条招聘方待确认问题时为 true；"
		"候选人只提问、只寒暄或只发送附件时必须为 false。"
		"若 last_assistant_message 为空，说明本次是候选人首次来信，没有待回答问题，"
		"answers_current_question 必须为 false，并用 reply 发出一个基础问题。"
		"next_action 只能是 continue、reject、ready_for_resume、manual_review。"
		"next_question_phase 只能是 basic、professional、resume、none：continue 时只能选 basic 或 professional；"
		"ready_for_resume 时必须选 resume；reject 或 manual_review 时必须选 none。"
	)
	payload = {
		"job": _compact_job(job),
		"confirmed_facts": state.facts,
		"conversation_summary": sanitize_unicode_text(state.conversation_summary[:500]),
		"last_assistant_message": sanitize_unicode_text(state.last_assistant_message[:300]),
		"candidate_message": sanitize_unicode_text(candidate_message[:1000]),
	}
	return [
		{"role": "system", "content": system},
		{"role": "user", "content": json.dumps(sanitize_json_value(payload), ensure_ascii=False, separators=(",", ":"))},
	]


def _safe_text(value: object, *, limit: int = 500) -> str:
	"""把模型标量收敛为短文本，拒绝对象和空白值进入持久化状态。"""
	return value.strip()[:limit] if isinstance(value, str) else ""


def _manual_review(reason: str) -> DialogueDecision:
	"""解析或安全校验失败时生成无对外回复的人工复核结果。"""
	return DialogueDecision({}, (), False, "", "", "none", "manual_review", reason)


def _is_professional_question(reply: str, job: JobProfile) -> bool:
	"""本地校验专业题至少包含岗位技术语境，阻止通勤题被伪装为专业题。"""
	text = reply.casefold()
	job_terms = [job.name, *job.skills, *job.criteria.must_have]
	if any(term and term.casefold() in text for term in job_terms):
		return True
	return any(term in reply for term in _PROFESSIONAL_QUESTION_TERMS)


def _is_resume_deferral(message: str) -> bool:
	"""识别候选人把专业核验推给简历的简短回避回答。"""
	compact = message.replace(" ", "").replace("\n", "")
	return any(term in compact for term in _RESUME_DEFERRAL_TERMS)


def _professional_evidence_follow_up(job: JobProfile) -> str:
	"""在专业回答缺失时给出单维追问，不把附件当作专业能力证明。"""
	role = job.name or "该岗位"
	return f"为了先确认{role}的匹配度，请用一两句话说明您在一个相关项目中负责的具体工作。"


def _remove_embedded_resume_request(reply: str) -> str:
	"""移除专业问题后夹带的索简历句，保留一次完整的专业核验问题。

	基础核验允许在一条消息中合并多个条件，不能再按第一个问号截断；这里只
	针对附件简历请求做窄匹配，避免模型同时要求回答专业题和发送附件。
	"""
	return _EMBEDDED_RESUME_REQUEST_RE.sub("", reply).strip(" ，,；;")


def _parse_decision(raw: str) -> DialogueDecision:
	"""解析模型 JSON，并将未知字段和过长内容挡在领域边界外。"""
	try:
		value = json.loads(raw)
	except json.JSONDecodeError:
		return _manual_review("ai_response_not_json")
	if not isinstance(value, dict):
		return _manual_review("ai_response_not_object")
	facts = (
		{
			str(key).strip(): _safe_text(item, limit=120)
			for key, item in (value.get("facts") or {}).items()
			if isinstance(key, str) and key.strip() and _safe_text(item, limit=120)
		}
		if isinstance(value.get("facts"), dict)
		else {}
	)
	questions = (
		tuple(
			question for item in (value.get("candidate_questions") or []) if (question := _safe_text(item, limit=200))
		)
		if isinstance(value.get("candidate_questions"), list)
		else ()
	)
	reply = _safe_text(value.get("reply"), limit=_MAX_REPLY_LENGTH + 1)
	question_phase = _safe_text(value.get("next_question_phase"), limit=20)
	action = _safe_text(value.get("next_action"), limit=40)
	if action not in _ALLOWED_ACTIONS:
		return _manual_review("ai_action_invalid")
	if action == "continue" and not reply:
		return _manual_review("ai_reply_missing")
	if question_phase not in _QUESTION_PHASES:
		return _manual_review("ai_question_phase_invalid")
	if action == "continue" and question_phase not in {"basic", "professional"}:
		return _manual_review("ai_question_phase_invalid")
	if action == "ready_for_resume" and question_phase != "resume":
		return _manual_review("ai_question_phase_invalid")
	if action in {"reject", "manual_review"} and question_phase != "none":
		return _manual_review("ai_question_phase_invalid")
	if len(reply) > _MAX_REPLY_LENGTH or any(term in reply for term in _FORBIDDEN_REPLY_TERMS):
		return _manual_review("ai_reply_unsafe")
	return DialogueDecision(
		facts=facts,
		candidate_questions=questions,
		answers_current_question=value.get("answers_current_question") is True,
		summary=_safe_text(value.get("summary")),
		reply=reply,
		next_question_phase=question_phase,
		next_action=action,
		reason=_safe_text(value.get("reason")),
	)


def decide_dialogue_turn(
	chat: ChatFunction,
	*,
	job: JobProfile,
	state: CandidateDialogueState,
	candidate_message: str,
) -> DialogueDecision:
	"""执行一次受控 AI 判断；网络异常与格式异常统一转人工复核而不自动发送。"""
	try:
		raw = chat(build_dialogue_messages(job=job, state=state, candidate_message=candidate_message))
	except Exception:
		return _manual_review("ai_request_failed")
	decision = _parse_decision(raw)
	if state.interview_phase.value == "professional" and _is_resume_deferral(candidate_message):
		return replace(
			decision,
			facts={},
			answers_current_question=False,
			reply=_professional_evidence_follow_up(job),
			next_question_phase="professional",
			next_action="continue",
			reason="candidate_deferred_professional_answer_to_resume",
		)
	if decision.next_action == "continue":
		decision = replace(decision, reply=_remove_embedded_resume_request(decision.reply))
	if decision.next_question_phase == "professional" and not _is_professional_question(decision.reply, job):
		return _manual_review("ai_professional_question_mismatch")
	return decision
