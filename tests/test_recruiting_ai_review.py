"""可选 AI 语义层的核对契约测试。

这一层的全部价值在于「模型说的话必须能在简历里被逐字验证」。因此这些测试守住四
条边界：引用要真的存在、标准不能被模型扩大、风险码不能自创、敏感人口属性不能进
入任何输出。最后两个测试把语义层接回评分器，确认它能补上规则漏判的等价表达，
同时不能改变风险等级 —— 模型不得借语义层绕过人工复核门禁。
"""

import json

import pytest

from boss_agent_cli.recruiting.ai_review import (
	AIResumeReview,
	AIReviewError,
	build_review_messages,
	declared_criteria,
	parse_review,
	review_resume,
)
from boss_agent_cli.recruiting.assessment import score_candidate
from boss_agent_cli.recruiting.models import JobProfile, RecruitingCriteria

_RESUME = """# 候选人简历 — 张三

- 城市: 杭州
- 期望薪资: 10-14K
- 学历: 本科
- 工作经验: 3 年

## 工作经历
### 1. 某科技公司 — 客户经理
- 时间: 2021.03 - 2024.06（3年）
**工作内容**
负责客户开发、电话邀约、跟进成交、维护老客户。
"""


def _job(**overrides) -> JobProfile:
	defaults = {
		"job_id": "job-1",
		"name": "ToB 销售顾问",
		"city": "杭州",
		"salary_range": "8-15K",
		"education_requirement": "本科及以上",
		"min_experience_years": 2,
		"criteria": RecruitingCriteria(
			must_have=["能接受电话销售"],
			nice_to_have=["招商加盟经验"],
			reject_if=["只想做客服"],
		),
	}
	return JobProfile(**{**defaults, "status": "published", **overrides})


def _payload(**overrides) -> str:
	body = {
		"criteria_findings": [
			{"criterion": "能接受电话销售", "matched": True, "quote": "负责客户开发、电话邀约、跟进成交"},
		],
		"risk_findings": [],
		"follow_up_questions": ["请说明你最近一次成交的客户类型和金额区间？"],
		"summary": "有真实客户开发经历，但未直接写明电话销售岗位。",
	}
	return json.dumps({**body, **overrides}, ensure_ascii=False)


def _parse(raw: str, job: JobProfile | None = None) -> AIResumeReview:
	target = job or _job()
	return parse_review(raw, resume_text=_RESUME, criteria=declared_criteria(target), model="test-model")


def test_prompt_carries_evidence_and_non_discrimination_constraints() -> None:
	"""提示词必须带上需求文档规定的证据与反歧视约束，否则模型会自由发挥。"""
	messages = build_review_messages(_job(), _RESUME)

	system = messages[0]["content"]
	user = json.loads(messages[1]["content"])

	assert "逐字找到" in system
	assert "不得新增 HR 没有表达过的要求" in system
	assert "性别、婚育、民族、户籍、年龄" in system
	assert "不要输出分数或录用建议" in system
	assert user["allowed_criteria"] == ["能接受电话销售", "招商加盟经验"]
	assert "frequent_job_change" in user["allowed_risk_codes"]


def test_review_prompt_replaces_unpaired_surrogate_before_ai_request() -> None:
	"""PDF 提取器异常字符不能在 AI 客户端编码请求时中断附件终审。"""
	messages = build_review_messages(_job(), "Java 项目\ud83d")

	assert "\ud83d" not in messages[1]["content"]
	assert "\ufffd" in messages[1]["content"]
	assert messages[1]["content"].encode("utf-8")


def test_quote_found_in_resume_is_accepted_as_verified_hit() -> None:
	"""引用能在简历中逐字找到时才算命中，并原样保留岗位标准文本。"""
	review = _parse(_payload())

	assert review.hit_quotes() == {"能接受电话销售": "负责客户开发、电话邀约、跟进成交"}
	assert review.follow_up_questions == ("请说明你最近一次成交的客户类型和金额区间？",)
	assert review.rejected_claims == ()
	assert review.to_dict()["advisory_only"] is True


def test_fabricated_quote_is_rejected_with_visible_reason() -> None:
	"""简历里没有的引用一律不采信，并把模型说过的话留在 rejected_claims。"""
	review = _parse(_payload(criteria_findings=[
		{"criterion": "能接受电话销售", "matched": True, "quote": "曾任电话销售主管，管理 20 人团队"},
	]))

	assert review.semantic_hits == ()
	assert any("引用未在简历原文中找到" in claim for claim in review.rejected_claims)


def test_criterion_outside_job_standard_is_rejected() -> None:
	"""模型不能新增 HR 没写过的要求，即使它能在简历里找到证据。"""
	review = _parse(_payload(criteria_findings=[
		{"criterion": "有团队管理经验", "matched": True, "quote": "维护老客户"},
	]))

	assert review.semantic_hits == ()
	assert any("岗位标准未声明该条件" in claim for claim in review.rejected_claims)


def test_reject_conditions_are_never_reachable_by_semantic_layer() -> None:
	"""淘汰条件命中意味着扣分，不能让模型把它当作可采信的“优点”。"""
	assert "只想做客服" not in declared_criteria(_job())


def test_unknown_risk_code_and_sensitive_terms_are_dropped() -> None:
	"""风险码必须来自既有码表；敏感人口属性条目整条丢弃。"""
	review = _parse(_payload(
		risk_findings=[
			{"code": "made_up_risk", "message": "自创风险", "quote": "维护老客户"},
			{"code": "frequent_job_change", "message": "已婚已育，稳定性存疑", "quote": "维护老客户"},
		],
		follow_up_questions=["请问你的婚育状况？"],
		summary="候选人性别为男，适合跑外勤。",
	))

	assert review.risk_findings == ()
	assert review.follow_up_questions == ()
	assert review.summary == ""
	assert any("风险码不在既有码表内" in claim for claim in review.rejected_claims)
	assert any("包含敏感人口属性" in claim for claim in review.rejected_claims)
	assert any("追问包含敏感人口属性" in claim for claim in review.rejected_claims)


def test_non_json_response_raises_so_caller_can_degrade() -> None:
	"""模型不守协议时必须报错，让命令层降级为纯规则，而不是静默返回空结论。"""
	with pytest.raises(AIReviewError):
		_parse("我觉得这个候选人还不错")


def test_code_fenced_json_is_accepted() -> None:
	"""模型习惯性包裹 ``` 代码块不应导致整份评审作废。"""
	review = _parse(f"```json\n{_payload()}\n```")

	assert review.hit_quotes()


def test_review_resume_skips_call_when_job_has_no_positive_criteria() -> None:
	"""岗位没有可命中的正向标准时不做无意义的外部调用。"""

	class _ExplodingService:
		model = "test-model"

		def chat(self, messages):  # pragma: no cover - 断言就是它不该被调用
			raise AssertionError("不应发起 AI 调用")

	empty_job = _job(criteria=RecruitingCriteria(), education_requirement="", skills=[])

	assert review_resume(_ExplodingService(), empty_job, _RESUME) == AIResumeReview(model="test-model")


def test_verified_hit_raises_hard_match_but_not_risk_level() -> None:
	"""语义命中补上规则漏判的等价表达，但不得改动风险等级。

	需求文档点名的场景：简历写“客户开发、电话邀约、跟进成交”而没写“销售”。
	纯规则的子串匹配会漏判；AI 给出可核对引用后应算作命中并提高综合分。同时模型
	提交的风险判断只能进证据行，``screening.risk.level`` 仍由本地规则决定，否则
	模型就能替 HR 决定是否放行。
	"""
	job = _job()
	baseline = score_candidate(job, candidate_id="c1", candidate_name="张三", resume_text=_RESUME)
	review = _parse(_payload(risk_findings=[
		{"code": "frequent_job_change", "message": "任职周期偏短", "quote": "2021.03 - 2024.06"},
	]))
	enhanced = score_candidate(
		job, candidate_id="c1", candidate_name="张三", resume_text=_RESUME, ai_review=review,
	)

	assert "能接受电话销售" not in baseline.matched_points
	assert "能接受电话销售" in enhanced.matched_points
	assert enhanced.final_score > baseline.final_score
	assert enhanced.engine == "rules+ai"
	assert any("AI 语义命中（已核对原文）" in line for line in enhanced.evidence)
	# 风险提示只出现在证据里，不进 risk_points，也不抬高风险等级。
	assert any("AI 风险提示（仅供人工核对）" in line for line in enhanced.evidence)
	assert enhanced.risk_points == baseline.risk_points
	assert enhanced.screening["risk"]["level"] == baseline.screening["risk"]["level"]


def test_ai_follow_up_questions_append_after_job_questions() -> None:
	"""AI 追问排在岗位知识库与标准生成的问题之后，作为补充而非替代。"""
	report = score_candidate(
		_job(), candidate_id="c1", candidate_name="张三", resume_text=_RESUME, ai_review=_parse(_payload()),
	)

	ai_items = [item for item in report.professional_question_items if item["kind"] == "ai_follow_up"]

	assert [item["question"] for item in ai_items] == ["请说明你最近一次成交的客户类型和金额区间？"]
	assert report.professional_question_items[0]["kind"] != "ai_follow_up"
	assert report.ai_review["advisory_only"] is True
