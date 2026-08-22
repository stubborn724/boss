"""RecruiterPlatform ABC + BossRecruiterPlatform adapter 测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.platforms import get_recruiter_platform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def _mock_client():
	client = MagicMock()
	client.close = MagicMock()
	return client


def test_boss_recruiter_platform_metadata():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	assert platform.name == "zhipin-recruiter"
	assert "招聘者" in platform.display_name


def test_boss_recruiter_is_success():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	assert platform.is_success({"code": 0}) is True
	assert platform.is_success({"code": 1}) is False


def test_boss_recruiter_unwrap_data():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	response = {"code": 0, "zpData": {"jobs": [1, 2, 3]}}
	assert platform.unwrap_data(response) == {"jobs": [1, 2, 3]}


def test_boss_recruiter_parse_error():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	unified, message = platform.parse_error({"code": 9, "message": "too fast"})
	assert unified == "RATE_LIMITED"
	assert "too fast" in message


def test_boss_recruiter_parse_error_maps_invalid_request():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	unified, message = platform.parse_error({"code": 121, "message": "请求不合法(121)"})
	assert unified == "INVALID_PARAM"
	assert message == "请求不合法(121)"


def test_parse_error_send_message_121_maps_to_endpoint_deprecated():
	"""issue #217 — sendReplyMsg 端点 121 重映射为 ENDPOINT_DEPRECATED，引导用户跟 issue。"""
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	unified, message = platform.parse_error(
		{
			"code": 121,
			"message": "请求不合法(121)",
			"__cli_endpoint_hint__": "https://www.zhipin.com/wapi/zpchat/fastReply/sendReplyMsg",
		}
	)
	assert unified == "ENDPOINT_DEPRECATED"
	assert "请求不合法" in message


def test_parse_error_exchange_121_keeps_invalid_param():
	"""保护边界：exchange_request 端点的 121 根因待查，保持 INVALID_PARAM 不动。"""
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	unified, _ = platform.parse_error(
		{
			"code": 121,
			"message": "请求不合法(121)",
			"__cli_endpoint_hint__": "https://www.zhipin.com/wapi/zpchat/exchange/request",
		}
	)
	assert unified == "INVALID_PARAM"


def test_friend_list_delegates():
	client = _mock_client()
	client.friend_list.return_value = {"code": 0, "zpData": {"result": []}}
	platform = BossRecruiterPlatform(client)
	result = platform.friend_list(page=1, job_id="j1")
	client.friend_list.assert_called_once_with(page=1, job_id="j1", label_id=0)
	assert result == {"code": 0, "zpData": {"result": []}}


def test_select_conversation_job_delegates_to_rpa_client():
	"""岗位自动化必须能通过平台适配层切换 BOSS 沟通列表职位。"""
	client = _mock_client()
	client.select_conversation_job.return_value = {"code": 0, "zpData": {"selectedJobName": "Java"}}
	platform = BossRecruiterPlatform(client)

	result = platform.select_conversation_job("Java")

	client.select_conversation_job.assert_called_once_with("Java")
	assert result == {"code": 0, "zpData": {"selectedJobName": "Java"}}


def test_select_all_conversation_jobs_delegates_to_rpa_client():
	"""全部岗位读取必须显式清除 BOSS 当前职位筛选。"""
	client = _mock_client()
	client.select_all_conversation_jobs.return_value = {"code": 0, "zpData": {"selectedScope": "all"}}
	platform = BossRecruiterPlatform(client)

	result = platform.select_all_conversation_jobs()

	client.select_all_conversation_jobs.assert_called_once_with()
	assert result == {"code": 0, "zpData": {"selectedScope": "all"}}


def test_fast_conversation_snapshot_delegates_to_rpa_client():
	"""后台轮询必须经适配器取得快速未读快照，不能静默退回全量分页扫描。"""
	client = _mock_client()
	client.fast_conversation_snapshot.return_value = {"code": 0, "zpData": {"friendList": [{"friendId": 123, "unreadMsgCount": 1}]}}
	platform = BossRecruiterPlatform(client)

	result = platform.fast_conversation_snapshot()

	client.fast_conversation_snapshot.assert_called_once_with()
	assert result == {"code": 0, "zpData": {"friendList": [{"friendId": 123, "unreadMsgCount": 1}]}}


def test_fast_conversation_snapshot_forwards_full_snapshot_flag():
	"""首轮同步必须透传完整快照标记，不能重新走逐页读取。"""
	client = _mock_client()
	client.fast_conversation_snapshot.return_value = {"code": 0, "zpData": {"friendList": []}}
	platform = BossRecruiterPlatform(client)

	platform.fast_conversation_snapshot(include_all=True)

	client.fast_conversation_snapshot.assert_called_once_with(include_all=True)


def test_view_geek_delegates():
	client = _mock_client()
	client.view_geek.return_value = {"code": 0, "zpData": {"name": "Alice"}}
	platform = BossRecruiterPlatform(client)
	result = platform.view_geek("g1", "j1", security_id="s1")
	client.view_geek.assert_called_once_with("g1", job_id="j1", security_id="s1")
	assert result == {"code": 0, "zpData": {"name": "Alice"}}


def test_search_geeks_delegates():
	client = _mock_client()
	client.search_geeks.return_value = {"code": 0, "zpData": {"list": []}}
	platform = BossRecruiterPlatform(client)
	result = platform.search_geeks("Python", city="101010100", page=2)
	client.search_geeks.assert_called_once_with(
		"Python",
		city="101010100",
		page=2,
		job_id=None,
		experience=None,
		degree=None,
		age=None,
		school_level=None,
		activeness=None,
		source=None,
		select=False,
		salary=None,
	)
	assert result == {"code": 0, "zpData": {"list": []}}


def test_job_offline_delegates():
	client = _mock_client()
	client.job_offline.return_value = {"code": 0, "zpData": {}}
	platform = BossRecruiterPlatform(client)
	result = platform.job_offline("enc123")
	client.job_offline.assert_called_once_with("enc123")
	assert result == {"code": 0, "zpData": {}}


def test_send_message_delegates():
	client = _mock_client()
	client.send_message.return_value = {"code": 0, "zpData": {}}
	platform = BossRecruiterPlatform(client)
	result = platform.send_message(123, "你好")
	client.send_message.assert_called_once_with(123, "你好")
	assert result == {"code": 0, "zpData": {}}


def test_send_message_by_friend_delegates():
	client = _mock_client()
	client.send_message_by_friend.return_value = {"code": 0, "zpData": {"friendId": 123}}
	platform = BossRecruiterPlatform(client)
	result = platform.send_message_by_friend(123, "你好")
	client.send_message_by_friend.assert_called_once_with(123, "你好")
	assert result["code"] == 0


def test_context_manager_closes():
	client = _mock_client()
	with BossRecruiterPlatform(client) as platform:
		assert platform.name == "zhipin-recruiter"
	client.close.assert_called_once()


def test_enter_returns_self():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	assert platform.__enter__() is platform
	client.close.assert_not_called()


def test_close_calls_client_close():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	platform.close()
	client.close.assert_called_once()


def test_close_tolerates_client_without_close():
	platform = BossRecruiterPlatform(object())  # type: ignore[arg-type]
	platform.close()  # 不抛错


def test_exit_closes_on_exception():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	with pytest.raises(RuntimeError):
		with platform:
			raise RuntimeError("boom")
	client.close.assert_called_once()


def test_recruiter_platform_registry():
	cls = get_recruiter_platform("zhipin-recruiter")
	assert cls is BossRecruiterPlatform


# ── get_recruiter_platform_instance ───────────────────────────────────
# 命令层测试一律 mock 掉这个 helper，所以它本身此前从未被真实调用过。


def _ctx(**obj):
	return SimpleNamespace(obj=obj)


def test_recruiter_instance_uses_explicit_cdp_only_when_bridge_is_unavailable(monkeypatch):
	captured: dict = {}
	bridge = MagicMock()
	bridge.is_extension_connected.return_value = False

	class _Client:
		def __init__(self, *, cdp_url):
			captured["cdp_url"] = cdp_url

	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BridgeClient", lambda: bridge)
	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BossRPAClient", _Client)
	platform = get_recruiter_platform_instance(_ctx(cdp_url="http://localhost:9222"), MagicMock())

	assert isinstance(platform, BossRecruiterPlatform)
	assert captured["cdp_url"] == "http://localhost:9222"


def test_recruiter_instance_empty_context_requires_browser_rpa(monkeypatch):
	bridge = MagicMock()
	bridge.is_extension_connected.return_value = False
	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BridgeClient", lambda: bridge)

	with pytest.raises(Exception, match="专用 Chrome 未连接"):
		get_recruiter_platform_instance(SimpleNamespace(obj=None), MagicMock())


def test_recruiter_instance_rejects_platform_without_recruiter_adapter():
	with pytest.raises(ValueError):
		get_recruiter_platform_instance(_ctx(platform="zhilian"), MagicMock())


def test_recruiter_instance_prefers_explicit_dedicated_cdp_over_bridge(monkeypatch):
	"""专用 Chrome 已配置时，不能被普通 Chrome Bridge 抢占。"""
	bridge = MagicMock()
	bridge.is_extension_connected.return_value = True
	cdp_client = MagicMock()
	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BridgeClient", lambda: bridge)
	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BossRPAClient", lambda *, cdp_url: cdp_client)

	platform = get_recruiter_platform_instance(
		_ctx(cdp_url="http://127.0.0.1:9222"), MagicMock(),
	)

	assert isinstance(platform, BossRecruiterPlatform)
	assert platform._client is cdp_client
	bridge.is_extension_connected.assert_not_called()


def test_recruiter_instance_without_bridge_or_cdp_refuses_api_fallback(monkeypatch):
	"""招聘 RPA 未就绪必须给出错误，不得伪造 API 平台数据。"""
	from boss_agent_cli.rpa.boss_client import BossRPAConnectionError

	bridge = MagicMock()
	bridge.is_extension_connected.return_value = False
	monkeypatch.setattr("boss_agent_cli.commands._recruiter_platform.BridgeClient", lambda: bridge)

	with pytest.raises(BossRPAConnectionError, match="专用 Chrome 未连接"):
		get_recruiter_platform_instance(_ctx(), MagicMock())
