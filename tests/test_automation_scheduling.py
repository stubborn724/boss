"""自动化节奏策略的持久化和边界测试。"""

from datetime import datetime, timezone

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.scheduling import PacingPolicy, build_pacing_status, pacing_policy_from_config


def _time(hour: int, minute: int = 0, second: int = 0) -> datetime:
	return datetime(2026, 7, 27, hour, minute, second, tzinfo=timezone.utc)


def test_default_automation_config_can_build_pacing_policy() -> None:
	"""Web 启动时必须能从默认自动化配置创建节奏策略。"""
	policy = pacing_policy_from_config(AutomationConfig())

	assert policy.daily_action_quota == 50
	assert policy.schedule_enabled is False


def test_pacing_persists_daily_quota_and_cooldown_in_state() -> None:
	"""真实动作达到每日额度或冷却窗口时必须暂停并返回可解释原因。"""
	policy = PacingPolicy(
		daily_action_quota=2,
		cooldown_seconds=60,
		schedule_enabled=True,
		work_start_hour=9,
		work_end_hour=18,
	)
	state: dict[str, object] = {}

	assert policy.evaluate(state, now=_time(10)).allowed is True
	policy.record_action(state, now=_time(10))

	cooling = policy.evaluate(state, now=_time(10, second=30))
	assert cooling.allowed is False
	assert cooling.reason == "cooldown"

	assert policy.evaluate(state, now=_time(10, 1)).allowed is True
	policy.record_action(state, now=_time(10, 1))
	quota = policy.evaluate(state, now=_time(10, 2))
	assert quota.allowed is False
	assert quota.reason == "daily_quota"
	assert state["pacing"]["count"] == 2  # type: ignore[index]


def test_pacing_applies_lunch_and_weekend_reduced_quota() -> None:
	"""午休和周末可配置为降量而非无提示地继续执行。"""
	policy = PacingPolicy(
		daily_action_quota=10,
		cooldown_seconds=0,
		schedule_enabled=True,
		work_start_hour=9,
		work_end_hour=18,
		lunch_start_hour=12,
		lunch_end_hour=14,
		lunch_quota_factor=0.2,
		weekend_quota_factor=0.1,
	)

	lunch = policy.evaluate({}, now=_time(13))
	assert lunch.allowed is True
	assert lunch.effective_quota == 2

	weekend = policy.evaluate({}, now=datetime(2026, 7, 25, 10, tzinfo=timezone.utc))
	assert weekend.allowed is True
	assert weekend.effective_quota == 1


def test_pacing_persists_random_start_gate_once_per_day() -> None:
	"""随机启动延迟写入状态，服务重启后不会重新随机一遍。"""
	policy = PacingPolicy(
		daily_action_quota=10,
		random_start_jitter_seconds=120,
		random_source=lambda: 90,
	)
	state: dict[str, object] = {}

	first = policy.evaluate(state, now=_time(8))
	assert first.allowed is False
	assert first.reason == "startup_jitter"
	startup_at = state["pacing"]["startup_at"]  # type: ignore[index]

	reloaded_state = {"pacing": {"day": "2026-07-27", "count": 0, "startup_at": startup_at}}
	second = policy.evaluate(reloaded_state, now=_time(8, 1, 31))
	assert second.allowed is True
	assert reloaded_state["pacing"]["startup_at"] == startup_at  # type: ignore[index]


def test_pacing_status_projects_budget_window_and_recovery_reason() -> None:
	"""工作台应看到和自动化引擎相同的额度、时段和冷却原因。"""
	policy = PacingPolicy(
		daily_action_quota=4,
		cooldown_seconds=60,
		schedule_enabled=True,
		work_start_hour=9,
		work_end_hour=18,
	)
	state: dict[str, object] = {}

	ready = build_pacing_status(policy, state, now=_time(10))

	assert ready == {
		"configured": True,
		"day": "2026-07-27",
		"count": 0,
		"daily_action_quota": 4,
		"effective_quota": 4,
		"remaining": 4,
		"allowed": True,
		"reason": "",
		"reason_label": "当前时段允许执行",
		"pause_until": "",
		"last_action_at": "",
		"schedule_enabled": True,
		"window": "work_hours",
		"window_label": "工作时段",
		"cooldown_seconds": 60,
	}

	policy.record_action(state, now=_time(10))
	cooling = build_pacing_status(policy, state, now=_time(10, second=30))

	assert cooling["allowed"] is False
	assert cooling["reason"] == "cooldown"
	assert cooling["reason_label"] == "动作冷却中，请在提示时间后再试"
	assert cooling["count"] == 1
	assert cooling["remaining"] == 3
	assert cooling["pause_until"] == "2026-07-27T10:01:00+00:00"


def test_pacing_status_explains_zero_quota_outside_work_hours() -> None:
	"""非工作时段额度为零时，页面应说明是时段策略而非登录失败。"""
	policy = PacingPolicy(
		daily_action_quota=10,
		schedule_enabled=True,
		work_start_hour=9,
		work_end_hour=18,
		off_hours_quota_factor=0,
	)

	status = build_pacing_status(policy, {}, now=_time(22))

	assert status["allowed"] is False
	assert status["reason"] == "daily_quota"
	assert status["reason_label"] == "非工作时段暂不执行"
	assert status["window"] == "off_hours"
	assert status["effective_quota"] == 0
