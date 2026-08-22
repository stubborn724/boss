"""BOSS 岗位自定义招呼语同步测试。"""

from boss_agent_cli.rpa.boss_client import BossRPAClient


def _configure_job_greeting_steps(
	monkeypatch,
	client: BossRPAClient,
	*,
	persisted: bool = True,
	save_result: bool = True,
) -> list[str]:
	"""用可观察的步骤替身固定岗位话术同步的边界。"""
	calls: list[str] = []
	monkeypatch.setattr(client, "navigate_to", lambda _url: calls.append("navigate"))
	monkeypatch.setattr(client, "wait_loaded", lambda: calls.append("loaded"))
	monkeypatch.setattr(client, "human_delay", lambda *_args: calls.append("delay"))
	monkeypatch.setattr(client, "_click_greeting_job_tab", lambda: calls.append("job_tab") or True, raising=False)
	monkeypatch.setattr(client, "_job_greeting_matches", lambda _job_name, _content: False, raising=False)
	monkeypatch.setattr(client, "_wait_for_existing_job_greeting", lambda _job_name, _content, **_kwargs: False, raising=False)
	monkeypatch.setattr(client, "_open_job_greeting_editor", lambda: calls.append("open_editor") or True, raising=False)
	monkeypatch.setattr(client, "_select_job_for_greeting", lambda job_name: calls.append(f"select:{job_name}") or True, raising=False)
	monkeypatch.setattr(client, "_wait_for_visible_textarea", lambda: calls.append("wait_input") or True)
	monkeypatch.setattr(client, "_save_job_greeting", lambda content: calls.append(f"save:{content}") or save_result, raising=False)
	monkeypatch.setattr(client, "_wait_for_job_greeting_persisted", lambda job_name, content, **_kwargs: calls.append(f"persisted:{job_name}:{content}") or persisted, raising=False)
	return calls


def test_probe_live_login_rejects_boss_login_page_without_navigation(monkeypatch) -> None:
	"""实时登录探测只读当前 URL，BOSS 登录页必须返回未登录。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "ensure_session", lambda: None)
	monkeypatch.setattr(client, "_eval", lambda script, **_kwargs: "https://www.zhipin.com/web/user/"
		if script == "window.location.href" else None)

	assert client.probe_live_login() is False


def test_probe_live_login_accepts_recruiter_page_without_navigation(monkeypatch) -> None:
	"""实时登录探测在招聘页面时返回已登录。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "ensure_session", lambda: None)
	monkeypatch.setattr(client, "_eval", lambda script, **_kwargs: "https://www.zhipin.com/web/chat/index"
		if script == "window.location.href" else None)

	assert client.probe_live_login() is True


def test_reset_session_discards_previous_cdp_page_binding() -> None:
	"""登录页新开标签后，下一次列表操作必须重新选择 BOSS 页面。"""
	from threading import RLock

	client = object.__new__(BossRPAClient)
	client._cdp_session_lock = RLock()
	client._ws_sock = object()
	client._ws_url = "ws://127.0.0.1/devtools/page/old"

	client.reset_session()

	assert client._ws_sock is None
	assert client._ws_url is None


def test_sync_job_greeting_uses_job_specific_editor_and_exact_job(monkeypatch) -> None:
	"""岗位招呼语必须在弹窗中选择目标职位后输入，不能误改通用招呼语。"""
	client = object.__new__(BossRPAClient)
	calls = _configure_job_greeting_steps(monkeypatch, client)

	result = client.sync_job_greeting("Java", "您好")

	assert result["code"] == 0
	assert calls == [
		"navigate", "loaded", "delay", "job_tab", "open_editor", "select:Java",
		"wait_input", "save:您好", "persisted:Java:您好",
	]
	assert result["zpData"] == {"verified": True, "job_name": "Java"}


def test_sync_job_greeting_opens_the_v2_settings_route(monkeypatch) -> None:
	"""岗位招呼语必须直达 v2 页面，旧聊天壳 iframe 已无法正确渲染该页面。"""
	client = object.__new__(BossRPAClient)
	navigated_urls: list[str] = []
	monkeypatch.setattr(client, "navigate_to", lambda url: navigated_urls.append(url))
	monkeypatch.setattr(client, "wait_loaded", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_click_greeting_job_tab", lambda: True, raising=False)
	monkeypatch.setattr(client, "_job_greeting_matches", lambda _name, _content: False, raising=False)
	monkeypatch.setattr(client, "_wait_for_existing_job_greeting", lambda _name, _content, **_kwargs: False, raising=False)
	monkeypatch.setattr(client, "_open_job_greeting_editor", lambda: True, raising=False)
	monkeypatch.setattr(client, "_select_job_for_greeting", lambda _name: True, raising=False)
	monkeypatch.setattr(client, "_wait_for_visible_textarea", lambda: True)
	monkeypatch.setattr(client, "_save_job_greeting", lambda _content: True, raising=False)
	monkeypatch.setattr(client, "_wait_for_job_greeting_persisted", lambda _name, _content, **_kwargs: True, raising=False)

	assert client.sync_job_greeting("Java", "您好")["code"] == 0
	assert navigated_urls == ["https://www.zhipin.com/web/frame/info_v2/set/greeting"]


def test_job_greeting_steps_support_the_current_v2_dom(monkeypatch) -> None:
	"""新版设置页的页签、弹窗和职位单选行应使用 v2 选择器完成定位。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []
	responses = iter((True, True, True, True, True, True))

	def _evaluate(script: str, **_kwargs: object) -> bool:
		scripts.append(script)
		return next(responses)

	monkeypatch.setattr(client, "_eval", _evaluate)
	monkeypatch.setattr("boss_agent_cli.rpa.boss_client.time.sleep", lambda _seconds: None)

	assert client._click_greeting_job_tab()
	assert client._open_job_greeting_editor()
	assert client._select_job_for_greeting("Java")
	assert client._wait_for_visible_textarea(timeout=0.1)
	assert client._save_job_greeting("您好") is None

	joined = "\n".join(scripts)
	assert ".tab-header .tab-item" in joined
	assert ".gjs-overlay" in joined
	assert ".job-list label.b-radio" in joined


def test_open_job_greeting_editor_waits_for_v2_button_after_tab_switch(monkeypatch) -> None:
	"""v2 页签切换后按钮异步挂载，首次未找到时应继续等待而不是直接失败。"""
	client = object.__new__(BossRPAClient)
	responses = iter((False, True, True))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))
	monkeypatch.setattr("boss_agent_cli.rpa.boss_client.time.sleep", lambda _seconds: None)

	assert client._open_job_greeting_editor()


def test_open_job_greeting_editor_recognizes_fixed_v2_overlay(monkeypatch) -> None:
	"""v2 弹窗采用 fixed 定位，不能再用 offsetParent 判定是否可见。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []

	def _evaluate(script: str, **_kwargs: object) -> bool:
		scripts.append(script)
		return True

	monkeypatch.setattr(client, "_eval", _evaluate)

	assert client._open_job_greeting_editor()
	assert "getClientRects" in scripts[0]


def test_sync_job_greeting_does_not_report_success_before_job_appears_in_configured_list(monkeypatch) -> None:
	"""保存点击成功不足以说明已持久化，目标职位未出现在配置列表时必须明确失败。"""
	client = object.__new__(BossRPAClient)
	_configure_job_greeting_steps(monkeypatch, client, persisted=False)

	result = client.sync_job_greeting("Java", "您好")

	assert result["code"] == -1
	assert "保存回显校验失败" in result["message"]


def test_sync_job_greeting_uses_persisted_message_when_save_click_has_no_cdp_result(monkeypatch) -> None:
	"""保存会关闭弹窗，CDP 点击结果丢失时仍应以岗位列表中的完整话术为准。"""
	client = object.__new__(BossRPAClient)
	_configure_job_greeting_steps(monkeypatch, client, save_result=False, persisted=True)

	result = client.sync_job_greeting("Java", "您好")

	assert result["code"] == 0


def test_sync_job_greeting_allows_time_for_boss_save_refresh(monkeypatch) -> None:
	"""BOSS 保存后异步刷新列表，回读窗口不能沿用编辑控件的短等待时间。"""
	client = object.__new__(BossRPAClient)
	_configure_job_greeting_steps(monkeypatch, client)
	wait_timeouts: list[float] = []
	monkeypatch.setattr(
		client,
		"_wait_for_job_greeting_persisted",
		lambda _job_name, _content, *, timeout: wait_timeouts.append(timeout) or True,
		raising=False,
	)

	assert client.sync_job_greeting("Java", "您好")["code"] == 0
	assert wait_timeouts == [12.0]


def test_sync_job_greeting_reuses_matching_boss_configuration(monkeypatch) -> None:
	"""已配置相同岗位话术时不重复保存，避免 BOSS 对相同内容不提交而阻塞流程。"""
	client = object.__new__(BossRPAClient)
	calls: list[str] = []
	monkeypatch.setattr(client, "navigate_to", lambda _url: calls.append("navigate"))
	monkeypatch.setattr(client, "wait_loaded", lambda: calls.append("loaded"))
	monkeypatch.setattr(client, "human_delay", lambda *_args: calls.append("delay"))
	monkeypatch.setattr(client, "_click_greeting_job_tab", lambda: calls.append("job_tab") or True, raising=False)
	monkeypatch.setattr(client, "_job_greeting_matches", lambda _name, _content: calls.append("matched") or True, raising=False)
	monkeypatch.setattr(client, "_open_job_greeting_editor", lambda: (_ for _ in ()).throw(AssertionError("不应重复打开编辑框")), raising=False)

	result = client.sync_job_greeting("Java", "您好")

	assert result == {"code": 0, "zpData": {"verified": True, "job_name": "Java"}}
	assert calls == ["navigate", "loaded", "delay", "job_tab", "matched"]


def test_wait_for_existing_job_greeting_retries_until_v2_list_is_rendered(monkeypatch) -> None:
	"""岗位页签后的列表异步渲染时，首次未匹配不能直接当作未配置。"""
	client = object.__new__(BossRPAClient)
	matched = iter((False, True))
	monkeypatch.setattr(client, "_job_greeting_matches", lambda _name, _content: next(matched), raising=False)
	monkeypatch.setattr("boss_agent_cli.rpa.boss_client.time.sleep", lambda _seconds: None)

	assert client._wait_for_existing_job_greeting("Java", "您好", timeout=0.1)


def test_persisted_greeting_reads_the_job_name_child_before_its_label(monkeypatch) -> None:
	"""职位标签会包含“职位：”前缀，校验必须优先读取其纯名称子元素。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(
		client,
		"_eval",
		lambda script, **_kwargs: ".job-name .text-ellipsis') ||" in script,
	)

	assert client._wait_for_job_greeting_persisted("Java", "您好", timeout=0.1)


def test_select_recommendation_job_waits_for_delayed_frame_and_accepts_current_job_label(monkeypatch) -> None:
	"""推荐 iframe 延迟加载时，当前 ``.job-item.curr`` 的岗位标签应被识别。"""
	client = object.__new__(BossRPAClient)
	responses = iter((
		{"status": "loading"},
		{"status": "selected"},
	))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))
	monkeypatch.setattr("boss_agent_cli.rpa.boss_client.time.sleep", lambda _seconds: None)

	assert client._select_recommendation_job("Java") is True


def test_greet_recommendation_targets_the_exact_geek_id_and_requires_page_confirmation(monkeypatch) -> None:
	"""推荐页打招呼必须用稳定 geek_id 定位，并以卡片回显确认发送。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "greet_rec_list", lambda: {"code": 0, "zpData": {"geekList": []}})
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	responses = iter((
		{"status": "clicked", "geekId": "geek-42", "name": "候选人"},
		{"status": "sent", "geekId": "geek-42", "name": "候选人"},
	))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.greet_recommendation_by_geek_id("geek-42")

	assert result == {
		"code": 0,
		"zpData": {"geek_id": "geek-42", "candidate_name": "候选人", "status": "sent"},
	}


def test_greet_recommendation_refuses_when_target_card_is_not_found(monkeypatch) -> None:
	"""找不到目标卡片时不允许按序号或姓名猜测发送对象。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "greet_rec_list", lambda: {"code": 0, "zpData": {"geekList": []}})
	monkeypatch.setattr(client, "_eval", lambda _script: {"status": "not_found"})

	result = client.greet_recommendation_by_geek_id("geek-42")

	assert result == {"code": -1, "message": "推荐页未找到目标候选人"}


def test_greet_recommendation_returns_stable_daily_quota_error(monkeypatch) -> None:
	"""BOSS 明确返回今日沟通上限时，适配器必须区分普通发送失败。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "greet_rec_list", lambda: {"code": 0, "zpData": {"geekList": []}})
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	responses = iter((
		{"status": "clicked", "geekId": "geek-42", "name": "候选人"},
		{"status": "quota", "message": "该职位今日沟通达上限"},
	))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.greet_recommendation_by_geek_id("geek-42")

	assert result["error_code"] == "RECOMMENDATION_DAILY_QUOTA_REACHED"
