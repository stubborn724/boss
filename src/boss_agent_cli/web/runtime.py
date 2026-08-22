"""本地招聘控制台的异步任务运行时。

HTTP 层不能在事件循环中直接执行浏览器登录或平台访问；本模块将两类耗时操作
放到受控后台线程，并只保存用户界面需要的最小状态和导出元数据。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import os
from pathlib import Path
from queue import Queue
import secrets
from threading import Event, Lock, Thread
import time
from typing import Any

from boss_agent_cli.commands.recruiter.analysis_tracker import AnalysisTracker
from boss_agent_cli.commands.recruiter.attachment_index import STATUS_UNKNOWN
from boss_agent_cli.commands.recruiter.batch_resume_export import (
	AVAILABLE_MODES,
	AVAILABLE_SOURCES,
	MAX_LIMIT,
	MODE_EXPORT,
	BatchExportReport,
	BatchTargetReadError,
)
from boss_agent_cli.commands.recruiter.conversation_resume_export import (
	ConversationResumeExportResult,
	ConversationResumeNotFoundError,
	ConversationResumePlatformError,
)
from boss_agent_cli.commands.recruiter.resume_download_service import (
	ResumeDownloadExportError,
	ResumeDownloadPlatformError,
)
from boss_agent_cli.commands.recruiter.recommendation_service import RecommendationResumeExportResult
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult
from boss_agent_cli.compliance import RESEARCH_MODE
from boss_agent_cli.rpa.boss_client import BossRPAConnectionError, BossRPALoginRequiredError
from boss_agent_cli.recruiting.ai_review import AIResumeReview
from boss_agent_cli.recruiting.job_standard_agent import JobStandardAgent
from boss_agent_cli.recruiting.job_context import JobContextError, resolve_job_context
from boss_agent_cli.recruiting.online_resume_validation import is_meaningful_online_resume_text
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.recruiting.models import JobProfile
from boss_agent_cli.recruiting.platform_job_sync import PlatformJobSyncService
from boss_agent_cli.recruiting.context import (
	DEFAULT_RECRUITING_CONTEXT,
	RecruitingContext,
	RecruitingContextRegistry,
)
from boss_agent_cli.recruiting.automation_coordinator import AutomationCoordinator
from boss_agent_cli.recruiting.automation_schedule_settings import (
	AutomationScheduleMonitor,
	AutomationScheduleSettings,
	AutomationScheduleSettingsStore,
)
from boss_agent_cli.recruiting.dialogue_transcript import DialogueTranscriptStore
from boss_agent_cli.recruiting.interview_settings import (
	InterviewInvitationSettings,
	InterviewInvitationSettingsStore,
)
from boss_agent_cli.recruiting.candidate_followups import (
	CandidateFollowUpSettings,
	CandidateFollowUpStore,
)

_SAFE_LOGIN_FAILURE_MESSAGE = "登录未完成，请在官方 BOSS 页面确认后重试"
_SAFE_DOWNLOAD_FAILURE_MESSAGE = "简历下载失败，请检查参数、登录态和输出目录后重试"
# 平台沟通列表是高频风控敏感只读接口。60 秒窗口既保留用户明确刷新的能力，
# 又避免页面操作产生短时间连续访问。
_CONVERSATION_LIST_REFRESH_INTERVAL_SECONDS = 60.0
_RECOMMENDATION_LIST_REFRESH_INTERVAL_SECONDS = 60.0
# 单次沟通列表同步的总预算。CDP 单命令超时只能限制一个页面调用，不能覆盖
# 多步骤列表读取、岗位筛选和本地队列写入；运行时还必须给整条任务设置上限，
# 防止任何未预期的页面状态让工作台永久显示“正在同步”。
_AUTOMATION_SYNC_TIMEOUT_SECONDS = 120.0


def _rpa_target_not_ready_error() -> dict[str, str]:
	"""构造 RPA 绑定错误的统一恢复提示。

	RPA 未连到 BOSS 页面时，系统无法判断账号是否有效。集中生成该错误，保证
	沟通列表、岗位同步等所有只读入口都不会把“浏览器上下文错误”误报为登录
	失效或空数据，同时避免多个入口的中文提示逐渐漂移。
	"""
	return {
		"code": "RPA_TARGET_NOT_READY",
		"message": "RPA 当前未连接 BOSS 招聘页面，请在已登录的 Chrome 中打开 BOSS 招聘端并连接 Bridge 后刷新",
	}


def _rpa_browser_login_required_error() -> dict[str, str]:
	"""构造专用 RPA 浏览器缺少 BOSS 登录会话时的恢复提示。"""
	return {
		"code": "RPA_BROWSER_LOGIN_REQUIRED",
		"message": "当前 Chrome 的 BOSS 招聘页面尚未登录，请完成登录后刷新",
	}


def _visible_friend_ids_from_records(records: list[dict[str, object]]) -> tuple[int, ...]:
	"""提取本次 BOSS 快照的可见会话顺序，供自动化队列过滤历史事实。

	页面沟通列表会把 ``friend_id`` 转成不透明选择标识；自动化队列则需要服务端
	记住原始顺序来还原 BOSS 当前列表。这里集中做类型校验和去重，避免布尔值、
	空值或重复记录污染后续队列投影。
	"""
	friend_ids: list[int] = []
	for record in records:
		friend_id = record.get("friend_id")
		if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
			continue
		friend_ids.append(friend_id)
	return tuple(dict.fromkeys(friend_ids))


def _candidate_name_from_records(records: list[dict[str, object]], friend_id: int) -> str:
	"""从刷新后的沟通快照取得候选人显示名，避免信任浏览器传入的名称。

	在线简历预览的身份依据始终是 ``friend_id``。姓名只用于本地展示，因此必须
	由同一轮已校验的 BOSS 沟通列表派生；读取不到时允许为空，不能因此改为点击
	或读取其它候选人。
	"""
	for record in records:
		recorded_id = record.get("friend_id", record.get("friendId"))
		try:
			if int(str(recorded_id)) != friend_id:
				continue
		except (TypeError, ValueError):
			continue
		return str(record.get("candidate_name", record.get("name", ""))).strip()[:120]
	return ""

LoginOperation = Callable[[], object]
OpenLoginPageOperation = Callable[[], object]
SavedLoginProbe = Callable[[], bool]
LiveLoginProbe = Callable[[], bool]
DownloadOperation = Callable[..., ResumeExportResult]
ConversationDownloadOperation = Callable[..., ConversationResumeExportResult]
CurrentChatFriendIdOperation = Callable[[], int]
LatestConversationFriendIdOperation = Callable[[], int]
ConversationListOperation = Callable[[], list[dict[str, object]]]
ConversationListForJobOperation = Callable[[str], list[dict[str, object]]]
ConversationDetailOperation = Callable[[int], dict[str, object]]
RecommendationListOperation = Callable[[str | None], list[dict[str, object]]]
RecommendationDownloadOperation = Callable[..., RecommendationResumeExportResult]
MonotonicClock = Callable[[], float]
RecruitingOperation = Callable[[], dict[str, Any]]
RecruitingWorkspaceFactory = Callable[[RecruitingContext], RecruitingWorkspace]
RecruitingAIReviewer = Callable[[JobProfile, str], AIResumeReview | None]
RecruitingContextSwitcher = Callable[[RecruitingContext], None]
PacingStatusProvider = Callable[[], dict[str, Any]]
# 批量导出由命令层装配：运行时只负责状态机、停止开关和进度投影。
BatchExportOperation = Callable[..., BatchExportReport]
AttachmentStatusProvider = Callable[[], dict[int, str]]
BossJobsProvider = Callable[[], list[dict[str, str]]]
PipelineOperation = Callable[..., Any]
CandidateContactExchangeOperation = Callable[[int, str], dict[str, Any]]
InterviewInvitationOperation = Callable[[int, dict[str, str]], dict[str, Any]]
OnlineResumePreviewOperation = Callable[[int], dict[str, Any]]

_PACING_STATUS_FIELDS = (
	"configured",
	"day",
	"count",
	"daily_action_quota",
	"effective_quota",
	"remaining",
	"allowed",
	"reason",
	"reason_label",
	"pause_until",
	"last_action_at",
	"schedule_enabled",
	"window",
	"window_label",
	"cooldown_seconds",
)


def _default_pacing_status(*, configured: bool = False, unavailable: bool = False) -> dict[str, Any]:
	"""返回无自动化记录或读取失败时的安全占位状态。"""
	if unavailable:
		reason_label = "安全节奏状态暂不可用，请查看本地自动化日志"
	else:
		reason_label = "当前工作台未接入自动化动作"
	return {
		"configured": configured,
		"day": "",
		"count": 0,
		"daily_action_quota": 0,
		"effective_quota": 0,
		"remaining": 0,
		"allowed": False if unavailable else True,
		"reason": "unavailable" if unavailable else "",
		"reason_label": reason_label,
		"pause_until": "",
		"last_action_at": "",
		"schedule_enabled": False,
		"window": "all_day",
		"window_label": "未接入自动化引擎",
		"cooldown_seconds": 0,
	}


class _SerialTaskRunner:
	"""在一条守护线程上顺序执行本地控制台的耗时任务。

	BOSS 的 CDP 驱动和部分 HTTP 连接带有线程归属，不能把同一个平台实例先在
	列表线程创建、再交给下载线程复用。这里使用显式队列而不是线程池：线程是
	守护线程，服务退出时不会因为等待登录超时而阻塞进程；同一运行时的所有任务
	仍共享唯一执行线程，保证浏览器上下文和认证客户端的生命周期稳定。
	"""

	def __init__(self) -> None:
		self._queue: Queue[tuple[Callable[[], None], Future[None]]] = Queue()
		self._start_lock = Lock()
		self._thread: Thread | None = None

	def submit(self, operation: Callable[[], None]) -> Future[None]:
		"""提交一个后台任务并返回可等待的完成句柄。"""
		future: Future[None] = Future()
		with self._start_lock:
			if self._thread is None:
				self._thread = Thread(target=self._run, name="boss-web-platform-serial", daemon=True)
				self._thread.start()
		self._queue.put((operation, future))
		return future

	def _run(self) -> None:
		"""持续消费任务；异常交给 Future 保存，不能杀掉后续任务。"""
		while True:
			operation, future = self._queue.get()
			if not future.set_running_or_notify_cancel():
				continue
			try:
				operation()
			except BaseException as exc:
				future.set_exception(exc)
			else:
				future.set_result(None)


class LocalConsoleRuntime:
	"""协调一次登录和一次简历下载的本地运行时。

	运行时是 Web 层与业务服务之间的边界：它不保存认证结果、原始平台响应或简历
	正文。登录和下载各自单飞，重复点击只返回现有任务状态，避免重复打开官方页面
	或在同一候选人快照写入期间产生并发竞争。
	"""

	def __init__(
		self,
		*,
		operating_mode: str,
		login_in_browser: LoginOperation,
		has_saved_login: SavedLoginProbe,
		open_login_page: OpenLoginPageOperation | None = None,
		probe_live_login: LiveLoginProbe | None = None,
		download_resume: DownloadOperation,
		download_conversation_resume: ConversationDownloadOperation | None = None,
		current_chat_friend_id: CurrentChatFriendIdOperation | None = None,
		latest_conversation_friend_id: LatestConversationFriendIdOperation | None = None,
		list_recent_conversations: ConversationListOperation | None = None,
		list_recent_conversations_for_job: ConversationListForJobOperation | None = None,
		conversation_detail: ConversationDetailOperation | None = None,
		list_recommendations: RecommendationListOperation | None = None,
		download_recommendation: RecommendationDownloadOperation | None = None,
		recruiting_workspace: RecruitingWorkspace | None = None,
		recruiting_ai_reviewer: RecruitingAIReviewer | None = None,
		recruiting_job_standard_agent: JobStandardAgent | None = None,
		recruiting_context_registry: RecruitingContextRegistry | None = None,
		recruiting_workspace_factory: RecruitingWorkspaceFactory | None = None,
		recruiting_context_switcher: RecruitingContextSwitcher | None = None,
		pacing_status: PacingStatusProvider | None = None,
		batch_export: BatchExportOperation | None = None,
		attachment_statuses: AttachmentStatusProvider | None = None,
		pipeline_operation: PipelineOperation | None = None,
		automation_coordinator: AutomationCoordinator | None = None,
		automation_transcript_store: DialogueTranscriptStore | None = None,
		interview_settings_store: InterviewInvitationSettingsStore | None = None,
		candidate_followup_store: CandidateFollowUpStore | None = None,
		automation_schedule_store: AutomationScheduleSettingsStore | None = None,
		request_contact_exchange: CandidateContactExchangeOperation | None = None,
		invite_interview: InterviewInvitationOperation | None = None,
		open_online_resume: OnlineResumePreviewOperation | None = None,
		platform_operation_lock: Any | None = None,
	list_boss_jobs: BossJobsProvider | None = None,
		list_boss_jobs_for_sync: BossJobsProvider | None = None,
		monotonic_clock: MonotonicClock = time.monotonic,
	) -> None:
		self.operating_mode = operating_mode
		self._login_in_browser = login_in_browser
		self._has_saved_login = has_saved_login
		# 打开官方页面与等待用户完成登录是两类不同操作。前者必须快速返回，
		# 否则按钮会在扫码期间看起来没有响应；后者仍由显式登录流程负责。
		self._open_login_page = open_login_page or login_in_browser
		# 历史凭据只能作为提示，不能作为自动化授权依据。命令层注入的实时探测
		# 直接检查当前 RPA 浏览器；保留可选项是为了兼容不接入 BOSS 的离线测试。
		self._probe_live_login = probe_live_login
		self._download_resume = download_resume
		self._download_conversation_resume = download_conversation_resume
		self._current_chat_friend_id = current_chat_friend_id
		self._latest_conversation_friend_id = latest_conversation_friend_id
		self._list_recent_conversations = list_recent_conversations
		# 手动页面列表允许查看全部职位；自动化同步则必须由命令层提供按岗位
		# 筛选的读取器，避免同一份 BOSS 列表被不同岗位队列混用。
		self._list_recent_conversations_for_job = list_recent_conversations_for_job
		self._conversation_detail = conversation_detail
		self._list_recommendations = list_recommendations
		self._download_recommendation = download_recommendation
		self._recruiting_context_registry = recruiting_context_registry
		# AI 评审器由命令层按用户配置显式注入。运行时不自行读取密钥，避免
		# Web 层绕过配置和合规边界；为空时工作区自然使用本地规则评估。
		self._recruiting_ai_reviewer = recruiting_ai_reviewer
		# 岗位标准 Agent 只解释用户输入并调用本地工作区；未配置外部 AI 时仍由
		# 其内部规则路径兜底，因此 Web 控制台不会因为模型不可用而无法建岗。
		self._recruiting_job_standard_agent = recruiting_job_standard_agent or JobStandardAgent()
		self._recruiting_workspace_factory = recruiting_workspace_factory
		self._recruiting_context_switcher = recruiting_context_switcher
		self._pacing_status = pacing_status
		self._batch_export_operation = batch_export
		self._attachment_statuses = attachment_statuses
		self._pipeline_operation = pipeline_operation
		self._automation_coordinator = automation_coordinator
		# 时间线由对话服务在成功处理/发送时写入；运行时只负责经过岗位校验后投影，
		# 因而页面无法请求其它岗位候选人的聊天内容。
		self._automation_transcript_store = automation_transcript_store
		# 面试设置是岗位执行配置，独立于岗位标准；候选人动作只接收这里验证过
		# 的值，避免 Web 表单直接把任意字段透传至 BOSS 页面。
		self._interview_settings_store = interview_settings_store
		self._candidate_followup_store = candidate_followup_store or CandidateFollowUpStore(Path.home() / ".boss-agent")
		self._automation_schedule_store = automation_schedule_store
		self._request_contact_exchange = request_contact_exchange
		self._invite_interview = invite_interview
		self._open_online_resume = open_online_resume
		self._automation_candidate_actions: dict[str, dict[str, Any]] = {}
		self._automation_sync: dict[str, Any] = {"state": "idle"}
		self._online_resume_preview: dict[str, Any] = {"state": "idle"}
		# 保存同步 Future 只用于判断旧任务是否仍占用串行执行线程；超时后不能
		# 直接复用同一线程提交新任务，否则旧任务仍持有平台锁时会再次排队卡住。
		self._automation_sync_future: Future[None] | None = None
		self._automation_sync_generation = 0
		# 自动化队列文件保留长期事实；执行页需要的是最近一次 BOSS 同步快照。
		# 这里按岗位保存短生命周期顺序，确保页面队列不会混入本地历史候选人。
		self._automation_visible_friend_ids_by_job: dict[str, tuple[int, ...]] = {}
		self._analysis_tracker = AnalysisTracker(Path("~/.boss-agent").expanduser())
		self._list_boss_jobs = list_boss_jobs
		self._list_boss_jobs_for_sync = list_boss_jobs_for_sync or list_boss_jobs
		if recruiting_context_registry is not None:
			try:
				active_context = recruiting_context_registry.active()
			except (RuntimeError, ValueError):
				active_context = DEFAULT_RECRUITING_CONTEXT
		else:
			active_context = getattr(recruiting_workspace, "context", DEFAULT_RECRUITING_CONTEXT)
		self._recruiting_context = active_context
		self._recruiting_workspace = recruiting_workspace
		if self._recruiting_workspace is None and recruiting_workspace_factory is not None:
			self._recruiting_workspace = recruiting_workspace_factory(active_context)
		# 单调时钟只用于进程内冷却，不会受系统时间校准影响；可注入使边界测试
		# 无须真实等待 60 秒。
		self._monotonic_clock = monotonic_clock
		self._state_lock = Lock()
		self._login_lock = Lock()
		# 平台客户端会复用同一个 HTTP 连接和 CDP 页面，底层实现不是线程安全的。
		# 列表刷新、简历导出和后台自动化必须共用同一把锁，避免并行请求互相
		# 关闭或覆盖会话状态。命令层可把这把锁同时注入协调器；普通测试和
		# 离线调用未注入时仍创建本地锁，保持原有单线程保护。
		self._platform_operation_lock = platform_operation_lock if platform_operation_lock is not None else Lock()
		self._login = self._read_live_login_state()
		self._download: dict[str, Any] = {"state": "idle"}
		self._conversation_download: dict[str, Any] = {"state": "idle"}
		self._conversation_list: dict[str, Any] = {"state": "idle", "items": []}
		self._conversation_selections: dict[str, int] = {}
		self._conversation_detail_state: dict[str, Any] = {"state": "idle"}
		# 上下文代际同时覆盖账号切换和沟通列表刷新。后台任务即使已经排队，
		# 也只能把结果写回它创建时对应的代际，避免旧候选人覆盖新列表。
		self._platform_generation = 0
		self._conversation_generation = 0
		self._last_conversation_list_success_at: float | None = None
		self._recommendations: dict[str, Any] = {"state": "idle", "items": []}
		self._recommendation_download: dict[str, Any] = {"state": "idle"}
		self._recommendation_selections: dict[str, dict[str, object]] = {}
		self._recommendation_job_id: str | None = None
		self._last_recommendation_success_at: float | None = None
		self._recruiting: dict[str, Any] = {"state": "idle"}
		# 批量导出是唯一会连续访问平台的入口：状态里保留进度、停批原因和逐人
		# 结果，页面靠轮询就能看到推进，不需要额外的长连接。
		self._batch_export: dict[str, Any] = {"state": "idle"}
		self._pipeline: dict[str, Any] = {"state": "idle"}
		self._single_analysis: dict[str, Any] = {"state": "idle"}
		self._batch_stop_event: Event | None = None
		self._task_runner = _SerialTaskRunner()
		self._workers: list[Future[None]] = []
		self._automation_schedule_monitor = (
			AutomationScheduleMonitor(
				store=automation_schedule_store,
				start_automation=lambda job_id, source, limit: self.start_automation(job_id=job_id, source=source, limit=limit),
				stop_automation=self.stop_automation_source,
				automation_status=lambda: self._automation_coordinator.status() if self._automation_coordinator else {"state": "unsupported"},
			)
			if automation_schedule_store is not None and automation_coordinator is not None
			else None
		)

	def status(self) -> dict[str, Any]:
		"""返回可以安全发送给浏览器的状态快照。

		状态接口只返回最近一次登录探测的缓存结果，不主动访问 RPA 页面。页面
		轮询与自动化任务会并发发生，若状态查询也读取同一 CDP WebSocket，可能
		抢走自动化命令的响应帧；实时探测仅在启动、登录流程和自动化轮询守卫中
		执行，既保留写操作门禁，也避免状态读取干扰正在处理的候选人。
		"""
		with self._state_lock:
			# 自动恢复：磁盘已有有效登录态但内存状态仍为失败/空闲时，
			# 静默修复为成功，不需要用户再次点击登录按钮。
			if self._probe_live_login is None and self._login.get("state") in ("failed", "idle"):
					login_error = self._login.get("error") if isinstance(self._login.get("error"), dict) else None
					# 已确认的官方会话问题不能被旧 TokenStore 自动覆盖：前者需要
					# 用户完成登录，后者只说明磁盘残留凭据仍存在而不能证明 RPA
					# 专用浏览器可用。
					if (login_error or {}).get("code") not in {
						"LOGIN_EXPIRED",
						"RPA_BROWSER_LOGIN_REQUIRED",
					}:
						try:
							if self._has_saved_login():
								self._login = {"state": "succeeded"}
						except Exception:
							pass
			# 列表失败时只会由 _run_conversation_list 写入固定错误码和恢复提示；
			# 在此显式投影，既让页面可以说明下一步，也不将异常原文带出运行时。
			conversation_list = {
				"state": self._conversation_list["state"],
				"items": list(self._conversation_list["items"]),
			}
			if "job_id" in self._conversation_list:
				# 页面轮询必须知道这份快照属于哪个 BOSS 岗位，避免切换
				# 岗位后把另一个异步读取任务的完成结果误显示为当前岗位。
				conversation_list["job_id"] = self._conversation_list["job_id"]
			if "error" in self._conversation_list:
				conversation_list["error"] = dict(self._conversation_list["error"])
			if "notice" in self._conversation_list:
				conversation_list["notice"] = dict(self._conversation_list["notice"])
			if self._conversation_list.get("refreshing") is True:
				conversation_list["refreshing"] = True
			recommendations = {
				"state": self._recommendations["state"],
				"items": list(self._recommendations["items"]),
			}
			for key in ("error", "notice", "job_id"):
				if key in self._recommendations:
					recommendations[key] = self._recommendations[key]
			if self._recommendations.get("refreshing") is True:
				recommendations["refreshing"] = True
			return {
				"operating_mode": self.operating_mode,
				"pipeline": self._pipeline_snapshot(),
				"automation": self._automation_snapshot(),
			"online_resume_preview": dict(self._online_resume_preview),
			"single_analysis": dict(self._single_analysis),
			"analysis_statuses": self._analysis_statuses(),
				"login": dict(self._login),
				"download": dict(self._download),
				"conversation_download": dict(self._conversation_download),
				"conversation_list": conversation_list,
				"conversation_detail": dict(self._conversation_detail_state),
				"recommendation_download": dict(self._recommendation_download),
				"recommendations": recommendations,
				"pacing": self._safe_pacing_status(),
				"recruiting": dict(self._recruiting),
				"batch_export": self._batch_export_snapshot(),
				"recruiting_context": self.recruiting_contexts(),
			}

	def _read_live_login_state(self) -> dict[str, Any]:
		"""读取实时 RPA 登录状态，历史 Cookie 仅用于生成辅助提示。

		实时探测失败时返回 idle 而不是 failed：这代表当前尚未验证成功，
		并非一次登录任务本身报错。这样页面会锁定招聘操作，同时仍允许用户
		点击官方登录入口完成登录；探测器不得把认证材料或底层异常带到页面。
		"""
		if self._probe_live_login is None:
			return {"state": "succeeded" if self._has_saved_login() else "idle"}
		try:
			if self._probe_live_login():
				return {"state": "succeeded"}
		except Exception:
			pass
		try:
			has_saved = self._has_saved_login()
		except Exception:
			has_saved = False
		if has_saved:
			return {"state": "idle", "notice": "检测到历史登录凭据，但当前 RPA 浏览器尚未登录 BOSS"}
		return {"state": "idle"}

	def _require_live_rpa_login(self) -> dict[str, Any] | None:
		"""校验平台写操作前的实时 RPA 登录态。

		招聘自动化会读取候选人消息并可能发送回复，不能只凭本地历史 Cookie
		启动。这里复用状态页同源的实时探测：未登录时直接返回可恢复的阻断结果，
		避免后台线程在登录页或 Chrome 内部缓存上反复执行，最终把底层编码/JSON
		解析异常刷到活动列表。
		"""
		login_state = self._read_live_login_state()
		if login_state.get("state") == "succeeded":
			return None
		return {
			"state": "blocked",
			"error": {
				"code": "RPA_BROWSER_LOGIN_REQUIRED",
				"message": "当前 RPA 浏览器尚未登录 BOSS，请先完成官方登录",
			},
		}

	def _automation_snapshot(self) -> dict[str, Any]:
		"""投影自动化控制与统一队列状态，未配置时明确提示而非伪造空成功。"""
		if self._automation_coordinator is None:
			return {"state": "unsupported", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘自动化"}}
		result = self._automation_coordinator.status()
		result["sync"] = dict(self._automation_sync)
		return result

	def automation_candidates(self, *, job_id: str, qualified_threshold: int = 80) -> dict[str, Any]:
		"""读取指定岗位的统一候选人队列，前端不得自行拼接多份状态。"""
		if self._automation_coordinator is None:
			raise RuntimeError("当前控制台未配置招聘自动化")
		with self._state_lock:
			visible_friend_ids = self._automation_visible_friend_ids_by_job.get(job_id)
		data = self._automation_coordinator.queue.snapshot(
			job_id,
			qualified_threshold=qualified_threshold,
			visible_friend_ids=visible_friend_ids,
		)
		return self._merge_followup_records(data)

	def _merge_followup_records(self, data: dict[str, Any]) -> dict[str, Any]:
		"""将联系方式和面试事实合并到统一候选人投影，避免前端读取第二份状态。"""
		for group in ("candidates", "qualified"):
			rows = data.get(group)
			if not isinstance(rows, list):
				continue
			for row in rows:
				if not isinstance(row, dict):
					continue
				record = self._candidate_followup_store.get(str(row.get("candidate_key") or ""))
				row.update(record.to_dict())
		return data

	def automation_candidate_detail(self, *, job_id: str, candidate_key: str) -> dict[str, Any] | None:
		"""返回一位候选人的自动化过程与已处理 AI 对话，不跨岗位读取。"""
		if self._automation_coordinator is None:
			raise RuntimeError("当前控制台未配置招聘自动化")
		candidate = self._automation_coordinator.queue.candidate_for_job(job_id=job_id, candidate_key=candidate_key)
		if candidate is None:
			return None
		timeline = (
			self._automation_transcript_store.list_for_candidate(job_id=job_id, friend_id=candidate.friend_id)
			if self._automation_transcript_store is not None
			else []
		)
		return {"candidate": candidate.to_dict(), "timeline": timeline}

	def automation_candidate_pool(self, *, qualified_threshold: int = 80) -> dict[str, list[dict[str, Any]]]:
		"""读取全岗位终审候选人池，自动化执行队列不参与此处展示。"""
		if self._automation_coordinator is None:
			raise RuntimeError("当前控制台未配置招聘自动化")
		return self._merge_followup_records({"qualified": self._automation_coordinator.queue.qualified_pool(qualified_threshold=qualified_threshold)})

	def export_automation_candidate_pool(self, *, job_id: str, file_format: str, qualified_threshold: int = 80) -> tuple[bytes, str, str]:
		"""导出当前岗位全部达标候选人，数据源始终是服务端候选人池。"""
		if self._automation_coordinator is None:
			raise RuntimeError("当前控制台未配置招聘自动化")
		if not job_id or file_format not in {"csv", "xlsx"}:
			raise ValueError("岗位或导出格式无效")
		candidates = self._automation_coordinator.queue.qualified_pool(qualified_threshold=qualified_threshold)
		if file_format == "csv":
			return self._candidate_followup_store.export_csv_bytes(job_id=job_id, candidates=candidates), "text/csv; charset=utf-8", f"{job_id}-候选人池.csv"
		return self._candidate_followup_store.export_xlsx_bytes(job_id=job_id, candidates=candidates), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{job_id}-候选人池.xlsx"

	def start_automation(self, *, job_id: str, source: str, limit: int) -> dict[str, Any]:
		"""启动沟通或推荐来源自动化；研究模式外一律阻断平台写操作。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "自动化沟通需要显式启用 research 模式"}}
		if self._automation_coordinator is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘自动化"}}
		if (blocked := self._require_live_rpa_login()) is not None:
			return blocked
		if self._recruiting_workspace is None:
			return {"state": "failed", "error": {"code": "JOB_NOT_READY", "message": "当前控制台未配置岗位管理"}}
		job = self._recruiting_workspace.store.get_job(job_id)
		if job is None:
			return {"state": "failed", "error": {"code": "JOB_NOT_FOUND", "message": "当前岗位不存在，请刷新岗位列表"}}
		try:
			resolve_job_context(job, require_confirmed=True)
		except JobContextError as exc:
			return {"state": "blocked", "error": {"code": "JOB_RULES_UNCONFIRMED", "message": str(exc)}}
		if source == "full_flow":
			return self._automation_coordinator.start_full_flow(
				job_id=job_id,
				conversation_limit=20,
				recommendation_limit=20,
			)
		return self._automation_coordinator.start(job_id=job_id, source=source, limit=limit)

	def pause_automation(self) -> dict[str, Any]:
		"""暂停统一后台队列。"""
		return self._automation_coordinator.pause() if self._automation_coordinator else {"state": "failed"}

	def resume_automation(self) -> dict[str, Any]:
		"""恢复统一后台队列。"""
		return self._automation_coordinator.resume() if self._automation_coordinator else {"state": "failed"}

	def stop_automation(self) -> dict[str, Any]:
		"""停止统一后台队列，候选人事实仍保留以便下次恢复。"""
		return self._automation_coordinator.stop() if self._automation_coordinator else {"state": "failed"}

	def stop_automation_source(self, source: str) -> dict[str, Any]:
		"""结束单一定时来源；手动“停止”按钮仍通过 ``stop_automation`` 结束全部。"""
		return self._automation_coordinator.stop_source(source) if self._automation_coordinator else {"state": "failed"}

	def start_automation_schedule_monitor(self) -> None:
		"""启动服务级定时监控；未配置存储时保持兼容的空操作。"""
		if self._automation_schedule_monitor is not None:
			self._automation_schedule_monitor.start()

	def automation_schedule_settings(self) -> dict[str, dict[str, object]]:
		"""读取两个按钮的独立配置及当前调度状态。"""
		if self._automation_schedule_store is None:
			raise RuntimeError("当前控制台未配置自动化定时任务")
		states = self._automation_schedule_monitor.status() if self._automation_schedule_monitor else {}
		return {
			source: {**settings.to_dict(), "runtime": states.get(source, {"state": "disabled"})}
			for source, settings in self._automation_schedule_store.all().items()
		}

	def save_automation_schedule_settings(self, *, source: str, values: dict[str, object]) -> dict[str, object]:
		"""保存一个按钮的定时配置，不接受浏览器传入运行中状态。"""
		if self._automation_schedule_store is None:
			raise RuntimeError("当前控制台未配置自动化定时任务")
		weekdays_value = values.get("weekdays", [0, 1, 2, 3, 4])
		weekdays = tuple(int(day) for day in weekdays_value) if isinstance(weekdays_value, list) else ()
		settings = AutomationScheduleSettings(
			enabled=values.get("enabled") is True,
			job_id=str(values.get("job_id") or ""),
			start_time=str(values.get("start_time") or "09:00"),
			end_time=str(values.get("end_time") or "18:00"),
			interval_minutes=int(values.get("interval_minutes") or 20),
			limit=int(values.get("limit") or (20 if source == "conversation" else 10)),
			daily_quota=int(values.get("daily_quota") or 100),
			weekdays=weekdays,
		)
		saved = self._automation_schedule_store.save(source=source, settings=settings)
		# 定时自动化会发送消息、处理附件，属于显式平台写操作。仅当用户在本次
		# Web 会话中明确保存“启用”的设置后才启动监控，服务重启或打开在线简历
		# 页面都不会因为磁盘里的历史配置自动恢复旧任务。
		if saved.enabled:
			self.start_automation_schedule_monitor()
		return saved.to_dict()

	def start_automation_sync(self, *, job_id: str) -> dict[str, Any]:
		"""后台执行只读沟通列表同步，不触发 AI 或发送消息。"""
		if self._automation_coordinator is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘自动化"}}
		with self._state_lock:
			pending_sync = self._automation_sync_future
			if pending_sync is not None and not pending_sync.done():
				return dict(self._automation_sync)
			self._automation_sync_generation += 1
			generation = self._automation_sync_generation
			self._automation_sync = {"state": "running", "job_id": job_id}
			started_state = dict(self._automation_sync)
			future = self._task_runner.submit(lambda: self._run_automation_sync(job_id, generation))
			self._automation_sync_future = future
		Thread(
			target=self._watch_automation_sync,
			args=(future, job_id, generation),
			name="boss-web-automation-sync-watchdog",
			daemon=True,
		).start()
		self._workers.append(future)
		# 返回提交瞬间的快照，避免极快完成的 mock/本地读取在 HTTP 响应前
		# 抢先改写状态，破坏调用方一直依赖的“提交即 running”契约。
		return started_state

	def _watch_automation_sync(self, future: Future[None], job_id: str, generation: int) -> None:
		"""监视同步总时限，避免后台任务异常拖延导致状态永久运行。

		Future 无法安全强杀已经进入浏览器调用的线程，因此超时后只取消尚未
		开始的队列任务，并通过代际号阻止迟到结果写回状态。正在执行的旧任务
		仍会由底层连接超时自然退出，但用户界面会立即得到明确的可恢复失败。
		"""
		try:
			future.result(timeout=_AUTOMATION_SYNC_TIMEOUT_SECONDS)
		except FutureTimeoutError:
			future.cancel()
			with self._state_lock:
				if generation != self._automation_sync_generation or future.done():
					return
				self._automation_sync_generation += 1
				self._automation_sync = {
					"state": "failed",
					"error": {
						"code": "CONVERSATION_SYNC_TIMEOUT",
						"message": "BOSS 沟通列表同步超时，后台任务已停止接收结果，请稍后重试",
					},
					"job_id": job_id,
				}
		except Exception:
			# 具体异常由同步任务本身负责投影；监视线程不能覆盖业务错误，
			# 也不能因为 Future 的异常再次制造未处理线程异常日志。
			return

	def _run_automation_sync(self, job_id: str, generation: int) -> None:
		"""隔离同步耗时与异常，确保 Web 事件循环不被 RPA 读取阻塞。"""
		started_at = time.monotonic()
		print(f"[RUNTIME] 沟通列表同步开始 job_id={job_id}", flush=True)
		try:
			# 登录探测同样需要访问 RPA 页面。必须放在后台任务中，避免用户点击
			# “同步沟通列表”时 HTTP 接口被 CDP 重连或页面切换拖住而无任何反馈。
			if (blocked := self._require_live_rpa_login()) is not None:
				result = blocked
				conversation_result = None
				visible_friend_ids = None
			else:
				recorded = self._read_recent_conversation_records(job_id=job_id, fail_if_platform_busy=True)
				print(f"[RUNTIME] 沟通列表读取完成 records={len(recorded)} elapsed={time.monotonic() - started_at:.1f}s", flush=True)
				conversation_result = self._conversation_list_result_from_records(recorded, job_id=job_id)
				synced = self._automation_coordinator.sync_records_once(job_id=job_id, records=recorded) if self._automation_coordinator else 0
				print(f"[RUNTIME] 沟通列表队列写入完成 synced={synced} elapsed={time.monotonic() - started_at:.1f}s", flush=True)
				result = {"state": "succeeded", "synced": synced, "job_id": job_id}
				visible_friend_ids = _visible_friend_ids_from_records(recorded)
		except BossRPALoginRequiredError:
			# 只有 RPA 明确被 BOSS 重定向到登录页时，才提示用户重新登录。
			result = {"state": "failed", "error": {"code": "RPA_BROWSER_LOGIN_REQUIRED", "message": "当前 RPA 浏览器尚未登录 BOSS，请完成登录后刷新"}}
			conversation_result = None
			visible_friend_ids = None
		except BossRPAConnectionError:
			# 未连接目标标签页与账号退出是两种恢复路径，不能继续共用登录提示。
			result = {"state": "failed", "error": {"code": "RPA_TARGET_NOT_READY", "message": "RPA 当前未连接 BOSS 招聘页面，请在已登录的 Chrome 中打开 BOSS 招聘端后重试"}}
			conversation_result = None
			visible_friend_ids = None
		except PermissionError:
			result = {"state": "failed", "error": {"code": "RPA_BROWSER_LOGIN_REQUIRED", "message": "BOSS 登录状态已失效，请完成登录后刷新"}}
			conversation_result = None
			visible_friend_ids = None
		except RuntimeError as exc:
			# 岗位筛选器失败属于页面/岗位上下文问题，应保留已脱敏的恢复原因；
			# 其它 RuntimeError 仍只显示固定文案，避免原始平台响应进入页面。
			message = str(exc).strip()
			platform_busy = message.startswith("BOSS 页面当前正被自动化占用")
			known_context_error = platform_busy or message.startswith(("自动化岗位", "当前 RPA", "BOSS 沟通列表"))
			print(f"[RUNTIME] 沟通列表同步异常：{message[:160]}", flush=True)
			user_message = message[:160] if known_context_error else "BOSS 沟通列表同步处理失败，请查看服务日志后重试"
			result = {
				"state": "failed",
				"error": {
					"code": "CONVERSATION_SYNC_BUSY" if platform_busy else "CONVERSATION_SYNC_FAILED",
					"message": user_message,
				},
			}
			if platform_busy:
				result["job_id"] = job_id
			conversation_result = None
			visible_friend_ids = None
		except Exception as exc:
			# 不能把岗位筛选、DOM 结构和本地队列异常伪装成登录失败。
			# 日志只记录异常类型，不记录候选人消息、简历或平台原始响应。
			print(f"[RUNTIME] 沟通列表同步异常：{type(exc).__name__}: {str(exc)[:200]}", flush=True)
			result = {
				"state": "failed",
				"error": {
					"code": "CONVERSATION_SYNC_FAILED",
					"message": "BOSS 沟通列表同步处理失败，请查看服务日志后重试",
					"detail": type(exc).__name__,
				},
			}
			conversation_result = None
			visible_friend_ids = None
		with self._state_lock:
			if generation != self._automation_sync_generation:
				return
			self._automation_sync = result
			if conversation_result is not None:
				self._conversation_generation += 1
				self._conversation_selections = conversation_result["selections"]
				self._conversation_list = conversation_result["state"]
				self._last_conversation_list_success_at = self._monotonic_clock()
			if visible_friend_ids is not None:
				self._automation_visible_friend_ids_by_job[job_id] = visible_friend_ids

	def open_automation_resume(self, *, candidate_key: str) -> dict[str, Any]:
		"""仅打开队列已验证附件，禁止浏览器借接口读取任意本机路径。"""
		if self._automation_coordinator is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘自动化"}}
		path = self._automation_coordinator.queue.verified_resume_path(candidate_key)
		if path is None:
			return {"state": "failed", "error": {"code": "RESUME_NOT_VERIFIED", "message": "该候选人没有已验证的附件简历"}}
		try:
			os.startfile(str(path))
		except OSError:
			return {"state": "failed", "error": {"code": "OPEN_FAILED", "message": "本地附件无法打开"}}
		return {"state": "succeeded", "path": str(path)}

	def start_online_resume_preview(self, *, selection_id: str, job_id: str) -> dict[str, Any]:
		"""提交一次“刷新列表后打开在线简历”的只预览任务。

		选择标识只用于恢复上一次列表中的 ``friend_id``。后台刷新当前岗位后会再次
		校验该身份仍在快照中，绝不按旧列表下标点击，也不进入附件下载或消息流程。
		"""
		if self._open_online_resume is None or self._list_recent_conversations_for_job is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置在线简历预览"}}
		if (blocked := self._require_live_rpa_login()) is not None:
			return blocked
		with self._state_lock:
			friend_id = self._conversation_selections.get(selection_id)
			if friend_id is None:
				return {"state": "failed", "error": {"code": "SELECTION_EXPIRED", "message": "候选人列表已变化，请先刷新沟通列表"}}
			if self._online_resume_preview.get("state") == "running":
				return dict(self._online_resume_preview)
			self._online_resume_preview = {"state": "running", "job_id": job_id, "selection_id": selection_id}
			started = dict(self._online_resume_preview)
		future = self._task_runner.submit(lambda: self._run_online_resume_preview(job_id=job_id, friend_id=friend_id))
		self._workers.append(future)
		return started

	def _run_online_resume_preview(self, *, job_id: str, friend_id: int) -> None:
		"""在平台串行线程刷新岗位快照、校验身份并打开在线预览。"""
		try:
			with self._platform_operation_lock:
				reader = self._list_recent_conversations_for_job
				if reader is None:
					raise RuntimeError("当前岗位沟通列表读取器未配置")
				records = reader(job_id)
				visible_friend_ids = _visible_friend_ids_from_records(records)
				if friend_id not in visible_friend_ids:
					raise LookupError("候选人已不在当前岗位沟通列表")
				response = self._open_online_resume(friend_id) if self._open_online_resume else {"code": -1}
				if not isinstance(response, dict) or response.get("code") != 0:
					raise RuntimeError("BOSS 在线简历入口未打开")
			resume_text = str(response.get("resume_text") or "").strip()[:20_000]
			if not is_meaningful_online_resume_text(resume_text):
				raise ValueError("BOSS 在线简历正文读取失败")
			# 以刷新后的列表姓名为准；RPA 返回值仅作为页面结构变化时的显示兜底。
			candidate_name = _candidate_name_from_records(records, friend_id) or str(response.get("candidate_name") or "").strip()[:120]
			result: dict[str, Any] = {
				"state": "succeeded",
				"job_id": job_id,
				"friend_id": friend_id,
				"candidate_name": candidate_name,
				"resume_text": resume_text,
			}
		except LookupError:
			result = {"state": "failed", "error": {"code": "CANDIDATE_NOT_FOUND", "message": "候选人已不在当前岗位沟通列表，请刷新后重试"}}
		except BossRPALoginRequiredError:
			result = {"state": "failed", "error": _rpa_browser_login_required_error()}
		except BossRPAConnectionError:
			result = {"state": "failed", "error": _rpa_target_not_ready_error()}
		except Exception:
			result = {"state": "failed", "error": {"code": "ONLINE_RESUME_PREVIEW_FAILED", "message": "BOSS 在线简历预览打开失败，请刷新沟通列表后重试"}}
		with self._state_lock:
			self._online_resume_preview = result

	def automation_interview_settings(self, *, job_id: str) -> dict[str, str]:
		"""读取当前岗位面试设置；默认空值仅用于编辑，不能直接发起邀约。"""
		if self._interview_settings_store is None:
			raise RuntimeError("当前控制台未配置面试设置")
		values = self._interview_settings_store.get(job_id=job_id).to_dict()
		if self._recruiting_workspace is not None:
			job = self._recruiting_workspace.store.get_job(job_id)
			values["greeting_message"] = job.greeting_message if job is not None else ""
		return values

	def automation_followup_settings(self, *, job_id: str) -> dict[str, Any]:
		"""读取当前岗位的联系方式和约面试自动化开关。"""
		return self._candidate_followup_store.settings(job_id).to_dict()

	def save_automation_followup_settings(self, *, job_id: str, values: dict[str, object]) -> dict[str, Any]:
		"""保存岗位级后续动作开关，不影响其它岗位。"""
		settings = CandidateFollowUpSettings(
			phone_enabled=values.get("phone_enabled") in {True, "true", "1", 1},
			wechat_enabled=values.get("wechat_enabled") in {True, "true", "1", 1},
			interview_enabled=values.get("interview_enabled") in {True, "true", "1", 1},
		)
		return self._candidate_followup_store.save_settings(job_id, settings).to_dict()

	def save_automation_interview_settings(self, *, job_id: str, values: dict[str, object]) -> dict[str, str]:
		"""验证后保存当前岗位面试设置，页面不能跨岗位写入配置。"""
		if self._interview_settings_store is None:
			raise RuntimeError("当前控制台未配置面试设置")
		settings = InterviewInvitationSettings(
			mode=str(values.get("mode") or "online"),
			address=str(values.get("address") or ""),
			note=str(values.get("note") or ""),
			date=str(values.get("date") or ""),
			time=str(values.get("time") or ""),
			contact_name=str(values.get("contact_name") or ""),
			contact_phone=str(values.get("contact_phone") or ""),
		)
		result = self._interview_settings_store.save(job_id=job_id, settings=settings).to_dict()
		greeting_message = str(values.get("greeting_message") or "").strip()[:100]
		if self._recruiting_workspace is not None:
			job = self._recruiting_workspace.store.get_job(job_id)
			if job is not None and greeting_message:
				# 设置页只更新话术字段，保留岗位原有发布状态，避免配置招呼语
				# 意外让正在招聘的职位变回草稿。
				job.greeting_message = greeting_message
				self._recruiting_workspace.store.update_job(job)
		result["greeting_message"] = greeting_message
		return result

	def automation_candidate_action_status(self, *, candidate_key: str) -> dict[str, Any]:
		"""返回单候选人最近一次页面动作状态，避免读取其它候选人的执行结果。"""
		with self._state_lock:
			return dict(self._automation_candidate_actions.get(candidate_key, {"state": "idle", "candidate_key": candidate_key}))

	def start_automation_candidate_action(self, *, job_id: str, candidate_key: str, action: str) -> dict[str, Any]:
		"""按当前岗位候选人键提交一项 BOSS 页面动作。

		浏览器只传固定动作名，真实 ``friend_id`` 始终从本地队列中恢复。这样即使
		页面被篡改也无法令后台改操作另一个会话；平台动作排入同一串行线程，避免
		与沟通自动化同时控制 Chrome。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "候选人平台操作需要显式启用 research 模式"}}
		if self._automation_coordinator is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘自动化"}}
		if action not in {"phone", "wechat", "interview"}:
			return {"state": "failed", "error": {"code": "INVALID_ACTION", "message": "候选人操作类型无效"}}
		candidate = self._automation_coordinator.queue.candidate_for_job(job_id=job_id, candidate_key=candidate_key)
		if candidate is None:
			return {"state": "failed", "error": {"code": "NOT_FOUND", "message": "候选人不属于当前岗位或尚未同步"}}
		if (blocked := self._require_live_rpa_login()) is not None:
			return blocked
		payload: dict[str, str] | None = None
		if action == "interview":
			if self._interview_settings_store is None or self._invite_interview is None:
				return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置 BOSS 约面试"}}
			try:
				payload = self._interview_settings_store.get(job_id=job_id).validated().to_dict()
			except ValueError:
				return {"state": "failed", "error": {"code": "INTERVIEW_SETTINGS_REQUIRED", "message": "请先在设置中填写完整的面试日期和时间"}}
		elif self._request_contact_exchange is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置 BOSS 联系方式操作"}}
		with self._state_lock:
			current = self._automation_candidate_actions.get(candidate_key)
			if current and current.get("state") == "running":
				return dict(current)
			state = {"state": "running", "candidate_key": candidate_key, "action": action}
			self._automation_candidate_actions[candidate_key] = state
		self._workers.append(self._task_runner.submit(
			lambda: self._run_automation_candidate_action(
				candidate_key=candidate_key,
				friend_id=candidate.friend_id,
				action=action,
				interview_payload=payload,
			)
		))
		return dict(state)

	def _run_automation_candidate_action(
		self,
		*,
		candidate_key: str,
		friend_id: int,
		action: str,
		interview_payload: dict[str, str] | None,
	) -> None:
		"""在串行 RPA 线程执行单一候选人动作，并收敛页面错误到安全状态。"""
		try:
			if action == "interview":
				response = self._invite_interview(friend_id, interview_payload or {}) if self._invite_interview else {"code": -1}
			else:
				response = self._request_contact_exchange(friend_id, action) if self._request_contact_exchange else {"code": -1}
			if not isinstance(response, dict) or response.get("code") != 0:
				raise RuntimeError("BOSS 页面未确认候选人操作")
			data = response.get("zpData") if isinstance(response.get("zpData"), dict) else {}
			if action in {"phone", "wechat"}:
				value = str(data.get("value") or data.get(action) or "").strip()
				self._candidate_followup_store.update(
					candidate_key,
					**{
						f"{action}_status": "succeeded" if value else "waiting",
						**({action: value} if value else {}),
					},
				)
			elif action == "interview":
				self._candidate_followup_store.update(candidate_key, interview_status="succeeded")
			result: dict[str, Any] = {"state": "succeeded", "candidate_key": candidate_key, "action": action}
		except BossRPALoginRequiredError:
			result = {"state": "failed", "candidate_key": candidate_key, "action": action, "error": _rpa_browser_login_required_error()}
		except Exception as exc:
			print(f"[RUNTIME] 候选人平台操作失败：{type(exc).__name__}", flush=True)
			result = {
				"state": "failed", "candidate_key": candidate_key, "action": action,
				"error": {"code": "CANDIDATE_ACTION_FAILED", "message": "BOSS 候选人操作未完成，请检查页面状态后重试"},
			}
		with self._state_lock:
			self._automation_candidate_actions[candidate_key] = result

	def _batch_export_snapshot(self) -> dict[str, Any]:
		"""复制批量状态；items 必须深拷贝，避免页面读到正在追加的列表。"""
		snapshot = {key: value for key, value in self._batch_export.items() if key != "items"}
		snapshot["items"] = [dict(item) for item in self._batch_export.get("items", [])]
		return snapshot

	def start_batch_export(
		self,
		*,
		source: str,
		limit: int,
		mode: str = MODE_EXPORT,
		job_id: str | None = None,
		output_dir: Path | None = None,
	) -> dict[str, Any]:
		"""启动一批候选人的串行导出或附件扫描。

		批量会连续访问平台，因此这里的门禁比单人导出更严：必须显式 research
		模式、必须有装配好的批量能力、同一时间只允许一批在跑，并且列表正在
		刷新时拒绝启动，避免两条路径同时占用平台客户端。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "批量导出候选人简历需要显式启用 research 模式"},
			}
		if self._batch_export_operation is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置批量导出"}}
		if source not in AVAILABLE_SOURCES or mode not in AVAILABLE_MODES:
			return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "批量导出来源或模式不受支持"}}
		if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
			return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": f"批量数量必须在 1 到 {MAX_LIMIT} 之间"}}
		with self._state_lock:
			if self._batch_export.get("state") == "running":
				return self._batch_export_snapshot()
			if self._conversation_list.get("state") == "running" or self._conversation_list.get("refreshing") is True:
				return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "沟通列表正在刷新，请稍后再启动批量导出"}}
			stop_event = Event()
			self._batch_stop_event = stop_event
			self._batch_export = {
				"state": "running",
				"source": source,
				"mode": mode,
				"requested": limit,
				"processed": 0,
				"succeeded": 0,
				"failed": 0,
				"with_attachment": 0,
				"stopped_reason": "",
				"items": [],
			}
			platform_generation = self._platform_generation
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_batch_export(
					source=source, limit=limit, mode=mode, job_id=job_id, output_dir=output_dir,
					stop_event=stop_event, platform_generation=platform_generation,
				)
			)
		)
		return self._batch_export_snapshot()

	def stop_batch_export(self) -> dict[str, Any]:
		"""请求在下一个候选人边界停批；不等待线程，也不中断进行中的请求。"""
		with self._state_lock:
			if self._batch_export.get("state") != "running":
				return self._batch_export_snapshot()
			if self._batch_stop_event is not None:
				self._batch_stop_event.set()
			self._batch_export["stopping"] = True
			return self._batch_export_snapshot()

	def _run_batch_export(
		self,
		*,
		source: str,
		limit: int,
		mode: str,
		job_id: str | None,
		output_dir: Path | None,
		stop_event: Event,
		platform_generation: int,
	) -> None:
		"""在串行线程上执行整批，并按人增量回写进度。"""

		def progress(item: Any) -> None:
			public = item.to_public_dict()
			with self._state_lock:
				if self._batch_export.get("state") != "running":
					return
				items = self._batch_export.setdefault("items", [])
				items.append(public)
				self._batch_export["processed"] = len(items)
				if public.get("error_code"):
					self._batch_export["failed"] = int(self._batch_export.get("failed", 0)) + 1
				else:
					self._batch_export["succeeded"] = int(self._batch_export.get("succeeded", 0)) + 1
				if item.has_attachment:
					self._batch_export["with_attachment"] = int(self._batch_export.get("with_attachment", 0)) + 1

		result: dict[str, Any]
		login_result: dict[str, Any] | None = None
		try:
			if self._batch_export_operation is None:
				raise RuntimeError("batch export is not configured")
			with self._platform_operation_lock:
				report = self._batch_export_operation(
					source=source, limit=limit, mode=mode, job_id=job_id, output_dir=output_dir,
					stop_event=stop_event, progress=progress,
				)
			result = {"state": "succeeded", **report.to_public_dict()}
		except PermissionError:
			result = {
				"state": "failed",
				"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后继续剩余候选人"},
			}
			login_result = {
				"state": "failed",
				"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请在官方页面重新登录"},
			}
		except BatchTargetReadError as exc:
			# 列表读取失败不能伪装成"没有候选人"：把受控错误码原样带给页面，
			# 用户才知道该重新登录、稍后重试还是换个来源。
			result = {"state": "failed", "error": {"code": exc.code, "message": str(exc)}}
		except Exception:
			# 底层异常可能带认证材料或候选人数据，页面只收到固定恢复提示。
			result = {
				"state": "failed",
				"error": {"code": "BATCH_EXPORT_FAILED", "message": "批量导出失败，请检查登录态和导出目录后重试"},
			}
		with self._state_lock:
			if platform_generation != self._platform_generation:
				return
			previous_items = list(self._batch_export.get("items", []))
			if result.get("state") == "failed":
				# 失败也要保留已经完成的人，否则用户不知道哪些简历已经落盘。
				result = {
					**{key: self._batch_export.get(key) for key in ("source", "mode", "requested")},
					**result,
					"processed": len(previous_items),
					"succeeded": self._batch_export.get("succeeded", 0),
					"failed": self._batch_export.get("failed", 0),
					"with_attachment": self._batch_export.get("with_attachment", 0),
					"items": previous_items,
				}
			self._batch_export = result
			self._batch_stop_event = None
			if login_result is not None:
				self._login = login_result


	def _analysis_statuses(self) -> dict[str, dict[str, Any]]:
		"""返回候选人分析与沟通阶段映射（按 selection_id 索引）。"""
		try:
			raw = self._analysis_tracker.status_map() if self._analysis_tracker else {}
		except Exception:
			raw = {}
		try:
			pipeline_states = self._pipeline_operation.conversation_statuses() if self._pipeline_operation else {}
			if isinstance(pipeline_states, dict):
				raw = {**pipeline_states, **raw}
		except Exception:
			pass
		# 交叉引用：friend_id → selection_id，保护内部 ID 不泄露
		result: dict[str, dict[str, Any]] = {}
		for sel_id, fid in self._conversation_selections.items():
			info = raw.get(fid)
			if info:
				result[sel_id] = info
		return result

	def start_single_analysis(self, *, friend_id: int, candidate_name: str = "") -> dict[str, Any]:
		"""启动单人分析任务。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "单人分析需要显式启用 research 模式"}}
		if self._pipeline_operation is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置流水线"}}
		with self._state_lock:
			if self._single_analysis.get("state") == "running":
				return dict(self._single_analysis)
			self._single_analysis = {"state": "running", "candidate_name": candidate_name, "friend_id": friend_id}
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_single_analysis(friend_id=friend_id, candidate_name=candidate_name)
			)
		)
		return dict(self._single_analysis)

	def start_batch_analysis(self, *, limit: int = 20) -> dict[str, Any]:
		"""启动全局批量分析（只分析未分析过的）。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "批量分析需要显式启用 research 模式"}}
		if self._pipeline_operation is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置流水线"}}
		with self._state_lock:
			if self._pipeline.get("state") == "running":
				return self._pipeline_snapshot()
			self._pipeline = {
				"state": "running", "requested": limit, "threshold": 70,
				"processed": 0, "resumed_sent": 0, "online_downloaded": 0,
				"attachment_downloaded": 0, "analyzed": 0, "pool_added": 0,
				"failed": 0, "stopped_reason": "", "items": [], "logs": [],
			}
			platform_generation = self._platform_generation
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_batch_analysis(
					limit=limit, platform_generation=platform_generation,
				)
			)
		)
		return self._pipeline_snapshot()

	def _run_single_analysis(self, *, friend_id: int, candidate_name: str) -> None:
		"""在后台线程执行单人分析：先处理已有简历，必要时首次索要简历。

		“分析”会交给流水线先检查本地和平台已有资料；无资料且从未发送索要
		消息时才自动打招呼一次。流水线以持久化等待记录、附件接收状态和已落盘
		简历共同阻止重复发送，因此用户重复点击不会造成二次打扰。
		"""
		result: dict[str, Any]
		step: Any | None = None
		try:
			if self._pipeline_operation is None:
				raise RuntimeError("pipeline is not configured")

			print(f"[RUNTIME] 开始分析 {candidate_name} (friend_id={friend_id})", flush=True)
			with self._platform_operation_lock:
				step = self._pipeline_operation.analyze_one(
					friend_id=friend_id, candidate_name=candidate_name,
					ask_for_resume=True,
				)

			item = _step_result_to_dict(step)
			try:
				logs = list(self._pipeline_operation.logger.entries())
			except Exception:
				logs = []
			# 只有真正拿到简历并完成评分才登记“已分析”。等待候选人回复时
			# 保留未分析状态，下一次点击或后台重试仍能继续状态机。
			if step.status == "analyzed":
				self._analysis_tracker.mark_analyzed(
					friend_id, name=candidate_name,
					score=step.score,
					recommendation=step.analysis.recommendation if step.analysis else "review",
				)
			result = {"state": "succeeded", "item": item, "logs": logs}
		except PermissionError:
			result = {"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效"}}
		except Exception as exc:
			result = {"state": "failed", "error": {"code": "ANALYSIS_FAILED", "message": str(exc)[:200]}}
		with self._state_lock:
			self._single_analysis = result

		# 如果消息已发送但没拿到附件，60 秒后自动重试（最多 5 次）
		if step is not None and step.status == "waiting_for_resume" and step.ask_resume_sent:
			retry_count = getattr(self, '_retry_counters', {}).get(friend_id, 0)
			if retry_count < 5:
				self._retry_counters = getattr(self, '_retry_counters', {})
				self._retry_counters[friend_id] = retry_count + 1
				delay = [60, 180, 300, 600, 1200][retry_count]
				print(f"[RUNTIME] {candidate_name} 将在 {delay}s 后自动重试 (第{retry_count+1}次)", flush=True)
				import threading
				t = threading.Timer(delay, lambda: self._retry_analysis(friend_id, candidate_name))
				t.daemon = True
				t.start()

	def _retry_analysis(self, friend_id: int, candidate_name: str) -> None:
		"""自动重试：候选人可能已回复，再次尝试下载附件。"""
		print(f"[RUNTIME] 自动重试 {candidate_name} (fid={friend_id})", flush=True)
		self._run_single_analysis(friend_id=friend_id, candidate_name=candidate_name)

	def _run_batch_analysis(self, *, limit: int, platform_generation: int) -> None:
		"""在后台线程执行全局批量分析（跳过已分析过的）。"""
		def progress(step_result: Any) -> None:
			item = _step_result_to_dict(step_result)
			with self._state_lock:
				if self._pipeline.get("state") != "running":
					return
				items = self._pipeline.setdefault("items", [])
				items.append(item)
				self._pipeline["processed"] = len(items)
				for key in ("online_downloaded", "attachment_downloaded", "analyzed", "pool_added"):
					self._pipeline[key] = sum(1 for r in items if r.get(key))
				self._pipeline["failed"] = sum(1 for r in items if r.get("error"))
			self._analysis_tracker.mark_analyzed(
				step_result.friend_id, name=step_result.candidate_name,
				score=step_result.score,
				recommendation=step_result.analysis.recommendation if step_result.analysis else "review",
			)

		try:
			if self._pipeline_operation is None:
				raise RuntimeError("pipeline is not configured")
			with self._platform_operation_lock:
				response = self._pipeline_operation._platform.friend_list(page=1)
				if not self._pipeline_operation._platform.is_success(response):
					raise RuntimeError("读取沟通列表失败")
				from boss_agent_cli.commands.recruiter.conversation_listing import (
					extract_non_empty_record_list, conversation_items_from_records,
				)
				records = extract_non_empty_record_list(
					self._pipeline_operation._platform.unwrap_data(response) or {}
				)
				all_items = conversation_items_from_records(records, limit=limit)
				all_ids = [
					int(item["friend_id"]) for item in all_items
					if isinstance(item.get("friend_id"), int)
				]
				unanalyzed = self._analysis_tracker.unanalyzed_ids(all_ids) if all_ids else []
				names = {
					int(item["friend_id"]): str(item.get("candidate_name", ""))
					for item in all_items
					if isinstance(item.get("friend_id"), int)
				}
				report = self._pipeline_operation.analyze_batch(
					friend_ids=unanalyzed[:limit],
					candidate_names=names,
					threshold=70,
					# 批量入口和单人“分析”使用同一沟通状态机：已有简历直接
					# 下载，无简历且从未联系过才首次打招呼。状态机自身持久化
					# 等待记录，因此重复执行不会重复发送。
					ask_for_resume=True,
					progress=progress,
				)
			result = report.to_public_dict()
			result["state"] = "succeeded"
			result["skipped_already_analyzed"] = len(all_ids) - len(unanalyzed[:limit])
		except PermissionError:
			result = {"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效"}}
		except Exception as exc:
			result = {"state": "failed", "error": {"code": "BATCH_FAILED", "message": str(exc)[:200]}}
		with self._state_lock:
			if platform_generation != self._platform_generation:
				return
			self._pipeline = result

	def _pipeline_snapshot(self) -> dict[str, Any]:
		"""流水线状态的安全快照（深拷贝 items/logs）。

		调用方必须已持有 _state_lock（与 _batch_export_snapshot 一致）。
		"""
		snap = {key: value for key, value in self._pipeline.items() if key not in ("items", "logs")}
		snap["items"] = [dict(item) for item in self._pipeline.get("items", [])]
		snap["logs"] = [dict(log) for log in self._pipeline.get("logs", [])]
		return snap

	def start_pipeline(self, *, limit: int = 20, threshold: int = 70,
	                   ask_for_resume: bool = True, job_id: str | None = None) -> dict[str, Any]:
		"""启动流水线后台任务。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "流水线需要显式启用 research 模式"}}
		if self._pipeline_operation is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置流水线"}}
		with self._state_lock:
			if self._pipeline.get("state") == "running":
				return self._pipeline_snapshot()
			self._pipeline = {
				"state": "running", "requested": limit, "threshold": threshold,
				"processed": 0, "resumed_sent": 0, "online_downloaded": 0,
				"attachment_downloaded": 0, "analyzed": 0, "pool_added": 0,
				"failed": 0, "stopped_reason": "", "items": [], "logs": [],
			}
			platform_generation = self._platform_generation
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_pipeline(
					limit=limit, threshold=threshold, ask_for_resume=ask_for_resume,
					job_id=job_id, platform_generation=platform_generation,
				)
			)
		)
		return self._pipeline_snapshot()

	def stop_pipeline(self) -> dict[str, Any]:
		"""请求停止流水线。"""
		with self._state_lock:
			if self._pipeline.get("state") != "running":
				return self._pipeline_snapshot()
			self._pipeline["stopping"] = True
			return self._pipeline_snapshot()

	def _run_pipeline(self, *, limit: int, threshold: int, ask_for_resume: bool,
	                  job_id: str | None, platform_generation: int) -> None:
		"""在后台线程执行流水线并增量回写进度。"""
		def progress(step_result: Any) -> None:
			item = _step_result_to_dict(step_result)
			with self._state_lock:
				if self._pipeline.get("state") != "running":
					return
				items = self._pipeline.setdefault("items", [])
				items.append(item)
				self._pipeline["processed"] = len(items)
				for key in ("resumed_sent", "online_downloaded", "attachment_downloaded",
				            "analyzed", "pool_added"):
					self._pipeline[key] = sum(1 for r in items if r.get(key))
				self._pipeline["failed"] = sum(1 for r in items if r.get("error"))

		login_expired = False
		try:
			if self._pipeline_operation is None:
				raise RuntimeError("pipeline is not configured")
			with self._platform_operation_lock:
				report = self._pipeline_operation(
					limit=limit, threshold=threshold, ask_for_resume=ask_for_resume,
					job_id=job_id, progress=progress,
				)
			result = report.to_public_dict() if hasattr(report, 'to_public_dict') else report
			result["state"] = "succeeded"
		except PermissionError:
			result = {"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效"}}
			login_expired = True
		except Exception:
			result = {"state": "failed", "error": {"code": "PIPELINE_FAILED", "message": "流水线执行失败"}}
		with self._state_lock:
			if platform_generation != self._platform_generation:
				return
			previous_items = list(self._pipeline.get("items", []))
			previous_logs = list(self._pipeline.get("logs", []))
			if result.get("state") == "failed":
				result["items"] = previous_items
				result["logs"] = previous_logs
			self._pipeline = result
			if login_expired:
				self._login = self._login_expired_state()

	def start_retry_poller(self) -> None:
		"""启动简历附件重试轮询器。

		重试任务属于服务级后台能力，不能依赖用户是否先打开“沟通列表”页面。
		Web 服务启动后即可启动；方法内部保持幂等，列表首次成功加载时再次调用
		也不会创建第二条线程。
		"""
		self._start_retry_poller_lazy()

	def _start_retry_poller_lazy(self) -> None:
		"""启动后台重试轮询器：每 60 秒检查一次到期重试任务。

		使用独立守护线程（不用 _SerialTaskRunner），因为轮询是永久循环。
		轮询只做低优先级补偿：一次最多处理一个任务，且只尝试非阻塞获取
		平台锁。列表同步、候选人新回复和当前沟通操作拿不到锁时，重试必须
		立即让路，否则“等待附件”的候选人会把整个自动化显示成卡死。
		"""
		with self._state_lock:
			if getattr(self, "_retry_poller_started", False):
				return
			self._retry_poller_started = True
		import threading
		def _poll_loop() -> None:
			import time as _time
			_time.sleep(10)  # 启动后等 10 秒，让首次列表加载先完成
			while True:
				_time.sleep(60)
				try:
					if self._pipeline_operation is None:
						continue
					due = self._pipeline_operation._retry_scheduler.get_due()
					if not due:
						continue
					print(f"[RETRY-POLL] 发现 {len(due)} 个到期重试任务", flush=True)
					if not self._platform_operation_lock.acquire(blocking=False):
						print("[RETRY-POLL] 平台正被沟通流程使用，本轮跳过", flush=True)
						continue
					try:
						results = self._pipeline_operation.process_retries(max_tasks=1)
					finally:
						self._platform_operation_lock.release()
					for r in results:
						if r.status == "analyzed":
							self._analysis_tracker.mark_analyzed(
								r.friend_id, name=r.candidate_name,
								score=r.score,
								recommendation=(
									r.analysis.recommendation
									if r.analysis else "review"
								),
							)
				except Exception as exc:
					print(f"[RETRY-POLL] 轮询异常: {exc}", flush=True)

		threading.Thread(target=_poll_loop, name="boss-web-retry-poller", daemon=True).start()

	def _safe_attachment_statuses(self) -> dict[int, str]:
		"""读取本地附件扫描索引；索引不可用时全部按未检测处理。

		徽标只是辅助信息，任何读取问题都不能让沟通列表本身失败。
		"""
		if self._attachment_statuses is None:
			return {}
		try:
			raw = self._attachment_statuses()
		except Exception:
			return {}
		if not isinstance(raw, dict):
			return {}
		return {
			friend_id: status
			for friend_id, status in raw.items()
			if isinstance(friend_id, int) and not isinstance(friend_id, bool) and isinstance(status, str)
		}

	def _safe_pacing_status(self) -> dict[str, Any]:
		"""读取节奏状态白名单，防止配置提供方误把内部字段送进页面。"""
		if self._pacing_status is None:
			return _default_pacing_status()
		try:
			raw = self._pacing_status()
		except Exception:
			return _default_pacing_status(configured=True, unavailable=True)
		if not isinstance(raw, dict):
			return _default_pacing_status(configured=True, unavailable=True)
		return {key: raw[key] for key in _PACING_STATUS_FIELDS if key in raw}

	def recruiting_contexts(self) -> dict[str, Any]:
		"""返回上下文选择器所需元数据；凭据和平台 Cookie 永不进入快照。"""
		if self._recruiting_context_registry is not None:
			try:
				return self._recruiting_context_registry.as_dict()
			except RuntimeError:
				pass
		return {
			"active_context": self._recruiting_context.to_dict(),
			"contexts": [self._recruiting_context.to_dict()],
		}

	def _has_active_platform_task_unlocked(self) -> bool:
		"""判断是否仍有平台任务在执行或等待，调用方必须已经持有状态锁。"""
		states = (
			self._login,
			self._download,
			self._conversation_download,
			self._conversation_detail_state,
			self._recommendation_download,
		)
		if any(state.get("state") == "running" for state in states):
			return True
		return bool(
			self._conversation_list.get("refreshing") is True
			or self._recommendations.get("refreshing") is True
			or self._conversation_list.get("state") == "running"
			or self._recommendations.get("state") == "running"
		)

	def switch_recruiting_context(self, context: RecruitingContext) -> dict[str, Any]:
		"""切换本地招聘工作区，并使上一个账号的所有平台状态立即失效。

		平台客户端和选择映射都绑定当前账号。切换时如果仍有后台平台任务，
		直接拒绝而不是替换可变闭包中的客户端；任务完成后再切换，能够避免
		旧账号的候选人编号在新账号中被误读。
		"""
		if self._recruiting_workspace_factory is None:
			raise RuntimeError("当前控制台未配置可切换的招聘上下文")
		with self._state_lock:
			if self._recruiting.get("state") == "running":
				raise RuntimeError("当前有招聘工作台操作正在执行，请完成后再切换")
			if self._has_active_platform_task_unlocked():
				raise RuntimeError("当前有平台读取或导出正在执行，请完成后再切换")
		new_workspace = self._recruiting_workspace_factory(context)
		if self._recruiting_context_switcher is not None:
			self._recruiting_context_switcher(context)
		if self._recruiting_context_registry is not None:
			self._recruiting_context_registry.activate(context)
		try:
			next_login_state = "succeeded" if self._has_saved_login() else "idle"
		except Exception:
			# 目标 Profile 的磁盘探测失败时宁可要求一次显式登录，也不能
			# 把上一个账号的成功状态误带到新上下文。
			next_login_state = "idle"
		with self._state_lock:
			self._recruiting_context = context
			self._recruiting_workspace = new_workspace
			self._platform_generation += 1
			self._conversation_generation += 1
			# 切换后清空上一个上下文的操作结果、选择映射和页面缓存，避免旧
			# 候选人短暂闪现，也避免旧 selection_id 在新账号下继续取数。
			self._login = {"state": next_login_state}
			self._download = {"state": "idle"}
			self._conversation_download = {"state": "idle"}
			self._recruiting = {"state": "idle"}
			self._conversation_list = {"state": "idle", "items": []}
			self._conversation_selections = {}
			self._automation_visible_friend_ids_by_job = {}
			self._conversation_detail_state = {"state": "idle"}
			self._last_conversation_list_success_at = None
			self._recommendation_download = {"state": "idle"}
			self._recommendations = {"state": "idle", "items": []}
			self._recommendation_selections = {}
			self._recommendation_job_id = None
			self._last_recommendation_success_at = None
		return self.recruiting_contexts()

	def recruiting_snapshot(self, job_id: str | None = None) -> dict[str, Any]:
		"""读取招聘工作区的脱敏快照，不触发平台访问。"""
		if self._recruiting_workspace is None:
			raise RuntimeError("recruiting workspace is not configured")
		data = self._recruiting_workspace.snapshot(job_id)
		data["context"] = self._recruiting_context.to_dict()
		return data

	def search_recruiting_knowledge(self, job_id: str, query: str) -> dict[str, Any]:
		"""检索岗位知识和 FAQ；这是本地只读操作，不进入后台平台任务。"""
		if self._recruiting_workspace is None:
			raise RuntimeError("recruiting workspace is not configured")
		return self._recruiting_workspace.search_knowledge(job_id, query)

	def answer_recruiting_question(self, job_id: str, question: str) -> dict[str, Any]:
		"""返回当前岗位的本地受控试答，不启动后台线程或平台操作。"""
		if self._recruiting_workspace is None:
			raise RuntimeError("recruiting workspace is not configured")
		return self._recruiting_workspace.answer_question(job_id, question)

	def recruiting_faq_drafts(self, job_id: str) -> dict[str, Any]:
		"""读取岗位 FAQ 待审核草稿；生成过程只读本地知识，不写入 Store。"""
		if self._recruiting_workspace is None:
			raise RuntimeError("recruiting workspace is not configured")
		return {"job_id": job_id, "drafts": self._recruiting_workspace.generate_faq_drafts(job_id)}

	def start_recruiting_optimization_draft(self, *, job_id: str, suggestion_id: str) -> dict[str, Any]:
		"""后台把复盘建议保存为本地待审核草稿，不执行实际优化动作。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"create-optimization-draft",
			lambda: workspace.create_optimization_draft(job_id, suggestion_id),
		)

	def start_recruiting_optimization_draft_review(
		self, *, draft_id: str, status: str, note: str,
	) -> dict[str, Any]:
		"""后台记录复盘草稿审核结果；采纳不会自动改动岗位或平台配置。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"review-optimization-draft",
			lambda: workspace.review_optimization_draft(draft_id, status=status, note=note),
		)

	def _start_recruiting(self, operation: str, callback: RecruitingOperation) -> dict[str, Any]:
		"""串行启动一个本地工作台操作，统一处理错误和状态投影。"""
		if self._recruiting_workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		with self._state_lock:
			if self._recruiting["state"] == "running":
				return dict(self._recruiting)
			self._recruiting = {"state": "running", "operation": operation}
		self._workers.append(self._task_runner.submit(lambda: self._run_recruiting(operation, callback)))
		return {"state": "running"}

	# ------------------------------------------------------------------
	# Template management (lightweight, no recruiting task queue)
	# ------------------------------------------------------------------

	def list_templates(self, *, job_id: str | None = None) -> list[dict[str, str]]:
		"""列出话术模板，可直接读取无需经过任务队列。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return []
		return workspace.list_templates(job_id=job_id)

	def save_template(self, template: Any) -> dict[str, Any]:
		"""保存话术模板，可直接写无需经过任务队列。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		try:
			result = workspace.save_template(template)
			return {"state": "succeeded", "template": result}
		except (ValueError, KeyError) as exc:
			return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": str(exc)}}

	def fetch_boss_jobs(self) -> dict[str, Any]:
		"""读取 BOSS 平台职位列表，返回给页面展示。

		职位列表会作为推荐候选人读取的前置选择数据，因此遵循同一 research
		模式边界；未授权时不触发平台访问，也不把“读取职位”变成旁路。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "读取 BOSS 职位需要显式启用 research 模式"},
			}
		if self._list_boss_jobs is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置 BOSS 职位读取"}}
		try:
			jobs = self._list_boss_jobs()
			return {"state": "succeeded", "items": jobs}
		except BossRPALoginRequiredError:
			return {"state": "failed", "error": _rpa_browser_login_required_error()}
		except BossRPAConnectionError:
			return {"state": "failed", "error": _rpa_target_not_ready_error()}
		except PermissionError:
			return {"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效"}}
		except Exception:
			return {"state": "failed", "error": {"code": "NETWORK_ERROR", "message": "BOSS 职位列表读取失败"}}

	def sync_boss_jobs_to_recruiting_workspace(self) -> dict[str, Any]:
		"""读取当前账号职位并镜像到本地工作台，不执行任何 BOSS 写操作。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "读取 BOSS 职位需要显式启用 research 模式"}}
		if self._list_boss_jobs_for_sync is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置 BOSS 职位读取"}}
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		try:
			items = self._list_boss_jobs_for_sync()
		except BossRPALoginRequiredError:
			return {"state": "failed", "error": _rpa_browser_login_required_error()}
		except BossRPAConnectionError:
			return {"state": "failed", "error": _rpa_target_not_ready_error()}
		except PermissionError:
			return {"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效"}}
		except Exception:
			return {"state": "failed", "error": {"code": "NETWORK_ERROR", "message": "BOSS 职位列表读取失败"}}
		if not items:
			return {"state": "failed", "error": {"code": "NO_BOSS_JOBS", "message": "未从 BOSS 职位管理页读取到职位，请确认当前登录的是招聘账号且职位仍可见"}}
		return {"state": "succeeded", "result": PlatformJobSyncService(workspace.store).sync(items)}

	def delete_template(self, template_id: str) -> dict[str, Any]:
		"""删除话术模板，可直接操作无需经过任务队列。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		existed = workspace.delete_template(template_id)
		return {"state": "succeeded" if existed else "failed", "deleted": existed}

	def start_recruiting_job(
		self,
		*,
		name: str,
		city: str,
		salary_range: str,
		education_requirement: str = "",
		min_experience_years: int | None = None,
		criteria_text: str = "",
		professional_qa_enabled: bool = True,
		greeting_message: str = "",
		status: str = "published",
	) -> dict[str, Any]:
		"""后台创建岗位并解析招聘标准；Web 可显式创建草稿。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"create-job",
			lambda: workspace.create_job(
				name=name,
				city=city,
				salary_range=salary_range,
				education_requirement=education_requirement,
				min_experience_years=min_experience_years,
				criteria_text=criteria_text,
				professional_qa_enabled=professional_qa_enabled,
				greeting_message=greeting_message,
				status=status,
			),
		)

	def start_recruiting_job_status(self, *, job_id: str, status: str) -> dict[str, Any]:
		"""后台发布或归档岗位，只修改本地工作流状态。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		callback = workspace.publish_job if status == "published" else workspace.archive_job
		return self._start_recruiting(
			"publish-job" if status == "published" else "archive-job",
			lambda: callback(job_id),
		)

	def start_recruiting_job_update(
		self,
		*,
		job_id: str,
		name: str,
		city: str,
		salary_range: str,
		education_requirement: str = "",
		min_experience_years: int | None = None,
		criteria_text: str = "",
		professional_qa_enabled: bool | None = None,
		greeting_message: str | None = None,
	) -> dict[str, Any]:
		"""后台更新岗位草稿；保存后必须重新发布。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"update-job",
			lambda: workspace.update_job(
				job_id,
				name=name,
				city=city,
				salary_range=salary_range,
				education_requirement=education_requirement,
				min_experience_years=min_experience_years,
				criteria_text=criteria_text,
				professional_qa_enabled=professional_qa_enabled,
				greeting_message=greeting_message,
			),
		)

	def start_recruiting_knowledge(
		self, *, job_id: str, category: str, title: str, content: str, audience: str = "",
	) -> dict[str, Any]:
		"""后台保存岗位知识库文档。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"add-knowledge",
			lambda: workspace.add_knowledge(
				job_id, category=category, title=title, content=content, audience=audience,
			),
		)

	def start_recruiting_knowledge_import(
		self, *, job_id: str, category: str, source_path: str, audience: str = "",
	) -> dict[str, Any]:
		"""后台导入用户明确选择的岗位知识文件。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"import-knowledge",
			lambda: workspace.import_knowledge(job_id, source_path, category=category, audience=audience),
		)

	def start_recruiting_faq(
		self,
		*,
		job_id: str,
		question: str,
		answer: str,
		allowed_variation: str,
		audience: str = "candidate",
		source_document_id: str = "",
		source_title: str = "",
		source_version: str = "",
	) -> dict[str, Any]:
		"""后台保存经 HR 审核的岗位 FAQ，并保留来源版本。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"add-faq",
			lambda: workspace.add_faq(
				job_id,
				question=question,
				answer=answer,
				allowed_variation=allowed_variation,
				audience=audience,
				source_document_id=source_document_id,
				source_title=source_title,
				source_version=source_version,
			),
		)

	def start_recruiting_candidate_import(
		self,
		*,
		resume_path: str,
		source: str = "local_markdown",
		job_id: str | None = None,
	) -> dict[str, Any]:
		"""后台导入本地简历引用。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"import-candidate",
			lambda: workspace.import_candidate(resume_path, source=source, job_id=job_id),
		)

	def start_recruiting_job_interpretation(
		self,
		*,
		requirements: str,
		job_id: str = "",
		hard_conditions: dict[str, object] | None = None,
	) -> dict[str, Any]:
		"""用自然语言直接设置岗位标准，并保留后续人工编辑能力。

		新岗位默认直接发布到本地评分流程，不产生“等待确认草案”；已有岗位
		更新则复用工作区既有审计规则，修改后需要重新发布才能继续评估。
		"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		clean_job_id = job_id.strip()
		return self._start_recruiting(
			"interpret-job-standard",
			lambda: self._recruiting_job_standard_agent.update_job(
				workspace,
				job_id=clean_job_id,
				requirements=requirements,
				hard_conditions=hard_conditions,
			) if clean_job_id else self._recruiting_job_standard_agent.create_job(
				workspace,
				requirements=requirements,
				hard_conditions=hard_conditions,
			),
		)

	def start_recruiting_job_rule_analysis(self, *, job_id: str, requirements: str) -> dict[str, Any]:
		"""后台分析某个已同步岗位的补充要求，但绝不写入岗位。

		岗位基础字段来自 BOSS 同步，规则编辑器只需要 AI 解析出的四类规则。
		因此本方法先确认岗位存在，再运行纯 ``analyze`` 操作，刻意不调用
		``update_job``，使用户关闭弹窗或放弃保存时不会留下半成品规则。
		"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		clean_job_id = job_id.strip()
		def analyze_rules() -> dict[str, Any]:
			"""读取一次岗位快照后执行纯分析，避免异步过程中重复查询。"""
			current = workspace.store.get_job(clean_job_id)
			if current is None:
				raise KeyError(clean_job_id)
			analysis = self._recruiting_job_standard_agent.analyze(requirements, current_name=current.name)
			return {"job_id": clean_job_id, "analysis": analysis.rule_payload()}

		return self._start_recruiting(
			"analyze-job-rules",
			analyze_rules,
		)

	def start_recruiting_job_rule_apply(
		self,
		*,
		job_id: str,
		rules: dict[str, object],
		scoring: dict[str, object] | None = None,
	) -> dict[str, Any]:
		"""后台保存已由 HR 审核的规则列表，基础字段仍保持只读。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"apply-job-rules",
			lambda: self._recruiting_job_standard_agent.apply_rules(
				workspace,
				job_id=job_id.strip(),
				rules=rules,
				scoring=scoring,
			),
		)

	def start_recruiting_auto_assignment(self, *, directory: str) -> dict[str, Any]:
		"""后台扫描本地简历目录并按最高匹配分自动归入岗位。

		这是纯本地文件与工作台操作，复用既有招聘任务队列只是为了避免 PDF 读取
		阻塞 Web 事件循环；不会登录、读取或写入 BOSS 平台。
		"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"auto-assign-local-resumes",
			lambda: workspace.auto_assign_local_resumes(directory, ai_reviewer=self._recruiting_ai_reviewer),
		)

	def start_recruiting_mismatch_feedback(
		self,
		*,
		job_id: str,
		candidate_id: str,
		reason_code: str,
		stage: str,
		note: str,
	) -> dict[str, Any]:
		"""后台保存不匹配反馈，明确不执行平台提交。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-mismatch-feedback",
			lambda: workspace.record_mismatch_feedback(
				job_id,
				candidate_id,
				reason_code=reason_code,
				stage=stage,
				note=note,
			),
		)

	def start_recruiting_candidate_transition(
		self,
		*,
		candidate_id: str,
		job_id: str = "",
		stage: str,
		action: str,
		note: str,
		ai_judgment: str,
		candidate_quote: str,
	) -> dict[str, Any]:
		"""后台记录候选人阶段；仅保存本地审计，不触发平台动作。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"transition-candidate",
			lambda: workspace.transition_candidate(
				candidate_id,
				job_id=job_id or None,
				stage=stage,
				action=action,
				note=note,
				ai_judgment=ai_judgment,
				candidate_quote=candidate_quote,
			),
		)

	def start_recruiting_answer(
		self,
		*,
		job_id: str,
		candidate_id: str,
		question: str,
		answer: str,
		question_id: str = "",
		question_version: str = "v1",
		source_ids: list[str] | None = None,
		follow_up_of: str = "",
	) -> dict[str, Any]:
		"""后台保存专业回答元数据，回答正文只在本地评分时读取。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-answer",
			lambda: workspace.record_answer(
				job_id,
				candidate_id,
				question=question,
				answer=answer,
				question_id=question_id,
				question_version=question_version,
				source_ids=source_ids,
				follow_up_of=follow_up_of,
			),
		)

	def start_recruiting_private_professional_qa(
		self,
		*,
		job_id: str,
		candidate_id: str,
		question: str,
		answer: str,
		question_id: str = "",
		question_version: str = "v1",
		source_ids: list[str] | None = None,
		outcome: str = "passed",
		note: str = "",
		follow_up_of: str = "",
	) -> dict[str, Any]:
		"""后台保存私域专业核验，并让工作台原子推进对应待办。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-private-professional-qa",
			lambda: workspace.record_private_professional_qa(
				job_id,
				candidate_id,
				question=question,
				answer=answer,
				question_id=question_id,
				question_version=question_version,
				source_ids=source_ids,
				outcome=outcome,
				note=note,
				follow_up_of=follow_up_of,
			),
		)

	def start_recruiting_communication(
		self,
		*,
		job_id: str,
		candidate_id: str,
		round_number: int,
		outcome: str,
		candidate_reply_summary: str,
		note: str,
		next_follow_up_at: str,
		template_key: str = "",
		template_version: str = "",
	) -> dict[str, Any]:
		"""后台保存一轮沟通事实，不执行任何 BOSS 外部动作。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-communication",
			lambda: workspace.record_communication(
				job_id,
				candidate_id,
				round_number=round_number,
				outcome=outcome,
				candidate_reply_summary=candidate_reply_summary,
				note=note,
				next_follow_up_at=next_follow_up_at,
				template_key=template_key,
				template_version=template_version,
			),
		)

	def start_recruiting_message_usage(
		self,
		*,
		job_id: str,
		candidate_id: str = "",
		template_key: str,
		template_version: str = "v1",
		note: str = "",
	) -> dict[str, Any]:
		"""后台保存话术已人工使用的事实，明确不触发 BOSS 发送。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-message-template-usage",
			lambda: workspace.record_message_template_usage(
				job_id,
				candidate_id=candidate_id,
				template_key=template_key,
				template_version=template_version,
				note=note,
			),
		)

	def start_recruiting_assessment(self, *, job_id: str, candidate_id: str) -> dict[str, Any]:
		"""后台生成一份必须人工确认的候选人评估。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"assess-candidate",
			lambda: workspace.assess(
				job_id,
				candidate_id,
				ai_reviewer=self._recruiting_ai_reviewer,
			),
		)

	def start_recruiting_review(
		self,
		*,
		job_id: str,
		candidate_id: str,
		outcome: str,
		note: str,
		manual_override: bool = False,
		override_reason: str = "",
	) -> dict[str, Any]:
		"""后台保存 HR 人工确认结果和可选的人工强制继续理由。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"review-assessment",
			lambda: workspace.review_assessment(
				job_id,
				candidate_id,
				outcome=outcome,
				note=note,
				manual_override=manual_override,
				override_reason=override_reason,
			),
		)

	def start_recruiting_task_update(
		self, *, task_id: str, status: str, note: str, target_stage: str | None = None,
	) -> dict[str, Any]:
		"""后台完成或跳过候选人待办，所有阶段推进仍只发生在本地工作台。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"update-candidate-task",
			lambda: workspace.complete_task(task_id, status=status, note=note, target_stage=target_stage),
		)

	def start_recruiting_basic_intent(self, *, job_id: str, candidate_id: str, note: str) -> dict[str, Any]:
		"""后台保存基础意向人工确认，不执行任何平台动作。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"confirm-basic-intent",
			lambda: workspace.confirm_basic_intent(job_id, candidate_id, note=note),
		)

	def start_recruiting_private_contact(
		self, *, job_id: str = "", candidate_id: str, channel: str, status: str, note: str,
	) -> dict[str, Any]:
		"""后台保存私域联系结果；不调用任何外部加私域动作。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-private-contact",
			lambda: workspace.record_private_contact(
				candidate_id,
				job_id=job_id or None,
				channel=channel,
				status=status,
				note=note,
			),
		)

	def start_recruiting_interview(
		self,
		*,
		job_id: str,
		candidate_id: str,
		scheduled_at: str,
		interviewer: str,
		note: str,
	) -> dict[str, Any]:
		"""后台保存已由 HR 在官方页面完成的面试邀约。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"schedule-interview",
			lambda: workspace.schedule_interview(
				job_id,
				candidate_id,
				scheduled_at=scheduled_at,
				interviewer=interviewer,
				note=note,
			),
		)

	def start_recruiting_interview_result(
		self, *, job_id: str, candidate_id: str, outcome: str, note: str,
	) -> dict[str, Any]:
		"""后台保存面试结果并生成终局决定待办。"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置招聘工作台"}}
		return self._start_recruiting(
			"record-interview-result",
			lambda: workspace.record_interview_result(job_id, candidate_id, outcome=outcome, note=note),
		)

	def _run_recruiting(self, operation: str, callback: RecruitingOperation) -> None:
		"""执行本地工作台回调，并将底层异常转换为安全提示。"""
		try:
			result = callback()
		except KeyError:
			state: dict[str, Any] = {
				"state": "failed",
				"operation": operation,
				"error": {"code": "NOT_FOUND", "message": "岗位或候选人不存在，请刷新工作区后重试"},
			}
		except ValueError as exc:
			# Workspace 只抛出经过固定字段校验的提示；不把路径或正文写入响应。
			state = {"state": "failed", "operation": operation, "error": {"code": "INVALID_PARAM", "message": str(exc)}}
		except Exception:
			state = {"state": "failed", "operation": operation, "error": {"code": "WORKSPACE_FAILED", "message": "招聘工作台操作失败，请检查本地数据后重试"}}
		else:
			state = {"state": "succeeded", "operation": operation, "result": result}
		with self._state_lock:
			self._recruiting = state

	def start_login(self, *, reuse_ready: bool = False) -> dict[str, Any]:
		"""启动官方页面登录任务；可选地复用当前已成功的登录态。

		CLI/内部调用默认保留“显式重新确认”的语义，便于用户主动刷新
		会话；Web 控制台传 ``reuse_ready=True``，避免页面轮询或误点击
		在已有有效状态下重复打开官方登录页。平台读取一旦明确返回过期，
		运行时会先把状态置为 failed，下一次 Web 请求自然会进入登录流程。
		"""
		with self._state_lock:
			if self._login["state"] == "running":
				return dict(self._login)
			previous_state = str(self._login.get("state"))
			# 只有实时探测已经明确成功时才允许复用当前页面。历史 Cookie
			# 不代表当前 RPA Chrome 可用，不能再作为跳过官方登录的条件。
			login_error = self._login.get("error") if isinstance(self._login.get("error"), dict) else None
			if reuse_ready and self._probe_live_login is None and self._has_saved_login() and (login_error or {}).get("code") != "LOGIN_EXPIRED":
				self._login = {"state": "succeeded"}
				return dict(self._login)
			self._login = {"state": "running"}
		self._workers.append(self._task_runner.submit(lambda: self._run_login(previous_state=previous_state)))
		return {"state": "running"}

	def start_open_login_page(self) -> dict[str, Any]:
		"""异步打开官方 BOSS 登录页，不等待用户完成扫码或账号确认。

		页面按钮的职责只是确保可见的官方登录页已经出现。把它和登录确认拆开后，
		用户可以立即看到操作结果；后续状态轮询仍只信任 RPA 的实时登录探测。
		"""
		with self._state_lock:
			if self._login["state"] == "running":
				return dict(self._login)
			self._login = {"state": "running", "notice": "正在打开 BOSS 登录页"}
		self._workers.append(self._task_runner.submit(self._run_open_login_page))
		return {"state": "running"}

	def start_conversation_list(self, *, force: bool = False, job_id: str | None = None) -> dict[str, Any]:
		"""读取最近沟通候选人，默认复用已成功的本地快照。

		页面首次载入与浏览器刷新都会调用此入口。如果每次都访问平台，紧邻的
		重复请求可能被平台节流，继而用失败状态覆盖刚取得的候选人列表。因此仅在
		用户明确点击刷新时通过 ``force`` 发起新的平台读取；候选人选择映射也随
		成功刷新一并更新，保持页面选择与后台内部 ID 的边界不变。
		"""
		clean_job_id = job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "读取沟通列表需要显式启用 research 模式"}}
		if self._list_recent_conversations is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置沟通列表"}}
		previous_items: list[dict[str, str]] | None = None
		with self._state_lock:
			current_job_id = self._conversation_list.get("job_id")
			if self._conversation_list["state"] == "running":
				return {"state": "running"}
			if self._conversation_list["state"] == "succeeded" and not force and current_job_id == clean_job_id:
				return {"state": "succeeded"}
			if self._conversation_list["state"] == "succeeded" and current_job_id == clean_job_id:
				last_success_at = self._last_conversation_list_success_at
				elapsed = self._monotonic_clock() - last_success_at if last_success_at is not None else None
				if force and elapsed is not None and elapsed < _CONVERSATION_LIST_REFRESH_INTERVAL_SECONDS:
					# 冷却阶段复用当前映射，用户仍可直接下载已显示的候选人资料。
					self._conversation_list["notice"] = {
						"code": "REFRESH_COOLDOWN",
						"message": "列表刚刚更新，请稍后再刷新",
					}
					return {"state": "succeeded"}
				previous_items = list(self._conversation_list["items"])
				# 强制刷新期间继续展示旧快照，避免网络波动使用户瞬间失去可操作列表。
				self._conversation_list = {"state": "succeeded", "items": previous_items, "job_id": clean_job_id, "refreshing": True}
			else:
				self._conversation_list = {"state": "running", "items": [], "job_id": clean_job_id}
			# 每次真正发起平台列表读取都淘汰旧详情选择，后台详情任务
			# 即使排在刷新之后，也必须通过代际检查才能写回状态。
			self._conversation_generation += 1
			conversation_generation = self._conversation_generation
			platform_generation = self._platform_generation
		self._workers.append(
			self._task_runner.submit(
			lambda: self._run_conversation_list(
				previous_items=previous_items,
				job_id=clean_job_id,
				conversation_generation=conversation_generation,
				platform_generation=platform_generation,
			)
		)
		)
		return {"state": "running"}

	def _read_recent_conversation_records(
		self,
		*,
		job_id: str | None = None,
		fail_if_platform_busy: bool = False,
	) -> list[dict[str, object]]:
		"""在平台锁内读取沟通列表，并为自动化同步选择岗位专用读取器。

		无岗位参数用于人工刷新，保留用户手动浏览全部职位的语义；携带岗位参数
		时只接受已注入的岗位读取器，不能静默回退到全职位列表。
		"""
		if self._list_recent_conversations is None:
			raise RuntimeError("conversation list is not configured")
		job_reader = self._list_recent_conversations_for_job
		if job_id and job_reader is None:
			raise RuntimeError("conversation list is not configured for the selected job")
		# 自动化同步与页面刷新都访问同一个 RPA 页面。统一经过平台锁，避免
		# 一个任务正在切换聊天上下文时，另一个任务同时刷新列表造成快照漂移。
		# 同步按钮是只读补偿入口；若全流程已经占用页面，立即返回忙碌状态，
		# 不能让用户误以为是 CDP 卡死并等待整整 120 秒。
		if fail_if_platform_busy:
			if not self._platform_operation_lock.acquire(blocking=False):
				raise RuntimeError("BOSS 页面当前正被自动化占用，本次同步未执行，请等待当前步骤完成后重试")
			acquired = True
		else:
			acquired = False
		try:
			if not fail_if_platform_busy:
				self._platform_operation_lock.acquire()
			acquired = True
			if job_id:
				# 上方已显式拒绝未配置岗位读取器的同步请求，局部变量在此处
				# 保持非空，避免可选成员在并发读取中被错误地当作可调用对象。
				if job_reader is None:
					raise RuntimeError("conversation list is not configured for the selected job")
				return job_reader(job_id)
			return self._list_recent_conversations()
		finally:
			if acquired:
				self._platform_operation_lock.release()

	def _conversation_list_result_from_records(self, recorded: list[dict[str, object]], *, job_id: str | None = None) -> dict[str, Any]:
		"""把内部沟通记录投影成页面快照和短生命周期选择映射。

		这里是唯一允许把 ``friend_id`` 转为不透明 ``selection_id`` 的边界。
		自动化同步也复用此投影，确保“同步 40 位”时页面展示的候选人正是本次
		RPA 读取到的列表，而不是上一次页面刷新或本地历史队列。
		"""
		selections: dict[str, int] = {}
		items: list[dict[str, Any]] = []
		attachment_statuses = self._safe_attachment_statuses()
		for item in recorded:
			friend_id = item.get("friend_id")
			if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
				continue
			selection_id = secrets.token_urlsafe(18)
			selections[selection_id] = friend_id
			public_item: dict[str, Any] = {
				"selection_id": selection_id,
				"candidate_name": str(item.get("candidate_name") or "（未命名候选人）"),
				"updated_at": str(item.get("updated_at") or "-"),
				# 徽标由服务端按会话标识查本地扫描索引得出；页面仍然只看到
				# selection_id，拿不到 friend_id，也无法反推候选人身份。
				"attachment_badge": attachment_statuses.get(friend_id, STATUS_UNKNOWN),
			}
			# 命令层已经完成平台字段映射，这里再做一次白名单投影，
			# 防止以后新增字段意外把 friend_id、URL 或消息正文带到页面。
			for key in ("position", "company", "city"):
				value = item.get(key)
				if isinstance(value, str) and value.strip():
					public_item[key] = value.strip()
			unread = item.get("unread_count")
			if isinstance(unread, int) and not isinstance(unread, bool) and unread >= 0:
				public_item["unread_count"] = min(unread, 999)
			items.append(public_item)
		return {"state": {"state": "succeeded", "items": items, "job_id": job_id}, "selections": selections}

	def start_recommendations(self, *, job_id: str | None = None, force: bool = False) -> dict[str, Any]:
		"""读取推荐牛人列表，并为页面建立短生命周期的候选人选择映射。

		推荐列表和沟通列表都属于招聘者个人资料读取，必须显式启用 research
		模式。刷新默认受 60 秒冷却约束；用户切换职位时允许立即读取新职位，
		但仍然只处理单页数据，避免一次点击演变成批量采集。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "读取推荐牛人需要显式启用 research 模式"}}
		if self._list_recommendations is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置推荐牛人读取"}}
		clean_job_id = job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
		previous_items: list[dict[str, object]] | None = None
		with self._state_lock:
			if self._recommendations["state"] == "running":
				return {"state": "running"}
			if self._recommendations["state"] == "succeeded" and not force and clean_job_id == self._recommendation_job_id:
				return {"state": "succeeded"}
			if self._recommendations["state"] == "succeeded":
				last_success_at = self._last_recommendation_success_at
				elapsed = self._monotonic_clock() - last_success_at if last_success_at is not None else None
				if force and clean_job_id == self._recommendation_job_id and elapsed is not None and elapsed < _RECOMMENDATION_LIST_REFRESH_INTERVAL_SECONDS:
					self._recommendations["notice"] = {
						"code": "REFRESH_COOLDOWN",
						"message": "推荐列表刚刚更新，请稍后再刷新",
					}
					return {"state": "succeeded"}
				previous_items = list(self._recommendations["items"])
				self._recommendations = {
					"state": "succeeded", "items": previous_items, "job_id": clean_job_id, "refreshing": True,
				}
			else:
				self._recommendations = {"state": "running", "items": [], "job_id": clean_job_id}
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_recommendations(previous_items=previous_items, job_id=clean_job_id)
			)
		)
		return {"state": "running"}

	def start_selected_recommendation_download(
		self, *, selection_id: str, job_id: str | None = None, output: Path | None, output_dir: Path | None,
	) -> dict[str, Any]:
		"""按页面选择标识导出推荐候选人的在线简历和已分享附件。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "下载推荐候选人简历需要显式启用 research 模式"}}
		if self._download_recommendation is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置推荐候选人导出"}}
		with self._state_lock:
			candidate = self._recommendation_selections.get(selection_id)
			if candidate is None:
				return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "推荐列表已更新，请重新选择后下载"}}
			if not all(isinstance(candidate.get(key), str) and str(candidate[key]).strip() for key in ("geek_id", "job_id", "security_id")):
				return {"state": "failed", "error": {"code": "RESUME_NOT_FOUND", "message": "该候选人的简历定位信息不完整，暂时无法导出"}}
			if self._recommendation_download["state"] == "running":
				return dict(self._recommendation_download)
			self._recommendation_download = {"state": "running"}
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_recommendation_download(
					candidate=candidate, workspace_job_id=job_id, output=output, output_dir=output_dir,
				)
			)
		)
		return {"state": "running"}

	def start_selected_conversation_download(
		self, *, selection_id: str, job_id: str | None = None, output: Path | None, output_dir: Path | None,
	) -> dict[str, Any]:
		"""依据不透明选择标识下载对应候选人资料，不接收内部会话 ID。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "下载沟通简历需要显式启用 research 模式"}}
		with self._state_lock:
			if self._conversation_list.get("state") == "running" or self._conversation_list.get("refreshing") is True:
				return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "沟通列表正在刷新，请稍后重新选择"}}
			friend_id = self._conversation_selections.get(selection_id)
		if friend_id is None:
			return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "候选人列表已更新，请重新选择后下载"}}
		return self.start_conversation_download(friend_id=friend_id, job_id=job_id, output=output, output_dir=output_dir)

	def start_selected_conversation_detail(self, *, selection_id: str) -> dict[str, Any]:
		"""按不透明选择标识读取一位候选人的官方卡片上下文。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "读取沟通详情需要显式启用 research 模式"}}
		if self._conversation_detail is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置沟通详情读取"}}
		with self._state_lock:
			if self._conversation_list.get("state") == "running" or self._conversation_list.get("refreshing") is True:
				return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "沟通列表正在刷新，请稍后重新选择"}}
			friend_id = self._conversation_selections.get(selection_id)
			if friend_id is None:
				return {"state": "failed", "error": {"code": "INVALID_PARAM", "message": "候选人列表已更新，请重新选择后读取"}}
			if self._conversation_detail_state.get("state") == "running":
				return dict(self._conversation_detail_state)
			self._conversation_detail_state = {"state": "running", "selection_id": selection_id}
			conversation_generation = self._conversation_generation
			platform_generation = self._platform_generation
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_conversation_detail(
					selection_id=selection_id,
					friend_id=friend_id,
					conversation_generation=conversation_generation,
					platform_generation=platform_generation,
				)
			)
		)
		return {"state": "running"}

	def start_current_chat_download(
		self, *, job_id: str | None = None, output: Path | None, output_dir: Path | None,
	) -> dict[str, Any]:
		"""从官方沟通页读取当前会话后启动导出。

		会话编号只在后台线程中短暂存在，随后直接传给既有导出服务；它不会进入
		浏览器请求体、页面表单或运行时状态，从而让用户无需接触内部定位参数。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "从当前沟通会话下载简历需要显式启用 research 模式"},
			}
		if self._download_conversation_resume is None or self._current_chat_friend_id is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置当前会话简历导出"}}
		with self._state_lock:
			if self._conversation_download["state"] == "running":
				return dict(self._conversation_download)
			self._conversation_download = {"state": "running"}
		self._workers.append(
			self._task_runner.submit(lambda: self._run_current_chat_download(workspace_job_id=job_id, output=output, output_dir=output_dir))
		)
		return {"state": "running"}

	def start_latest_conversation_download(
		self, *, job_id: str | None = None, output: Path | None, output_dir: Path | None,
	) -> dict[str, Any]:
		"""导出平台当前排序最靠前的沟通会话，供默认页面入口使用。"""
		if self.operating_mode != RESEARCH_MODE:
			return {"state": "blocked", "error": {"code": "COMPLIANCE_BLOCKED", "message": "下载最近沟通简历需要显式启用 research 模式"}}
		if self._download_conversation_resume is None or self._latest_conversation_friend_id is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置最近会话简历导出"}}
		with self._state_lock:
			if self._conversation_download["state"] == "running":
				return dict(self._conversation_download)
			self._conversation_download = {"state": "running"}
		self._workers.append(
			self._task_runner.submit(lambda: self._run_latest_conversation_download(workspace_job_id=job_id, output=output, output_dir=output_dir))
		)
		return {"state": "running"}

	def start_conversation_download(
		self, *, friend_id: int, job_id: str | None = None, output: Path | None, output_dir: Path | None,
	) -> dict[str, Any]:
		"""启动一个会话资料导出任务，仅允许显式研究模式。

		它使用单独状态供页面展示，但与普通下载共用运行锁，避免同一登录态下同时
		触发两份候选人个人资料读取和文件写入。
		"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "从沟通会话下载简历需要显式启用 research 模式"},
			}
		if self._download_conversation_resume is None:
			return {"state": "failed", "error": {"code": "NOT_SUPPORTED", "message": "当前控制台未配置会话简历导出"}}
		with self._state_lock:
			if self._conversation_download["state"] == "running":
				return dict(self._conversation_download)
			self._conversation_download = {"state": "running"}
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_conversation_download(friend_id=friend_id, workspace_job_id=job_id, output=output, output_dir=output_dir)
			)
		)
		return {"state": "running"}

	def start_download(
		self,
		*,
		geek_id: str,
		job_id: str,
		security_id: str,
		workspace_job_id: str | None = None,
		output: Path | None,
		output_dir: Path | None,
	) -> dict[str, Any]:
		"""启动一份候选人简历下载，严格保留现有研究模式边界。"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "下载在线简历需要显式启用 research 模式"},
			}
		with self._state_lock:
			if self._download["state"] == "running":
				return dict(self._download)
			self._download = {"state": "running"}
		self._workers.append(
			self._task_runner.submit(
				lambda: self._run_download(
					geek_id=geek_id,
					job_id=job_id,
					security_id=security_id,
					workspace_job_id=workspace_job_id,
					output=output,
					output_dir=output_dir,
				)
			)
		)
		return {"state": "running"}

	def wait_for_idle(self, timeout: float) -> None:
		"""供测试等待当前后台任务结束，生产请求不应阻塞等待。"""
		for worker in tuple(self._workers):
			try:
				worker.result(timeout=timeout)
			except FutureTimeoutError:
				continue

	def _run_login(self, *, previous_state: str) -> None:
		"""执行官方浏览器登录，并把任意底层异常收敛为安全状态。"""
		with self._login_lock:
			result: dict[str, Any]
			try:
				self._login_in_browser()
			except Exception:
				# 用户可能已在另一个官方页面完成登录，随后又点了确认按钮。确认
				# 流程超时并不必然意味着既有 TokenStore 已失效。只有任务开始前
				# 已经是成功态时才保留成功，避免“失败态 + 残留旧 Cookie”再次
				# 被误报为已登录，从而让用户陷入重复点击登录的循环。
				result = {"state": "succeeded"} if previous_state == "succeeded" and self._has_saved_login() else {
					"state": "failed", "error": {"code": "LOGIN_FAILED", "message": _SAFE_LOGIN_FAILURE_MESSAGE},
				}
			else:
				result = {"state": "succeeded"}
		with self._state_lock:
			self._login = result

	def _run_open_login_page(self) -> None:
		"""执行打开页面任务，并把失败收敛为可读的本地控制台状态。"""
		try:
			self._open_login_page()
		except Exception:
			result = {"state": "failed", "error": {"code": "LOGIN_PAGE_OPEN_FAILED", "message": "BOSS 登录页打开失败，请检查专用 RPA Chrome 后重试"}}
		else:
			# 打开页面不会改变既有登录会话。若实时 RPA 已经验证通过，必须立即
			# 保留成功态，避免用户看到“已登录”却又被页面提示重新登录。
			result = self._read_live_login_state()
			if result.get("state") != "succeeded":
				result = {"state": "page_opened", "notice": "BOSS 登录页已打开，请在专用 RPA 浏览器中完成登录后刷新状态"}
		with self._state_lock:
			self._login = result

	def _run_conversation_list(
		self,
		*,
		previous_items: list[dict[str, Any]] | None,
		job_id: str | None,
		conversation_generation: int,
		platform_generation: int,
	) -> None:
		"""构造严格白名单候选人列表与短生命周期选择映射。

		代际参数用于丢弃切换账号或再次刷新后才完成的旧任务，避免旧平台
		结果覆盖当前页面状态。
		"""
		fetched_successfully = False
		try:
			recorded = self._read_recent_conversation_records(job_id=job_id)
			conversation_result = self._conversation_list_result_from_records(recorded, job_id=job_id)
			selections = conversation_result["selections"]
			result = conversation_result["state"]
		except BossRPALoginRequiredError:
			# 日常浏览器的登录不会自动共享给隔离 profile。这里明确标记专用
			# 浏览器需要登录，不能误写为账号失效，也不能继续显示空候选人列表。
			result = {"state": "failed", "items": [], "job_id": job_id, "error": _rpa_browser_login_required_error()}
			login_result = {"state": "failed", "error": _rpa_browser_login_required_error()}
		except BossRPAConnectionError:
			# RPA 连接的是其他本地项目页面时，不能把它当作 BOSS 登录过期，
			# 更不能落成“成功但 0 人”。保留现有登录状态，避免用户已在 BOSS
			# 登录却因浏览器上下文接错而被迫重复登录。
			result = {"state": "failed", "items": [], "job_id": job_id, "error": _rpa_target_not_ready_error()}
			login_result = None
		except PermissionError:
			# 命令层只在平台明确反馈认证失效时抛出 PermissionError。不能继续
			# 信任启动时读到的本地凭据，必须让页面重新显示官方登录入口。
			result: dict[str, Any] = {
				"state": "failed",
				"items": [], "job_id": job_id,
				"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后刷新列表"},
			}
			login_result: dict[str, Any] | None = {
				"state": "failed",
				"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请在官方页面重新登录"},
			}
		except Exception as exc:
			# 平台短暂拒绝或网络波动不能毁掉已经成功显示的候选人列表。底层异常
			# 可能包含认证材料或候选人数据，故页面只收到固定的恢复提示。
			if self._is_login_expired_error(exc):
				result = {
					"state": "failed",
					"items": [], "job_id": job_id,
					"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后刷新列表"},
				}
				login_result = self._login_expired_state()
			elif previous_items is not None:
				result = {
					"state": "succeeded",
					"items": previous_items, "job_id": job_id,
					"notice": {"code": "REFRESH_FAILED", "message": "刷新列表失败，仍显示上次成功结果"},
				}
				login_result = None
			else:
				result = {"state": "failed", "items": [], "job_id": job_id, "error": {"code": "NETWORK_ERROR", "message": "沟通列表读取失败，请检查登录状态后重试"}}
				login_result = None
		else:
			login_result = None
			fetched_successfully = True
		with self._state_lock:
			if (
				platform_generation != self._platform_generation
				or conversation_generation != self._conversation_generation
			):
				return
			if fetched_successfully:
				self._conversation_selections = selections
				self._conversation_detail_state = {"state": "idle"}
				self._last_conversation_list_success_at = self._monotonic_clock()
			if login_result is not None:
				self._login = login_result
			self._conversation_list = result

	def _run_conversation_detail(
		self,
		*,
		selection_id: str,
		friend_id: int,
		conversation_generation: int,
		platform_generation: int,
	) -> None:
		"""在共享平台锁内读取单条详情，并再次过滤页面可见字段。"""
		login_expired = False
		with self._platform_operation_lock:
			try:
				if self._conversation_detail is None:
					raise RuntimeError("conversation detail is not configured")
				raw = self._conversation_detail(friend_id)
				data = self._conversation_detail_public_item(raw)
			except PermissionError:
				login_expired = True
				result: dict[str, Any] = {
					"state": "failed",
					"selection_id": selection_id,
					"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后重试"},
				}
			except LookupError:
				result = {
					"state": "failed",
					"selection_id": selection_id,
					"error": {"code": "NOT_FOUND", "message": "未找到该候选人的 BOSS 卡片信息，请刷新列表后重试"},
				}
			except Exception as exc:
				if self._is_login_expired_error(exc):
					login_expired = True
					result = {
						"state": "failed",
						"selection_id": selection_id,
						"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后重试"},
					}
				else:
					result = {
						"state": "failed",
						"selection_id": selection_id,
						"error": {"code": "NETWORK_ERROR", "message": "候选人卡片信息读取失败，请检查登录状态后重试"},
					}
			else:
				result = {"state": "succeeded", "selection_id": selection_id, "data": data}
		with self._state_lock:
			if (
				platform_generation != self._platform_generation
				or conversation_generation != self._conversation_generation
				or self._conversation_selections.get(selection_id) != friend_id
			):
				return
			if login_expired:
				self._login = self._login_expired_state()
			self._conversation_detail_state = result

	@staticmethod
	def _conversation_detail_public_item(value: dict[str, object]) -> dict[str, object]:
		"""只保留候选人对应所需的职位、公司、城市和未读字段。"""
		if not isinstance(value, dict):
			raise ValueError("conversation detail must be a mapping")
		public: dict[str, object] = {}
		for key in ("candidate_name", "position", "company", "city", "updated_at"):
			item = value.get(key)
			if isinstance(item, str) and item.strip():
				public[key] = item.strip()
		unread = value.get("unread_count")
		if isinstance(unread, int) and not isinstance(unread, bool) and unread >= 0:
			public["unread_count"] = min(unread, 999)
		return public

	def _run_recommendations(self, *, previous_items: list[dict[str, object]] | None, job_id: str | None) -> None:
		"""读取并投影推荐卡片；底层响应只在后台线程中短暂存在。"""
		fetched_successfully = False
		try:
			if self._list_recommendations is None:
				raise RuntimeError("recommendation list is not configured")
			with self._platform_operation_lock:
				recorded = self._list_recommendations(job_id)
			selections: dict[str, dict[str, object]] = {}
			items: list[dict[str, object]] = []
			for item in recorded[:20]:
				if not isinstance(item, dict):
					continue
				selection_id = secrets.token_urlsafe(18)
				selections[selection_id] = dict(item)
				public_item = self._recommendation_public_item(item)
				public_item["selection_id"] = selection_id
				items.append(public_item)
		except PermissionError:
			result: dict[str, Any] = {
				"state": "failed", "items": [], "job_id": job_id,
				"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后刷新推荐列表"},
			}
			login_result: dict[str, Any] | None = {
				"state": "failed", "error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请在官方页面重新登录"},
			}
		except Exception:
			if previous_items is not None:
				result = {
					"state": "succeeded", "items": previous_items, "job_id": job_id,
					"notice": {"code": "REFRESH_FAILED", "message": "刷新推荐列表失败，仍显示上次成功结果"},
				}
			else:
				result = {
					"state": "failed", "items": [], "job_id": job_id,
					"error": {"code": "NETWORK_ERROR", "message": "推荐牛人列表读取失败，请检查登录状态后重试"},
				}
			login_result = None
		else:
			result = {"state": "succeeded", "items": items, "job_id": job_id}
			login_result = None
			fetched_successfully = True
		with self._state_lock:
			if fetched_successfully:
				self._recommendation_selections = selections
				self._recommendation_job_id = job_id
				self._last_recommendation_success_at = self._monotonic_clock()
			if login_result is not None:
				self._login = login_result
			self._recommendations = result

	@staticmethod
	def _recommendation_public_item(item: dict[str, object]) -> dict[str, object]:
		"""将候选人内部记录投影为固定的页面白名单字段。"""
		def text(key: str, fallback: str = "-") -> str:
			value = item.get(key)
			return value.strip() if isinstance(value, str) and value.strip() else fallback

		can_download = all(isinstance(item.get(key), str) and str(item[key]).strip() for key in ("geek_id", "job_id", "security_id"))
		return {
			"candidate_name": text("candidate_name", "（未命名候选人）"),
			"title": text("title"),
			"city": text("city"),
			"experience": text("experience"),
			"degree": text("degree"),
			"salary": text("salary"),
			"active_time": text("active_time"),
			"company": text("company"),
			"source": "recommendation",
			"can_download": can_download,
			"download_hint": "" if can_download else "平台未提供完整的简历定位信息",
		}

	def _run_recommendation_download(
		self, *, candidate: dict[str, object], workspace_job_id: str | None, output: Path | None, output_dir: Path | None,
	) -> None:
		"""在共享平台锁内执行推荐候选人资料导出，并只保留安全元数据。"""
		login_expired = False
		with self._platform_operation_lock:
			try:
				if self._download_recommendation is None:
					raise RuntimeError("recommendation download is not configured")
				exported = self._download_recommendation(
					geek_id=str(candidate["geek_id"]),
					job_id=str(candidate["job_id"]),
					security_id=str(candidate["security_id"]),
					friend_id=candidate.get("friend_id"),
					output=output,
					output_dir=output_dir,
				)
			except Exception as exc:
				login_expired = self._is_login_expired_error(exc)
				result: dict[str, Any] = {"state": "failed", "error": self._recommendation_download_error(exc)}
			else:
				metadata = self._recommendation_export_metadata(exported)
				self._attach_workspace_import(metadata, exported.online_resume.path, source="boss_recommendation", job_id=workspace_job_id)
				result = {"state": "succeeded", "result": metadata}
		with self._state_lock:
			if login_expired:
				self._login = self._login_expired_state()
			self._recommendation_download = result

	@staticmethod
	def _recommendation_download_error(exc: Exception) -> dict[str, str]:
		"""将推荐导出异常收敛为不含候选人数据和 URL 的恢复提示。"""
		if isinstance(exc, ConversationResumeNotFoundError):
			return {"code": "RESUME_NOT_FOUND", "message": "该推荐候选人没有可用的简历信息"}
		if isinstance(exc, ConversationResumePlatformError):
			return {"code": "NETWORK_ERROR", "message": "推荐候选人资料读取失败，请检查登录状态后重试"}
		if isinstance(exc, ResumeDownloadExportError):
			return {"code": "EXPORT_FAILED", "message": "在线简历已读取，但保存文件失败，请检查输出目录后重试"}
		if isinstance(exc, ResumeDownloadPlatformError):
			if exc.code in {"LOGIN_EXPIRED", "TOKEN_REFRESH_FAILED"}:
				return {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后重试"}
			if exc.code == "RATE_LIMITED":
				return {"code": "RATE_LIMITED", "message": "平台暂时限制请求，请稍后再试"}
			return {"code": "DOWNLOAD_FAILED", "message": "推荐候选人在线简历获取失败，请稍后重试"}
		return {"code": "DOWNLOAD_FAILED", "message": "推荐候选人简历下载失败，请检查登录状态和输出目录后重试"}

	def _run_download(
		self,
		*,
		geek_id: str,
		job_id: str,
		security_id: str,
		workspace_job_id: str | None,
		output: Path | None,
		output_dir: Path | None,
	) -> None:
		"""调用共享服务并仅把 ResumeExportResult 投影到浏览器状态。"""
		login_expired = False
		with self._platform_operation_lock:
			try:
				exported = self._download_resume(
					geek_id=geek_id,
					job_id=job_id,
					security_id=security_id,
					output=output,
					output_dir=output_dir,
				)
			except Exception as exc:
				login_expired = self._is_login_expired_error(exc)
				result: dict[str, Any] = {"state": "failed", "error": {"code": "DOWNLOAD_FAILED", "message": _SAFE_DOWNLOAD_FAILURE_MESSAGE}}
			else:
				metadata = self._export_metadata(exported)
				self._attach_workspace_import(metadata, exported.path, source="boss_conversation", job_id=workspace_job_id)
				result = {"state": "succeeded", "result": metadata}
		with self._state_lock:
			if login_expired:
				self._login = self._login_expired_state()
			self._download = result

	@staticmethod
	def _is_login_expired_error(exc: Exception) -> bool:
		"""判断领域错误是否明确表示官方会话已经失效。"""
		return getattr(exc, "code", None) in {"LOGIN_EXPIRED", "TOKEN_REFRESH_FAILED"}

	@staticmethod
	def _login_expired_state() -> dict[str, Any]:
		"""生成统一的可重登录状态，供所有下载入口复用。"""
		return {
			"state": "failed",
			"error": {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请在官方页面重新登录"},
		}

	def _run_conversation_download(
		self, *, friend_id: int, workspace_job_id: str | None, output: Path | None, output_dir: Path | None,
	) -> None:
		"""调用会话导出服务，并把结果收敛为页面可展示的白名单元数据。"""
		login_expired = False
		with self._platform_operation_lock:
			try:
				if self._download_conversation_resume is None:
					raise RuntimeError("conversation download is not configured")
				exported = self._download_conversation_resume(
					friend_id=friend_id, output=output, output_dir=output_dir,
				)
			except Exception as exc:
				login_expired = self._is_login_expired_error(exc)
				result: dict[str, Any] = {
					"state": "failed",
					"error": self._conversation_download_error(exc),
				}
			else:
				metadata = self._conversation_export_metadata(exported)
				self._attach_workspace_import(metadata, exported.online_resume.path, source="boss_conversation", job_id=workspace_job_id)
				result = {"state": "succeeded", "result": metadata}
		with self._state_lock:
			if login_expired:
				self._login = self._login_expired_state()
			self._conversation_download = result

	def _run_current_chat_download(
		self, *, workspace_job_id: str | None, output: Path | None, output_dir: Path | None,
	) -> None:
		"""在平台操作锁内读取当前会话并导出，避免 CDP 并发访问。"""
		login_expired = False
		with self._platform_operation_lock:
			result: dict[str, Any]
			try:
				if self._download_conversation_resume is None or self._current_chat_friend_id is None:
					raise RuntimeError("current conversation download is not configured")
				friend_id = self._current_chat_friend_id()
				if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
					raise ValueError("current conversation id is invalid")
				exported = self._download_conversation_resume(
					friend_id=friend_id, output=output, output_dir=output_dir,
				)
			except LookupError:
				# 当前会话不存在属于用户可恢复状态，不应伪装成认证或落盘故障；同时
				# 丢弃底层异常，避免页面泄露平台实现细节或候选人数据。
				result = {
					"state": "failed",
					"error": {"code": "DOWNLOAD_FAILED", "message": "请先在 BOSS 官方沟通页选中候选人后重试"},
				}
			except Exception as exc:
				login_expired = self._is_login_expired_error(exc)
				result = {
					"state": "failed",
					"error": self._conversation_download_error(exc),
				}
			else:
				metadata = self._conversation_export_metadata(exported)
				self._attach_workspace_import(metadata, exported.online_resume.path, source="boss_conversation", job_id=workspace_job_id)
				result = {"state": "succeeded", "result": metadata}
		with self._state_lock:
			if login_expired:
				self._login = self._login_expired_state()
			self._conversation_download = result

	def _run_latest_conversation_download(
		self, *, workspace_job_id: str | None, output: Path | None, output_dir: Path | None,
	) -> None:
		"""在共享平台操作锁内解析最近会话并复用现有导出服务。"""
		login_expired = False
		with self._platform_operation_lock:
			result: dict[str, Any]
			try:
				if self._download_conversation_resume is None or self._latest_conversation_friend_id is None:
					raise RuntimeError("latest conversation download is not configured")
				friend_id = self._latest_conversation_friend_id()
				if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
					raise ValueError("latest conversation id is invalid")
				exported = self._download_conversation_resume(friend_id=friend_id, output=output, output_dir=output_dir)
			except Exception as exc:
				login_expired = self._is_login_expired_error(exc)
				result = {"state": "failed", "error": self._conversation_download_error(exc)}
			else:
				metadata = self._conversation_export_metadata(exported)
				self._attach_workspace_import(metadata, exported.online_resume.path, source="boss_conversation", job_id=workspace_job_id)
				result = {"state": "succeeded", "result": metadata}
		with self._state_lock:
			if login_expired:
				self._login = self._login_expired_state()
			self._conversation_download = result

	@staticmethod
	def _conversation_download_error(exc: Exception) -> dict[str, str]:
		"""把会话导出领域异常映射为固定、安全且可操作的页面提示。

		领域异常可能源自 HTTP、CDP 或文件系统，原文有机会包含候选人字段、
		签名 URL 或本地绝对路径，因此 Web 层只允许有限类型和固定文案穿过边界。
		"""
		if isinstance(exc, ConversationResumeNotFoundError):
			return {"code": "RESUME_NOT_FOUND", "message": "沟通会话没有可用简历信息，请刷新列表后重试"}
		if isinstance(exc, ConversationResumePlatformError):
			if exc.code in {"LOGIN_EXPIRED", "TOKEN_REFRESH_FAILED"}:
				return {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后重试"}
			return {"code": "NETWORK_ERROR", "message": "沟通会话读取失败，请检查登录状态后重试"}
		if isinstance(exc, ResumeDownloadExportError):
			return {"code": "EXPORT_FAILED", "message": "在线简历已读取，但保存文件失败，请检查输出目录后重试"}
		if isinstance(exc, ResumeDownloadPlatformError):
			if exc.code in {"LOGIN_EXPIRED", "TOKEN_REFRESH_FAILED"}:
				return {"code": "LOGIN_EXPIRED", "message": "BOSS 登录已失效，请重新登录后重试"}
			if exc.code == "RATE_LIMITED":
				return {"code": "RATE_LIMITED", "message": "平台暂时限制请求，请稍后再试"}
			if exc.code == "PLATFORM_RESPONSE_INVALID":
				return {"code": "PLATFORM_RESPONSE_INVALID", "message": "在线简历返回格式异常，请稍后重试"}
			return {"code": "DOWNLOAD_FAILED", "message": "在线简历获取失败，请稍后重试"}
		return {"code": "DOWNLOAD_FAILED", "message": _SAFE_DOWNLOAD_FAILURE_MESSAGE}

	@staticmethod
	def _export_metadata(exported: ResumeExportResult) -> dict[str, Any]:
		"""显式白名单导出元数据，防止以后新增字段意外进入 Web 响应。"""
		return {
			"geek_id": exported.geek_id,
			"candidate_name": exported.candidate_name,
			"path": str(exported.path),
			"filename": exported.filename,
			"bytes_written": exported.bytes_written,
			"sections": exported.sections,
			"exported_at": exported.exported_at,
		}

	@staticmethod
	def _recommendation_export_metadata(exported: RecommendationResumeExportResult) -> dict[str, Any]:
		"""投影推荐导出结果，排除平台定位字段、正文和附件 URL。"""
		attachment: dict[str, Any] = {"status": exported.attachment.status}
		if exported.attachment.status == "downloaded":
			attachment.update({
				"filename": exported.attachment.filename,
				"path": str(exported.attachment.path),
				"bytes_written": exported.attachment.bytes_written,
			})
		return {
			"candidate_name": exported.candidate_name,
			"online_resume": {
				"filename": exported.online_resume.filename,
				"path": str(exported.online_resume.path),
				"bytes_written": exported.online_resume.bytes_written,
				"sections": exported.online_resume.sections,
			},
			"attachment": attachment,
		}

	def _attach_workspace_import(
		self, metadata: dict[str, Any], path: Path, *, source: str, job_id: str | None = None,
	) -> None:
		"""把已落盘简历登记到本地工作台，并将失败变成可恢复元数据。

		导出和工作台登记属于两个独立的本地边界：文件已经成功写入时，即使
		工作区 JSON 暂时不可写，也不能把一次成功的平台读取误报成下载失败。
		因此这里只写固定状态和下一步提示，不把底层路径异常或简历正文带回页面。
		"""
		workspace = self._recruiting_workspace
		if workspace is None:
			return
		try:
			candidate = workspace.import_candidate(path, source=source, job_id=job_id)
		except Exception:
			metadata["workspace_import"] = {
				"state": "failed",
				"message": "简历已保存，但自动进入招聘工作台失败；导入失败时可手动重试",
			}
			return
		handoff: dict[str, Any] = {
			"state": "imported",
			"candidate_id": candidate["candidate_id"],
			"candidate_name": candidate["name"],
			"stage": candidate["stage"],
			"source": candidate["source"],
			"job_id": job_id or "",
			"next_action": "选择岗位并生成简历评估",
		}
		# 导入和工作流投影是两个本地步骤：导入成功不能只给一个泛化的
		# “生成评估”文案，否则候选人已存在、已绑定其他岗位或已进入终局时，
		# 页面会把用户引到错误动作。这里读取一次安全快照，只投影当前候选人的
		# 待办元数据，不返回简历正文、路径、哈希或平台内部定位参数。
		try:
			workspace_snapshot = workspace.snapshot(job_id)
		except Exception:
			workspace_snapshot = None
		if isinstance(workspace_snapshot, dict):
			candidate_row = next(
				(
					row
					for row in workspace_snapshot.get("candidates", [])
					if isinstance(row, dict) and row.get("candidate_id") == candidate["candidate_id"]
				),
				None,
			)
			if isinstance(candidate_row, dict):
				for key in ("next_action", "pending_task_id", "pending_task_kind", "pending_task_title"):
					value = candidate_row.get(key)
					if isinstance(value, (str, int, float)) and str(value).strip():
						handoff[key] = str(value)
		metadata["workspace_import"] = handoff

	@staticmethod
	def _conversation_export_metadata(exported: ConversationResumeExportResult) -> dict[str, Any]:
		"""投影会话导出结果，拒绝 URL、内部定位参数和文件内容进入状态。"""
		attachment: dict[str, Any] = {"status": exported.attachment.status}
		if exported.attachment.status == "downloaded":
			attachment.update({
				"filename": exported.attachment.filename,
				"path": str(exported.attachment.path),
				"bytes_written": exported.attachment.bytes_written,
			})
		return {
			"candidate_name": exported.candidate_name,
			"online_resume": {
				"filename": exported.online_resume.filename,
				"path": str(exported.online_resume.path),
				"bytes_written": exported.online_resume.bytes_written,
				"sections": exported.online_resume.sections,
			},
			"attachment": attachment,
		}


def _step_result_to_dict(result: Any) -> dict[str, Any]:
    """将 PipelineStepResult 投影为白名单 dict。"""
    data: dict[str, Any] = {
        "candidate_name": getattr(result, "candidate_name", ""),
        "status": getattr(result, "status", "pending"),
        "online_resume_downloaded": getattr(result, "online_resume_downloaded", False),
        "attachment_downloaded": getattr(result, "attachment_downloaded", False),
        "attachment_available": getattr(result, "attachment_available", False),
        "ask_resume_sent": getattr(result, "ask_resume_sent", False),
        "score": getattr(result, "score", 0),
        "pool_added": getattr(result, "pool_added", False),
        "error": str(getattr(result, "error", ""))[:100],
    }
    analysis = getattr(result, "analysis", None)
    if analysis is not None:
        data.update({
            "analysis_recommendation": getattr(analysis, "recommendation", ""),
            "analysis_source": getattr(analysis, "source", ""),
            "strengths": list(getattr(analysis, "strengths", ())),
            "gaps": list(getattr(analysis, "gaps", ())),
            "follow_up_questions": list(getattr(analysis, "follow_up_questions", ())),
        })
    return data
