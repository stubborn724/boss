"""招聘自动化的持久化节奏和额度策略。

该模块只做本地时间判断，不负责等待、重试或执行平台动作。策略将每日计数、
上次动作和随机启动门写入 ``AutomationStore`` 的状态字典，进程重启后仍能
恢复同一暂停原因；所有暂停结果都带稳定代码，页面和日志可以给出明确解释。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import random
from typing import Any, Callable

from boss_agent_cli.automation.config import AutomationConfig


RandomSource = Callable[[], float]

_PACING_REASON_LABELS = {
	"": "当前时段允许执行",
	"startup_jitter": "今日启动窗口尚未到达",
	"daily_quota": "已达到当前时段额度",
	"cooldown": "动作冷却中，请在提示时间后再试",
}


@dataclass(frozen=True, slots=True)
class PacingDecision:
	"""一次节奏判定的可展示结果。"""

	allowed: bool
	reason: str = ""
	effective_quota: int = 0
	count: int = 0
	pause_until: str = ""

	def to_dict(self) -> dict[str, Any]:
		"""转成 Web/CLI 可安全回显的状态，不携带账号或候选人数据。"""
		return {
			"allowed": self.allowed,
			"reason": self.reason,
			"effective_quota": self.effective_quota,
			"count": self.count,
			"pause_until": self.pause_until,
		}


@dataclass(frozen=True, slots=True)
class PacingPolicy:
	"""可配置的每日额度、工作时段、降量和冷却规则。"""

	daily_action_quota: int = 50
	cooldown_seconds: int = 0
	schedule_enabled: bool = False
	work_start_hour: int = 0
	work_end_hour: int = 24
	lunch_start_hour: int = 0
	lunch_end_hour: int = 0
	lunch_quota_factor: float = 1.0
	weekend_quota_factor: float = 1.0
	off_hours_quota_factor: float = 1.0
	random_start_jitter_seconds: int = 0
	random_source: RandomSource | None = None

	def __post_init__(self) -> None:
		"""拒绝会导致无限动作或负等待的配置，避免错误配置绕过保护。"""
		if self.daily_action_quota < 0:
			raise ValueError("每日动作额度不能为负数")
		if self.cooldown_seconds < 0 or self.random_start_jitter_seconds < 0:
			raise ValueError("冷却和随机启动时间不能为负数")
		for name, value in (
			("work_start_hour", self.work_start_hour),
			("work_end_hour", self.work_end_hour),
			("lunch_start_hour", self.lunch_start_hour),
			("lunch_end_hour", self.lunch_end_hour),
		):
			if not 0 <= value <= 24:
				raise ValueError(f"{name} 必须在 0 到 24 之间")
		for name, value_float in (
			("lunch_quota_factor", self.lunch_quota_factor),
			("weekend_quota_factor", self.weekend_quota_factor),
			("off_hours_quota_factor", self.off_hours_quota_factor),
		):
			if not 0 <= value_float <= 1:
				raise ValueError(f"{name} 必须在 0 到 1 之间")

	def evaluate(self, state: dict[str, Any], *, now: datetime | None = None) -> PacingDecision:
		"""按当前时间判断是否允许下一次真实动作，并同步日状态。"""
		current = _ensure_aware(now or datetime.now(timezone.utc))
		pacing = _prepare_state(state, current)
		count = _safe_int(pacing.get("count"), default=0)

		startup_at = self._ensure_startup_gate(pacing, current)
		if startup_at and current < startup_at:
			return PacingDecision(False, "startup_jitter", self._effective_quota(current), count, startup_at.isoformat())

		effective_quota = self._effective_quota(current)
		if count >= effective_quota:
			return PacingDecision(False, "daily_quota", effective_quota, count)

		last_action = _parse_timestamp(pacing.get("last_action_at"))
		if last_action is not None and self.cooldown_seconds:
			pause_until = last_action + timedelta(seconds=self.cooldown_seconds)
			if current < pause_until:
				return PacingDecision(False, "cooldown", effective_quota, count, pause_until.isoformat())
		return PacingDecision(True, "", effective_quota, count)

	def record_action(self, state: dict[str, Any], *, now: datetime | None = None) -> None:
		"""记录一次真实平台动作；dry-run 不应调用此方法。"""
		current = _ensure_aware(now or datetime.now(timezone.utc))
		pacing = _prepare_state(state, current)
		pacing["count"] = _safe_int(pacing.get("count"), default=0) + 1
		pacing["last_action_at"] = current.isoformat()

	def _effective_quota(self, current: datetime) -> int:
		"""按周末、午休和非工作时段计算本时段额度。"""
		factor = 1.0
		if self.schedule_enabled:
			in_work_hours = self.work_start_hour <= current.hour < self.work_end_hour
			if not in_work_hours:
				factor = min(factor, self.off_hours_quota_factor)
			if current.weekday() >= 5:
				factor = min(factor, self.weekend_quota_factor)
			if self.lunch_start_hour < self.lunch_end_hour and self.lunch_start_hour <= current.hour < self.lunch_end_hour:
				factor = min(factor, self.lunch_quota_factor)
		return max(0, math.floor(self.daily_action_quota * factor))

	def _ensure_startup_gate(self, pacing: dict[str, Any], current: datetime) -> datetime | None:
		"""每天只生成一次启动门，并把结果写回状态供重启恢复。"""
		if self.random_start_jitter_seconds <= 0:
			return None
		stored = _parse_timestamp(pacing.get("startup_at"))
		if stored is not None:
			return stored
		source = self.random_source or (lambda: random.uniform(0, self.random_start_jitter_seconds))
		delay = min(self.random_start_jitter_seconds, max(0, int(source())))
		startup_at = current + timedelta(seconds=delay)
		pacing["startup_at"] = startup_at.isoformat()
		return startup_at


def pacing_policy_from_config(config: AutomationConfig) -> PacingPolicy:
	"""从统一自动化配置创建节奏策略，避免 Web 与执行器各自映射字段。"""
	return PacingPolicy(
		daily_action_quota=config.daily_action_quota,
		cooldown_seconds=config.cooldown_seconds,
		schedule_enabled=config.schedule_enabled,
		work_start_hour=config.work_start_hour,
		work_end_hour=config.work_end_hour,
		lunch_start_hour=config.lunch_start_hour,
		lunch_end_hour=config.lunch_end_hour,
		lunch_quota_factor=config.lunch_quota_factor,
		weekend_quota_factor=config.weekend_quota_factor,
		off_hours_quota_factor=config.off_hours_quota_factor,
		random_start_jitter_seconds=config.random_start_jitter_seconds,
	)


def build_pacing_status(
	policy: PacingPolicy,
	state: dict[str, Any],
	*,
	now: datetime | None = None,
) -> dict[str, Any]:
	"""把节奏判定投影成 Web 可读状态，并保留引擎的持久化日状态。

	该函数是 ``SafetyGuard`` 与本地工作台共用的单一事实来源：页面看到的额度、
	冷却和工作时段都来自同一套规则，而不是另写一份前端判断。调用方负责把
	传入的状态写回 ``AutomationStore``；投影只包含时间、计数和策略结果，绝不
	包含 Cookie、候选人标识或原始平台响应。
	"""
	current = _ensure_aware(now or datetime.now(timezone.utc))
	decision = policy.evaluate(state, now=current)
	pacing = state.get("pacing")
	if not isinstance(pacing, dict):
		pacing = {}
	count = _safe_int(pacing.get("count"), default=0)
	effective_quota = decision.effective_quota
	window, window_label = _schedule_window(policy, current)
	reason_label = _PACING_REASON_LABELS.get(decision.reason, "当前由安全策略暂停")
	if decision.reason == "daily_quota" and effective_quota == 0:
		reason_label = {
			"off_hours": "非工作时段暂不执行",
			"lunch": "午休时段暂不执行",
			"weekend": "周末时段暂不执行",
		}.get(window, reason_label)
	return {
		"configured": True,
		"day": str(pacing.get("day") or current.date().isoformat()),
		"count": count,
		"daily_action_quota": policy.daily_action_quota,
		"effective_quota": effective_quota,
		"remaining": max(0, effective_quota - count),
		"allowed": decision.allowed,
		"reason": decision.reason,
		"reason_label": reason_label,
		"pause_until": decision.pause_until,
		"last_action_at": str(pacing.get("last_action_at") or ""),
		"schedule_enabled": policy.schedule_enabled,
		"window": window,
		"window_label": window_label,
		"cooldown_seconds": policy.cooldown_seconds,
	}


def _schedule_window(policy: PacingPolicy, current: datetime) -> tuple[str, str]:
	"""将策略当前时段翻译成稳定标签，帮助 HR 区分额度暂停和登录失败。"""
	if not policy.schedule_enabled:
		return "all_day", "未启用工作时段限制"
	in_work_hours = policy.work_start_hour <= current.hour < policy.work_end_hour
	if current.weekday() >= 5:
		return "weekend", "周末降量"
	if policy.lunch_start_hour < policy.lunch_end_hour and policy.lunch_start_hour <= current.hour < policy.lunch_end_hour:
		return "lunch", "午休降量"
	if not in_work_hours:
		return "off_hours", "非工作时段"
	return "work_hours", "工作时段"


def _ensure_aware(value: datetime) -> datetime:
	"""将无时区时间按 UTC 解释，避免夏令时或本地环境造成比较异常。"""
	return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
	"""解析旧状态时间，损坏时返回 None 而不是阻断整个自动化周期。"""
	if not isinstance(value, str) or not value.strip():
		return None
	try:
		return _ensure_aware(datetime.fromisoformat(value))
	except ValueError:
		return None


def _safe_int(value: Any, *, default: int) -> int:
	"""读取 JSON 中的计数并拒绝布尔值和负数。"""
	if isinstance(value, bool):
		return default
	try:
		return max(0, int(value))
	except (TypeError, ValueError):
		return default


def _prepare_state(state: dict[str, Any], current: datetime) -> dict[str, Any]:
	"""初始化或跨日重置 pacing 区块，同时保留可审计的上次动作字段。"""
	raw = state.setdefault("pacing", {})
	if not isinstance(raw, dict):
		raw = {}
		state["pacing"] = raw
	day = current.date().isoformat()
	if raw.get("day") != day:
		raw.clear()
		raw.update({"day": day, "count": 0, "last_action_at": "", "startup_at": ""})
	return raw
