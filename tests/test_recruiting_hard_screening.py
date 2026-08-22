"""沟通前硬筛规则测试。"""

from boss_agent_cli.recruiting.conversation_profile import ConversationProfile
from boss_agent_cli.recruiting.hard_screening import HardScreeningRules, ScreeningOutcome, evaluate_hard_screening


def test_explicit_salary_conflict_rejects_before_dialogue() -> None:
	"""候选人期望薪资明确超过岗位上限时可在 AI 前淘汰。"""
	profile = ConversationProfile.from_display_fields(expectation_text="广州 Java 18-25K")
	rules = HardScreeningRules(city="广州", salary_range="8-12K")

	decision = evaluate_hard_screening(rules, profile)

	assert decision.outcome is ScreeningOutcome.HARD_REJECTED
	assert decision.reason_codes == ("salary_mismatch",)


def test_missing_profile_field_allows_dialogue_confirmation() -> None:
	"""资料缺失不是淘汰依据，应由后续 AI 对话补充确认。"""
	profile = ConversationProfile.from_display_fields(communication_job="Java")
	rules = HardScreeningRules(city="广州", minimum_degree="本科", salary_range="8-12K")

	decision = evaluate_hard_screening(rules, profile)

	assert decision.outcome is ScreeningOutcome.DIALOGUE_ALLOWED_WITH_MISSING_FACTS
	assert set(decision.missing_fields) == {"city", "degree", "salary"}


def test_explicit_city_and_degree_conflicts_are_explainable() -> None:
	"""多个明确冲突应保留稳定原因，供 RPA 跳过时审计。"""
	profile = ConversationProfile.from_display_fields(
		education_text="广州大学 计算机科学 大专",
		expectation_text="深圳 Java 8-10K",
	)
	rules = HardScreeningRules(city="广州", minimum_degree="本科", salary_range="8-12K")

	decision = evaluate_hard_screening(rules, profile)

	assert decision.outcome is ScreeningOutcome.HARD_REJECTED
	assert decision.reason_codes == ("city_mismatch", "education_mismatch")


def test_bachelor_or_above_requirement_rejects_explicit_college_degree() -> None:
	"""“本科及以上”必须规范化为本科门槛，不能让页面明确的大专资料漏筛。"""
	profile = ConversationProfile.from_display_fields(education_text="2023-2027 广州职业学院 软件工程 大专")

	decision = evaluate_hard_screening(HardScreeningRules(minimum_degree="本科及以上"), profile)

	assert decision.outcome is ScreeningOutcome.HARD_REJECTED
	assert decision.reason_codes == ("education_mismatch",)


def test_multiple_expected_cities_allow_the_published_job_city() -> None:
	"""候选人期望写成“福州 & 广州”时，包含岗位城市就不应被误淘汰。"""
	profile = ConversationProfile.from_display_fields(expectation_text="福州 & 广州 Java 8-10K")

	decision = evaluate_hard_screening(HardScreeningRules(city="广州"), profile)

	assert decision.outcome is ScreeningOutcome.DIALOGUE_ALLOWED


def test_explicit_expected_city_without_job_city_rejects_before_ai() -> None:
	"""候选人明确只期望其它城市时必须在调用 AI 前结束，不浪费沟通额度。"""
	profile = ConversationProfile.from_display_fields(expectation_text="福州 Java 8-10K")

	decision = evaluate_hard_screening(HardScreeningRules(city="广州"), profile)

	assert decision.outcome is ScreeningOutcome.HARD_REJECTED
	assert decision.reason_codes == ("city_mismatch",)
