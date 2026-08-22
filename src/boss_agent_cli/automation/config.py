"""Automation configuration parsing with conservative defaults."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Final

from boss_agent_cli.automation.models import AutomationMode, PlatformAction

DEFAULT_ALLOWED_ACTIONS: Final = (
	PlatformAction.SCAN_CONVERSATIONS,
	PlatformAction.READ_CANDIDATE_PROFILE,
	PlatformAction.SEND_QUESTIONNAIRE,
	PlatformAction.SEND_FOLLOW_UP,
	PlatformAction.EXCHANGE_CONTACT,
	PlatformAction.CREATE_INTERVIEW_LEAD,
)


@unique
class ReplyStrategy(str, Enum):
	TEMPLATE = "template"
	LOCAL_AI = "local_ai"
	HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class AutomationConfig:
	mode: AutomationMode = AutomationMode.AUTONOMOUS
	platforms: tuple[str, ...] = ("zhilian", "zhipin")
	allowed_actions: tuple[PlatformAction, ...] = DEFAULT_ALLOWED_ACTIONS
	human_review_threshold: float = 0.65
	auto_execute_threshold: float = 0.82
	max_actions_per_run: int = 50
	max_consecutive_errors: int = 3
	# 以下字段是持久化节奏保护；默认值兼容旧版行为（不额外等待、全天可运行），
	# 用户可以在配置文件中显式收紧额度和工作时段。
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
	tabs: tuple[str, ...] = ("新招呼", "未读")
	max_per_tab: int = 20
	questionnaire_message: str = "您好，想确认下近期是否看机会？"
	follow_up_message: str = (
		"谢谢回复，我这边同步岗位信息，方便的话可以继续沟通面试时间。"
	)
	reply_strategy: ReplyStrategy = ReplyStrategy.HYBRID
	stop_on_page_text: tuple[str, ...] = (
		"验证码",
		"安全验证",
		"操作频繁",
		"账号异常",
		"访问受限",
	)


_DEFAULT = AutomationConfig()


def automation_config_from_dict(raw: dict[str, Any] | None) -> AutomationConfig:
	"""Parse automation config from config.json-compatible data."""
	data = raw or {}
	default_actions = [action.value for action in DEFAULT_ALLOWED_ACTIONS]
	allowed_action_values = {action.value for action in PlatformAction}
	actions = tuple(
		PlatformAction(item)
		for item in data.get("allowed_actions", default_actions)
		if item in allowed_action_values
	)
	return AutomationConfig(
		mode=AutomationMode(data.get("mode", _DEFAULT.mode.value)),
		platforms=tuple(str(item) for item in data.get("platforms", _DEFAULT.platforms)),
		allowed_actions=actions or DEFAULT_ALLOWED_ACTIONS,
		human_review_threshold=float(data.get("human_review_threshold", _DEFAULT.human_review_threshold)),
		auto_execute_threshold=float(data.get("auto_execute_threshold", _DEFAULT.auto_execute_threshold)),
		max_actions_per_run=int(data.get("max_actions_per_run", _DEFAULT.max_actions_per_run)),
		max_consecutive_errors=int(data.get("max_consecutive_errors", _DEFAULT.max_consecutive_errors)),
		daily_action_quota=int(data.get("daily_action_quota", _DEFAULT.daily_action_quota)),
		cooldown_seconds=int(data.get("cooldown_seconds", _DEFAULT.cooldown_seconds)),
		schedule_enabled=bool(data.get("schedule_enabled", _DEFAULT.schedule_enabled)),
		work_start_hour=int(data.get("work_start_hour", _DEFAULT.work_start_hour)),
		work_end_hour=int(data.get("work_end_hour", _DEFAULT.work_end_hour)),
		lunch_start_hour=int(data.get("lunch_start_hour", _DEFAULT.lunch_start_hour)),
		lunch_end_hour=int(data.get("lunch_end_hour", _DEFAULT.lunch_end_hour)),
		lunch_quota_factor=float(data.get("lunch_quota_factor", _DEFAULT.lunch_quota_factor)),
		weekend_quota_factor=float(data.get("weekend_quota_factor", _DEFAULT.weekend_quota_factor)),
		off_hours_quota_factor=float(data.get("off_hours_quota_factor", _DEFAULT.off_hours_quota_factor)),
		random_start_jitter_seconds=int(
			data.get("random_start_jitter_seconds", _DEFAULT.random_start_jitter_seconds)
		),
		tabs=tuple(str(item) for item in data.get("tabs", _DEFAULT.tabs)),
		max_per_tab=int(data.get("max_per_tab", _DEFAULT.max_per_tab)),
		questionnaire_message=str(data.get("questionnaire_message", _DEFAULT.questionnaire_message)),
		follow_up_message=str(data.get("follow_up_message", _DEFAULT.follow_up_message)),
		reply_strategy=ReplyStrategy(data.get("reply_strategy", _DEFAULT.reply_strategy.value)),
		stop_on_page_text=tuple(str(item) for item in data.get("stop_on_page_text", _DEFAULT.stop_on_page_text)),
	)
