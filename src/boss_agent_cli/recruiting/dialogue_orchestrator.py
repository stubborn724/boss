"""招聘 AI 对话状态编排器。

本模块把“读取最新消息、调用一次 AI、保存摘要、生成发送指令”串成可恢复的
纯应用服务。平台发送由命令层完成，因而测试可以验证重复轮询不会产生副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Callable, cast

from boss_agent_cli.recruiting.dialogue_ai import ChatFunction, decide_dialogue_turn
from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage, InterviewPhase
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore
from boss_agent_cli.recruiting.models import JobProfile


_LEGACY_BASIC_QUESTION_TERMS = (
	"通勤", "地铁", "居住", "所在区", "所在城市", "到岗", "实习时长", "实习多久", "学历",
)


@dataclass(frozen=True)
class DialogueTurnResult:
	"""一次消息处理的最小结果，供 RPA 层决定是否发送。"""

	state: CandidateDialogueState
	outbound_message: str = ""
	ignored_duplicate: bool = False
	should_finalize_resume: bool = False


class DialogueOrchestrator:
	"""按候选人串行推进 AI 对话，并限制每位候选人的 AI 回合数。"""

	def __init__(
		self,
		*,
		chat: ChatFunction,
		state_store: DialogueStateStore,
		job_loader: Callable[[str], JobProfile],
		max_ai_turns: int = 6,
	) -> None:
		self._chat = chat
		self._store = state_store
		self._job_loader = job_loader
		self._max_ai_turns = max_ai_turns

	def handle_candidate_message(
		self,
		*,
		state: CandidateDialogueState,
		message_id: str,
		message: str,
	) -> DialogueTurnResult:
		"""只处理新消息；重复消息直接返回，不消耗 AI 额度。"""
		state = self._recover_legacy_basic_phase(state)
		if self._store.has_processed_message(state.candidate_key, message_id):
			return DialogueTurnResult(state=state, ignored_duplicate=True)
		if state.ai_turn_count >= self._max_ai_turns:
			next_state = replace(
				state,
				stage=cast(DialogueStage, DialogueStage.MANUAL_REVIEW),
				last_processed_message_id=message_id,
			)
			self._store.save(next_state)
			return DialogueTurnResult(state=next_state)

		job = self._job_loader(state.job_id)
		if (education_result := self._education_mismatch_result(state=state, message_id=message_id, message=message, job=job)) is not None:
			self._store.save(education_result.state)
			return education_result
		if (location_result := self._location_mismatch_result(state=state, message_id=message_id, message=message, job=job)) is not None:
			self._store.save(location_result.state)
			return location_result
		if (basic_result := self._confirmed_basic_result(state=state, message_id=message_id, message=message, job=job)) is not None:
			self._store.save(basic_result.state)
			return basic_result
		if (
			state.interview_phase is InterviewPhase.PROFESSIONAL
			and _has_substantive_professional_answer(message)
		):
			# 专业阶段只收集一条达到事实量门槛的项目回答。不能再依赖上一条
			# 问题是否恰好包含“方案/问题/结果”等词，因为历史会话中的首道
			# 专业题可能较短，但候选人已经主动补齐了职责、技术栈和结果。
			# 固定话术直接索要附件，避免二次专业追问，也不把冗长夸奖交给模型。
			next_state = replace(
				state,
				stage=cast(DialogueStage, DialogueStage.READY_FOR_RESUME),
				last_assistant_message=_RESUME_REQUEST_MESSAGE,
				last_processed_message_id=message_id,
				professional_reply_count=state.professional_reply_count + 1,
			)
			self._store.save(next_state)
			return DialogueTurnResult(
				state=next_state,
				outbound_message=_RESUME_REQUEST_MESSAGE,
				should_finalize_resume=True,
			)
		decision = decide_dialogue_turn(self._chat, job=job, state=state, candidate_message=message)
		# 只有候选人明确回答了当前待确认问题才消耗阶段计数。候选人反问、
		# 寒暄或附件上传都可能需要回复，但绝不能被误记为已完成问答。
		basic_count = state.basic_reply_count
		professional_count = state.professional_reply_count
		phase: InterviewPhase = state.interview_phase
		if decision.answers_current_question:
			if phase is InterviewPhase.BASIC:
				basic_count += 1
			elif phase is InterviewPhase.PROFESSIONAL:
				professional_count += 1
		if decision.next_action == "continue":
			requested_phase = InterviewPhase(decision.next_question_phase)
			if phase is InterviewPhase.PROFESSIONAL and requested_phase is InterviewPhase.BASIC:
				decision = replace(decision, next_action="manual_review", reply="", reason="ai_phase_regression")
			elif requested_phase is InterviewPhase.PROFESSIONAL:
				# 进入专业阶段时才需要验证基础题已完成；已经在专业阶段的候选人可以
				# 因回答不够具体继续追问，不能把正常的第二道专业题误判为越级。
				if phase is InterviewPhase.BASIC:
					if not decision.answers_current_question or basic_count < 1:
						decision = replace(decision, next_action="manual_review", reply="", reason="ai_phase_premature")
					else:
						phase = InterviewPhase.PROFESSIONAL
			elif requested_phase is InterviewPhase.BASIC:
				phase = InterviewPhase.BASIC
		if decision.next_action == "ready_for_resume":
			# 索简历只能由上方经过本地验证的专业回答触发。模型即使把英语证书、
			# 到岗或实习时长误判为专业能力，也只能被收敛回唯一的完整专业题。
			decision = replace(
				decision,
				reply=_professional_question(job),
				next_question_phase="professional",
				next_action="continue",
				reason="professional_evidence_required_before_resume",
			)
		if (
			phase is InterviewPhase.BASIC
			and state.basic_reply_count >= 1
			and decision.answers_current_question
			and decision.next_action == "continue"
			and decision.next_question_phase == "basic"
		):
			# 基础核验是一次合并问题，不允许模型在已有明确回答后继续拆成
			# “城市 -> 通勤 -> 到岗”等多个回合。这里在本地强制切换专业阶段，
			# 保证阶段顺序不受模型偶发的重复提问影响，也不会提前索要简历。
			decision = replace(
				decision,
				reply=_professional_question(job),
				next_question_phase="professional",
				reason="basic_question_already_answered",
			)
			phase = InterviewPhase.PROFESSIONAL
		next_stage = cast(DialogueStage, {
			"continue": DialogueStage.WAITING_CANDIDATE,
			"reject": DialogueStage.REJECTED,
			"ready_for_resume": DialogueStage.READY_FOR_RESUME,
			"manual_review": DialogueStage.MANUAL_REVIEW,
		}[decision.next_action])
		next_state = replace(
			state,
			stage=next_stage,
			facts={**state.facts, **decision.facts},
			conversation_summary=decision.summary or state.conversation_summary,
			last_assistant_message=decision.reply,
			last_processed_message_id=message_id,
			ai_turn_count=state.ai_turn_count + 1,
			interview_phase=phase,
			basic_reply_count=basic_count,
			professional_reply_count=professional_count,
		)
		self._store.save(next_state)
		return DialogueTurnResult(
			state=next_state,
			outbound_message=decision.reply if next_stage is not DialogueStage.MANUAL_REVIEW else "",
			should_finalize_resume=next_stage is DialogueStage.READY_FOR_RESUME,
		)

	@staticmethod
	def _confirmed_basic_result(
		*,
		state: CandidateDialogueState,
		message_id: str,
		message: str,
		job: JobProfile,
	) -> DialogueTurnResult | None:
		"""将明确完成的合并基础核验直接推进到详细专业问题。

		候选人已明确说可以到岗位城市、已毕业或可尽快到岗时，再调用模型拆分
		学历和通勤问题只会增加成本。这里严格限定为当前版本的合并基础话术，
		并要求回复同时包含城市和到岗时间证据；模糊的迁移意愿不会被误判。
		"""
		if state.interview_phase is not InterviewPhase.BASIC:
			return None
		if not _is_combined_basic_question(state.last_assistant_message):
			return None
		if not _confirms_availability(message, job.city):
			return None
		question = _professional_question(job)
		next_state = replace(
			state,
			stage=cast(DialogueStage, DialogueStage.WAITING_CANDIDATE),
			facts={**state.facts, "availability": f"可到{job.city}并尽快到岗" if job.city else "已确认可到岗"},
			last_assistant_message=question,
			last_processed_message_id=message_id,
			interview_phase=cast(InterviewPhase, InterviewPhase.PROFESSIONAL),
			basic_reply_count=state.basic_reply_count + 1,
		)
		return DialogueTurnResult(state=next_state, outbound_message=question)

	@staticmethod
	def _education_mismatch_result(
		*,
		state: CandidateDialogueState,
		message_id: str,
		message: str,
		job: JobProfile,
	) -> DialogueTurnResult | None:
		"""处理候选人主动说明的学历不符，避免继续消耗 AI 和沟通额度。"""
		minimum_degree = _required_degree(job.education_requirement)
		if not minimum_degree or not _message_conflicts_with_degree(message, minimum_degree):
			return None
		outbound = f"感谢您的坦诚沟通。该岗位学历要求为{minimum_degree}，本次沟通先到这里，祝您求职顺利。"
		next_state = replace(
			state,
			stage=cast(DialogueStage, DialogueStage.REJECTED),
			facts={**state.facts, "education_screening": f"未满足{minimum_degree}学历要求"},
			last_assistant_message=outbound,
			last_processed_message_id=message_id,
		)
		return DialogueTurnResult(state=next_state, outbound_message=outbound)

	@staticmethod
	def _location_mismatch_result(
		*,
		state: CandidateDialogueState,
		message_id: str,
		message: str,
		job: JobProfile,
	) -> DialogueTurnResult | None:
		"""对明确未满足岗位城市到岗条件的回复直接礼貌结束，不调用 AI。"""
		if not job.city or not _is_location_question(state.last_assistant_message):
			return None
		if _confirms_availability(message, job.city):
			return None
		# 本地拒绝只处理“我在福州/安徽滁州”等明确城市事实。区域名、园区名、
		# 通勤时长和“考虑一下”等回答仍交由当前问题和 AI 判断，不能把信息不足
		# 误判为异地而提前结束正常候选人的沟通。
		if not _explicit_non_job_city(message, job.city):
			return None
		outbound = f"感谢您的回复。由于该岗位目前需要能在{job.city}稳定到岗，本次沟通先到这里，祝您求职顺利。"
		next_state = replace(
			state,
			stage=cast(DialogueStage, DialogueStage.REJECTED),
			facts={**state.facts, "location_screening": f"未明确满足{job.city}到岗条件"},
			last_assistant_message=outbound,
			last_processed_message_id=message_id,
		)
		return DialogueTurnResult(state=next_state, outbound_message=outbound)

	@staticmethod
	def _recover_legacy_basic_phase(state: CandidateDialogueState) -> CandidateDialogueState:
		"""把旧版提前切换到专业阶段的待答基础题恢复为正确阶段。

		早期状态机在收到任意基础回答后立即写入 ``professional``，即使刚发出的
		下一题仍是通勤等基础问题。只有尚无专业回答、且最后一条招聘方话术出现
		明确基础维度时才恢复，避免改写已经真实完成的专业问答。
		"""
		if state.interview_phase is not InterviewPhase.PROFESSIONAL:
			return state
		if state.professional_reply_count > 0:
			return state
		question = state.last_assistant_message
		if not any(term in question for term in _LEGACY_BASIC_QUESTION_TERMS):
			return state
		return replace(state, interview_phase=cast(InterviewPhase, InterviewPhase.BASIC))


def _is_combined_basic_question(message: str) -> bool:
	"""识别当前版本的合并基础核验话术，避免影响历史单题会话。"""
	compact = re.sub(r"\s+", "", message)
	return "到岗" in compact and ("在读" in compact or "毕业" in compact) and ("时长" in compact or "尽快" in compact or "每周" in compact)


_DEGREE_RANK = {"高中": 1, "中专": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}
_RESUME_REQUEST_MESSAGE = "感谢回复，请发送附件简历。"


def _required_degree(requirement: str) -> str:
	"""从岗位展示文案提取最低学历，兼容“本科及以上”等表达。"""
	found = [degree for degree in _DEGREE_RANK if degree in requirement]
	return min(found, key=_DEGREE_RANK.__getitem__) if found else ""


def _message_conflicts_with_degree(message: str, minimum_degree: str) -> bool:
	"""识别候选人明确学历反证，模糊表述始终交给人工而非自动淘汰。"""
	compact = re.sub(r"\s+", "", message)
	if minimum_degree == "本科" and any(term in compact for term in ("不是本科", "非本科", "没本科", "无本科")):
		return True
	candidate_degree = next((degree for degree in _DEGREE_RANK if degree in compact), "")
	return bool(candidate_degree and _DEGREE_RANK[candidate_degree] < _DEGREE_RANK[minimum_degree])


def _professional_question(job: JobProfile) -> str:
	"""生成带自然过渡的唯一完整专业题，一次取得四类专业证据。"""
	role = job.name or "该岗位"
	focus = next((skill for skill in job.skills if skill.strip()), role)
	return (
		f"感谢您的确认。接下来想了解一下您的技术实践，请结合一个与{role}相关的项目，说明您负责的核心模块、"
		f"使用{focus}时的技术方案、遇到的具体问题及最终处理结果。"
	)


def _confirms_availability(message: str, city: str) -> bool:
	"""只接受城市与可到岗时间均明确的回复，拒绝“合适可以过去”等模糊承诺。"""
	compact = re.sub(r"\s+", "", message)
	if city and city not in compact:
		return False
	city_confirmed = any(term in compact for term in (f"到{city}", f"在{city}", f"去{city}")) if city else True
	# “六月毕业”“毕业后可到岗”同样给出了可安排的到岗时间，不应因为缺少
	# “已”字而重复追问。仍要求同时出现岗位城市，避免把孤立的毕业年份放行。
	timing_confirmed = any(term in compact for term in ("已毕业", "毕业了", "毕业", "尽快到岗", "随时到岗", "可以到岗"))
	return city_confirmed and timing_confirmed


def _is_location_question(message: str) -> bool:
	"""识别当前是否正在确认城市或到岗，避免无关消息触发本地结束规则。"""
	return any(term in message for term in ("城市", "到岗", "通勤", "长期", "在广州", "到广州"))


def _explicit_non_job_city(message: str, job_city: str) -> bool:
	"""识别候选人明确陈述的异地城市，拒绝模糊区域和纯通勤时长。

	城市实体要求至少包含“市/州”等行政后缀，或使用常见的“在福州”形式。
	该规则故意保守：无法确定所在地时宁可继续当前问答，也不误结束沟通。
	"""
	compact = re.sub(r"\s+", "", message)
	if job_city in compact and not any(term in compact for term in (f"不在{job_city}", f"不能到{job_city}", f"无法到{job_city}")):
		return False
	explicit_city = re.search(r"(?:目前)?(?:在|住在|位于|来自)([\u4e00-\u9fff]{2,8}(?:市|州))", compact)
	if explicit_city is not None:
		return explicit_city.group(1) != job_city
	# 平台沟通常省略“市”，这里仅接受常见城市名称，避免“龙洞”等区名命中。
	known_city = re.search(r"(?:目前)?(?:在|住在|位于|来自)(北京|上海|广州|深圳|福州|杭州|南京|武汉|成都|重庆|长沙|西安|苏州|天津|厦门|滁州)", compact)
	return known_city is not None and known_city.group(1) != job_city


def _has_substantive_professional_answer(message: str) -> bool:
	"""判断专业回复是否包含足以结束同一能力点追问的最小事实量。

	旧实现要求命中两个固定词，容易把“Java/Spring 项目、接口开发、缓存接入”
	这类真实回答误判为不完整。这里按“行动、技术/场景、问题/方案”三类事实
	判断，候选人只要给出两类且达到最低长度，就直接进入索要简历阶段。
	"""
	compact = re.sub(r"\s+", "", message)
	if len(compact) < 20:
		return False
	action_terms = ("负责", "开发", "实现", "设计", "维护", "参与", "使用", "搭建", "编写", "接入")
	technical_terms = (
		"项目", "系统", "模块", "接口", "数据库", "缓存", "架构", "代码", "脚本", "服务",
		"java", "spring", "mysql", "redis", "linux", "ebpf", "python", "go", "c++", "sql",
	)
	problem_terms = ("问题", "故障", "异常", "排查", "解决", "处理", "优化", "性能", "稳定性", "方案")
	groups = (
		any(term in compact.casefold() for term in action_terms),
		any(term in compact.casefold() for term in technical_terms),
		any(term in compact for term in problem_terms),
	)
	return sum(groups) >= 2
