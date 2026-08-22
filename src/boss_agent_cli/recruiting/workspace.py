"""招聘工作台的应用编排服务。

本模块把岗位配置、知识库、候选人本地简历和确定性评估串成一个窄接口，
供 CLI 或本地 Web 控制台复用。它不访问 BOSS、不发送消息，也不把简历正文
写入状态快照；所有需要人工作出的招聘决定都以 ``review_required`` 标记。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from boss_agent_cli.recruiting.ai_review import AIResumeReview
from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService
from boss_agent_cli.recruiting.assessment import evaluate_job_readiness, parse_natural_language_job, score_candidate
from boss_agent_cli.recruiting.knowledge import generate_faq_drafts
from boss_agent_cli.recruiting.insights import build_daily_snapshot_metrics, build_optimization_projection
from boss_agent_cli.recruiting.models import CANDIDATE_STAGE_LABELS, CANDIDATE_STAGE_ORDER, JobProfile, default_job_weights, utc_now_iso
from boss_agent_cli.recruiting.rejection_analytics import build_rejection_reason_statistics
from boss_agent_cli.recruiting.screening import build_review_gate
from boss_agent_cli.recruiting.store import RecruitingStore
from boss_agent_cli.recruiting.context import DEFAULT_RECRUITING_CONTEXT, RecruitingContext
from boss_agent_cli.recruiting.workflow import build_score_groups, build_workflow_projection

_MAX_JOB_NAME_CHARS = 120
_MAX_CITY_CHARS = 80
_MAX_SALARY_CHARS = 80
_MAX_CRITERIA_CHARS = 4_000
_MAX_EDUCATION_CHARS = 80
_MAX_INDUSTRY_CHARS = 120
_MAX_SKILL_CHARS = 80
_MAX_KNOWLEDGE_TITLE_CHARS = 160
_MAX_KNOWLEDGE_CONTENT_CHARS = 20_000
_MAX_FAQ_QUESTION_CHARS = 500
_MAX_FAQ_ANSWER_CHARS = 4_000
_MAX_FAQ_VARIATION_CHARS = 1_000
_MAX_SEARCH_QUERY_CHARS = 200
_MAX_RESUME_PATH_CHARS = 4_096
_MAX_REVIEW_NOTE_CHARS = 1_000
_MAX_OVERRIDE_REASON_CHARS = 1_000
_MAX_INTERVIEW_TEXT_CHARS = 160
_MAX_COMMUNICATION_SUMMARY_CHARS = 2_000
_MAX_COMMUNICATION_OUTCOME_CHARS = 32
_MAX_MISMATCH_REASON_CHARS = 64
_MAX_MISMATCH_STAGE_CHARS = 64
_ALLOWED_CANDIDATE_SOURCES = {"local_markdown", "local_auto_import", "boss_conversation", "boss_recommendation"}
_REVIEW_OUTCOMES: dict[str, tuple[str, str, str]] = {
	"proceed": ("已确认继续沟通", "人工确认后继续沟通", "professional_passed"),
	"follow_up": ("需要补充信息", "补充信息后再次人工复核", "professional_qa"),
	"reject": ("已确认暂不推进", "人工确认后结束本轮流程", "rejected"),
}
_SOURCE_LABELS = {
	"boss_conversation": "BOSS 沟通",
	"boss_recommendation": "BOSS 推荐",
	"local_markdown": "本地导入",
	"local_auto_import": "本地自动分析",
}


def _limited_text(value: str, *, field_name: str, limit: int, required: bool = False) -> str:
	"""清理并限制表单文本，统一拒绝空值和异常长输入。"""
	text = value.strip()
	if required and not text:
		raise ValueError(f"{field_name}不能为空")
	if len(text) > limit:
		raise ValueError(f"{field_name}过长，最多支持 {limit} 个字符")
	return text


def _optional_experience_years(value: int | None) -> int | None:
	"""校验岗位最低年限，避免 Web 输入把布尔值或异常大数字写入岗位标准。"""
	if value is None:
		return None
	if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
		raise ValueError("最低工作年限必须是 0 到 100 之间的整数")
	return value


def _optional_professional_qa_enabled(value: bool | None) -> bool | None:
	"""校验岗位专业问答开关；更新岗位未传值时保留原有配置。"""
	if value is None:
		return None
	if not isinstance(value, bool):
		raise ValueError("专业问答开关必须是布尔值")
	return value


def _age_hours(timestamp: str) -> float:
	"""计算阶段已停留小时数；旧数据时间损坏时返回 0 而不是阻断快照。"""
	try:
		parsed = datetime.fromisoformat(timestamp)
		if parsed.tzinfo is None:
			parsed = parsed.replace(tzinfo=timezone.utc)
		return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600)
	except (TypeError, ValueError):
		return 0.0


def _candidate_stage_after_review(current_stage: str, outcome: str) -> str:
	"""根据评估所在阶段决定人工确认后的目标阶段。

		评估报告既用于专业问答后的初步确认，也用于“已交换简历”后的二次
		简历评估。两种报告的决策文案相同，但后续动作不同；把阶段判断集中
		在这里，避免工作台入口通过一次 ``proceed`` 静默跳过简历交换。
		历史数据如果已经进入沟通或更后阶段，重新生成评估后继续时保留当前
		阶段，防止刷新报告把候选人倒退到早期流程。
	"""
	if outcome != "proceed":
		return _REVIEW_OUTCOMES[outcome][2]
	if current_stage == "resume_exchanged":
		return "resume_passed"
	if current_stage in {
		"resume_passed",
		"private_domain_pending",
		"private_domain_added",
		"interview_pending",
		"interview_scheduled",
		"interview_completed",
	}:
		return current_stage
	return "professional_passed"


class RecruitingWorkspace:
	"""招聘工作台的单机应用服务，隐藏 Store 和评估器的组合细节。"""

	def __init__(
		self,
		data_dir: Path,
		*,
		store: RecruitingStore | None = None,
		context: RecruitingContext | None = None,
	) -> None:
		"""创建工作台；显式注入 Store 便于测试和未来迁移存储实现。"""
		self._store = store or RecruitingStore(data_dir, context=context or DEFAULT_RECRUITING_CONTEXT)

	@property
	def store(self) -> RecruitingStore:
		"""返回底层 Store，供运行时复用同一工作区实例。"""
		return self._store

	@property
	def context(self) -> RecruitingContext:
		"""返回当前工作台上下文，供 Web 层显示并用于切换后的审计。"""
		return self._store.context

	@staticmethod
	def _job_snapshot(job: JobProfile) -> dict[str, Any]:
		"""把岗位稳定字段和发布前完整性提示合并成一个前端快照。"""
		payload = job.to_dict()
		payload["readiness"] = evaluate_job_readiness(job)
		return payload

	@staticmethod
	def _job_display_order(job: JobProfile) -> tuple[int, str, str]:
		"""定义岗位看板的稳定显示优先级，避免历史手工岗位抢占默认视图。

		评分看板首次加载应优先展示当前仍在招聘的 BOSS 岗位，其次才是 BOSS
		明确关闭但需要保留历史评估的岗位；手工历史岗位仍完整保留，却不会让
		用户误以为它是本次平台同步结果。名称和本地 ID 作为同优先级的稳定排序
		键，避免 JSON 写入顺序影响默认岗位。
		"""
		if job.source == "boss":
			priority = 1 if job.platform_sync_status == "closed" else 0
		else:
			priority = 2
		return priority, job.name.casefold(), job.job_id

	def create_job(
		self,
		*,
		name: str,
		city: str = "",
		salary_range: str = "",
		education_requirement: str = "",
	min_experience_years: int | None = None,
		industry: str = "",
		skills: list[str] | None = None,
		criteria_text: str = "",
		professional_qa_enabled: bool = True,
		greeting_message: str = "",
		status: str = "published",
	) -> dict[str, Any]:
		"""创建岗位并解析自然语言标准，返回岗位快照和合规提醒。

		Python/CLI 调用默认保持历史的 ``published`` 语义；Web 表单会显式传入
		``draft``，因此新岗位不会绕过页面上的人工发布确认。
		"""
		job_name = _limited_text(name, field_name="岗位名称", limit=_MAX_JOB_NAME_CHARS, required=True)
		job_city = _limited_text(city, field_name="工作城市", limit=_MAX_CITY_CHARS)
		job_salary = _limited_text(salary_range, field_name="薪资范围", limit=_MAX_SALARY_CHARS)
		explicit_education = _limited_text(education_requirement, field_name="学历要求", limit=_MAX_EDUCATION_CHARS)
		explicit_years = _optional_experience_years(min_experience_years)
		qa_enabled = _optional_professional_qa_enabled(professional_qa_enabled)
		assert qa_enabled is not None
		criteria_input = _limited_text(criteria_text, field_name="招聘标准", limit=_MAX_CRITERIA_CHARS)
		criteria, structured, warnings = parse_natural_language_job(criteria_input)
		education = _limited_text(
			explicit_education or str(structured.get("education_requirement") or ""),
			field_name="学历要求",
			limit=_MAX_EDUCATION_CHARS,
		)
		job_industry = _limited_text(
			industry or str(structured.get("industry") or ""),
			field_name="行业要求",
			limit=_MAX_INDUSTRY_CHARS,
		)
		raw_years = structured.get("min_experience_years")
		minimum_years = explicit_years if explicit_years is not None else (int(raw_years) if isinstance(raw_years, int) else None)
		raw_skills = structured.get("skills")
		skill_values = skills if skills is not None else (raw_skills if isinstance(raw_skills, list) else [])
		skills = [
			_limited_text(str(skill), field_name="技能要求", limit=_MAX_SKILL_CHARS, required=True)
			for skill in skill_values
			if str(skill).strip()
		]
		job = self._store.create_job(
			name=job_name,
			city=job_city,
			salary_range=job_salary,
			education_requirement=education,
			min_experience_years=minimum_years,
			industry=job_industry,
			skills=skills,
			criteria=criteria,
			professional_qa_enabled=qa_enabled,
			greeting_message=greeting_message.strip()[:100],
			status=_limited_text(status, field_name="岗位状态", limit=32, required=True),
		)
		return {
			"job": self._job_snapshot(job),
			"warnings": warnings,
			"readiness": evaluate_job_readiness(job),
		}

	def list_jobs(self) -> list[dict[str, Any]]:
		"""返回所有岗位的脱敏快照，供岗位选择器使用。"""
		return [self._job_snapshot(job) for job in self._store.list_jobs()]

	def update_job(
		self,
		job_id: str,
		*,
		name: str,
		city: str = "",
		salary_range: str = "",
		education_requirement: str = "",
	min_experience_years: int | None = None,
		industry: str = "",
		skills: list[str] | None = None,
		criteria_text: str = "",
		professional_qa_enabled: bool | None = None,
		greeting_message: str | None = None,
		publish_immediately: bool = False,
	) -> dict[str, Any]:
		"""更新岗位标准，并按入口明确决定是否立即生效。

		岗位标准属于筛选依据，普通表单修改默认不能继续沿用旧的发布确认；即使
		原岗位已经发布，保存修改也会将状态降为 ``draft``。岗位标准 Agent 是
		用户明确要求的“直接设置”入口，只有显式传入 ``publish_immediately``
		才会直接生效，避免普通编辑无意绕过这一边界。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		job = self._store.get_job(clean_job_id)
		if job is None:
			raise KeyError(clean_job_id)
		job_name = _limited_text(name, field_name="岗位名称", limit=_MAX_JOB_NAME_CHARS, required=True)
		job_city = _limited_text(city, field_name="工作城市", limit=_MAX_CITY_CHARS)
		job_salary = _limited_text(salary_range, field_name="薪资范围", limit=_MAX_SALARY_CHARS)
		explicit_education = _limited_text(education_requirement, field_name="学历要求", limit=_MAX_EDUCATION_CHARS)
		explicit_years = _optional_experience_years(min_experience_years)
		qa_enabled = _optional_professional_qa_enabled(professional_qa_enabled)
		criteria_input = _limited_text(criteria_text, field_name="招聘标准", limit=_MAX_CRITERIA_CHARS)
		criteria, structured, warnings = parse_natural_language_job(criteria_input)
		raw_years = structured.get("min_experience_years")
		raw_skills = structured.get("skills")
		job.name = job_name
		job.city = job_city
		job.salary_range = job_salary
		job.education_requirement = _limited_text(
			explicit_education or str(structured.get("education_requirement") or ""), field_name="学历要求", limit=_MAX_EDUCATION_CHARS,
		)
		job.min_experience_years = explicit_years if explicit_years is not None else (int(raw_years) if isinstance(raw_years, int) else None)
		job.industry = _limited_text(
			industry or str(structured.get("industry") or ""), field_name="行业要求", limit=_MAX_INDUSTRY_CHARS,
		)
		job.skills = [
			_limited_text(str(skill), field_name="技能要求", limit=_MAX_SKILL_CHARS, required=True)
			for skill in (skills if skills is not None else (raw_skills if isinstance(raw_skills, list) else []))
			if str(skill).strip()
		]
		job.criteria = criteria
		if qa_enabled is not None:
			job.professional_qa_enabled = qa_enabled
		if greeting_message is not None:
			job.greeting_message = greeting_message.strip()[:100]
		job.status = "published" if publish_immediately else "draft"
		if not publish_immediately:
			# 普通岗位编辑会改变筛选依据，保存草稿后必须重新人工确认，不能
			# 沿用修改前版本的启用标记继续跑自动化。
			job.rules_confirmed = False
		updated = self._store.update_job(job)
		return {"job": self._job_snapshot(updated), "warnings": warnings, "readiness": evaluate_job_readiness(updated)}

	def update_job_rules(
		self,
		job_id: str,
		*,
		criteria_text: str,
		weights: dict[str, int] | None = None,
		screening_threshold: int | None = None,
		recommendation_threshold: int | None = None,
		professional_qa_threshold: int | None = None,
	) -> dict[str, object]:
		"""仅更新岗位的四类筛选规则，保留 BOSS 同步字段与当前岗位状态。

		规则审核弹窗不拥有岗位名称、城市、薪资、学历等基础字段的编辑权；这些
		字段必须继续以 BOSS 职位同步为准。普通 ``update_job`` 会重算全部字段
		并改变发布状态，因此这里提供窄接口，防止保存 AI 补充规则时误覆盖同步
		结果或把已关闭岗位改为开放。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		job = self._store.get_job(clean_job_id)
		if job is None:
			raise KeyError(clean_job_id)
		criteria_input = _limited_text(criteria_text, field_name="招聘规则", limit=_MAX_CRITERIA_CHARS, required=True)
		criteria, _, warnings = parse_natural_language_job(criteria_input)
		job.criteria = criteria
		if weights is not None:
			expected = set(default_job_weights())
			if set(weights) != expected or any(isinstance(value, bool) or not 0 <= int(value) <= 100 for value in weights.values()):
				raise ValueError("评分权重字段无效或超出 0 到 100")
			if sum(int(value) for value in weights.values()) <= 0:
				raise ValueError("评分权重总和必须大于 0")
			job.weights = {key: int(weights[key]) for key in expected}
		for field_name, value in (
			("screening_threshold", screening_threshold),
			("recommendation_threshold", recommendation_threshold),
			("professional_qa_threshold", professional_qa_threshold),
		):
			if value is None:
				continue
			if isinstance(value, bool) or not 0 <= int(value) <= 100:
				raise ValueError("评分阈值必须在 0 到 100 之间")
			setattr(job, field_name, int(value))
		# 规则审核弹窗的保存动作就是用户明确确认。每次确认生成新版本，运行中
		# 的自动化在当前候选人步骤结束后重新加载岗位时即可切换到该版本。
		current_version = job.rules_version.removeprefix("v")
		try:
			next_version = int(current_version) + 1
		except ValueError:
			next_version = 2
		job.rules_confirmed = True
		job.rules_version = f"v{next_version}"
		job.rules_confirmed_at = utc_now_iso()
		updated = self._store.update_job(job)
		return {"job": self._job_snapshot(updated), "warnings": warnings, "readiness": evaluate_job_readiness(updated)}

	def publish_job(self, job_id: str) -> dict[str, Any]:
		"""人工确认岗位标准并发布到本地筛选工作流。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		job = self._store.get_job(clean_job_id)
		if job is None:
			raise KeyError(clean_job_id)
		readiness = evaluate_job_readiness(job)
		if not readiness["ready"]:
			missing = "、".join(str(item) for item in readiness["missing_required_fields"])
			raise ValueError(f"岗位标准尚未完整，不能发布；请补充：{missing}")
		job.rules_confirmed = True
		job.rules_confirmed_at = utc_now_iso()
		self._store.update_job(job)
		return self._job_snapshot(self._store.set_job_status(clean_job_id, "published"))

	def archive_job(self, job_id: str) -> dict[str, Any]:
		"""归档岗位并阻止后续评估，历史候选人和报告仍保留。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		return self._job_snapshot(self._store.set_job_status(clean_job_id, "archived"))

	def add_knowledge(
		self,
		job_id: str,
		*,
		category: str,
		title: str,
		content: str,
		audience: str = "",
		source_path: str = "",
		source_sha256: str = "",
	) -> dict[str, str]:
		"""为指定岗位添加一条企业销售或业务知识。

			手工录入也允许带来源元数据，统一走 Store 的同一条写入边界；这样
			知识回答可以区分“已审核 FAQ”和“仅检索到的知识摘录”。
			"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		document = self._store.add_knowledge(
			clean_job_id,
			category=_limited_text(category, field_name="知识类别", limit=32, required=True),
			title=_limited_text(title, field_name="知识标题", limit=_MAX_KNOWLEDGE_TITLE_CHARS, required=True),
			content=_limited_text(content, field_name="知识正文", limit=_MAX_KNOWLEDGE_CONTENT_CHARS, required=True),
			audience=_limited_text(audience, field_name="知识范围", limit=32),
			source_type="manual",
			source_path=_limited_text(source_path, field_name="知识来源路径", limit=_MAX_RESUME_PATH_CHARS),
			source_sha256=_limited_text(source_sha256, field_name="知识来源版本", limit=128),
		)
		return document.to_dict()

	def import_knowledge(
		self, job_id: str, file_path: Path | str, *, category: str, audience: str = ""
	) -> dict[str, Any]:
		"""导入本地岗位知识文件，并保留来源信息供问题和事实复核。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_category = _limited_text(category, field_name="知识类别", limit=32, required=True)
		return self._store.import_knowledge(
			clean_job_id,
			Path(file_path),
			category=clean_category,
			audience=_limited_text(audience, field_name="知识范围", limit=32),
		).to_dict()

	def add_faq(
		self,
		job_id: str,
		*,
		question: str,
		answer: str,
		allowed_variation: str = "",
		audience: str = "candidate",
		source_document_id: str = "",
		source_title: str = "",
		source_version: str = "",
	) -> dict[str, str]:
		"""为指定岗位保存一个候选人 FAQ 事实答案。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		faq = self._store.add_faq(
			clean_job_id,
			question=_limited_text(question, field_name="FAQ 问题", limit=_MAX_FAQ_QUESTION_CHARS, required=True),
			answer=_limited_text(answer, field_name="FAQ 答案", limit=_MAX_FAQ_ANSWER_CHARS, required=True),
			allowed_variation=_limited_text(allowed_variation, field_name="允许变化说明", limit=_MAX_FAQ_VARIATION_CHARS),
			audience=_limited_text(audience, field_name="FAQ 范围", limit=32),
			source_document_id=_limited_text(source_document_id, field_name="FAQ 来源文档", limit=128),
			source_title=_limited_text(source_title, field_name="FAQ 来源标题", limit=_MAX_KNOWLEDGE_TITLE_CHARS),
			source_version=_limited_text(source_version, field_name="FAQ 来源版本", limit=128),
		)
		return faq.to_dict()

	def generate_faq_drafts(self, job_id: str) -> list[dict[str, str]]:
		"""根据当前岗位知识生成待审核 FAQ 草稿，不会自动写入 FAQ。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		if self._store.get_job(clean_job_id) is None:
			raise KeyError(clean_job_id)
		return generate_faq_drafts(self._store.list_knowledge(clean_job_id))

	def create_optimization_draft(self, job_id: str, suggestion_id: str) -> dict[str, str]:
		"""把当前复盘建议显式保存为待审核草稿，重复请求按建议 ID 幂等。

		先从实时投影中定位建议，再交给 Store 保存，避免客户端自行伪造标题或
		原因；保存动作只增加本地审核记录，不会直接修改任何招聘配置。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_suggestion_id = _limited_text(suggestion_id, field_name="复盘建议标识", limit=128, required=True)
		projection = self.snapshot(clean_job_id)["optimization"]
		suggestion = next(
			(item for item in projection.get("suggestions", []) if item.get("suggestion_id") == clean_suggestion_id),
			None,
		)
		if not isinstance(suggestion, dict):
			raise ValueError("复盘建议已失效，请刷新工作区后重试")
		return self._store.create_optimization_draft(clean_job_id, suggestion)

	# ------------------------------------------------------------------
	# CommunicationTemplate management
	# ------------------------------------------------------------------

	def save_template(self, template: Any) -> dict[str, object]:
		"""创建或更新话术模板（轻量操作，不经过任务队列）。"""
		return self._store.save_template(template)

	def list_templates(self, *, job_id: str | None = None) -> list[dict[str, str]]:
		"""列出话术模板。"""
		return self._store.list_templates(job_id=job_id)

	def delete_template(self, template_id: str) -> bool:
		"""删除话术模板。"""
		return self._store.delete_template(template_id)

	def _save_and_return_daily_snapshot(
		self,
		*,
		candidate_count: int = 0,
		active_count: int = 0,
		assessed_count: int = 0,
		hired_count: int = 0,
		avg_score: float | None = None,
		knowledge_count: int = 0,
		faq_count: int = 0,
		communication_count: int = 0,
		interview_count: int = 0,
	) -> dict[str, Any]:
		"""保存当日指标快照并返回；失败时不阻塞工作台读取。"""
		try:
			metrics = build_daily_snapshot_metrics(
				candidate_count=candidate_count,
				active_candidate_count=active_count,
				assessed_count=assessed_count,
				hired_count=hired_count,
				avg_score=avg_score,
				knowledge_count=knowledge_count,
				faq_count=faq_count,
				communication_count=communication_count,
				interview_count=interview_count,
			)
			snapshot_id = self._store.save_daily_snapshot(metrics)
			return {"snapshot_id": snapshot_id, "date": snapshot_id.replace("daily-", ""), "metrics": metrics}
		except Exception:
			return {"snapshot_id": "", "date": "", "metrics": {}}

	def review_optimization_draft(self, draft_id: str, *, status: str, note: str = "") -> dict[str, str]:
		"""记录 HR 对复盘草稿的审核结果，不把审核误当成配置已生效。"""
		clean_draft_id = _limited_text(draft_id, field_name="改进草稿标识", limit=128, required=True)
		clean_status = _limited_text(status, field_name="改进草稿状态", limit=32, required=True)
		clean_note = _limited_text(note, field_name="改进草稿备注", limit=_MAX_REVIEW_NOTE_CHARS)
		return self._store.review_optimization_draft(clean_draft_id, status=clean_status, note=clean_note)

	def search_knowledge(self, job_id: str, query: str, *, audience: str | None = None) -> dict[str, Any]:
		"""在当前岗位范围内检索知识和 FAQ，并返回可人工核对的引用。

		检索只读本地 Store，不调用模型或平台；没有命中时明确返回空列表，
		前端因此可以阻止用户把无来源内容误当成企业事实。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_query = _limited_text(query, field_name="检索问题", limit=_MAX_SEARCH_QUERY_CHARS, required=True)
		if self._store.get_job(clean_job_id) is None:
			raise KeyError(clean_job_id)
		if audience is not None:
			audience = _limited_text(audience, field_name="知识范围", limit=32, required=True)
		hits = [
			*self._store.search_knowledge(clean_job_id, clean_query, audience=audience),
			*self._store.search_faq(clean_job_id, clean_query, audience=audience),
		]
		hits.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("source_title", ""))))
		return {"query": clean_query, "hits": hits[:10]}

	def answer_question(self, job_id: str, question: str) -> dict[str, Any]:
		"""基于当前岗位的 FAQ 或知识库事实生成可人工核对的试答。

			这是一个受控回答入口，而不是通用聊天模型：已审核 FAQ 优先，只有
			FAQ 没有命中时才使用知识库的短摘录；两者都没有命中就明确拒答。
			返回值只包含一条答案和来源元数据，不返回整篇知识正文、文件路径或
			候选人数据，也不会触发 BOSS 平台动作。
			"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_question = _limited_text(question, field_name="候选人问题", limit=_MAX_SEARCH_QUERY_CHARS, required=True)
		if self._store.get_job(clean_job_id) is None:
			raise KeyError(clean_job_id)

		# 对外试答强制使用 candidate/shared 范围，防止内部销售手册被误当成
		# 候选人可见事实；FAQ 和知识摘录都走同一条过滤边界。
		faq_hits = self._store.search_faq(clean_job_id, clean_question, limit=1, audience="candidate")
		if faq_hits:
			hit = faq_hits[0]
			faq = next((entry for entry in self._store.list_faq(clean_job_id) if entry.faq_id == hit.get("source_id")), None)
			if faq is not None:
				result = {
					"status": "answered",
					"answer": faq.answer,
					"source_type": "faq",
					"source_id": faq.faq_id,
					"source_title": faq.source_title or faq.question,
					"source_version": faq.source_version,
					"confidence": "faq",
					"matched_question": faq.question,
				}
				self._store.record_question_demand(
					clean_job_id,
					query=clean_question,
					status=str(result["status"]),
					source_type=str(result["source_type"]),
					source_id=str(result["source_id"]),
					source_title=str(result["source_title"]),
				)
				return result

		knowledge_hits = self._store.search_knowledge(
			clean_job_id, clean_question, limit=1, audience="candidate"
		)
		if knowledge_hits:
			hit = knowledge_hits[0]
			result = {
				"status": "answered",
				"answer": str(hit.get("snippet") or ""),
				"source_type": "knowledge",
				"source_id": str(hit.get("source_id") or ""),
				"source_title": str(hit.get("source_title") or hit.get("title") or "未命名知识"),
				"source_version": str(hit.get("source_sha256") or ""),
				"confidence": "knowledge_excerpt",
			}
			self._store.record_question_demand(
				clean_job_id,
				query=clean_question,
				status=str(result["status"]),
				source_type=str(result["source_type"]),
				source_id=str(result["source_id"]),
				source_title=str(result["source_title"]),
			)
			return result

		result = {
			"status": "no_source",
			"answer": "暂无可基于当前岗位本地事实确认的答案，请补充知识库或人工核实。",
			"source_type": "",
			"source_id": "",
			"source_title": "",
			"source_version": "",
			"confidence": "none",
		}
		self._store.record_question_demand(clean_job_id, query=clean_question, status="no_source")
		return result

	def import_candidate(
		self,
		file_path: Path | str,
		*,
		source: str = "local_markdown",
		job_id: str | None = None,
	) -> dict[str, str]:
		"""导入一份用户明确选择的本地 Markdown/TXT 简历引用。"""
		path_text = str(file_path).strip()
		if not path_text:
			raise ValueError("简历路径不能为空")
		if len(path_text) > _MAX_RESUME_PATH_CHARS:
			raise ValueError("简历路径过长")
		clean_source = _limited_text(source, field_name="候选人来源", limit=64, required=True)
		if clean_source not in _ALLOWED_CANDIDATE_SOURCES:
			raise ValueError("候选人来源不受支持")
		clean_job_id = _limited_text(job_id or "", field_name="岗位标识", limit=128) or None
		candidate = self._store.import_candidate(Path(path_text), source=clean_source, job_id=clean_job_id)
		return candidate.to_dict()

	def auto_assign_local_resumes(
		self,
		directory: Path | str,
		*,
		ai_reviewer: Callable[[JobProfile, str], AIResumeReview | None] | None = None,
	) -> dict[str, Any]:
		"""扫描本地简历并按最高匹配分自动绑定岗位。

		批处理的评分、目录过滤和关闭岗位排除都由独立服务承担；工作台只作为
		应用层入口，保证 Web、CLI 或未来定时任务走同一套本地审计与存储边界。
		"""
		return AutoResumeAssignmentService(self._store, ai_reviewer=ai_reviewer).scan_and_assign(directory)

	def assess(
		self,
		job_id: str,
		candidate_id: str,
		*,
		ai_reviewer: Callable[[JobProfile, str], AIResumeReview | None] | None = None,
	) -> dict[str, Any]:
		"""读取候选人正文并生成、保存一份必须人工确认的评估报告。

		``ai_reviewer`` 是可选的语义评审器（见 :mod:`boss_agent_cli.recruiting.ai_review`）。
		不传时行为与纯规则完全一致，Web 控制台因此无需改动；传入时简历正文会被发送
		到调用方已配置的 AI 服务，所以是否启用必须由命令层显式决定，本方法不自己去
		读 AI 配置。评审器抛异常时由调用方负责降级，这里不吞掉失败原因。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		job = self._store.get_job(clean_job_id)
		candidate = self._store.get_candidate(clean_candidate_id)
		if job is None:
			raise KeyError(clean_job_id)
		if candidate is None:
			raise KeyError(clean_candidate_id)
		candidate_stage = self._store.get_candidate_job_state(clean_candidate_id, clean_job_id).get("stage", candidate.stage)
		if candidate_stage in {"hired", "rejected", "paused"}:
			# 终局候选人的评估和待办都已经闭合；允许旧卡片再次生成报告会
			# 把“流程已完成”与“可继续操作”同时展示，且可能重新打开沟通链路。
			raise ValueError("候选人已进入终局，不能重新生成评估")
		if job.status != "published":
			raise ValueError("岗位尚未发布，不能生成评估")
		linked_jobs = self._store.list_candidate_job_ids(clean_candidate_id)
		if linked_jobs and clean_job_id not in linked_jobs:
			raise ValueError("候选人未绑定当前岗位，请先从候选人卡片绑定")
		if not linked_jobs:
			self._store.link_candidate_to_job(clean_candidate_id, clean_job_id)
		resume_text = self._store.read_candidate_resume(clean_candidate_id)
		answers = self._store.list_candidate_answers(clean_job_id, clean_candidate_id)
		knowledge = self._store.list_knowledge(clean_job_id)
		report = score_candidate(
			job,
			candidate_id=candidate.candidate_id,
			candidate_name=candidate.name,
			resume_text=resume_text,
			answers=answers,
			knowledge_documents=knowledge,
			ai_review=ai_reviewer(job, resume_text) if ai_reviewer is not None else None,
		)
		# 评估保存时同步计算一次门禁快照，前端可以直接告诉 HR 缺哪一项，
		# 不必等到点击“继续沟通”后才收到模糊错误。
		report_payload = report.to_dict()
		report_payload["review_gate"] = build_review_gate(
			report_payload,
			candidate_stage=candidate_stage,
			candidate_events=self._store.list_candidate_events(clean_candidate_id, job_id=clean_job_id),
		)
		return self._store.save_assessment(clean_job_id, clean_candidate_id, report_payload)

	def record_mismatch_feedback(
		self,
		job_id: str,
		candidate_id: str,
		*,
		reason_code: str,
		stage: str,
		note: str = "",
	) -> dict[str, str | bool]:
		"""记录不匹配原因，默认只落本地，不自动提交平台。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_reason = _limited_text(reason_code, field_name="不匹配原因", limit=_MAX_MISMATCH_REASON_CHARS, required=True)
		clean_stage = _limited_text(stage, field_name="筛选阶段", limit=_MAX_MISMATCH_STAGE_CHARS, required=True)
		clean_note = _limited_text(note, field_name="不匹配备注", limit=_MAX_REVIEW_NOTE_CHARS)
		if self._store.get_job(clean_job_id) is None or self._store.get_candidate(clean_candidate_id) is None:
			raise KeyError(clean_candidate_id)
		linked_jobs = self._store.list_candidate_job_ids(clean_candidate_id)
		if linked_jobs and clean_job_id not in linked_jobs:
			raise ValueError("候选人未绑定当前岗位，不能记录不匹配反馈")
		if not linked_jobs:
			self._store.link_candidate_to_job(clean_candidate_id, clean_job_id)
		return self._store.record_mismatch_feedback(
			clean_job_id,
			clean_candidate_id,
			reason_code=clean_reason,
			stage=clean_stage,
			note=clean_note,
		)

	def record_answer(
		self,
		job_id: str,
		candidate_id: str,
		*,
		question: str,
		answer: str,
		question_id: str = "",
		question_version: str = "v1",
		source_ids: list[str] | None = None,
		follow_up_of: str = "",
		channel: str = "boss",
		verification_status: str = "recorded",
	) -> dict[str, Any]:
		"""保存一条人工录入的候选人回答，供下一次综合评估使用。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		return self._store.save_candidate_answer(
			clean_job_id,
			clean_candidate_id,
			question=_limited_text(question, field_name="专业问题", limit=1_000, required=True),
			answer=_limited_text(answer, field_name="候选人回答", limit=8_000, required=True),
			question_id=_limited_text(question_id, field_name="问题标识", limit=128),
			question_version=_limited_text(question_version, field_name="问题版本", limit=64) or "v1",
			source_ids=[_limited_text(str(item), field_name="问题来源", limit=128, required=True) for item in (source_ids or [])],
			follow_up_of=_limited_text(follow_up_of, field_name="追问标识", limit=128),
			channel=_limited_text(channel, field_name="回答来源渠道", limit=32) or "boss",
			verification_status=_limited_text(verification_status, field_name="回答核验结论", limit=32) or "recorded",
		)

	def record_private_professional_qa(
		self,
		job_id: str,
		candidate_id: str,
		*,
		question: str,
		answer: str,
		question_id: str = "",
		question_version: str = "v1",
		source_ids: list[str] | None = None,
		outcome: str = "passed",
		note: str = "",
		follow_up_of: str = "",
	) -> dict[str, Any]:
		"""记录岗位关闭 BOSS 问答后的私域专业核验，并推进专用待办。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		return self._store.record_private_professional_qa(
			clean_job_id,
			clean_candidate_id,
			question=_limited_text(question, field_name="专业问题", limit=1_000, required=True),
			answer=_limited_text(answer, field_name="候选人回答", limit=8_000, required=True),
			question_id=_limited_text(question_id, field_name="问题标识", limit=128),
			question_version=_limited_text(question_version, field_name="问题版本", limit=64) or "v1",
			source_ids=[_limited_text(str(item), field_name="问题来源", limit=128, required=True) for item in (source_ids or [])],
			outcome=_limited_text(outcome, field_name="私域核验结果", limit=32, required=True),
			note=_limited_text(note, field_name="私域核验备注", limit=_MAX_REVIEW_NOTE_CHARS),
			follow_up_of=_limited_text(follow_up_of, field_name="追问标识", limit=128),
		)

	def list_candidate_answers(self, job_id: str, candidate_id: str) -> list[dict[str, str | int]]:
		"""读取本地回答正文，供评分器或人工导出使用，不直接返回 Web。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		return self._store.list_candidate_answers(clean_job_id, clean_candidate_id)

	def record_communication(
		self,
		job_id: str,
		candidate_id: str,
		*,
		round_number: int,
		outcome: str,
		candidate_reply_summary: str = "",
		note: str = "",
		next_follow_up_at: str = "",
		template_key: str = "",
		template_version: str = "",
	) -> dict[str, Any]:
		"""记录一轮人工沟通，并把下一轮跟进挂回本地待办中心。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_outcome = _limited_text(
			outcome, field_name="沟通结果", limit=_MAX_COMMUNICATION_OUTCOME_CHARS, required=True,
		)
		return self._store.record_communication(
			clean_job_id,
			clean_candidate_id,
			round_number=round_number,
			outcome=clean_outcome,
			candidate_reply_summary=_limited_text(
				candidate_reply_summary,
				field_name="候选人回复摘要",
				limit=_MAX_COMMUNICATION_SUMMARY_CHARS,
			),
			note=_limited_text(note, field_name="沟通备注", limit=_MAX_REVIEW_NOTE_CHARS),
			next_follow_up_at=_limited_text(
				next_follow_up_at,
				field_name="下一次跟进时间",
				limit=_MAX_INTERVIEW_TEXT_CHARS,
			),
			template_key=_limited_text(template_key, field_name="话术标识", limit=128),
			template_version=_limited_text(template_version, field_name="话术版本", limit=64),
		)

	def record_message_template_usage(
		self,
		job_id: str,
		*,
		candidate_id: str = "",
		template_key: str,
		template_version: str = "v1",
		note: str = "",
	) -> dict[str, str]:
		"""记录 HR 已复制并人工使用的话术版本，不执行发送动作。"""
		return self._store.record_message_template_usage(
			_limited_text(job_id, field_name="岗位标识", limit=128, required=True),
			candidate_id=_limited_text(candidate_id, field_name="候选人标识", limit=128),
			template_key=_limited_text(template_key, field_name="话术标识", limit=128, required=True),
			template_version=_limited_text(template_version, field_name="话术版本", limit=64) or "v1",
			note=_limited_text(note, field_name="话术备注", limit=_MAX_REVIEW_NOTE_CHARS),
		)

	def transition_candidate(
		self,
		candidate_id: str,
		*,
		job_id: str | None = None,
		stage: str,
		action: str,
		note: str = "",
		ai_judgment: str = "",
		candidate_quote: str = "",
	) -> dict[str, dict[str, str]]:
		"""记录一次人工阶段流转；这个动作只更新本地记录，不触发 BOSS 操作。"""
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_stage = _limited_text(stage, field_name="候选人阶段", limit=64, required=True)
		if clean_stage not in CANDIDATE_STAGE_LABELS:
			raise ValueError("候选人阶段不受支持")
		result = self._store.transition_candidate(
			clean_candidate_id,
			job_id=_limited_text(job_id or "", field_name="岗位标识", limit=128) or None,
			stage=clean_stage,
			action=_limited_text(action, field_name="阶段动作", limit=160, required=True),
			note=_limited_text(note, field_name="阶段备注", limit=1_000),
			ai_judgment=_limited_text(ai_judgment, field_name="AI 判断", limit=1_000),
			candidate_quote=_limited_text(candidate_quote, field_name="候选人原话", limit=2_000),
		)
		# 原话只用于本地审计，不能进入 Web 任务结果或浏览器状态。
		return {
			"candidate": result["candidate"],
			"event": {key: value for key, value in result["event"].items() if key != "candidate_quote"},
		}

	def confirm_basic_intent(self, job_id: str, candidate_id: str, *, note: str) -> dict[str, Any]:
		"""记录基础意向人工确认，为后续专业问答和最终门禁提供审计事实。"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_note = _limited_text(note, field_name="基础意向备注", limit=_MAX_REVIEW_NOTE_CHARS, required=True)
		result = self._store.confirm_basic_intent(clean_job_id, clean_candidate_id, note=clean_note)
		return {"candidate": result["candidate"], "event": {key: value for key, value in result["event"].items() if key != "candidate_quote"}}

	def list_candidate_events(
		self, candidate_id: str, *, job_id: str | None = None,
	) -> list[dict[str, str]]:
		"""读取本地审计事件，可按岗位返回当前流程的时间线。"""
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_job_id = (
			_limited_text(job_id, field_name="岗位标识", limit=128, required=True)
			if job_id is not None
			else None
		)
		return self._store.list_candidate_events(clean_candidate_id, job_id=clean_job_id)

	def complete_task(
		self,
		task_id: str,
		*,
		status: str = "completed",
		note: str = "",
		target_stage: str | None = None,
	) -> dict[str, Any]:
		"""完成或跳过一个本地待办，并返回阶段推进后的安全元数据。"""
		clean_task_id = _limited_text(task_id, field_name="待办标识", limit=128, required=True)
		clean_status = _limited_text(status, field_name="待办状态", limit=16, required=True)
		clean_note = _limited_text(note, field_name="待办备注", limit=_MAX_REVIEW_NOTE_CHARS)
		clean_target_stage = _limited_text(target_stage or "", field_name="目标阶段", limit=64)
		return self._store.complete_task(
			clean_task_id,
			status=clean_status,
			note=clean_note,
			target_stage=clean_target_stage or None,
		)

	def record_private_contact(
		self,
		candidate_id: str,
		*,
		job_id: str | None = None,
		channel: str,
		status: str,
		note: str = "",
	) -> dict[str, Any]:
		"""记录人工确认的私域结果，不执行任何外部添加动作。"""
		return self._store.record_private_contact(
			_limited_text(candidate_id, field_name="候选人标识", limit=128, required=True),
			job_id=_limited_text(job_id or "", field_name="岗位标识", limit=128) or None,
			channel=_limited_text(channel, field_name="私域渠道", limit=32, required=True),
			status=_limited_text(status, field_name="私域状态", limit=32, required=True),
			note=_limited_text(note, field_name="私域备注", limit=_MAX_REVIEW_NOTE_CHARS),
		)

	def schedule_interview(
		self,
		job_id: str,
		candidate_id: str,
		*,
		scheduled_at: str,
		interviewer: str = "",
		note: str = "",
	) -> dict[str, Any]:
		"""记录 HR 已经在官方页面完成的面试邀约。"""
		return self._store.schedule_interview(
			_limited_text(job_id, field_name="岗位标识", limit=128, required=True),
			_limited_text(candidate_id, field_name="候选人标识", limit=128, required=True),
			scheduled_at=_limited_text(scheduled_at, field_name="面试时间", limit=_MAX_INTERVIEW_TEXT_CHARS, required=True),
			interviewer=_limited_text(interviewer, field_name="面试官", limit=_MAX_INTERVIEW_TEXT_CHARS),
			note=_limited_text(note, field_name="面试备注", limit=_MAX_REVIEW_NOTE_CHARS),
		)

	def record_interview_result(
		self,
		job_id: str,
		candidate_id: str,
		*,
		outcome: str,
		note: str = "",
	) -> dict[str, Any]:
		"""记录面试完成、未通过或取消结果，并生成下一步本地待办。"""
		return self._store.record_interview_result(
			_limited_text(job_id, field_name="岗位标识", limit=128, required=True),
			_limited_text(candidate_id, field_name="候选人标识", limit=128, required=True),
			outcome=_limited_text(outcome, field_name="面试结果", limit=32, required=True),
			note=_limited_text(note, field_name="面试备注", limit=_MAX_REVIEW_NOTE_CHARS),
		)

	def review_assessment(
		self,
		job_id: str,
		candidate_id: str,
		*,
		outcome: str,
		note: str = "",
		manual_override: bool = False,
		override_reason: str = "",
	) -> dict[str, Any]:
		"""记录 HR 对评估报告的人工确认，并明确下一步动作。

		``proceed`` 默认必须通过评估门禁；门禁未通过时只有 HR 明确勾选
		人工强制继续并填写理由才会落盘。这个边界放在应用服务层而不是页面
		层，确保 CLI、Web 和未来其他入口使用同一套规则。
		"""
		clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
		clean_candidate_id = _limited_text(candidate_id, field_name="候选人标识", limit=128, required=True)
		clean_outcome = _limited_text(outcome, field_name="确认结果", limit=32, required=True)
		if clean_outcome not in _REVIEW_OUTCOMES:
			raise ValueError("确认结果不受支持")
		clean_note = _limited_text(note, field_name="人工备注", limit=_MAX_REVIEW_NOTE_CHARS)
		if not isinstance(manual_override, bool):
			raise ValueError("人工强制继续标识无效")
		clean_override_reason = _limited_text(
			override_reason,
			field_name="强制继续理由",
			limit=_MAX_OVERRIDE_REASON_CHARS,
		)
		if self._store.get_job(clean_job_id) is None or self._store.get_candidate(clean_candidate_id) is None:
			raise KeyError(clean_candidate_id)
		report = self._store.get_assessment(clean_job_id, clean_candidate_id)
		if not isinstance(report, dict):
			raise KeyError(f"{clean_job_id}:{clean_candidate_id}")
		candidate = self._store.get_candidate(clean_candidate_id)
		if candidate is None:
			raise KeyError(clean_candidate_id)
		if report.get("review_required") is False:
			# 人工确认是一次性的决策节点；重复点击旧报告会把已进入终局或
			# 已开始沟通的候选人重新拉回流程。需要再次判断时必须先生成新报告，
			# 这样评估时间、证据和审计事件能够保持一一对应。
			raise ValueError("评估已经人工确认，请先重新生成评估")
		candidate_events = self._store.list_candidate_events(clean_candidate_id, job_id=clean_job_id)
		candidate_stage = self._store.get_candidate_job_state(clean_candidate_id, clean_job_id).get("stage", candidate.stage)
		review_gate = build_review_gate(
			report,
			candidate_stage=candidate_stage,
			candidate_events=candidate_events,
			candidate_answers=self._store.list_candidate_answers(clean_job_id, clean_candidate_id),
		)
		# 保留专业问答低分的明确旧错误文案，帮助正在使用旧页面的 HR
		# 立刻知道该补哪一步；其他门禁统一返回可解释的失败摘要。
		if clean_outcome == "proceed":
			if manual_override and not clean_override_reason:
				raise ValueError("人工强制继续必须填写强制继续理由")
			if not manual_override and not review_gate["eligible"]:
				qa = report.get("screening", {}).get("professional_qa") if isinstance(report.get("screening"), dict) else None
				if isinstance(qa, dict) and qa.get("status") == "follow_up":
					raise ValueError("专业问答低于 60 分，请先完成追问后重新评估")
				if any(
					item.get("code") == "basic_intent"
					for item in review_gate.get("failed_checks", [])
					if isinstance(item, dict)
				):
					raise ValueError("评估门禁未通过：请先完成基础意向确认，再人工复核评估")
				raise ValueError(f"评估门禁未通过：{review_gate['summary']}")
		decision, next_action, _ = _REVIEW_OUTCOMES[clean_outcome]
		candidate_stage = _candidate_stage_after_review(candidate_stage, clean_outcome)
		return self._store.review_assessment(
			clean_job_id,
			clean_candidate_id,
			outcome=clean_outcome,
			decision=decision,
			next_action=next_action,
			note=clean_note,
			candidate_stage=candidate_stage,
			manual_override=manual_override,
			override_reason=clean_override_reason,
			review_gate=review_gate,
		)

	def snapshot(self, job_id: str | None = None) -> dict[str, Any]:
		"""返回页面所需工作区快照，候选人只包含路径和哈希等元数据。"""
		selected_job: JobProfile | None = None
		if job_id is not None:
			clean_job_id = _limited_text(job_id, field_name="岗位标识", limit=128, required=True)
			selected_job = self._store.get_job(clean_job_id)
			if selected_job is None:
				raise KeyError(clean_job_id)
		# 岗位选择器需要始终看到当前可操作岗位；知识库和 FAQ 再按 selected_id
		# 隔离。BOSS 镜像被标记为 not_discovered 表示本次职位管理页未发现它。
		# 没有评估证据的旧镜像继续隐藏，防止旧版 RPA 误建的名称污染当前招聘；
		# 已完成分析的关闭岗位则必须保留，确保候选人、评分和原岗位始终可追溯。
		jobs = [
			job
			for job in self._store.list_jobs()
			if not (
				# 归档代表 HR 已明确停止使用该岗位。记录和候选人关联仍在
				# Store 中保留用于审计，但不再作为新分析或岗位选择的入口。
				job.status == "archived"
				or (
				job.source == "boss"
				and job.platform_sync_status == "not_discovered"
				and not self._store.has_assessments_for_job(job.job_id)
				)
			)
		]
		jobs.sort(key=self._job_display_order)
		selected_id = selected_job.job_id if selected_job is not None else (jobs[0].job_id if jobs else None)
		knowledge = self._store.list_knowledge(selected_id) if selected_id else []
		faq = self._store.list_faq(selected_id) if selected_id else []
		candidates = self._store.list_candidates(selected_id)
		candidate_rows: list[dict[str, Any]] = []
		stage_counts = {stage: 0 for stage in CANDIDATE_STAGE_ORDER}
		all_tasks = self._store.list_candidate_tasks()
		visible_candidate_ids = {candidate.candidate_id for candidate in candidates}
		visible_tasks = [
			task for task in all_tasks
			if not selected_id
			or (
				task.candidate_id in visible_candidate_ids
				and task.job_id in {"", selected_id}
			)
		]
		task_by_candidate: dict[str, list[dict[str, Any]]] = {}
		for task in visible_tasks:
			task_by_candidate.setdefault(task.candidate_id, []).append(task.to_dict())
		for candidate in candidates:
			row: dict[str, Any] = candidate.to_dict()
			# 工作台只保存简历路径而非正文。文件可能被用户移动或清理，因此每次
			# 快照都显式给出可用性，防止前端把失效历史记录误显示为可重新评分的
			# 候选人；缺失时仍保留元数据和历史评估以满足审计追溯。
			try:
				row["resume_available"] = Path(str(row.get("resume_path") or "")).expanduser().is_file()
			except OSError:
				row["resume_available"] = False
			job_state = self._store.get_candidate_job_state(candidate.candidate_id, selected_id) if selected_id else {
				"stage": candidate.stage,
				"stage_updated_at": candidate.stage_updated_at,
				"last_action": candidate.last_action,
			}
			candidate_stage = str(job_state.get("stage") or candidate.stage)
			row["stage"] = candidate_stage
			row["stage_label"] = CANDIDATE_STAGE_LABELS.get(candidate_stage, CANDIDATE_STAGE_LABELS["pending_screening"])
			row["stage_updated_at"] = str(job_state.get("stage_updated_at") or candidate.stage_updated_at)
			row["last_action"] = str(job_state.get("last_action") or candidate.last_action)
			events = self._store.list_candidate_events(candidate.candidate_id, job_id=selected_id)
			communications = self._store.list_communications(selected_id, candidate.candidate_id) if selected_id else self._store.list_communications(candidate_id=candidate.candidate_id)
			row["event_count"] = len(events)
			row["answer_count"] = self._store.count_candidate_answers(candidate.candidate_id)
			row["communication_count"] = len(communications)
			row["communication_timeline"] = communications
			row["last_event_at"] = events[-1]["created_at"] if events else row["stage_updated_at"]
			row["timeline"] = [
				{
					key: event.get(key, "")
					for key in ("event_id", "stage", "stage_label", "action", "actor", "note", "ai_judgment", "created_at")
				}
				for event in events
			]
			candidate_tasks = [
				task for task in task_by_candidate.get(candidate.candidate_id, [])
				if not selected_id or task.get("job_id") in {"", selected_id}
			]
			# 候选人卡片的“下一步”必须从待处理任务中计算，不能直接取
			# JSON 插入顺序里的第一条历史任务；评估完成后，历史的“生成评估”
			# 往往排在当前“人工确认评估”之前，旧逻辑因此会把用户引回旧动作。
			pending_candidate_tasks = [task for task in candidate_tasks if task.get("status") == "pending"]
			pending_candidate_tasks.sort(
				key=lambda task: (
					0 if str(task.get("due_at") or "") else 1,
					str(task.get("due_at") or ""),
					str(task.get("updated_at") or task.get("created_at") or ""),
				)
			)
			pending_task = pending_candidate_tasks[0] if pending_candidate_tasks else None
			row["pending_task_count"] = len(pending_candidate_tasks)
			row["pending_task_id"] = str(pending_task.get("task_id") or "") if pending_task else ""
			row["pending_task_kind"] = str(pending_task.get("kind") or "") if pending_task else ""
			row["pending_task_title"] = str(pending_task.get("title") or "") if pending_task else ""
			# 保留旧字段供已有页面使用，但语义收敛为“下一条待办标题”；
			# 没有待办时返回空串，终局和“待复核”由 next_action 区分。
			row["last_task_title"] = row["pending_task_title"]
			if pending_task:
				row["next_action"] = row["pending_task_title"]
			elif candidate_stage in {"hired", "rejected", "paused"}:
				row["next_action"] = "流程已完成"
			else:
				row["next_action"] = "请检查已跳过的待办或记录下一步"
			follow_up_times = [
				str(task.get("due_at") or "")
				for task in candidate_tasks
				if task.get("status") == "pending" and task.get("due_at")
			]
			row["next_follow_up_at"] = min(follow_up_times) if follow_up_times else ""
			stage_counts[candidate_stage] = stage_counts.get(candidate_stage, 0) + 1
			candidate_rows.append(row)
		total_candidates = len(candidate_rows)
		funnel: list[dict[str, Any]] = []
		for stage in CANDIDATE_STAGE_ORDER:
			stage_rows = [row for row in candidate_rows if row.get("stage") == stage]
			count = len(stage_rows)
			funnel.append(
				{
					"stage": stage,
					"label": CANDIDATE_STAGE_LABELS[stage],
					"count": count,
					"share": round(count / total_candidates * 100) if total_candidates else 0,
					"avg_age_hours": round(
						sum(_age_hours(str(row.get("stage_updated_at") or "")) for row in stage_rows) / count,
						1,
					)
					if count
					else 0,
				}
			)
		source_counts: dict[str, int] = {}
		for row in candidate_rows:
			source = str(row.get("source") or "local_markdown")
			source_counts[source] = source_counts.get(source, 0) + 1
		sources = [
			{
				"source": source,
				"label": _SOURCE_LABELS.get(source, "其他来源"),
				"count": count,
				"share": round(count / total_candidates * 100) if total_candidates else 0,
			}
			for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
		]
		screened_count = total_candidates - stage_counts.get("pending_screening", 0)
		reviewed_count = sum(
			stage_counts.get(stage, 0)
			for stage in (
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
		)
		terminal_count = sum(stage_counts.get(stage, 0) for stage in ("hired", "rejected", "paused"))
		pipeline_stats = {
			"total": total_candidates,
			"counts": stage_counts,
			# 暂缓与录用、淘汰一样都是终局；把它算进 active 会让漏斗
			# 显示仍有候选人需要处理，和顶部“流程已完成”产生矛盾。
			"active": sum(count for stage, count in stage_counts.items() if stage not in {"rejected", "hired", "paused"}),
			"pending_tasks": sum(1 for task in visible_tasks if task.status == "pending"),
			"completed_tasks": sum(1 for task in visible_tasks if task.status == "completed"),
			"funnel": funnel,
			"sources": sources,
			"conversion": {
				"screened_rate": round(screened_count / total_candidates * 100) if total_candidates else 0,
				"reviewed_rate": round(reviewed_count / total_candidates * 100) if total_candidates else 0,
				"terminal_rate": round(terminal_count / total_candidates * 100) if total_candidates else 0,
			},
		}
		assessments: list[dict[str, Any]] = []
		if selected_id:
			for candidate in candidates:
				report = self._store.get_assessment(selected_id, candidate.candidate_id)
				if report is not None:
					# 门禁依赖人工阶段事件；每次刷新重新计算，避免 HR 记录基础意向
					# 后页面仍显示上一次“未通过”的旧快照。
					job_stage = self._store.get_candidate_job_state(candidate.candidate_id, selected_id).get("stage", candidate.stage)
					review_gate = build_review_gate(
						report,
						candidate_stage=job_stage,
						candidate_events=self._store.list_candidate_events(candidate.candidate_id, job_id=selected_id),
						candidate_answers=self._store.list_candidate_answers(selected_id, candidate.candidate_id),
					)
					updated_report = dict(report)
					updated_report["review_gate"] = review_gate
					assessments.append(updated_report)
		job_rows = [self._job_snapshot(job) for job in jobs]
		workflow = build_workflow_projection(
			jobs=job_rows,
			candidates=candidate_rows,
			tasks=[task.to_dict() for task in visible_tasks],
			assessments=assessments,
			selected_job_id=selected_id,
		)
		score_groups = build_score_groups(
			candidates=candidate_rows,
			assessments=assessments,
			selected_job_id=selected_id,
		)
		communications = self._store.list_communications(selected_id)
		template_usages = self._store.list_message_template_usages(selected_id)
		decisions = self._store.list_candidate_decisions(selected_id)
		mismatch_feedback = self._store.list_mismatch_feedback(selected_id)
		question_demands = self._store.list_question_demands(selected_id)
		optimization = build_optimization_projection(
			job_id=selected_id or "all",
			candidate_count=total_candidates,
			knowledge_count=len(knowledge),
			faq_count=len(faq),
			assessment_count=len(assessments),
			professional_qa_scores=[
				int(report["professional_qa_score"])
				for report in assessments
				if isinstance(report.get("professional_qa_score"), (int, float))
			],
			mismatch_reasons=[str(item.get("reason_code") or "") for item in mismatch_feedback],
			communication_outcomes=[str(item.get("outcome") or "") for item in communications],
			decision_outcomes=[str(item.get("outcome") or "") for item in decisions],
			candidate_rows=candidate_rows,
			assessment_rows=assessments,
			template_usages=template_usages,
			communication_rows=communications,
			decision_rows=decisions,
			question_demands=question_demands,
		)
		optimization_drafts = self._store.list_optimization_drafts(selected_id)
		# 不合格原因只从当前岗位已经生成的评估报告汇总；它是只读复盘信息，
		# 不会反向修改候选人阶段、岗位条件或人工确认结论。
		rejection_reason_statistics = build_rejection_reason_statistics(assessments)
		return {
			"selected_job_id": selected_id,
			"jobs": job_rows,
			"knowledge": [document.to_dict() for document in knowledge],
			"faq": [entry.to_dict() for entry in faq],
			"candidates": candidate_rows,
			"tasks": [
				{
					**task.to_dict(),
					"candidate_name": next((candidate.name for candidate in candidates if candidate.candidate_id == task.candidate_id), "未命名候选人"),
				}
				for task in visible_tasks
			],
			"pipeline": pipeline_stats,
			"assessments": assessments,
			"communications": communications,
			"message_template_usages": template_usages,
			"workflow": workflow,
			# 分数段固定为六组，前端可以稳定渲染空组；候选人只使用当前岗位
			# 报告归类，避免多岗位招聘时把另一职位的分数显示到这里。
			"score_groups": score_groups,
			"rejection_reason_statistics": rejection_reason_statistics,
			"private_contacts": self._store.list_private_contacts(job_id=selected_id),
			"interviews": self._store.list_interview_invites(selected_id),
			"decisions": decisions,
			"mismatch_feedback": mismatch_feedback,
			"question_demands": question_demands,
			"optimization": optimization,
			"optimization_drafts": optimization_drafts,
			"templates": self._store.list_templates(job_id=selected_id),
			"daily_snapshot": self._save_and_return_daily_snapshot(
				candidate_count=total_candidates,
				active_count=pipeline_stats["active"],
				assessed_count=len(assessments),
				hired_count=stage_counts.get("hired", 0),
				avg_score=round(
					sum(int(r.get("final_score", 0)) for r in assessments) / len(assessments), 1
				) if assessments else None,
				knowledge_count=len(knowledge),
				faq_count=len(faq),
				communication_count=len(communications),
				interview_count=len(self._store.list_interview_invites(selected_id)),
			),
			"historical_snapshots": self._store.get_daily_snapshots(days=30),
		}
