"""招聘 AI 对话编排的幂等测试。"""

import json
from pathlib import Path

from boss_agent_cli.recruiting.assessment import parse_natural_language_criteria
from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage, InterviewPhase
from boss_agent_cli.recruiting.dialogue_orchestrator import DialogueOrchestrator
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore
from boss_agent_cli.recruiting.models import JobProfile


def test_orchestrator_processes_a_new_message_only_once(tmp_path: Path) -> None:
	"""同一平台消息重复出现时不能重复调用 AI 或重复发送。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", criteria=criteria)
	calls = 0

	def chat(_messages):
		nonlocal calls
		calls += 1
		return json.dumps(
			{
				"facts": {"city": "广州"},
				"candidate_questions": [],
				"summary": "已确认城市。",
				"reply": "请问您有几年 Java 项目经验？",
				"answers_current_question": True,
				"next_question_phase": "professional",
				"next_action": "continue",
				"reason": "继续确认经验",
			},
			ensure_ascii=False,
		)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(candidate_key="friend:42", job_id="job-1", stage=DialogueStage.WAITING_CANDIDATE)
	first = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="我在广州")
	second = orchestrator.handle_candidate_message(state=first.state, message_id="m-1", message="我在广州")

	assert first.outbound_message == "请问您有几年 Java 项目经验？"
	assert second.ignored_duplicate is True
	assert calls == 1
	assert first.state.interview_phase is InterviewPhase.PROFESSIONAL
	assert first.state.basic_reply_count == 1


def test_orchestrator_does_not_count_a_candidate_question_as_pending_answer(tmp_path: Path) -> None:
	"""候选人只问反问题时，不能被误判为完成基础问题。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", criteria=criteria)

	def chat(_messages):
		return json.dumps(
			{
				"facts": {},
				"candidate_questions": ["薪资范围是多少？"],
				"summary": "候选人询问薪资，尚未回答通勤问题。",
				"reply": "这个岗位薪资会结合经验沟通。请问您目前所在区或附近地铁站是哪里？",
				"answers_current_question": False,
				"next_question_phase": "basic",
				"next_action": "continue",
				"reason": "先回答候选人问题，再继续确认通勤",
			},
			ensure_ascii=False,
		)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(candidate_key="friend:43", job_id="job-1", stage=DialogueStage.WAITING_CANDIDATE)
	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="薪资范围是多少？")

	assert result.state.basic_reply_count == 0
	assert result.state.professional_reply_count == 0
	assert result.state.interview_phase is InterviewPhase.BASIC
	assert result.state.stage is DialogueStage.WAITING_CANDIDATE


def test_orchestrator_keeps_basic_phase_for_another_basic_question(tmp_path: Path) -> None:
	"""候选人回答所在地后仍需补问通勤时，不能提前进入专业阶段。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", criteria=criteria)

	def chat(_messages):
		return json.dumps(
			{
				"facts": {"location": "龙洞"},
				"candidate_questions": [],
				"summary": "已确认候选人所在区域，仍需确认通勤。",
				"reply": "请问您从龙洞到实习地点通勤时间大概多久？",
				"answers_current_question": True,
				"next_question_phase": "basic",
				"next_action": "continue",
				"reason": "继续完成基础核验",
			},
			ensure_ascii=False,
		)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:44",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请问您目前所在的城市是哪里？",
	)

	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="我在龙洞")

	assert result.outbound_message.endswith("大概多久？")
	assert result.state.basic_reply_count == 1
	assert result.state.professional_reply_count == 0
	assert result.state.interview_phase is InterviewPhase.BASIC


def test_orchestrator_promotes_confirmed_basic_reply_without_another_ai_call(tmp_path: Path) -> None:
	"""已一次确认到岗与学业条件后，应直接发详细专业题，不能继续消耗 AI 追问基础信息。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", skills=["Java", "Spring Boot"], criteria=criteria)
	calls = 0

	def chat(_messages):
		nonlocal calls
		calls += 1
		raise AssertionError("基础条件已明确时不应再调用 AI")

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:55",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请确认您是否可到广州到岗、当前是否在读及可投入时长。",
		facts={"education": "本科"},
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="可以到广州，已经毕业，可以尽快到岗。",
	)

	assert calls == 0
	assert result.state.interview_phase is InterviewPhase.PROFESSIONAL
	assert "Java" in result.outbound_message
	assert "负责" in result.outbound_message
	assert result.state.facts["availability"] == "可到广州并尽快到岗"
	assert result.outbound_message.startswith("感谢您的确认。接下来想了解一下您的技术实践，")


def test_orchestrator_ends_explicit_nonlocal_reply_without_ai_call(tmp_path: Path) -> None:
	"""明确在异地且未确认可到岗时应直接结束，不能反复追问城市。"""
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州")
	calls = 0

	def chat(_messages):
		nonlocal calls
		calls += 1
		raise AssertionError("明确城市不符时不应调用 AI")

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:56",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请问您目前所在的城市是哪里？该岗位需要在广州稳定到岗。",
	)

	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="我在安徽滁州。")

	assert calls == 0
	assert result.state.stage is DialogueStage.REJECTED
	assert "广州" in result.outbound_message


def test_orchestrator_requests_resume_after_one_professional_answer(tmp_path: Path) -> None:
	"""同一专业能力点拿到一次回答后应进入简历终审，不能继续多轮追问。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", criteria=criteria)

	def chat(_messages):
		return json.dumps({
			"facts": {"project": "Spring Boot 项目"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "候选人已说明项目职责。",
			"reply": "请再补充缓存一致性如何处理？",
			"next_question_phase": "professional",
			"next_action": "continue",
			"reason": "继续追问",
		}, ensure_ascii=False)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:57",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请说明一个 Java 项目的职责、技术方案、问题和结果。",
	)

	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="我负责订单模块，使用 Spring Boot 和 Redis，处理了缓存穿透。")

	assert result.state.stage is DialogueStage.READY_FOR_RESUME
	assert "附件简历" in result.outbound_message


def test_orchestrator_requires_a_professional_answer_before_requesting_resume(tmp_path: Path) -> None:
	"""候选人仅回答城市、到岗或实习时长时，模型不得越过专业核验直接索简历。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", skills=["Java"], criteria=criteria)

	def chat(_messages):
		return json.dumps({
			"facts": {"availability": "可到广州"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "候选人确认可到岗。",
			"reply": "方便发送一份附件简历吗？",
			"next_question_phase": "resume",
			"next_action": "ready_for_resume",
			"reason": "索要简历",
		}, ensure_ascii=False)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:58",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.BASIC,
		last_assistant_message="请确认能否到广州到岗、毕业时间和每周可实习天数。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="我在广州，六月毕业，每周可以实习五天。",
	)

	assert result.state.stage is DialogueStage.WAITING_CANDIDATE
	assert result.state.interview_phase is InterviewPhase.PROFESSIONAL
	assert "项目" in result.outbound_message
	assert "简历" not in result.outbound_message


def test_orchestrator_rejects_explicit_non_bachelor_reply_without_ai_call(tmp_path: Path) -> None:
	"""岗位要求本科时，候选人明确说明非本科必须直接结束，不能继续消耗模型。"""
	job = JobProfile(job_id="job-1", name="Java 后端", education_requirement="本科")
	calls = 0

	def chat(_messages):
		nonlocal calls
		calls += 1
		raise AssertionError("明确学历不符时不应调用 AI")

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:59",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请确认您的学历是否为全日制本科。",
	)

	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="嗯，我的学历不是本科。")

	assert calls == 0
	assert result.state.stage is DialogueStage.REJECTED
	assert result.outbound_message == "感谢您的坦诚沟通。该岗位学历要求为本科，本次沟通先到这里，祝您求职顺利。"


def test_orchestrator_uses_short_fixed_resume_request_after_professional_answer(tmp_path: Path) -> None:
	"""专业回答有效后只索要附件简历，不追加夸奖或重复评估说明。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", skills=["Java"], criteria=criteria)

	orchestrator = DialogueOrchestrator(
		chat=lambda _messages: (_ for _ in ()).throw(AssertionError("有效专业回答无需再次调用 AI")),
		state_store=DialogueStateStore(tmp_path),
		job_loader=lambda _: job,
	)
	state = CandidateDialogueState(
		candidate_key="friend:60",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请结合一个 Java 项目，说明您负责的核心模块、技术方案、遇到的问题及最终处理结果。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="我负责订单模块，使用 Spring Boot 和 Redis 设计缓存方案，遇到穿透问题后加了布隆过滤器，接口延迟明显降低。",
	)

	assert result.state.stage is DialogueStage.READY_FOR_RESUME
	assert result.outbound_message == "感谢回复，请发送附件简历。"


def test_orchestrator_does_not_repeat_a_complete_professional_question_with_core_module_wording(tmp_path: Path) -> None:
	"""已问完整专业题时，次日收到有效回答也只能索简历，不能再次追问。

	历史话术使用“核心模块”而非“职责”，两者均表示候选人需要说明自身承担的
	工作范围。该差异不能让状态机遗忘昨天的问题并再次消耗一次专业问答额度。
	"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", skills=["Java"], criteria=criteria)
	orchestrator = DialogueOrchestrator(
		chat=lambda _messages: (_ for _ in ()).throw(AssertionError("完整专业题的有效回答不应再次调用 AI 追问")),
		state_store=DialogueStateStore(tmp_path),
		job_loader=lambda _: job,
	)
	state = CandidateDialogueState(
		candidate_key="friend:61",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请结合一个与Java相关的项目，说明核心模块、技术方案、具体问题及解决结果。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-2",
		message="我在订单项目中负责缓存模块，使用 Redis 设计缓存方案，排查序列化导致的读取异常后调整了序列化方式，接口稳定性明显提升。",
	)

	assert result.state.stage is DialogueStage.READY_FOR_RESUME
	assert result.should_finalize_resume is True
	assert result.outbound_message == "感谢回复，请发送附件简历。"


def test_orchestrator_requests_resume_after_substantive_answer_to_short_professional_question(tmp_path: Path) -> None:
	"""简短专业题得到完整项目回答后，不能因为题目不完整而再次追问。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", skills=["Java"], criteria=criteria)

	orchestrator = DialogueOrchestrator(
		chat=lambda _messages: (_ for _ in ()).throw(AssertionError("有效专业回答无需再次调用 AI")),
		state_store=DialogueStateStore(tmp_path),
		job_loader=lambda _: job,
	)
	state = CandidateDialogueState(
		candidate_key="friend:62",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请介绍一个 Java 项目中您主要负责的部分。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="我负责 WMS 后端的入库、出库和库存模块，使用 Spring Boot、MyBatis、Redis，针对库存并发问题增加锁和缓存优化，接口性能明显提升。",
	)

	assert result.state.stage is DialogueStage.READY_FOR_RESUME
	assert result.state.professional_reply_count == 1
	assert result.outbound_message == "感谢回复，请发送附件简历。"


def test_orchestrator_allows_a_follow_up_inside_the_professional_phase(tmp_path: Path) -> None:
	"""候选人已回答专业题但职责不够具体时，AI 可以继续追问而不能误转人工复核。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", criteria=criteria)

	def chat(_messages):
		return json.dumps(
			{
				"facts": {"project": "完成过 Spring Boot 项目"},
				"candidate_questions": [],
				"summary": "候选人有项目经验，继续确认个人职责。",
				"reply": "请说明其中一个项目中您负责的核心模块？",
				"answers_current_question": True,
				"next_question_phase": "professional",
				"next_action": "continue",
				"reason": "继续专业核验",
			},
			ensure_ascii=False,
		)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:46",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请介绍一个 Java 项目的主要职责。",
	)

	result = orchestrator.handle_candidate_message(state=state, message_id="m-1", message="我做过 Spring Boot 项目。")

	assert result.outbound_message == "请说明其中一个项目中您负责的核心模块？"
	assert result.state.stage is DialogueStage.WAITING_CANDIDATE
	assert result.state.interview_phase is InterviewPhase.PROFESSIONAL
	assert result.state.professional_reply_count == 1


def test_orchestrator_recovers_a_legacy_professional_state_with_a_pending_commute_question(tmp_path: Path) -> None:
	"""旧版本把基础回复提前切专业时，下一条真实回复仍应按基础阶段处理。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", city="广州", criteria=criteria)

	def chat(_messages):
		return json.dumps(
			{
				"facts": {"commute": "约45分钟"},
				"candidate_questions": [],
				"summary": "基础信息已满足，转入专业提问。",
				"reply": "请结合一个 Java 项目说明您如何处理缓存一致性。",
				"answers_current_question": True,
				"next_question_phase": "professional",
				"next_action": "continue",
				"reason": "基础阶段完成",
			},
			ensure_ascii=False,
		)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	legacy_state = CandidateDialogueState(
		candidate_key="friend:45",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		professional_reply_count=0,
		last_assistant_message="请问您从龙洞到实习地点通勤时间大概多久？",
	)

	result = orchestrator.handle_candidate_message(state=legacy_state, message_id="m-1", message="大概45分钟")

	assert result.outbound_message.startswith("请结合一个 Java 项目")
	assert result.state.basic_reply_count == 2
	assert result.state.professional_reply_count == 0
	assert result.state.interview_phase is InterviewPhase.PROFESSIONAL


def test_orchestrator_recognizes_technical_answer_without_fixed_result_keywords(tmp_path: Path) -> None:
	"""完整技术回答不应因没有“结果”字样而再次发送同一专业题。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", skills=["Java", "Spring Boot"], criteria=criteria)
	orchestrator = DialogueOrchestrator(
		chat=lambda _messages: (_ for _ in ()).throw(AssertionError("完整技术回答不应再次调用 AI")),
		state_store=DialogueStateStore(tmp_path),
		job_loader=lambda _: job,
	)
	state = CandidateDialogueState(
		candidate_key="friend:63",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		basic_reply_count=1,
		last_assistant_message="请介绍一个与 Java 相关的项目和您的技术实践。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="熟悉 Java、Spring Boot、MySQL、Redis，做过订单和支付系统，主要负责接口开发、数据库设计和缓存接入。",
	)

	assert result.state.stage is DialogueStage.READY_FOR_RESUME
	assert result.outbound_message == "感谢回复，请发送附件简历。"


def test_orchestrator_does_not_send_a_second_basic_question_after_basic_reply(tmp_path: Path) -> None:
	"""基础问题已有明确回答时，模型返回第二个基础问题也必须被本地阶段闸门改成专业题。"""
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java")
	job = JobProfile(job_id="job-1", name="Java 后端", skills=["Java"], criteria=criteria)

	def chat(_messages):
		return json.dumps({
			"facts": {"availability": "广州，每周五天"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "基础条件已确认。",
			"reply": "请问您目前住在哪里，通勤是否方便？",
			"next_question_phase": "basic",
			"next_action": "continue",
			"reason": "模型仍想补问基础信息",
		}, ensure_ascii=False)

	orchestrator = DialogueOrchestrator(chat=chat, state_store=DialogueStateStore(tmp_path), job_loader=lambda _: job)
	state = CandidateDialogueState(
		candidate_key="friend:64",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.BASIC,
		basic_reply_count=1,
		last_assistant_message="请确认您是否可到广州到岗、毕业时间和每周可实习天数。",
	)

	result = orchestrator.handle_candidate_message(
		state=state,
		message_id="m-1",
		message="可以到广州，每周实习五天，毕业后也可以尽快到岗。",
	)

	assert result.state.interview_phase is InterviewPhase.PROFESSIONAL
	assert "项目" in result.outbound_message
	assert "通勤" not in result.outbound_message
