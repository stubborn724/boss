"""启动仅本机可访问的招聘简历控制台。"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import click
from aiohttp import web

from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.scheduling import build_pacing_status, pacing_policy_from_config
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter.attachment_index import AttachmentIndex
from boss_agent_cli.commands.recruiter.ai_dialogue import (
	greet_recommendations_once,
	process_dialogue_once,
)
from boss_agent_cli.commands.recruiter.batch_resume_export import (
	SOURCE_CONVERSATION,
	BatchExportReport,
	BatchResumeExportService,
	collect_targets,
)
from boss_agent_cli.commands.recruiter.boss_job_listing import normalize_boss_job_response, normalize_boss_job_sync_response
from boss_agent_cli.commands.recruiter.conversation_listing import (
	conversation_items_from_records,
	extract_non_empty_record_list,
	load_conversation_items,
	positive_platform_id,
)
from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.service import AIService
from boss_agent_cli.commands.recruiter.communication_pipeline import CommunicationPipeline
from boss_agent_cli.commands.recruiter.conversation_resume_export import (
	ConversationResumeExportResult,
	ConversationResumeExportService,
	ConversationAttachmentResult,
)
from boss_agent_cli.commands.recruiter.recommendation_service import (
	RecommendationResumeExportResult,
	normalize_recommendation_response,
	prepare_recommendation_query,
)
from boss_agent_cli.commands.recruiter.resume_download_service import ResumeDownloadService
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult, export_candidate_resume
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
from boss_agent_cli.compliance import RESEARCH_MODE, operating_mode
from boss_agent_cli.output import emit_success
from boss_agent_cli.recruiting.ai_review import AIReviewError, review_resume
from boss_agent_cli.recruiting.job_standard_agent import JobStandardAgent
from boss_agent_cli.recruiting.job_context import JobContextError, resolve_job_context
from boss_agent_cli.recruiting.context import (
	DEFAULT_RECRUITING_CONTEXT,
	RecruitingContext,
	RecruitingContextRegistry,
	context_data_dir,
)
from boss_agent_cli.commands.recruiter.conversation_state import ConversationStateStore
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.recruiting.automation_coordinator import (
	AutomationCandidateEvent,
	AutomationCoordinator,
	ConversationSeed,
)
from boss_agent_cli.recruiting.automation_queue import AutomationCandidateStage, AutomationQueueStore
from boss_agent_cli.recruiting.dialogue_models import DialogueStage, InterviewPhase
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore
from boss_agent_cli.recruiting.dialogue_transcript import DialogueTranscriptStore
from boss_agent_cli.recruiting.interview_settings import InterviewInvitationSettingsStore
from boss_agent_cli.recruiting.automation_schedule_settings import AutomationScheduleSettingsStore
from boss_agent_cli.recruiting.recommendation_quota import RecommendationQuotaStore
from boss_agent_cli.recruiting.candidate_followups import CandidateFollowUpExecutor, CandidateFollowUpStore
from boss_agent_cli.recruiting.automation_state_projection import restore_candidate_from_persisted_states
from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime

from urllib.parse import urlparse


def _probe_automation_platform(platform: Any, conversation_reader: Callable[[], list[dict[str, object]]]) -> bool:
	"""验证自动化所需的实时 BOSS 会话，允许轻量探测异常时使用只读兜底。

	CDP 长连接在页面切换、浏览器刷新或 WebSocket 重连期间，单独读取当前 URL
	可能暂时失败；但同一会话的沟通列表读取可能已经恢复。自动化守卫不能因为
	这个瞬时探测异常就停止一个实际上可读的 BOSS 页面，因此先使用轻量探测，
	失败后再执行一次不产生平台写操作的沟通列表读取。只有两条路径都失败时，
	才把会话判定为不可用。
	"""
	try:
		probe_result = platform.probe_live_login()
	except Exception:
		probe_result = None
	if probe_result is True:
		return True
	if probe_result is False:
		return False
	try:
		conversation_reader()
	except Exception:
		return False
	return True


def _ensure_all_conversation_jobs(platform: Any) -> None:
	"""将手动沟通列表读取显式切回 BOSS 的全部职位。

	RPA 浏览器是共享的有状态页面。没有岗位参数并不代表 BOSS 会自动取消
	上一次 Java、售后等筛选；因此必须先切换并验证“全部职位”回显，失败时
	停止读取，不能把单岗数据伪装成全部岗位结果。
	"""
	selector = getattr(platform, "select_all_conversation_jobs", None)
	if not callable(selector):
		raise RuntimeError("当前 RPA 平台不支持切换 BOSS 沟通列表全部职位")
	response = selector()
	if not platform.is_success(response):
		message = str(response.get("message") or "BOSS 沟通列表全部职位切换失败") if isinstance(response, dict) else "BOSS 沟通列表全部职位切换失败"
		raise RuntimeError(message)
	data = platform.unwrap_data(response)
	selected_scope = str(data.get("selectedScope") or "") if isinstance(data, dict) else ""
	if selected_scope != "all":
		raise RuntimeError("BOSS 沟通列表未切换到全部职位")


def _create_configured_ai_service(data_dir: Path) -> AIService | None:
	"""按本地 AI 配置创建共享客户端，配置不完整时保持规则降级。

	Web 控制台的沟通话术和候选人评估共用同一份 AI 配置，但两条调用链仍由
	各自的业务函数决定提示词和输出结构。集中创建客户端可以避免模型、温度和
	令牌上限在两个入口发生漂移。
	"""
	store = AIConfigStore(data_dir)
	if not store.is_configured():
		return None
	config = store.load_config()
	api_key = store.get_api_key()
	base_url = store.get_base_url()
	if not api_key or not base_url:
		return None
	return AIService(
		base_url=base_url,
		api_key=api_key,
		model=str(config.get("ai_model", "deepseek-chat")),
		temperature=float(config.get("ai_temperature", 0.7)),
		max_tokens=int(config.get("ai_max_tokens", 4096)),
	)


def _create_recruiting_ai_reviewer(data_dir: Path, mode: str) -> Any | None:
	"""创建候选人语义评审器，并在单次模型失败时退回本地规则。

	简历正文只有在用户明确启用 Research Mode 且已配置 AI 服务时才会出网。
	AI 评审是辅助证据，不应让网络、限流或模型格式异常阻断候选人列表；失败时
	返回 ``None``，工作区仍会保存完整的确定性评分报告，页面也会显示规则来源。
	"""
	if mode != RESEARCH_MODE:
		return None
	service = _create_configured_ai_service(data_dir)
	if service is None:
		return None

	def reviewer(job: Any, resume_text: str) -> Any:
		try:
			return review_resume(service, job, resume_text)
		except AIReviewError:
			return None

	return reviewer


def _create_recruiting_job_standard_agent(data_dir: Path, mode: str) -> JobStandardAgent:
	"""创建岗位标准 Agent；未显式启用 AI 时保留确定性规则解析。"""
	service = _create_configured_ai_service(data_dir) if mode == RESEARCH_MODE else None
	return JobStandardAgent(ai_service=service)


def _create_pipeline_operation(data_dir: Path, platform: Any, conv_service: Any, context: Any) -> Any:
	"""创建 CommunicationPipeline 实例供 Web 运行时调用。"""
	service = _create_configured_ai_service(data_dir)
	ai_chat_fn = None
	if service is not None:
		ai_chat_fn = service.chat
	def evidence_reviewer(job: Any, resume_text: str) -> Any:
		"""只返回经过原文核对的语义证据，失败时安全降级为纯规则评分。"""
		if service is None:
			return None
		try:
			return review_resume(service, job, resume_text)
		except AIReviewError:
			return None
	resume_service = ResumeDownloadService(
		platform=platform, parser=parse_resume, exporter=export_candidate_resume,
	)
	export_service = ConversationResumeExportService(
		platform=platform,
		online_exporter=resume_service.download,
		attachment_downloader=platform.download_attachment,
	) if conv_service is None else conv_service
	return CommunicationPipeline(
		platform=platform,
		data_dir=data_dir,
		ai_chat_fn=ai_chat_fn,
		export_service=export_service,
		ai_evidence_reviewer=evidence_reviewer,
	)

def _default_web_output_dir(data_dir: Path, context: RecruitingContext = DEFAULT_RECRUITING_CONTEXT) -> Path:
	desktop = Path.home() / "Desktop"
	if desktop.is_dir():
		if context.is_default:
			return desktop
		return desktop / "BossAgent" / context.company_id / context.account_id
	return context_data_dir(data_dir, context) / "recruiter" / "resumes"

def _resolve_web_cdp_url(ctx: click.Context, auth: AuthManager, logger: Any) -> str | None:
	obj = ctx.obj or {}
	configured = obj.get("cdp_url")
	if isinstance(configured, str) and configured.strip():
		configured = configured.strip()
		if obj.get("cdp_url_source") == "cli":
			return configured
		try:
			if probe_cdp(configured):
				return configured
		except Exception:
			pass
		host = urlparse(configured).hostname
		if host not in {"127.0.0.1", "localhost", "::1"}:
			return configured
	# 自动检测：先探测默认 9222 端口是否有已运行的 Chrome
	try:
		if probe_cdp("http://127.0.0.1:9222"):
			return "http://127.0.0.1:9222"
	except Exception:
		pass
	# 没检测到已运行的 Chrome，才尝试启动新的
	try:
		resolved = auth.ensure_browser_cdp()
	except Exception:
		return None
	return resolved.strip() if isinstance(resolved, str) and resolved.strip() else None

@click.command("web")
@click.option("--port", default=8765, show_default=True, type=click.IntRange(1024, 65535), help="本地控制台端口（仅绑定 127.0.0.1）")
@click.option("--login-timeout", default=120, show_default=True, type=click.IntRange(30, 600), help="官方页面登录等待秒数")
@click.pass_context
def web_cmd(ctx: click.Context, port: int, login_timeout: int) -> None:
	"""启动本地招聘简历控制台（仅监听 127.0.0.1）"""
	data_dir: Path = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	platform_name = ctx.obj.get("platform", "zhipin")
	automation_config = automation_config_from_dict((ctx.obj.get("config") or {}).get("automation"))
	automation_store = AutomationStore(data_dir)
	pacing_policy = pacing_policy_from_config(automation_config)

	def pacing_status() -> dict[str, Any]:
		state = automation_store.read_state()
		status = build_pacing_status(pacing_policy, state)
		automation_store.write_state(state)
		return status

	context_registry = RecruitingContextRegistry(data_dir)
	active_context = context_registry.active()
	auth = AuthManager(
		context_data_dir(data_dir, active_context),
		logger=logger,
		platform=platform_name,
	)
	cdp_url = _resolve_web_cdp_url(ctx, auth, logger)
	platform = get_recruiter_platform_instance(ctx, auth, cdp_url=cdp_url)

	def raise_read_error(response: dict[str, object], *, fallback_message: str) -> None:
		"""将平台只读接口的认证失败转换为运行时可恢复状态。

		BOSS 有时在 HTTP 200 响应体中返回认证过期码，而不是抛出网络异常。
		这里集中处理该分支，确保职位和推荐候选人入口都会引导用户回到官方
		登录页；未知错误仍使用固定提示，避免把平台原始文本显示到本地页面。
		"""
		try:
			error_code, _ = platform.parse_error(response)
		except Exception:
			error_code = "UNKNOWN"
		if error_code in {"LOGIN_EXPIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}:
			raise PermissionError("BOSS recruiter login expired")
		raise RuntimeError(fallback_message)

	def download_resume(**kwargs: Any) -> ResumeExportResult:
		service = ResumeDownloadService(
			platform=platform,
			parser=parse_resume,
			exporter=export_candidate_resume,
		)
		return service.download(data_dir=context_data_dir(data_dir, active_context), **kwargs)

	def list_boss_jobs() -> list[dict[str, str]]:
		"""读取可用于推荐列表的 BOSS 在线职位。

		归一化层仅保留平台明确提供的加密职位标识，避免后续推荐读取误传
		普通职位 ID 并得到难以理解的空列表。
		"""
		try:
			response = platform.list_jobs()
		except (RecruiterAuthError, AuthRequired, TokenRefreshFailed) as exc:
			raise PermissionError("BOSS recruiter login expired") from exc
		if not platform.is_success(response):
			raise_read_error(response, fallback_message="BOSS 职位列表读取失败")
		return normalize_boss_job_response(response)

	def list_boss_jobs_for_sync() -> list[dict[str, str]]:
		"""读取职位管理页记录，允许 RPA 只提供名称时建立本地镜像。"""
		response = platform.list_jobs()
		if not platform.is_success(response):
			raise_read_error(response, fallback_message="BOSS 职位列表读取失败")
		# ``list_jobs`` 在此处只由职位管理 RPA 调用；真实卡片通常不暴露
		# encryptJobId，因此允许归一化层按职位名称生成本地稳定关联键。
		return normalize_boss_job_sync_response(response, rpa_source="job_management")

	def list_recommendations(job_id: str | None) -> list[dict[str, object]]:
		"""读取并归一化指定职位的推荐候选人列表。

		平台原始响应可能随版本变化，本命令层只返回推荐服务定义的内部
		稳定字段，供运行时转换成不透明选择令牌，避免将候选人定位信息
		直接发送到浏览器。
		"""
		requested_job_id = job_id.strip() if isinstance(job_id, str) else ""
		job = RecruitingWorkspace(automation_data_dir, context=active_context).store.get_job(requested_job_id) if requested_job_id else None
		platform_job_id = prepare_recommendation_query(platform, job) if job is not None else requested_job_id or None
		try:
			response = platform.greet_rec_list(page=1, job_id=platform_job_id)
		except (RecruiterAuthError, AuthRequired, TokenRefreshFailed) as exc:
			raise PermissionError("BOSS recruiter login expired") from exc
		if not platform.is_success(response):
			raise_read_error(response, fallback_message="推荐牛人列表读取失败")
		return [candidate.to_internal_dict() for candidate in normalize_recommendation_response(response)]

	def _ensure_automation_conversation_job(job_id: str) -> None:
		"""在自动化同步前将 BOSS 沟通页锁定到本地当前岗位。

		页面手动刷新仍可查看全部职位；只有后台自动化传入岗位 ID 时才强制筛选。
		这样既保留人工浏览能力，又确保队列快照、未读轮询和候选人处理始终来自同一
		职位。筛选器不支持、岗位不存在或回显异常时立即失败，不能继续混用全职位
		会话数据。
		"""
		job = RecruitingWorkspace(automation_data_dir, context=active_context).store.get_job(job_id)
		if job is None or not job.name.strip():
			raise RuntimeError("自动化岗位不存在或缺少岗位名称，无法筛选 BOSS 沟通列表")
		selector = getattr(platform, "select_conversation_job", None)
		if not callable(selector):
			raise RuntimeError("当前 RPA 平台不支持按岗位筛选 BOSS 沟通列表")
		response = selector(job.name)
		if not platform.is_success(response):
			message = str(response.get("message") or "BOSS 沟通列表岗位筛选失败") if isinstance(response, dict) else "BOSS 沟通列表岗位筛选失败"
			raise RuntimeError(message)
		data = platform.unwrap_data(response)
		selected_name = str(data.get("selectedJobName") or "") if isinstance(data, dict) else ""
		if selected_name.casefold() != job.name.strip().casefold():
			raise RuntimeError(f"BOSS 沟通列表未切换到岗位：{job.name}")

	def list_recent(job_id: str | None = None) -> list[dict[str, object]]:
		"""读取沟通会话；自动化传入岗位时先完成岗位筛选。

		BOSS 的沟通列表是虚拟滚动结构。支持一次浏览器内完整快照的 RPA 客户端
		直接复用该结果，避免首轮同步逐页读取导致页面长时间跳动；旧平台适配器仍
		保留分页回退，保证命令行和测试替身的兼容性。
		"""
		if job_id:
			_ensure_automation_conversation_job(job_id)
		else:
			_ensure_all_conversation_jobs(platform)
		fast_snapshot = getattr(platform, "fast_conversation_snapshot", None)
		if callable(fast_snapshot):
			try:
				response = fast_snapshot(include_all=True)
			except TypeError:
				# 旧版适配器没有 include_all 参数时走稳定的分页路径，不能
				# 因接口版本差异把沟通列表同步误报为失败。
				response = None
			if response is not None and platform.is_success(response):
				records = extract_non_empty_record_list(platform.unwrap_data(response) or {})
				return conversation_items_from_records(records)
		return load_conversation_items(platform, job_id="", max_pages=50)

	def list_recent_incremental(job_id: str | None = None) -> list[dict[str, object]]:
		"""快速读取全量未读会话，避免每轮逐页等待约 90 秒。

		RPA 实现将虚拟列表滚动和卡片投影放入一次浏览器脚本；其它平台或旧版
		适配器没有该能力时才退回分页读取，保证命令行和测试替身仍可工作。
		"""
		if job_id:
			_ensure_automation_conversation_job(job_id)
		else:
			_ensure_all_conversation_jobs(platform)
		fast_snapshot = getattr(platform, "fast_conversation_snapshot", None)
		if callable(fast_snapshot):
			response = fast_snapshot()
			if platform.is_success(response):
				records = extract_non_empty_record_list(platform.unwrap_data(response) or {})
				return conversation_items_from_records(records)
		return load_conversation_items(platform, job_id="", max_pages=50)

	def get_current_friend_id() -> int:
		fid = platform.current_chat_friend_id()
		if not isinstance(fid, int) or fid <= 0:
			raise RuntimeError("未在 BOSS 沟通页选中候选人")
		return fid

	def get_latest_friend_id() -> int:
		resp = platform.friend_list(page=1)
		records = extract_non_empty_record_list(resp)
		if not records:
			zpdata = resp.get("zpData") or resp.get("data")
			if isinstance(zpdata, dict):
				records = extract_non_empty_record_list(zpdata)
		if not records and isinstance(resp, dict):
			for key in ("friendList", "list", "result", "items", "friends"):
				candidate = resp.get(key)
				if isinstance(candidate, list):
					records = [item for item in candidate if isinstance(item, dict)]
					if records:
						break
		fid = positive_platform_id(records[0].get("friendId")) if records else None
		if fid is None:
			raise RuntimeError("无法获取最近会话 ID")
		return fid

	def conversation_detail_op(friend_id: int) -> dict[str, object]:
		resp = platform.chat_history(friend_id, count=1)
		if not platform.is_success(resp):
			raise RuntimeError("会话详情读取失败")
		data = resp.get("zpData") or resp.get("data") or resp
		if isinstance(data, dict):
			return {
				"position": str(data.get("jobName") or data.get("positionName") or ""),
				"company": str(data.get("companyName") or data.get("brandName") or ""),
				"city": str(data.get("cityName") or data.get("city") or ""),
			}
		return {}

	def download_conversation(**kwargs: Any) -> ConversationResumeExportResult:
		if kwargs.get("output") is None and kwargs.get("output_dir") is None:
			kwargs["output_dir"] = _default_web_output_dir(data_dir, active_context)
		resume_service = ResumeDownloadService(platform=platform, parser=parse_resume, exporter=export_candidate_resume)
		service = ConversationResumeExportService(platform=platform, online_exporter=resume_service.download, attachment_downloader=platform.download_attachment)
		return service.export(data_dir=context_data_dir(data_dir, active_context), **kwargs)

	def download_rec(**kwargs: Any) -> RecommendationResumeExportResult:
		friend_id = kwargs.pop("friend_id", None)
		if kwargs.get("output") is None and kwargs.get("output_dir") is None:
			kwargs["output_dir"] = _default_web_output_dir(data_dir, active_context)
		resume_service = ResumeDownloadService(platform=platform, parser=parse_resume, exporter=export_candidate_resume)
		if isinstance(friend_id, int) and friend_id > 0:
			conv_service = ConversationResumeExportService(platform=platform, online_exporter=resume_service.download, attachment_downloader=platform.download_attachment)
			exported = conv_service.export(data_dir=context_data_dir(data_dir, active_context), friend_id=friend_id, **kwargs)
			return RecommendationResumeExportResult(candidate_name=exported.candidate_name, online_resume=exported.online_resume, attachment=exported.attachment)
		return RecommendationResumeExportResult(candidate_name="", online_resume=resume_service.download(data_dir=context_data_dir(data_dir, active_context), **kwargs), attachment=ConversationAttachmentResult(status="not_checked"))

	def batch_export_op(
		*,
		source: str,
		limit: int,
		mode: str = "export",
		job_id: str | None = None,
		output_dir: str | None = None,
		stop_event: Any = None,
		on_progress: Any = None,
	) -> BatchExportReport:
		"""批量导出：遍历沟通或推荐列表，逐人导出简历。"""
		output_path = Path(output_dir) if output_dir else _default_web_output_dir(data_dir, active_context)
		output_path.mkdir(parents=True, exist_ok=True)
		resume_service = ResumeDownloadService(platform=platform, parser=parse_resume, exporter=export_candidate_resume)
		conv_service = ConversationResumeExportService(platform=platform, online_exporter=resume_service.download, attachment_downloader=platform.download_attachment)
		service = BatchResumeExportService(
			export_conversation=lambda **kw: conv_service.export(data_dir=context_data_dir(data_dir, active_context), **kw),
			export_online=lambda **kw: resume_service.download(data_dir=context_data_dir(data_dir, active_context), **kw),
		)
		if source == "conversation":
			targets = collect_targets(platform, source=SOURCE_CONVERSATION, limit=limit, job_id=job_id)
		elif source == "recommendation":
			targets = collect_targets(platform, source="recommendation", limit=limit, job_id=job_id)
		else:
			raise ValueError(f"不支持的来源: {source}")
		return service.run(
			targets=targets,
			output_dir=output_path,
			mode=mode,
			stop_event=stop_event,
			on_progress=on_progress,
		)

	try:
		pipeline_op = _create_pipeline_operation(data_dir, platform, None, active_context)
	except Exception:
		pipeline_op = None
	automation_data_dir = context_data_dir(data_dir, active_context)
	automation_ai_service = _create_configured_ai_service(automation_data_dir)
	automation_queue = AutomationQueueStore(automation_data_dir)
	automation_dialogue_states = DialogueStateStore(automation_data_dir)
	automation_conversation_states = ConversationStateStore(automation_data_dir)
	automation_transcripts = DialogueTranscriptStore(automation_data_dir)
	# 所有 BOSS 页面动作必须串行。该锁同时交给 Web 运行时和自动化协调器，
	# 覆盖手动同步、后台轮询、附件终审和详情读取，避免共享 RPA 页面被并发切换。
	platform_operation_lock = Lock()
	# 候选人联系方式和约面试都复用当前已登录平台实例；运行时只保存可调用
	# 边界，不持有平台对象本身，避免 Web 层绕开岗位和会话校验。
	interview_settings_store = InterviewInvitationSettingsStore(automation_data_dir)
	automation_schedule_store = AutomationScheduleSettingsStore(automation_data_dir)
	# 推荐额度属于 BOSS 账号，不能按岗位各存一份，否则切岗会绕过当天上限。
	recommendation_quota = RecommendationQuotaStore(automation_data_dir)
	# 同一轮同步和对话处理共享这份短生命周期原始快照。它只在内存中保存，
	# 不落盘、不进入活动日志，职责是避免对每个 friend_id 再次滚动整张列表。
	automation_snapshot_records: dict[str, list[dict[str, object]]] = {}

	def sync_automation_conversation_records(
		job_id: str,
		records: list[dict[str, object]],
		*,
		replace_snapshot: bool = False,
	) -> list[ConversationSeed]:
		"""从同一份 BOSS 快照同步身份，并恢复同岗位历史事实。

		Web 手动同步已经读取了实时沟通列表；这里只解释传入记录，不再次访问
		平台，确保“沟通列表展示”和“自动化队列入库”来自同一批候选人。
		"""
		# 只有完整快照才能定义本轮固定顺序。后续增量同步可能只包含未读窗口，
		# 不能用空列表或少量记录覆盖完整快照，否则恢复核对时无法定位深层候选人。
		if replace_snapshot or len(records) > len(automation_snapshot_records.get(job_id, [])):
			automation_snapshot_records[job_id] = [dict(item) for item in records]
		friend_ids = {
			int(item["friend_id"])
			for item in records
			if isinstance(item.get("friend_id"), int)
			and not isinstance(item.get("friend_id"), bool)
			and int(item["friend_id"]) > 0
		}
		# 两份账本可能达到 MB 级。先批量读取，再进入逐条解释，避免 968 位
		# 候选人触发近千次完整 JSON 解析，导致同步在 watchdog 前耗尽预算。
		dialogue_states = automation_dialogue_states.map_for_job(job_id=job_id, friend_ids=friend_ids)
		conversation_states = automation_conversation_states.get_many(friend_ids)
		seeds: list[ConversationSeed] = []
		for item in records:
			friend_id = item.get("friend_id")
			if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
				continue
			candidate_name = str(item.get("candidate_name") or "")
			dialogue_state = dialogue_states.get(friend_id)
			# 对话账本带有岗位标识。不同岗位的历史不得覆盖当前岗位队列，避免
			# 同一候选人曾应聘其它岗位时复用旧评分或附件结论。
			matching_dialogue_state = dialogue_state if dialogue_state is not None and dialogue_state.job_id == job_id else None
			if matching_dialogue_state is not None:
				restore_candidate_from_persisted_states(
					queue=automation_queue,
					job_id=job_id,
					seed=ConversationSeed(friend_id=friend_id, candidate_name=candidate_name, source=matching_dialogue_state.source),
					dialogue_state=matching_dialogue_state,
					conversation_state=conversation_states.get(friend_id, {}),
				)
			seeds.append(ConversationSeed(
				friend_id=friend_id,
				candidate_name=candidate_name,
				source=matching_dialogue_state.source if matching_dialogue_state is not None else "conversation",
				unread_count=(
					int(item.get("unread_count") or 0)
					if isinstance(item.get("unread_count"), int) and not isinstance(item.get("unread_count"), bool)
					else 0
				),
				conversation_version=str(item.get("conversation_version") or "").strip()[:128],
			))
		return seeds

	def sync_automation_conversations(job_id: str) -> list[ConversationSeed]:
		"""后台轮询入口：主动读取 BOSS 沟通列表后复用统一快照解释逻辑。"""
		return sync_automation_conversation_records(job_id, list_recent(job_id), replace_snapshot=True)

	def process_automation_dialogue(job_id: str, friend_ids: tuple[int, ...], stop_event: Any) -> list[AutomationCandidateEvent]:
		"""只处理新回复；没有 AI 配置时明确转人工而不发送猜测话术。"""
		workspace = RecruitingWorkspace(automation_data_dir, context=active_context)
		job = workspace.store.get_job(job_id)
		if job is None or automation_ai_service is None:
			return [
				AutomationCandidateEvent(friend_id=friend_id, stage=AutomationCandidateStage.MANUAL_REVIEW, action="岗位或 AI 服务未就绪")
				for friend_id in friend_ids
			]
		events: list[AutomationCandidateEvent] = []
		if stop_event.is_set():
			return events
		# 一轮固定目标后批量读取，避免每个 friend_id 都重新扫描几十页列表。
		report = process_dialogue_once(
			data_dir=automation_data_dir,
			platform=platform,
			job=job,
			chat=automation_ai_service.chat,
			limit=len(friend_ids),
			friend_ids=friend_ids,
			# 协调器已将本批次严格限制为 BOSS 未读、首次检查或列表版本变化的
			# 会话；允许这些定向目标再次读取一条消息，才能补偿人工点开后红点
			# 消失的真实回复，而不会扫描其它等待中的候选人。
			force_waiting_recheck=True,
			stop_requested=stop_event.is_set,
			snapshot_records=automation_snapshot_records.get(job_id),
		)
		for rejected_id in report.hard_rejected_friend_ids:
			events.append(AutomationCandidateEvent(
				friend_id=rejected_id,
				stage=AutomationCandidateStage.HARD_REJECTED,
				action="RPA 硬筛不通过",
				reason_codes=report.hard_rejection_reason_codes(rejected_id),
			))
		for unresolved_id in report.unresolved_friend_ids:
			events.append(AutomationCandidateEvent(
				friend_id=unresolved_id,
				# 保持可调度状态：BOSS 的未读徽标可能在下一次 DOM 刷新时短暂
				# 消失，不能因为一次消息读取缺失就永久跳过真实候选人回复。
				stage=AutomationCandidateStage.SYNCED,
				action="发现未读回复但暂时未读取到消息，下一轮重试",
			))
		for processed_id in report.processed_friend_ids:
			state = DialogueStateStore(automation_data_dir).get_for_job(job_id=job_id, friend_id=processed_id)
			if state is None:
				continue
			if state.stage is DialogueStage.READY_FOR_RESUME:
				stage, action = AutomationCandidateStage.WAITING_ATTACHMENT, "已完成两阶段问答，等待附件"
			elif state.stage is DialogueStage.MANUAL_REVIEW:
				stage, action = AutomationCandidateStage.MANUAL_REVIEW, "AI 建议人工复核"
			elif state.interview_phase is InterviewPhase.PROFESSIONAL:
				stage, action = AutomationCandidateStage.PROFESSIONAL_DIALOGUE, "已进入专业问答"
			else:
				stage, action = AutomationCandidateStage.WAITING_CANDIDATE, "等待候选人回复基础问题"
			events.append(AutomationCandidateEvent(friend_id=processed_id, stage=stage, action=action))
		return events

	def finalize_automation_attachments(job_id: str, friend_ids: tuple[int, ...], stop_event: Any) -> list[AutomationCandidateEvent]:
		"""只对已完成两阶段问答的会话走附件下载和终审。"""
		if pipeline_op is None:
			return []
		events: list[AutomationCandidateEvent] = []
		states = DialogueStateStore(automation_data_dir)
		for friend_id in friend_ids:
			if stop_event.is_set():
				break
			state = states.get_for_job(job_id=job_id, friend_id=friend_id)
			if state is None or state.stage is not DialogueStage.READY_FOR_RESUME:
				continue
			# 每位候选人的步骤开始时重新加载最新已确认版本。页面在步骤执行中
			# 保存新规则不会打断当前 RPA 动作，下一位或下一步骤自然切换。
			job = RecruitingWorkspace(automation_data_dir, context=active_context).store.get_job(job_id)
			if job is None:
				events.append(AutomationCandidateEvent(friend_id=friend_id, stage=AutomationCandidateStage.MANUAL_REVIEW, action="岗位不存在，附件终审已停止"))
				continue
			try:
				job_context = resolve_job_context(job, require_confirmed=True)
			except JobContextError as exc:
				events.append(AutomationCandidateEvent(friend_id=friend_id, stage=AutomationCandidateStage.MANUAL_REVIEW, action=str(exc)))
				continue
			step = pipeline_op.analyze_one(
				friend_id=friend_id,
				candidate_name=state.candidate_name,
				ask_for_resume=True,
				job_context=job_context,
			)
			if step.status == "analyzed" and step.attachment_path:
				events.append(AutomationCandidateEvent(
					friend_id=friend_id,
					stage=AutomationCandidateStage.ANALYZED,
					action="附件简历终审完成",
					score=step.score,
					recommendation=step.analysis.recommendation if step.analysis else "review",
					resume_path=Path(step.attachment_path),
				))
			elif step.status == "waiting_for_resume":
				events.append(AutomationCandidateEvent(friend_id=friend_id, stage=AutomationCandidateStage.WAITING_ATTACHMENT, action="等待候选人发送附件"))
			elif step.status in {"error", "no_resume"}:
				events.append(AutomationCandidateEvent(friend_id=friend_id, stage=AutomationCandidateStage.MANUAL_REVIEW, action=step.error or "附件流程待人工复核"))
		return events

	def greet_automation_recommendations(job_id: str, limit: int, stop_event: Any) -> list[str]:
		workspace = RecruitingWorkspace(automation_data_dir, context=active_context)
		job = workspace.store.get_job(job_id)
		return greet_recommendations_once(
			data_dir=automation_data_dir,
			platform=platform,
			job=job,
			limit=limit,
			stop_requested=stop_event.is_set,
			recommendation_quota=recommendation_quota,
		) if job is not None else []

	def request_contact_exchange(friend_id: int, contact_type: str) -> dict[str, Any]:
		"""仅由运行时传入已校验的会话与固定类型，委托 RPA 完成二次确认。"""
		return platform.request_contact_exchange(friend_id=friend_id, contact_type=contact_type)

	def invite_interview(friend_id: int, payload: dict[str, str]) -> dict[str, Any]:
		"""提交岗位级已验证设置，不从浏览器直接读取 BOSS 表单字段。"""
		return platform.invite_interview_via_ui(friend_id=friend_id, payload=payload)

	candidate_followup_store = CandidateFollowUpStore(automation_data_dir)
	followup_executor = CandidateFollowUpExecutor(
		store=candidate_followup_store,
		request_contact=request_contact_exchange,
		invite_interview=invite_interview,
		interview_settings=interview_settings_store,
	)

	def run_candidate_followups(job_id: str) -> int:
		"""只为当前岗位已达标候选人执行联系方式和约面试后续动作。

		队列中的附件终审是唯一准入事实；这里不使用前端传入的候选人列表，
		避免未达标人员通过页面参数绕过岗位筛选。每轮都检查已有达标记录，
		从而让候选人稍后回复联系方式或约面试失败后的退避重试能够继续推进。
		"""
		processed = 0
		for candidate in automation_queue.list_for_job(job_id):
			if candidate.stage is not AutomationCandidateStage.ANALYZED:
				continue
			if not isinstance(candidate.score, int) or candidate.score < 80 or candidate.recommendation == "reject":
				continue
			followup_executor.execute(
				job_id=job_id,
				candidate_key=candidate.candidate_key,
				friend_id=candidate.friend_id,
				candidate=candidate.to_dict(),
			)
			processed += 1
		return processed

	def guard_automation_runtime() -> str | None:
		"""后台轮询每轮复检 RPA 登录态，掉线时停止而不是暴露底层解析异常。"""
		if _probe_automation_platform(platform, list_recent):
			return None
		return "当前 RPA 浏览器状态不可用，请确认 BOSS 招聘页已登录后重试"

	automation_coordinator = AutomationCoordinator(
		queue=automation_queue,
		sync_conversations=sync_automation_conversations,
		sync_recent_conversations=lambda job_id: sync_automation_conversation_records(job_id, list_recent_incremental(job_id)),
		sync_records=sync_automation_conversation_records,
		process_dialogue=process_automation_dialogue,
		finalize_attachments=finalize_automation_attachments,
		greet_recommendations=greet_automation_recommendations,
		candidate_followups=run_candidate_followups,
		recommendation_quota=recommendation_quota,
		runtime_guard=guard_automation_runtime,
		# 后台轮询必须与同步、详情、简历下载共用运行时平台锁；否则多个线程
		# 会同时切换同一个 BOSS 页面，导致 CDP 会话失效、超时或误报未登录。
		platform_operation_lock=platform_operation_lock,
		# 本次用户授权只到当天 21:00。将绝对本地时间注入协调器，服务重启或
		# 页面关闭后也不会依赖易失的外部定时器继续执行沟通动作。
		hard_stop_at=datetime.now().astimezone().replace(hour=21, minute=0, second=0, microsecond=0),
	)
	recruiting_ai_reviewer = _create_recruiting_ai_reviewer(data_dir, operating_mode(ctx))
	recruiting_job_standard_agent = _create_recruiting_job_standard_agent(data_dir, operating_mode(ctx))
	def open_login_page() -> None:
		"""打开官方页面后清除旧 RPA 标签绑定，保证后续列表读取重新定位页面。"""
		opened_cdp_url = auth.open_login_page(cdp_url=cdp_url)
		# 服务启动时的 CDP 端口可能来自已退出的临时 Chrome。认证入口已自行
		# 回退并启动专用 Chrome 后，平台客户端必须同步更新目标地址，否则
		# 后续沟通列表仍会访问旧端口而造成“打开成功却刷新失败”。
		client = getattr(platform, "_client", None)
		if client is not None and hasattr(client, "_cdp_url"):
			setattr(client, "_cdp_url", opened_cdp_url)
		reset = getattr(platform, "reset_rpa_session", None)
		if callable(reset):
			reset()

	runtime = LocalConsoleRuntime(
		operating_mode=operating_mode(ctx),
		login_in_browser=lambda: auth.login_in_browser(timeout=login_timeout, cdp_url=cdp_url),
		has_saved_login=auth.has_saved_login,
		open_login_page=open_login_page,
		# 登录状态必须来自当前 RPA 页面；本地 TokenStore 仅用于显示历史凭据提示。
		probe_live_login=platform.probe_live_login,
		download_resume=download_resume,
		download_conversation_resume=download_conversation,
		current_chat_friend_id=get_current_friend_id,
		latest_conversation_friend_id=get_latest_friend_id,
		list_recent_conversations=list_recent,
		list_recent_conversations_for_job=lambda job_id: list_recent(job_id),
		conversation_detail=conversation_detail_op,
		list_recommendations=list_recommendations,
		download_recommendation=download_rec,
		list_boss_jobs=list_boss_jobs,
		list_boss_jobs_for_sync=list_boss_jobs_for_sync,
		# Web 运行时必须注入当前企业/账号上下文的工作台实例；否则页面虽能
		# 展示招聘控件，所有同步和候选人操作都会被判定为未配置。
		recruiting_workspace=RecruitingWorkspace(context_data_dir(data_dir, active_context), context=active_context),
		recruiting_ai_reviewer=recruiting_ai_reviewer,
		recruiting_job_standard_agent=recruiting_job_standard_agent,
		recruiting_context_registry=context_registry,
		recruiting_workspace_factory=lambda context: RecruitingWorkspace(context_data_dir(data_dir, context), context=context),
		pacing_status=pacing_status,
		batch_export=batch_export_op,
		attachment_statuses=lambda: AttachmentIndex.for_data_dir(data_dir).statuses(),
		pipeline_operation=pipeline_op,
		automation_coordinator=automation_coordinator,
		automation_transcript_store=automation_transcripts,
		interview_settings_store=interview_settings_store,
		candidate_followup_store=candidate_followup_store,
		automation_schedule_store=automation_schedule_store,
		request_contact_exchange=request_contact_exchange,
		invite_interview=invite_interview,
		open_online_resume=lambda friend_id: platform.open_online_resume_preview(friend_id=friend_id),
		platform_operation_lock=platform_operation_lock,
	)
	# 本地“获取简历”页面仅用于读取和预览在线简历。服务启动不得恢复历史
	# 定时自动化：那会在用户只想刷新或预览时，隐式执行消息和附件流程。用户
	# 必须在设置页本次保存并启用定时任务，运行时才会激活调度监控。
	app = create_console_app(runtime, session_token=secrets.token_urlsafe(32))
	url = f"http://127.0.0.1:{port}"
	emit_success("web", {"url": url, "host": "127.0.0.1", "port": port})
	try:
		asyncio.run(_serve(app, port))
	except KeyboardInterrupt:
		return

async def _serve(app: web.Application, port: int) -> None:
	"""以手动 runner 启动服务，避免 aiohttp 把非 JSON 启动横幅写入 stdout。"""
	runner = web.AppRunner(app, access_log=None)
	await runner.setup()
	site = web.TCPSite(runner, host="127.0.0.1", port=port)
	await site.start()
	try:
		await asyncio.Event().wait()
	finally:
		await runner.cleanup()
