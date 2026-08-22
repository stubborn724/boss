"""招聘自动化协调器。

协调器将 BOSS 沟通列表、推荐牛人、AI 对话和附件终审收敛为一个按岗位运行的
串行任务。平台适配器通过窄回调注入，领域层不依赖 CDP、Click 或 Web 框架，因而
能用确定性测试验证“未回复换人、回复后恢复、推荐回流”的调度规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from pathlib import Path
from threading import Event, RLock, Thread
from time import sleep
from typing import Any, Callable

from boss_agent_cli.recruiting.automation_queue import (
	AutomationCandidateStage,
	AutomationCandidateUpsert,
	AutomationQueueStore,
)
from boss_agent_cli.recruiting.recommendation_quota import (
	RecommendationDailyQuotaReached,
	RecommendationQuotaStore,
)
from boss_agent_cli.recruiting.unicode_safety import sanitize_unicode_text


@dataclass(frozen=True, slots=True)
class ConversationSeed:
	"""从 BOSS 沟通列表同步到本地队列的最小记录。"""

	friend_id: int
	candidate_name: str = ""
	source: str = "conversation"
	last_message_id: str = ""
	# 未读数只属于本轮平台快照，用于回复优先级，不落入长期队列状态。
	unread_count: int = 0
	# 卡片版本是浏览器端根据列表摘要生成的不可逆值，不携带消息正文。
	# 当用户手动打开过聊天、导致 BOSS 未读红点被清除时，仍可据此发现变化。
	conversation_version: str = ""


def _conversation_records_to_seeds(records: list[dict[str, object]]) -> list[ConversationSeed]:
	"""把平台原始沟通记录收敛为队列可保存的最小身份。

	Web 运行时只负责读取和脱敏展示，不应理解自动化队列字段；协调器在领域层
	统一处理 ``friend_id`` 校验、姓名兜底和来源约束，使页面刷新、手动同步和
	后台轮询不会各自维护一套隐式转换规则。
	"""
	seeds: list[ConversationSeed] = []
	for record in records:
		friend_id = record.get("friend_id")
		if isinstance(friend_id, bool) or not isinstance(friend_id, int) or friend_id <= 0:
			continue
		seeds.append(
			ConversationSeed(
				friend_id=friend_id,
				candidate_name=str(record.get("candidate_name") or ""),
				source="conversation",
				last_message_id=str(record.get("last_message_id") or ""),
				unread_count=_safe_unread_count(record.get("unread_count")),
				conversation_version=str(record.get("conversation_version") or "").strip()[:128],
			)
		)
	return seeds


def _safe_unread_count(value: object) -> int:
	"""把平台未读字段收敛为非负整数，防止异常数据破坏排序。"""
	return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


@dataclass(frozen=True, slots=True)
class AutomationCandidateEvent:
	"""一次已完成平台操作的脱敏结果，供协调器更新队列。"""

	friend_id: int
	stage: AutomationCandidateStage
	action: str
	reason_codes: tuple[str, ...] = ()
	score: int | None = None
	recommendation: str = ""
	resume_path: Path | None = None
	last_message_id: str = ""


class AutomationPhaseError(RuntimeError):
	"""标记自动化失败所在阶段，避免底层异常在控制台中失去上下文。

	BOSS 页面操作由多个步骤组成，单独显示 ``OSError`` 或 ``timed out`` 无法判断
	是列表同步、消息读取还是附件下载失败。该异常只携带阶段名、异常类型和受限
	长度的异常摘要，不写入候选人消息、简历正文或平台完整响应，便于恢复操作又
	不扩大敏感数据的日志范围。
	"""

	def __init__(self, phase: str, cause: Exception) -> None:
		self.phase = phase
		self.cause = cause
		detail = str(cause).strip()[:160] or type(cause).__name__
		super().__init__(f"{phase}: {type(cause).__name__}: {detail}")


@dataclass(frozen=True, slots=True)
class DialogueWorkPlan:
	"""一个轮询周期内允许打开的会话集合。

	``friend_ids`` 是实际交给 RPA 的会话顺序；``first_check_ids`` 则记录其中来自
	当前全量快照、尚未进入过自动化处理的新候选人。两者分开保存，才能在没有
	产生 AI 事件时仍将首次检查的会话移出新人队列，避免每 20 秒反复点开同一人。
	"""

	friend_ids: tuple[int, ...]
	first_check_ids: tuple[int, ...]


ConversationSync = Callable[[str], list[ConversationSeed]]
ConversationRecordSync = Callable[[str, list[dict[str, object]]], list[ConversationSeed]]
DialogueProcessor = Callable[[str, tuple[int, ...], Event], list[AutomationCandidateEvent]]
AttachmentFinalizer = Callable[[str, tuple[int, ...], Event], list[AutomationCandidateEvent]]
RecommendationGreeter = Callable[[str, int, Event], list[str]]
RuntimeGuard = Callable[[], str | None]
CandidateFollowUpRunner = Callable[[str], int]


class AutomationCoordinator:
	"""执行一次或持续执行招聘自动化的应用服务。

	每个周期都先同步沟通列表，再处理已回流的真实会话；推荐入口仅发送招呼并
	记录活动，绝不在缺少 ``friend_id`` 时创建候选人记录。后台循环可暂停与停止，
	但队列事实持续落盘，重启后再次开始即可从原阶段续跑。
	"""
	# 平台未必可靠返回未读红点或摘要版本。低频恢复核对用于补偿这类漏报，
	# 但不能每轮重新打开大量等待会话，否则会重新造成页面抖动和无效等待。
	_RECOVERY_INTERVAL_CYCLES = 3
	# 附件已发送是候选人明确回流的强信号。每轮优先给这类会话两个 RPA 名额，
	# 既能及时点击“同意”并下载，也不会让附件下载长时间挤占全部沟通处理能力。
	_MAX_ATTACHMENT_CHECKS_PER_CYCLE = 2
	# 无新消息时五分钟复查一次，避免 20 秒轮询持续点开同一聊天；平台未读
	# 或列表版本变化属于即时信号，不受该退避影响。
	_ATTACHMENT_RETRY_DELAY = timedelta(minutes=5)

	def __init__(
		self,
		*,
		queue: AutomationQueueStore,
		sync_conversations: ConversationSync,
		sync_recent_conversations: ConversationSync | None = None,
		sync_records: ConversationRecordSync | None = None,
		process_dialogue: DialogueProcessor,
		finalize_attachments: AttachmentFinalizer,
		greet_recommendations: RecommendationGreeter,
		candidate_followups: CandidateFollowUpRunner | None = None,
		recommendation_quota: RecommendationQuotaStore | Any | None = None,
		runtime_guard: RuntimeGuard | None = None,
		platform_operation_lock: Any | None = None,
		poll_interval_seconds: float = 20.0,
		hard_stop_at: datetime | None = None,
		clock: Callable[[], datetime] | None = None,
	) -> None:
		self.queue = queue
		self._sync_conversations = sync_conversations
		# 首轮需要完整快照以固定当前周期顺序；后续轮询只需读取首屏未读会话。
		# 该回调可选，未接入的平台保持旧版全量同步语义以兼容命令行和领域测试。
		self._sync_recent_conversations = sync_recent_conversations
		# 每个岗位各自保存一次全量快照顺序。后台轮询期间只读取首屏未读，
		# 但“下一位新人”必须沿着这份固定顺序继续，不能被首屏旧会话卡住。
		self._snapshot_orders: dict[str, tuple[int, ...]] = {}
		# 最近增量快照发现的列表版本变化。它是单轮调度信号，不能持久化为
		# BOSS 未读，否则候选人未回复时会被无限重复打开。
		self._changed_conversation_ids: dict[str, set[int]] = {}
		# 每个岗位独立记录恢复轮询次数和快照游标，保证恢复核对按页面顺序轮转，
		# 不会一直重复命中快照顶部的同一批等待会话。
		self._recovery_cycles: dict[str, int] = {}
		self._recovery_cursors: dict[str, int] = {}
		self._attachment_cursors: dict[str, int] = {}
		# Web 同步按钮已经拿到一份实时 BOSS 列表时，应把这份快照直接交给队列
		# 入库，避免协调器再读一次平台导致“页面列表”和“自动化队列”来自两次
		# 不同时间的 RPA 结果。未注入时使用默认字段投影，便于纯领域测试复用。
		self._sync_records = sync_records
		self._process_dialogue = process_dialogue
		self._finalize_attachments = finalize_attachments
		self._greet_recommendations = greet_recommendations
		# 后续动作必须在附件终审写入队列之后执行。回调只接收岗位 ID，由应用层
		# 从同一份队列读取达标候选人，既不会把未达标人员带入 RPA，也能在下一轮
		# 继续处理等待联系方式或约面试的退避任务。
		self._candidate_followups = candidate_followups
		# 推荐额度是账号级事实而非岗位队列状态。它只影响推荐发送阶段，沟通和
		# 附件终审仍沿用既有节奏执行，避免一个岗位触顶后全流程被意外停止。
		self._recommendation_quota = recommendation_quota
		# 启动时的登录校验只能覆盖当前瞬间；后台轮询可能运行很久，BOSS 页面
		# 期间掉线或被用户切走时，必须在每轮平台动作前重新拦截并给出可恢复
		# 文案，避免把 JSON/编码等底层异常刷到活动列表。
		self._runtime_guard = runtime_guard
		# Web 页面刷新、简历下载和自动化循环共享同一个 RPA 页面。将锁从
		# Web 运行时注入领域协调器，而不是在各个回调中各自加锁，才能覆盖
		# “检查登录态 -> 读取列表 -> 打开会话 -> 下载附件”的完整临界区。
		# 领域测试不注入时使用空上下文，保持协调器可独立运行。
		self._platform_operation_lock = platform_operation_lock
		self._poll_interval_seconds = max(5.0, poll_interval_seconds)
		# 截止时间是本次运行的硬边界。它由 Web 入口按本地日期注入，协调器在
		# 平台读取、对话和附件操作之间复检，避免仅依赖外部脚本而在重启后失效。
		self._hard_stop_at = hard_stop_at
		self._clock = clock or (lambda: datetime.now(timezone.utc))
		# 控制接口会在持锁状态下返回快照；可重入锁避免 start/pause 的快照读取
		# 反向等待自身，从而把“暂停”按钮永久卡住。
		self._lock = RLock()
		self._stop_event = Event()
		# 暂停和停止语义不同：停止结束后台线程；暂停保留线程与队列事实，但必须
		# 向正在处理批次的回调发出协作式中断信号，防止用户点击暂停后仍连续
		# 打开后续聊天、发送消息或下载附件。
		self._pause_event = Event()
		self._paused = False
		self._worker: Thread | None = None
		self._state: dict[str, object] = {"state": "idle", "job_id": "", "sources": [], "activities": []}

	def start(self, *, job_id: str, source: str, limit: int) -> dict[str, object]:
		"""启动单一后台循环；重复点击只返回当前任务状态。"""
		if source not in {"conversation", "recommendation"}:
			raise ValueError("自动化来源无效")
		if not job_id.strip():
			raise ValueError("必须选择岗位")
		with self._lock:
			# 已有沟通循环时，推荐上限只能拒绝“追加推荐来源”，不能覆盖正在
			# 运行的沟通状态或清空其活动记录。
			if self._worker is not None and self._worker.is_alive():
				if source == "recommendation" and self._recommendation_is_blocked():
					return {
						**self.status(),
						"error": {
							"code": "RECOMMENDATION_DAILY_QUOTA_REACHED",
							"message": self._recommendation_message(),
						},
					}
				sources = self._state_sources()
				sources.add(source)
				self._state["sources"] = sorted(sources)
				source_limits = self._state_source_limits()
				source_limits[source] = limit
				self._state["source_limits"] = source_limits
				return self.status()
			if source == "recommendation" and self._recommendation_is_blocked():
				return self._recommendation_blocked_state(job_id=job_id, sources=[source])
			if (deadline_message := self._hard_stop_message()) is not None:
				# 截止后不得创建后台线程，更不能为了确认状态再读取一次 BOSS 页面。
				self._stop_event.set()
				self._state = {"state": "blocked", "job_id": job_id, "sources": [source], "limit": limit, "activities": []}
				self._add_activity(action="自动化未启动", status="blocked", detail=deadline_message)
				return {**self.status(), "error": {"code": "AUTOMATION_DEADLINE_REACHED", "message": deadline_message}}
			self._stop_event.clear()
			self._pause_event.clear()
			self._paused = False
			# 每次重新启动都是一个新的自动化周期，应以点击后的完整沟通列表为准。
			# 运行中的轮询不会清空该顺序，因此中途新增会话仍会留给下一周期。
			self._snapshot_orders.pop(job_id, None)
			self._recovery_cycles.pop(job_id, None)
			self._recovery_cursors.pop(job_id, None)
			self._attachment_cursors.pop(job_id, None)
			self._state = {"state": "running", "job_id": job_id, "sources": [source], "limit": limit, "source_limits": {source: limit}, "activities": []}
			self._worker = Thread(target=self._run_loop, name="recruiting-automation", daemon=True)
			self._worker.start()
			return self.status()

	def start_full_flow(self, *, job_id: str, conversation_limit: int = 20, recommendation_limit: int = 20) -> dict[str, object]:
		"""启动沟通优先的全流程；两个来源共享一个线程，绝不并发操作平台。"""
		if not job_id.strip():
			raise ValueError("必须选择岗位")
		with self._lock:
			if (deadline_message := self._hard_stop_message()) is not None:
				self._stop_event.set()
				self._state = {"state": "blocked", "job_id": job_id, "sources": ["conversation", "recommendation"], "full_flow": True, "activities": []}
				self._add_activity(action="全流程未启动", status="blocked", detail=deadline_message)
				return {**self.status(), "error": {"code": "AUTOMATION_DEADLINE_REACHED", "message": deadline_message}}
			if self._worker is not None and self._worker.is_alive():
				return self.status()
			self._stop_event.clear()
			self._pause_event.clear()
			self._paused = False
			self._snapshot_orders.pop(job_id, None)
			self._recovery_cycles.pop(job_id, None)
			self._recovery_cursors.pop(job_id, None)
			self._attachment_cursors.pop(job_id, None)
			recommendation_blocked = self._recommendation_is_blocked()
			self._state = {
				"state": "running",
				"job_id": job_id,
				"sources": ["conversation"] if recommendation_blocked else ["conversation", "recommendation"],
				"full_flow": True,
				"recommendation_blocked": recommendation_blocked,
				"source_limits": {"conversation": max(1, conversation_limit), "recommendation": max(1, recommendation_limit)},
				"activities": [],
			}
			if recommendation_blocked:
				self._add_activity(action="推荐牛人今日已停止", status="blocked", detail=self._recommendation_message())
			self._worker = Thread(target=self._run_loop, name="recruiting-full-flow", daemon=True)
			self._worker.start()
			return self.status()

	def pause(self) -> dict[str, object]:
		"""暂停后不再开始新的平台动作，已完成的状态仍保持。"""
		with self._lock:
			self._paused = True
			self._pause_event.set()
			if self._state.get("state") == "running":
				self._state["state"] = "paused"
			return self.status()

	def resume(self) -> dict[str, object]:
		"""恢复已暂停循环，不创建第二个浏览器任务。"""
		with self._lock:
			self._paused = False
			self._pause_event.clear()
			if self._worker is not None and self._worker.is_alive():
				self._state["state"] = "running"
			return self.status()

	def stop(self) -> dict[str, object]:
		"""请求后台循环在当前候选人动作完成后停止。"""
		self._stop_event.set()
		with self._lock:
			self._state["state"] = "stopping"
			return self.status()

	def stop_source(self, source: str) -> dict[str, object]:
		"""停止一个自动化来源，保留同岗位仍在运行的另一个来源。

		沟通列表和推荐牛人共用浏览器与轮询线程，不能各自创建并发线程；但设置页
		需要独立结束时间。因此这里仅从来源集合移除目标，最后一个来源被移除时才
		触发原有停止流程，已有候选人队列和对话状态完全不变。
		"""
		if source not in {"conversation", "recommendation", "full_flow"}:
			raise ValueError("自动化来源无效")
		with self._lock:
			if source == "full_flow":
				self._stop_event.set()
				self._state["state"] = "stopping"
				return self.status()
			sources = self._state_sources()
			sources.discard(source)
			self._state["sources"] = sorted(sources)
			source_limits = self._state_source_limits()
			source_limits.pop(source, None)
			self._state["source_limits"] = source_limits
			if sources:
				return self.status()
		self._stop_event.set()
		with self._lock:
			self._state["state"] = "stopping"
			return self.status()

	def wait_until_stopped(self, *, timeout: float) -> bool:
		"""等待后台线程退出，供测试和控制台收尾确认使用。"""
		worker = self._worker
		if worker is None:
			return True
		worker.join(timeout=max(0.0, timeout))
		return not worker.is_alive()

	def sync_once(self, *, job_id: str) -> int:
		"""仅同步一次沟通列表身份记录，不触发 AI、发消息或附件下载。

		Web 的“同步沟通列表”按钮使用此公开边界，调用者无须了解协调器内部的
		平台适配回调。这样只读同步不会意外进入 ``run_once`` 的写操作分支。
		"""
		return len(self._sync_queue(job_id, full_snapshot=True))

	def sync_records_once(self, *, job_id: str, records: list[dict[str, object]]) -> int:
		"""复用调用方已读取的 BOSS 沟通快照同步队列。

		该入口服务 Web“同步沟通列表”按钮：同一批原始记录同时刷新页面列表和
		自动化队列，保证前端看到的候选人顺序与 BOSS 当前列表一致。队列写入仍
		留在协调器内完成，避免运行时越过自动化领域边界直接操作持久化文件。
		"""
		seeds = self._sync_record_queue(job_id, records)
		self._remember_snapshot_order(job_id, seeds)
		return len(seeds)

	def run_once(
		self,
		*,
		job_id: str,
		include_recommendations: bool,
		limit: int,
		recommendation_limit: int | None = None,
	) -> dict[str, int | str]:
		"""执行一个确定性周期，供后台循环和测试共同复用。

		返回值同时包含执行计数和周期状态。过去仅返回 ``synced=0``，会把“本轮
		没有新回复”的正常空闲误解成“同步失败”；现在通过 ``state``、
		``idle_reason`` 和 ``observed`` 把平台未发现新活动与真实异常分开。
		"""
		if self._hard_stop_message() is not None:
			self._stop_event.set()
			return {
				"state": "blocked",
				"idle_reason": "deadline_reached",
				"observed": 0,
				"synced": 0,
				"greeted": 0,
				"processed": 0,
				"analyzed": 0,
			}
		seeds = self._execute_phase(
			"沟通列表同步",
			lambda: self._sync_queue(job_id, full_snapshot=job_id not in self._snapshot_orders),
		)
		# 工作计划在“没有新人”时可能清理快照顺序；附件补查仍必须使用本轮
		# 开始前固定的顺序，所以先保存一份局部快照，不能事后再从可变状态读取。
		snapshot_order = self._snapshot_orders.get(job_id, ())
		# 附件已经发送的未读/版本变化会话不能再交给普通问答处理。它们已完成
		# 对话准入，正确动作是立即同意、打开附件并终审；若仍按普通未读处理，
		# 附件只能等到后续轮转才有机会下载，造成“候选人发了简历却一直没处理”。
		attachment_signal_ids = self._attachment_signal_work_ids(job_id=job_id, seeds=seeds)
		attachment_signal_events = (
			self._execute_phase(
				"附件回流终审",
				lambda: self._finalize_attachments(job_id, attachment_signal_ids, self._pause_event),
			)
			if attachment_signal_ids and not self._operation_interrupted() and self._hard_stop_message() is None
			else []
		)
		self._apply_events(job_id, attachment_signal_events)

		work_plan = self._dialogue_work_plan(
			job_id=job_id,
			seeds=seeds,
			limit=limit,
			excluded_ids=set(attachment_signal_ids),
		)
		events = (
			self._execute_phase(
				"候选人对话处理",
				lambda: self._process_dialogue(job_id, work_plan.friend_ids, self._pause_event),
			)
			if work_plan.friend_ids and not self._operation_interrupted() and self._hard_stop_message() is None
			else []
		)
		self._apply_events(job_id, events)
		self._mark_unreported_dialogue_checks_as_waiting(job_id=job_id, checked_friend_ids=work_plan.friend_ids, events=events)
		# 等待附件不是一次性结果：候选人可能在上一轮之后才发送文件，且这类
		# 会话通常不会再次带有 BOSS 未读标记。若只把本轮沟通目标交给终审，
		# 候选人会永久停在 WAITING_ATTACHMENT，自动化运行多久都不会再次点开。
		# 因此在沟通优先处理完成后，沿用本轮固定快照顺序补充少量等待附件目标。
		attachment_ids = self._waiting_attachment_work_ids(
			job_id=job_id,
			limit=max(1, limit),
			excluded_ids=set((*attachment_signal_ids, *work_plan.friend_ids)),
			snapshot_order=snapshot_order,
		)
		# 对话处理和附件终审是两个独立队列。对话目标不能因为当前状态正好是
		# READY_FOR_RESUME 就再次占满附件批次，否则一轮会重复点击同一会话并阻塞
		# 下一轮未读回复；附件只消费本轮单独选出的少量目标。
		new_attachment_ids = tuple(
			event.friend_id
			for event in events
			if event.stage is AutomationCandidateStage.WAITING_ATTACHMENT
		)
		new_attachment_ids = new_attachment_ids[:self._MAX_ATTACHMENT_CHECKS_PER_CYCLE]
		attachment_capacity = max(0, self._MAX_ATTACHMENT_CHECKS_PER_CYCLE - len(attachment_signal_ids) - len(new_attachment_ids))
		finalize_ids = tuple(dict.fromkeys((*new_attachment_ids, *attachment_ids[:attachment_capacity])))
		final_events = (
			self._execute_phase(
				"附件等待终审",
				lambda: self._finalize_attachments(job_id, finalize_ids, self._pause_event),
			)
			if finalize_ids and not self._operation_interrupted() and self._hard_stop_message() is None
			else []
		)
		self._apply_events(job_id, final_events)
		followup_count = 0
		if self._candidate_followups is not None and not self._operation_interrupted() and self._hard_stop_message() is None:
			followup_count = self._execute_phase("达标候选人后续动作", lambda: self._candidate_followups(job_id))
		recommendation_blocked = include_recommendations and self._recommendation_is_blocked()
		if recommendation_blocked:
			self._add_activity(action="推荐牛人今日已停止", status="blocked", detail=self._recommendation_message())
		greeted_messages = (
			self._execute_phase(
				"推荐牛人招呼",
				lambda: self._greet_recommendations(job_id, recommendation_limit or limit, self._pause_event),
			)
			if include_recommendations and not recommendation_blocked and not self._operation_interrupted() and self._hard_stop_message() is None
			else []
		)
		for message in greeted_messages:
			self._add_activity(action=str(message), status="greeted")
		all_events = [*attachment_signal_events, *events, *final_events]
		processed_count = len(events)
		analyzed_count = sum(1 for event in all_events if event.stage is AutomationCandidateStage.ANALYZED)
		greeted_count = len(greeted_messages)
		# 附件尚未到达时，终审器会按周期回报 ``WAITING_ATTACHMENT``，以便
		# 队列保留“已索要简历”的真实阶段。该回报不代表有新消息、文件或评分
		# 结果；若把它当作推进，控制台和监控会把正常等待误判成持续处理中。
		attachment_progressed = any(
			event.stage is not AutomationCandidateStage.WAITING_ATTACHMENT
			for event in (*attachment_signal_events, *final_events)
		)
		is_idle = not work_plan.friend_ids and not attachment_progressed and not greeted_messages
		idle_reason = ""
		if is_idle:
			waiting_attachment_exists = any(
				candidate.stage is AutomationCandidateStage.WAITING_ATTACHMENT
				for candidate in self.queue.list_for_job(job_id)
			)
			idle_reason = (
				"waiting_for_attachment"
				if final_events or waiting_attachment_exists
				else ("no_new_candidate_activity" if not seeds else "waiting_for_candidate_reply")
			)
		return {
			"state": "idle" if is_idle else "active",
			"idle_reason": idle_reason,
			"observed": len(seeds),
			"synced": len(work_plan.friend_ids),
			"greeted": greeted_count,
			"processed": processed_count,
			"analyzed": analyzed_count,
			"followups": followup_count,
			"recommendation_blocked": 1 if recommendation_blocked else 0,
		}

	@staticmethod
	def _execute_phase(phase: str, operation: Callable[[], Any]) -> Any:
		"""执行一个平台阶段并保留阶段边界，供后台日志和恢复判断使用。"""
		try:
			return operation()
		except RecommendationDailyQuotaReached:
			# 额度耗尽是可预期的业务状态，交给后台循环单独处理。
			raise
		except AutomationPhaseError:
			raise
		except Exception as exc:
			raise AutomationPhaseError(phase, exc) from exc

	def _operation_interrupted(self) -> bool:
		"""判断当前批次是否因暂停或停止而不能开启下一项平台操作。"""
		return self._stop_event.is_set() or self._pause_event.is_set()

	def _attachment_signal_work_ids(self, *, job_id: str, seeds: list[ConversationSeed]) -> tuple[int, ...]:
		"""选择本轮已发附件的优先终审目标。

		BOSS 的未读红点可能因人工打开聊天而消失，因此同时消费列表版本变化信号。
		只有本地阶段明确为 ``WAITING_ATTACHMENT`` 的候选人才会走这条路径，避免
		把普通候选人的未读回复误当成附件并跳过基础或专业问答。
		"""
		changed_ids = self._changed_conversation_ids.get(job_id, set())
		selected: list[int] = []
		for seed in seeds:
			if seed.unread_count <= 0 and seed.friend_id not in changed_ids:
				continue
			candidate = self.queue.candidate_for_job(
				job_id=job_id,
				candidate_key=f"job:{job_id}:friend:{seed.friend_id}",
			)
			if candidate is None or candidate.stage is not AutomationCandidateStage.WAITING_ATTACHMENT:
				continue
			selected.append(seed.friend_id)
			if len(selected) >= self._MAX_ATTACHMENT_CHECKS_PER_CYCLE:
				break
		if selected:
			# 版本变化只负责触发一次检查；检查完成后即使附件仍未到达，也由附件
			# 轮转补查继续负责，不能每轮都把同一候选人置顶。
			self._changed_conversation_ids[job_id] = changed_ids.difference(selected)
		return tuple(selected)

	def _waiting_attachment_work_ids(
		self,
		*,
		job_id: str,
		limit: int,
		excluded_ids: set[int],
		snapshot_order: tuple[int, ...],
	) -> tuple[int, ...]:
		"""从当前岗位快照中挑选待复查附件的候选人。

		附件等待状态属于长期事实，不能依赖本轮未读红点，否则候选人发送附件后
		如果红点被用户点开、平台未展示红点，后台就永远不会再次检查。这里严格
		使用启动本周期时固定的 ``_snapshot_orders``，既保持页面顺序，也避免从
		历史队列盲点已经不属于当前岗位列表的会话。``excluded_ids`` 用于防止
		同一轮既处理沟通又重复执行附件检查。
		"""
		selected: list[int] = []
		if not snapshot_order:
			return ()
		batch_limit = min(2, max(1, limit))
		cursor = self._attachment_cursors.get(job_id, 0) % len(snapshot_order)
		rotated_order = (*snapshot_order[cursor:], *snapshot_order[:cursor])
		for friend_id in rotated_order:
			if friend_id in excluded_ids:
				continue
			candidate = self.queue.candidate_for_job(
				job_id=job_id,
				candidate_key=f"job:{job_id}:friend:{friend_id}",
			)
			if candidate is None or candidate.stage is not AutomationCandidateStage.WAITING_ATTACHMENT:
				continue
			if not self._attachment_retry_due(candidate.attachment_retry_at):
				continue
			selected.append(friend_id)
			if len(selected) >= batch_limit:
				break
		if selected:
			self._attachment_cursors[job_id] = (cursor + len(selected)) % len(snapshot_order)
		return tuple(selected)

	def _attachment_retry_due(self, retry_at: str) -> bool:
		"""判断无强信号附件复查是否到期，坏值按立即复查兼容历史数据。"""
		if not retry_at.strip():
			return True
		try:
			due_at = datetime.fromisoformat(retry_at)
		except ValueError:
			return True
		if due_at.tzinfo is None:
			due_at = due_at.replace(tzinfo=timezone.utc)
		return self._clock() >= due_at

	def _sync_queue(self, job_id: str, *, full_snapshot: bool) -> list[ConversationSeed]:
		"""同步队列：首轮全量建快照，后续只读取轻量未读窗口。"""
		if full_snapshot or self._sync_recent_conversations is None:
			seeds = self._sync_conversations(job_id)
		else:
			seeds = self._sync_recent_conversations(job_id)
		valid_seeds = self._upsert_seeds(job_id, seeds)
		if full_snapshot:
			self._remember_snapshot_order(job_id, valid_seeds)
		return valid_seeds

	def _sync_record_queue(self, job_id: str, records: list[dict[str, object]]) -> list[ConversationSeed]:
		"""将已读取的沟通记录转为队列身份记录，并保留命令层注入的历史恢复能力。"""
		seeds = self._sync_records(job_id, records) if self._sync_records is not None else _conversation_records_to_seeds(records)
		return self._upsert_seeds(job_id, seeds)

	def _upsert_seeds(self, job_id: str, seeds: list[ConversationSeed]) -> list[ConversationSeed]:
		"""批量写入有效身份记录，避免同步时逐人读写整份队列 JSON。

		同步只更新列表身份字段，阶段、评分和附件等事实仍由后续状态机维护。
		先读出当前岗位候选人快照用于版本比较，再把所有增量交给队列的一次性
		原子写入边界，避免 826 人列表触发数百次 fsync。
		"""
		valid_seeds: list[ConversationSeed] = []
		changed_ids: set[int] = set()
		previous_by_key = {
			candidate.candidate_key: candidate
			for candidate in self.queue.list_for_job(job_id)
		}
		updates: list[AutomationCandidateUpsert] = []
		for seed in seeds:
			if seed.friend_id <= 0:
				continue
			previous = previous_by_key.get(f"job:{job_id}:friend:{seed.friend_id}")
			# 等待候选人回复与等待附件都可能因人工点开而清除未读红点。两者前后
			# 版本变化都必须视为回流信号：前者进入对话，后者立即走附件同意下载。
			# 缺失版本的旧状态会在本轮同步补齐，但不会因此打开所有历史会话。
			if (
				previous is not None
				and previous.stage in {
					AutomationCandidateStage.WAITING_CANDIDATE,
					AutomationCandidateStage.WAITING_ATTACHMENT,
				}
				and previous.conversation_version
				and seed.conversation_version
				and previous.conversation_version != seed.conversation_version
			):
				changed_ids.add(seed.friend_id)
			updates.append(AutomationCandidateUpsert(
				friend_id=seed.friend_id,
				job_id=job_id,
				candidate_name=seed.candidate_name,
				source=seed.source if seed.source in {"conversation", "recommendation"} else "conversation",
				last_message_id=seed.last_message_id,
				conversation_version=seed.conversation_version,
				# 同步只能补充列表身份、版本和未读信号。已有候选人的动作来自
				# 对话或附件状态机，不能被每 20 秒一次的同步覆盖成“已同步”。
				last_action="已同步 BOSS 沟通列表" if previous is None else "",
			))
			valid_seeds.append(seed)
		if updates:
			self.queue.upsert_candidates(updates)
		if changed_ids:
			self._changed_conversation_ids.setdefault(job_id, set()).update(changed_ids)
		return valid_seeds

	def _remember_snapshot_order(self, job_id: str, seeds: list[ConversationSeed]) -> None:
		"""保存本周期全量快照的去重顺序，不将增量首屏会话插入当前周期。"""
		self._snapshot_orders[job_id] = tuple(dict.fromkeys(seed.friend_id for seed in seeds))

	def _dialogue_work_plan(
		self,
		*,
		job_id: str,
		seeds: list[ConversationSeed],
		limit: int,
		excluded_ids: set[int] | None = None,
	) -> DialogueWorkPlan:
		"""按“未读优先、快照新人顺序推进”生成本周期 RPA 工作批次。

		增量同步只可信地表达首屏未读状态，不能据此把未读为零的旧会话重新交给
		RPA。无未读时才从完整快照中挑一个仍为 ``SYNCED`` 的候选人首次检查；
		快照内没有新人后清空顺序，下一周期再做一次全量同步接纳处理中新增的人。
		"""
		max_items = max(1, limit)
		excluded = excluded_ids or set()
		unread_ids = tuple(dict.fromkeys(seed.friend_id for seed in seeds if seed.unread_count > 0 and seed.friend_id not in excluded))
		changed_ids = self._changed_conversation_ids.get(job_id, set())
		# BOSS 原生未读永远优先；版本变化只用于补偿红点已被人工清除的回复。
		# 两种信号都只会把候选人送入一次消息指纹校验，不会直接生成 AI 回复。
		changed_in_snapshot = tuple(seed.friend_id for seed in seeds if seed.friend_id in changed_ids and seed.friend_id not in excluded)
		changed_outside_snapshot = tuple(sorted(changed_ids.difference(changed_in_snapshot).difference(excluded)))
		priority_ids = tuple(dict.fromkeys((*unread_ids, *changed_in_snapshot, *changed_outside_snapshot)))
		if priority_ids:
			self._recovery_cycles[job_id] = 0
			selected_ids = priority_ids[:max_items]
			# 版本变化可能一次超过批量上限；只消费本轮实际交给 RPA 的 ID，
			# 其余变化必须留到下轮，不能因切片而静默丢失。
			self._changed_conversation_ids[job_id] = changed_ids.difference(selected_ids)
			return DialogueWorkPlan(friend_ids=selected_ids, first_check_ids=())

		first_check_ids: list[int] = []
		for friend_id in self._snapshot_orders.get(job_id, ()):
			if friend_id in excluded:
				continue
			candidate = self.queue.candidate_for_job(job_id=job_id, candidate_key=f"job:{job_id}:friend:{friend_id}")
			if candidate is not None and candidate.stage is AutomationCandidateStage.SYNCED:
				first_check_ids.append(friend_id)
				if len(first_check_ids) >= max_items:
					break
		if first_check_ids:
			self._recovery_cycles[job_id] = 0
			friend_ids = tuple(first_check_ids)
			return DialogueWorkPlan(friend_ids=friend_ids, first_check_ids=friend_ids)

		# 未读和版本信号都可能因用户打开会话或 BOSS DOM 延迟而丢失。每隔固定
		# 几轮从原始快照按顺序抽取一小批仍在进行中的会话做恢复核对；这只读取
		# 最新消息，不会自动重复发问题。游标轮转后，底部候选人也能获得机会。
		self._recovery_cycles[job_id] = self._recovery_cycles.get(job_id, 0) + 1
		if self._recovery_cycles[job_id] % self._RECOVERY_INTERVAL_CYCLES:
			return DialogueWorkPlan(friend_ids=(), first_check_ids=())
		ordered_ids = self._snapshot_orders.get(job_id, ())
		if not ordered_ids:
			return DialogueWorkPlan(friend_ids=(), first_check_ids=())
		cursor = self._recovery_cursors.get(job_id, 0) % len(ordered_ids)
		rotated_ids = (*ordered_ids[cursor:], *ordered_ids[:cursor])
		recoverable_stages = {
			AutomationCandidateStage.BASIC_DIALOGUE,
			AutomationCandidateStage.PROFESSIONAL_DIALOGUE,
			AutomationCandidateStage.WAITING_CANDIDATE,
		}
		recovery_ids = tuple(
			friend_id
			for friend_id in rotated_ids
			if friend_id not in excluded and (candidate := self.queue.candidate_for_job(
				job_id=job_id,
				candidate_key=f"job:{job_id}:friend:{friend_id}",
			)) is not None
			and candidate.stage in recoverable_stages
		)[:max_items]
		if recovery_ids:
			self._recovery_cursors[job_id] = (cursor + len(recovery_ids)) % len(ordered_ids)
		return DialogueWorkPlan(friend_ids=recovery_ids, first_check_ids=())

		# 当前快照没有可恢复的对话时保持空闲。不能删除快照，否则下一轮会重扫
		# 整张虚拟沟通列表，既拖慢真实回复处理，又让 BOSS 页面持续滚动。
		return DialogueWorkPlan(friend_ids=(), first_check_ids=())

	def _mark_unreported_dialogue_checks_as_waiting(
		self,
		*,
		job_id: str,
		checked_friend_ids: tuple[int, ...],
		events: list[AutomationCandidateEvent],
	) -> None:
		"""把没有产生 AI 事件的已检查会话移出新人队列。

		RPA 读取后可能发现候选人尚未回复、最后一条消息已经处理完，或平台未读
		徽标与消息指纹存在短暂差异，此时命令层不会返回事件。若仍保留 ``SYNCED``，
		下次轮询会再次把该会话当新人点开；显式标为等待后，只有平台出现未读回复
		才会再次进入 RPA。
		"""
		reported_ids = {event.friend_id for event in events}
		for friend_id in checked_friend_ids:
			if friend_id in reported_ids:
				continue
			candidate = self.queue.candidate_for_job(
				job_id=job_id,
				candidate_key=f"job:{job_id}:friend:{friend_id}",
			)
			# 首次检查和未读回复没有产生事件时仍需移出新人队列，避免下轮
			# 重复打开；恢复核对的专业/基础沟通阶段必须保持原阶段，不能被
			# 一次“暂时没有新消息”错误降级成等待基础问题。
			if candidate is None or candidate.stage is not AutomationCandidateStage.SYNCED:
				continue
			waiting_stage = AutomationCandidateStage("waiting_candidate")
			self.queue.update_stage(
				f"job:{job_id}:friend:{friend_id}",
				stage=waiting_stage,
				last_action="首次检查完成，等待候选人回复",
			)

	def _apply_events(self, job_id: str, events: list[AutomationCandidateEvent]) -> None:
		"""将适配器回报写回统一队列，终审事件额外校验附件路径。"""
		for event in events:
			# 队列主键属于岗位评估而非单纯 BOSS 会话；同一候选人转投其它岗位时，
			# 该事件只能更新当前岗位的记录，不能覆盖之前岗位的附件终审。
			key = f"job:{job_id}:friend:{event.friend_id}"
			if event.stage is AutomationCandidateStage.ANALYZED and event.score is not None and event.resume_path is not None:
				self.queue.record_final_review(
					friend_id=event.friend_id,
					job_id=job_id,
					score=event.score,
					recommendation=event.recommendation,
					resume_path=event.resume_path,
				)
			else:
				self.queue.update_stage(
					key,
					stage=event.stage,
					last_action=event.action,
					reason_codes=event.reason_codes,
					last_message_id=event.last_message_id,
				)
			if event.stage is AutomationCandidateStage.WAITING_ATTACHMENT:
				self.queue.set_attachment_retry_at(
					key,
					retry_at=(self._clock() + self._ATTACHMENT_RETRY_DELAY).isoformat(timespec="seconds"),
				)
			elif event.stage is not AutomationCandidateStage.WAITING_ATTACHMENT:
				self.queue.set_attachment_retry_at(key, retry_at="")
			self._add_activity(action=event.action, status=event.stage.value, candidate_key=key)

	def _run_loop(self) -> None:
		"""后台轮询：等待回复不会阻塞下一周期，停止请求优先于下一轮操作。"""
		while not self._stop_event.is_set():
			with self._lock:
				paused = self._paused
				job_id = str(self._state.get("job_id") or "")
				sources = self._state_sources()
				source_limits = self._state_source_limits()
				limit = source_limits.get("conversation", 20)
				recommendation_limit = source_limits.get("recommendation", 10)
			if not paused and job_id:
				try:
					# 手动同步和附件下载使用的是 Web 运行时同一把锁。锁必须覆盖
					# 登录探测与完整轮询，而不是只包住某一条列表读取，否则探测
					# 通过后页面仍可能在下一步被另一个线程切走。
					with self._platform_operation_lock or nullcontext():
						if (deadline_message := self._hard_stop_message()) is not None:
							self._add_activity(action="自动化轮询已停止", status="blocked", detail=deadline_message)
							self._stop_event.set()
							break
						if (guard_message := self._runtime_guard_message()) is not None:
							self._add_activity(action="自动化轮询已停止", status="blocked", detail=guard_message)
							self._stop_event.set()
							break
						full_flow = bool(self._state.get("full_flow"))
						report = self.run_once(
							job_id=job_id,
							include_recommendations=False if full_flow else "recommendation" in sources,
							limit=limit,
							recommendation_limit=recommendation_limit,
						)
						# 全流程只有在沟通列表本轮没有立即任务时才进入推荐阶段；
						# 推荐阶段结束后下一轮重新读取沟通列表，及时处理新回复。
						if (
							full_flow
							and not self._recommendation_is_blocked()
							and report.get("state") == "idle"
							and not self._operation_interrupted()
						):
							report = self.run_once(
								job_id=job_id,
								include_recommendations=True,
								limit=limit,
								recommendation_limit=recommendation_limit,
							)
							is_idle = report.get("state") == "idle"
							self._add_activity(
								action="自动化轮询空闲" if is_idle else "自动化轮询完成",
								status="idle" if is_idle else "succeeded",
								detail=str(report),
							)
				except RecommendationDailyQuotaReached:
					# 推荐触顶后移除推荐来源，保留沟通列表与附件处理。
					self._disable_recommendation_source_after_quota()
				except Exception as exc:
					self._add_activity(
						action="自动化轮询失败",
						status="failed",
						detail=str(exc)[:200] or type(exc).__name__,
					)
			if not self._stop_event.is_set():
				# 轮询间隔属于正常运行状态而不是停止。明确记录下一次同步时间，
				# 让控制台可区分“等待候选人回复”和浏览器/RPA 异常中断。
				self._add_activity(
					action="等待下一轮轮询",
					status="waiting",
					detail=f"{int(self._poll_interval_seconds)} 秒后继续同步 BOSS 沟通列表",
				)
			for _ in range(int(self._poll_interval_seconds * 10)):
				if self._stop_event.is_set():
					break
				sleep(0.1)
		with self._lock:
			self._state["state"] = "stopped"

	def _runtime_guard_message(self) -> str | None:
		"""运行时守卫返回中文阻断原因；异常也收敛为登录恢复提示。"""
		if self._runtime_guard is None:
			return None
		try:
			message = self._runtime_guard()
		except Exception:
			return "当前 RPA 浏览器状态不可用，请确认 BOSS 招聘页已登录后重试"
		if message is None:
			return None
		return message.strip()[:200] or "当前 RPA 浏览器尚未登录 BOSS，请先完成官方登录"

	def _recommendation_is_blocked(self) -> bool:
		"""读取账号级额度状态；状态存储异常不能阻断其它自动化。"""
		if self._recommendation_quota is None:
			return False
		try:
			return bool(self._recommendation_quota.is_blocked())
		except Exception:
			return False

	def _recommendation_message(self) -> str:
		"""返回对用户可见的固定额度提示，不暴露 BOSS 页面内容。"""
		if self._recommendation_quota is not None:
			try:
				message = self._recommendation_quota.status().get("message")
				if isinstance(message, str) and message.strip():
					return message.strip()[:240]
			except Exception:
				pass
		return "BOSS 推荐牛人今日沟通已达上限，今天已停止所有岗位的推荐牛人自动化，仅继续处理沟通列表；次日自动恢复。"

	def _recommendation_blocked_state(self, *, job_id: str, sources: list[str]) -> dict[str, object]:
		"""构造推荐来源当天不可启动时的稳定页面状态。"""
		message = self._recommendation_message()
		with self._lock:
			self._state = {
				"state": "blocked",
				"job_id": job_id,
				"sources": sources,
				"recommendation_blocked": True,
				"activities": [],
			}
			self._add_activity(action="推荐牛人今日已停止", status="blocked", detail=message)
			return {**self.status(), "error": {"code": "RECOMMENDATION_DAILY_QUOTA_REACHED", "message": message}}

	def _disable_recommendation_source_after_quota(self) -> None:
		"""后台运行中触顶时仅移除推荐来源，不停止沟通列表。"""
		message = self._recommendation_message()
		with self._lock:
			sources = self._state_sources()
			sources.discard("recommendation")
			self._state["sources"] = sorted(sources)
			self._state["recommendation_blocked"] = True
			source_limits = self._state_source_limits()
			source_limits.pop("recommendation", None)
			self._state["source_limits"] = source_limits
			self._add_activity(action="推荐牛人今日已停止", status="blocked", detail=message)
			if not sources:
				self._stop_event.set()

	def _hard_stop_message(self) -> str | None:
		"""判断本轮自动化是否已到用户设定的绝对截止时间。

		截止时间允许测试传入 UTC，也允许 Web 入口传入本地时区。若某个旧调用方
		传入无时区时间，则按时钟的时区解释，避免比较异常把“21 点后停止”静默失效。
		"""
		deadline = self._hard_stop_at
		if deadline is None:
			return None
		now = self._clock()
		if deadline.tzinfo is None and now.tzinfo is not None:
			deadline = deadline.replace(tzinfo=now.tzinfo)
		elif deadline.tzinfo is not None and now.tzinfo is None:
			now = now.replace(tzinfo=deadline.tzinfo)
		if now < deadline:
			return None
		return f"已到 {deadline.strftime('%H:%M')}，自动化已停止，不再执行新的 BOSS 操作"

	def _add_activity(self, *, action: str, status: str, candidate_key: str = "", detail: str = "") -> None:
		"""保存简短活动记录，防止日志意外携带候选人消息或简历正文。"""
		with self._lock:
			stored_activities = self._state.get("activities")
			activities = list(stored_activities) if isinstance(stored_activities, list) else []
			activities.insert(0, {
				"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
				"action": sanitize_unicode_text(action[:160]),
				"status": sanitize_unicode_text(status[:40]),
				"candidate_key": sanitize_unicode_text(candidate_key[:128]),
				"detail": sanitize_unicode_text(detail[:200]),
			})
			self._state["activities"] = activities[:50]

	def _state_sources(self) -> set[str]:
		"""从 Web 可序列化状态读取来源集合，隔离动态字典的类型边界。"""
		stored_sources = self._state.get("sources")
		if not isinstance(stored_sources, list):
			return set()
		return {source for source in stored_sources if isinstance(source, str)}

	def _state_source_limits(self) -> dict[str, int]:
		"""读取两个来源各自的单轮数量，并兼容旧状态中的共享 ``limit``。"""
		stored = self._state.get("source_limits")
		if isinstance(stored, dict):
			return {
				str(source): value
				for source, value in stored.items()
				if source in {"conversation", "recommendation"} and isinstance(value, int) and 1 <= value <= 50
			}
		legacy = self._state.get("limit")
		return {source: legacy for source in self._state_sources() if isinstance(legacy, int)}

	def status(self) -> dict[str, object]:
		"""返回 Web 可直接轮询的控制状态，不暴露平台对象或线程句柄。"""
		with self._lock:
			result = dict(self._state)
		if self._recommendation_quota is not None:
			try:
				result["recommendation_quota"] = self._recommendation_quota.status()
			except Exception:
				result["recommendation_quota"] = {"blocked": False}
		return result
