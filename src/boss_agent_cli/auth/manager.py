from pathlib import Path
from typing import Any

from boss_agent_cli.auth.browser import login_via_browser, login_via_cdp, probe_cdp, refresh_stoken, refresh_stoken_via_cdp
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.persistent_chrome import PersistentChrome
from boss_agent_cli.auth.qr_login import qr_login_httpx
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.output import Logger


class AuthRequired(Exception):
	pass


class TokenRefreshFailed(Exception):
	pass


class AuthManager:
	def __init__(
		self,
		data_dir: Path,
		*,
		logger: Logger | None = None,
		platform: str = "zhipin",
		persistent_chrome: PersistentChrome | None = None,
	) -> None:
		self._platform = platform or "zhipin"
		auth_dir = data_dir / "auth" if self._platform == "zhipin" else data_dir / "auth" / self._platform
		self._store = TokenStore(auth_dir)
		self._token: dict[str, Any] | None = None
		self._logger = logger or Logger()
		# 专用 profile 与数据目录同属本项目，保证单账号会话不会混入用户日常浏览器。
		self._persistent_chrome = persistent_chrome or PersistentChrome(
			profile_dir=data_dir / "browser" / self._platform,
		)

	def _login_action(self) -> str:
		return "boss --platform zhilian login" if self._platform == "zhilian" else "boss login"

	def get_token(self) -> dict[str, Any]:
		if self._token is not None:
			return self._token
		self._token = self._store.load()
		if self._token is None:
			raise AuthRequired(f"未登录，请先执行 {self._login_action()}")
		return self._token

	def has_saved_login(self) -> bool:
		"""判断磁盘登录态是否至少包含平台主认证 Cookie。

		控制台启动时不能只用 ``session.enc`` 是否存在作为“已登录”依据：
		文件可能来自一次只保存了部分 Cookie 的旧登录。这里仅检查经过解密后
		得到的主 Cookie，不发起网络请求，也不把 Cookie 值写入日志或页面；
		招聘读取所需的运行时令牌由 CDP 页面在实际请求时提供。
		"""
		token = self._store.load()
		return token is not None and self._has_primary_cookie(token)

	def ensure_browser_cdp(self) -> str:
		"""启动或复用本账号专用 Chrome，并返回本机 CDP 地址。

		Web 控制台和招聘平台客户端必须连接同一个持久化 profile。若每次
		请求改用临时浏览器，页面里的登录 Cookie 与 HTTP 客户端会分裂，
		表现为刚登录又立刻要求登录；生命周期细节统一由 ``PersistentChrome``
		负责，认证管理器只暴露稳定的边界方法。
		"""
		return self._persistent_chrome.ensure_running()

	def reload_from_store(self) -> bool:
		"""重新读取磁盘登录态，并告知调用方会话材料是否已经变化。

		本地控制台可能在另一个终端完成官方登录。此时 TokenStore 已经写入新
		凭据，但常驻进程仍缓存旧值；只读平台请求失败后可调用本方法，以一次
		受控重试接入新登录态。返回 ``False`` 时调用方必须保留原请求结果，不能
		把它当成自动登录或无限重试的依据。
		"""
		persisted = self._store.load()
		if persisted is None or persisted == self._token:
			return False
		self._token = persisted
		return True

	def login(
		self,
		*,
		timeout: int = 120,
		cookie_source: str | None = None,
		cdp_url: str | None = None,
		force_cdp: bool = False,
	) -> dict[str, Any]:
		"""三级降级登录：Cookie 提取 → CDP 自动探测 → patchright 扫码。

		Args:
			force_cdp: 为 True 时跳过 Cookie 提取，CDP 不可用直接报错。
		"""
		method = "未知"
		token: dict[str, Any] | None = None

		if force_cdp:
			# --cdp 强制模式：跳过 Cookie，CDP 不可用直接抛异常
			self._logger.info("强制 CDP 模式，跳过 Cookie 提取")
			token = login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform)
			method = "CDP 扫码"
			self._store.save(token)
			self._token = token
			return {**token, "_method": method}

		# 第一步：尝试从本地浏览器提取 Cookie
		self._logger.info("尝试从本地浏览器提取 Cookie...")
		token = extract_cookies(cookie_source, platform=self._platform)
		if token and self._has_primary_cookie(token):
			if self._verify_cookie(token):
				self._store.save(token)
				self._token = token
				self._logger.info("Cookie 提取成功，已保存")
				return {**token, "_method": "Cookie 提取"}
			self._logger.info("提取的 Cookie 已失效，降级到 CDP")
		else:
			self._logger.info("未能从浏览器提取 Cookie，降级到 CDP")

		# 第二步：CDP 自动探测
		if probe_cdp(cdp_url):
			self._logger.info("检测到 CDP 可用，尝试 CDP 登录...")
			try:
				token = login_via_cdp(cdp_url=cdp_url, timeout=timeout, platform=self._platform)
				method = "CDP 扫码"
				self._store.save(token)
				self._token = token
				return {**token, "_method": method}
			except Exception as e:
				self._logger.info(f"CDP 登录失败（{e}），降级到 patchright")
		else:
			self._logger.info("CDP 不可用，尝试 QR 纯 httpx 登录")

		# 第三步：QR 纯 httpx 登录（仅 zhipin）
		if self._platform == "zhipin":
			try:
				self._logger.info("尝试 QR 纯 httpx 登录...")
				token = qr_login_httpx(timeout=timeout)
				method = "QR httpx 登录"
				self._store.save(token)
				self._token = token
				return {**token, "_method": method}
			except Exception as e:
				self._logger.info(f"QR httpx 登录失败（{e}），降级到 patchright")

		# 第四步：patchright 扫码（兜底）
		token = login_via_browser(timeout=timeout, platform=self._platform)
		method = "扫码登录"
		self._store.save(token)
		self._token = token
		return {**token, "_method": method}

	def login_in_browser(self, *, timeout: int = 120, cdp_url: str | None = None) -> dict[str, Any]:
		"""在官方平台页面完成一次显式浏览器登录。

		本地 Web 控制台优先复用用户已经启动的 CDP Chrome。这样二维码由用户的
		正式浏览器内核渲染，且登录成功后的会话与后续招聘读取使用同一上下文；
		只有 CDP 不可用时才启动 patchright 作为可见窗口兜底。这里刻意不走 Cookie
		提取或纯 HTTP 二维码路径，因为控制台必须始终呈现用户可操作的官方页面。
		认证成功后仍通过同一 TokenStore 持久化，避免 Web 与 CLI 形成两套登录态。
		"""
		resolved_cdp_url = cdp_url.strip() if isinstance(cdp_url, str) and cdp_url.strip() else None
		if resolved_cdp_url is None:
			resolved_cdp_url = self._persistent_chrome.ensure_running(open_login_page=True)
		token = login_via_cdp(
			cdp_url=resolved_cdp_url,
			timeout=timeout,
			platform=self._platform,
			keep_page_open=True,
			require_login_confirmation=True,
		)
		method = "专用 Chrome 登录"
		self._store.save(token)
		self._token = token
		return {**token, "_method": method}

	def open_login_page(self, *, cdp_url: str | None = None) -> str:
		"""确保专用 Chrome 打开官方 BOSS 登录页，但不等待用户完成登录。

		控制台的“打开 BOSS 登录页”按钮只需要完成可见页面导航。即使 Web 服务
		启动时已经绑定了 CDP 地址，也必须优先使用本项目专用浏览器，避免把
		其它项目或历史标签当成当前招聘 RPA 目标。
		"""
		# 显式 CLI CDP 地址代表用户指定的招聘浏览器，仍需通过 Chrome DevTools
		# 新建官方登录页；未指定时由专用 profile 负责启动和打开页面。
		resolved_cdp_url = cdp_url.strip() if isinstance(cdp_url, str) and cdp_url.strip() else None
		if resolved_cdp_url is None:
			return self._persistent_chrome.ensure_running(open_login_page=True)
		login_url = "https://www.zhipin.com/web/user/"
		try:
			from urllib.parse import quote

			import httpx

			response = httpx.put(f"{resolved_cdp_url.rstrip('/')}/json/new?{quote(login_url, safe='')}", timeout=3)
			response.raise_for_status()
		except Exception:
			# Web 服务启动期间记录的 CDP 地址可能对应已经退出的临时 Chrome。
			# 此时不能要求用户重启服务或手动寻找端口，直接恢复到项目专用 profile。
			return self._persistent_chrome.ensure_running(open_login_page=True)
		return resolved_cdp_url

	def _has_primary_cookie(self, token: dict[str, Any]) -> bool:
		cookies = token.get("cookies", {})
		if not isinstance(cookies, dict):
			return False
		if self._platform == "zhilian":
			return bool(cookies.get("at") or cookies.get("zp_token"))
		primary_cookie = "wt2"
		return bool(cookies.get(primary_cookie))

	def _verify_cookie(self, token: dict[str, Any]) -> bool:
		"""验证 Cookie 是否有效。"""
		try:
			import httpx
			if self._platform == "zhilian":
				from boss_agent_cli.api.zhilian_client import USER_INFO_URL
				headers = {
					"User-Agent": token.get("user_agent") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
					"Referer": "https://i.zhaopin.com/",
				}
				if client_id := token.get("x_zp_client_id") or token.get("client_id"):
					headers["x-zp-client-id"] = str(client_id)
				resp = httpx.get(
					USER_INFO_URL,
					cookies=token.get("cookies", {}),
					headers=headers,
					timeout=10,
				)
				data = resp.json()
				return bool(data.get("code") == 200)

			from boss_agent_cli.api import endpoints
			resp = httpx.get(
				endpoints.USER_INFO_URL,
				cookies=token.get("cookies", {}),
				headers={
					"User-Agent": token.get("user_agent") or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
					"Referer": "https://www.zhipin.com/",
				},
				timeout=10,
			)
			data = resp.json()
			return bool(data.get("code") == 0)
		except (httpx.HTTPError, ValueError, KeyError):
			return False

	def force_refresh(self, cdp_url: str | None = None) -> None:
		with self._store.refresh_lock():
			current = self._store.load()
			if current is None:
				raise TokenRefreshFailed("无法刷新 Token，请重新登录")
			self._logger.info("Token 过期，正在静默刷新...")
			try:
				if self._platform == "zhilian":
					refreshed = extract_cookies(None, platform=self._platform)
					if not refreshed or not self._verify_cookie(refreshed):
						refreshed = login_via_cdp(cdp_url=cdp_url, timeout=30, platform=self._platform)
					if not refreshed or not self._verify_cookie(refreshed):
						raise TokenRefreshFailed("智联登录态刷新失败，请重新登录")
					self._store.save(refreshed)
					self._token = refreshed
					return

				# CDP 优先：指纹一致，不会被 BOSS 直聘拒绝
				if probe_cdp(cdp_url):
					self._logger.info("检测到 CDP，使用 CDP 刷新 stoken")
					new_stoken = refresh_stoken_via_cdp(cdp_url)
				else:
					self._logger.info("CDP 不可用，降级到 headless 刷新 stoken")
					new_stoken = refresh_stoken(
						current["cookies"],
						current.get("user_agent", ""),
					)
				refreshed = {**current, "stoken": new_stoken}
				self._store.save(refreshed)
				self._token = refreshed
			except Exception as e:
				raise TokenRefreshFailed(f"Token 刷新失败: {e}") from e

	def check_status(self) -> dict[str, Any] | None:
		return self._store.load()

	def logout(self) -> None:
		"""清除本地登录态"""
		self._store.clear()
		self._token = None
