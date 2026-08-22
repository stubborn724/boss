"""招聘岗位标准解析和候选人规则评估。

这里提供一个无需外部模型即可运行的确定性基线。它不是替代大模型，而是让
工作台在未配置 AI 时仍能给出可解释、可测试、必须人工确认的初筛结果；未来
接入 AI 时可以复用同一组领域对象，只替换证据生成器，不改变外部动作边界。
"""

from __future__ import annotations

import re
from hashlib import sha256
from collections.abc import Mapping
from typing import Any, Iterable

from boss_agent_cli.recruiting.ai_review import AIResumeReview
from boss_agent_cli.recruiting.models import AssessmentReport, JobProfile, RecruitingCriteria
from boss_agent_cli.recruiting.screening import (
	SENSITIVE_TERMS,
	evaluate_job_readiness,
	extract_candidate_profile,
	screen_candidate,
)

# 敏感人口属性词表只有一份真源（``screening.SENSITIVE_TERMS``）：标准解析和 AI
# 语义层必须按同一份定义丢弃条目，各自维护一份副本迟早会分叉。
_SENSITIVE_TERMS = SENSITIVE_TERMS

_SPLIT_RE = re.compile(r"[\n\r；;。.!！?？]+")
_LEADING_MARK_RE = re.compile(r"^(?:[-*•·]|\d+[.)、．])\s*")
_MUST_PREFIXES = ("必须", "需要", "要求", "希望", "最好有")
_NICE_PREFIXES = ("优先", "加分", "最好", "有则更佳")
_REJECT_PREFIXES = ("不接受", "不要", "淘汰", "拒绝", "不能")
_RISK_PREFIXES = ("风险", "风险信号", "注意")
_EDUCATION_RE = re.compile(r"^(?:学历\s*[：:]\s*)?(博士|硕士|本科|大专|高中)(及以上|以上)?$")
_EXPERIENCE_RE = re.compile(r"(?:至少|不少于|要求)?\s*(\d+)\s*(?:年|年以上|年工作经验|年经验)")
_INDUSTRY_RE = re.compile(r"^行业\s*[：:]\s*(.+)$", re.IGNORECASE)
_SKILLS_RE = re.compile(r"^(?:技能|技术栈|熟悉)\s*[：:]\s*(.+)$", re.IGNORECASE)


def _clean_item(value: str) -> str:
	"""清理列表标记和分类前后缀，保留 HR 原话的业务含义。"""
	text = _LEADING_MARK_RE.sub("", value.strip())
	for prefix in _MUST_PREFIXES + _NICE_PREFIXES + _REJECT_PREFIXES + _RISK_PREFIXES:
		if text.startswith(prefix):
			text = text[len(prefix) :].strip(" ：:，,、")
			break
	for suffix in ("优先", "加分", "最好", "有则更佳"):
		if text.endswith(suffix):
			text = text[: -len(suffix)].strip(" ：:，,、")
			break
	if text.startswith("有") and len(text) > 1:
		text = text[1:].strip(" ：:，,、")
	return text.strip(" ：:，,")


def _append_unique(target: list[str], value: str) -> None:
	"""按出现顺序去重，避免报告中的证据重复刷屏。"""
	if value and value not in target:
		target.append(value)


def parse_natural_language_job(text: str) -> tuple[RecruitingCriteria, dict[str, object], list[str]]:
	"""同时解析岗位结构化字段和四类自由文本标准。

	结构化字段只识别明确格式，无法确定的句子仍交给原有保守解析器并要求
	人工确认。被识别的字段不会再次进入 ``must_have``，避免“本科及以上”被
	当作候选人简历中的普通关键词而失去独立评分意义。
	"""
	structured: dict[str, object] = {
		"education_requirement": "",
		"min_experience_years": None,
		"industry": "",
		"skills": [],
	}
	remaining: list[str] = []
	for raw_item in _SPLIT_RE.split(text or ""):
		item = _LEADING_MARK_RE.sub("", raw_item.strip())
		if not item:
			continue
		education_match = _EDUCATION_RE.match(item)
		if education_match:
			structured["education_requirement"] = f"{education_match.group(1)}{education_match.group(2) or ''}"
			continue
		experience_match = _EXPERIENCE_RE.search(item)
		if experience_match and ("经验" in item or "工作" in item or "年以上" in item):
			structured["min_experience_years"] = int(experience_match.group(1))
			continue
		industry_match = _INDUSTRY_RE.match(item)
		if industry_match:
			structured["industry"] = industry_match.group(1).strip()
			continue
		skills_match = _SKILLS_RE.match(item)
		if skills_match:
			raw_skills = re.split(r"[,，、/／|]+", skills_match.group(1))
			structured["skills"] = [skill.strip() for skill in raw_skills if skill.strip()]
			continue
		remaining.append(item)
	criteria, warnings = parse_natural_language_criteria("；".join(remaining))
	return criteria, structured, warnings


def parse_natural_language_criteria(text: str) -> tuple[RecruitingCriteria, list[str]]:
	"""将 HR 的自然语言标准拆成四类规则并返回合规提醒。

	解析器只做保守的关键词分类；不确定的句子进入 ``must_have``，后续由人工
	复核，避免模型或启发式规则偷偷扩大招聘要求。敏感人口属性整句丢弃并提醒，
	从源头保证它不会影响候选人评分。
	"""
	criteria = RecruitingCriteria()
	warnings: list[str] = []
	for raw_item in _SPLIT_RE.split(text or ""):
		original = _LEADING_MARK_RE.sub("", raw_item.strip())
		if not original:
			continue
		is_reject = any(original.startswith(prefix) or prefix in original[:4] for prefix in _REJECT_PREFIXES)
		is_nice = any(original.startswith(prefix) or original.endswith(suffix) for prefix in _NICE_PREFIXES for suffix in ("优先", "加分", "最好", "有则更佳"))
		# 规则编辑器会用“风险：”序列化明确的风险信号。优先判断该分类，不能
		# 再依赖正文是否恰好含有“频繁”或“空窗”等有限关键词。
		is_risk = any(original.startswith(prefix) for prefix in _RISK_PREFIXES)
		item = _clean_item(original)
		if not item:
			continue
		if any(term in item for term in _SENSITIVE_TERMS):
			warning = f"已忽略包含敏感人口属性的条件：{item}"
			_append_unique(warnings, warning)
			continue
		if is_reject:
			_append_unique(criteria.reject_if, item)
		elif is_nice:
			_append_unique(criteria.nice_to_have, item)
		elif is_risk or "风险" in item or "担心" in item or "频繁" in item or "空窗" in item:
			_append_unique(criteria.risk_signals, item)
		else:
			_append_unique(criteria.must_have, item)
	return criteria, warnings


def _normalise(value: str) -> str:
	"""去除空白和大小写差异，让中英文混合条件可做稳定包含匹配。"""
	return re.sub(r"\s+", "", value.casefold())


def _matches(resume_text: str, criterion: str) -> bool:
	"""判断简历是否明确提到一条标准；不做无证据的语义推断。"""
	needle = _normalise(criterion)
	normalised_resume = _normalise(resume_text)
	if not needle:
		return False
	if needle in normalised_resume:
		return True
	# 平台简历常把“招商加盟经验”写成“做过招商加盟”。仅兼容稳定的
	# “经验”词尾，不扩展到同义词，仍要求简历出现岗位标准的业务短语。
	if needle.endswith("经验"):
		base = needle[: -len("经验")]
		return len(base) >= 2 and base in normalised_resume
	return False


def _matched(criteria: Iterable[str], resume_text: str, semantic_hits: Mapping[str, str] | None = None) -> list[str]:
	"""返回在简历中找到的标准原文。

	``semantic_hits`` 是可选 AI 语义层已通过原文核对的等价表达命中。规则匹配和
	语义命中在这里等价采信，但调用方会分别记录证据来源，保证「为什么算命中」
	始终可追溯。
	"""
	verified = semantic_hits or {}
	return [criterion for criterion in criteria if _matches(resume_text, criterion) or criterion in verified]


def _level(score: int) -> str:
	"""按需求文档的分段返回人可读等级。"""
	if score >= 90:
		return "强烈推荐"
	if score >= 80:
		return "推荐"
	if score >= 70:
		return "待确认"
	if score >= 60:
		return "风险较高"
	return "不推荐"


def _knowledge_value(document: object, key: str) -> str:
	"""兼容 KnowledgeDocument 和测试/扩展层使用的映射对象。"""
	if isinstance(document, Mapping):
		return str(document.get(key) or "")
	return str(getattr(document, key, "") or "")


def _question_id(job_id: str, question: str, source_ids: list[str]) -> str:
	"""按岗位、问题文本和来源生成稳定的本地问题标识。"""
	seed = "|".join((job_id, question, *sorted(source_ids)))
	return f"question-{sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _knowledge_question_item(job: JobProfile, document: object) -> dict[str, Any] | None:
	"""把一份知识文档转换成一条带引用的问题。"""
	document_id = _knowledge_value(document, "document_id")
	title = _knowledge_value(document, "title") or "岗位知识"
	content = _knowledge_value(document, "content")
	if not document_id or not content.strip():
		return None
	fragments = [
		part.strip(" -*#•·")
		for part in re.split(r"[\n\r。！？!?；;]+", content)
		if part.strip(" -*#•·")
	]
	snippet = (fragments[0] if fragments else title).strip()
	if len(snippet) > 90:
		snippet = snippet[:87].rstrip() + "..."
	question = f"请结合“{title}”中的业务事实，说明你会如何处理“{snippet}”？"
	source_ids = [document_id]
	source_updated_at = _knowledge_value(document, "updated_at")
	version_seed = "|".join((document_id, source_updated_at, content))
	version = f"v{sha256(version_seed.encode('utf-8')).hexdigest()[:10]}"
	return {
		"question_id": _question_id(job.job_id, question, source_ids),
		"question": question,
		"question_version": version,
		"source_ids": source_ids,
		"source_titles": [title],
		"kind": "knowledge",
		"follow_up_questions": [
			"你在这个场景中具体承担了哪一部分工作？",
			"当时结果如何，有哪些可以核对的指标或事实？",
		],
	}


def _criteria_question_parts(criterion: str) -> tuple[str, list[str]]:
	"""按岗位标准中的业务词生成主问题和两条人工追问。"""
	text = criterion.strip()
	compact = text.casefold()
	if "crm" in compact:
		return (
			"请具体说明你使用 CRM 管理客户开发的流程，以及如何维护跟进记录？",
			["你在 CRM 中维护过哪些关键字段？", "请举一个通过记录和复盘提升转化的案例。"],
		)
	if "电话" in text or "电销" in text:
		return (
			f"请具体说明你在“{text}”相关工作中的通话流程，以及如何处理客户异议？",
			["你通常如何判断客户是否值得继续跟进？", "请给出一次从首次通话到结果的完整案例。"],
		)
	if "客户" in text or "销售" in text or "招商" in text:
		return (
			f"请具体说明你在“{text}”方面负责的环节、目标和实际结果？",
			["当客户暂时没有兴趣时，你会如何判断原因并推进？", "请说明一个可核对的业绩或转化结果。"],
		)
	return (
		f"方便具体说下你在“{text}”方面做过什么，以及结果如何吗？",
		["你在其中承担的具体职责是什么？", "如果再做一次，你会调整哪个环节？"],
	)


def generate_professional_question_items(
	job: JobProfile,
	*,
	knowledge_documents: Iterable[object] | None = None,
	limit: int = 5,
) -> list[dict[str, Any]]:
	"""生成 3-5 条带版本和来源引用的岗位专业问题。

	知识问题优先进入结果，保证岗位知识库一旦配置就能真正参与问答；没有
	知识文档时仍保留旧版的岗位标准和通用销售问题，兼容历史工作区。
	"""
	limit = max(3, min(limit, 5))
	items: list[dict[str, Any]] = []
	for document in knowledge_documents or []:
		item = _knowledge_question_item(job, document)
		if item is not None:
			items.append(item)
	for criterion in [*job.criteria.must_have, *job.criteria.nice_to_have]:
		question, follow_ups = _criteria_question_parts(criterion)
		items.append(
			{
				"question_id": _question_id(job.job_id, question, []),
				"question": question,
				"question_version": "v1",
				"source_ids": [],
				"source_titles": [],
				"kind": "criteria",
				"follow_up_questions": follow_ups,
			}
		)
	for risk_signal in job.criteria.risk_signals:
		clean_risk = risk_signal.strip()
		if not clean_risk:
			continue
		question = f"请说明你在“{clean_risk}”方面遇到过的具体情况、原因和改进方式？"
		items.append(
			{
				"question_id": _question_id(job.job_id, question, []),
				"question": question,
				"question_version": "v1",
				"source_ids": [],
				"source_titles": [],
				"kind": "risk",
				"risk_signal": clean_risk,
				"follow_up_questions": [
					"这类情况最近一次发生在什么时候？",
					"你采取了什么措施，后续结果如何？",
				],
			}
		)
	defaults = [
		"请举一个从陌生客户到成交的完整案例，最难的环节是什么？",
		"遇到客户说暂时没兴趣时，你通常会怎样判断和推进？",
		"你对这个岗位的工作地点、薪资和工作节奏有哪些需要确认的地方？",
	]
	for question in defaults:
		items.append(
			{
				"question_id": _question_id(job.job_id, question, []),
				"question": question,
				"question_version": "v1",
				"source_ids": [],
				"source_titles": [],
				"kind": "default",
				"follow_up_questions": [],
			}
		)
	unique: list[dict[str, Any]] = []
	seen: set[str] = set()
	for item in items:
		question = str(item.get("question") or "")
		if question and question not in seen:
			seen.add(question)
			unique.append(item)
	return unique[:limit]


def generate_professional_questions(
	job: JobProfile,
	*,
	knowledge_documents: Iterable[object] | None = None,
	limit: int = 5,
) -> list[str]:
	"""兼容旧调用方，只返回问题文本；新调用方应使用带元数据的条目 API。"""
	return [
		str(item["question"])
		for item in generate_professional_question_items(
			job, knowledge_documents=knowledge_documents, limit=limit,
		)
	]


def generate_message_templates(job: JobProfile, *, candidate_name: str) -> dict[str, str]:
	"""生成需要 HR 人工复制确认的四阶段话术草稿。"""
	name = candidate_name.strip() or "你好"
	location = job.city or "工作地点"
	salary = job.salary_range or "薪资和提成方案"
	return {
		"greeting": f"{name}，你好！我们正在招聘{job.name}，主要工作地点在{location}。看过你的经历，想先了解下你目前还在看机会吗？",
		"basic_confirmation": f"这个岗位在{location}，薪资为{salary}。想先确认下工作地点、薪资和岗位节奏是否符合你的预期？",
		"resume_exchange": "你的经历和岗位匹配度不错。如果方便，我们先交换简历，我再帮你做进一步匹配，并同步后续面试安排。",
		"interview_invite": "我们初步看下来比较匹配，想邀请你和面试官做一次正式沟通，主要聊过往经历、岗位细节和薪资情况。你什么时候方便？",
	}


def _score_professional_answers(
	job: JobProfile, answers: Iterable[Mapping[str, object]],
) -> tuple[int | None, list[str], list[dict[str, Any]]]:
	"""逐题计算专业回答分数，并按当前岗位阈值生成可追溯状态。"""
	rows = list(answers)
	if not rows:
		return None, [], []
	scores: list[int] = []
	evidence: list[str] = []
	breakdown: list[dict[str, Any]] = []
	criteria_terms = [*job.criteria.must_have, *job.criteria.nice_to_have]
	for index, row in enumerate(rows, start=1):
		answer = str(row.get("answer") or "")
		normalised = _normalise(answer)
		score = 35
		length_points = 0
		if len(answer.strip()) >= 30:
			length_points = 25
		elif len(answer.strip()) >= 12:
			length_points = 12
		score += length_points
		criteria_hit = any(_normalise(term) in normalised for term in criteria_terms if term.strip())
		if criteria_hit:
			score += 20
		business_hit = any(keyword in normalised for keyword in ("客户", "成交", "业绩", "异议", "跟进", "复盘"))
		if business_hit:
			score += 20
		score = min(100, score)
		scores.append(score)
		question_id = str(row.get("question_id") or "未标识问题")
		question_version = str(row.get("question_version") or "v1")
		question = str(row.get("question") or "")
		source_ids = row.get("source_ids")
		clean_source_ids = [str(item) for item in source_ids if str(item).strip()] if isinstance(source_ids, list) else []
		source_suffix = ""
		if clean_source_ids:
			source_suffix = f"，来源 {','.join(clean_source_ids)}"
		evidence.append(
			f"第 {index} 条回答已记录，问题 {question_id}（{question_version}）"
			f"{source_suffix}，长度与岗位相关证据评分 {score} 分"
		)
		breakdown.append(
			{
				"question_id": question_id,
				"question_version": question_version,
				"question": question,
				"score": score,
				"status": "pass" if score >= job.professional_qa_threshold else "follow_up",
				"answer_length": len(answer.strip()),
				"length_points": length_points,
				"criteria_hit": criteria_hit,
				"business_evidence": business_hit,
				"source_ids": clean_source_ids,
			}
		)
	return round(sum(scores) / len(scores)), evidence, breakdown


_SCORE_COMPONENTS: tuple[str, ...] = (
	"hard_match",
	"experience",
	"professional_qa",
	"communication",
	"stability",
	"location_salary",
)
_EDUCATION_RANK = {"高中": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}
_RESUME_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:年|年以上)(?:工作经验|经验)?")


def _normalised_weights(raw_weights: Mapping[str, object]) -> dict[str, int]:
	"""清洗岗位权重；坏数据回退默认值，避免评分除零或丢失维度。"""
	weights: dict[str, int] = {}
	for component in _SCORE_COMPONENTS:
		raw_value = raw_weights.get(component, 0)
		try:
			value = int(raw_value) if isinstance(raw_value, (int, float, str)) else 0
		except (TypeError, ValueError):
			value = 0
		weights[component] = max(0, value)
	if sum(weights.values()) <= 0:
		weights = {component: 1 for component in _SCORE_COMPONENTS}
	return weights


def _structured_match_score(job: JobProfile, resume_text: str) -> tuple[int, list[str], list[str]]:
	"""评估学历、行业和技能等结构化硬条件，并返回脱敏证据。"""
	normalised_resume = _normalise(resume_text)
	score = 100
	matched: list[str] = []
	risk: list[str] = []
	if job.education_requirement:
		required_name = job.education_requirement.replace("及以上", "").replace("以上", "").strip()
		required_rank = _EDUCATION_RANK.get(required_name, 0)
		found_rank = max(
			(rank for name, rank in _EDUCATION_RANK.items() if _normalise(name) in normalised_resume),
			default=0,
		)
		if required_rank and found_rank >= required_rank:
			matched.append(f"学历满足：{job.education_requirement}")
		elif required_rank:
			score -= 20
			risk.append(f"未找到学历证据：{job.education_requirement}")
	if job.industry:
		if _normalise(job.industry) in normalised_resume:
			matched.append(f"行业匹配：{job.industry}")
		else:
			score -= 15
			risk.append(f"未找到行业证据：{job.industry}")
	for skill in job.skills:
		if _normalise(skill) in normalised_resume:
			matched.append(f"技能匹配：{skill}")
		else:
			score -= 8
			risk.append(f"未找到技能证据：{skill}")
	return max(0, min(100, score)), matched, risk


def _experience_score(job: JobProfile, resume_text: str) -> tuple[int, list[str]]:
	"""从简历显式年限中计算经验维度；未知时保持中性并提示人工核对。"""
	if job.min_experience_years is None:
		return 80, ["岗位未设置最低工作年限"]
	values = [float(match.group(1)) for match in _RESUME_YEARS_RE.finditer(resume_text)]
	if not values:
		return 50, [f"未找到至少 {job.min_experience_years} 年经验的明确证据"]
	actual = max(values)
	if actual >= job.min_experience_years:
		return 100, [f"工作年限证据满足：约 {actual:g} 年"]
	return 35, [f"工作年限证据不足：约 {actual:g} 年，要求 {job.min_experience_years} 年"]


def _engine_label(has_qa: bool, has_ai: bool) -> str:
	"""拼出报告的引擎标记，让审计记录能区分这一份分数是怎么算出来的。"""
	parts = ["rules"]
	if has_ai:
		parts.append("ai")
	if has_qa:
		parts.append("qa")
	return "+".join(parts)


def score_candidate(
	job: JobProfile,
	*,
	candidate_id: str,
	candidate_name: str,
	resume_text: str,
	answers: Iterable[Mapping[str, object]] | None = None,
	knowledge_documents: Iterable[object] | None = None,
	ai_review: AIResumeReview | None = None,
) -> AssessmentReport:
	"""按岗位独立标准计算可解释评分，并强制保留人工复核节点。

	``ai_review`` 是可选的 AI 语义评审结果。它只以两种方式进入报告：

	* ``semantic_hits`` 参与命中判定 —— 每条都已在 :mod:`ai_review` 里核对过能在
	  简历原文逐字找到，因此和规则匹配同样可验证；
	* ``risk_findings`` 只作为证据行展示，不进入 ``risk_points``，也不改
	  ``screening.risk.level`` —— 风险严重程度是判断而非事实，不能让模型左右门禁。

	淘汰条件与风险信号一律不看 AI 命中：它们命中意味着扣分，若允许语义层放行就
	等于让模型给自己开后门。
	"""
	criteria = job.criteria
	semantic_hits = ai_review.hit_quotes() if ai_review is not None else {}
	matched_must = _matched(criteria.must_have, resume_text, semantic_hits)
	matched_nice = _matched(criteria.nice_to_have, resume_text, semantic_hits)
	hit_reject = _matched(criteria.reject_if, resume_text)
	hit_risk = _matched(criteria.risk_signals, resume_text)

	must_ratio = len(matched_must) / len(criteria.must_have) if criteria.must_have else 1.0
	nice_ratio = len(matched_nice) / len(criteria.nice_to_have) if criteria.nice_to_have else 0.0
	resume_score = round(50 + must_ratio * 25 + nice_ratio * 20 - len(hit_reject) * 45 - len(hit_risk) * 10)
	resume_score = max(0, min(100, resume_score))
	structured_score, structured_matched, structured_risk = _structured_match_score(job, resume_text)
	resume_score = max(0, min(100, round(resume_score * 0.8 + structured_score * 0.2)))
	if hit_reject:
		# reject_if 是岗位明确的淘汰条件，必须成为硬性降权，而不是被其他
		# 中性维度的默认分数“冲平”，否则报告会给出过于乐观的等级。
		resume_score = min(resume_score, 10)
	matched_points = [*matched_must, *matched_nice, *structured_matched]
	risk_points = [*hit_reject, *hit_risk, *structured_risk]
	answer_rows = list(answers or [])
	professional_qa_score, professional_qa_evidence, professional_qa_breakdown = _score_professional_answers(job, answer_rows)
	screening = screen_candidate(
		job,
		resume_text,
		answers=answer_rows,
		professional_qa_score=professional_qa_score,
		professional_qa_breakdown=professional_qa_breakdown,
		semantic_hits=semantic_hits,
	)
	experience_score, experience_evidence = _experience_score(job, resume_text)
	component_scores: dict[str, int] = {
		"hard_match": resume_score,
		"experience": experience_score,
		# 未配置专业问答的岗位保留原有简历评估逻辑；启用专业问答时，缺少实答
		# 只能说明简历初步命中，不能以默认高分进入严格候选人池。
		"professional_qa": (
			professional_qa_score
			if professional_qa_score is not None
			else (60 if hit_reject else (80 if not job.professional_qa_enabled else 50))
		),
		"communication": 70 if hit_reject else 80,
		"stability": max(45, 80 - len(hit_risk) * 10 - len(hit_reject) * 15),
		"location_salary": 80 if not job.city or _normalise(job.city) in _normalise(resume_text) else 70,
	}
	weights = _normalised_weights(job.weights)
	total_weight = sum(weights.values())
	score_breakdown: dict[str, dict[str, Any]] = {}
	for component in _SCORE_COMPONENTS:
		score_breakdown[component] = {
			"score": component_scores[component],
			"weight": weights[component],
			"weighted_score": round(component_scores[component] * weights[component] / total_weight, 2),
		}
	score = round(sum(item["weighted_score"] for item in score_breakdown.values()))
	# 证据分两类来源：规则子串命中写“简历明确提到”，AI 语义命中必须带上原文引用，
	# 这样报告读者永远能判断某一项是被谁、凭什么算作命中的。
	evidence = [
		f"简历明确提到：{item}" if item not in semantic_hits or _matches(resume_text, item)
		else f"AI 语义命中（已核对原文）：{item} ←「{semantic_hits[item]}」"
		for item in matched_points
	]
	evidence.extend(f"简历命中风险：{item}" for item in risk_points)
	evidence.extend(experience_evidence)
	if ai_review is not None:
		# AI 风险提示只进证据，不进 risk_points：进了就会改稳定性分和风险等级。
		evidence.extend(
			f"AI 风险提示（仅供人工核对）：{finding.message} ←「{finding.quote}」"
			for finding in ai_review.risk_findings
		)
		evidence.extend(f"AI 未采信的断言：{claim}" for claim in ai_review.rejected_claims)
	missing = [item for item in criteria.must_have if item not in matched_must]
	if missing:
		evidence.extend(f"简历未找到明确证据：{item}" for item in missing)
	if not resume_text.strip():
		evidence.append("简历正文为空，无法完成可靠评估")

	if score >= 80:
		next_action = "人工确认后再考虑交换简历"
	elif score >= 70:
		next_action = "继续追问缺失条件并人工复核"
	else:
		next_action = "暂不推荐，由 HR 人工确认是否礼貌结束"

	question_items = generate_professional_question_items(job, knowledge_documents=knowledge_documents)
	# AI 的追问问题排在岗位问题之后：岗位知识库和标准生成的问题是业务基线，
	# 模型补充的追问只用于覆盖它没能确认的地方。
	if ai_review is not None:
		asked = {str(item.get("question") or "") for item in question_items}
		question_items = [
			*question_items,
			*(
				{
					"question_id": _question_id(job.job_id, question, []),
					"question": question,
					"question_version": "v1",
					"source_ids": [],
					"source_titles": [],
					"kind": "ai_follow_up",
					"follow_up_questions": [],
				}
				for question in ai_review.follow_up_questions
				if question not in asked
			),
		]
	return AssessmentReport(
		job_id=job.job_id,
		candidate_id=candidate_id,
		candidate_name=candidate_name,
		final_score=score,
		level=_level(score),
		decision="待人工确认",
		matched_points=matched_points,
		risk_points=risk_points,
		evidence=evidence,
		next_action=next_action,
		review_required=True,
		engine=_engine_label(professional_qa_score is not None, ai_review is not None),
		professional_questions=[str(item["question"]) for item in question_items],
		professional_question_items=question_items,
		answer_count=len(answer_rows),
		professional_qa_enabled=job.professional_qa_enabled,
		professional_qa_score=professional_qa_score,
		professional_qa_evidence=professional_qa_evidence,
		professional_qa_breakdown=professional_qa_breakdown,
		screening=screening,
		score_breakdown=score_breakdown,
		message_templates=generate_message_templates(job, candidate_name=candidate_name),
		ai_review=ai_review.to_dict() if ai_review is not None else {},
	)


__all__ = [
	"evaluate_job_readiness",
	"extract_candidate_profile",
	"generate_message_templates",
	"generate_professional_questions",
	"generate_professional_question_items",
	"parse_natural_language_criteria",
	"parse_natural_language_job",
	"screen_candidate",
	"score_candidate",
]
