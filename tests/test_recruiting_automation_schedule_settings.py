"""招聘自动化双来源定时设置测试。"""

from datetime import datetime

from boss_agent_cli.recruiting.automation_schedule_settings import (
	AutomationScheduleMonitor,
	AutomationScheduleSettings,
	AutomationScheduleSettingsStore,
)


def test_schedule_settings_are_isolated_by_source_and_persist(tmp_path) -> None:
	"""沟通列表与推荐牛人的配置必须独立持久化，服务重启后只恢复配置。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	conversation = AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="09:00",
		end_time="18:00",
		interval_minutes=20,
		limit=20,
		daily_quota=100,
		weekdays=(0, 1, 2, 3, 4),
	)
	recommendation = AutomationScheduleSettings(
		enabled=True,
		job_id="job-support",
		start_time="10:00",
		end_time="17:30",
		interval_minutes=60,
		limit=10,
		daily_quota=30,
		weekdays=(1, 3, 5),
	)

	store.save(source="conversation", settings=conversation)
	store.save(source="recommendation", settings=recommendation)
	reloaded = AutomationScheduleSettingsStore(tmp_path)

	assert reloaded.get(source="conversation") == conversation
	assert reloaded.get(source="recommendation") == recommendation


def test_schedule_settings_decide_time_window_without_restoring_runtime_state() -> None:
	"""配置只判断允许启动的窗口，不保存上次运行线程或未完成平台动作。"""
	settings = AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="09:00",
		end_time="18:00",
		weekdays=(0, 1, 2, 3, 4),
	)

	assert settings.is_active_at(datetime(2026, 8, 19, 10, 0)) is True
	assert settings.is_active_at(datetime(2026, 8, 19, 18, 0)) is False
	assert settings.is_active_at(datetime(2026, 8, 22, 10, 0)) is False


def test_full_flow_schedule_supports_window_crossing_midnight() -> None:
	"""全流程定时任务支持 20:00 到次日 09:00 的跨午夜窗口。"""
	settings = AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="20:00",
		end_time="09:00",
		weekdays=(0, 1, 2, 3, 4, 5, 6),
	)

	assert settings.is_active_at(datetime(2026, 8, 19, 20, 0)) is True
	assert settings.is_active_at(datetime(2026, 8, 20, 8, 59)) is True
	assert settings.is_active_at(datetime(2026, 8, 20, 9, 0)) is False


def test_full_flow_schedule_is_a_persisted_source(tmp_path) -> None:
	"""全流程定时任务与两个独立来源一样持久化且互不覆盖。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	settings = AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="20:00",
		end_time="09:00",
		limit=20,
		daily_quota=20,
		weekdays=(0, 1, 2, 3, 4, 5, 6),
	)

	store.save(source="full_flow", settings=settings)

	assert store.get(source="full_flow") == settings
	assert set(store.all()) == {"conversation", "recommendation", "full_flow"}


def test_schedule_settings_reject_invalid_window_and_source(tmp_path) -> None:
	"""无效时间窗口与未知来源不能进入持久化配置。"""
	store = AutomationScheduleSettingsStore(tmp_path)

	for source, settings in (
		("other", AutomationScheduleSettings()),
		("conversation", AutomationScheduleSettings(enabled=True, job_id="job-java", start_time="09:00", end_time="09:00")),
	):
		try:
			store.save(source=source, settings=settings)
		except ValueError:
			pass
		else:
			raise AssertionError("无效定时配置不应保存")


def test_schedule_monitor_starts_due_source_and_stops_at_window_end(tmp_path) -> None:
	"""到点只启动对应按钮，结束时间到达后停止本次定时运行。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	store.save(source="conversation", settings=AutomationScheduleSettings(
		enabled=True, job_id="job-java", start_time="09:00", end_time="10:00", limit=17,
	))
	starts: list[tuple[str, str, int]] = []
	stops: list[str] = []
	runtime_state: dict[str, object] = {"state": "idle", "job_id": "", "sources": []}
	monitor = AutomationScheduleMonitor(
		store=store,
		start_automation=lambda job_id, source, limit: starts.append((job_id, source, limit)) or runtime_state.update(
			{"state": "running", "job_id": job_id, "sources": [source]},
		) or dict(runtime_state),
		stop_automation=lambda source: stops.append(source) or runtime_state.update({"state": "stopped"}) or dict(runtime_state),
		automation_status=lambda: dict(runtime_state),
	)

	monitor.tick(datetime(2026, 8, 19, 9, 5))
	monitor.tick(datetime(2026, 8, 19, 9, 20))
	assert starts == [("job-java", "conversation", 17)]
	monitor.tick(datetime(2026, 8, 19, 10, 0))
	assert stops == ["conversation"]
	assert monitor.status()["conversation"]["state"] == "outside_window"


def test_schedule_monitor_waits_when_other_job_is_running(tmp_path) -> None:
	"""共享浏览器正在处理其它岗位时，新定时任务必须等待，不能合并错误岗位。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	store.save(source="recommendation", settings=AutomationScheduleSettings(
		enabled=True, job_id="job-support", start_time="09:00", end_time="18:00",
	))
	starts: list[str] = []
	monitor = AutomationScheduleMonitor(
		store=store,
		start_automation=lambda job_id, source, limit: starts.append(source) or {},
		stop_automation=lambda _source: {},
		automation_status=lambda: {"state": "running", "job_id": "job-java", "sources": ["conversation"]},
	)

	monitor.tick(datetime(2026, 8, 19, 10, 0))

	assert starts == []
	assert monitor.status()["recommendation"]["state"] == "waiting_for_other_job"


def test_schedule_monitor_starts_full_flow_as_one_exclusive_source(tmp_path) -> None:
	"""全流程到点只启动一次，并把两个独立来源视为同一项平台占用。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	store.save(source="full_flow", settings=AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="20:00",
		end_time="09:00",
		weekdays=(0, 1, 2, 3, 4, 5, 6),
	))
	starts: list[tuple[str, str, int]] = []
	runtime_state: dict[str, object] = {"state": "idle", "job_id": "", "sources": []}
	monitor = AutomationScheduleMonitor(
		store=store,
		start_automation=lambda job_id, source, limit: starts.append((job_id, source, limit)) or runtime_state.update(
			{"state": "running", "job_id": job_id, "sources": ["conversation", "recommendation"]}
		) or dict(runtime_state),
		stop_automation=lambda _source: runtime_state.update({"state": "stopping"}) or dict(runtime_state),
		automation_status=lambda: dict(runtime_state),
	)

	monitor.tick(datetime(2026, 8, 19, 20, 0))
	monitor.tick(datetime(2026, 8, 20, 8, 30))

	assert starts == [("job-java", "full_flow", 20)]
	assert monitor.status()["full_flow"]["state"] == "running"


def test_schedule_monitor_does_not_retry_recommendation_quota_until_next_day(tmp_path) -> None:
	"""推荐定时任务当天触顶后不重复提交，次日自动允许再次启动。"""
	store = AutomationScheduleSettingsStore(tmp_path)
	store.save(source="recommendation", settings=AutomationScheduleSettings(
		enabled=True,
		job_id="job-java",
		start_time="09:00",
		end_time="18:00",
		weekdays=(0, 1, 2, 3, 4, 5, 6),
	))
	starts: list[datetime] = []
	monitor = AutomationScheduleMonitor(
		store=store,
		start_automation=lambda _job_id, _source, _limit: starts.append(datetime.now()) or {
			"state": "blocked",
			"error": {
				"code": "RECOMMENDATION_DAILY_QUOTA_REACHED",
				"message": "BOSS 推荐牛人今日沟通已达上限",
			},
		},
		stop_automation=lambda _source: {},
		automation_status=lambda: {"state": "idle", "job_id": "", "sources": []},
	)

	monitor.tick(datetime(2026, 8, 19, 10, 0))
	monitor.tick(datetime(2026, 8, 19, 10, 1))
	monitor.tick(datetime(2026, 8, 20, 10, 0))

	assert len(starts) == 2
