"""招聘工作台的持久化领域对象。

这些对象刻意只保存可供本地工作台复盘的元数据和评估证据。候选人简历正文
仍保留在用户明确选择的本地文件中，工作台只记录文件路径和摘要标识，避免
页面状态、评估报告或日志重复复制个人资料。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


DEFAULT_JOB_WEIGHTS: dict[str, int] = {
	"hard_match": 25,
	"experience": 20,
	"professional_qa": 25,
	"communication": 15,
	"stability": 10,
	"location_salary": 5,
}

# 知识范围是“用途边界”而不是平台权限：内部资料只供 HR/评估使用，候选人
# 试答只能读取 candidate 与 shared。保留 shared 是为了让企业制度等事实可以
# 同时服务内部复盘和候选人核对；旧记录会按类别自动映射到安全默认值。
KNOWLEDGE_AUDIENCE_LABELS: dict[str, str] = {
	"internal": "内部评估",
	"candidate": "候选人可见",
	"shared": "内部与候选人共用",
}


def default_knowledge_audience(category: str) -> str:
	"""按旧版知识类别推导默认范围，避免升级后销售资料意外对外。"""
	return "internal" if category.strip().casefold() == "sales" else "candidate"


def normalise_knowledge_audience(audience: Any, *, category: str) -> str:
	"""校验并归一化知识范围；空值走兼容默认，非法值明确拒绝。"""
	clean = str(audience or "").strip().casefold()
	if not clean:
		return default_knowledge_audience(category)
	if clean not in KNOWLEDGE_AUDIENCE_LABELS:
		raise ValueError("知识范围只能是 internal、candidate 或 shared")
	return clean


def default_job_weights() -> dict[str, int]:
	"""返回岗位评分权重副本，避免不同岗位共享可变字典。"""
	return dict(DEFAULT_JOB_WEIGHTS)


def utc_now_iso() -> str:
	"""返回无时区歧义的 UTC ISO 时间，便于多账号和多机器审计。"""
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(value: Any, *, default: int) -> int:
	"""把旧版 JSON 中的数字安全转换为整数，非法值回退到默认值。"""
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _safe_bool(value: Any, *, default: bool) -> bool:
	"""把岗位配置中的布尔值安全恢复，兼容旧 JSON 的缺失和字符串写法。"""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		if value.strip().casefold() in {"true", "1", "yes", "on"}:
			return True
		if value.strip().casefold() in {"false", "0", "no", "off"}:
			return False
	if isinstance(value, (int, float)) and value in {0, 1}:
		return bool(value)
	return default


def _safe_candidate_profile(value: Any) -> dict[str, Any]:
	"""只保留候选人画像允许的业务字段，阻断敏感字段和超长内容进入状态。

	画像是由本地简历显式标签提取的脱敏快照。这里再次做白名单清洗，是为了
	兼容旧版或手工编辑过的 workspace.json，保证 Web 快照不会因为输入状态
	里出现 ``gender``、``age`` 等字段而把敏感人口属性带入后续筛选。
	"""
	if not isinstance(value, dict):
		return {}
	profile: dict[str, Any] = {}
	for key in (
		"city",
		"expected_salary",
		"education",
		"recent_role",
		"industry",
		"activity",
	):
		raw = value.get(key)
		profile[key] = raw.strip()[:256] if isinstance(raw, str) else ""
	raw_experience = value.get("experience_years")
	profile["experience_years"] = None
	if raw_experience is not None:
		try:
			profile["experience_years"] = float(raw_experience)
		except (TypeError, ValueError):
			pass
	for key in ("skills", "missing_fields"):
		raw_values = value.get(key)
		profile[key] = (
			[str(item).strip()[:128] for item in raw_values if str(item).strip()][:32]
			if isinstance(raw_values, list)
			else []
		)
	return profile


# 阶段顺序同时用于工作台漏斗展示和输入校验；值保持英文机器标识，避免文案
# 变化导致历史数据无法迁移。自动化平台动作不会由阶段变更触发，阶段只记录
# HR 或本地规则已经确认的事实。
CANDIDATE_STAGE_ORDER: tuple[str, ...] = (
	"pending_screening",
	"initial_pass",
	"greeted",
	"basic_confirming",
	"basic_passed",
	"professional_qa",
	"professional_passed",
	"resume_exchanged",
	"resume_passed",
	"private_domain_pending",
	"private_domain_added",
	"interview_pending",
	"interview_scheduled",
	"interview_completed",
	"hired",
	"rejected",
	"paused",
)

CANDIDATE_STAGE_LABELS: dict[str, str] = {
	"pending_screening": "待筛选",
	"initial_pass": "初筛通过",
	"greeted": "已打招呼",
	"basic_confirming": "基础条件确认中",
	"basic_passed": "基础条件通过",
	"professional_qa": "专业问答中",
	"professional_passed": "专业问答通过",
	"resume_exchanged": "已交换简历",
	"resume_passed": "简历评估通过",
	"private_domain_pending": "待加私域",
	"private_domain_added": "已加私域",
	"interview_pending": "待邀约面试",
	"interview_scheduled": "已约面",
	"interview_completed": "面试完成",
	"hired": "录用",
	"rejected": "淘汰",
	"paused": "暂缓",
}

# 岗位生命周期只记录本地人工确认的状态，不会自动发布或下线 BOSS 职位。
# ``published`` 表示筛选标准已经由 HR 确认，可以进入本地评估；``draft`` 和
# ``archived`` 都会阻止新的评估，避免使用未确认或已停用的岗位标准。
JOB_STATUS_LABELS: dict[str, str] = {
	"draft": "草稿",
	"published": "已发布",
	"archived": "已归档",
}

MISMATCH_REASON_LABELS: dict[str, str] = {
	"city_mismatch": "城市不匹配",
	"salary_mismatch": "薪资不匹配",
	"education_mismatch": "学历不匹配",
	"experience_mismatch": "经验不足",
	"skill_mismatch": "核心技能不匹配",
	"direction_mismatch": "职业方向不一致",
	"stability_risk": "稳定性风险",
	"information_incomplete": "信息不足",
	"other": "其他原因",
}

TASK_STATUS_LABELS: dict[str, str] = {
	"pending": "待处理",
	"completed": "已完成",
	"skipped": "已跳过",
}

PRIVATE_DOMAIN_CONTACT_STATUS_LABELS: dict[str, str] = {
	"pending": "待确认",
	"added": "已添加",
	"declined": "未添加",
}
INTERVIEW_INVITE_STATUS_LABELS: dict[str, str] = {
	"scheduled": "已安排",
	"completed": "已完成",
	"cancelled": "已取消",
}
INTERVIEW_RESULT_LABELS: dict[str, str] = {
	"scheduled": "待面试",
	"passed": "面试通过",
	"failed": "面试未通过",
	"cancelled": "面试取消",
}
COMMUNICATION_OUTCOME_LABELS: dict[str, str] = {
	"continue": "继续沟通",
	"qualified": "沟通通过",
	"follow_up": "待跟进",
	"no_response": "未回复",
	"declined": "明确拒绝",
}
HIRING_DECISION_LABELS: dict[str, str] = {
	"hired": "录用",
	"rejected": "淘汰",
	"paused": "暂缓",
}

# 复盘草稿只描述本地建议的审核状态，不等同于岗位或平台配置已经发生变化。
OPTIMIZATION_DRAFT_STATUS_LABELS: dict[str, str] = {
	"pending_review": "待审核",
	"accepted": "已采纳",
	"ignored": "已忽略",
}


def candidate_stage_label(stage: str) -> str:
	"""把内部阶段转换成人可读文案，未知旧数据统一显示为待筛选。"""
	return CANDIDATE_STAGE_LABELS.get(stage, CANDIDATE_STAGE_LABELS["pending_screening"])


@dataclass
class RecruitingCriteria:
	"""一个岗位独立的四类筛选标准。

	敏感人口属性不会由解析器写入这些字段；即使调用方直接构造对象，评分器
	也只把这里的内容当作业务证据，不会访问候选人的性别、婚育、民族或户籍。
	"""

	must_have: list[str] = field(default_factory=list)
	nice_to_have: list[str] = field(default_factory=list)
	reject_if: list[str] = field(default_factory=list)
	risk_signals: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, list[str]]:
		"""返回稳定的 JSON 结构，供本地 Store 和 API 白名单复用。"""
		return {
			"must_have": list(self.must_have),
			"nice_to_have": list(self.nice_to_have),
			"reject_if": list(self.reject_if),
			"risk_signals": list(self.risk_signals),
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "RecruitingCriteria":
		"""从旧版本或手工编辑的 JSON 安全恢复标准列表。"""
		if not isinstance(raw, dict):
			return cls()

		def values(key: str) -> list[str]:
			value = raw.get(key, [])
			if not isinstance(value, list):
				return []
			return [str(item).strip() for item in value if str(item).strip()]

		return cls(
			must_have=values("must_have"),
			nice_to_have=values("nice_to_have"),
			reject_if=values("reject_if"),
			risk_signals=values("risk_signals"),
		)


@dataclass
class JobProfile:
	"""岗位配置及其独立评估边界。

	结构化字段与自然语言 criteria 同时保留：前者用于稳定评分和筛选，后者
	保存 HR 的原始业务表达。字段均为可选，旧版 workspace.json 缺失时使用空值，
	这样升级不会把历史岗位误判为不合格。
	"""

	job_id: str
	name: str
	city: str = ""
	salary_range: str = ""
	education_requirement: str = ""
	min_experience_years: int | None = None
	industry: str = ""
	skills: list[str] = field(default_factory=list)
	criteria: RecruitingCriteria = field(default_factory=RecruitingCriteria)
	weights: dict[str, int] = field(default_factory=default_job_weights)
	# 关闭时不在 BOSS 流程生成专业问答待办；必要的专业核验由 HR 在私域
	# 或其他人工渠道承接，最终仍需经过本地评估和人工门禁。默认开启是为了
	# 让旧版岗位升级后保持原有行为。
	professional_qa_enabled: bool = True
	# 岗位专属 BOSS 打招呼语，由本地平台维护并由 RPA 同步到官方设置页。
	greeting_message: str = ""
	# 默认保持 ``published`` 是为了兼容旧版 Python/CLI 调用；Web 新建岗位
	# 显式传入 ``draft``，由页面上的发布按钮完成人工门禁。
	status: str = "published"
	status_updated_at: str = ""
	created_at: str = ""
	updated_at: str = ""
	# 平台镜像字段与 HR 本地筛选标准刻意分开。同步只允许改动这一组字段，
	# 避免平台职位名称更新时意外覆盖已审核的评分权重、知识库或人工备注。
	source: str = "manual"
	platform_job_id: str = ""
	platform_snapshot: dict[str, str] = field(default_factory=dict)
	last_synced_at: str = ""
	platform_sync_status: str = ""
	# 自动化只能消费人工确认后的岗位规则。旧版已发布岗位默认视为已确认，
	# 以保持升级兼容；新同步的 BOSS 草稿会由同步服务显式标记为未确认。
	rules_confirmed: bool = True
	rules_version: str = "v1"
	rules_confirmed_at: str = ""
	# 每个岗位可独立调整自动化推进门槛；默认值保持现有业务行为。
	screening_threshold: int = 70
	recommendation_threshold: int = 80
	professional_qa_threshold: int = 60

	def __post_init__(self) -> None:
		"""补齐时间戳，保证从表单创建的岗位也具备审计起点。"""
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = now
		if self.status not in JOB_STATUS_LABELS:
			self.status = "draft"
		if not self.status_updated_at:
			self.status_updated_at = self.updated_at
		self.screening_threshold = max(0, min(100, int(self.screening_threshold)))
		self.recommendation_threshold = max(0, min(100, int(self.recommendation_threshold)))
		self.professional_qa_threshold = max(0, min(100, int(self.professional_qa_threshold)))

	def to_dict(self) -> dict[str, Any]:
		"""转换为可持久化的岗位快照。"""
		return {
			"job_id": self.job_id,
			"name": self.name,
			"city": self.city,
			"salary_range": self.salary_range,
			"education_requirement": self.education_requirement,
			"min_experience_years": self.min_experience_years,
			"industry": self.industry,
			"skills": list(self.skills),
			"criteria": self.criteria.to_dict(),
			"weights": dict(self.weights),
			"professional_qa_enabled": self.professional_qa_enabled,
			"greeting_message": self.greeting_message,
			"status": self.status,
			"status_label": JOB_STATUS_LABELS[self.status],
			"status_updated_at": self.status_updated_at,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
			"source": self.source,
			"platform_job_id": self.platform_job_id,
			"platform_snapshot": dict(self.platform_snapshot),
			"last_synced_at": self.last_synced_at,
			"platform_sync_status": self.platform_sync_status,
			"rules_confirmed": self.rules_confirmed,
			"rules_version": self.rules_version,
			"rules_confirmed_at": self.rules_confirmed_at,
			"screening_threshold": self.screening_threshold,
			"recommendation_threshold": self.recommendation_threshold,
			"professional_qa_threshold": self.professional_qa_threshold,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "JobProfile":
		"""从本地 JSON 恢复岗位；缺失字段使用安全默认值。"""
		if not isinstance(raw, dict):
			raise ValueError("岗位记录格式无效")
		weights = raw.get("weights")
		minimum_experience = raw.get("min_experience_years")
		try:
			parsed_minimum_experience = int(minimum_experience) if minimum_experience is not None else None
		except (TypeError, ValueError):
			parsed_minimum_experience = None
		raw_skills = raw.get("skills")
		skills = [str(item).strip() for item in raw_skills if str(item).strip()] if isinstance(raw_skills, list) else []
		return cls(
			job_id=str(raw.get("job_id") or ""),
			name=str(raw.get("name") or ""),
			city=str(raw.get("city") or ""),
			salary_range=str(raw.get("salary_range") or ""),
			education_requirement=str(raw.get("education_requirement") or ""),
			min_experience_years=parsed_minimum_experience,
			industry=str(raw.get("industry") or ""),
			skills=skills,
			criteria=RecruitingCriteria.from_dict(raw.get("criteria")),
			weights={str(k): int(v) for k, v in weights.items()} if isinstance(weights, dict) else default_job_weights(),
			professional_qa_enabled=_safe_bool(raw.get("professional_qa_enabled"), default=True),
			greeting_message=str(raw.get("greeting_message") or ""),
			status=str(raw.get("status") or "published"),
			status_updated_at=str(raw.get("status_updated_at") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
			source=str(raw.get("source") or "manual"),
			platform_job_id=str(raw.get("platform_job_id") or ""),
			platform_snapshot={
				str(key): str(value)
				for key, value in raw.get("platform_snapshot", {}).items()
				if isinstance(key, str) and isinstance(value, (str, int, float)) and not isinstance(value, bool)
			} if isinstance(raw.get("platform_snapshot"), dict) else {},
			last_synced_at=str(raw.get("last_synced_at") or ""),
			platform_sync_status=str(raw.get("platform_sync_status") or ""),
			rules_confirmed=_safe_bool(raw.get("rules_confirmed"), default=str(raw.get("status") or "published") == "published"),
			rules_version=str(raw.get("rules_version") or "v1"),
			rules_confirmed_at=str(raw.get("rules_confirmed_at") or ""),
			screening_threshold=_safe_int(raw.get("screening_threshold"), default=70),
			recommendation_threshold=_safe_int(raw.get("recommendation_threshold"), default=80),
			professional_qa_threshold=_safe_int(raw.get("professional_qa_threshold"), default=60),
		)


@dataclass
class KnowledgeDocument:
	"""岗位企业知识库中的一份本地文本及其可追溯来源。"""

	document_id: str
	job_id: str
	category: str
	title: str
	content: str
	created_at: str = ""
	updated_at: str = ""
	source_type: str = "manual"
	source_path: str = ""
	source_sha256: str = ""
	audience: str = ""

	def __post_init__(self) -> None:
		"""恢复旧记录时补齐范围，保证销售内部内容默认不会对外。"""
		self.audience = normalise_knowledge_audience(self.audience, category=self.category)
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = now

	def to_dict(self) -> dict[str, Any]:
		"""返回存储结构；内容只落盘到用户本地数据目录。"""
		return {
			"document_id": self.document_id,
			"job_id": self.job_id,
			"category": self.category,
			"title": self.title,
			"content": self.content,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
			"source_type": self.source_type,
			"source_path": self.source_path,
			"source_sha256": self.source_sha256,
			"audience": self.audience,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "KnowledgeDocument":
		"""从 JSON 恢复知识文档。"""
		if not isinstance(raw, dict):
			raise ValueError("知识文档记录格式无效")
		return cls(
			document_id=str(raw.get("document_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			category=str(raw.get("category") or "company"),
			title=str(raw.get("title") or "未命名知识"),
			content=str(raw.get("content") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
			source_type=str(raw.get("source_type") or "manual"),
			source_path=str(raw.get("source_path") or ""),
			source_sha256=str(raw.get("source_sha256") or ""),
			audience=str(raw.get("audience") or ""),
		)


@dataclass
class FAQEntry:
	"""岗位 FAQ，允许自然口吻变化但不允许改变事实。"""

	faq_id: str
	job_id: str
	question: str
	answer: str
	allowed_variation: str = ""
	created_at: str = ""
	updated_at: str = ""
	source_document_id: str = ""
	source_title: str = ""
	source_version: str = ""
	review_status: str = "approved"
	audience: str = "candidate"

	def __post_init__(self) -> None:
		"""FAQ 默认属于候选人可见范围，避免新增 FAQ 后无法用于试答。"""
		self.audience = normalise_knowledge_audience(self.audience, category="company")
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = now

	def to_dict(self) -> dict[str, str]:
		"""返回稳定 FAQ 结构。"""
		return {
			"faq_id": self.faq_id,
			"job_id": self.job_id,
			"question": self.question,
			"answer": self.answer,
			"allowed_variation": self.allowed_variation,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
			"source_document_id": self.source_document_id,
			"source_title": self.source_title,
			"source_version": self.source_version,
			"review_status": self.review_status,
			"audience": self.audience,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "FAQEntry":
		"""从 JSON 恢复 FAQ。"""
		if not isinstance(raw, dict):
			raise ValueError("FAQ 记录格式无效")
		return cls(
			faq_id=str(raw.get("faq_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			question=str(raw.get("question") or ""),
			answer=str(raw.get("answer") or ""),
			allowed_variation=str(raw.get("allowed_variation") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
			source_document_id=str(raw.get("source_document_id") or ""),
			source_title=str(raw.get("source_title") or ""),
			source_version=str(raw.get("source_version") or ""),
			review_status=str(raw.get("review_status") or "approved"),
			audience=str(raw.get("audience") or "candidate"),
		)


@dataclass
class CandidateRecord:
	"""候选人本地简历引用和脱敏业务画像。

	正文仍只保存在用户选择的本地文件中；``profile`` 只保存城市、学历、
	经验和技能等硬条件证据，方便工作台在不读取正文的情况下展示下一步。
	"""

	candidate_id: str
	name: str
	resume_path: str
	source: str = "local_markdown"
	resume_sha256: str = ""
	imported_at: str = ""
	stage: str = "pending_screening"
	stage_updated_at: str = ""
	last_action: str = ""
	profile: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		if not self.imported_at:
			self.imported_at = utc_now_iso()
		if self.stage not in CANDIDATE_STAGE_LABELS:
			self.stage = "pending_screening"
		if not self.stage_updated_at:
			self.stage_updated_at = self.imported_at

	def to_dict(self) -> dict[str, Any]:
		"""返回不含简历正文的候选人元数据和画像白名单。"""
		return {
			"candidate_id": self.candidate_id,
			"name": self.name,
			"resume_path": self.resume_path,
			"source": self.source,
			"resume_sha256": self.resume_sha256,
			"imported_at": self.imported_at,
			"stage": self.stage,
			"stage_label": candidate_stage_label(self.stage),
			"stage_updated_at": self.stage_updated_at,
			"last_action": self.last_action,
			"profile": _safe_candidate_profile(self.profile),
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CandidateRecord":
		"""从 JSON 恢复候选人元数据。"""
		if not isinstance(raw, dict):
			raise ValueError("候选人记录格式无效")
		return cls(
			candidate_id=str(raw.get("candidate_id") or ""),
			name=str(raw.get("name") or "未命名候选人"),
			resume_path=str(raw.get("resume_path") or ""),
			source=str(raw.get("source") or "local_markdown"),
			resume_sha256=str(raw.get("resume_sha256") or ""),
			imported_at=str(raw.get("imported_at") or ""),
			stage=str(raw.get("stage") or "pending_screening"),
			stage_updated_at=str(raw.get("stage_updated_at") or ""),
			last_action=str(raw.get("last_action") or ""),
			profile=_safe_candidate_profile(raw.get("profile")),
		)


@dataclass
class CandidateEvent:
	"""候选人阶段审计事件；原话只保存在本地事件文件，不进入默认页面快照。

	``job_id`` 用于同一候选人被多个岗位复用时隔离评估门禁和阶段时间线。
	旧版事件没有该字段时以空字符串恢复；Store 只在候选人唯一绑定岗位时
	兼容读取空标识，避免把历史事实跨岗位误当成当前岗位事实。
	"""

	event_id: str
	candidate_id: str
	stage: str
	action: str
	actor: str = "hr"
	note: str = ""
	ai_judgment: str = ""
	candidate_quote: str = ""
	created_at: str = ""
	job_id: str = ""

	def __post_init__(self) -> None:
		if not self.created_at:
			self.created_at = utc_now_iso()
		if self.stage not in CANDIDATE_STAGE_LABELS:
			self.stage = "pending_screening"

	def to_dict(self) -> dict[str, str]:
		"""返回完整本地审计结构；调用方负责按隐私边界投影字段。"""
		return {
			"event_id": self.event_id,
			"candidate_id": self.candidate_id,
			"job_id": self.job_id,
			"stage": self.stage,
			"stage_label": candidate_stage_label(self.stage),
			"action": self.action,
			"actor": self.actor,
			"note": self.note,
			"ai_judgment": self.ai_judgment,
			"candidate_quote": self.candidate_quote,
			"created_at": self.created_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CandidateEvent":
		"""从本地 JSON 恢复事件，并对旧记录缺失字段提供默认值。"""
		if not isinstance(raw, dict):
			raise ValueError("候选人事件记录格式无效")
		return cls(
			event_id=str(raw.get("event_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			stage=str(raw.get("stage") or "pending_screening"),
			action=str(raw.get("action") or "未命名动作"),
			actor=str(raw.get("actor") or "hr"),
			note=str(raw.get("note") or ""),
			ai_judgment=str(raw.get("ai_judgment") or ""),
			candidate_quote=str(raw.get("candidate_quote") or ""),
			created_at=str(raw.get("created_at") or ""),
		)


@dataclass
class MismatchFeedback:
	"""候选人与岗位不匹配的本地反馈记录。

	该记录用于解释筛选结果和汇总岗位效果，不会自动提交到 BOSS，也不会保存
	候选人简历正文。``submitted_to_platform`` 明确标识目前仍由 HR 决定是否
	在官方页面手工反馈，避免把本地判断误当成平台动作。
	"""

	feedback_id: str
	job_id: str
	candidate_id: str
	reason_code: str
	stage: str
	source: str = "local"
	note: str = ""
	submitted_to_platform: bool = False
	created_at: str = ""

	def __post_init__(self) -> None:
		"""将未知原因降级为其他，并补齐审计时间。"""
		if not self.created_at:
			self.created_at = utc_now_iso()
		if self.reason_code not in MISMATCH_REASON_LABELS:
			self.reason_code = "other"
		self.submitted_to_platform = bool(self.submitted_to_platform)

	def to_dict(self) -> dict[str, str | bool]:
		"""返回可供工作台展示的反馈元数据。"""
		return {
			"feedback_id": self.feedback_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"reason_code": self.reason_code,
			"reason_label": MISMATCH_REASON_LABELS[self.reason_code],
			"stage": self.stage,
			"source": self.source,
			"note": self.note,
			"submitted_to_platform": self.submitted_to_platform,
			"created_at": self.created_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "MismatchFeedback":
		"""从旧版 JSON 恢复反馈记录，缺失字段使用安全默认值。"""
		if not isinstance(raw, dict):
			raise ValueError("不匹配反馈记录格式无效")
		return cls(
			feedback_id=str(raw.get("feedback_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			reason_code=str(raw.get("reason_code") or "other"),
			stage=str(raw.get("stage") or "unknown"),
			source=str(raw.get("source") or "local"),
			note=str(raw.get("note") or ""),
			submitted_to_platform=bool(raw.get("submitted_to_platform", False)),
			created_at=str(raw.get("created_at") or ""),
		)


@dataclass
class OptimizationDraft:
	"""一条可审计的招聘复盘改进草稿。

	草稿保存的是由本地事实推导出的建议元数据，不保存候选人简历正文或联系方式。
	``accepted`` 只表示 HR 确认了后续改进方向，系统不会据此自动修改岗位、知识库、
	FAQ，也不会向 BOSS 发起任何动作；实际落地仍通过已有人工表单完成。
	"""

	draft_id: str
	job_id: str
	suggestion_id: str
	kind: str
	severity: str
	title: str
	reason: str
	action: str
	status: str = "pending_review"
	created_at: str = ""
	updated_at: str = ""
	reviewed_at: str = ""
	review_note: str = ""

	def __post_init__(self) -> None:
		"""校正旧状态值并补齐创建、更新时间，保证展示和排序稳定。"""
		if self.status not in OPTIMIZATION_DRAFT_STATUS_LABELS:
			self.status = "pending_review"
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = self.created_at

	def to_dict(self) -> dict[str, str]:
		"""返回可安全放入 Web 快照的草稿元数据。"""
		return {
			"draft_id": self.draft_id,
			"job_id": self.job_id,
			"suggestion_id": self.suggestion_id,
			"kind": self.kind,
			"severity": self.severity,
			"title": self.title,
			"reason": self.reason,
			"action": self.action,
			"status": self.status,
			"status_label": OPTIMIZATION_DRAFT_STATUS_LABELS[self.status],
			"created_at": self.created_at,
			"updated_at": self.updated_at,
			"reviewed_at": self.reviewed_at,
			"review_note": self.review_note,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "OptimizationDraft":
		"""从旧版或手工编辑过的 JSON 安全恢复改进草稿。"""
		if not isinstance(raw, dict):
			raise ValueError("改进草稿记录格式无效")
		return cls(
			draft_id=str(raw.get("draft_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			suggestion_id=str(raw.get("suggestion_id") or ""),
			kind=str(raw.get("kind") or "unknown"),
			severity=str(raw.get("severity") or "medium"),
			title=str(raw.get("title") or "未命名改进建议"),
			reason=str(raw.get("reason") or ""),
			action=str(raw.get("action") or "人工复核"),
			status=str(raw.get("status") or "pending_review"),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
			reviewed_at=str(raw.get("reviewed_at") or ""),
			review_note=str(raw.get("review_note") or ""),
		)


@dataclass
class CandidateAnswer:
	"""岗位专业问题的一条本地回答记录，正文不进入默认 Web 快照。

	问题 ID、版本和来源 ID 是评估证据的连接点；回答版本用于同一问题被
	重新追问或修订时保留顺序，而不是覆盖历史回答。``channel`` 区分
	BOSS 页面与私域人工核验，``verification_status`` 记录人工核验结论，
	这样关闭 BOSS 专业问答后仍能保留完整的本地证据链。
	"""

	answer_id: str
	job_id: str
	candidate_id: str
	question: str
	answer: str
	created_at: str = ""
	question_id: str = ""
	question_version: str = "v1"
	source_ids: list[str] = field(default_factory=list)
	answer_version: int = 1
	follow_up_of: str = ""
	channel: str = "boss"
	verification_status: str = "recorded"

	def __post_init__(self) -> None:
		if not self.created_at:
			self.created_at = utc_now_iso()

	def to_dict(self) -> dict[str, Any]:
		"""返回完整本地回答记录，供评分器使用。"""
		return {
			"answer_id": self.answer_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"question": self.question,
			"answer": self.answer,
			"answer_length": len(self.answer),
			"created_at": self.created_at,
			"question_id": self.question_id,
			"question_version": self.question_version,
			"source_ids": list(self.source_ids),
			"answer_version": self.answer_version,
			"follow_up_of": self.follow_up_of,
			"channel": self.channel,
			"verification_status": self.verification_status,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CandidateAnswer":
		"""从本地 JSON 恢复回答记录。"""
		if not isinstance(raw, dict):
			raise ValueError("候选人回答记录格式无效")
		return cls(
			answer_id=str(raw.get("answer_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			question=str(raw.get("question") or ""),
			answer=str(raw.get("answer") or ""),
			created_at=str(raw.get("created_at") or ""),
			question_id=str(raw.get("question_id") or ""),
			question_version=str(raw.get("question_version") or "v1"),
			source_ids=[str(item) for item in raw.get("source_ids", []) if str(item).strip()]
			if isinstance(raw.get("source_ids"), list)
			else [],
			answer_version=_safe_int(raw.get("answer_version"), default=1),
			follow_up_of=str(raw.get("follow_up_of") or ""),
			channel=str(raw.get("channel") or "boss"),
			verification_status=str(raw.get("verification_status") or "recorded"),
		)


@dataclass
class CommunicationTemplate:
	"""可复用的沟通话术模板定义。

	与 MessageTemplateUsage 不同，CommunicationTemplate 本身是模板定义，
	供 HR 在管理后台创建、编辑和管理；使用时通过 record_message_template_usage
	关联到具体候选人，产生效果追踪数据。
	"""

	template_id: str
	job_id: str = ""
	template_key: str = ""
	title: str = ""
	body: str = ""
	category: str = "greeting"
	version: str = "v1"
	created_at: str = ""
	updated_at: str = ""

	def __post_init__(self) -> None:
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = now

	def to_dict(self) -> dict[str, str]:
		return {
			"template_id": self.template_id,
			"job_id": self.job_id,
			"template_key": self.template_key,
			"title": self.title,
			"body": self.body,
			"category": self.category,
			"version": self.version,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CommunicationTemplate":
		if not isinstance(raw, dict):
			raise ValueError("话术模板格式无效")
		return cls(
			template_id=str(raw.get("template_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			template_key=str(raw.get("template_key") or ""),
			title=str(raw.get("title") or ""),
			body=str(raw.get("body") or ""),
			category=str(raw.get("category") or "greeting"),
			version=str(raw.get("version") or "v1"),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
		)


@dataclass
class MessageTemplateUsage:
	"""人工使用招聘话术的本地审计记录。

	记录的含义是 HR 已经复制并在官方页面自行使用了某一版本话术；
	platform_action 固定为 manual_only，因此不会被误读为系统已替用户发送消息。
	单独建模还能让后续复盘按模板统计，而不把话术正文复制进沟通记录或日志。
	"""

	usage_id: str
	job_id: str
	candidate_id: str = ""
	template_key: str = ""
	template_version: str = "v1"
	note: str = ""
	used_at: str = ""
	platform_action: str = "manual_only"

	def __post_init__(self) -> None:
		"""补齐使用时间，并把旧数据或未知动作收敛到安全值。"""
		if not self.used_at:
			self.used_at = utc_now_iso()
		if self.platform_action != "manual_only":
			self.platform_action = "manual_only"

	def to_dict(self) -> dict[str, str]:
		"""返回可供页面和复盘使用的元数据，不包含话术发送结果。"""
		return {
			"usage_id": self.usage_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"template_key": self.template_key,
			"template_version": self.template_version,
			"note": self.note,
			"used_at": self.used_at,
			"platform_action": self.platform_action,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "MessageTemplateUsage":
		"""从本地 JSON 恢复话术使用记录，兼容缺少字段的旧状态。"""
		if not isinstance(raw, dict):
			raise ValueError("话术使用记录格式无效")
		return cls(
			usage_id=str(raw.get("usage_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			template_key=str(raw.get("template_key") or ""),
			template_version=str(raw.get("template_version") or "v1"),
			note=str(raw.get("note") or ""),
			used_at=str(raw.get("used_at") or ""),
			platform_action=str(raw.get("platform_action") or "manual_only"),
		)


@dataclass
class CommunicationRecord:
	"""候选人一轮人工沟通的本地事实记录。

	沟通正文不会被系统自动发送或同步；这里只保存 HR 手工归纳的结果、候选人
	回复摘要和下一次跟进时间，方便四轮沟通形成可追踪的时间线。摘要不是平台
	原始消息副本，长度也被限制在适合复盘的范围内。
	"""

	communication_id: str
	job_id: str
	candidate_id: str
	round_number: int
	outcome: str
	candidate_reply_summary: str = ""
	note: str = ""
	next_follow_up_at: str = ""
	template_key: str = ""
	template_version: str = ""
	created_at: str = ""
	updated_at: str = ""

	def __post_init__(self) -> None:
		"""补齐审计时间，并将旧数据中的未知结果降级为待跟进。"""
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = self.created_at
		if self.outcome not in COMMUNICATION_OUTCOME_LABELS:
			self.outcome = "follow_up"
		if self.round_number < 1:
			self.round_number = 1

	def to_dict(self) -> dict[str, Any]:
		"""返回可供工作台展示的沟通元数据，不包含平台原始消息。"""
		return {
			"communication_id": self.communication_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"round_number": self.round_number,
			"round_label": f"第 {self.round_number} 轮沟通",
			"outcome": self.outcome,
			"outcome_label": COMMUNICATION_OUTCOME_LABELS[self.outcome],
			"candidate_reply_summary": self.candidate_reply_summary,
			"note": self.note,
			"next_follow_up_at": self.next_follow_up_at,
			"template_key": self.template_key,
			"template_version": self.template_version,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CommunicationRecord":
		"""从本地 JSON 恢复沟通记录，并兼容缺失的新字段。"""
		if not isinstance(raw, dict):
			raise ValueError("沟通记录格式无效")
		try:
			round_number = int(raw.get("round_number") or 1)
		except (TypeError, ValueError):
			round_number = 1
		return cls(
			communication_id=str(raw.get("communication_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			round_number=round_number,
			outcome=str(raw.get("outcome") or "follow_up"),
			candidate_reply_summary=str(raw.get("candidate_reply_summary") or ""),
			note=str(raw.get("note") or ""),
			next_follow_up_at=str(raw.get("next_follow_up_at") or ""),
			template_key=str(raw.get("template_key") or ""),
			template_version=str(raw.get("template_version") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
		)


@dataclass
class CandidateTask:
	"""候选人本地待办。

		待办把“评估结论”转换成一个需要 HR 明确确认的下一步。``target_stage``
		只表示用户完成人工动作后本地记录应进入的阶段，绝不触发 BOSS 消息、加
		私域或面试邀约，因此它既能形成闭环，又不会越过平台合规边界。
	"""

	task_id: str
	candidate_id: str
	kind: str
	title: str
	description: str = ""
	job_id: str = ""
	target_stage: str = ""
	allowed_target_stages: list[str] = field(default_factory=list)
	communication_round: int = 0
	due_at: str = ""
	status: str = "pending"
	note: str = ""
	created_at: str = ""
	updated_at: str = ""
	completed_at: str = ""

	def __post_init__(self) -> None:
		"""补齐时间戳并将未知状态降级为待处理，兼容旧版 JSON。"""
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = self.created_at
		if self.status not in TASK_STATUS_LABELS:
			self.status = "pending"
		if self.target_stage not in CANDIDATE_STAGE_LABELS and self.target_stage:
			self.target_stage = ""
		self.allowed_target_stages = [
			stage for stage in self.allowed_target_stages if stage in CANDIDATE_STAGE_LABELS
		]
		if self.communication_round < 0:
			self.communication_round = 0

	def to_dict(self) -> dict[str, Any]:
		"""返回不含候选人简历正文的待办元数据。"""
		return {
			"task_id": self.task_id,
			"candidate_id": self.candidate_id,
			"job_id": self.job_id,
			"kind": self.kind,
			"title": self.title,
			"description": self.description,
			"target_stage": self.target_stage,
			"target_stage_label": candidate_stage_label(self.target_stage) if self.target_stage else "",
			"allowed_target_stages": list(self.allowed_target_stages),
			"allowed_target_stage_labels": [candidate_stage_label(stage) for stage in self.allowed_target_stages],
			"communication_round": self.communication_round,
			"due_at": self.due_at,
			"status": self.status,
			"status_label": TASK_STATUS_LABELS[self.status],
			"note": self.note,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
			"completed_at": self.completed_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CandidateTask":
		"""从本地 JSON 恢复待办，并兼容缺少描述或状态的旧记录。"""
		if not isinstance(raw, dict):
			raise ValueError("候选人待办记录格式无效")
		return cls(
			task_id=str(raw.get("task_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			kind=str(raw.get("kind") or "manual"),
			title=str(raw.get("title") or "未命名待办"),
			description=str(raw.get("description") or ""),
			target_stage=str(raw.get("target_stage") or ""),
			allowed_target_stages=[
				str(stage) for stage in raw.get("allowed_target_stages", [])
				if isinstance(stage, str)
			],
			communication_round=_safe_int(raw.get("communication_round"), default=0),
			due_at=str(raw.get("due_at") or ""),
			status=str(raw.get("status") or "pending"),
			note=str(raw.get("note") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
			completed_at=str(raw.get("completed_at") or ""),
		)


@dataclass
class PrivateDomainContact:
	"""候选人私域联系结果的本地审计记录。

	该对象只表示 HR 已经在官方页面或线下完成的事实，不保存账号密码、
		联系方式明文或任何会自动触发外部添加动作的指令。
	"""

	contact_id: str
	candidate_id: str
	channel: str
	status: str
	job_id: str = ""
	note: str = ""
	created_at: str = ""
	updated_at: str = ""

	def __post_init__(self) -> None:
		"""补齐时间并把未知状态降级为待确认，兼容旧版本地文件。"""
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = self.created_at
		if self.status not in PRIVATE_DOMAIN_CONTACT_STATUS_LABELS:
			self.status = "pending"

	def to_dict(self) -> dict[str, str]:
		"""返回不含敏感联系方式的私域记录元数据。"""
		return {
			"contact_id": self.contact_id,
			"candidate_id": self.candidate_id,
			"job_id": self.job_id,
			"channel": self.channel,
			"status": self.status,
			"status_label": PRIVATE_DOMAIN_CONTACT_STATUS_LABELS[self.status],
			"note": self.note,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "PrivateDomainContact":
		"""从 JSON 安全恢复私域记录。"""
		if not isinstance(raw, dict):
			raise ValueError("私域联系记录格式无效")
		return cls(
			contact_id=str(raw.get("contact_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			channel=str(raw.get("channel") or "other"),
			status=str(raw.get("status") or "pending"),
			job_id=str(raw.get("job_id") or ""),
			note=str(raw.get("note") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
		)


@dataclass
class InterviewInvite:
	"""候选人面试邀约和结构化结果的本地记录。

	``status`` 表示邀约记录是否已经结束，``outcome`` 表示面试本身的结论。
	两者分开保存，才能阻止“面试已结束但未通过”被误读成可录用，同时兼容
	旧版只有 status 的本地 JSON。
	"""

	invite_id: str
	job_id: str
	candidate_id: str
	scheduled_at: str
	interviewer: str = ""
	status: str = "scheduled"
	outcome: str = "scheduled"
	note: str = ""
	created_at: str = ""
	updated_at: str = ""

	def __post_init__(self) -> None:
		"""补齐时间并将未知状态降级为已安排，防止旧数据阻断页面。"""
		now = utc_now_iso()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = self.created_at
		if self.status not in INTERVIEW_INVITE_STATUS_LABELS:
			self.status = "scheduled"
		if self.outcome not in INTERVIEW_RESULT_LABELS:
			self.outcome = "scheduled" if self.status == "scheduled" else "cancelled"

	def to_dict(self) -> dict[str, str]:
		"""返回面试时间、面试官和结果等非简历元数据。"""
		return {
			"invite_id": self.invite_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"scheduled_at": self.scheduled_at,
			"interviewer": self.interviewer,
			"status": self.status,
			"status_label": INTERVIEW_INVITE_STATUS_LABELS[self.status],
			"outcome": self.outcome,
			"outcome_label": INTERVIEW_RESULT_LABELS[self.outcome],
			"note": self.note,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "InterviewInvite":
		"""从 JSON 恢复面试记录。"""
		if not isinstance(raw, dict):
			raise ValueError("面试记录格式无效")
		return cls(
			invite_id=str(raw.get("invite_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			scheduled_at=str(raw.get("scheduled_at") or ""),
			interviewer=str(raw.get("interviewer") or ""),
			status=str(raw.get("status") or "scheduled"),
			outcome=str(raw.get("outcome") or ("scheduled" if raw.get("status") == "scheduled" else "cancelled")),
			note=str(raw.get("note") or ""),
			created_at=str(raw.get("created_at") or ""),
			updated_at=str(raw.get("updated_at") or ""),
		)


@dataclass
class CandidateDecision:
	"""候选人终局决定，保存录用、淘汰或暂缓的原因。"""

	decision_id: str
	job_id: str
	candidate_id: str
	outcome: str
	reason: str = ""
	created_at: str = ""

	def __post_init__(self) -> None:
		"""未知终局默认暂缓，避免历史数据误显示为录用。"""
		if not self.created_at:
			self.created_at = utc_now_iso()
		if self.outcome not in HIRING_DECISION_LABELS:
			self.outcome = "paused"

	def to_dict(self) -> dict[str, str]:
		"""返回终局决定元数据。"""
		return {
			"decision_id": self.decision_id,
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"outcome": self.outcome,
			"outcome_label": HIRING_DECISION_LABELS[self.outcome],
			"reason": self.reason,
			"created_at": self.created_at,
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "CandidateDecision":
		"""从 JSON 恢复终局决定。"""
		if not isinstance(raw, dict):
			raise ValueError("候选人决定记录格式无效")
		return cls(
			decision_id=str(raw.get("decision_id") or ""),
			job_id=str(raw.get("job_id") or ""),
			candidate_id=str(raw.get("candidate_id") or ""),
			outcome=str(raw.get("outcome") or "paused"),
			reason=str(raw.get("reason") or ""),
			created_at=str(raw.get("created_at") or ""),
		)


@dataclass
class AssessmentReport:
	"""一份带证据的岗位评估报告，关键决策永远要求人工确认。"""

	job_id: str
	candidate_id: str
	candidate_name: str
	final_score: int
	level: str
	decision: str
	matched_points: list[str] = field(default_factory=list)
	risk_points: list[str] = field(default_factory=list)
	evidence: list[str] = field(default_factory=list)
	next_action: str = "人工复核"
	review_required: bool = True
	review_status: str = "pending"
	reviewed_at: str = ""
	review_note: str = ""
	engine: str = "rules"
	professional_questions: list[str] = field(default_factory=list)
	professional_question_items: list[dict[str, Any]] = field(default_factory=list)
	# 把岗位是否启用专业问答写进报告，确保旧报告和新岗位门禁不会依赖页面
	# 当前选择而产生歧义；缺失字段的历史报告按启用处理。
	professional_qa_enabled: bool = True
	answer_count: int = 0
	professional_qa_score: int | None = None
	professional_qa_evidence: list[str] = field(default_factory=list)
	# 每道问题只保存问题标识、版本、分数和脱敏证据，不保存候选人回答正文。
	professional_qa_breakdown: list[dict[str, Any]] = field(default_factory=list)
	# 三层初筛结果是对总分的解释性补充；旧版报告缺少此字段时使用空字典，
	# 这样升级本地 workspace.json 不会破坏历史评估或前端读取。
	screening: dict[str, Any] = field(default_factory=dict)
	score_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
	message_templates: dict[str, str] = field(default_factory=dict)
	# 可选 AI 语义层的核对结果：命中、被拒断言、追问和风险提示。空字典表示这一份
	# 报告完全由本地规则得出。字段只做证据留痕，``advisory_only`` 恒为真。
	ai_review: dict[str, Any] = field(default_factory=dict)
	evaluated_at: str = ""

	def __post_init__(self) -> None:
		if not self.evaluated_at:
			self.evaluated_at = utc_now_iso()

	def to_dict(self) -> dict[str, Any]:
		"""转换为可安全返回给本地页面的报告元数据和证据。"""
		return {
			"job_id": self.job_id,
			"candidate_id": self.candidate_id,
			"candidate_name": self.candidate_name,
			"final_score": self.final_score,
			"level": self.level,
			"decision": self.decision,
			"matched_points": list(self.matched_points),
			"risk_points": list(self.risk_points),
			"evidence": list(self.evidence),
			"next_action": self.next_action,
			"review_required": self.review_required,
			"review_status": self.review_status,
			"reviewed_at": self.reviewed_at,
			"review_note": self.review_note,
			"engine": self.engine,
			"professional_questions": list(self.professional_questions),
			"professional_question_items": [dict(item) for item in self.professional_question_items],
			"professional_qa_enabled": self.professional_qa_enabled,
			"answer_count": self.answer_count,
			"professional_qa_score": self.professional_qa_score,
			"professional_qa_evidence": list(self.professional_qa_evidence),
			"professional_qa_breakdown": [dict(item) for item in self.professional_qa_breakdown],
			"screening": {
				key: dict(value) if isinstance(value, dict) else value
				for key, value in self.screening.items()
			},
			"score_breakdown": {key: dict(value) for key, value in self.score_breakdown.items()},
			"message_templates": dict(self.message_templates),
			"ai_review": dict(self.ai_review),
			"evaluated_at": self.evaluated_at,
		}
