"""岗位就绪检查和候选人三层初筛。

本模块把需求文档中的“硬条件、语义匹配、风险识别”收敛成一个可测试的
本地领域服务。它只读取用户已经导入的简历文本和岗位配置，不调用平台、不
发送消息，也不根据性别、婚育、年龄、民族或户籍等敏感人口属性做判断。

规则解析是可解释的基线：当证据缺失时返回“待人工确认”，而不是猜测候选人
一定符合。未来接入模型时可以替换语义证据生成器，但返回结构和人工确认门槛
保持稳定，避免前端和审计记录随着模型版本漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from collections.abc import Iterable, Mapping
from typing import Any

from boss_agent_cli.recruiting.models import JobProfile

_EDUCATION_RANK = {"高中": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}
_EXPERIENCE_RE = re.compile(r"(?:工作经验|工作经历|经验|从业年限)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(?:年|年以上)?")
_FALLBACK_EXPERIENCE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:年|年以上)(?:工作经验|经验)?")
# 只解析明确的年月范围，避免把教育年份、项目编号等单独年份误当成工作经历。
_EXPERIENCE_RANGE_RE = re.compile(
	r"(?P<start_year>20\d{2})(?:[./-](?P<start_month>\d{1,2}))?\s*"
	r"(?:-|~|至|到)\s*"
	r"(?:(?P<end_year>20\d{2})(?:[./-](?P<end_month>\d{1,2}))?|(?P<current>至今|现在|目前))",
	re.IGNORECASE,
)
_SALARY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[-~至到]\s*(\d+(?:\.\d+)?))?\s*(K|k|千|万)?")
_FIELD_RE = re.compile(
	r"(?:^|[\n\r])\s*[-*]?\s*(?:城市|所在城市|期望城市|工作地点|期望工作地|"
	r"期望薪资|薪资期望|期望待遇|学历|最高学历|最近职位|当前职位|目标职位|"
	r"行业|所属行业|活跃度|最近活跃|技能|技术栈|熟悉)\s*[：:]\s*([^\n\r]+)",
	re.IGNORECASE,
)
_FIELD_LABELS = {
	"城市": "city",
	"所在城市": "city",
	"期望城市": "city",
	"工作地点": "city",
	"期望工作地": "city",
	"期望薪资": "expected_salary",
	"薪资期望": "expected_salary",
	"期望待遇": "expected_salary",
	"学历": "education",
	"最高学历": "education",
	"最近职位": "recent_role",
	"当前职位": "recent_role",
	"目标职位": "recent_role",
	"行业": "industry",
	"所属行业": "industry",
	"活跃度": "activity",
	"最近活跃": "activity",
	"技能": "skills",
	"技术栈": "skills",
	"熟悉": "skills",
}

# 只对常见业务短语做有限同义兼容，不能把任意相近词直接当成硬条件通过。
_SEMANTIC_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
	("电话销售", ("电话销售", "电销", "电话邀约", "电话开发", "陌生电话")),
	("客户开发", ("客户开发", "客户拓展", "商务拓展", "bd", "陌生客户")),
	("销售", ("销售", "成交", "签单", "业绩", "客户开发", "商务拓展")),
	("跟进成交", ("跟进成交", "跟进", "成交", "签单")),
	("客服", ("客服", "客户服务", "售后服务")),
)
# 敏感人口属性词表和风险码表是跨模块共享的公开词表：岗位标准解析器和可选的 AI
# 语义层都用这一份定义过滤输入输出，否则模型可以自创风险类别或绕过敏感属性约束，
# 前端和审计记录也会随之漂移。两者都是声明式常量，改动即等于改动对外契约。
#
# 词表同时收录「婚育」和「已婚 / 未育」这类真实写法：只写类别名挡不住模型或 HR
# 用口语表达同一个歧视性判断。刻意不收录单字「婚」——「婚庆行业销售经验」是合法
# 的业务条件，按单字过滤会把它一起误杀。
SENSITIVE_TERMS = (
	"性别", "婚育", "婚姻", "民族", "户籍", "籍贯", "年龄",
	"已婚", "未婚", "离婚", "已育", "未育", "生育", "怀孕", "孕期", "产假",
)
RISK_PATTERNS: tuple[tuple[str, str, str], ...] = (
	("frequent_job_change", "频繁跳槽", "high"),
	("employment_gap", "存在空窗期或待业描述", "medium"),
	("short_resume", "简历信息偏少，关键字段缺失", "medium"),
	("direction_mismatch", "最近职位与岗位方向缺少明确关联", "medium"),
	("salary_mismatch", "期望薪资明显高于岗位范围", "medium"),
	("qa_inconsistency", "专业回答与岗位要求缺少一致证据", "medium"),
)
RISK_SIGNAL_CODES = frozenset(code for code, _, _ in RISK_PATTERNS)
_LEGACY_QA_THRESHOLD = 60
_TIMELINE_GAP_MONTHS = 6
_TIMELINE_SHORT_TENURE_MONTHS = 12
_TIMELINE_SHORT_TENURE_COUNT = 2


def _normalise(value: str) -> str:
	"""去掉空白和大小写差异，便于中文短语做稳定包含匹配。"""
	return re.sub(r"\s+", "", value.casefold())


def normalise_for_match(value: str) -> str:
	"""对外暴露筛选层的归一化口径。

	可选的 AI 语义层要用完全相同的口径核对「模型给出的引用是否真的出自简历」，
	口径一旦分叉，同一条引用在核对时通过、在匹配时落空，报告就会自相矛盾。
	"""
	return _normalise(value)


def _unique(values: Iterable[str]) -> list[str]:
	"""按出现顺序去重，保证报告在同一输入下稳定。"""
	return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _field_values(text: str) -> dict[str, str]:
	"""提取简历中的显式标签字段；未标注的自然语言不强行猜测。"""
	values: dict[str, str] = {}
	for match in _FIELD_RE.finditer(text):
		label = match.group(0).split(":", 1)[0].split("：", 1)[0].strip(" -*\t")
		for candidate_label, field_name in _FIELD_LABELS.items():
			if label.endswith(candidate_label):
				values.setdefault(field_name, match.group(1).strip())
				break
	return values


def _parse_salary_upper(value: str) -> float | None:
	"""把薪资文本转换为统一的上限数值，仅用于相对风险提示。"""
	match = _SALARY_RE.search(value)
	if match is None:
		return None
	try:
		number = float(match.group(2) or match.group(1))
	except ValueError:
		return None
	unit = (match.group(3) or "").lower()
	if unit in {"k", "千"}:
		return number * 1_000
	if unit == "万":
		return number * 10_000
	return number


def _parse_experience(text: str) -> float | None:
	"""优先读取工作经验标签，兼容“3 年工作经验”这类无标签表达。"""
	match = _EXPERIENCE_RE.search(text) or _FALLBACK_EXPERIENCE_RE.search(text)
	if match is None:
		return None
	try:
		return float(match.group(1))
	except ValueError:
		return None


def _parse_experience_timeline(text: str) -> list[tuple[int, int]]:
	"""提取可确认的工作经历月份区间，供风险提示使用。

	简历格式差异很大，这里只接受 ``YYYY.MM-YYYY.MM``、``YYYY年M月-至今``
	等明确范围；无法确认的自然语言不参与计算。月份换算成连续整数后，空窗和
	任职时长都能用同一套边界规则判断，避免按字符串排序产生跨年错误。
	"""
	normalised = re.sub(r"(?<=\d)年", ".", text or "")
	normalised = re.sub(r"(?<=\d)月", "", normalised)
	now = datetime.now(timezone.utc)
	current_month = now.year * 12 + now.month
	ranges: set[tuple[int, int]] = set()
	for match in _EXPERIENCE_RANGE_RE.finditer(normalised):
		try:
			start_year = int(match.group("start_year"))
			start_month = int(match.group("start_month") or 1)
			if match.group("current"):
				end_month_index = current_month
			else:
				end_year = int(match.group("end_year"))
				end_month = int(match.group("end_month") or 12)
				if not 1 <= end_month <= 12:
					continue
				end_month_index = end_year * 12 + end_month
			start_month_index = start_year * 12 + start_month
		except (TypeError, ValueError):
			continue
		if 1 <= start_month <= 12 and start_month_index <= end_month_index:
			ranges.add((start_month_index, end_month_index))
	return sorted(ranges)


def _split_skills(value: str) -> list[str]:
	"""将技能标签拆成短语，拒绝把整段描述当作一个技能。"""
	return _unique(re.split(r"[,，、/／|；;]+", value))


@dataclass
class CandidateProfile:
	"""从简历显式证据提取的候选人画像。

	画像只保留与岗位匹配有关的业务字段。敏感人口属性即使出现在原文中也
	不会进入这个对象，从而让后续评估器没有机会误用它们。
	"""

	city: str = ""
	expected_salary: str = ""
	education: str = ""
	experience_years: float | None = None
	recent_role: str = ""
	industry: str = ""
	activity: str = ""
	skills: list[str] = field(default_factory=list)
	missing_fields: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		"""转换为前端可展示的脱敏画像，不复制简历正文。"""
		return {
			"city": self.city,
			"expected_salary": self.expected_salary,
			"education": self.education,
			"experience_years": self.experience_years,
			"recent_role": self.recent_role,
			"industry": self.industry,
			"activity": self.activity,
			"skills": list(self.skills),
			"missing_fields": list(self.missing_fields),
		}


def extract_candidate_profile(resume_text: str) -> CandidateProfile:
	"""从简历的显式标签提取结构化画像，缺失字段保留为空值。"""
	text = resume_text or ""
	values = _field_values(text)
	known = {
		"city": values.get("city", ""),
		"expected_salary": values.get("expected_salary", ""),
		"education": values.get("education", ""),
		"recent_role": values.get("recent_role", ""),
		"industry": values.get("industry", ""),
		"activity": values.get("activity", ""),
	}
	if not known["education"]:
		known["education"] = next((name for name in _EDUCATION_RANK if name in text), "")
	if not known["activity"]:
		known["activity"] = next((term for term in ("刚刚活跃", "今日活跃", "本周活跃", "最近活跃") if term in text), "")
	# 最近职位只有在简历明确标注时才进入风险判断。直接把任意包含“客户”
	# 的经历句子当成职位，会把正常的业务描述误报为方向偏离。
	skills = _split_skills(values.get("skills", ""))
	experience_years = _parse_experience(text)
	# 只把会影响硬条件判断的字段列为“信息不足”。最近职位和行业是风险
	# 参考信息，缺失时不应把一个本来完整的候选人直接标成短简历。
	missing_fields = [
		field_name
		for field_name, value in (
			("city", known["city"]),
			("expected_salary", known["expected_salary"]),
			("education", known["education"]),
		)
		if not value
	]
	if experience_years is None:
		missing_fields.append("experience_years")
	if not skills:
		missing_fields.append("skills")
	return CandidateProfile(
		city=known["city"],
		expected_salary=known["expected_salary"],
		education=known["education"],
		experience_years=experience_years,
		recent_role=known["recent_role"],
		industry=known["industry"],
		activity=known["activity"],
		skills=skills,
		missing_fields=_unique(missing_fields),
	)


def evaluate_job_readiness(job: JobProfile) -> dict[str, Any]:
	"""返回岗位发布前的必答项，避免标准不完整却直接进入筛选。"""
	required: list[tuple[str, bool, str]] = [
		("city", bool(job.city.strip()), "工作城市是什么？"),
		("salary_range", bool(job.salary_range.strip()), "薪资范围和提成方式是什么？"),
		("education_requirement", bool(job.education_requirement.strip()), "最低学历要求是什么？"),
		("min_experience_years", job.min_experience_years is not None, "最低工作年限是多少？"),
		(
			"core_skills",
			bool(job.skills or job.criteria.must_have),
			"最核心的业务能力或技能至少列一项。",
		),
	]
	missing = [key for key, present, _ in required if not present]
	questions = [question for key, present, question in required if not present]
	return {
		"ready": not missing,
		"missing_required_fields": missing,
		"clarification_questions": questions,
		"summary": "岗位标准已具备筛选所需的必答项" if not missing else f"还需要补充 {len(missing)} 项岗位信息",
		"review_required": True,
	}


def _semantic_match(criterion: str, resume_text: str, profile: CandidateProfile) -> bool:
	"""对岗位短语做有限同义匹配，仍要求简历出现对应业务证据。"""
	normalised_resume = _normalise(resume_text)
	normalised_criterion = _normalise(criterion)
	if not normalised_criterion:
		return False
	if normalised_criterion in normalised_resume:
		return True
	for key, terms in _SEMANTIC_GROUPS:
		if _normalise(key) in normalised_criterion or normalised_criterion.startswith(_normalise(key)):
			return any(_normalise(term) in normalised_resume for term in terms)
	return any(_normalise(term) in _normalise("、".join(profile.skills)) for term in (criterion,))


def _hard_filter(job: JobProfile, profile: CandidateProfile) -> dict[str, Any]:
	"""检查城市、薪资、学历、工作年限等硬条件并区分失败和信息缺失。"""
	mismatches: list[str] = []
	unknowns: list[str] = []
	checks: list[dict[str, str]] = []
	if job.city:
		if not profile.city:
			unknowns.append("缺少城市证据")
		elif _normalise(job.city) not in _normalise(profile.city) and _normalise(profile.city) not in _normalise(job.city):
			mismatches.append(f"城市不匹配：岗位 {job.city}，候选人 {profile.city}")
		else:
			checks.append({"field": "city", "status": "pass", "message": f"城市匹配：{profile.city}"})
	if job.salary_range:
		job_upper = _parse_salary_upper(job.salary_range)
		candidate_upper = _parse_salary_upper(profile.expected_salary) if profile.expected_salary else None
		if candidate_upper is None:
			unknowns.append("缺少期望薪资证据")
		elif job_upper is not None and candidate_upper > job_upper:
			mismatches.append(f"期望薪资高于岗位范围：{profile.expected_salary} / {job.salary_range}")
		else:
			checks.append({"field": "salary_range", "status": "pass", "message": "期望薪资在岗位范围内"})
	if job.education_requirement:
		required_name = job.education_requirement.replace("及以上", "").replace("以上", "").strip()
		required_rank = _EDUCATION_RANK.get(required_name, 0)
		found_rank = _EDUCATION_RANK.get(profile.education, 0)
		if not profile.education:
			unknowns.append("缺少学历证据")
		elif required_rank and found_rank < required_rank:
			mismatches.append(f"学历不满足：要求 {job.education_requirement}，候选人 {profile.education}")
		else:
			checks.append({"field": "education_requirement", "status": "pass", "message": f"学历满足：{profile.education}"})
	if job.min_experience_years is not None:
		if profile.experience_years is None:
			unknowns.append("缺少工作年限证据")
		elif profile.experience_years < job.min_experience_years:
			mismatches.append(f"工作年限不足：约 {profile.experience_years:g} 年 / 要求 {job.min_experience_years} 年")
		else:
			checks.append({"field": "min_experience_years", "status": "pass", "message": "工作年限满足"})
	status = "fail" if mismatches else ("review" if unknowns else "pass")
	return {"status": status, "mismatches": mismatches, "unknowns": unknowns, "checks": checks}


def _semantic_layer(
	job: JobProfile,
	resume_text: str,
	profile: CandidateProfile,
	semantic_hits: Mapping[str, str] | None = None,
) -> dict[str, Any]:
	"""计算岗位硬技能和自然语言标准的可解释语义命中。

	``semantic_hits`` 是可选 AI 语义层交来的「已核对原文」命中（标准 -> 引用）。
	它只能补充规则漏判的等价表达，且每条都已在 ``ai_review`` 里通过逐字核对；
	命中来源会写进 ``evidence``，让 HR 分得清哪一项是规则匹配、哪一项由 AI 补上。
	"""
	requirements = _unique([*job.skills, *job.criteria.must_have])
	verified = dict(semantic_hits or {})
	if not requirements:
		return {"score": 80, "matched": [], "missing": [], "evidence": ["岗位未配置核心技能，语义匹配保持中性"]}
	matched = [
		item for item in requirements
		if _semantic_match(item, resume_text, profile) or item in verified
	]
	ai_only = [item for item in matched if item in verified and not _semantic_match(item, resume_text, profile)]
	missing = [item for item in requirements if item not in matched]
	score = round(len(matched) / len(requirements) * 100)
	evidence = [f"已命中 {len(matched)}/{len(requirements)} 项岗位能力"]
	evidence.extend(f"AI 语义命中（已核对原文）：{item} ←「{verified[item]}」" for item in ai_only)
	return {
		"score": score,
		"matched": matched,
		"missing": missing,
		"ai_matched": ai_only,
		"evidence": evidence,
	}


def _risk_layer(
	job: JobProfile,
	profile: CandidateProfile,
	resume_text: str,
	answers: list[Mapping[str, object]],
) -> dict[str, Any]:
	"""识别简历过短、空窗、跳槽和问答不足等风险信号。"""
	normalised = _normalise(resume_text)
	signals: list[dict[str, str]] = []
	if any(term in normalised for term in ("频繁跳槽", "一年换了", "半年换了")):
		signals.append({"code": "frequent_job_change", "message": "频繁跳槽", "severity": "high"})
	if any(term in normalised for term in ("空窗期", "待业", "离职至今")):
		signals.append({"code": "employment_gap", "message": "存在空窗期或待业描述", "severity": "medium"})
	# 当简历给出了明确年月时，补充可解释的时间线风险；文字关键词优先保留，
	# 避免同一类风险被重复展示。阈值刻意保守，不能凭一段模糊描述判定跳槽。
	timeline = _parse_experience_timeline(resume_text)
	if len(timeline) >= 2:
		longest_gap = 0
		covered_end = timeline[0][1]
		for start_month, end_month in timeline[1:]:
			if start_month > covered_end:
				longest_gap = max(longest_gap, start_month - covered_end - 1)
			covered_end = max(covered_end, end_month)
		if longest_gap >= _TIMELINE_GAP_MONTHS and not any(item["code"] == "employment_gap" for item in signals):
			signals.append(
				{
					"code": "employment_gap",
					"message": f"经历时间线存在约 {longest_gap} 个月空窗期",
					"severity": "medium",
				}
			)
		short_tenures = sum(
			1 for start_month, end_month in timeline if end_month - start_month + 1 < _TIMELINE_SHORT_TENURE_MONTHS
		)
		if (
			len(timeline) >= 3
			and short_tenures >= _TIMELINE_SHORT_TENURE_COUNT
			and not any(item["code"] == "frequent_job_change" for item in signals)
		):
			signals.append(
				{
					"code": "frequent_job_change",
					"message": f"经历时间线有 {short_tenures} 段不足一年任职",
					"severity": "high",
				}
			)
	if len(re.sub(r"\s+", "", resume_text)) < 45 or len(profile.missing_fields) >= 2:
		signals.append({"code": "short_resume", "message": "简历信息偏少，关键字段缺失", "severity": "medium"})
	if job.name and profile.recent_role:
		job_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", _normalise(job.name)))
		role_text = _normalise(profile.recent_role)
		if job_terms and not any(term in role_text for term in job_terms):
			signals.append({"code": "direction_mismatch", "message": "最近职位与岗位方向缺少明确关联", "severity": "medium"})
	job_upper = _parse_salary_upper(job.salary_range) if job.salary_range else None
	candidate_upper = _parse_salary_upper(profile.expected_salary) if profile.expected_salary else None
	if job_upper is not None and candidate_upper is not None and candidate_upper > job_upper * 1.3:
		signals.append({"code": "salary_mismatch", "message": "期望薪资明显高于岗位范围", "severity": "medium"})
	if answers and any(len(str(row.get("answer") or "").strip()) < 12 for row in answers):
		signals.append({"code": "qa_inconsistency", "message": "专业回答缺少岗位相关的具体证据", "severity": "medium"})
	severity = "low"
	if any(item["severity"] == "high" for item in signals):
		severity = "high"
	elif signals:
		severity = "medium"
	return {"level": severity, "signals": signals, "summary": "未发现明显风险" if not signals else "；".join(item["message"] for item in signals)}


def _professional_qa_layer(
	job: JobProfile,
	answers: list[Mapping[str, object]],
	professional_qa_score: int | None,
	professional_qa_breakdown: list[Mapping[str, object]] | None = None,
	*,
	enabled: bool = True,
) -> dict[str, Any]:
	"""把专业问答转换成逐题状态；关闭岗位开关时返回明确的无需状态。"""
	breakdown = [dict(item) for item in (professional_qa_breakdown or [])]
	threshold = job.professional_qa_threshold
	if not enabled:
		return {
			"status": "not_required",
			"threshold": threshold,
			"score": professional_qa_score,
			"follow_up_questions": [],
			"question_scores": breakdown,
			"failed_question_ids": [],
			"message": "当前岗位未启用 BOSS 专业问答",
		}
	failed_question_ids = [
		str(item.get("question_id") or "未标识问题")
		for item in breakdown
		if str(item.get("status") or "") != "pass"
	]
	if not answers or professional_qa_score is None:
		return {
			"status": "not_started",
			"threshold": threshold,
			"score": professional_qa_score,
			"follow_up_questions": [],
			"question_scores": breakdown,
			"failed_question_ids": failed_question_ids,
		}
	if professional_qa_score < threshold or failed_question_ids:
		follow_up_questions = [
			f"请补充问题 {question_id} 的具体案例：你本人做了什么、结果如何、能否给出可量化数据？"
			for question_id in failed_question_ids
		]
		if not follow_up_questions:
			follow_up_questions = [
				"请补充一个具体案例：你本人做了什么、结果如何、能否给出可量化数据？",
				f"请结合“{job.name}”岗位，说明遇到困难时你会如何判断和推进。",
			]
		return {
			"status": "follow_up",
			"threshold": threshold,
			"score": professional_qa_score,
			"follow_up_questions": follow_up_questions,
			"question_scores": breakdown,
			"failed_question_ids": failed_question_ids,
		}
	return {
		"status": "pass",
		"threshold": threshold,
		"score": professional_qa_score,
		"follow_up_questions": [],
		"question_scores": breakdown,
		"failed_question_ids": failed_question_ids,
	}


def _qa_consistency_layer(
	job: JobProfile,
	resume_text: str,
	answers: list[Mapping[str, object]],
	*,
	enabled: bool = True,
) -> dict[str, Any]:
	"""核对专业回答和简历是否存在最基本的共同业务证据。

	这个检查只做“需要人工复核”的保守判断，不试图判断候选人是否说谎：
	岗位标准、岗位技能或中文二元短语至少有一项同时出现在简历和回答中，
	才认为存在可追溯的一致性证据。没有回答、回答过短或没有共同证据时，
	推进门禁会阻断，但 HR 仍可通过显式人工强制继续记录例外理由。
	"""
	if not enabled:
		return {"status": "not_required", "message": "当前岗位未启用 BOSS 专业问答", "evidence": []}
	if not answers:
		return {"status": "not_started", "message": "尚未记录专业问答", "evidence": []}
	answer_text = " ".join(str(row.get("answer") or "") for row in answers).strip()
	if len(answer_text) < 12:
		return {"status": "review", "message": "专业回答过短，无法核对简历一致性", "evidence": []}
	resume_normalised = _normalise(resume_text)
	answer_normalised = _normalise(answer_text)
	terms = _unique([*job.criteria.must_have, *job.criteria.nice_to_have, *job.skills, job.name])
	matched_terms = [term for term in terms if _normalise(term) in resume_normalised and _normalise(term) in answer_normalised]
	if matched_terms:
		return {
			"status": "pass",
			"message": "简历与专业回答存在岗位证据交集",
			"evidence": [f"共同证据：{term}" for term in matched_terms[:5]],
		}
	# 中文岗位往往使用同义说法（如“电话销售”和“电话开发”），补充有限
	# 同义组检查，仍然要求同一组词同时命中简历和回答，避免泛化放行。
	for key, variants in _SEMANTIC_GROUPS:
		resume_hit = any(_normalise(term) in resume_normalised for term in variants)
		answer_hit = any(_normalise(term) in answer_normalised for term in variants)
		if resume_hit and answer_hit:
			return {
				"status": "pass",
				"message": "简历与专业回答存在同义岗位证据交集",
				"evidence": [f"共同证据组：{key}"],
			}
	return {"status": "review", "message": "简历与专业回答缺少共同岗位证据", "evidence": []}


def screen_candidate(
	job: JobProfile,
	resume_text: str,
	*,
	answers: Iterable[Mapping[str, object]] | None = None,
	professional_qa_score: int | None = None,
	professional_qa_breakdown: Iterable[Mapping[str, object]] | None = None,
	semantic_hits: Mapping[str, str] | None = None,
) -> dict[str, Any]:
	"""生成三层初筛结果和下一步建议，所有结论仍需人工确认。

	``semantic_hits`` 只影响语义层：它是可选 AI 语义层已核对原文的等价表达命中。
	风险等级、硬条件和问答判定仍完全由本地规则决定 —— 模型不能调低风险，也就
	不可能借语义层绕过 :func:`build_review_gate` 的高风险拦截。
	"""
	profile = extract_candidate_profile(resume_text)
	answer_rows = list(answers or [])
	hard_filter = _hard_filter(job, profile)
	semantic_match = _semantic_layer(job, resume_text, profile, semantic_hits)
	# 初筛阈值属于岗位版本，写入报告后既能驱动当前决策，也能让历史报告
	# 保留当时实际使用的门槛，后续配置修改不会反向改变旧结论。
	semantic_match["threshold"] = job.screening_threshold
	risk = _risk_layer(job, profile, resume_text, answer_rows)
	professional_qa = _professional_qa_layer(
		job,
		answer_rows,
		professional_qa_score,
		[dict(item) for item in (professional_qa_breakdown or [])],
		enabled=job.professional_qa_enabled,
	)
	resume_qa_consistency = _qa_consistency_layer(
		job, resume_text, answer_rows, enabled=job.professional_qa_enabled,
	)
	if professional_qa["status"] == "follow_up":
		decision = "待人工确认"
		next_action = "补充专业问答后再次评估"
	elif hard_filter["status"] == "fail" or risk["level"] == "high" or semantic_match["score"] < job.screening_threshold:
		decision = "不推荐"
		next_action = "记录不匹配原因并结束本轮"
	elif hard_filter["status"] == "review" or risk["level"] == "medium":
		decision = "待人工确认"
		next_action = "补齐基础条件并人工复核"
	else:
		decision = "初筛通过"
		next_action = "进入基础条件确认，需 HR 人工确认"
	return {
		"profile": profile.to_dict(),
		"hard_filter": hard_filter,
		"semantic_match": semantic_match,
		"risk": risk,
		"professional_qa": professional_qa,
		"resume_qa_consistency": resume_qa_consistency,
		"decision": decision,
		"next_action": next_action,
		"review_required": True,
	}


def build_review_gate(
	report: Mapping[str, Any],
	*,
	candidate_stage: str,
	candidate_events: Iterable[Mapping[str, Any]],
	candidate_answers: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
	"""把需求文档中的最终推进条件收敛为可解释的人工复核门禁。

	门禁只产生纯数据，不改变候选人状态，也不执行任何平台动作。默认
	``proceed`` 必须通过全部检查：综合分至少 80、硬条件通过、专业问答
	至少 60 且已完成、简历与问答有共同证据、没有高风险，并且审计时间线
	中已经记录过基础意向通过。岗位关闭 BOSS 专业问答且调用方提供回答列表时，
	还要求存在一条私域核验通过记录。调用方可以把失败检查展示给 HR；只有显式
	人工强制继续才允许记录例外，避免在页面上静默绕过规则。
	"""
	raw_screening = report.get("screening")
	screening: Mapping[str, Any] = raw_screening if isinstance(raw_screening, Mapping) else {}
	failed_checks: list[dict[str, Any]] = []
	checks: list[dict[str, Any]] = []

	def add_check(code: str, passed: bool, message: str, value: Any = None) -> None:
		"""记录一项检查，并把失败原因保留为稳定机器码。"""
		item = {"code": code, "passed": passed, "message": message, "value": value}
		checks.append(item)
		if not passed:
			failed_checks.append(item)

	final_score_value = report.get("final_score")
	try:
		final_score = int(final_score_value) if final_score_value is not None else -1
	except (TypeError, ValueError):
		final_score = -1
	add_check("final_score", final_score >= 80, "综合评分必须达到 80 分", final_score)

	raw_hard_filter = screening.get("hard_filter")
	hard_filter: Mapping[str, Any] = raw_hard_filter if isinstance(raw_hard_filter, Mapping) else {}
	hard_status = str(hard_filter.get("status") or "unknown")
	add_check("hard_filter", hard_status == "pass", "城市、薪资、学历和工作年限等硬条件必须通过", hard_status)

	professional_qa_enabled = report.get("professional_qa_enabled", True) is not False
	raw_professional_qa = screening.get("professional_qa")
	professional_qa: Mapping[str, Any] = raw_professional_qa if isinstance(raw_professional_qa, Mapping) else {}
	qa_status = str(professional_qa.get("status") or "not_started")
	qa_score_value = professional_qa.get("score")
	qa_threshold_value = professional_qa.get("threshold")
	try:
		qa_score = int(qa_score_value) if qa_score_value is not None else -1
	except (TypeError, ValueError):
		qa_score = -1
	try:
		qa_threshold = int(qa_threshold_value) if qa_threshold_value is not None else _LEGACY_QA_THRESHOLD
	except (TypeError, ValueError):
		qa_threshold = _LEGACY_QA_THRESHOLD
	add_check(
		"professional_qa",
		(not professional_qa_enabled) or (qa_status == "pass" and qa_score >= qa_threshold),
		"当前岗位未启用 BOSS 专业问答" if not professional_qa_enabled else f"专业问答必须完成且达到 {qa_threshold} 分",
		{"status": "not_required" if not professional_qa_enabled else qa_status, "score": qa_score},
	)
	if not professional_qa_enabled and candidate_answers is not None:
		private_qa_passed = any(
			isinstance(row, Mapping)
			and str(row.get("channel") or "") == "private_domain"
			and str(row.get("verification_status") or "") == "passed"
			for row in candidate_answers
		)
		add_check(
			"private_professional_qa",
			private_qa_passed,
			"岗位关闭 BOSS 专业问答时，必须先记录私域专业核验通过",
			"passed" if private_qa_passed else "not_started",
		)

	consistency = screening.get("resume_qa_consistency")
	consistency_map = consistency if isinstance(consistency, Mapping) else {}
	consistency_status = str(consistency_map.get("status") or "not_started")
	add_check(
		"resume_qa_consistency",
		(not professional_qa_enabled) or consistency_status == "pass",
		"当前岗位未启用 BOSS 专业问答" if not professional_qa_enabled else "简历与专业问答必须存在共同岗位证据",
		"not_required" if not professional_qa_enabled else consistency_status,
	)

	raw_risk = screening.get("risk")
	risk: Mapping[str, Any] = raw_risk if isinstance(raw_risk, Mapping) else {}
	risk_level = str(risk.get("level") or "unknown")
	add_check("risk", risk_level != "high", "不能存在重大风险", risk_level)

	# 基础意向是候选人阶段流转中的事实，不从单次评估报告猜测。只接受
	# 已写入审计时间线的基础通过或其后阶段，防止调用方直接伪造当前阶段。
	allowed_intent_stages = {"basic_passed"}
	intent_events = [
		row for row in candidate_events
		if isinstance(row, Mapping)
		and str(row.get("stage") or "") in allowed_intent_stages
		and str(row.get("action") or "") in {"基础意向人工确认", "基础条件人工确认"}
	]
	add_check(
		"basic_intent",
		bool(intent_events),
		"必须先记录基础意向通过的人工审计事件",
		{"candidate_stage": candidate_stage, "event_count": len(intent_events)},
	)

	return {
		"eligible": not failed_checks,
		"checks": checks,
		"failed_checks": failed_checks,
		"thresholds": {"final_score": 80, "professional_qa": qa_threshold, "risk": "high"},
		"summary": "已满足继续沟通门禁" if not failed_checks else "；".join(item["message"] for item in failed_checks),
	}


__all__ = [
	"CandidateProfile",
	"RISK_PATTERNS",
	"RISK_SIGNAL_CODES",
	"SENSITIVE_TERMS",
	"evaluate_job_readiness",
	"extract_candidate_profile",
	"build_review_gate",
	"normalise_for_match",
	"screen_candidate",
]
