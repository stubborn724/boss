from pathlib import Path
from unittest.mock import patch

import pytest


@patch("boss_agent_cli.auth.persistent_chrome.time.sleep", return_value=None)
@patch("boss_agent_cli.auth.persistent_chrome.subprocess.Popen")
@patch("boss_agent_cli.auth.persistent_chrome.PersistentChrome._is_ready", side_effect=[False, True])
def test_ensure_running_starts_dedicated_chrome_with_local_cdp(mock_ready, mock_popen, mock_sleep, tmp_path: Path):
	"""首次使用必须启动独立 profile，并且调试端口只监听本机。"""
	from boss_agent_cli.auth.persistent_chrome import PersistentChrome

	manager = PersistentChrome(profile_dir=tmp_path / "profile", chrome_path=Path("C:/Chrome/chrome.exe"), readiness_attempts=2)

	assert manager.ensure_running() == "http://127.0.0.1:9222"
	arguments = mock_popen.call_args.args[0]
	assert arguments[0] == str(Path("C:/Chrome/chrome.exe"))
	assert "--remote-debugging-address=127.0.0.1" in arguments
	assert "--remote-debugging-port=9222" in arguments
	assert f"--user-data-dir={tmp_path / 'profile'}" in arguments
	assert mock_popen.call_count == 1


@patch("boss_agent_cli.auth.persistent_chrome.subprocess.Popen")
@patch("boss_agent_cli.auth.persistent_chrome.PersistentChrome._is_ready", return_value=True)
def test_ensure_running_reuses_ready_chrome(mock_ready, mock_popen, tmp_path: Path):
	"""已运行的专用浏览器必须被复用，不能重复开窗口或清空会话。"""
	from boss_agent_cli.auth.persistent_chrome import PersistentChrome

	manager = PersistentChrome(profile_dir=tmp_path / "profile", chrome_path=Path("C:/Chrome/chrome.exe"))

	assert manager.ensure_running() == "http://127.0.0.1:9222"
	mock_popen.assert_not_called()


@patch("boss_agent_cli.auth.persistent_chrome.httpx.put")
@patch("boss_agent_cli.auth.persistent_chrome.PersistentChrome._is_ready", return_value=True)
def test_ensure_running_can_open_login_page_without_patchright(mock_ready, mock_put, tmp_path: Path):
	"""登录页必须先由本机 CDP 打开，不能依赖可能崩溃的 Playwright 会话创建标签。"""
	from boss_agent_cli.auth.persistent_chrome import PersistentChrome

	manager = PersistentChrome(profile_dir=tmp_path / "profile", chrome_path=Path("C:/Chrome/chrome.exe"))

	assert manager.ensure_running(open_login_page=True) == "http://127.0.0.1:9222"
	mock_put.assert_called_once()
	assert "https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fuser%2F" in mock_put.call_args.args[0]


@patch("boss_agent_cli.auth.persistent_chrome.time.sleep", return_value=None)
@patch("boss_agent_cli.auth.persistent_chrome.subprocess.Popen")
@patch("boss_agent_cli.auth.persistent_chrome.PersistentChrome._is_ready", return_value=False)
def test_ensure_running_exposes_no_local_paths_when_browser_never_becomes_ready(mock_ready, mock_popen, mock_sleep, tmp_path: Path):
	"""启动失败只给用户恢复建议，不泄露 profile 或命令行路径。"""
	from boss_agent_cli.auth.persistent_chrome import PersistentChrome, PersistentChromeUnavailable

	manager = PersistentChrome(profile_dir=tmp_path / "profile", chrome_path=Path("C:/Chrome/chrome.exe"), readiness_attempts=1)

	with pytest.raises(PersistentChromeUnavailable, match="专用 Chrome 未能启动") as error:
		manager.ensure_running()

	assert str(tmp_path) not in str(error.value)
