"""将 HR 的自然语言招聘需求直接转成可编辑岗位标准。

本模块只负责岗位标准的解释和落盘编排，不参与候选人评分，也不访问 BOSS。
模型可用时负责理解口语化描述；模型未配置、超时或返回不符合契约时退回既有
规则解析。两条路径最终都交给 ``RecruitingWorkspace`` 校验和保存，确保 Web、
CLI 及未来 Agent 入口始终遵循相同的数据边界。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from boss_agent_cli.ai.service import AIServiceError
from boss_agent_cli.recruiting.assessment import parse_natural_language_job
from boss_agent_cli.recruiting.screening import SENSITIVE_TERMS
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


class JobStandardAI(Protocol):
	"""岗位标准 Agent 所需的最小 AI 协议，便于替换和测试。"""

	model: str

	def chat(self, messages: list[dict[str, str]]) -> str:
		"""返回一段 JSON 文本。"""


_MAX_TEXT_LENGTH = 4_000
_MAX_LIST_ITEMS = 24
_MAX_ITEM_LENGTH = 160
_LIST_KEYS = ("must_have", "nice_to_have", "reject_if", "risk_signals", "skills", "notes")
_RULE_KEYS = ("must_have", "nice_to_have", "reject_if", "risk_signals")


@dataclass(frozen=True)
class JobStandardAnalysis:
	"""经过安全收敛后的岗位标准，供创建与更新流程共同使用。"""

	name: str
	city: str
	salary_range: str
	education_requirement: str
	min_experience_years: int | None
	industry: str
	skills: list[str]
	must_have: list[str]
	nice_to_have: list[str]
	reject_if: list[str]
	risk_signals: list[str]
	notes: list[str]
	warnings: list[str]
	source: str

	def criteria_text(self) -> str:
		"""转换为既有工作区认可的自然语言规则格式。

		工作区目前以 ``parse_natural_language_job`` 为唯一规则落盘入口；在这里
		保留类别前缀，让 AI 输出和规则回退都走同一解析、审计与评分链路。
		"""
		items = [
			*[f"必须：{item}" for item in self.must_have],
			*[f"优先：{item}" for item in self.nice_to_have],
			*[f"淘汰：{item}" for item in self.reject_if],
			*[f"风险：{item}" for item in self.risk_signals],
		]
		return "；".join(items)

	def rule_payload(self) -> dict[str, object]:
		"""投影出规则编辑器允许读取的字段，隔离 AI 原始响应与岗位基础信息。

		Web 编辑器只需要四类招聘规则及其生成来源。显式构造白名单可以避免未来
		为分析对象增加内部字段后，被页面无意透出；同时不返回用户输入的原始
		自然语言，避免它在编辑岗位时重复占用界面空间。
		"""
		return {
			"must_have": list(self.must_have),
			"nice_to_have": list(self.nice_to_have),
			"reject_if": list(self.reject_if),
			"risk_signals": list(self.risk_signals),
			"source": self.source,
			"warnings": list(self.warnings),
			"notes": list(self.notes),
		}


def _clean_text(value: object, *, limit: int = _MAX_ITEM_LENGTH) -> str:
	"""收敛模型任意值为受限纯文本，拒绝嵌套对象伪装成字段内容。"""
	if value is None or isinstance(value, (bool, list, tuple, dict)):
		return ""
	return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _contains_sensitive(value: str) -> bool:
	"""检测不应参与招聘筛选的人口属性，保持与评分层同一词表。"""
	return any(term in value for term in SENSITIVE_TERMS)


def _clean_list(value: object, *, warnings: list[str]) -> list[str]:
	"""过滤重复、过长和敏感的列表项，并记录用户可理解的过滤原因。"""
	if not isinstance(value, list):
		return []
	items: list[str] = []
	for raw in value[:_MAX_LIST_ITEMS]:
		item = _clean_text(raw)
		if not item or item in items:
			continue
		if _contains_sensitive(item):
			warnings.append(f"已忽略包含敏感人口属性的条件：{item}")
			continue
		items.append(item)
	return items


def _strip_code_fence(raw: str) -> str:
	"""兼容模型常见的 JSON 代码块包装，不接受夹带的额外说明。"""
	text = raw.strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
	return text.strip()


def _parse_years(value: object) -> int | None:
	"""只接受合理整数年限，异常输出退回未知而不替模型猜测。"""
	if isinstance(value, bool) or not isinstance(value, (str, int, float)):
		return None
	try:
		years = int(value) if value is not None and str(value).strip() else None
	except (TypeError, ValueError):
		return None
	return years if years is None or 0 <= years <= 100 else None


def _optional_score(value: object) -> int | None:
	"""解析可选岗位评分阈值，非法值交给工作区统一拒绝。"""
	if value is None or value == "":
		return None
	if isinstance(value, bool):
		raise ValueError("评分阈值必须是整数")
	try:
		return int(value)
	except (TypeError, ValueError) as exc:
		raise ValueError("评分阈值必须是整数") from exc


def build_job_standard_messages(requirements: str, *, current_name: str = "") -> list[dict[str, str]]:
	"""构建受限 JSON 提示词，明确 AI 只解释标准、不作录用决定。"""
	payload = {
		"requirements": requirements[:_MAX_TEXT_LENGTH],
		"current_name": current_name,
		"output_schema": {
			"name": "岗位名称",
			"city": "工作城市或空字符串",
			"salary_range": "薪资范围或空字符串",
			"education_requirement": "学历要求或空字符串",
			"min_experience_years": "整数或 null",
			"industry": "行业经历要求或空字符串",
			"skills": ["必备技能"],
			"must_have": ["必须条件"],
			"nice_to_have": ["加分条件"],
			"reject_if": ["淘汰条件"],
			"risk_signals": ["风险识别信号"],
			"notes": ["标准解释或需人工补充项"],
		},
	}
	return [
		{
			"role": "system",
			"content": (
				"你是招聘岗位标准助手。把 HR 的自然语言需求拆成可编辑的岗位标准，只返回 JSON。\n"
				"规则：1. 只提取用户明确表达或合理概括的专业要求；2. 不得写入年龄、性别、婚育、民族、户籍、籍贯等人口属性；\n"
				"3. 不能创建候选人评分、录用结论或平台操作；4. 不确定的字段保持空值，并在 notes 说明；\n"
				"5. 每个列表最多 24 条，保持简洁、可被 HR 修改。"
			),
		},
		{"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
	]


class JobStandardAgent:
	"""以 AI 优先、规则兜底的方式直接保存岗位标准。"""

	def __init__(self, *, ai_service: JobStandardAI | None = None) -> None:
		"""注入已由命令层配置的 AI 客户端，禁止本模块自行读取密钥。"""
		self._ai_service = ai_service

	def analyze(self, requirements: str, *, current_name: str = "") -> JobStandardAnalysis:
		"""分析需求并产生安全的结构化标准，不写入任何存储。"""
		clean_requirements = _clean_text(requirements, limit=_MAX_TEXT_LENGTH)
		if not clean_requirements:
			raise ValueError("岗位需求不能为空")
		if self._ai_service is not None:
			try:
				raw = self._ai_service.chat(build_job_standard_messages(clean_requirements, current_name=current_name))
				return self._from_ai_response(raw, requirements=clean_requirements, current_name=current_name)
			except (AIServiceError, TypeError, ValueError, json.JSONDecodeError):
				# AI 的可用性不能阻断招聘配置。认证、限流、网络异常和结构化
				# 响应错误均在此处降级，保证用户填写的岗位标准仍可立即保存。
				pass
		return self._from_rules(clean_requirements, current_name=current_name)

	def create_job(self, workspace: RecruitingWorkspace, *, requirements: str, hard_conditions: Mapping[str, object] | None = None) -> dict[str, Any]:
		"""分析后立即创建已生效岗位，避免生成等待确认的草案记录。"""
		analysis = self.analyze(requirements)
		values = self._apply_hard_conditions(analysis, hard_conditions)
		result = workspace.create_job(**values, status="published")
		# 工作区会再次解析已清理后的规则，因此它本身不会知道 Agent 在第一层
		# 丢弃了哪些敏感项。合并提示可让页面明确说明实际保存边界。
		return {
			**result,
			"warnings": list(dict.fromkeys([*result.get("warnings", []), *analysis.warnings])),
			"analysis_source": analysis.source,
			"notes": analysis.notes,
		}

	def update_job(self, workspace: RecruitingWorkspace, *, job_id: str, requirements: str, hard_conditions: Mapping[str, object] | None = None) -> dict[str, Any]:
		"""重新解释需求并更新现有岗位；既有工作区会要求重新发布修改后的标准。"""
		current = workspace.store.get_job(job_id)
		if current is None:
			raise KeyError(job_id)
		analysis = self.analyze(requirements, current_name=current.name)
		values = self._apply_hard_conditions(analysis, hard_conditions)
		values = self._merge_with_current_job(values, current)
		# BOSS 同步的职位名称来自平台职位管理，是候选人归属的事实键之一。
		# 自然语言仅用于补充筛选标准，即使模型误把补充描述识别为新职位，也不能
		# 改写这一名称；手工岗位则仍保留原有的可编辑行为。
		if current.source == "boss" and current.platform_job_id:
			values["name"] = current.name
		result = workspace.update_job(job_id, **values, publish_immediately=True)
		return {
			**result,
			"warnings": list(dict.fromkeys([*result.get("warnings", []), *analysis.warnings])),
			"analysis_source": analysis.source,
			"notes": analysis.notes,
		}

	def apply_rules(
		self,
		workspace: RecruitingWorkspace,
		*,
		job_id: str,
		rules: Mapping[str, object],
		scoring: Mapping[str, object] | None = None,
	) -> dict[str, Any]:
		"""保存 HR 审核后的四类规则，不重新解析或覆盖 BOSS 的职位基础信息。

		AI 分析只是生成建议，HR 可以在规则编辑器删除或补充条目后再提交。因此
		此处不调用 ``analyze``，也不接收自然语言、岗位名称或硬条件；只接受四个
		明确的规则列表，并交给工作区的窄写入接口保存，保持岗位状态不变。
		"""
		if set(rules) != set(_RULE_KEYS):
			raise ValueError("规则仅支持必须条件、加分条件、淘汰条件和风险信号")
		warnings: list[str] = []
		cleaned: dict[str, list[str]] = {}
		for key in _RULE_KEYS:
			value = rules.get(key)
			if not isinstance(value, list):
				raise ValueError("四类规则都必须是列表")
			cleaned[key] = _clean_list(value, warnings=warnings)
		if not any(cleaned.values()):
			raise ValueError("请至少保留一条招聘规则")
		criteria_text = "；".join(
			[
				*[f"必须：{item}" for item in cleaned["must_have"]],
				*[f"优先：{item}" for item in cleaned["nice_to_have"]],
				*[f"淘汰：{item}" for item in cleaned["reject_if"]],
				*[f"风险：{item}" for item in cleaned["risk_signals"]],
			]
		)
		scoring_values = dict(scoring or {})
		raw_weights = scoring_values.get("weights")
		weights = {str(key): int(value) for key, value in raw_weights.items()} if isinstance(raw_weights, Mapping) else None
		result = workspace.update_job_rules(
			job_id,
			criteria_text=criteria_text,
			weights=weights,
			screening_threshold=_optional_score(scoring_values.get("screening_threshold")),
			recommendation_threshold=_optional_score(scoring_values.get("recommendation_threshold")),
			professional_qa_threshold=_optional_score(scoring_values.get("professional_qa_threshold")),
		)
		return {
			**result,
			"warnings": list(dict.fromkeys([*result.get("warnings", []), *warnings])),
			"rule_source": "reviewed",
		}

	def _from_ai_response(self, raw: str, *, requirements: str, current_name: str) -> JobStandardAnalysis:
		"""解析 AI JSON 并按字段白名单过滤，输出异常时抛错触发规则回退。"""
		data = json.loads(_strip_code_fence(raw))
		if not isinstance(data, Mapping):
			raise ValueError("岗位标准 AI 返回格式无效")
		warnings: list[str] = []
		name = _clean_text(data.get("name")) or current_name or self._fallback_name(requirements)
		if _contains_sensitive(name):
			warnings.append(f"已忽略包含敏感人口属性的岗位名称：{name}")
			name = current_name or self._fallback_name(requirements)
		return JobStandardAnalysis(
			name=name,
			city=_clean_text(data.get("city"), limit=80),
			salary_range=_clean_text(data.get("salary_range"), limit=80),
			education_requirement=_clean_text(data.get("education_requirement"), limit=80),
			min_experience_years=_parse_years(data.get("min_experience_years")),
			industry=_clean_text(data.get("industry"), limit=120),
			skills=_clean_list(data.get("skills"), warnings=warnings),
			must_have=_clean_list(data.get("must_have"), warnings=warnings),
			nice_to_have=_clean_list(data.get("nice_to_have"), warnings=warnings),
			reject_if=_clean_list(data.get("reject_if"), warnings=warnings),
			risk_signals=_clean_list(data.get("risk_signals"), warnings=warnings),
			notes=_clean_list(data.get("notes"), warnings=warnings),
			warnings=warnings,
			source="ai",
		)

	def _from_rules(self, requirements: str, *, current_name: str) -> JobStandardAnalysis:
		"""复用既有解析器构造同一领域对象，保证无 AI 时评分口径不漂移。"""
		criteria, structured, warnings = parse_natural_language_job(requirements)
		raw_skills = structured.get("skills")
		skills = raw_skills if isinstance(raw_skills, list) else []
		return JobStandardAnalysis(
			name=current_name or self._fallback_name(requirements),
			city="",
			salary_range="",
			education_requirement=str(structured.get("education_requirement") or ""),
			min_experience_years=_parse_years(structured.get("min_experience_years")),
			industry=str(structured.get("industry") or ""),
			skills=[str(item) for item in skills if str(item).strip()],
			must_have=list(criteria.must_have),
			nice_to_have=list(criteria.nice_to_have),
			reject_if=list(criteria.reject_if),
			risk_signals=list(criteria.risk_signals),
			notes=["未使用 AI 服务，已按本地规则直接设置岗位标准。"],
			warnings=list(warnings),
			source="rules",
		)

	@staticmethod
	def _fallback_name(requirements: str) -> str:
		"""从首句取得保守名称；无法识别时使用可编辑通用名。"""
		first = re.split(r"[；;。.!！?？\n\r]", requirements, maxsplit=1)[0].strip()
		first = re.sub(r"^(?:我想招|招聘|招一位|需要一位|需要招|岗位)\s*", "", first)
		return first[:120] or "待命名岗位"

	@staticmethod
	def _apply_hard_conditions(analysis: JobStandardAnalysis, hard_conditions: Mapping[str, object] | None) -> dict[str, Any]:
		"""把设置抽屉的显式字段覆盖 Agent 解析，设置优先于推测。"""
		overrides = hard_conditions or {}
		def text(key: str, fallback: str) -> str:
			value = _clean_text(overrides.get(key), limit=120)
			return value if value else fallback
		return {
			"name": text("name", analysis.name),
			"city": text("city", analysis.city),
			"salary_range": text("salary_range", analysis.salary_range),
			"education_requirement": text("education_requirement", analysis.education_requirement),
			"min_experience_years": _parse_years(overrides.get("min_experience_years")) if "min_experience_years" in overrides else analysis.min_experience_years,
			"industry": text("industry", analysis.industry),
			"skills": analysis.skills,
			"criteria_text": analysis.criteria_text(),
		}

	@staticmethod
	def _merge_with_current_job(values: dict[str, Any], current: Any) -> dict[str, Any]:
		"""将自然语言结果作为增量补充，保留 BOSS 同步与既有人工字段。

		BOSS 职位同步是岗位名称和平台状态的事实来源；用户输入的自然语言只是
		补充筛选标准。因此 Agent 没有明确给出的城市、薪资、学历、年限、行业
		与技能必须回退到现有值，四类标准则按类别合并，避免一次补充描述抹掉
		已配置的必备条件或淘汰条件。
		"""
		for field_name in ("name", "city", "salary_range", "education_requirement", "industry"):
			if not str(values.get(field_name) or "").strip():
				values[field_name] = str(getattr(current, field_name, "") or "")
		if values.get("min_experience_years") is None:
			values["min_experience_years"] = getattr(current, "min_experience_years", None)
		current_skills = [str(item).strip() for item in getattr(current, "skills", []) if str(item).strip()]
		values["skills"] = list(dict.fromkeys([*current_skills, *values.get("skills", [])]))
		current_criteria = getattr(current, "criteria", None)
		criteria_items = [
			*[f"必须：{item}" for item in getattr(current_criteria, "must_have", [])],
			*[f"优先：{item}" for item in getattr(current_criteria, "nice_to_have", [])],
			*[f"淘汰：{item}" for item in getattr(current_criteria, "reject_if", [])],
			*[f"风险：{item}" for item in getattr(current_criteria, "risk_signals", [])],
			str(values.get("criteria_text") or ""),
		]
		values["criteria_text"] = "；".join(item for item in criteria_items if item.strip())
		return values


__all__ = ["JobStandardAgent", "JobStandardAnalysis", "build_job_standard_messages"]
