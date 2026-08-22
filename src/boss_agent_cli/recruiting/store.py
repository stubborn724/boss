"""招聘工作台的本地 JSON 持久化。

工作台目前是单机、单用户工具，JSON 文件比引入数据库更容易备份和迁移；写入
仍采用同目录临时文件 + fsync + replace，避免服务中断留下半份岗位或评估记录。
所有公开方法只返回结构化元数据，候选人简历正文在评估时按路径短暂读取，不
进入工作区状态快照。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Any, Mapping

from boss_agent_cli.recruiting.models import (
	CandidateAnswer,
	CandidateRecord,
	CandidateEvent,
	CandidateTask,
	CandidateDecision,
	CANDIDATE_STAGE_LABELS,
	COMMUNICATION_OUTCOME_LABELS,
	CommunicationRecord,
	CommunicationTemplate,
	MessageTemplateUsage,
	FAQEntry,
	HIRING_DECISION_LABELS,
	JobProfile,
	KnowledgeDocument,
	MismatchFeedback,
	OptimizationDraft,
	OPTIMIZATION_DRAFT_STATUS_LABELS,
	PrivateDomainContact,
	PRIVATE_DOMAIN_CONTACT_STATUS_LABELS,
	InterviewInvite,
	RecruitingCriteria,
	JOB_STATUS_LABELS,
	normalise_knowledge_audience,
	utc_now_iso,
)
from boss_agent_cli.recruiting.knowledge import parse_knowledge_file
from boss_agent_cli.recruiting.context import DEFAULT_RECRUITING_CONTEXT, RecruitingContext
from boss_agent_cli.recruiting.resume_text_reader import ResumeTextReadError, read_resume_text
from boss_agent_cli.recruiting.screening import extract_candidate_profile

_STATE_VERSION = 3
_DEFAULT_STATE: dict[str, Any] = {
	"version": _STATE_VERSION,
	"jobs": {},
	"knowledge": {},
	"faq": {},
	"candidates": {},
	"candidate_events": {},
	"candidate_answers": {},
	"candidate_tasks": {},
	"communications": {},
	"message_template_usages": {},
	"message_templates": {},
	"daily_snapshots": {},
	# 候选人问题只保存短文本和命中来源，用于 FAQ 需求复盘，不保存平台原始会话。
	"question_demands": {},
	"private_contacts": {},
	"interview_invites": {},
	"candidate_decisions": {},
	"assessments": {},
	"optimization_drafts": {},
	# 旧版状态没有这些键，_read_state 会自动补齐，保证升级无需手工迁移。
	"candidate_jobs": {},
	# 候选人可以同时绑定多个岗位。全局 candidates.stage 继续保留用于旧
	# 调用方兼容，但工作台按岗位读取这个映射作为阶段权威来源。
	"candidate_job_states": {},
	"mismatch_feedback": {},
}

# 阶段完成后的下一步只生成本地待办。target_stage 是人工确认完成后要写入的
# 本地阶段，不代表系统会替用户发送消息或执行平台动作。
_STAGE_FOLLOW_UPS: dict[str, tuple[str, str, str, str, tuple[str, ...]]] = {
	"greeted": ("confirm_basic", "记录基础意向", "在 BOSS 沟通页人工确认城市、薪资和工作节奏", "basic_passed", ()),
	"basic_confirming": ("complete_basic", "完成基础条件确认", "记录人工确认结果后继续专业问答", "basic_passed", ()),
	"basic_passed": ("start_professional_qa", "发起专业问答", "使用岗位问题人工记录候选人回答", "professional_qa", ()),
	"professional_passed": ("prepare_resume_exchange", "准备交换简历", "人工确认后在平台完成简历交换", "resume_exchanged", ()),
	"resume_exchanged": ("review_resume", "完成简历评估", "生成本地综合评估并完成人工确认", "resume_passed", ()),
	"resume_passed": ("continue_conversation", "在 BOSS 沟通页继续沟通", "完成第 1 轮人工沟通后记录结果；连续四轮都有本地时间线。", "private_domain_pending", ()),
	"private_domain_pending": ("record_private_contact", "记录私域联系结果", "在官方页面完成加私域后记录渠道和结果", "private_domain_added", ()),
	"private_domain_added": ("prepare_interview", "准备面试邀约", "先确认面试方式和时间，再记录正式邀约", "interview_pending", ()),
	"interview_pending": ("schedule_interview", "记录面试邀约", "在官方页面完成邀约后记录时间和面试官", "interview_scheduled", ()),
	"interview_scheduled": ("record_interview", "记录面试结果", "面试完成后填写阶段备注和结论", "interview_completed", ()),
	"interview_completed": (
		"record_hiring_decision",
		"记录录用决定",
		"人工确认录用、淘汰或暂缓，并填写原因",
		"hired",
		("hired", "rejected", "paused"),
	),
}
_PRIVATE_DOMAIN_CHANNELS = {"wechat", "phone", "email", "other"}
_INTERVIEW_OUTCOMES = {"passed", "failed", "cancelled"}
_PRIVATE_PROFESSIONAL_QA_OUTCOMES = {"passed", "follow_up"}
_ANSWER_CHANNELS = {"boss", "private_domain"}
_COMMUNICATION_MAX_ROUNDS = 4
_TERMINAL_STAGES = {"hired", "rejected", "paused"}
_COMMUNICATION_TEXT_LIMITS = {
	"candidate_reply_summary": 2_000,
	"note": 1_000,
	"next_follow_up_at": 160,
}


def _search_terms(query: str) -> list[str]:
	"""把中文短句拆成有限关键词；不引入外部向量服务，保证本地可复现。"""
	normalised = re.sub(r"\s+", "", query.casefold())
	if not normalised:
		return []
	terms = [normalised]
	if len(normalised) > 1 and any("\u4e00" <= char <= "\u9fff" for char in normalised):
		terms.extend(char for char in normalised if "\u4e00" <= char <= "\u9fff")
	return list(dict.fromkeys(terms))


def _normalise_question_key(query: str) -> str:
	"""生成问题聚合键；仅折叠大小写、空白和标点，不做语义猜测。"""
	return re.sub(r"[\W_]+", "", query.casefold(), flags=re.UNICODE)


def _search_snippet(text: str, query: str, limit: int = 220) -> str:
	"""截取命中附近的一小段事实，避免检索结果复制整篇知识正文。"""
	clean = re.sub(r"\s+", " ", text).strip()
	if len(clean) <= limit:
		return clean
	needle = query.strip()
	position = clean.casefold().find(needle.casefold()) if needle else -1
	start = max(0, position - 60) if position >= 0 else 0
	return clean[start : start + limit].rstrip() + ("…" if start + limit < len(clean) else "")


def _rank_text(query: str, title: str, content: str) -> int:
	"""按短语和标题命中次数给本地文本排序；分数只用于相对排序。"""
	terms = _search_terms(query)
	if not terms:
		return 0
	normalised_title = re.sub(r"\s+", "", title.casefold())
	normalised_content = re.sub(r"\s+", "", content.casefold())
	return sum(normalised_title.count(term) * 3 + normalised_content.count(term) for term in terms)


def _audience_matches(actual: str, requested: str | None, *, category: str) -> bool:
	"""判断知识范围是否可供当前用途读取，shared 对两侧都可见。"""
	if requested is None:
		return True
	clean_requested = normalise_knowledge_audience(requested, category=category)
	clean_actual = normalise_knowledge_audience(actual, category=category)
	return clean_actual in {clean_requested, "shared"}


class RecruitingStoreError(RuntimeError):
	"""工作台存储损坏或写入失败时的安全错误。"""


def _new_id(prefix: str) -> str:
	"""生成不可预测但不含个人信息的本地记录 ID。"""
	return f"{prefix}-{secrets.token_hex(8)}"


def _safe_name_from_resume(text: str, path: Path) -> str:
	"""从已导出的 Markdown 标题或姓名字段提取候选人显示名。"""
	for line in text.splitlines():
		stripped = line.strip()
		if stripped.startswith("# 候选人简历"):
			continue
		match = re.match(r"^[-*]?\s*姓名\s*[：:]\s*(.+)$", stripped)
		if match:
			return match.group(1).strip() or "未命名候选人"
		if stripped.startswith("# "):
			candidate = stripped[2:].strip()
			if candidate and "简历" not in candidate:
				return candidate
	return path.stem.split("-")[0].strip() or "未命名候选人"


class RecruitingStore:
	"""岗位、知识库、候选人引用和评估报告的本地存储边界。"""

	def __init__(self, data_dir: Path, *, context: RecruitingContext | None = None) -> None:
		"""初始化岗位状态存储；非默认上下文使用独立目录，避免跨企业串数据。"""
		self._context = context or DEFAULT_RECRUITING_CONTEXT
		self._directory = data_dir / "recruiting"
		for part in self._context.storage_parts:
			self._directory /= "contexts" if part == self._context.storage_parts[0] else part
		self._directory.mkdir(parents=True, exist_ok=True)
		self._path = self._directory / "workspace.json"
		self._lock = RLock()
		if not self._path.exists():
			self._write_state(dict(_DEFAULT_STATE))

	@property
	def context(self) -> RecruitingContext:
		"""返回当前存储所属上下文，供运行时生成安全状态标签。"""
		return self._context

	@property
	def state_path(self) -> Path:
		"""返回工作区 JSON 路径，便于诊断和隔离回归测试。"""
		return self._path

	@staticmethod
	def _migrate_state(state: dict[str, Any], source_version: int) -> bool:
		"""把旧版状态升级到当前状态机版本，并返回是否需要持久化。

		版本 2 修复了首轮沟通待办的阶段语义：早期实现把沟通待办的目标
		阶段写成 ``private_domain_added``，用户尚未记录私域结果时页面却会
		显示“完成后进入已加私域”。当前状态机要求沟通只进入
		``private_domain_pending``，私域结果必须通过专用记录接口确认。
		迁移只改待办元数据，不重写候选人阶段或历史审计事件，因此不会把
		已经发生的外部事实伪造成新的事实。

		版本 3 增加 ``candidate_job_states``。单岗位旧记录可以安全继承全局
		阶段；多岗位记录只按明确带岗位的事件恢复，没有证据的岗位回到待筛选，
		避免把一个岗位的推进猜给另一个岗位。
		"""
		if source_version >= _STATE_VERSION:
			return False
		changed = True
		candidate_tasks = state.get("candidate_tasks")
		if isinstance(candidate_tasks, dict):
			for raw_task in candidate_tasks.values():
				if not isinstance(raw_task, dict):
					continue
				if (
					raw_task.get("kind") == "continue_conversation"
					and raw_task.get("target_stage") == "private_domain_added"
				):
					raw_task["target_stage"] = "private_domain_pending"
		if source_version < 3:
			candidate_job_states = state.setdefault("candidate_job_states", {})
			candidates = state.get("candidates")
			candidate_jobs = state.get("candidate_jobs")
			events = state.get("candidate_events")
			if isinstance(candidate_job_states, dict) and isinstance(candidates, dict) and isinstance(candidate_jobs, dict):
				for candidate_id, raw_candidate in candidates.items():
					if not isinstance(raw_candidate, dict):
						continue
					raw_job_ids = candidate_jobs.get(candidate_id, [])
					job_ids = [str(value).strip() for value in raw_job_ids if str(value).strip()] if isinstance(raw_job_ids, list) else []
					for job_id in job_ids:
						key = f"{candidate_id}:{job_id}"
						if key in candidate_job_states:
							continue
						stage = "pending_screening"
						stage_updated_at = str(raw_candidate.get("imported_at") or "")
						last_action = ""
						if isinstance(events, dict):
							matching_events = [
								(index, event)
								for index, event in enumerate(events.values())
								if isinstance(event, dict)
								and event.get("candidate_id") == candidate_id
								and str(event.get("job_id") or "") == job_id
							]
							if matching_events:
								# 时间戳精度可能只有秒；用状态字典的插入顺序作为第二排序键，
								# 确保同一秒内的迁移不会错误选择更早的阶段事件。
								_, latest_event = max(
									matching_events,
									key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
								)
								stage = str(latest_event.get("stage") or stage)
								stage_updated_at = str(latest_event.get("created_at") or stage_updated_at)
								last_action = str(latest_event.get("action") or "")
							elif len(job_ids) == 1:
								stage = str(raw_candidate.get("stage") or stage)
								stage_updated_at = str(raw_candidate.get("stage_updated_at") or stage_updated_at)
								last_action = str(raw_candidate.get("last_action") or "")
						candidate_job_states[key] = {
							"candidate_id": str(candidate_id),
							"job_id": job_id,
							"stage": stage if stage in CANDIDATE_STAGE_LABELS else "pending_screening",
							"stage_updated_at": stage_updated_at,
							"last_action": last_action,
						}
		state["version"] = _STATE_VERSION
		return changed

	def _read_state(self) -> dict[str, Any]:
		"""读取并校验顶层 JSON；损坏时抛领域错误而不是静默覆盖。"""
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			raise RecruitingStoreError("招聘工作区读取失败，请检查本地数据目录") from exc
		if not isinstance(raw, dict):
			raise RecruitingStoreError("招聘工作区格式无效")
		try:
			source_version = int(raw.get("version", 1))
		except (TypeError, ValueError):
			# 缺失或损坏的版本号按最早版本处理，优先执行兼容迁移，
			# 避免把旧状态当成当前版本而继续暴露错误阶段文案。
			source_version = 1
		state = dict(_DEFAULT_STATE)
		state.update(raw)
		for key in _DEFAULT_STATE:
			if key not in state:
				state[key] = _DEFAULT_STATE[key]
		if self._migrate_state(state, source_version):
			# 迁移在读取边界一次性持久化，确保页面刷新、进程重启和其他
			# CLI 入口看到的是同一版本，不会每次继续展示旧的阶段文案。
			self._write_state(state)
		return state

	def _write_state(self, state: dict[str, Any]) -> None:
		"""原子写入状态，临时文件始终和目标位于同一目录。"""
		tmp = self._directory / f".workspace.{os.getpid()}.{secrets.token_hex(4)}.tmp"
		try:
			with tmp.open("w", encoding="utf-8", newline="\n") as handle:
				handle.write(json.dumps(state, ensure_ascii=False, indent=2))
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(tmp, self._path)
		except OSError as exc:
			try:
				tmp.unlink(missing_ok=True)
			except OSError:
				pass
			raise RecruitingStoreError("招聘工作区写入失败，请检查本地数据目录") from exc

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
		criteria: RecruitingCriteria | None = None,
		professional_qa_enabled: bool = True,
		greeting_message: str = "",
		status: str = "published",
		job_id: str | None = None,
	) -> JobProfile:
		"""创建岗位；每个岗位拥有独立标准和独立知识库关联。

		结构化字段在 Store 边界再次清洗，防止未来新增 CLI/API 调用方绕过
		Workspace 的输入限制写入负数年限或空技能项。
		"""
		clean_name = name.strip()
		if not clean_name:
			raise ValueError("岗位名称不能为空")
		if min_experience_years is not None and min_experience_years < 0:
			raise ValueError("最低工作年限不能为负数")
		if status not in JOB_STATUS_LABELS:
			raise ValueError("岗位状态不受支持")
		if not isinstance(professional_qa_enabled, bool):
			raise ValueError("专业问答开关必须是布尔值")
		clean_skills = [item.strip() for item in (skills or []) if item.strip()]
		job = JobProfile(
			job_id=job_id or _new_id("job"),
			name=clean_name,
			city=city.strip(),
			salary_range=salary_range.strip(),
			education_requirement=education_requirement.strip(),
			min_experience_years=min_experience_years,
			industry=industry.strip(),
			skills=clean_skills,
			criteria=criteria or RecruitingCriteria(),
			professional_qa_enabled=professional_qa_enabled,
			greeting_message=greeting_message.strip()[:100],
			status=status,
		)
		with self._lock:
			state = self._read_state()
			state["jobs"][job.job_id] = job.to_dict()
			self._write_state(state)
		return job

	def set_job_status(self, job_id: str, status: str) -> JobProfile:
		"""记录岗位状态变更；发布和归档都必须由本地 HR 明确触发。"""
		if status not in JOB_STATUS_LABELS:
			raise ValueError("岗位状态不受支持")
		with self._lock:
			state = self._read_state()
			raw = state["jobs"].get(job_id)
			if not isinstance(raw, dict):
				raise KeyError(job_id)
			job = JobProfile.from_dict(raw)
			job.status = status
			job.status_updated_at = utc_now_iso()
			job.updated_at = job.status_updated_at
			state["jobs"][job_id] = job.to_dict()
			self._write_state(state)
		return job

	def list_jobs(self) -> list[JobProfile]:
		"""按更新时间倒序返回岗位。"""
		with self._lock:
			state = self._read_state()
			items = [JobProfile.from_dict(value) for value in state["jobs"].values()]
		return sorted(items, key=lambda item: item.updated_at, reverse=True)

	def get_job(self, job_id: str) -> JobProfile | None:
		"""读取单个岗位，不存在时返回 None。"""
		with self._lock:
			raw = self._read_state()["jobs"].get(job_id)
		return JobProfile.from_dict(raw) if raw is not None else None

	def update_job(self, job: JobProfile) -> JobProfile:
		"""保存岗位修改并更新时间戳。"""
		if not job.name.strip():
			raise ValueError("岗位名称不能为空")
		job.updated_at = utc_now_iso()
		with self._lock:
			state = self._read_state()
			if job.job_id not in state["jobs"]:
				raise KeyError(job.job_id)
			state["jobs"][job.job_id] = job.to_dict()
			self._write_state(state)
		return job

	def add_knowledge(
		self,
		job_id: str,
		*,
		category: str,
		title: str,
		content: str,
		audience: str = "",
		source_type: str = "manual",
		source_path: str = "",
		source_sha256: str = "",
	) -> KnowledgeDocument:
		"""为岗位增加企业销售/业务知识；正文只保存在本机。

		``audience`` 决定这条事实能否用于候选人试答。销售资料默认内部，
		只有 HR 明确选择 candidate/shared 才会进入对外回答边界。
		"""
		if self.get_job(job_id) is None:
			raise KeyError(job_id)
		if category not in {"sales", "company"}:
			raise ValueError("知识库类别必须是 sales 或 company")
		clean_audience = normalise_knowledge_audience(audience, category=category)
		if not title.strip() or not content.strip():
			raise ValueError("知识标题和正文不能为空")
		document = KnowledgeDocument(
			_new_id("kb"), job_id, category, title.strip(), content.strip(),
			source_type=source_type.strip() or "manual",
			source_path=source_path.strip(),
			source_sha256=source_sha256.strip(),
			audience=clean_audience,
		)
		with self._lock:
			state = self._read_state()
			state["knowledge"][document.document_id] = document.to_dict()
			self._write_state(state)
		return document

	def import_knowledge(
		self, job_id: str, file_path: Path, *, category: str, audience: str = ""
	) -> KnowledgeDocument:
		"""解析并保存一份岗位知识文件，解析失败时不写入工作区。"""
		if self.get_job(job_id) is None:
			raise KeyError(job_id)
		if category not in {"sales", "company"}:
			raise ValueError("知识库类别必须是 sales 或 company")
		clean_audience = normalise_knowledge_audience(audience, category=category)
		parsed = parse_knowledge_file(file_path)
		with self._lock:
			state = self._read_state()
			for raw in state["knowledge"].values():
				if (
					isinstance(raw, dict)
					and raw.get("job_id") == job_id
					and raw.get("source_path") == parsed.source_path
					and raw.get("source_sha256") == parsed.source_sha256
				):
					return KnowledgeDocument.from_dict(raw)
			document = KnowledgeDocument(
				_new_id("kb"),
				job_id,
				category,
				parsed.title,
				parsed.content,
				source_type=parsed.source_type,
				source_path=parsed.source_path,
				source_sha256=parsed.source_sha256,
				audience=clean_audience,
			)
			state["knowledge"][document.document_id] = document.to_dict()
			self._write_state(state)
		return document

	def list_knowledge(self, job_id: str, *, audience: str | None = None) -> list[KnowledgeDocument]:
		"""列出岗位知识文档，不返回其他岗位的内容。"""
		clean_audience = None
		if audience is not None:
			clean_audience = normalise_knowledge_audience(audience, category="company")
		with self._lock:
			items = [
				KnowledgeDocument.from_dict(value)
				for value in self._read_state()["knowledge"].values()
				if isinstance(value, dict)
				and value.get("job_id") == job_id
				and _audience_matches(
					str(value.get("audience") or ""),
					clean_audience,
					category=str(value.get("category") or "company"),
				)
			]
		return sorted(items, key=lambda item: item.updated_at, reverse=True)

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
	) -> FAQEntry:
		"""为岗位保存一个事实受控的候选人常见问题答案。"""
		if self.get_job(job_id) is None:
			raise KeyError(job_id)
		if not question.strip() or not answer.strip():
			raise ValueError("FAQ 问题和答案不能为空")
		clean_audience = normalise_knowledge_audience(audience, category="company")
		faq = FAQEntry(
			_new_id("faq"),
			job_id,
			question.strip(),
			answer.strip(),
			allowed_variation.strip(),
			source_document_id=source_document_id.strip(),
			source_title=source_title.strip(),
			source_version=source_version.strip(),
			review_status="approved",
			audience=clean_audience,
		)
		with self._lock:
			state = self._read_state()
			state["faq"][faq.faq_id] = faq.to_dict()
			self._write_state(state)
		return faq

	def list_faq(self, job_id: str, *, audience: str | None = None) -> list[FAQEntry]:
		"""列出岗位 FAQ。"""
		clean_audience = None
		if audience is not None:
			clean_audience = normalise_knowledge_audience(audience, category="company")
		with self._lock:
			items = [
				FAQEntry.from_dict(value)
				for value in self._read_state()["faq"].values()
				if isinstance(value, dict)
				and value.get("job_id") == job_id
				and _audience_matches(
					str(value.get("audience") or "candidate"),
					clean_audience,
					category="company",
				)
			]
		return sorted(items, key=lambda item: item.updated_at, reverse=True)

	def search_knowledge(
		self, job_id: str, query: str, *, limit: int = 8, audience: str | None = None
	) -> list[dict[str, Any]]:
		"""检索当前岗位知识文档，返回带来源和短摘录的事实证据。"""
		clean_query = query.strip()
		if not clean_query:
			return []
		items = self.list_knowledge(job_id, audience=audience)
		hits: list[dict[str, Any]] = []
		for document in items:
			score = _rank_text(clean_query, document.title, document.content)
			if score <= 0:
				continue
			hits.append(
				{
					"kind": "knowledge",
					"source_type": "knowledge",
					"source_id": document.document_id,
					"source_title": document.title,
					"title": document.title,
					"category": document.category,
					"source_file_type": document.source_type,
					"source_path": document.source_path,
					"source_sha256": document.source_sha256,
					"audience": document.audience,
					"snippet": _search_snippet(document.content, clean_query),
					"score": score,
				}
			)
		return sorted(hits, key=lambda item: (-int(item["score"]), str(item["source_title"])))[: max(1, limit)]

	def search_faq(
		self, job_id: str, query: str, *, limit: int = 8, audience: str | None = None
	) -> list[dict[str, Any]]:
		"""检索当前岗位 FAQ，答案以短摘录返回并保留问题作为引用标题。"""
		clean_query = query.strip()
		if not clean_query:
			return []
		items = self.list_faq(job_id, audience=audience)
		hits: list[dict[str, Any]] = []
		for entry in items:
			score = _rank_text(clean_query, entry.question, entry.answer)
			if score <= 0:
				continue
			hits.append(
				{
					"kind": "faq",
					"source_type": "faq",
					"source_id": entry.faq_id,
					"source_title": entry.question,
					"question": entry.question,
					"snippet": _search_snippet(entry.answer, clean_query),
					"audience": entry.audience,
					"score": score,
				}
			)
		return sorted(hits, key=lambda item: (-int(item["score"]), str(item["source_title"])))[: max(1, limit)]

	def record_question_demand(
		self,
		job_id: str,
		*,
		query: str,
		status: str,
		source_type: str = "",
		source_id: str = "",
		source_title: str = "",
	) -> dict[str, str]:
		"""记录一次候选人问题试答需求，限定在当前岗位上下文。"""
		clean_query = query.strip()
		if self.get_job(job_id) is None:
			raise KeyError(job_id)
		if not clean_query:
			raise ValueError("候选人问题不能为空")
		if len(clean_query) > 500:
			raise ValueError("候选人问题过长")
		clean_status = status.strip() or "unknown"
		if clean_status not in {"answered", "no_source"}:
			clean_status = "unknown"
		record = {
			"demand_id": _new_id("question-demand"),
			"job_id": job_id,
			"query": clean_query,
			"normalized_query": _normalise_question_key(clean_query),
			"status": clean_status,
			"source_type": source_type.strip(),
			"source_id": source_id.strip(),
			"source_title": source_title.strip(),
			"created_at": utc_now_iso(),
		}
		with self._lock:
			state = self._read_state()
			state["question_demands"][record["demand_id"]] = record
			self._write_state(state)
		return record

	def list_question_demands(self, job_id: str | None = None) -> list[dict[str, str]]:
		"""读取候选人问题需求，按岗位过滤并按最近记录倒序返回。"""
		with self._lock:
			items = [
				{str(key): str(value) for key, value in raw.items()}
				for raw in self._read_state()["question_demands"].values()
				if isinstance(raw, dict)
				and (job_id is None or raw.get("job_id") == job_id)
			]
		return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

	def import_candidate(
		self,
		file_path: Path,
		*,
		source: str = "local_markdown",
		job_id: str | None = None,
	) -> CandidateRecord:
		"""导入本地 PDF、Markdown 或 TXT 简历引用，不把正文写入状态。"""
		path = file_path.expanduser().resolve()
		try:
			text = read_resume_text(path)
		except ResumeTextReadError as exc:
			raise ValueError(str(exc)) from exc
		candidate_id = f"candidate-{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]}"
		candidate = CandidateRecord(
			candidate_id=candidate_id,
			name=_safe_name_from_resume(text, path),
			resume_path=str(path),
			source=source,
			resume_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
			profile=extract_candidate_profile(text).to_dict(),
		)
		with self._lock:
			state = self._read_state()
			if job_id is not None and job_id not in state["jobs"]:
				raise KeyError(job_id)
			existing = state["candidates"].get(candidate_id)
			if isinstance(existing, dict):
				# 同一份简历重复导入时更新哈希和姓名，但保留阶段与历史，避免
				# 用户每次刷新下载结果都把候选人退回“待筛选”。
				existing_candidate = CandidateRecord.from_dict(existing)
				candidate.stage = existing_candidate.stage
				candidate.stage_updated_at = existing_candidate.stage_updated_at
				candidate.last_action = existing_candidate.last_action
			state["candidates"][candidate.candidate_id] = candidate.to_dict()
			if not isinstance(existing, dict):
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate.candidate_id,
					job_id=job_id or "",
					stage=candidate.stage,
					action="导入候选人",
					actor="system",
					note=f"来源：{source}",
				)
				state["candidate_events"][event.event_id] = event.to_dict()
				initial_task = CandidateTask(
					task_id=_new_id("task"),
					candidate_id=candidate.candidate_id,
					job_id=job_id or "",
					kind="assess_candidate",
					title="选择岗位并生成简历评估",
					description="选择目标岗位后生成本地评估，结果需要 HR 人工确认。",
				)
				state["candidate_tasks"][initial_task.task_id] = initial_task.to_dict()
			if job_id is not None:
				linked_jobs = state["candidate_jobs"].setdefault(candidate.candidate_id, [])
				if not isinstance(linked_jobs, list):
					linked_jobs = []
					state["candidate_jobs"][candidate.candidate_id] = linked_jobs
				if job_id not in linked_jobs:
					linked_jobs.append(job_id)
					# 同一份简历绑定新岗位时，必须为新岗位建立独立
					# 评估入口；否则候选人虽出现在列表里，工作流却没有
					# 任何可执行动作。
					self._ensure_task_in_state(
						state,
						candidate_id=candidate.candidate_id,
						job_id=job_id,
						kind="assess_candidate",
						title="选择岗位并生成简历评估",
						 description="选择目标岗位后生成本地评估，结果需要 HR 人工确认。",
					)
				# 岗位绑定完成后再初始化岗位级阶段；新岗位不能继承候选人
				# 在其他岗位上的全局兼容阶段。这里候选人刚导入，故明确关闭
				# 旧阶段继承，避免同一份简历重新绑定岗位时串阶段。
				self._ensure_candidate_job_state_in_state(state, candidate, job_id=job_id)
			self._write_state(state)
		return candidate

	def update_candidate_name(self, candidate_id: str, name: str) -> CandidateRecord:
		"""更新候选人展示姓名，保留其余简历引用、阶段和岗位关联。

		本地分析索引有时比 PDF 文件名更能提供姓名。该方法只更新白名单中的展示
		字段，不复制简历正文，也不改动任何岗位评估或外部平台事实。
		"""
		clean_candidate_id = candidate_id.strip()
		clean_name = name.strip()
		if not clean_candidate_id or not clean_name:
			raise ValueError("候选人标识和姓名不能为空")
		if len(clean_name) > 120:
			raise ValueError("候选人姓名过长")
		with self._lock:
			state = self._read_state()
			raw = state["candidates"].get(clean_candidate_id)
			if not isinstance(raw, dict):
				raise KeyError(clean_candidate_id)
			candidate = CandidateRecord.from_dict(raw)
			candidate.name = clean_name
			state["candidates"][clean_candidate_id] = candidate.to_dict()
			self._write_state(state)
		return candidate

	def link_candidate_to_job(self, candidate_id: str, job_id: str) -> None:
		"""把已有简历引用绑定到岗位，后续快照只在绑定岗位展示。"""
		with self._lock:
			state = self._read_state()
			if candidate_id not in state["candidates"]:
				raise KeyError(candidate_id)
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			candidate = CandidateRecord.from_dict(state["candidates"][candidate_id])
			self._bind_candidate_to_job_in_state(state, candidate, job_id=job_id, inherit_legacy_stage=True)
			self._write_state(state)

	def list_candidates(self, job_id: str | None = None) -> list[CandidateRecord]:
		"""返回候选人元数据；指定岗位时只返回该岗位绑定的候选人。

		未绑定岗位的旧记录仍会在指定岗位中显示，保证升级后的历史简历不会
		消失；一旦用户明确绑定，它就不再跨岗位展示。
		"""
		with self._lock:
			state = self._read_state()
			items = []
			for value in state["candidates"].values():
				if not isinstance(value, dict):
					continue
				candidate_id = str(value.get("candidate_id") or "")
				linked_jobs = state["candidate_jobs"].get(candidate_id)
				if job_id is not None and isinstance(linked_jobs, list) and linked_jobs and job_id not in linked_jobs:
					continue
				items.append(CandidateRecord.from_dict(value))
		return sorted(items, key=lambda item: item.imported_at, reverse=True)

	def list_candidate_job_ids(self, candidate_id: str) -> list[str]:
		"""返回候选人已绑定岗位，供工作台构建上下文和调试数据隔离。"""
		with self._lock:
			raw = self._read_state()["candidate_jobs"].get(candidate_id, [])
		return [str(job_id) for job_id in raw if isinstance(job_id, str)] if isinstance(raw, list) else []

	def get_candidate(self, candidate_id: str) -> CandidateRecord | None:
		"""读取候选人引用。"""
		with self._lock:
			raw = self._read_state()["candidates"].get(candidate_id)
		return CandidateRecord.from_dict(raw) if raw is not None else None

	def list_candidate_tasks(
		self,
		candidate_id: str | None = None,
		*,
		job_id: str | None = None,
		status: str | None = None,
	) -> list[CandidateTask]:
		"""按创建时间倒序返回待办，可按候选人、岗位和状态筛选。"""
		with self._lock:
			raw_tasks = self._read_state()["candidate_tasks"].values()
			items = [
				CandidateTask.from_dict(value)
				for value in raw_tasks
				if isinstance(value, dict)
				and (candidate_id is None or value.get("candidate_id") == candidate_id)
				and (job_id is None or value.get("job_id") == job_id)
				and (status is None or value.get("status") == status)
			]
		return sorted(items, key=lambda item: item.created_at, reverse=True)

	def get_task(self, task_id: str) -> CandidateTask | None:
		"""读取单个待办，不存在时返回 None。"""
		with self._lock:
			raw = self._read_state()["candidate_tasks"].get(task_id)
		return CandidateTask.from_dict(raw) if raw is not None else None

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
		"""原子保存一轮沟通，并生成下一轮人工待跟进任务。

		沟通只记录 HR 已在 BOSS 页面完成的事实，不会发送消息、读取平台原始
		聊天正文或执行加私域动作。轮次必须连续，四轮后未达成意向会进入暂缓，
		避免工作台无限生成无效待办。
		"""
		clean_outcome = outcome.strip().lower()
		clean_summary = candidate_reply_summary.strip()
		clean_note = note.strip()
		clean_follow_up = next_follow_up_at.strip()
		if round_number < 1 or round_number > _COMMUNICATION_MAX_ROUNDS:
			raise ValueError("沟通轮次只能是 1 到 4")
		if clean_outcome not in COMMUNICATION_OUTCOME_LABELS:
			raise ValueError("沟通结果不受支持")
		if round_number < _COMMUNICATION_MAX_ROUNDS and clean_outcome in {"follow_up", "no_response"} and not clean_follow_up:
			raise ValueError("待跟进或未回复必须填写下一次跟进时间")
		for field_name, value, limit in (
			("候选人回复摘要", clean_summary, _COMMUNICATION_TEXT_LIMITS["candidate_reply_summary"]),
			("沟通备注", clean_note, _COMMUNICATION_TEXT_LIMITS["note"]),
			("下一次跟进时间", clean_follow_up, _COMMUNICATION_TEXT_LIMITS["next_follow_up_at"]),
		):
			if len(value) > limit:
				raise ValueError(f"{field_name}过长，最多支持 {limit} 个字符")

		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			candidate = CandidateRecord.from_dict(raw_candidate)
			existing_records = [
				CommunicationRecord.from_dict(raw)
				for raw in state["communications"].values()
				if isinstance(raw, dict) and raw.get("job_id") == job_id and raw.get("candidate_id") == candidate_id
			]
			expected_round = max((record.round_number for record in existing_records), default=0) + 1
			if round_number != expected_round:
				raise ValueError(f"请按顺序记录第 {expected_round} 轮沟通")
			pending_task_raw = next(
				(
					raw
					for raw in state["candidate_tasks"].values()
					if isinstance(raw, dict)
					and raw.get("candidate_id") == candidate_id
					and raw.get("job_id") == job_id
					and raw.get("status") == "pending"
					and raw.get("kind") in {"continue_conversation", "communication_round", "communication_follow_up"}
					and int(raw.get("communication_round") or round_number) == round_number
				),
				None,
			)
			if pending_task_raw is None:
				raise ValueError("当前没有待处理的沟通轮次，请刷新工作区后重试")
			communication = CommunicationRecord(
				communication_id=_new_id("communication"),
				job_id=job_id,
				candidate_id=candidate_id,
				round_number=round_number,
				outcome=clean_outcome,
				candidate_reply_summary=clean_summary,
				note=clean_note,
					next_follow_up_at=clean_follow_up,
				template_key=template_key.strip(),
				template_version=template_version.strip(),
			)
			state["communications"][communication.communication_id] = communication.to_dict()
			now = communication.updated_at
			pending_task = CandidateTask.from_dict(pending_task_raw)
			pending_task.status = "completed"
			pending_task.note = clean_note or clean_summary
			pending_task.updated_at = now
			pending_task.completed_at = now
			state["candidate_tasks"][pending_task.task_id] = pending_task.to_dict()

			if clean_outcome == "declined":
				new_stage = "rejected"
			elif round_number == _COMMUNICATION_MAX_ROUNDS and clean_outcome != "qualified":
				new_stage = "paused"
			else:
				# 沟通期间保留“待加私域”阶段，只有 HR 后续记录私域结果才继续推进。
				new_stage = "private_domain_pending"
			last_action = f"第 {round_number} 轮沟通：{COMMUNICATION_OUTCOME_LABELS[clean_outcome]}"
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=job_id,
				stage=new_stage,
				updated_at=now,
				last_action=last_action,
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=job_id,
				stage=new_stage,
				action=last_action,
				actor="hr",
				note=clean_note,
				ai_judgment=COMMUNICATION_OUTCOME_LABELS[clean_outcome],
			)
			state["candidate_events"][event.event_id] = event.to_dict()
			if new_stage in _TERMINAL_STAGES:
				# 明确拒绝和四轮未通过都会直接进入终局，必须同步留下决定
				# 记录；否则漏斗只看到“淘汰/暂缓”，却没有可复盘的原因。
				self._record_terminal_decision_in_state(
					state,
					job_id=job_id,
					candidate_id=candidate_id,
					outcome="rejected" if new_stage == "rejected" else "paused",
					reason=clean_note or clean_summary or COMMUNICATION_OUTCOME_LABELS[clean_outcome],
				)

			if clean_outcome == "qualified":
				self._ensure_task_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					kind="record_private_contact",
					title="记录私域联系结果",
					description="在官方页面完成加私域后记录渠道和结果；系统不会代替添加。",
					target_stage="private_domain_added",
				)
			elif clean_outcome not in {"declined"} and round_number < _COMMUNICATION_MAX_ROUNDS:
				next_round = round_number + 1
				self._ensure_task_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					kind="communication_round",
					title=f"第 {next_round} 轮沟通",
					description="在 BOSS 沟通页人工完成下一轮跟进后，记录候选人回复摘要和结果。",
					target_stage="private_domain_pending",
					communication_round=next_round,
					due_at=clean_follow_up,
				)
			self._write_state(state)
		return {"communication": communication.to_dict(), "candidate": candidate.to_dict()}

	def list_communications(
		self, job_id: str | None = None, candidate_id: str | None = None,
	) -> list[dict[str, Any]]:
		"""按时间升序返回指定岗位或候选人的沟通摘要。"""
		with self._lock:
			records = [
				CommunicationRecord.from_dict(raw).to_dict()
				for raw in self._read_state()["communications"].values()
				if isinstance(raw, dict)
				and (job_id is None or raw.get("job_id") == job_id)
				and (candidate_id is None or raw.get("candidate_id") == candidate_id)
			]
		return sorted(records, key=lambda item: (str(item["created_at"]), int(item["round_number"])))

	def record_message_template_usage(
		self,
		job_id: str,
		*,
		candidate_id: str = "",
		template_key: str,
		template_version: str = "v1",
		note: str = "",
	) -> dict[str, str]:
		"""记录 HR 已人工使用的话术版本，不发送、不调用平台接口。"""
		if self.get_job(job_id) is None:
			raise KeyError(job_id)
		clean_key = template_key.strip()
		clean_version = template_version.strip() or "v1"
		if not clean_key:
			raise ValueError("话术标识不能为空")
		if len(clean_key) > 128 or len(clean_version) > 64 or len(note.strip()) > 1000:
			raise ValueError("话术使用记录过长")
		usage = MessageTemplateUsage(
			usage_id=_new_id("template-usage"),
			job_id=job_id,
			candidate_id=candidate_id.strip(),
			template_key=clean_key,
			template_version=clean_version,
			note=note.strip(),
		)
		with self._lock:
			state = self._read_state()
			state["message_template_usages"][usage.usage_id] = usage.to_dict()
			self._write_state(state)
		return usage.to_dict()

	def list_message_template_usages(
		self, job_id: str | None = None, candidate_id: str | None = None,
	) -> list[dict[str, str]]:
		"""按时间倒序返回话术使用元数据，供页面与复盘读取。"""
		with self._lock:
			items = [
				MessageTemplateUsage.from_dict(raw).to_dict()
				for raw in self._read_state()["message_template_usages"].values()
				if isinstance(raw, dict)
				and (job_id is None or raw.get("job_id") == job_id)
				and (candidate_id is None or raw.get("candidate_id") == candidate_id)
			]
		return sorted(items, key=lambda item: str(item["used_at"]), reverse=True)

	# ------------------------------------------------------------------
	# CommunicationTemplate CRUD
	# ------------------------------------------------------------------

	def save_template(self, template: "CommunicationTemplate") -> dict[str, str]:
		"""创建或更新话术模板（按 template_id 幂等）。"""
		from boss_agent_cli.recruiting.models import CommunicationTemplate as CT

		clean = CT(
			template_id=template.template_id or _new_id("tmpl"),
			job_id=template.job_id.strip(),
			template_key=template.template_key.strip(),
			title=template.title.strip(),
			body=template.body.strip(),
			category=template.category.strip() or "greeting",
			version=template.version.strip() or "v1",
			created_at=template.created_at or utc_now_iso(),
			updated_at=utc_now_iso(),
		)
		if not clean.template_key or not clean.body:
			raise ValueError("话术模板必须有标识和正文")
		if len(clean.template_key) > 128 or len(clean.body) > 5000:
			raise ValueError("话术模板字段过长")
		with self._lock:
			state = self._read_state()
			state.setdefault("message_templates", {})
			state["message_templates"][clean.template_id] = clean.to_dict()
			self._write_state(state)
		return clean.to_dict()

	def get_template(self, template_id: str) -> dict[str, str] | None:
		"""读取单个话术模板，用于编辑回填和页面展示。"""
		with self._lock:
			templates = self._read_state().get("message_templates", {})
			raw = templates.get(template_id.strip())
			return dict(raw) if isinstance(raw, dict) else None

	def list_templates(self, job_id: str | None = None) -> list[dict[str, str]]:
		"""列出话术模板，可按岗位过滤；按更新时间倒序。"""
		with self._lock:
			items = [
				dict(raw) for raw in self._read_state().get("message_templates", {}).values()
				if isinstance(raw, dict)
				and (job_id is None or raw.get("job_id") == job_id)
			]
		return sorted(items, key=lambda item: str(item.get("updated_at", "")), reverse=True)

	def delete_template(self, template_id: str) -> bool:
		"""删除话术模板；返回是否实际删除了记录。"""
		with self._lock:
			state = self._read_state()
			templates = state.get("message_templates", {})
			existed = template_id.strip() in templates
			if existed:
				del templates[template_id.strip()]
				state["message_templates"] = templates
				self._write_state(state)
			return existed

	# ------------------------------------------------------------------
	# Daily snapshot (time-series metrics)
	# ------------------------------------------------------------------

	def save_daily_snapshot(self, metrics: dict[str, Any]) -> str:
		"""保存当日指标快照（幂等：同一日期只保留最后一次）。

		调用方负责聚合指标；这里只做轻量校验和持久化。
		"""
		today = utc_now_iso()[:10]  # YYYY-MM-DD
		snapshot_id = f"daily-{today}"
		entry = {
			"snapshot_id": snapshot_id,
			"date": today,
			"recorded_at": utc_now_iso(),
			"metrics": dict(metrics),
		}
		if any(len(str(v)) > 10_000 for v in entry["metrics"].values()):
			raise ValueError("指标值过长")
		with self._lock:
			state = self._read_state()
			state.setdefault("daily_snapshots", {})
			state["daily_snapshots"][snapshot_id] = entry
			self._write_state(state)
		return snapshot_id

	def get_daily_snapshots(self, *, days: int = 30) -> list[dict[str, Any]]:
		"""返回最近 N 天的指标快照，按日期升序。"""
		with self._lock:
			all_snapshots = list(self._read_state().get("daily_snapshots", {}).values())
		all_snapshots.sort(key=lambda s: str(s.get("date", "")))
		return all_snapshots[-days:]

	def _ensure_task_in_state(
		self,
		state: dict[str, Any],
		*,
		candidate_id: str,
		kind: str,
		title: str,
		description: str,
		job_id: str = "",
		target_stage: str = "",
		allowed_target_stages: tuple[str, ...] = (),
		communication_round: int = 0,
		due_at: str = "",
	) -> CandidateTask:
		"""在已有锁内复用同一候选人的待处理任务，避免刷新产生重复待办。"""
		for raw in state["candidate_tasks"].values():
			if not isinstance(raw, dict):
				continue
			if (
				raw.get("candidate_id") == candidate_id
				and (not job_id or raw.get("job_id", "") in {"", job_id})
				and raw.get("kind") == kind
				and raw.get("status") == "pending"
			):
				return CandidateTask.from_dict(raw)
		task = CandidateTask(
			task_id=_new_id("task"),
			candidate_id=candidate_id,
			job_id=job_id,
			kind=kind,
			title=title,
			description=description,
				target_stage=target_stage,
				allowed_target_stages=list(allowed_target_stages),
				communication_round=communication_round,
				due_at=due_at,
		)
		state["candidate_tasks"][task.task_id] = task.to_dict()
		return task

	def _ensure_stage_follow_up_in_state(
		self, state: dict[str, Any], *, candidate_id: str, stage: str, job_id: str = "",
	) -> CandidateTask | None:
		"""为可继续推进的阶段生成一个唯一待办；终止阶段不生成后续动作。"""
		follow_up = _STAGE_FOLLOW_UPS.get(stage)
		if follow_up is None:
			return None
		if stage == "basic_passed" and job_id:
			# 岗位关闭 BOSS 专业问答时，不能把核验直接跳到交换简历。
			# 先建立一个只记录私域人工核验的结构化待办，后续由专用接口
			# 写入问题、回答、来源和结论，再解锁平台外部的简历交换动作。
			raw_job = state["jobs"].get(job_id)
			qa_enabled = True
			if isinstance(raw_job, dict) and isinstance(raw_job.get("professional_qa_enabled"), bool):
				qa_enabled = bool(raw_job["professional_qa_enabled"])
			if not qa_enabled:
				follow_up = (
					"private_professional_qa",
					"记录私域专业核验",
					"岗位未启用 BOSS 专业问答；请在私域人工完成专业核验并记录问题、回答和结论",
					"professional_passed",
					(),
				)
		kind, title, description, target_stage, allowed_target_stages = follow_up
		return self._ensure_task_in_state(
			state,
			candidate_id=candidate_id,
			job_id=job_id,
			kind=kind,
			title=title,
			description=description,
			target_stage=target_stage,
			allowed_target_stages=allowed_target_stages,
		)

	@staticmethod
	def _candidate_job_ids_in_state(state: dict[str, Any], candidate_id: str) -> list[str]:
		"""读取候选人绑定的有效岗位 ID，统一处理旧状态的缺失或脏值。"""
		raw_job_ids = state["candidate_jobs"].get(candidate_id, [])
		if not isinstance(raw_job_ids, list):
			return []
		return [str(value).strip() for value in raw_job_ids if str(value).strip()]

	@classmethod
	def _event_belongs_to_job_in_state(
		cls, state: dict[str, Any], raw_event: dict[str, Any], *, job_id: str,
	) -> bool:
		"""判断审计事件是否属于当前岗位，并为旧事件提供有边界的兼容。"""
		event_job_id = str(raw_event.get("job_id") or "").strip()
		if event_job_id:
			return event_job_id == job_id
		# 版本升级前的事件没有岗位字段。只有候选人没有绑定岗位，或只绑定
		# 当前这一个岗位时才能安全回读；一旦存在多个岗位，空值无法推断，
		# 宁可不把历史事实复用到任一岗位，也不能让基础意向跨岗位放行。
		linked_jobs = cls._candidate_job_ids_in_state(state, str(raw_event.get("candidate_id") or ""))
		return len(linked_jobs) <= 1 and (not linked_jobs or linked_jobs[0] == job_id)

	@classmethod
	def _candidate_job_state_in_state(
		cls, state: dict[str, Any], candidate_id: str, job_id: str | None,
	) -> dict[str, str]:
		"""在已有状态快照中读取候选人的岗位级阶段。

		新版本优先使用显式映射；旧记录没有映射时，只有带当前岗位的最新
		事件或唯一岗位绑定可以安全回退。多岗位且证据不足时返回待筛选，
		这是“宁可要求重新确认，也不跨岗推进”的关键边界。
		"""
		raw_candidate = state.get("candidates", {}).get(candidate_id)
		fallback = {
			"candidate_id": candidate_id,
			"job_id": job_id or "",
			"stage": "pending_screening",
			"stage_updated_at": "",
			"last_action": "",
		}
		if not isinstance(raw_candidate, dict):
			return fallback
		if not job_id:
			return {
				"candidate_id": candidate_id,
				"job_id": "",
				"stage": str(raw_candidate.get("stage") or "pending_screening"),
				"stage_updated_at": str(raw_candidate.get("stage_updated_at") or ""),
				"last_action": str(raw_candidate.get("last_action") or ""),
			}
		key = f"{candidate_id}:{job_id}"
		raw_state = state.get("candidate_job_states", {}).get(key)
		if isinstance(raw_state, dict):
			stage = str(raw_state.get("stage") or "pending_screening")
			return {
				"candidate_id": candidate_id,
				"job_id": job_id,
				"stage": stage if stage in CANDIDATE_STAGE_LABELS else "pending_screening",
				"stage_updated_at": str(raw_state.get("stage_updated_at") or ""),
				"last_action": str(raw_state.get("last_action") or ""),
			}
		events = state.get("candidate_events", {})
		if isinstance(events, dict):
			matching_events = [
				(index, event)
				for index, event in enumerate(events.values())
				if isinstance(event, dict)
				and event.get("candidate_id") == candidate_id
				and cls._event_belongs_to_job_in_state(state, event, job_id=job_id)
			]
			if matching_events:
				# utc_now_iso 在部分运行环境只保留到秒；索引保证同秒连续操作
				# 仍按审计写入顺序恢复，而不是把候选人退回最早阶段。
				_, latest_event = max(
					matching_events,
					key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
				)
				stage = str(latest_event.get("stage") or "pending_screening")
				return {
					"candidate_id": candidate_id,
					"job_id": job_id,
					"stage": stage if stage in CANDIDATE_STAGE_LABELS else "pending_screening",
					"stage_updated_at": str(latest_event.get("created_at") or ""),
					"last_action": str(latest_event.get("action") or ""),
				}
		linked_jobs = cls._candidate_job_ids_in_state(state, candidate_id)
		if len(linked_jobs) <= 1 and (not linked_jobs or linked_jobs[0] == job_id):
			return {
				"candidate_id": candidate_id,
				"job_id": job_id,
				"stage": str(raw_candidate.get("stage") or "pending_screening"),
				"stage_updated_at": str(raw_candidate.get("stage_updated_at") or ""),
				"last_action": str(raw_candidate.get("last_action") or ""),
			}
		return fallback

	@classmethod
	def _set_candidate_job_state_in_state(
		cls,
		state: dict[str, Any],
		candidate: CandidateRecord,
		*,
		job_id: str,
		stage: str,
		updated_at: str,
		last_action: str,
	) -> None:
		"""同时写岗位级状态和兼容用的全局候选人字段。

		全局字段保留是为了让旧 CLI 继续得到有意义的返回值；页面快照和
		后续门禁使用岗位级映射，因此多个岗位之间不会再相互覆盖。
		"""
		if stage not in CANDIDATE_STAGE_LABELS:
			raise ValueError("候选人阶段不受支持")
		candidate.stage = stage
		candidate.stage_updated_at = updated_at
		candidate.last_action = last_action
		state["candidates"][candidate.candidate_id] = candidate.to_dict()
		if job_id:
			candidate_job_states = state.setdefault("candidate_job_states", {})
			candidate_job_states[f"{candidate.candidate_id}:{job_id}"] = {
				"candidate_id": candidate.candidate_id,
				"job_id": job_id,
				"stage": stage,
				"stage_updated_at": updated_at,
				"last_action": last_action,
			}

	@classmethod
	def _ensure_candidate_job_state_in_state(
		cls, state: dict[str, Any], candidate: CandidateRecord, *, job_id: str,
	) -> None:
		"""为新绑定岗位建立待筛选状态，重复调用保持幂等。"""
		if not job_id:
			return
		states = state.setdefault("candidate_job_states", {})
		key = f"{candidate.candidate_id}:{job_id}"
		if key not in states:
			states[key] = {
				"candidate_id": candidate.candidate_id,
				"job_id": job_id,
				"stage": "pending_screening",
				"stage_updated_at": candidate.imported_at,
				"last_action": "",
			}

	@classmethod
	def _bind_candidate_to_job_in_state(
		cls,
		state: dict[str, Any],
		candidate: CandidateRecord,
		*,
		job_id: str,
		inherit_legacy_stage: bool = False,
	) -> None:
		"""在已有锁内绑定岗位，并按边界迁移旧的全局阶段。

		旧版 CLI 允许候选人先被导入、再以无岗位参数记录阶段，最后才由面试
		或评估接口提供岗位 ID。只有候选人尚未绑定任何岗位时，才能把这条旧
		阶段安全继承到第一次明确的岗位；已经绑定过其他岗位时绝不猜测归属。
		该规则集中在 Store，避免导入、面试和未来 Web 入口各自实现不同迁移。
		"""
		if not job_id:
			return
		linked_jobs = state["candidate_jobs"].setdefault(candidate.candidate_id, [])
		if not isinstance(linked_jobs, list):
			linked_jobs = []
			state["candidate_jobs"][candidate.candidate_id] = linked_jobs
		had_no_jobs = not linked_jobs
		if job_id not in linked_jobs:
			linked_jobs.append(job_id)
		cls._ensure_candidate_job_state_in_state(state, candidate, job_id=job_id)
		if not (inherit_legacy_stage and had_no_jobs):
			return
		stage = candidate.stage if candidate.stage in CANDIDATE_STAGE_LABELS else "pending_screening"
		raw_state = state["candidate_job_states"].get(f"{candidate.candidate_id}:{job_id}")
		if isinstance(raw_state, dict):
			raw_state.update(
				{
					"stage": stage,
					"stage_updated_at": candidate.stage_updated_at or candidate.imported_at,
					"last_action": candidate.last_action,
				}
			)

	def get_candidate_job_state(self, candidate_id: str, job_id: str) -> dict[str, str]:
		"""读取指定岗位下候选人的阶段投影，供工作台避免串岗展示。"""
		with self._lock:
			return self._candidate_job_state_in_state(self._read_state(), candidate_id, job_id)

	@classmethod
	def _has_basic_intent_event_in_state(
		cls, state: dict[str, Any], candidate_id: str, *, job_id: str,
	) -> bool:
		"""检查候选人是否已经留下基础意向通过事件。

		基础意向是评估门禁要求的人工事实，不能只看候选人当前阶段：旧数据
		可能被手工回填到后续阶段，却没有对应审计事件。事件结构允许旧版本
		缺少岗位字段，因此通过统一的岗位归属兼容规则读取，避免历史单岗位
		数据失效，同时阻断多岗位候选人的跨岗位复用。
		"""
		return any(
			isinstance(raw, dict)
			and raw.get("candidate_id") == candidate_id
			and cls._event_belongs_to_job_in_state(state, raw, job_id=job_id)
			and raw.get("stage") == "basic_passed"
			and raw.get("action") in {"基础意向人工确认", "基础条件人工确认"}
			for raw in state["candidate_events"].values()
		)

	def _ensure_basic_intent_task_in_state(
		self, state: dict[str, Any], *, candidate_id: str, job_id: str,
	) -> CandidateTask | None:
		"""为尚未确认基础意向的早期候选人创建唯一待办。

		评估报告的门禁需要基础意向事件，但新导入候选人过去只有“人工确认
		评估”入口，导致用户只能通过隐藏的阶段下拉或强制放行继续。把待办
		在评估保存时补齐，既让 UI 有明确入口，也兼容评估前已经手工回填的
		候选人；后续所有页面刷新都由 ``_ensure_task_in_state`` 保证幂等。
		"""
		raw_candidate = state["candidates"].get(candidate_id)
		if not isinstance(raw_candidate, dict) or self._has_basic_intent_event_in_state(
			state, candidate_id, job_id=job_id,
		):
			return None
		stage = self._candidate_job_state_in_state(state, candidate_id, job_id).get("stage", "pending_screening")
		if stage not in {"pending_screening", "initial_pass", "greeted", "basic_confirming", "basic_passed"}:
			return None
		return self._ensure_task_in_state(
			state,
			candidate_id=candidate_id,
			job_id=job_id,
			kind="confirm_basic",
			title="记录基础意向",
			description="在 BOSS 沟通页确认城市、薪资和工作节奏后回填人工结果。",
			target_stage="basic_passed",
		)

	def _complete_pending_tasks_in_state(
		self,
		state: dict[str, Any],
		*,
		candidate_id: str,
		job_id: str,
		kinds: set[str],
		note: str,
	) -> None:
		"""在新评估或人工确认完成时关闭已经被当前操作替代的旧待办。"""
		now = utc_now_iso()
		for task_id, raw in list(state["candidate_tasks"].items()):
			if not isinstance(raw, dict):
				continue
			if (
				raw.get("candidate_id") == candidate_id
				and (not job_id or raw.get("job_id", "") in {"", job_id})
				and raw.get("kind") in kinds
				and raw.get("status") == "pending"
			):
				updated = dict(raw)
				updated.update({"status": "completed", "status_label": "已完成", "note": note, "updated_at": now, "completed_at": now})
				state["candidate_tasks"][task_id] = updated

	def _close_all_pending_tasks_in_state(
		self,
		state: dict[str, Any],
		*,
		candidate_id: str,
		job_id: str,
		note: str,
	) -> None:
		"""在终局决定落盘时关闭同一岗位的全部遗留待办。"""
		now = utc_now_iso()
		for task_id, raw in list(state["candidate_tasks"].items()):
			if not isinstance(raw, dict):
				continue
			if (
				raw.get("candidate_id") == candidate_id
				and (not job_id or raw.get("job_id", "") in {"", job_id})
				and raw.get("status") == "pending"
			):
				updated = dict(raw)
				updated.update({"status": "completed", "status_label": "已完成", "note": note, "updated_at": now, "completed_at": now})
				state["candidate_tasks"][task_id] = updated

	def _record_terminal_decision_in_state(
		self,
		state: dict[str, Any],
		*,
		job_id: str,
		candidate_id: str,
		outcome: str,
		reason: str,
	) -> CandidateDecision:
		"""在已有存储锁内写入唯一终局决定，补齐所有终局入口的审计链。

		终局可能来自人工评估拒绝、沟通明确拒绝或私域未添加，不一定经过
		``record_hiring_decision`` 待办。统一从这里创建决定记录，保证漏斗、
		复盘和页面的“流程已完成”不会只依赖候选人阶段字段。
		"""
		if outcome not in HIRING_DECISION_LABELS:
			raise ValueError("终局决定不受支持")
		for raw in state["candidate_decisions"].values():
			if (
				isinstance(raw, dict)
				and raw.get("job_id") == job_id
				and raw.get("candidate_id") == candidate_id
			):
				# 旧版状态可能在决定写入后又留下同岗位待办；即使决定记录
				# 已存在，也要重复执行清理，避免刷新后重新指向历史动作。
				self._close_all_pending_tasks_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					note=f"终局决定：{HIRING_DECISION_LABELS[outcome]}",
				)
				return CandidateDecision.from_dict(raw)
		decision = CandidateDecision(
			decision_id=_new_id("decision"),
			job_id=job_id,
			candidate_id=candidate_id,
			outcome=outcome,
			reason=reason.strip(),
		)
		state["candidate_decisions"][decision.decision_id] = decision.to_dict()
		# 终局一旦确认，旧评估、沟通或面试待办都失去推进意义；统一关闭，
		# 防止工作流刷新后又指向已经结束的历史动作。
		self._close_all_pending_tasks_in_state(
			state,
			candidate_id=candidate_id,
			job_id=job_id,
			note=f"终局决定：{HIRING_DECISION_LABELS[outcome]}",
		)
		return decision

	def complete_task(
		self,
		task_id: str,
		*,
		status: str = "completed",
		note: str = "",
		target_stage: str | None = None,
	) -> dict[str, Any]:
		"""人工完成或跳过待办，并在完成时原子推进候选人阶段。

		终局待办允许 HR 明确选择录用、淘汰或暂缓；默认阶段只作为兼容旧数据的
		兜底，不允许通过接口写入任意阶段或绕过状态机。
		"""
		if status not in {"completed", "skipped", "pending"}:
			raise ValueError("待办状态必须是 completed、skipped 或 pending")
		clean_note = note.strip()
		if len(clean_note) > 1_000:
			raise ValueError("待办备注过长，最多支持 1000 个字符")
		with self._lock:
			state = self._read_state()
			raw_task = state["candidate_tasks"].get(task_id)
			if not isinstance(raw_task, dict):
				raise KeyError(task_id)
			task = CandidateTask.from_dict(raw_task)
			# 评估和人工确认都有专用接口：前者需要读取简历正文并保存报告，
			# 后者需要明确的 proceed/follow_up/reject 结果。禁止普通待办接口
			# 直接推进，避免旧页面的“标记完成”把关键人工决策静默跳过。
			if task.status == "pending" and task.kind == "review_assessment":
				raise ValueError("人工确认待办必须通过人工确认接口完成")
			if status == "completed" and task.status == "pending" and task.kind in {
				"continue_conversation",
				"communication_round",
				"communication_follow_up",
				"record_private_contact",
				"schedule_interview",
				"record_interview",
			}:
				# 这些待办对应外部页面发生过的事实，必须由专用接口写入
				# 沟通轮次、私域联系、面试记录等审计对象。通用“标记完成”
				# 只能用于准备动作，否则候选人会进入下一阶段但缺少证据，
				# 下次刷新还可能出现无法继续的孤儿状态。
				raise ValueError("沟通、私域和面试待办必须通过对应记录接口完成")
			if task.status == "pending" and task.kind in {
				"assess_candidate",
				"reassess_candidate",
				"start_professional_qa",
				"review_resume",
			}:
				raise ValueError("评估待办必须通过评估接口完成")
			candidate_raw = state["candidates"].get(task.candidate_id)
			if not isinstance(candidate_raw, dict):
				raise KeyError(task.candidate_id)
			candidate = CandidateRecord.from_dict(candidate_raw)
			if status == "pending":
				# 恢复只允许作用于已跳过待办；它是可审计的撤销动作，
				# 不改变候选人阶段，也不会创建重复任务。
				if task.status != "skipped":
					raise ValueError("只有已跳过的待办可以恢复")
				now = utc_now_iso()
				task.status = "pending"
				task.note = clean_note
				task.updated_at = now
				task.completed_at = ""
				state["candidate_tasks"][task.task_id] = task.to_dict()
				self._write_state(state)
				return {"task": task.to_dict(), "candidate": candidate.to_dict()}
			if task.status == "pending":
				current_job_state = self._candidate_job_state_in_state(state, candidate.candidate_id, task.job_id or None)
				selected_stage = (target_stage or task.target_stage or current_job_state.get("stage") or candidate.stage).strip()
				allowed_stages = set(task.allowed_target_stages)
				if task.kind == "record_hiring_decision":
					allowed_stages.update(HIRING_DECISION_LABELS)
				if allowed_stages and selected_stage not in allowed_stages:
					raise ValueError("终局待办只能选择录用、淘汰或暂缓")
				if selected_stage not in CANDIDATE_STAGE_LABELS:
					raise ValueError("目标阶段不受支持")
				if task.kind == "record_hiring_decision" and selected_stage == "hired":
					# 录用是本地流程的最终承诺，必须同时具备私域已添加和
					# 面试通过两条事实；淘汰/暂缓仍允许 HR 根据业务原因结束。
					# 只看候选人当前 stage 不够，因为旧数据可能手工回填过
					# interview_completed，却没有保存实际面试结论。
					workflow_job_id = task.job_id
					private_added = any(
						isinstance(raw_contact, dict)
						and raw_contact.get("candidate_id") == candidate.candidate_id
						and raw_contact.get("status") == "added"
						and raw_contact.get("job_id") in {"", workflow_job_id}
						for raw_contact in state["private_contacts"].values()
					)
					if not private_added:
						raise ValueError("录用前必须记录私域已添加")
					latest_interview = next(
						(
							InterviewInvite.from_dict(raw_invite)
							for raw_invite in sorted(
								state["interview_invites"].values(),
								key=lambda value: str(value.get("updated_at") or value.get("created_at") or ""),
								reverse=True,
							)
							if isinstance(raw_invite, dict)
							and raw_invite.get("candidate_id") == candidate.candidate_id
							and raw_invite.get("job_id") == workflow_job_id
						),
						None,
					)
					if latest_interview is None or latest_interview.outcome != "passed":
						raise ValueError("面试未通过或尚未记录面试通过结果，不能录用")
				now = utc_now_iso()
				task.status = status
				task.note = clean_note
				task.updated_at = now
				task.completed_at = now
				state["candidate_tasks"][task.task_id] = task.to_dict()
				if status == "completed":
					self._set_candidate_job_state_in_state(
						state,
						candidate,
						job_id=task.job_id,
						stage=selected_stage,
						updated_at=now,
						last_action=task.title,
					)
					event = CandidateEvent(
						event_id=_new_id("event"),
						candidate_id=candidate.candidate_id,
						job_id=task.job_id,
						stage=selected_stage,
						action=task.title,
						actor="hr",
						note=clean_note,
					)
					state["candidate_events"][event.event_id] = event.to_dict()
					if task.kind == "record_hiring_decision":
						self._record_terminal_decision_in_state(
							state,
							job_id=task.job_id,
							candidate_id=candidate.candidate_id,
							outcome=selected_stage,
							reason=clean_note,
						)
					self._ensure_stage_follow_up_in_state(
						state,
						candidate_id=candidate.candidate_id,
						stage=selected_stage,
						job_id=task.job_id,
					)
				state["candidates"][candidate.candidate_id] = candidate.to_dict()
			self._write_state(state)
		return {
			"task": task.to_dict(),
			"candidate": candidate.to_dict(),
		}

	def transition_candidate(
		self,
		candidate_id: str,
		*,
		job_id: str | None = None,
		stage: str,
		action: str,
		actor: str = "hr",
		note: str = "",
		ai_judgment: str = "",
		candidate_quote: str = "",
	) -> dict[str, dict[str, str]]:
		"""原子更新候选人阶段并写入一条本地审计事件。"""
		clean_stage = stage.strip()
		if clean_stage not in CANDIDATE_STAGE_LABELS:
			raise ValueError("候选人阶段不受支持")
		if clean_stage in _TERMINAL_STAGES:
			# 录用、淘汰和暂缓必须由 ``record_hiring_decision`` 待办写入，
			# 因为该入口会同时保存 CandidateDecision。普通阶段记录只负责
			# 审计人工事实，不能制造“页面已终局但没有决定记录”的断链状态。
			raise ValueError("终局阶段必须通过终局待办完成")
		clean_action = action.strip()
		if not clean_action:
			raise ValueError("阶段动作不能为空")
		for field_name, value, limit in (
			("动作", clean_action, 160),
			("角色", actor.strip(), 32),
			("备注", note.strip(), 1_000),
			("AI 判断", ai_judgment.strip(), 1_000),
			("候选人原话", candidate_quote.strip(), 2_000),
		):
			if len(value) > limit:
				raise ValueError(f"{field_name}过长，最多支持 {limit} 个字符")
		with self._lock:
			state = self._read_state()
			raw = state["candidates"].get(candidate_id)
			if not isinstance(raw, dict):
				raise KeyError(candidate_id)
			clean_job_id = job_id.strip() if job_id else ""
			if clean_job_id and clean_job_id not in state["jobs"]:
				raise KeyError(clean_job_id)
			linked_jobs = state["candidate_jobs"].setdefault(candidate_id, [])
			if clean_job_id and clean_job_id not in linked_jobs:
				linked_jobs.append(clean_job_id)
			# 旧 CLI/测试调用没有岗位参数时，只有一个绑定岗位才安全推断；
			# 多岗位候选人必须由 Web/新调用方明确传入当前岗位，避免待办串岗。
			if len(linked_jobs) > 1 and not clean_job_id:
				raise ValueError("多岗位候选人必须指定岗位")
			workflow_job_id = clean_job_id or (str(linked_jobs[0]) if len(linked_jobs) == 1 else "")
			candidate = CandidateRecord.from_dict(raw)
			now = utc_now_iso()
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=workflow_job_id,
				stage=clean_stage,
				updated_at=now,
				last_action=clean_action,
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=workflow_job_id,
				stage=clean_stage,
				action=clean_action,
				actor=actor.strip() or "hr",
				note=note.strip(),
				ai_judgment=ai_judgment.strip(),
				candidate_quote=candidate_quote.strip(),
			)
			state["candidate_events"][event.event_id] = event.to_dict()
			if clean_stage != "pending_screening":
				# 手工回填已经明确候选人越过了初筛或评估阶段，
				# 同一岗位的旧入口待办不应继续抢占工作流的下一步。
				self._complete_pending_tasks_in_state(
					state,
					candidate_id=candidate_id,
					job_id=workflow_job_id,
					kinds={
						"assess_candidate",
						"reassess_candidate",
						"start_professional_qa",
						"review_resume",
						"review_assessment",
						"confirm_basic",
						"complete_basic",
					},
					note="人工回填阶段，前置待办已关闭",
				)
			self._ensure_stage_follow_up_in_state(
				state,
				candidate_id=candidate_id,
				stage=clean_stage,
				job_id=workflow_job_id,
			)
			self._write_state(state)
		return {"candidate": candidate.to_dict(), "event": event.to_dict()}

	def confirm_basic_intent(
		self,
		job_id: str,
		candidate_id: str,
		*,
		note: str,
	) -> dict[str, dict[str, str]]:
		"""记录基础意向确认事件并把候选人推进到专业问答阶段。

		基础意向是“愿意继续了解、城市/薪资/节奏可接受”的人工事实，不能
		从评估分数或候选人当前阶段推断。因此使用独立方法和固定动作名称，
		让评估门禁、前端按钮和审计检索共享同一语义。
		"""
		clean_note = note.strip()
		if not clean_note:
			raise ValueError("基础意向确认备注不能为空")
		if len(clean_note) > 1_000:
			raise ValueError("基础意向确认备注过长，最多支持 1000 个字符")
		with self._lock:
			state = self._read_state()
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			candidate_stage = self._candidate_job_state_in_state(state, candidate_id, job_id).get("stage", "pending_screening")
			if candidate_stage in _TERMINAL_STAGES:
				raise ValueError("终局候选人不能重复记录基础意向")
			if candidate_stage not in {"pending_screening", "initial_pass", "greeted", "basic_confirming", "basic_passed"}:
				raise ValueError("候选人已进入后续阶段，不能重复记录基础意向")
			linked_jobs = state["candidate_jobs"].setdefault(candidate_id, [])
			if job_id not in linked_jobs:
				linked_jobs.append(job_id)
			candidate = CandidateRecord.from_dict(raw_candidate)
			now = utc_now_iso()
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=job_id,
				stage="basic_passed",
				updated_at=now,
				last_action="基础意向人工确认",
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=job_id,
				stage="basic_passed",
				action="基础意向人工确认",
				actor="hr",
				note=clean_note,
				ai_judgment="基础意向已由 HR 确认",
			)
			state["candidate_events"][event.event_id] = event.to_dict()
			self._complete_pending_tasks_in_state(
				state, candidate_id=candidate_id, job_id=job_id, kinds={"confirm_basic", "complete_basic"}, note=clean_note,
			)
			self._ensure_stage_follow_up_in_state(
				state, candidate_id=candidate_id, stage="basic_passed", job_id=job_id,
			)
			self._write_state(state)
		return {"candidate": candidate.to_dict(), "event": event.to_dict()}

	def record_private_contact(
		self,
		candidate_id: str,
		*,
		job_id: str | None = None,
		channel: str,
		status: str,
		note: str = "",
	) -> dict[str, Any]:
		"""保存 HR 已经确认的私域联系结果，并推进本地阶段。

		本方法只记录事实，不读取或写入微信号等联系方式，也不会调用 BOSS 或
		其他平台的添加接口。``status=pending`` 只保存跟进记录；``added``
		才会把候选人推进到已加私域，``declined`` 则进入暂缓并停止自动待办。
		"""
		clean_channel = channel.strip().lower()
		clean_status = status.strip().lower()
		clean_note = note.strip()
		if clean_channel not in _PRIVATE_DOMAIN_CHANNELS:
			raise ValueError("私域渠道不受支持")
		if clean_status not in PRIVATE_DOMAIN_CONTACT_STATUS_LABELS:
			raise ValueError("私域联系状态不受支持")
		if len(clean_note) > 1_000:
			raise ValueError("私域备注过长，最多支持 1000 个字符")
		with self._lock:
			state = self._read_state()
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			candidate = CandidateRecord.from_dict(raw_candidate)
			clean_job_id = job_id.strip() if job_id else ""
			if clean_job_id and clean_job_id not in state["jobs"]:
				raise KeyError(clean_job_id)
			linked_jobs = state["candidate_jobs"].setdefault(candidate_id, [])
			if clean_job_id and clean_job_id not in linked_jobs:
				linked_jobs.append(clean_job_id)
			contact = PrivateDomainContact(
				contact_id=_new_id("contact"),
				candidate_id=candidate_id,
				channel=clean_channel,
				status=clean_status,
				job_id=clean_job_id,
				note=clean_note,
			)
			state["private_contacts"][contact.contact_id] = contact.to_dict()
			pending_job_ids = {
				str(raw.get("job_id") or "")
				for raw in state["candidate_tasks"].values()
				if isinstance(raw, dict)
				and raw.get("candidate_id") == candidate_id
				and raw.get("status") == "pending"
				and raw.get("kind") in {"continue_conversation", "record_private_contact"}
				and str(raw.get("job_id") or "")
			}
			if clean_job_id:
				workflow_job_id = clean_job_id
			elif len(pending_job_ids) > 1:
				raise ValueError("多岗位候选人必须指定岗位")
			else:
				workflow_job_id = next(iter(pending_job_ids), "")
			if not workflow_job_id:
				# 旧数据的阶段待办可能没有岗位标识，但候选人导入时已经
				# 建立岗位绑定；终局决定仍必须归属到正确岗位。
				if isinstance(linked_jobs, list):
					candidate_job_ids = [str(value) for value in linked_jobs if str(value).strip()]
					if len(candidate_job_ids) > 1:
						raise ValueError("多岗位候选人必须指定岗位")
					workflow_job_id = candidate_job_ids[0] if candidate_job_ids else ""
			if contact.job_id != workflow_job_id:
				contact.job_id = workflow_job_id
				state["private_contacts"][contact.contact_id] = contact.to_dict()
			if clean_status == "added":
				new_stage = "private_domain_added"
				last_action = "记录私域已添加"
				self._set_candidate_job_state_in_state(
					state,
					candidate,
					job_id=workflow_job_id,
					stage=new_stage,
					updated_at=contact.updated_at,
					last_action=last_action,
				)
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate_id,
					job_id=workflow_job_id,
					stage=new_stage,
					action=last_action,
					actor="hr",
					note=clean_note,
				)
				state["candidate_events"][event.event_id] = event.to_dict()
				self._complete_pending_tasks_in_state(
					state,
					candidate_id=candidate_id,
					job_id=workflow_job_id,
					kinds={"continue_conversation", "record_private_contact"},
					note="已记录私域联系结果",
				)
				self._ensure_stage_follow_up_in_state(
					state,
					candidate_id=candidate_id,
					stage=candidate.stage,
					job_id=workflow_job_id,
				)
			elif clean_status == "declined":
				new_stage = "paused"
				last_action = "记录私域暂未添加"
				self._set_candidate_job_state_in_state(
					state,
					candidate,
					job_id=workflow_job_id,
					stage=new_stage,
					updated_at=contact.updated_at,
					last_action=last_action,
				)
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate_id,
					job_id=workflow_job_id,
					stage=new_stage,
					action=last_action,
					actor="hr",
					note=clean_note,
				)
				state["candidate_events"][event.event_id] = event.to_dict()
				self._record_terminal_decision_in_state(
					state,
					job_id=workflow_job_id,
					candidate_id=candidate_id,
					outcome="paused",
					reason=clean_note or "私域联系未完成",
				)
				self._complete_pending_tasks_in_state(
					state,
					candidate_id=candidate_id,
					job_id=workflow_job_id,
					kinds={"continue_conversation", "record_private_contact"},
					note="私域联系未完成，候选人暂缓",
				)
			self._write_state(state)
		return {"contact": contact.to_dict(), "candidate": candidate.to_dict()}

	def list_private_contacts(
		self, candidate_id: str | None = None, *, job_id: str | None = None,
	) -> list[dict[str, str]]:
		"""返回私域联系记录的脱敏元数据，按创建时间倒序排列。

		旧版记录没有岗位标识，因此查询指定岗位时仍保留空标识记录，供
		迁移后的单岗位工作区读取；新写入记录始终带上明确岗位。
		"""
		with self._lock:
			items = [
				PrivateDomainContact.from_dict(value).to_dict()
				for value in self._read_state()["private_contacts"].values()
				if isinstance(value, dict)
				and (candidate_id is None or value.get("candidate_id") == candidate_id)
				and (job_id is None or value.get("job_id") in {None, "", job_id})
			]
		return sorted(items, key=lambda item: item["created_at"], reverse=True)

	def schedule_interview(
		self,
		job_id: str,
		candidate_id: str,
		*,
		scheduled_at: str,
		interviewer: str = "",
		note: str = "",
	) -> dict[str, Any]:
		"""记录 HR 已在官方页面完成的面试邀约，并生成面试结果待办。"""
		clean_time = scheduled_at.strip()
		clean_interviewer = interviewer.strip()
		clean_note = note.strip()
		if not clean_time:
			raise ValueError("面试时间不能为空")
		if len(clean_time) > 160 or len(clean_interviewer) > 160 or len(clean_note) > 1_000:
			raise ValueError("面试记录字段过长")
		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			candidate = CandidateRecord.from_dict(raw_candidate)
			# 旧调用方可能先在无岗位上下文中推进了候选人，再由当前面试
			# 接口提供岗位 ID。此处在读取阶段前恢复唯一旧岗位投影；若候选人
			# 已绑定多个岗位，助手不会继承全局阶段，仍要求明确岗位事实。
			self._bind_candidate_to_job_in_state(state, candidate, job_id=job_id, inherit_legacy_stage=True)
			current_stage = self._candidate_job_state_in_state(state, candidate_id, job_id).get("stage", candidate.stage)
			if current_stage != "interview_pending":
				raise ValueError("候选人当前不在待邀约面试阶段")
			invite = InterviewInvite(
				invite_id=_new_id("interview"),
				job_id=job_id,
				candidate_id=candidate_id,
				scheduled_at=clean_time,
				interviewer=clean_interviewer,
				note=clean_note,
			)
			state["interview_invites"][invite.invite_id] = invite.to_dict()
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=job_id,
				stage="interview_scheduled",
				updated_at=invite.updated_at,
				last_action="记录面试邀约",
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=job_id,
				stage="interview_scheduled",
				action="记录面试邀约",
				actor="hr",
				note=clean_note,
			)
			state["candidate_events"][event.event_id] = event.to_dict()
			self._complete_pending_tasks_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kinds={"schedule_interview"},
				note="已记录面试邀约",
			)
			self._ensure_stage_follow_up_in_state(
				state,
				candidate_id=candidate_id,
				stage="interview_scheduled",
				job_id=job_id,
			)
			self._write_state(state)
		return {"invite": invite.to_dict(), "candidate": candidate.to_dict()}

	def record_interview_result(
		self,
		job_id: str,
		candidate_id: str,
		*,
		outcome: str,
		note: str = "",
	) -> dict[str, Any]:
		"""记录面试结果；取消时回到待邀约，其他结果进入面试完成阶段。"""
		clean_outcome = outcome.strip().lower()
		clean_note = note.strip()
		if clean_outcome not in _INTERVIEW_OUTCOMES:
			raise ValueError("面试结果不受支持")
		if len(clean_note) > 1_000:
			raise ValueError("面试备注过长，最多支持 1000 个字符")
		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			candidate = CandidateRecord.from_dict(raw_candidate)
			matching = [
				InterviewInvite.from_dict(value)
				for value in state["interview_invites"].values()
				if isinstance(value, dict)
				and value.get("job_id") == job_id
				and value.get("candidate_id") == candidate_id
				and value.get("status") == "scheduled"
			]
			if not matching:
				raise ValueError("没有找到待完成的面试邀约")
			invite = sorted(matching, key=lambda item: item.created_at, reverse=True)[0]
			invite.status = "cancelled" if clean_outcome == "cancelled" else "completed"
			invite.outcome = clean_outcome
			invite.note = clean_note
			invite.updated_at = utc_now_iso()
			state["interview_invites"][invite.invite_id] = invite.to_dict()
			if clean_outcome == "cancelled":
				new_stage = "interview_pending"
				last_action = "记录面试取消"
			else:
				new_stage = "interview_completed"
				last_action = "记录面试结果"
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=job_id,
				stage=new_stage,
				updated_at=invite.updated_at,
				last_action=last_action,
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=job_id,
				stage=new_stage,
				action=last_action,
				actor="hr",
				note=clean_note,
			)
			state["candidate_events"][event.event_id] = event.to_dict()
			self._complete_pending_tasks_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kinds={"record_interview"},
				note="已记录面试结果",
			)
			self._ensure_stage_follow_up_in_state(
				state,
				candidate_id=candidate_id,
				stage=new_stage,
				job_id=job_id,
			)
			self._write_state(state)
		return {"invite": invite.to_dict(), "candidate": candidate.to_dict()}

	def list_interview_invites(self, job_id: str | None = None, candidate_id: str | None = None) -> list[dict[str, str]]:
		"""按岗位和候选人筛选面试记录，供漏斗和页面复盘使用。"""
		with self._lock:
			items = [
				InterviewInvite.from_dict(value).to_dict()
				for value in self._read_state()["interview_invites"].values()
				if isinstance(value, dict)
				and (job_id is None or value.get("job_id") == job_id)
				and (candidate_id is None or value.get("candidate_id") == candidate_id)
			]
		return sorted(items, key=lambda item: item["created_at"], reverse=True)

	def list_candidate_decisions(self, job_id: str | None = None, candidate_id: str | None = None) -> list[dict[str, str]]:
		"""返回录用、淘汰或暂缓决定的本地元数据。"""
		with self._lock:
			items = [
				CandidateDecision.from_dict(value).to_dict()
				for value in self._read_state()["candidate_decisions"].values()
				if isinstance(value, dict)
				and (job_id is None or value.get("job_id") == job_id)
				and (candidate_id is None or value.get("candidate_id") == candidate_id)
			]
		return sorted(items, key=lambda item: item["created_at"], reverse=True)

	def record_mismatch_feedback(
		self,
		job_id: str,
		candidate_id: str,
		*,
		reason_code: str,
		stage: str,
		note: str = "",
		source: str = "local",
	) -> dict[str, str | bool]:
		"""保存一次不匹配反馈，仅用于本地复盘，不提交平台。"""
		if not reason_code.strip():
			raise ValueError("不匹配原因不能为空")
		if not stage.strip():
			raise ValueError("不匹配阶段不能为空")
		if len(note.strip()) > 1_000:
			raise ValueError("不匹配备注过长")
		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			if candidate_id not in state["candidates"]:
				raise KeyError(candidate_id)
			feedback = MismatchFeedback(
				feedback_id=_new_id("mismatch"),
				job_id=job_id,
				candidate_id=candidate_id,
				reason_code=reason_code.strip(),
				stage=stage.strip(),
				note=note.strip(),
				source=source.strip() or "local",
			)
			state["mismatch_feedback"][feedback.feedback_id] = feedback.to_dict()
			self._write_state(state)
		return feedback.to_dict()

	def list_mismatch_feedback(
		self,
		job_id: str | None = None,
		candidate_id: str | None = None,
	) -> list[dict[str, str | bool]]:
		"""按岗位或候选人读取不匹配反馈，默认按时间倒序。"""
		with self._lock:
			items = [
				MismatchFeedback.from_dict(value).to_dict()
				for value in self._read_state()["mismatch_feedback"].values()
				if isinstance(value, dict)
				and (job_id is None or value.get("job_id") == job_id)
				and (candidate_id is None or value.get("candidate_id") == candidate_id)
			]
		return sorted(items, key=lambda item: str(item["created_at"]), reverse=True)

	def create_optimization_draft(self, job_id: str, suggestion: Mapping[str, Any]) -> dict[str, str]:
		"""把一条当前复盘建议落成本地待审核草稿，并按建议 ID 幂等复用。

		建议正文来自本地统计投影，Store 只接受有限长度的元数据；同一岗位下同一
		``suggestion_id`` 使用稳定键保存，避免页面重复点击或轮询生成重复草稿。
		创建动作不会修改岗位配置、知识库、FAQ 或任何平台状态。
		"""
		if not isinstance(suggestion, Mapping):
			raise ValueError("复盘建议格式无效")
		suggestion_id = str(suggestion.get("suggestion_id") or "").strip()
		if not suggestion_id or len(suggestion_id) > 128:
			raise ValueError("复盘建议标识无效")
		values = {
			"kind": str(suggestion.get("kind") or "unknown").strip()[:64],
			"severity": str(suggestion.get("severity") or "medium").strip()[:32],
			"title": str(suggestion.get("title") or "").strip()[:200],
			"reason": str(suggestion.get("reason") or "").strip()[:1_000],
			"action": str(suggestion.get("action") or "").strip()[:1_000],
		}
		if not values["title"] or not values["reason"] or not values["action"]:
			raise ValueError("复盘建议缺少标题、原因或建议动作")
		key = f"{job_id}:{suggestion_id}"
		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			existing = state["optimization_drafts"].get(key)
			if isinstance(existing, dict):
				return OptimizationDraft.from_dict(existing).to_dict()
			draft = OptimizationDraft(
				draft_id=_new_id("optimization"),
				job_id=job_id,
				suggestion_id=suggestion_id,
				**values,
			)
			state["optimization_drafts"][key] = draft.to_dict()
			self._write_state(state)
		return draft.to_dict()

	def list_optimization_drafts(self, job_id: str | None = None) -> list[dict[str, str]]:
		"""读取岗位改进草稿，按更新时间倒序返回完整审核轨迹。"""
		with self._lock:
			items = [
				OptimizationDraft.from_dict(value).to_dict()
				for value in self._read_state()["optimization_drafts"].values()
				if isinstance(value, dict) and (job_id is None or value.get("job_id") == job_id)
			]
		return sorted(items, key=lambda item: str(item["updated_at"]), reverse=True)

	def review_optimization_draft(self, draft_id: str, *, status: str, note: str = "") -> dict[str, str]:
		"""记录 HR 对改进草稿的采纳、忽略或重新打开，不执行实际配置变更。"""
		if status not in OPTIMIZATION_DRAFT_STATUS_LABELS:
			raise ValueError("改进草稿状态不受支持")
		clean_note = note.strip()
		if len(clean_note) > 1_000:
			raise ValueError("改进草稿备注过长")
		with self._lock:
			state = self._read_state()
			matching_key = next(
				(
					key
					for key, value in state["optimization_drafts"].items()
					if isinstance(value, dict) and value.get("draft_id") == draft_id
				),
				None,
			)
			if matching_key is None:
				raise KeyError(draft_id)
			draft = OptimizationDraft.from_dict(state["optimization_drafts"][matching_key])
			draft.status = status
			draft.review_note = clean_note
			draft.reviewed_at = utc_now_iso() if status != "pending_review" else ""
			draft.updated_at = utc_now_iso()
			state["optimization_drafts"][matching_key] = draft.to_dict()
			self._write_state(state)
		return draft.to_dict()

	def list_candidate_events(
		self, candidate_id: str, *, job_id: str | None = None,
	) -> list[dict[str, str]]:
		"""按时间升序返回候选人审计事件，并可按岗位隔离读取。

		未传岗位时保留完整候选人时间线，供全局复盘和旧调用方使用；传入
		岗位后只返回明确属于该岗位的事件。旧事件缺少岗位字段时，只有候选人
		没有多岗位绑定才能兼容读取，避免把历史基础意向误用到其他岗位。
		"""
		with self._lock:
			state = self._read_state()
			raw_events = state["candidate_events"].values()
			events = [
				CandidateEvent.from_dict(value).to_dict()
				for value in raw_events
				if isinstance(value, dict)
				and value.get("candidate_id") == candidate_id
				and (
					job_id is None
					or self._event_belongs_to_job_in_state(state, value, job_id=job_id)
				)
			]
		return sorted(events, key=lambda item: item["created_at"])

	def _new_candidate_answer_in_state(
		self,
		state: dict[str, Any],
		job_id: str,
		candidate_id: str,
		*,
		question: str,
		answer: str,
		question_id: str,
		question_version: str,
		source_ids: list[str],
		follow_up_of: str,
		channel: str,
		verification_status: str,
	) -> CandidateAnswer:
		"""在已有存储锁内创建回答记录，供 BOSS 和私域核验共用。

		把版本计算和状态写入集中到一个内部方法，确保私域核验更新候选人阶段
		时不会先写回答、后写阶段而留下半条链路。
		"""
		clean_question = question.strip()
		clean_answer = answer.strip()
		if not clean_question or not clean_answer:
			raise ValueError("问题和回答不能为空")
		if len(clean_question) > 1_000 or len(clean_answer) > 8_000:
			raise ValueError("问题或回答过长")
		clean_question_id = question_id.strip() or f"question-{hashlib.sha256(f'{job_id}|{clean_question}'.encode('utf-8')).hexdigest()[:16]}"
		clean_question_version = question_version.strip() or "v1"
		clean_follow_up_of = follow_up_of.strip()
		clean_channel = channel.strip().lower() or "boss"
		if clean_channel not in _ANSWER_CHANNELS:
			raise ValueError("回答来源渠道不受支持")
		clean_verification_status = verification_status.strip().lower() or "recorded"
		if clean_verification_status not in {"recorded", "passed", "follow_up"}:
			raise ValueError("回答核验结论不受支持")
		clean_source_ids = [str(item).strip() for item in source_ids if str(item).strip()]
		previous_versions = [
			int(value.get("answer_version") or 0)
			for value in state["candidate_answers"].values()
			if isinstance(value, dict)
			and value.get("job_id") == job_id
			and value.get("candidate_id") == candidate_id
			and value.get("question_id") == clean_question_id
		]
		answer_record = CandidateAnswer(
			answer_id=_new_id("answer"),
			job_id=job_id,
			candidate_id=candidate_id,
			question=clean_question,
			answer=clean_answer,
			question_id=clean_question_id,
			question_version=clean_question_version,
			source_ids=clean_source_ids,
			answer_version=max(previous_versions, default=0) + 1,
			follow_up_of=clean_follow_up_of,
			channel=clean_channel,
			verification_status=clean_verification_status,
		)
		state["candidate_answers"][answer_record.answer_id] = answer_record.to_dict()
		return answer_record

	def save_candidate_answer(
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
		"""保存一条回答并返回不含回答正文的元数据。

		``channel`` 和 ``verification_status`` 是私域专业核验的最小结构化
		字段；BOSS 既有回答默认仍使用 ``boss/recorded``，保持旧数据兼容。
		"""
		with self._lock:
			state = self._read_state()
			if job_id not in state["jobs"]:
				raise KeyError(job_id)
			candidate_raw = state["candidates"].get(candidate_id)
			if not isinstance(candidate_raw, dict):
				raise KeyError(candidate_id)
			answer_record = self._new_candidate_answer_in_state(
				state,
				job_id,
				candidate_id,
				question=question,
				answer=answer,
				question_id=question_id,
				question_version=question_version,
				source_ids=list(source_ids or []),
				follow_up_of=follow_up_of,
				channel=channel,
				verification_status=verification_status,
			)
			candidate = CandidateRecord.from_dict(candidate_raw)
			current_stage = self._candidate_job_state_in_state(state, candidate_id, job_id).get("stage", candidate.stage)
			if current_stage in {"pending_screening", "initial_pass", "greeted", "basic_confirming", "basic_passed"}:
				self._set_candidate_job_state_in_state(
					state,
					candidate,
					job_id=job_id,
					stage="professional_qa",
					updated_at=answer_record.created_at,
					last_action="记录专业问答",
				)
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate_id,
					job_id=job_id,
					stage="professional_qa",
					action="记录专业问答",
					actor="hr",
					note=f"已记录第 {self._count_answers_in_state(state, job_id, candidate_id)} 条回答",
				)
				state["candidate_events"][event.event_id] = event.to_dict()
			self._ensure_task_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kind="reassess_candidate",
				title="重新生成综合评估",
				description="回答已保存；重新生成评估后再进行人工确认。",
				target_stage="professional_qa",
			)
			self._write_state(state)
		return {
			"answer_id": answer_record.answer_id,
			"job_id": job_id,
			"candidate_id": candidate_id,
			"question": answer_record.question,
			"answer_length": len(answer_record.answer),
			"created_at": answer_record.created_at,
			"question_id": answer_record.question_id,
			"question_version": answer_record.question_version,
			"source_ids": list(answer_record.source_ids),
			"answer_version": answer_record.answer_version,
			"follow_up_of": answer_record.follow_up_of,
			"channel": answer_record.channel,
			"verification_status": answer_record.verification_status,
		}

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
		"""记录岗位关闭 BOSS 问答后的私域专业核验。

		私域核验仍由 HR 在外部渠道完成；本方法只把人工提供的问题、回答、
		来源版本和结论原子写入本地。``follow_up`` 会保留同一待办等待补充，
		``passed`` 才会推进到“准备交换简历”，避免关闭 BOSS 问答后出现跳步。
		"""
		clean_outcome = outcome.strip().lower()
		if clean_outcome not in _PRIVATE_PROFESSIONAL_QA_OUTCOMES:
			raise ValueError("私域专业核验结果只能是 passed 或 follow_up")
		clean_note = note.strip()
		if len(clean_note) > 1_000:
			raise ValueError("私域专业核验备注过长，最多支持 1000 个字符")
		with self._lock:
			state = self._read_state()
			raw_job = state["jobs"].get(job_id)
			if not isinstance(raw_job, dict):
				raise KeyError(job_id)
			if raw_job.get("professional_qa_enabled") is not False:
				raise ValueError("当前岗位已启用 BOSS 专业问答，不能使用私域核验入口")
			raw_candidate = state["candidates"].get(candidate_id)
			if not isinstance(raw_candidate, dict):
				raise KeyError(candidate_id)
			raw_task = next(
				(
					raw
					for raw in state["candidate_tasks"].values()
					if isinstance(raw, dict)
					and raw.get("candidate_id") == candidate_id
					and raw.get("job_id") == job_id
					and raw.get("kind") == "private_professional_qa"
					and raw.get("status") == "pending"
				),
				None,
			)
			if not isinstance(raw_task, dict):
				raise ValueError("当前没有待处理的私域专业核验，请刷新工作区后重试")
			answer_record = self._new_candidate_answer_in_state(
				state,
				job_id,
				candidate_id,
				question=question,
				answer=answer,
				question_id=question_id,
				question_version=question_version,
				source_ids=list(source_ids or []),
				follow_up_of=follow_up_of,
				channel="private_domain",
				verification_status=clean_outcome,
			)
			candidate = CandidateRecord.from_dict(raw_candidate)
			now = answer_record.created_at
			if clean_outcome == "passed":
				task = CandidateTask.from_dict(raw_task)
				task.status = "completed"
				task.note = clean_note or "私域专业核验通过"
				task.updated_at = now
				task.completed_at = now
				state["candidate_tasks"][task.task_id] = task.to_dict()
				self._set_candidate_job_state_in_state(
					state,
					candidate,
					job_id=job_id,
					stage="professional_passed",
					updated_at=now,
					last_action="私域专业核验通过",
				)
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate_id,
					job_id=job_id,
					stage="professional_passed",
					action="私域专业核验通过",
					actor="hr",
					note=clean_note,
				)
				state["candidate_events"][event.event_id] = event.to_dict()
				self._ensure_stage_follow_up_in_state(
					state,
					candidate_id=candidate_id,
					stage=candidate.stage,
					job_id=job_id,
				)
			else:
				self._set_candidate_job_state_in_state(
					state,
					candidate,
					job_id=job_id,
					stage="professional_qa",
					updated_at=now,
					last_action="私域专业核验待补充",
				)
				raw_task["note"] = clean_note or "需要补充私域专业核验"
				raw_task["updated_at"] = now
				state["candidate_tasks"][str(raw_task.get("task_id") or "")] = raw_task
				event = CandidateEvent(
					event_id=_new_id("event"),
					candidate_id=candidate_id,
					job_id=job_id,
					stage="professional_qa",
					action="私域专业核验待补充",
					actor="hr",
					note=clean_note,
				)
				state["candidate_events"][event.event_id] = event.to_dict()
			self._write_state(state)
		return {
			"answer": {
				"answer_id": answer_record.answer_id,
				"job_id": job_id,
				"candidate_id": candidate_id,
				"question": answer_record.question,
				"answer_length": len(answer_record.answer),
				"created_at": answer_record.created_at,
				"question_id": answer_record.question_id,
				"question_version": answer_record.question_version,
				"source_ids": list(answer_record.source_ids),
				"answer_version": answer_record.answer_version,
				"follow_up_of": answer_record.follow_up_of,
				"channel": answer_record.channel,
				"verification_status": answer_record.verification_status,
			},
			"candidate": candidate.to_dict(),
		}

	@staticmethod
	def _count_answers_in_state(state: dict[str, Any], job_id: str, candidate_id: str) -> int:
		"""在已有锁内统计回答数量，避免回答写入过程中重新读取文件。"""
		return sum(
			1
			for value in state["candidate_answers"].values()
			if isinstance(value, dict) and value.get("job_id") == job_id and value.get("candidate_id") == candidate_id
		)

	def list_candidate_answers(self, job_id: str, candidate_id: str) -> list[dict[str, str | int]]:
		"""返回指定岗位候选人的完整回答，限定在本地评分边界内使用。"""
		with self._lock:
			rows = [
				CandidateAnswer.from_dict(value).to_dict()
				for value in self._read_state()["candidate_answers"].values()
				if isinstance(value, dict) and value.get("job_id") == job_id and value.get("candidate_id") == candidate_id
			]
		return sorted(rows, key=lambda item: str(item["created_at"]))

	def count_candidate_answers(self, candidate_id: str) -> int:
		"""返回候选人的回答总数，只用于工作台列表计数。"""
		with self._lock:
			return sum(
				1
				for value in self._read_state()["candidate_answers"].values()
				if isinstance(value, dict) and value.get("candidate_id") == candidate_id
			)

	def read_candidate_resume(self, candidate_id: str) -> str:
		"""读取已导入候选人的受控文本正文，仅供评估服务短暂使用。

		路径来自本地 Store 中用户主动导入的记录；再次校验扩展名、文件大小和
		格式，是为了防止文件在导入后被替换成任意二进制或超大文件。正文不会
		写回工作区 JSON，也不会由 Web 层直接返回。
		"""
		candidate = self.get_candidate(candidate_id)
		if candidate is None:
			raise KeyError(candidate_id)
		path = Path(candidate.resume_path).expanduser().resolve()
		try:
			return read_resume_text(path)
		except ResumeTextReadError as exc:
			raise ValueError(str(exc)) from exc

	def save_assessment(self, job_id: str, candidate_id: str, report: Mapping[str, Any] | Any) -> dict[str, Any]:
		"""保存评估报告；支持 AssessmentReport 或已脱敏字典。"""
		payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
		payload.update({"job_id": job_id, "candidate_id": candidate_id, "saved_at": utc_now_iso()})
		key = f"{job_id}:{candidate_id}"
		with self._lock:
			state = self._read_state()
			state["assessments"][key] = payload
			# 评估门禁要求基础意向必须有人工审计事实。首次评估时补建
			# 专用待办，让 Web/CLI 都能沿着可见动作完成门禁，而不是被迫
			# 使用隐藏的阶段回填或人工强制继续。
			self._ensure_basic_intent_task_in_state(
				state, candidate_id=candidate_id, job_id=job_id,
			)
			self._complete_pending_tasks_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kinds={"assess_candidate", "reassess_candidate", "start_professional_qa", "review_resume"},
				note="已生成评估",
			)
			self._ensure_task_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kind="review_assessment",
				title="人工确认评估结果",
				description="查看匹配点、风险点和专业问答证据后选择下一步。",
			)
			self._write_state(state)
		return dict(payload)

	def get_assessment(self, job_id: str, candidate_id: str) -> dict[str, Any] | None:
		"""读取某岗位对某候选人的最近评估。"""
		with self._lock:
			raw = self._read_state()["assessments"].get(f"{job_id}:{candidate_id}")
		return dict(raw) if isinstance(raw, dict) else None

	def has_assessments_for_job(self, job_id: str) -> bool:
		"""判断岗位是否保留过至少一份评估，用于历史岗位的可见性决策。

		BOSS 职位关闭后，平台同步会把本地镜像标记为 ``not_discovered``。该状态
		不能覆盖已经形成的招聘判断：只要存在一份已保存评估，岗位、候选人与
		评分证据就必须继续能够在本地工作台中对应查看。此方法只读取索引，不
		返回简历正文或评估内容，避免视图筛选不必要地扩散候选人数据。
		"""
		clean_job_id = job_id.strip()
		if not clean_job_id:
			return False
		with self._lock:
			assessments = self._read_state()["assessments"]
			return any(
				isinstance(raw, dict) and str(raw.get("job_id") or "") == clean_job_id
				for raw in assessments.values()
			)

	def review_assessment(
		self,
		job_id: str,
		candidate_id: str,
		*,
		outcome: str,
		decision: str,
		next_action: str,
		note: str,
		candidate_stage: str = "resume_passed",
		manual_override: bool = False,
		override_reason: str = "",
		review_gate: Mapping[str, Any] | None = None,
	) -> dict[str, Any]:
		"""保存人工确认和候选人阶段，覆盖下一步动作但不修改原始证据。

		门禁在 Workspace 层计算；Store 只负责把门禁快照和人工例外理由与
		评估报告一起原子保存，保证刷新页面或重新打开工作区后仍可审计。
		"""
		key = f"{job_id}:{candidate_id}"
		with self._lock:
			state = self._read_state()
			raw = state["assessments"].get(key)
			if not isinstance(raw, dict):
				raise KeyError(key)
			candidate_raw = state["candidates"].get(candidate_id)
			if not isinstance(candidate_raw, dict):
				raise KeyError(candidate_id)
			if candidate_stage not in CANDIDATE_STAGE_LABELS:
				raise ValueError("候选人阶段不受支持")
			payload = dict(raw)
			payload.update({
				"review_status": outcome,
				"review_required": False,
				"decision": decision,
				"next_action": next_action,
				"review_note": note,
				"manual_override": bool(manual_override),
				"override_reason": override_reason,
				"review_gate": dict(review_gate) if isinstance(review_gate, Mapping) else {},
				"reviewed_at": utc_now_iso(),
				"saved_at": utc_now_iso(),
			})
			state["assessments"][key] = payload
			# 人工强制继续是显式业务例外：它可以越过门禁，但不能把
			# 已经处理过的基础意向待办遗留在列表里，避免页面同时显示“已
			# 进入简历交换”和“待确认基础意向”。例外原因已随评估保存，
			# 因此关闭待办不会丢失审计信息。
			failed_checks = review_gate.get("failed_checks", []) if isinstance(review_gate, Mapping) else []
			basic_intent_failed = isinstance(failed_checks, list) and any(
				isinstance(item, Mapping) and item.get("code") == "basic_intent"
				for item in failed_checks
			)
			if manual_override and basic_intent_failed:
				self._complete_pending_tasks_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					kinds={"confirm_basic", "complete_basic"},
					note="人工强制继续：基础意向未单独回填",
				)
			candidate = CandidateRecord.from_dict(candidate_raw)
			reviewed_at = utc_now_iso()
			self._set_candidate_job_state_in_state(
				state,
				candidate,
				job_id=job_id,
				stage=candidate_stage,
				updated_at=reviewed_at,
				last_action="人工确认评估",
			)
			event = CandidateEvent(
				event_id=_new_id("event"),
				candidate_id=candidate_id,
				job_id=job_id,
				stage=candidate_stage,
				action="人工确认评估",
				actor="hr",
				note=note,
				ai_judgment=decision,
			)
			state["candidates"][candidate_id] = candidate.to_dict()
			state["candidate_events"][event.event_id] = event.to_dict()
			if candidate_stage in _TERMINAL_STAGES:
				# 评估拒绝属于终局决定，不能只更新候选人 stage；同时写入
				# CandidateDecision，后续漏斗和复盘才能解释“为何结束”。
				self._record_terminal_decision_in_state(
					state,
					job_id=job_id,
					candidate_id=candidate_id,
					outcome=candidate_stage,
					reason=note or decision,
				)
			self._complete_pending_tasks_in_state(
				state,
				candidate_id=candidate_id,
				job_id=job_id,
				kinds={"review_assessment"},
				note=f"人工确认：{outcome}",
			)
			if outcome == "proceed" and candidate_stage in {"professional_passed", "resume_passed"}:
				# 专业问答评估通过只解锁“交换简历”，而简历复评通过才
				# 解锁沟通；两者都必须先经过对应阶段待办，不能把外部动作
				# 合并成一次确认，否则本地审计会缺少中间节点。
				self._ensure_stage_follow_up_in_state(
					state,
					candidate_id=candidate_id,
					stage=candidate_stage,
					job_id=job_id,
				)
			elif outcome == "proceed":
				self._ensure_task_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					kind="continue_conversation",
					title="在 BOSS 沟通页继续沟通",
					description="完成第 1 轮人工沟通后记录结果；连续四轮都有本地时间线。",
					target_stage="private_domain_pending",
					communication_round=1,
				)
			elif outcome == "follow_up":
				self._ensure_task_in_state(
					state,
					candidate_id=candidate_id,
					job_id=job_id,
					kind="reassess_candidate",
					title="补充信息并重新评估",
					description="补充专业问答后重新生成综合评估，再次人工确认。",
					target_stage="professional_qa",
				)
			self._write_state(state)
		return dict(payload)
