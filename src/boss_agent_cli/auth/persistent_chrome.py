"""管理单账号专用 Chrome 的本机生命周期。

该模块只处理浏览器进程与 CDP 就绪状态，不读取 Cookie、不判断 BOSS 登录结果，也不
关闭浏览器。将这部分从认证流程拆出，能够让控制台重启后重连同一 profile，同时避免
误把用户日常浏览器或临时自动化浏览器当作登录态来源。
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from urllib.parse import quote

import httpx

_DEFAULT_CDP_URL = "http://127.0.0.1:9222"
_READY_TIMEOUT_SECONDS = 10
_ZHIPIN_LOGIN_PAGE_URL = "https://www.zhipin.com/web/user/"


class PersistentChromeUnavailable(RuntimeError):
	"""专用 Chrome 未就绪时向上层提供的安全异常。"""


class PersistentChrome:
	"""启动或复用专用 profile 的 Chrome，始终仅允许本机 CDP 连接。"""

	def __init__(
		self,
		*,
		profile_dir: Path,
		cdp_url: str = _DEFAULT_CDP_URL,
		chrome_path: Path | None = None,
		readiness_attempts: int = _READY_TIMEOUT_SECONDS,
	) -> None:
		self._profile_dir = profile_dir
		self._cdp_url = cdp_url.rstrip("/")
		self._chrome_path = chrome_path
		self._readiness_attempts = readiness_attempts

	def ensure_running(self, *, open_login_page: bool = False) -> str:
		"""返回就绪的本机 CDP 地址，并按需打开官方登录页。

		已运行的专用 Chrome 不应被重启，否则会丢失用户正在完成的扫码状态；但
		登录入口必须显式确保官方页面存在，不能再由 Patchright 创建空白标签后
		才导航。这样 CDP 会话建立失败时，用户仍能看到可操作的官方登录页。
		"""
		if self._is_ready():
			if open_login_page:
				self._open_login_page()
			return self._cdp_url
		self._profile_dir.mkdir(parents=True, exist_ok=True)
		chrome_path = self._chrome_path or self._find_chrome_path()
		if chrome_path is None:
			raise PersistentChromeUnavailable("未找到专用 Chrome，请安装或配置 Google Chrome 后重试")
		try:
			subprocess.Popen(
				self._command(chrome_path),
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
			)
		except OSError as error:
			raise PersistentChromeUnavailable("专用 Chrome 未能启动，请检查浏览器后重试") from error
		for _ in range(self._readiness_attempts):
			if self._is_ready():
				return self._cdp_url
			time.sleep(1)
		raise PersistentChromeUnavailable("专用 Chrome 未能启动，请检查浏览器后重试")

	def _open_login_page(self) -> None:
		"""通过 Chrome DevTools 的本机新页面端点直接打开 BOSS 官方登录页。

		该端点不依赖 Playwright/Patchright 对全部页面会话的附加，适合作为登录页
		展示边界。失败时明确中止登录，避免 UI 仅留下 ``about:blank`` 空标签。
		"""
		# ``/json/new`` 把问号后的内容直接当 URL 解析；保留 ``:/`` 会被 Chrome
		# 当作普通查询键并创建 about:blank，因此必须完整百分号编码。
		login_url = quote(_ZHIPIN_LOGIN_PAGE_URL, safe="")
		try:
			response = httpx.put(f"{self._cdp_url}/json/new?{login_url}", timeout=3)
			response.raise_for_status()
		except httpx.HTTPError as error:
			raise PersistentChromeUnavailable("专用 Chrome 无法打开 BOSS 登录页，请检查浏览器后重试") from error

	def _command(self, chrome_path: Path) -> list[str]:
		"""构造隔离 profile 与仅本机调试端口的启动参数。"""
		port = self._cdp_url.rsplit(":", maxsplit=1)[-1]
		return [
			str(chrome_path),
			"--remote-debugging-address=127.0.0.1",
			f"--remote-debugging-port={port}",
			"--remote-allow-origins=http://localhost",
			f"--user-data-dir={self._profile_dir}",
			"--no-first-run",
			"--new-window",
			"https://www.zhipin.com/web/user/",
		]

	def _is_ready(self) -> bool:
		"""只探测本机 CDP 的公开版本端点，不读取浏览器 profile 或会话数据。"""
		try:
			response = httpx.get(f"{self._cdp_url}/json/version", timeout=1)
			return bool(response.json().get("webSocketDebuggerUrl"))
		except (httpx.HTTPError, ValueError, KeyError):
			return False

	@staticmethod
	def _find_chrome_path() -> Path | None:
		"""按 Windows 常见安装位置查找正式 Chrome，找不到时由上层给出恢复提示。"""
		candidates = (
			Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
			Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
			Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
		)
		return next((candidate for candidate in candidates if candidate.is_file()), None)
