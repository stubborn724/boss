"""按自动化来源持久化定时任务配置。

本模块只描述“什么时候允许启动哪一个按钮”，不保存后台线程、浏览器状态或
候选人处理进度。运行事实继续由 :class:`AutomationCoordinator` 管理，因而服务
重启只会恢复用户配置，不会把中断前的平台操作重复执行。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
import json
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from typing import Any, Callable


_FILENAME = "automation-schedule-settings.json"
# 三个入口各自保存配置；``full_flow`` 由后台串行调度，不与两个独立入口并发。
_SOURCES = {"conversation", "recommendation", "full_flow"}


@dataclass(frozen=True)
class AutomationScheduleSettings:
	"""一个来源的岗位级定时任务配置。

	``interval_minutes`` 是同一任务成功提交后的最短再次触发间隔；协调器本身仍
	负责内部 20 秒轮询。两层间隔职责不同，避免设置页改变已有对话轮询语义。
	"""

	enabled: bool = False
	job_id: str = ""
	start_time: str = "09:00"
	end_time: str = "18:00"
	interval_minutes: int = 20
	limit: int = 20
	daily_quota: int = 100
	weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

	def validated(self) -> "AutomationScheduleSettings":
		"""清洗并校验配置，允许跨午夜窗口并拒绝无效配额。"""
		job_id = self.job_id.strip()[:128]
		if self.enabled and not job_id:
			raise ValueError("启用定时任务前必须选择岗位")
		try:
			start = time.fromisoformat(self.start_time.strip())
			end = time.fromisoformat(self.end_time.strip())
		except ValueError as exc:
			raise ValueError("定时任务时间格式无效") from exc
		if start == end:
			raise ValueError("开始时间和结束时间不能相同")
		if not 1 <= self.interval_minutes <= 1440:
			raise ValueError("执行间隔必须在 1 到 1440 分钟之间")
		if not 1 <= self.limit <= 50:
			raise ValueError("单次处理数量必须在 1 到 50 之间")
		if not 1 <= self.daily_quota <= 1000:
			raise ValueError("每日配额必须在 1 到 1000 之间")
		weekdays = tuple(sorted(set(self.weekdays)))
		if not weekdays or any(day < 0 or day > 6 for day in weekdays):
			raise ValueError("每周执行日期无效")
		return AutomationScheduleSettings(
			enabled=bool(self.enabled),
			job_id=job_id,
			start_time=start.strftime("%H:%M"),
			end_time=end.strftime("%H:%M"),
			interval_minutes=int(self.interval_minutes),
			limit=int(self.limit),
			daily_quota=int(self.daily_quota),
			weekdays=weekdays,
		)

	def is_active_at(self, now: datetime) -> bool:
		"""判断本地时间是否处于半开窗口，支持例如 20:00-09:00 的跨午夜任务。"""
		settings = self.validated()
		if not settings.enabled:
			return False
		current = now.time().replace(second=0, microsecond=0, tzinfo=None)
		start = time.fromisoformat(settings.start_time)
		end = time.fromisoformat(settings.end_time)
		if start < end:
			return now.weekday() in settings.weekdays and start <= current < end
		# 跨午夜时，午夜后的时间属于前一个自然日的执行窗口。
		window_day = now.weekday() if current >= start else (now.weekday() - 1) % 7
		return window_day in settings.weekdays and (current >= start or current < end)

	def to_dict(self) -> dict[str, object]:
		"""输出固定字段，元组转换为适合 JSON 和 Web 的数组。"""
		result = asdict(self)
		result["weekdays"] = list(self.weekdays)
		return result

	@classmethod
	def from_dict(cls, value: Any) -> "AutomationScheduleSettings | None":
		"""兼容读取历史或损坏 JSON；单项损坏不影响另一个来源。"""
		if not isinstance(value, dict):
			return None
		try:
			weekdays_value = value.get("weekdays", (0, 1, 2, 3, 4))
			weekdays = tuple(int(day) for day in weekdays_value) if isinstance(weekdays_value, list | tuple) else ()
			return cls(
				enabled=value.get("enabled") is True,
				job_id=str(value.get("job_id") or ""),
				start_time=str(value.get("start_time") or "09:00"),
				end_time=str(value.get("end_time") or "18:00"),
				interval_minutes=int(value.get("interval_minutes") or 20),
				limit=int(value.get("limit") or 20),
				daily_quota=int(value.get("daily_quota") or 100),
				weekdays=weekdays,
			).validated()
		except (TypeError, ValueError):
			return None


class AutomationScheduleSettingsStore:
	"""以原子 JSON 快照保存两个按钮各自的定时设置。"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / _FILENAME
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	@staticmethod
	def _validate_source(source: str) -> str:
		clean_source = source.strip().casefold()
		if clean_source not in _SOURCES:
			raise ValueError("定时任务来源无效")
		return clean_source

	def _read(self) -> dict[str, AutomationScheduleSettings]:
		if not self._path.exists():
			return {}
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		if not isinstance(raw, dict):
			return {}
		return {
			source: settings
			for source, value in raw.items()
			if source in _SOURCES and (settings := AutomationScheduleSettings.from_dict(value)) is not None
		}

	def _write(self, values: dict[str, AutomationScheduleSettings]) -> None:
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump({source: item.to_dict() for source, item in values.items()}, stream, ensure_ascii=False, indent=2, sort_keys=True)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass

	def get(self, *, source: str) -> AutomationScheduleSettings:
		"""读取一个按钮的配置；未设置时返回禁用默认值。"""
		clean_source = self._validate_source(source)
		with self._lock:
			return self._read().get(clean_source, AutomationScheduleSettings())

	def all(self) -> dict[str, AutomationScheduleSettings]:
		"""始终返回三个来源，方便页面稳定渲染独立表单。"""
		with self._lock:
			stored = self._read()
		return {source: stored.get(source, AutomationScheduleSettings()) for source in sorted(_SOURCES)}

	def save(self, *, source: str, settings: AutomationScheduleSettings) -> AutomationScheduleSettings:
		"""验证并保存单一来源，绝不覆盖另一个按钮的配置。"""
		clean_source = self._validate_source(source)
		validated = settings.validated()
		with self._lock:
			values = self._read()
			values[clean_source] = validated
			self._write(values)
		return validated


StartAutomation = Callable[[str, str, int], dict[str, object]]
StopAutomationSource = Callable[[str], dict[str, object]]
AutomationStatus = Callable[[], dict[str, object]]


class AutomationScheduleMonitor:
	"""监视两个独立时间窗口并调用已有自动化控制边界。

	监控器不直接访问 BOSS，也不理解候选人状态机。它只读取持久化设置，并调用
	与手动按钮相同的启动/停止接口；由此定时与手动执行共享登录校验、合规门禁、
	平台锁和对话逻辑，不产生第二套容易漂移的业务流程。
	"""

	def __init__(
		self,
		*,
		store: AutomationScheduleSettingsStore,
		start_automation: StartAutomation,
		stop_automation: StopAutomationSource,
		automation_status: AutomationStatus,
		clock: Callable[[], datetime] | None = None,
		check_interval_seconds: float = 15.0,
	) -> None:
		self._store = store
		self._start_automation = start_automation
		self._stop_automation = stop_automation
		self._automation_status = automation_status
		self._clock = clock or datetime.now
		self._check_interval_seconds = max(1.0, check_interval_seconds)
		self._lock = Lock()
		self._stop_event = Event()
		self._worker: Thread | None = None
		# 所有权只存在于当前进程。服务重启后为空，因此不会停止或恢复旧进程留下
		# 的平台动作；新进程仅在下一次 tick 重新满足条件时创建新运行。
		self._owned_sources: set[str] = set()
		# 推荐额度是账号级当天事实。记录阻断日期可避免调度器在当天每个 tick
		# 重复提交推荐启动请求；日期变化时自动清除，配置无需重新保存。
		self._recommendation_quota_blocked_day = ""
		self._states: dict[str, dict[str, object]] = {
			"conversation": {"state": "disabled"},
			"recommendation": {"state": "disabled"},
		}

	def start(self) -> None:
		"""幂等启动守护线程，避免 Web 初始化重复创建调度器。"""
		with self._lock:
			if self._worker is not None and self._worker.is_alive():
				return
			self._stop_event.clear()
			self._worker = Thread(target=self._run, name="recruiting-automation-schedules", daemon=True)
			self._worker.start()

	def close(self) -> None:
		"""仅停止本地监控线程，不在服务关闭时额外驱动平台。"""
		self._stop_event.set()

	def _run(self) -> None:
		while not self._stop_event.is_set():
			self.tick(self._clock())
			for _ in range(int(self._check_interval_seconds * 10)):
				if self._stop_event.is_set():
					return
				sleep(0.1)

	def tick(self, now: datetime) -> None:
		"""执行一次确定性调度判断，供后台线程和测试共同使用。"""
		if self._recommendation_quota_blocked_day and self._recommendation_quota_blocked_day != now.date().isoformat():
			self._recommendation_quota_blocked_day = ""
		for source, settings in self._store.all().items():
			active = settings.is_active_at(now) if settings.enabled else False
			if not active:
				if source in self._owned_sources:
					self._stop_automation(source)
					self._owned_sources.discard(source)
				self._set_state(source, "outside_window" if settings.enabled else "disabled", settings)
				continue
			status = self._automation_status()
			runtime_state = str(status.get("state") or "idle")
			runtime_job_id = str(status.get("job_id") or "")
			sources_value = status.get("sources")
			runtime_sources = set(sources_value) if isinstance(sources_value, list) else set()
			if runtime_state in {"running", "paused", "stopping"} and runtime_job_id and runtime_job_id != settings.job_id:
				self._set_state(source, "waiting_for_other_job", settings)
				continue
			runtime_matches_source = (
				source in runtime_sources
				if source != "full_flow"
				else {"conversation", "recommendation"}.issubset(runtime_sources)
			)
			if runtime_matches_source and runtime_job_id == settings.job_id:
				self._set_state(source, runtime_state, settings)
				continue
			if source == "recommendation" and self._recommendation_quota_blocked_day == now.date().isoformat():
				self._set_state(source, "blocked", settings, error={
					"code": "RECOMMENDATION_DAILY_QUOTA_REACHED",
					"message": "BOSS 推荐牛人今日沟通已达上限，今天仅处理沟通列表；次日自动恢复。",
				})
				continue
			result = self._start_automation(settings.job_id, source, settings.limit)
			result_state = str(result.get("state") or "failed")
			error = result.get("error")
			if (
				source == "recommendation"
				and isinstance(error, dict)
				and str(error.get("code") or "") == "RECOMMENDATION_DAILY_QUOTA_REACHED"
			):
				self._recommendation_quota_blocked_day = now.date().isoformat()
				self._set_state(source, "blocked", settings, error=error)
				continue
			if result_state not in {"blocked", "failed"}:
				self._owned_sources.add(source)
			self._set_state(source, result_state, settings, error=error)

	def _set_state(
		self,
		source: str,
		state: str,
		settings: AutomationScheduleSettings,
		*,
		error: object | None = None,
	) -> None:
		"""收敛状态字段，避免平台响应或候选人内容进入设置接口。"""
		value: dict[str, object] = {"state": state, "job_id": settings.job_id}
		if isinstance(error, dict):
			value["error"] = {
				"code": str(error.get("code") or "SCHEDULE_START_FAILED")[:80],
				"message": str(error.get("message") or "定时任务启动失败")[:160],
			}
		with self._lock:
			self._states[source] = value

	def status(self) -> dict[str, dict[str, object]]:
		"""返回两个来源各自的调度状态快照。"""
		with self._lock:
			return {source: dict(value) for source, value in self._states.items()}
