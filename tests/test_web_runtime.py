"""本地招聘控制台运行时的行为测试。"""

from pathlib import Path
from threading import Event, Lock

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult
from boss_agent_cli.recruiting.automation_coordinator import ConversationSeed
from boss_agent_cli.recruiting.automation_coordinator import AutomationCoordinator
from boss_agent_cli.recruiting.automation_queue import AutomationQueueStore
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def test_login_is_single_flight_and_exposes_no_authentication_material(tmp_path: Path) -> None:
	"""重复点击登录只能启动一个官方页面任务，状态也不得包含凭据。"""
	entered = Event()
	release = Event()
	calls = 0

	def login() -> None:
		nonlocal calls
		calls += 1
		entered.set()
		release.wait(timeout=1)

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=login,
		has_saved_login=lambda: False,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.start_login()["state"] == "running"
	assert entered.wait(timeout=1)
	assert runtime.start_login()["state"] == "running"
	assert calls == 1
	assert "token" not in runtime.status()["login"]

	release.set()
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["login"]["state"] == "succeeded"


def test_open_login_page_reports_opened_without_waiting_for_login_confirmation() -> None:
	"""打开登录页是独立动作，不能等待用户扫码而让按钮看起来无响应。"""
	opened = Event()

	def open_page() -> None:
		opened.set()

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: False,
		open_login_page=open_page,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.start_open_login_page() == {"state": "running"}
	assert opened.wait(timeout=1)
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["login"] == {
		"state": "page_opened",
		"notice": "BOSS 登录页已打开，请在专用 RPA 浏览器中完成登录后刷新状态",
	}


def test_open_login_page_reports_logged_in_when_live_rpa_session_is_already_valid() -> None:
	"""已登录时打开官方页后应保留实时登录结论，不能误报需要重新登录。"""
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: False,
		open_login_page=lambda: None,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.start_open_login_page() == {"state": "running"}
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["login"] == {"state": "succeeded"}


def test_saved_cookie_does_not_mark_console_as_logged_in_without_live_rpa_session() -> None:
	"""历史 Cookie 不能替代当前 RPA 浏览器的实际登录校验。

	本地加密会话可能来自已关闭或已退出的旧 Chrome。控制台若只按文件存在
	解锁自动化，会让用户在未登录的 BOSS 页面上误以为可以开始操作。
	"""
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: False,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.status()["login"] == {
		"state": "idle",
		"notice": "检测到历史登录凭据，但当前 RPA 浏览器尚未登录 BOSS",
	}


def test_live_rpa_session_marks_console_as_logged_in() -> None:
	"""只有实时 RPA 探测成功时，控制台才可以显示已登录。"""
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: False,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.status()["login"] == {"state": "succeeded"}


def test_status_uses_cached_live_login_state_without_reprobing_rpa() -> None:
	"""状态轮询不得与自动化线程并发读写同一个 RPA 浏览器连接。"""
	probe_calls = 0

	def probe_live_login() -> bool:
		"""记录实时 RPA 探测次数，构造页面轮询时的共享客户端场景。"""
		nonlocal probe_calls
		probe_calls += 1
		return True

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: False,
		probe_live_login=probe_live_login,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert probe_calls == 1
	assert runtime.status()["login"] == {"state": "succeeded"}
	assert runtime.status()["login"] == {"state": "succeeded"}
	assert probe_calls == 1


def test_automation_start_is_blocked_when_live_rpa_session_is_not_logged_in() -> None:
	"""自动化写操作必须先通过实时 RPA 登录态门禁。

	历史 Cookie 只能说明磁盘上曾经有凭据，不能证明当前 BOSS 专用浏览器仍在
	招聘端页面。未登录时若启动后台循环，会反复读取登录页或 Chrome 内部缓存，
	最终在页面上刷出难以理解的底层解析失败。
	"""
	class _Coordinator:
		def start(self, **_kwargs: object) -> dict[str, object]:
			raise AssertionError("未登录时不能启动自动化后台循环")

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: False,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		automation_coordinator=_Coordinator(),
	)

	result = runtime.start_automation(job_id="job-java", source="conversation", limit=20)

	assert result == {
		"state": "blocked",
		"error": {
			"code": "RPA_BROWSER_LOGIN_REQUIRED",
			"message": "当前 RPA 浏览器尚未登录 BOSS，请先完成官方登录",
		},
	}


def test_automation_sync_is_blocked_when_live_rpa_session_is_not_logged_in() -> None:
	"""只读同步在后台检查登录，提交接口不能被 RPA 探测阻塞。"""
	class _Coordinator:
		def sync_once(self, **_kwargs: object) -> int:
			raise AssertionError("未登录时不能读取 BOSS 沟通列表")

		def status(self) -> dict[str, object]:
			return {"state": "idle", "sources": []}

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: False,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		automation_coordinator=_Coordinator(),
	)

	result = runtime.start_automation_sync(job_id="job-java")

	assert result == {"state": "running", "job_id": "job-java"}
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["automation"]["sync"]["state"] == "blocked"
	assert runtime.status()["automation"]["sync"]["error"]["code"] == "RPA_BROWSER_LOGIN_REQUIRED"


def test_automation_sync_refreshes_visible_conversation_list_snapshot() -> None:
	"""自动化同步应刷新页面同源沟通快照，避免队列与 BOSS 当前列表不同步。"""
	class _Coordinator:
		def __init__(self) -> None:
			self.records: list[dict[str, object]] = []

		def sync_once(self, **_kwargs: object) -> int:
			raise AssertionError("自动化同步必须复用页面刚读取的 BOSS 快照，不能再次读取平台列表")

		def sync_records_once(self, *, job_id: str, records: list[dict[str, object]]) -> int:
			self.job_id = job_id
			self.records = records
			return len(records)

		def status(self) -> dict[str, object]:
			return {"state": "idle", "activities": []}

	coordinator = _Coordinator()
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: (_ for _ in ()).throw(AssertionError("岗位同步不得读取未筛选的全部职位列表")),
		list_recent_conversations_for_job=lambda job_id: [
			{"friend_id": 101, "candidate_name": "许辉燃", "updated_at": "16:20", "position": job_id, "unread_count": 1},
			{"friend_id": 102, "candidate_name": "陈晨", "updated_at": "16:19", "position": job_id, "unread_count": 2},
		],
		automation_coordinator=coordinator,
	)

	result = runtime.start_automation_sync(job_id="job-java")
	runtime.wait_for_idle(timeout=1)
	state = runtime.status()

	assert result["state"] == "running"
	assert state["automation"]["sync"] == {"state": "succeeded", "synced": 2, "job_id": "job-java"}
	assert state["conversation_list"]["state"] == "succeeded"
	assert [item["candidate_name"] for item in state["conversation_list"]["items"]] == ["许辉燃", "陈晨"]
	assert all("friend_id" not in item for item in state["conversation_list"]["items"])
	assert [record["friend_id"] for record in coordinator.records] == [101, 102]
	assert all(record["position"] == "job-java" for record in coordinator.records)


def test_conversation_list_refresh_reads_selected_boss_job(tmp_path: Path) -> None:
	"""在线简历岗位筛选必须通过岗位读取器进入对应 BOSS 沟通列表。"""
	requested_job_ids: list[str] = []
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: (_ for _ in ()).throw(AssertionError("选择岗位后不能读取全部列表")),
		list_recent_conversations_for_job=lambda job_id: requested_job_ids.append(job_id) or [
			{"friend_id": 101, "candidate_name": "岗位候选人"},
		],
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)

	assert runtime.start_conversation_list(force=True, job_id="job-support") == {"state": "running"}
	runtime.wait_for_idle(timeout=1)

	listing = runtime.status()["conversation_list"]
	assert requested_job_ids == ["job-support"]
	assert listing["job_id"] == "job-support"
	assert [item["candidate_name"] for item in listing["items"]] == ["岗位候选人"]


def test_automation_sync_does_not_mislabel_processing_error_as_login_failure() -> None:
	"""沟通列表处理异常必须保留为处理失败，不能误导用户重复登录。"""
	class _Coordinator:
		def sync_records_once(self, **_kwargs: object) -> int:
			raise AssertionError("列表读取失败时不应写入队列")

		def status(self) -> dict[str, object]:
			return {"state": "idle", "activities": []}

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: [],
		list_recent_conversations_for_job=lambda _job_id: (_ for _ in ()).throw(ValueError("页面字段异常")),
		automation_coordinator=_Coordinator(),
	)

	runtime.start_automation_sync(job_id="job-java")
	runtime.wait_for_idle(timeout=1)
	state = runtime.status()

	assert state["automation"]["sync"] == {
		"state": "failed",
		"error": {
			"code": "CONVERSATION_SYNC_FAILED",
			"message": "BOSS 沟通列表同步处理失败，请查看服务日志后重试",
			"detail": "ValueError",
		},
	}


def test_automation_sync_times_out_instead_of_remaining_running(monkeypatch) -> None:
	"""同步流程超时后必须显示受控失败，不能永久占用工作台状态。"""
	import boss_agent_cli.web.runtime as runtime_module

	entered = Event()
	release = Event()

	class _Coordinator:
		def sync_records_once(self, **_kwargs: object) -> int:
			raise AssertionError("同步读取未完成时不应写入队列")

		def status(self) -> dict[str, object]:
			return {"state": "idle", "activities": []}

	def read_for_job(_job_id: str) -> list[dict[str, object]]:
		entered.set()
		release.wait(timeout=1)
		return []

	monkeypatch.setattr(runtime_module, "_AUTOMATION_SYNC_TIMEOUT_SECONDS", 0.01, raising=False)
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: [],
		list_recent_conversations_for_job=read_for_job,
		automation_coordinator=_Coordinator(),
	)

	assert runtime.start_automation_sync(job_id="job-java")["state"] == "running"
	assert entered.wait(timeout=1)
	deadline = runtime_module.time.monotonic() + 1
	while runtime.status()["automation"]["sync"]["state"] == "running" and runtime_module.time.monotonic() < deadline:
		runtime_module.time.sleep(0.01)

	assert runtime.status()["automation"]["sync"] == {
		"state": "failed",
		"job_id": "job-java",
		"error": {
			"code": "CONVERSATION_SYNC_TIMEOUT",
			"message": "BOSS 沟通列表同步超时，后台任务已停止接收结果，请稍后重试",
		},
	}

	release.set()
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["automation"]["sync"]["state"] == "failed"


def test_automation_sync_reports_platform_busy_without_waiting_for_full_flow() -> None:
	"""全流程占用 BOSS 页面时，同步应立即返回忙碌而不是等待超时。"""
	class _Coordinator:
		def sync_records_once(self, **_kwargs: object) -> int:
			raise AssertionError("平台忙时不应写入同步队列")

		def status(self) -> dict[str, object]:
			return {"state": "running", "activities": []}

	platform_lock = Lock()
	platform_lock.acquire()
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: [],
		list_recent_conversations_for_job=lambda _job_id: [],
		automation_coordinator=_Coordinator(),
		platform_operation_lock=platform_lock,
	)

	try:
		assert runtime.start_automation_sync(job_id="job-java")["state"] == "running"
		runtime.wait_for_idle(timeout=1)
		state = runtime.status()["automation"]["sync"]
		assert state == {
			"state": "failed",
			"job_id": "job-java",
			"error": {
				"code": "CONVERSATION_SYNC_BUSY",
				"message": "BOSS 页面当前正被自动化占用，本次同步未执行，请等待当前步骤完成后重试",
			},
		}
	finally:
		platform_lock.release()


def test_automation_candidates_follow_latest_sync_snapshot_order(tmp_path: Path) -> None:
	"""自动化队列展示必须跟随最近一次 BOSS 同步快照，而不是本地历史排序。"""
	queue = AutomationQueueStore(tmp_path)
	historical_resume = tmp_path / "historical.pdf"
	historical_resume.write_bytes(b"historical")
	queue.upsert_candidate(friend_id=99, job_id="job-java", candidate_name="历史候选人", source="conversation")
	queue.record_final_review(
		friend_id=99,
		job_id="job-java",
		score=99,
		recommendation="invite_to_interview",
		resume_path=historical_resume,
	)
	coordinator = AutomationCoordinator(
		queue=queue,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=3, candidate_name="谭金武"),
			ConversationSeed(friend_id=1, candidate_name="许辉燃"),
		],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		list_recent_conversations=lambda: [
			{"friend_id": 3, "candidate_name": "谭金武", "updated_at": "16:14"},
			{"friend_id": 1, "candidate_name": "许辉燃", "updated_at": "16:20"},
		],
		list_recent_conversations_for_job=lambda _job_id: [
			{"friend_id": 3, "candidate_name": "谭金武", "updated_at": "16:14"},
			{"friend_id": 1, "candidate_name": "许辉燃", "updated_at": "16:20"},
		],
		automation_coordinator=coordinator,
	)

	runtime.start_automation_sync(job_id="job-java")
	runtime.wait_for_idle(timeout=1)
	snapshot = runtime.automation_candidates(job_id="job-java")

	assert [row["candidate_name"] for row in snapshot["candidates"]] == ["谭金武", "许辉燃"]
	assert "历史候选人" not in {row["candidate_name"] for row in snapshot["candidates"]}


def test_candidate_contact_action_resolves_only_current_job_friend_id(tmp_path: Path) -> None:
	"""候选人列表动作必须由当前岗位候选人键解析 friend_id，不能由浏览器传入。"""
	from types import SimpleNamespace

	from boss_agent_cli.recruiting.interview_settings import InterviewInvitationSettingsStore

	queue = AutomationQueueStore(tmp_path)
	candidate = queue.upsert_candidate(friend_id=42, job_id="job-java", candidate_name="候选人", source="conversation")
	requests: list[tuple[int, str]] = []
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		automation_coordinator=SimpleNamespace(queue=queue),
		interview_settings_store=InterviewInvitationSettingsStore(tmp_path),
		request_contact_exchange=lambda friend_id, contact_type: requests.append((friend_id, contact_type)) or {"code": 0},
	)

	result = runtime.start_automation_candidate_action(job_id="job-java", candidate_key=candidate.candidate_key, action="phone")
	runtime.wait_for_idle(timeout=1)

	assert result == {"state": "running", "candidate_key": candidate.candidate_key, "action": "phone"}
	assert requests == [(42, "phone")]
	assert runtime.automation_candidate_action_status(candidate_key=candidate.candidate_key) == {
		"state": "succeeded", "candidate_key": candidate.candidate_key, "action": "phone",
	}


def test_candidate_interview_action_requires_saved_settings(tmp_path: Path) -> None:
	"""未保存日期和时间时不能尝试打开 BOSS 约面试弹窗。"""
	from types import SimpleNamespace

	from boss_agent_cli.recruiting.interview_settings import InterviewInvitationSettingsStore

	queue = AutomationQueueStore(tmp_path)
	candidate = queue.upsert_candidate(friend_id=42, job_id="job-java", candidate_name="候选人", source="conversation")
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
		automation_coordinator=SimpleNamespace(queue=queue),
		interview_settings_store=InterviewInvitationSettingsStore(tmp_path),
		invite_interview=lambda *_args: (_ for _ in ()).throw(AssertionError("不应发起邀约")),
	)

	result = runtime.start_automation_candidate_action(job_id="job-java", candidate_key=candidate.candidate_key, action="interview")

	assert result["state"] == "failed"
	assert result["error"]["code"] == "INTERVIEW_SETTINGS_REQUIRED"


def test_download_is_blocked_outside_research_mode() -> None:
	"""控制台不得以页面操作绕过既有 operating_mode 合规边界。"""
	runtime = LocalConsoleRuntime(
		operating_mode="assisted",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	result = runtime.start_download(geek_id="g", job_id="j", security_id="s", output=None, output_dir=None)

	assert result == {
		"state": "blocked",
		"error": {"code": "COMPLIANCE_BLOCKED", "message": "下载在线简历需要显式启用 research 模式"},
	}


def test_download_status_contains_only_export_metadata(tmp_path: Path) -> None:
	"""下载完成后的任务状态只能保存文件元数据，不能保留简历正文。"""
	expected = ResumeExportResult(
		path=tmp_path / "candidate.md",
		filename="candidate.md",
		bytes_written=12,
		candidate_name="张三",
		geek_id="g",
		exported_at="2026-07-30T12:00:00",
		sections=["basic"],
	)
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: expected,
	)

	assert runtime.start_download(geek_id="g", job_id="j", security_id="s", output=None, output_dir=None)["state"] == "running"
	runtime.wait_for_idle(timeout=1)

	result = runtime.status()["download"]
	assert result["state"] == "succeeded"
	assert result["result"] == {
		"geek_id": "g",
		"candidate_name": "张三",
		"path": str(tmp_path / "candidate.md"),
		"filename": "candidate.md",
		"bytes_written": 12,
		"sections": ["basic"],
		"exported_at": "2026-07-30T12:00:00",
	}
	assert "简历正文" not in str(result)
