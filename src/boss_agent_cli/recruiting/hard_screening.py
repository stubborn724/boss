"""候选人沟通前的确定性硬筛规则。

缺失信息永远不会成为自动淘汰依据；本模块仅在页面资料已明确冲突时产生拒绝结果。
这样 RPA 可以跳过明显不匹配候选人，同时把不完整资料交给后续对话确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from boss_agent_cli.recruiting.conversation_profile import ConversationProfile


_DEGREE_RANK = {"高中": 1, "中专": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}
_SALARY_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


class ScreeningOutcome(StrEnum):
	"""硬筛结果，供命令层决定是否允许读取聊天消息。"""

	HARD_REJECTED = "hard_rejected"
	DIALOGUE_ALLOWED = "dialogue_allowed"
	DIALOGUE_ALLOWED_WITH_MISSING_FACTS = "dialogue_allowed_with_missing_facts"


@dataclass(frozen=True)
class HardScreeningRules:
	"""本地岗位可配置的首轮硬规则。"""

	city: str = ""
	minimum_degree: str = ""
	salary_range: str = ""


@dataclass(frozen=True)
class HardScreenDecision:
	"""规则输出必须同时给出稳定结果、原因和需补问字段。"""

	outcome: ScreeningOutcome
	reason_codes: tuple[str, ...] = ()
	missing_fields: tuple[str, ...] = ()


def evaluate_hard_screening(rules: HardScreeningRules, profile: ConversationProfile) -> HardScreenDecision:
	"""评估页面资料；只有明确反证才拒绝候选人。"""
	reasons: list[str] = []
	missing: list[str] = []
	if rules.city:
		cities = profile.expectation_cities or ((profile.expectation_city,) if profile.expectation_city else ())
		if not cities:
			missing.append("city")
		elif not any(_normalise(rules.city) == _normalise(city) for city in cities):
			reasons.append("city_mismatch")
	minimum_degree = _minimum_degree(rules.minimum_degree)
	if minimum_degree:
		if not profile.education_degree:
			missing.append("degree")
		elif _DEGREE_RANK.get(profile.education_degree, 0) < _DEGREE_RANK[minimum_degree]:
			reasons.append("education_mismatch")
	if rules.salary_range:
		if not profile.expected_salary:
			missing.append("salary")
		elif _salary_upper(profile.expected_salary) > _salary_upper(rules.salary_range):
			reasons.append("salary_mismatch")
	if reasons:
		return HardScreenDecision(ScreeningOutcome.HARD_REJECTED, tuple(reasons), tuple(missing))
	if missing:
		return HardScreenDecision(ScreeningOutcome.DIALOGUE_ALLOWED_WITH_MISSING_FACTS, (), tuple(missing))
	return HardScreenDecision(ScreeningOutcome.DIALOGUE_ALLOWED)


def _normalise(value: str) -> str:
	"""消除展示空白和大小写差异，不做不可靠的同义推断。"""
	return re.sub(r"\s+", "", value).casefold()


def _minimum_degree(requirement: str) -> str:
	"""从岗位学历文案解析最低门槛，兼容“本科及以上”等常见写法。

	岗位规则存的是展示文案而不是枚举。若用完整文案直接查询等级表，“本科及
	以上”会变成未知条件，让页面明确写着大专的候选人被错误放行。这里只识别
	受控学历词并取最低等级；无法识别时不淘汰，避免文案漂移带来误伤。
	"""
	found = [degree for degree in _DEGREE_RANK if degree in requirement]
	return min(found, key=_DEGREE_RANK.__getitem__) if found else ""


def _salary_upper(value: str) -> float:
	"""读取薪资区间上限，无法解析时返回零以避免误淘汰。"""
	numbers = [float(item) for item in _SALARY_NUMBER_RE.findall(value)]
	if not numbers:
		return 0
	upper = max(numbers)
	return upper * 10 if "万" in value else upper
