"""招聘工作台本地复盘指标和优化建议测试。"""

from boss_agent_cli.recruiting.insights import build_optimization_projection


def test_optimization_projection_explains_feedback_without_mutating_configuration() -> None:
	"""复盘应汇总来源事实并生成建议，而不是直接改岗位或话术。"""
	result = build_optimization_projection(
		job_id="job-1",
		candidate_count=4,
		knowledge_count=0,
		faq_count=1,
		assessment_count=3,
		professional_qa_scores=[45, 55, 80],
		mismatch_reasons=["salary_mismatch", "salary_mismatch", "city_mismatch"],
		communication_outcomes=["no_response", "no_response", "follow_up"],
		decision_outcomes=["rejected", "rejected", "hired"],
	)

	assert result["metrics"]["candidate_count"] == 4
	assert result["metrics"]["top_mismatch_reason"] == "salary_mismatch"
	assert result["metrics"]["mismatch_reason_rates"] == {
		"salary_mismatch": {"count": 2, "rate": 66.7},
		"city_mismatch": {"count": 1, "rate": 33.3},
	}
	assert {item["kind"] for item in result["suggestions"]} >= {
		"knowledge_gap",
		"mismatch_pattern",
		"qa_threshold",
		"communication_pattern",
	}
	assert result["mutations"] == []
	assert all("自动修改" not in item["action"] for item in result["suggestions"])


def test_optimization_projection_is_quiet_when_pipeline_has_no_risk_signal() -> None:
	"""没有可解释风险时仍返回稳定的空建议而不是制造噪音。"""
	result = build_optimization_projection(
		job_id="job-2",
		candidate_count=0,
		knowledge_count=2,
		faq_count=3,
		assessment_count=0,
		professional_qa_scores=[],
		mismatch_reasons=[],
		communication_outcomes=[],
		decision_outcomes=[],
	)

	assert result["suggestions"] == []
	assert result["metrics"]["candidate_count"] == 0


def test_optimization_projection_reports_source_stage_and_template_conversion() -> None:
	"""复盘应把来源、阶段和话术使用映射成只读转化指标。"""
	result = build_optimization_projection(
		job_id="job-3",
		candidate_count=3,
		knowledge_count=1,
		faq_count=1,
		assessment_count=2,
		professional_qa_scores=[75],
		mismatch_reasons=[],
		communication_outcomes=["continue"],
		decision_outcomes=["hired"],
		candidate_rows=[
			{"candidate_id": "c1", "source": "boss_conversation", "stage": "hired"},
			{"candidate_id": "c2", "source": "boss_conversation", "stage": "resume_passed"},
			{"candidate_id": "c3", "source": "boss_recommendation", "stage": "rejected"},
		],
		template_usages=[
			{"template_key": "greeting", "template_version": "v1", "candidate_id": "c1"},
			{"template_key": "greeting", "template_version": "v1", "candidate_id": "c2"},
		],
	)

	assert result["metrics"]["source_conversion"]["boss_conversation"]["candidate_count"] == 2
	assert result["metrics"]["source_conversion"]["boss_conversation"]["terminal_count"] == 1
	assert result["metrics"]["stage_conversion"]["hired"] == 1
	assert result["metrics"]["template_effectiveness"]["greeting:v1"]["usage_count"] == 2
	assert result["mutations"] == []


def test_optimization_projection_reports_outcome_rates_and_template_quality() -> None:
	"""复盘应把沟通和终局结果换算成比例，并关联话术后的实际结果。"""
	result = build_optimization_projection(
		job_id="job-4",
		candidate_count=4,
		knowledge_count=1,
		faq_count=1,
		assessment_count=4,
		professional_qa_scores=[70],
		mismatch_reasons=[],
		communication_outcomes=["qualified", "no_response", "qualified", "follow_up"],
		decision_outcomes=["hired", "rejected", "rejected", "paused"],
		template_usages=[
			{"template_key": "greeting", "template_version": "v2", "candidate_id": "c1"},
			{"template_key": "greeting", "template_version": "v2", "candidate_id": "c2"},
		],
		communication_rows=[
			{"template_key": "greeting", "template_version": "v2", "candidate_id": "c1", "outcome": "qualified"},
			{"template_key": "greeting", "template_version": "v2", "candidate_id": "c2", "outcome": "no_response"},
		],
	)

	assert result["metrics"]["communication_outcome_rates"]["qualified"] == {"count": 2, "rate": 50.0}
	assert result["metrics"]["decision_outcome_rates"]["hired"] == {"count": 1, "rate": 25.0}
	template = result["metrics"]["template_effectiveness"]["greeting:v2"]
	assert template["qualified_count"] == 1
	assert template["qualified_rate"] == 50.0


def test_optimization_projection_reports_question_faq_and_hiring_feedback() -> None:
	"""复盘应统计候选人问题、FAQ 需求和录用结果反馈，并标记小样本。"""
	result = build_optimization_projection(
		job_id="job-5",
		candidate_count=3,
		knowledge_count=1,
		faq_count=1,
		assessment_count=2,
		professional_qa_scores=[82],
		mismatch_reasons=[],
		communication_outcomes=["qualified", "no_response"],
		decision_outcomes=["hired"],
		question_demands=[
			{
				"query": "薪资怎么发？",
				"normalized_query": "薪资怎么发",
				"status": "answered",
				"source_type": "faq",
				"source_id": "faq-salary",
				"source_title": "薪资说明",
			},
			{
				"query": "薪资怎么发",
				"normalized_query": "薪资怎么发",
				"status": "no_source",
				"source_type": "",
				"source_id": "",
				"source_title": "",
			},
		],
		communication_rows=[
			{"template_key": "greeting", "template_version": "v1", "candidate_id": "c1", "outcome": "qualified"},
			{"template_key": "greeting", "template_version": "v1", "candidate_id": "c2", "outcome": "no_response"},
		],
		decision_rows=[{"candidate_id": "c1", "outcome": "hired"}],
	)

	metrics = result["metrics"]
	assert metrics["question_demand_rates"]["薪资怎么发"]["count"] == 2
	assert metrics["question_demand_rates"]["薪资怎么发"]["answered_count"] == 1
	assert metrics["faq_demand_rates"]["faq-salary"]["count"] == 1
	assert metrics["top_faq_questions"][0]["question"] == "薪资说明"
	template = metrics["template_outcome_rates"]["greeting:v1"]
	assert template["reply_rate"] == 50.0
	assert template["qualified_rate"] == 50.0
	assert template["hired_rate"] == 50.0
	assert template["low_sample"] is True
	assert metrics["sample_notice"]
	assert any(item["kind"] == "hiring_feedback" for item in result["suggestions"])


def test_optimization_projection_compares_hiring_signals_without_candidate_identity() -> None:
	"""录用学习只输出聚合信号，不把候选人标识带回复盘快照。"""
	result = build_optimization_projection(
		job_id="job-learning",
		candidate_count=6,
		knowledge_count=2,
		faq_count=2,
		assessment_count=6,
		professional_qa_scores=[90, 88, 86, 62, 60, 58],
		mismatch_reasons=[],
		communication_outcomes=["qualified"] * 6,
		decision_outcomes=["hired", "hired", "hired", "rejected", "rejected", "rejected"],
		candidate_rows=[
			{"candidate_id": "candidate-1", "name": "甲", "profile": {"experience_years": 4, "industry": "SaaS", "skills": ["CRM", "电话销售"]}},
			{"candidate_id": "candidate-2", "name": "乙", "profile": {"experience_years": 5, "industry": "SaaS", "skills": ["CRM"]}},
			{"candidate_id": "candidate-3", "name": "丙", "profile": {"experience_years": 3, "industry": "企业服务", "skills": ["CRM"]}},
			{"candidate_id": "candidate-4", "name": "丁", "profile": {"experience_years": 1, "industry": "零售", "skills": ["门店销售"]}},
			{"candidate_id": "candidate-5", "name": "戊", "profile": {"experience_years": 2, "industry": "零售", "skills": ["门店销售"]}},
			{"candidate_id": "candidate-6", "name": "己", "profile": {"experience_years": 1, "industry": "零售", "skills": ["门店销售"]}},
		],
		assessment_rows=[
			{"candidate_id": "candidate-1", "final_score": 90, "score_breakdown": {"professional_qa": {"score": 92}, "stability": {"score": 88}}},
			{"candidate_id": "candidate-2", "final_score": 88, "score_breakdown": {"professional_qa": {"score": 90}, "stability": {"score": 86}}},
			{"candidate_id": "candidate-3", "final_score": 86, "score_breakdown": {"professional_qa": {"score": 88}, "stability": {"score": 84}}},
			{"candidate_id": "candidate-4", "final_score": 62, "score_breakdown": {"professional_qa": {"score": 60}, "stability": {"score": 55}}},
			{"candidate_id": "candidate-5", "final_score": 60, "score_breakdown": {"professional_qa": {"score": 58}, "stability": {"score": 54}}},
			{"candidate_id": "candidate-6", "final_score": 58, "score_breakdown": {"professional_qa": {"score": 56}, "stability": {"score": 52}}},
		],
		decision_rows=[
			{"candidate_id": "candidate-1", "outcome": "hired"},
			{"candidate_id": "candidate-2", "outcome": "hired"},
			{"candidate_id": "candidate-3", "outcome": "hired"},
			{"candidate_id": "candidate-4", "outcome": "rejected"},
			{"candidate_id": "candidate-5", "outcome": "rejected"},
			{"candidate_id": "candidate-6", "outcome": "rejected"},
		],
	)

	learning = result["metrics"]["hiring_learning"]
	assert learning["status"] == "ready"
	assert learning["hired_count"] == 3
	assert learning["comparison_count"] == 3
	assert learning["low_sample"] is False
	final_score = next(item for item in learning["numeric_signals"] if item["key"] == "final_score")
	assert final_score["hired_average"] == 88.0
	assert final_score["comparison_average"] == 60.0
	assert final_score["difference"] == 28.0
	assert any(item["signal"] == "CRM" for item in learning["profile_signals"])
	assert any(item["kind"] == "hiring_learning" for item in result["suggestions"])
	assert "candidate-1" not in str(learning)
	assert "甲" not in str(learning)
	assert result["mutations"] == []


def test_optimization_projection_marks_hiring_learning_as_low_sample() -> None:
	"""录用组或对照组不足三条时只给趋势提示，不生成可靠结论。"""
	result = build_optimization_projection(
		job_id="job-learning-small",
		candidate_count=2,
		knowledge_count=1,
		faq_count=1,
		assessment_count=2,
		professional_qa_scores=[80, 55],
		mismatch_reasons=[],
		communication_outcomes=[],
		decision_outcomes=["hired", "rejected"],
		candidate_rows=[
			{"candidate_id": "candidate-hired", "profile": {"experience_years": 5, "skills": ["CRM"]}},
			{"candidate_id": "candidate-rejected", "profile": {"experience_years": 1, "skills": ["门店销售"]}},
		],
		assessment_rows=[
			{"candidate_id": "candidate-hired", "final_score": 88},
			{"candidate_id": "candidate-rejected", "final_score": 55},
		],
		decision_rows=[
			{"candidate_id": "candidate-hired", "outcome": "hired"},
			{"candidate_id": "candidate-rejected", "outcome": "rejected"},
		],
	)

	learning = result["metrics"]["hiring_learning"]
	assert learning["status"] == "ready"
	assert learning["low_sample"] is True
	assert "至少各有 3 条" in learning["notice"]
	assert "录用结果学习" in result["metrics"]["sample_notice"]
