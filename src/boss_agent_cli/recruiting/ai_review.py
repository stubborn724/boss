"""招聘评估的可选 AI 语义层。

规则引擎（``screening`` / ``assessment``）是评分的唯一算术真源；本模块只解决
规则做不到的那一件事：**语义等价识别**。需求文档点名的场景是简历写「负责客户
开发、电话邀约、跟进成交」却没写「销售」，子串匹配必然漏判。

为了让模型的结论可审计、可复现、不可编造，这里对模型输出做四层收口：

1. **原文引用核对**：每条命中必须给出能在简历正文里逐字找到的引用，找不到就
   丢弃。模型无法凭空宣称候选人具备某项经验；
2. **标准白名单**：``criterion`` 必须来自岗位已声明的标准，对应需求「不得加入
   HR 没有表达过的硬性要求」；
3. **风险码表白名单**：风险只能落在 ``screening`` 既有的风险码上，避免模型自创
   风险类别导致前端和审计记录漂移；
4. **敏感属性过滤**：命中性别、婚育、民族、户籍、年龄等词的条目整条丢弃。

被丢弃的条目不是静默忽略，而是进入 ``rejected_claims``，让 HR 看到模型说过什么
以及为什么没被采信。任何一条都不改变「关键决策必须人工确认」的边界：语义命中只
是补充证据，风险提示只做展示，AI 不参与也不能绕过 ``build_review_gate``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, Iterable, Mapping, Sequence

from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.recruiting.models import JobProfile
from boss_agent_cli.recruiting.screening import (
	RISK_SIGNAL_CODES,
	SENSITIVE_TERMS,
	normalise_for_match,
)
from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value, sanitize_unicode_text

# 简历正文截断长度。招聘者视角的在线简历导出通常在 2-6 KB；一万字符足够覆盖
# 长简历，同时给岗位标准和输出留出上下文预算。
_MAX_RESUME_CHARS = 10_000
# 单条引用/问题的长度上限。引用只需要证明「这句话在简历里」，不需要整段搬运。
_MAX_QUOTE_CHARS = 200
_MAX_QUESTION_CHARS = 200
_MAX_SUMMARY_CHARS = 500
# 条目上限：防止模型返回超长列表把报告刷屏或撑爆本地工作区 JSON。
_MAX_FINDINGS = 30
_MAX_RISK_FINDINGS = 10
_MAX_QUESTIONS = 5
_MAX_REJECTED = 20
# 引用必须至少这么长才有核对价值：一两个字的「销售」既可能来自简历也可能来自
# 岗位标准本身，不足以证明模型真的读到了候选人的经历。
_MIN_QUOTE_CHARS = 4


class AIReviewError(RuntimeError):
	"""AI 语义评审失败（服务不可用、响应不是 JSON、结构不符合约定）。

	命令层捕获它后降级为纯规则评估，并在信封里说明降级原因；异常消息不携带
	简历正文，只描述失败类型。
	"""


@dataclass(frozen=True, slots=True)
class SemanticHit:
	"""一条已通过原文核对的岗位标准命中。"""

	criterion: str
	quote: str


@dataclass(frozen=True, slots=True)
class RiskFinding:
	"""一条已通过原文核对的风险提示，只用于展示，不改风险等级。"""

	code: str
	message: str
	quote: str


@dataclass(frozen=True)
class AIResumeReview:
	"""模型输出经过核对后的可审计结果。"""

	semantic_hits: tuple[SemanticHit, ...] = ()
	risk_findings: tuple[RiskFinding, ...] = ()
	follow_up_questions: tuple[str, ...] = ()
	summary: str = ""
	rejected_claims: tuple[str, ...] = ()
	model: str = ""

	def hit_quotes(self) -> dict[str, str]:
		"""投影成 ``{岗位标准: 原文引用}``，供评分层作为已核对证据消费。"""
		return {hit.criterion: hit.quote for hit in self.semantic_hits}

	def to_dict(self) -> dict[str, Any]:
		"""转换为可写入本地工作区和 JSON 信封的报告片段。"""
		return {
			"engine": "ai_semantic",
			"model": self.model,
			"summary": self.summary,
			"semantic_hits": [{"criterion": hit.criterion, "quote": hit.quote} for hit in self.semantic_hits],
			"risk_findings": [
				{"code": finding.code, "message": finding.message, "quote": finding.quote}
				for finding in self.risk_findings
			],
			"follow_up_questions": list(self.follow_up_questions),
			"rejected_claims": list(self.rejected_claims),
			# 明确写进报告：模型只能补充证据，不能决定推进。
			"advisory_only": True,
		}


@dataclass
class _Collector:
	"""收集核对结果与被拒原因，保证拒绝理由和采信条目一起可见。"""

	rejected: list[str] = field(default_factory=list)

	def reject(self, reason: str, detail: str) -> None:
		"""记录一条被丢弃的模型断言；同一原因不重复刷屏。"""
		text = f"{reason}：{detail}"[:_MAX_QUOTE_CHARS]
		if text not in self.rejected and len(self.rejected) < _MAX_REJECTED:
			self.rejected.append(text)


def declared_criteria(job: JobProfile) -> list[str]:
	"""返回岗位已声明的可命中标准。

	只包含「具备了会加分」的正向条件（必须项、加分项、结构化技能）。淘汰条件和
	风险信号不在其中：它们命中意味着扣分，不能通过语义层被当作优点采信。
	"""
	values = [*job.criteria.must_have, *job.criteria.nice_to_have, *job.skills]
	return list(dict.fromkeys(item.strip() for item in values if item and item.strip()))


def _contains_sensitive(*values: str) -> str:
	"""返回命中的第一个敏感词，未命中返回空串。"""
	joined = " ".join(values)
	return next((term for term in SENSITIVE_TERMS if term in joined), "")


def _text(value: Any, limit: int) -> str:
	"""把模型给出的任意值收敛为受限长度的纯文本。"""
	if value is None or isinstance(value, (dict, list, tuple, bool)):
		return ""
	return str(value).strip()[:limit]


def build_review_messages(job: JobProfile, resume_text: str) -> list[dict[str, str]]:
	"""构造语义评审消息；约束照搬需求文档的「候选人评估 Prompt」。"""
	payload = {
		"job": {
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
			"risk_signals": list(job.criteria.risk_signals),
		},
		"allowed_criteria": declared_criteria(job),
		"allowed_risk_codes": sorted(RISK_SIGNAL_CODES),
		"resume_text": sanitize_unicode_text(resume_text[:_MAX_RESUME_CHARS]),
		"output_schema": {
			"criteria_findings": [
				{"criterion": "必须来自 allowed_criteria 的原文", "matched": True, "quote": "简历中的逐字原文"},
			],
			"risk_findings": [
				{"code": "必须来自 allowed_risk_codes", "message": "风险说明", "quote": "简历中的逐字原文"},
			],
			"follow_up_questions": ["需要向候选人追问的问题"],
			"summary": "一段结论说明",
		},
	}
	return [
		{
			"role": "system",
			"content": (
				"你是严谨的人才评估助手，只负责语义证据发现，不负责决定是否录用。\n"
				"要求：\n"
				"1. 必须基于简历原文证据判断；每条 criteria_findings 都要给出能在简历里逐字找到的 quote，"
				"找不到原文就不要输出这一条；\n"
				"2. criterion 只能从 allowed_criteria 里原样选取，不得新增 HR 没有表达过的要求；\n"
				"3. code 只能从 allowed_risk_codes 里选取；\n"
				"4. 不确定的地方写进 follow_up_questions，不要猜测通过；\n"
				"5. 不得因性别、婚育、民族、户籍、年龄等无关因素做任何判断或说明；\n"
				"6. 不要输出分数或录用建议，评分由规则引擎负责；\n"
				"7. 只返回 JSON，不要包含 markdown 代码块或解释文字。"
			),
		},
		{"role": "user", "content": json.dumps(sanitize_json_value(payload), ensure_ascii=False)},
	]


def _strip_code_fence(raw: str) -> str:
	"""剥掉模型习惯性包裹的 ``` 代码块，与本地回复草稿解析口径一致。"""
	text = raw.strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
	return text


def _quote_found(quote: str, normalised_resume: str) -> bool:
	"""判断引用是否真的出自简历正文。

	归一化口径与筛选层一致（去空白、忽略大小写），因此模型换行、加空格或改用
	全角空格都不会造成误判；但只要它改写了实质文字，核对就会失败。
	"""
	needle = normalise_for_match(quote)
	return len(needle) >= _MIN_QUOTE_CHARS and needle in normalised_resume


def _parse_criteria_findings(
	rows: Sequence[Any],
	*,
	allowed: Mapping[str, str],
	normalised_resume: str,
	collector: _Collector,
) -> list[SemanticHit]:
	"""核对语义命中：标准必须已声明，引用必须逐字出现在简历中。"""
	hits: list[SemanticHit] = []
	seen: set[str] = set()
	for row in rows[:_MAX_FINDINGS]:
		if not isinstance(row, Mapping):
			continue
		criterion = _text(row.get("criterion"), _MAX_QUOTE_CHARS)
		quote = _text(row.get("quote"), _MAX_QUOTE_CHARS)
		if row.get("matched") is False:
			continue
		canonical = allowed.get(normalise_for_match(criterion), "")
		if not canonical:
			collector.reject("岗位标准未声明该条件", criterion or "（空条件）")
			continue
		if sensitive := _contains_sensitive(criterion, quote):
			collector.reject(f"包含敏感人口属性（{sensitive}）", canonical)
			continue
		if not _quote_found(quote, normalised_resume):
			collector.reject("引用未在简历原文中找到", f"{canonical} ←「{quote or '（空引用）'}」")
			continue
		if canonical in seen:
			continue
		seen.add(canonical)
		hits.append(SemanticHit(criterion=canonical, quote=quote))
	return hits


def _parse_risk_findings(
	rows: Sequence[Any],
	*,
	normalised_resume: str,
	collector: _Collector,
) -> list[RiskFinding]:
	"""核对风险提示：码表必须已知，引用必须逐字出现在简历中。"""
	findings: list[RiskFinding] = []
	seen: set[str] = set()
	for row in rows[:_MAX_RISK_FINDINGS]:
		if not isinstance(row, Mapping):
			continue
		code = _text(row.get("code"), 64)
		message = _text(row.get("message"), _MAX_QUOTE_CHARS)
		quote = _text(row.get("quote"), _MAX_QUOTE_CHARS)
		if code not in RISK_SIGNAL_CODES:
			collector.reject("风险码不在既有码表内", code or "（空风险码）")
			continue
		if sensitive := _contains_sensitive(message, quote):
			collector.reject(f"包含敏感人口属性（{sensitive}）", code)
			continue
		if not _quote_found(quote, normalised_resume):
			collector.reject("风险引用未在简历原文中找到", f"{code} ←「{quote or '（空引用）'}」")
			continue
		if code in seen:
			continue
		seen.add(code)
		findings.append(RiskFinding(code=code, message=message or code, quote=quote))
	return findings


def _parse_questions(rows: Sequence[Any], collector: _Collector) -> list[str]:
	"""收集追问问题；敏感提问直接丢弃并记录原因。"""
	questions: list[str] = []
	for row in rows:
		question = _text(row, _MAX_QUESTION_CHARS)
		if not question:
			continue
		if sensitive := _contains_sensitive(question):
			collector.reject(f"追问包含敏感人口属性（{sensitive}）", question)
			continue
		if question not in questions:
			questions.append(question)
		if len(questions) >= _MAX_QUESTIONS:
			break
	return questions


def _as_sequence(value: Any) -> list[Any]:
	"""容错取列表；模型返回单个对象或 null 时退化为空列表。"""
	if isinstance(value, list):
		return value
	if isinstance(value, Mapping):
		return [value]
	return []


def parse_review(
	raw: str,
	*,
	resume_text: str,
	criteria: Iterable[str],
	model: str = "",
) -> AIResumeReview:
	"""解析并核对模型输出；结构不合法时抛 :class:`AIReviewError`。

	单条不合格只丢这一条（进 ``rejected_claims``），只有整体不是 JSON 对象才
	视为失败 —— 那说明模型没有遵守协议，继续解析等于猜测。
	"""
	try:
		data = json.loads(_strip_code_fence(raw))
	except JSONDecodeError as exc:
		raise AIReviewError("AI 语义评审返回的不是合法 JSON") from exc
	if not isinstance(data, Mapping):
		raise AIReviewError("AI 语义评审必须返回 JSON 对象")

	collector = _Collector()
	allowed = {normalise_for_match(item): item for item in criteria if item.strip()}
	normalised_resume = normalise_for_match(resume_text)
	summary = _text(data.get("summary"), _MAX_SUMMARY_CHARS)
	if _contains_sensitive(summary):
		collector.reject("结论包含敏感人口属性", "已丢弃 summary")
		summary = ""
	return AIResumeReview(
		semantic_hits=tuple(
			_parse_criteria_findings(
				_as_sequence(data.get("criteria_findings")),
				allowed=allowed,
				normalised_resume=normalised_resume,
				collector=collector,
			)
		),
		risk_findings=tuple(
			_parse_risk_findings(
				_as_sequence(data.get("risk_findings")),
				normalised_resume=normalised_resume,
				collector=collector,
			)
		),
		follow_up_questions=tuple(_parse_questions(_as_sequence(data.get("follow_up_questions")), collector)),
		summary=summary,
		rejected_claims=tuple(collector.rejected),
		model=model,
	)


def review_resume(service: AIService, job: JobProfile, resume_text: str) -> AIResumeReview:
	"""调用 AI 完成一份简历的语义评审。

	岗位没有任何可命中的正向标准时直接返回空结果，不做无意义的外部调用 ——
	此时模型既没有可选的 ``criterion``，输出也一定会被白名单全部拒绝。
	"""
	criteria = declared_criteria(job)
	if not criteria or not resume_text.strip():
		return AIResumeReview(model=service.model)
	try:
		raw = service.chat(build_review_messages(job, resume_text))
	except AIServiceError as exc:
		raise AIReviewError(f"AI 语义评审调用失败：{exc}") from exc
	return parse_review(raw, resume_text=resume_text, criteria=criteria, model=service.model)


__all__ = [
	"AIResumeReview",
	"AIReviewError",
	"RiskFinding",
	"SemanticHit",
	"build_review_messages",
	"declared_criteria",
	"parse_review",
	"review_resume",
]
