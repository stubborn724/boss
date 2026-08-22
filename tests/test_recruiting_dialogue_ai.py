"""招聘 AI 对话紧凑上下文与输出校验测试。"""

import json

from boss_agent_cli.recruiting.assessment import parse_natural_language_criteria
from boss_agent_cli.recruiting.dialogue_ai import build_dialogue_messages, decide_dialogue_turn
from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage, InterviewPhase
from boss_agent_cli.recruiting.models import JobProfile


def _job() -> JobProfile:
	criteria, _warnings = parse_natural_language_criteria("必须熟悉 Java；不接受无法到岗")
	return JobProfile(
		job_id="job-1",
		name="Java 后端开发",
		city="广州",
		salary_range="15-20K",
		education_requirement="本科",
		min_experience_years=3,
		skills=["Java", "Spring Boot"],
		criteria=criteria,
	)


def test_ai_reads_compact_state_and_latest_message_only() -> None:
	"""模型输入必须排除完整聊天，避免每轮重复消耗 Token。"""
	captured: list[dict[str, str]] = []

	def chat(messages: list[dict[str, str]]) -> str:
		captured.extend(messages)
		return json.dumps({
			"facts": {"experience": "3年 Java"},
			"candidate_questions": ["是否双休"],
			"summary": "候选人已确认三年 Java 经验，并询问工作制度。",
			"reply": "我们目前双休。请结合一个 Java 项目说明您如何处理缓存一致性。",
			"answers_current_question": True,
			"next_question_phase": "professional",
			"next_action": "continue",
			"reason": "仍需确认到岗时间",
		}, ensure_ascii=False)

	decision = decide_dialogue_turn(
		chat,
		job=_job(),
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			stage=DialogueStage.WAITING_CANDIDATE,
			facts={"city": "广州", "education": "本科"},
			conversation_summary="候选人愿意了解机会。",
			last_assistant_message="请问您有几年 Java 项目经验？",
		),
		candidate_message="我有三年 Java 经验，你们是双休吗？",
	)

	prompt = "\n".join(message["content"] for message in captured)
	assert "我有三年 Java 经验" in prompt
	assert "候选人愿意了解机会" in prompt
	assert "完整聊天记录" not in prompt
	assert "简历正文" not in prompt
	assert decision.facts == {"experience": "3年 Java"}
	assert decision.candidate_questions == ("是否双休",)


def test_ai_rejects_a_commute_question_labeled_as_professional() -> None:
	"""模型标签和话术不一致时，不能把基础题当专业题发送出去。"""
	decision = decide_dialogue_turn(
		lambda _messages: json.dumps({
			"facts": {"location": "龙洞"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "仍需确认通勤。",
			"reply": "请问您从龙洞到实习地点通勤时间大概多久？",
			"next_question_phase": "professional",
			"next_action": "continue",
			"reason": "继续核验",
		}, ensure_ascii=False),
		job=_job(),
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			stage=DialogueStage.WAITING_CANDIDATE,
			last_assistant_message="请问您目前所在的城市是哪里？",
		),
		candidate_message="我在龙洞",
	)

	assert decision.next_action == "manual_review"
	assert decision.reply == ""


def test_professional_prompt_contains_job_specific_focus() -> None:
	"""专业问答必须基于当前岗位的行业、技能和筛选规则，而不是复用通用题库。"""
	job = _job()
	job.industry = "企业服务"
	job.criteria.nice_to_have = ["分布式系统经验"]
	messages = build_dialogue_messages(
		job=job,
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			interview_phase=InterviewPhase.PROFESSIONAL,
		),
		candidate_message="我做过后端项目。",
	)

	prompt = "\n".join(message["content"] for message in messages)
	assert "岗位专属专业核验" in prompt
	assert '"industry":"企业服务"' in prompt
	assert '"nice_to_have":["分布式系统经验"]' in prompt


def test_dialogue_prompt_replaces_unpaired_surrogate_from_boss_message() -> None:
	"""BOSS 消息异常字符不能在 AI 客户端编码请求时中断整轮沟通。"""
	messages = build_dialogue_messages(
		job=_job(),
		state=CandidateDialogueState(candidate_key="friend:42", job_id="job-1"),
		candidate_message="我做过 Java 项目\ud83d",
	)

	assert "\ud83d" not in messages[1]["content"]
	assert "\ufffd" in messages[1]["content"]
	assert messages[1]["content"].encode("utf-8")


def test_ai_decision_rejects_untrusted_reply_shape() -> None:
	"""模型输出不完整或越界时必须阻止自动发送。"""
	decision = decide_dialogue_turn(
		lambda _messages: "not-json",
		job=_job(),
		state=CandidateDialogueState(candidate_key="friend:42", job_id="job-1"),
		candidate_message="我想了解岗位",
	)

	assert decision.next_action == "manual_review"
	assert decision.reply == ""


def test_ai_keeps_professional_phase_when_candidate_defers_to_resume() -> None:
	"""“简历上有”不构成专业回答，不能据此直接索要附件或进入终审。"""
	decision = decide_dialogue_turn(
		lambda _messages: json.dumps({
			"facts": {"skills": "Java"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "候选人称简历中有相关经验。",
			"reply": "方便发送一份附件简历吗？",
			"next_question_phase": "resume",
			"next_action": "ready_for_resume",
			"reason": "索要简历",
		}, ensure_ascii=False),
		job=_job(),
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			stage=DialogueStage.WAITING_CANDIDATE,
			interview_phase=InterviewPhase.PROFESSIONAL,
			last_assistant_message="请介绍一个 Java 项目中的具体职责。",
		),
		candidate_message="简历上有的",
	)

	assert decision.answers_current_question is False
	assert decision.next_question_phase == "professional"
	assert decision.next_action == "continue"
	assert "具体" in decision.reply


def test_ai_keeps_a_compact_combined_basic_confirmation() -> None:
	"""基础条件应在一条短消息内合并确认，避免城市学历实习拆成多轮。"""
	decision = decide_dialogue_turn(
		lambda _messages: json.dumps({
			"facts": {},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "已确认通勤。",
			"reply": "请问您目前是在读本科吗？预计可以实习多长时间？",
			"next_question_phase": "basic",
			"next_action": "continue",
			"reason": "继续核验",
		}, ensure_ascii=False),
		job=_job(),
		state=CandidateDialogueState(candidate_key="friend:42", job_id="job-1"),
		candidate_message="通勤半小时",
	)

	assert decision.reply == "请问您目前是在读本科吗？预计可以实习多长时间？"


def test_ai_removes_a_resume_request_appended_after_a_professional_question() -> None:
	"""专业追问后夹带索要附件时，只能保留第一个待确认维度。"""
	decision = decide_dialogue_turn(
		lambda _messages: json.dumps({
			"facts": {"project": "使用 Spring Boot 完成个人项目"},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "候选人有项目经历，仍需确认个人职责。",
			"reply": "请说明该项目中您负责的核心模块？另外，请发送附件简历。",
			"next_question_phase": "professional",
			"next_action": "continue",
			"reason": "继续专业核验",
		}, ensure_ascii=False),
		job=_job(),
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			stage=DialogueStage.WAITING_CANDIDATE,
			interview_phase=InterviewPhase.PROFESSIONAL,
			last_assistant_message="请介绍一个 Java 项目中的具体职责。",
		),
		candidate_message="我做过 Spring Boot 项目。",
	)

	assert decision.reply == "请说明该项目中您负责的核心模块？"
	assert "简历" not in decision.reply


def test_ai_rejects_english_certificate_as_a_professional_question() -> None:
	"""英语四级等证书不是岗位专业核验，不能作为专业阶段的唯一问题。"""
	decision = decide_dialogue_turn(
		lambda _messages: json.dumps({
			"facts": {},
			"candidate_questions": [],
			"answers_current_question": True,
			"summary": "确认英语能力。",
			"reply": "请问您是否已经取得英语四级证书？",
			"next_question_phase": "professional",
			"next_action": "continue",
			"reason": "继续专业核验",
		}, ensure_ascii=False),
		job=_job(),
		state=CandidateDialogueState(
			candidate_key="friend:42",
			job_id="job-1",
			stage=DialogueStage.WAITING_CANDIDATE,
			interview_phase=InterviewPhase.PROFESSIONAL,
		),
		candidate_message="我可以长期实习。",
	)

	assert decision.next_action == "manual_review"
	assert decision.reply == ""
