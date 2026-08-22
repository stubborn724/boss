"""招聘工作台规则评估的行为契约测试。"""

from boss_agent_cli.recruiting.assessment import (
	evaluate_job_readiness,
	extract_candidate_profile,
	generate_message_templates,
	generate_professional_questions,
	parse_natural_language_job,
	parse_natural_language_criteria,
	screen_candidate,
	score_candidate,
)
from boss_agent_cli.recruiting.models import JobProfile, RecruitingCriteria
from boss_agent_cli.recruiting.screening import build_review_gate


def test_knowledge_questions_include_stable_sources() -> None:
	"""知识文档应生成可复核的问题 ID、版本和来源引用。"""
	from boss_agent_cli.recruiting.assessment import generate_professional_question_items

	job = JobProfile(
		job_id="job-knowledge",
		name="销售顾问",
		criteria=RecruitingCriteria(must_have=["销售经验"]),
	)
	documents = [
		{
			"document_id": "kb-1",
			"title": "销售流程",
			"content": "先做需求诊断，再进行电话跟进。",
			"updated_at": "2026-07-31T10:00:00+00:00",
		}
	]

	first = generate_professional_question_items(job, knowledge_documents=documents)
	second = generate_professional_question_items(job, knowledge_documents=documents)

	knowledge_item = next(item for item in first if item["source_ids"])
	assert knowledge_item["question_id"] == next(item for item in second if item["source_ids"])["question_id"]
	assert knowledge_item["question_version"].startswith("v")
	assert knowledge_item["source_ids"] == ["kb-1"]


def test_professional_question_items_add_skill_specific_follow_ups_and_risk_checks() -> None:
	"""岗位技能和稳定性风险应生成可人工追问的结构化问题。"""
	from boss_agent_cli.recruiting.assessment import generate_professional_question_items

	job = JobProfile(
		job_id="job-rich-questions",
		name="ToB 销售顾问",
		criteria=RecruitingCriteria(
			must_have=["电话销售经验"],
			nice_to_have=["CRM 客户开发"],
			risk_signals=["频繁跳槽"],
		),
	)

	items = generate_professional_question_items(job, limit=5)

	criteria_items = [item for item in items if item["kind"] == "criteria"]
	risk_items = [item for item in items if item["kind"] == "risk"]
	assert criteria_items
	assert any(item["follow_up_questions"] for item in criteria_items)
	assert any("CRM" in item["question"] or "电话销售" in item["question"] for item in criteria_items)
	assert risk_items
	assert "频繁跳槽" in risk_items[0]["question"]
	assert risk_items[0]["follow_up_questions"]


def test_parse_criteria_extracts_categories_and_drops_sensitive_constraints() -> None:
	"""自然语言标准应拆成可执行类别，且性别等敏感条件不能进入评分规则。"""
	criteria, warnings = parse_natural_language_criteria(
		"必须有电话销售经验；招商加盟经验优先；不接受频繁跳槽；不要按性别或婚育筛选"
	)

	assert criteria.must_have == ["电话销售经验"]
	assert criteria.nice_to_have == ["招商加盟经验"]
	assert criteria.reject_if == ["频繁跳槽"]
	assert criteria.risk_signals == []
	assert any("敏感" in warning for warning in warnings)


def test_score_candidate_without_professional_answer_stays_below_strict_pool_threshold() -> None:
	"""简历初筛再匹配，缺少专业实答时也不能进入严格候选人池。"""
	job = JobProfile(
		job_id="job-1",
		name="ToB 销售顾问",
		criteria=RecruitingCriteria(
			must_have=["电话销售经验", "接受单休"],
			nice_to_have=["招商加盟经验"],
			reject_if=["只想做纯客服"],
			risk_signals=["频繁跳槽"],
		),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-1",
		candidate_name="张三",
		resume_text="有三年电话销售经验，做过招商加盟，接受单休。",
	)

	assert report.final_score < 80
	assert report.level == "待确认"
	assert "电话销售经验" in report.matched_points
	assert report.review_required is True
	assert report.decision == "待人工确认"
	assert report.engine == "rules"


def test_score_candidate_marks_reject_and_risk_evidence() -> None:
	"""命中淘汰项或风险项时应降低分数并保留具体证据。"""
	job = JobProfile(
		job_id="job-2",
		name="销售顾问",
		criteria=RecruitingCriteria(
			must_have=["销售经验"],
			reject_if=["只想做纯客服"],
			risk_signals=["频繁跳槽"],
		),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-2",
		candidate_name="李四",
		resume_text="有销售经验，但只想做纯客服，过去一年频繁跳槽。",
	)

	assert report.final_score < 70
	assert "只想做纯客服" in report.risk_points
	assert "频繁跳槽" in report.risk_points
	assert report.level == "不推荐"


def test_score_candidate_without_professional_evidence_cannot_reach_auto_qualification() -> None:
	"""没有专业问答证据时，即使简历关键词很多也不能自动进入严格候选人池。"""
	job = JobProfile(
		job_id="job-strict",
		name="Java 后端",
		criteria=RecruitingCriteria(must_have=["Java", "Spring Boot"], nice_to_have=["Redis"]),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-strict",
		candidate_name="候选人",
		resume_text="有 Java、Spring Boot 和 Redis 项目经验。",
	)

	assert report.professional_qa_score is None
	assert report.final_score < 80


def test_questions_and_messages_are_bounded_and_manual_only() -> None:
	"""问题和话术应短而可复制，不能暗含自动发送动作。"""
	job = JobProfile(
		job_id="job-3",
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		criteria=RecruitingCriteria(must_have=["销售经验"], nice_to_have=["SaaS 客户开发"]),
	)

	questions = generate_professional_questions(job, limit=5)
	templates = generate_message_templates(job, candidate_name="王五")

	assert 3 <= len(questions) <= 5
	assert all(question.strip() for question in questions)
	assert {"greeting", "basic_confirmation", "resume_exchange", "interview_invite"} <= set(templates)
	assert all("自动发送" not in text for text in templates.values())


def test_parse_natural_language_job_extracts_structured_requirements() -> None:
	"""岗位自然语言中的学历、经验、行业和技能应进入结构化字段。"""
	criteria, structured, warnings = parse_natural_language_job(
		"本科及以上；3年以上工作经验；行业：SaaS；技能：CRM、电话销售；必须有客户开发经验"
	)

	assert structured["education_requirement"] == "本科及以上"
	assert structured["min_experience_years"] == 3
	assert structured["industry"] == "SaaS"
	assert structured["skills"] == ["CRM", "电话销售"]
	assert criteria.must_have == ["客户开发经验"]
	assert warnings == []


def test_score_candidate_uses_job_weights_and_returns_breakdown() -> None:
	"""评分报告必须使用岗位权重，并能解释每个维度的证据和得分。"""
	job = JobProfile(
		job_id="job-weighted",
		name="销售顾问",
		weights={
			"hard_match": 80,
			"experience": 5,
			"professional_qa": 5,
			"communication": 5,
			"stability": 3,
			"location_salary": 2,
		},
		criteria=RecruitingCriteria(must_have=["销售经验"]),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-weighted",
		candidate_name="张三",
		resume_text="有销售经验。",
	)

	assert set(report.score_breakdown) == {
		"hard_match",
		"experience",
		"professional_qa",
		"communication",
		"stability",
		"location_salary",
	}
	assert report.score_breakdown["hard_match"]["weight"] == 80
	assert report.score_breakdown["hard_match"]["weighted_score"] > 0
	assert report.final_score == round(sum(item["weighted_score"] for item in report.score_breakdown.values()))


def test_score_candidate_keeps_per_question_qa_evidence_and_blocks_weak_answers() -> None:
	"""专业问答要逐题保留评分证据，单题过低时必须生成定向追问。"""
	job = JobProfile(
		job_id="job-qa-breakdown",
		name="销售顾问",
		criteria=RecruitingCriteria(must_have=["客户开发"]),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-qa-breakdown",
		candidate_name="候选人",
		resume_text="有客户开发经验。",
		answers=[
			{
				"question_id": "question-weak",
				"question_version": "v1",
				"question": "请举一个客户开发案例？",
				"answer": "还可以",
			},
			{
				"question_id": "question-strong",
				"question_version": "v1",
				"question": "请说明你如何跟进成交？",
				"answer": "我负责客户开发，先确认需求，再处理异议并持续跟进成交，最终完成签约。",
			},
		],
	)

	weak = next(item for item in report.professional_qa_breakdown if item["question_id"] == "question-weak")
	assert weak["status"] == "follow_up"
	assert weak["score"] < 60
	assert weak["answer_length"] == 3
	assert report.screening["professional_qa"]["failed_question_ids"] == ["question-weak"]
	assert report.screening["professional_qa"]["status"] == "follow_up"
	assert any("question-weak" in question for question in report.screening["professional_qa"]["follow_up_questions"])


def test_professional_qa_uses_the_current_job_threshold() -> None:
	"""同一回答必须按当前岗位阈值判定，不能继续使用全局固定 60 分。"""
	job = JobProfile(
		job_id="job-strict-qa",
		name="Java 后端",
		professional_qa_threshold=80,
		criteria=RecruitingCriteria(must_have=["Java"]),
	)

	report = score_candidate(
		job,
		candidate_id="candidate-strict-qa",
		candidate_name="候选人",
		resume_text="有 Java 项目经验。",
		answers=[{
			"question_id": "java-case",
			"question": "请介绍一个 Java 项目。",
			"answer": "我负责 Java 服务开发，处理需求并完成接口交付。",
		}],
	)

	assert report.professional_qa_breakdown[0]["score"] == 67
	assert report.professional_qa_breakdown[0]["status"] == "follow_up"
	assert report.screening["professional_qa"]["threshold"] == 80
	assert report.screening["professional_qa"]["status"] == "follow_up"


def test_review_gate_does_not_require_professional_qa_when_job_disables_it() -> None:
	"""关闭岗位专业问答时，最终门禁应明确跳过 QA 和简历一致性条件。"""
	job = JobProfile(
		job_id="job-no-qa",
		name="销售顾问",
		professional_qa_enabled=False,
		criteria=RecruitingCriteria(must_have=["销售经验"]),
	)
	report = score_candidate(
		job,
		candidate_id="candidate-no-qa",
		candidate_name="候选人",
		resume_text="有销售经验。",
	)
	gate = build_review_gate(
		report.to_dict(),
		candidate_stage="basic_passed",
		candidate_events=[{"stage": "basic_passed", "action": "基础意向人工确认"}],
	)

	assert report.to_dict()["professional_qa_enabled"] is False
	assert gate["eligible"] is True
	checks = {item["code"]: item for item in gate["checks"]}
	assert checks["professional_qa"]["passed"] is True
	assert checks["professional_qa"]["value"]["status"] == "not_required"
	assert checks["resume_qa_consistency"]["passed"] is True
	assert checks["resume_qa_consistency"]["value"] == "not_required"


def test_job_readiness_exposes_missing_hr_answers_before_screening() -> None:
	"""岗位保存后应明确告诉 HR 哪些必答项还没补齐。"""
	job = JobProfile(job_id="job-draft", name="销售顾问")

	readiness = evaluate_job_readiness(job)

	assert readiness["ready"] is False
	assert readiness["missing_required_fields"] == [
		"city",
		"salary_range",
		"education_requirement",
		"min_experience_years",
		"core_skills",
	]
	assert len(readiness["clarification_questions"]) == 5


def test_candidate_profile_extracts_structured_evidence_without_sensitive_fields() -> None:
	"""候选人画像只提取业务证据，不能把敏感人口属性带入筛选。"""
	profile = extract_candidate_profile(
		"城市：杭州\n期望薪资：12-18K\n学历：本科\n工作经验：3年\n"
		"最近职位：客户经理\n行业：SaaS\n技能：CRM、电话邀约\n性别：女"
	)

	assert profile.city == "杭州"
	assert profile.expected_salary == "12-18K"
	assert profile.education == "本科"
	assert profile.experience_years == 3
	assert profile.recent_role == "客户经理"
	assert profile.industry == "SaaS"
	assert profile.skills == ["CRM", "电话邀约"]
	assert "性别" not in profile.to_dict()


def test_three_layer_screening_returns_hard_semantic_and_risk_evidence() -> None:
	"""初筛报告要能解释硬条件、语义匹配和风险，而不只给一个总分。"""
	job = JobProfile(
		job_id="job-screen",
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		education_requirement="本科及以上",
		min_experience_years=2,
		skills=["客户开发"],
		criteria=RecruitingCriteria(must_have=["电话销售经验"]),
	)

	screening = screen_candidate(
		job,
		"城市：杭州\n期望薪资：12-18K\n学历：本科\n工作经验：3年\n"
		"做过电话邀约和跟进成交，负责客户开发。",
	)

	assert screening["hard_filter"]["status"] == "pass"
	assert screening["semantic_match"]["score"] == 100
	assert "电话销售经验" in screening["semantic_match"]["matched"]
	assert screening["risk"]["level"] == "low"
	assert screening["decision"] == "初筛通过"


def test_three_layer_screening_uses_the_current_job_threshold() -> None:
	"""岗位初筛阈值应决定低语义分是淘汰还是进入人工复核。"""
	job = JobProfile(
		job_id="job-lenient-screening",
		name="技术支持",
		screening_threshold=40,
		skills=["故障排查", "客户沟通"],
	)

	screening = screen_candidate(
		job,
		"城市：广州\n期望薪资：8-10K\n学历：本科\n工作经验：3年\n技能：客户沟通\n"
		"有客户沟通经验，长期负责用户问题跟进、处理结果回访和服务记录整理；"
		"能够独立梳理问题现象、同步处理进度、维护工单记录，并持续跟踪客户反馈直至问题关闭。",
	)

	assert screening["semantic_match"]["score"] == 50
	assert screening["semantic_match"]["threshold"] == 40
	assert screening["decision"] == "初筛通过"


def test_screening_reads_experience_timeline_for_gap_and_short_tenure_risks() -> None:
	"""明确的年月经历应补充空窗和短任职风险，但不依赖简历出现风险关键词。"""
	job = JobProfile(job_id="job-timeline", name="销售顾问")

	screening = screen_candidate(
		job,
		"工作经历：\n"
		"2018.01-2018.09 客户顾问\n"
		"2019.06-2020.02 销售顾问\n"
		"2021.01-2021.08 商务顾问\n"
		"2021.09-至今 销售顾问\n",
	)

	signals = {item["code"]: item["message"] for item in screening["risk"]["signals"]}
	assert "employment_gap" in signals
	assert "frequent_job_change" in signals
	assert "空窗" in signals["employment_gap"]
	assert "不足一年" in signals["frequent_job_change"]


def test_screening_marks_generic_professional_answer_for_follow_up() -> None:
	"""专业回答低于门槛时必须生成追问，不能直接进入私域或面试。"""
	job = JobProfile(job_id="job-qa", name="销售顾问")

	screening = screen_candidate(
		job,
		"有销售经验。",
		answers=[{"answer": "还可以"}],
		professional_qa_score=35,
	)

	assert screening["professional_qa"]["status"] == "follow_up"
	assert screening["professional_qa"]["threshold"] == 60
	assert screening["professional_qa"]["follow_up_questions"]
	assert screening["next_action"] == "补充专业问答后再次评估"


def test_review_gate_requires_all_document_conditions_and_explains_failures() -> None:
	"""进入私域前必须同时满足评分、硬条件、问答、风险和基础意向门禁。"""
	report = {
		"final_score": 79,
		"screening": {
			"hard_filter": {"status": "review", "unknowns": ["缺少城市证据"]},
			"professional_qa": {"status": "not_started", "score": None, "threshold": 60},
			"resume_qa_consistency": {"status": "review", "message": "缺少一致证据"},
			"risk": {"level": "high", "signals": [{"code": "frequent_job_change"}]},
		},
	}

	gate = build_review_gate(report, candidate_stage="professional_qa", candidate_events=[])

	assert gate["eligible"] is False
	assert {item["code"] for item in gate["failed_checks"]} == {
		"final_score",
		"hard_filter",
		"professional_qa",
		"resume_qa_consistency",
		"risk",
		"basic_intent",
	}
	assert gate["thresholds"]["final_score"] == 80


def test_review_gate_passes_only_after_basic_intent_and_professional_qa() -> None:
	"""基础意向事件和合格专业问答共同存在时才允许默认推进。"""
	report = {
		"final_score": 86,
		"screening": {
			"hard_filter": {"status": "pass"},
			"professional_qa": {"status": "pass", "score": 82, "threshold": 60},
			"resume_qa_consistency": {"status": "pass", "message": "存在一致证据"},
			"risk": {"level": "low", "signals": []},
		},
	}

	gate = build_review_gate(
		report,
		candidate_stage="professional_qa",
		candidate_events=[{"stage": "basic_passed", "action": "基础条件人工确认"}],
	)

	assert gate["eligible"] is True
	assert gate["failed_checks"] == []


def test_review_gate_does_not_treat_professional_qa_as_basic_intent() -> None:
	"""直接记录专业回答不能替代基础意向人工确认。"""
	report = {
		"final_score": 86,
		"screening": {
			"hard_filter": {"status": "pass"},
			"professional_qa": {"status": "pass", "score": 82},
			"resume_qa_consistency": {"status": "pass"},
			"risk": {"level": "low"},
		},
	}

	gate = build_review_gate(
		report,
		candidate_stage="professional_qa",
		candidate_events=[{"stage": "professional_qa", "action": "记录专业问答"}],
	)

	assert gate["eligible"] is False
	assert any(item["code"] == "basic_intent" for item in gate["failed_checks"])
