import sys
import time
from typing import Any, cast
from urllib.parse import urlparse

from patchright.sync_api import sync_playwright

LOGIN_PAGE_URL = "https://www.zhipin.com/web/user/"
HOME_URL = "https://www.zhipin.com/"
_DEFAULT_CDP_URL = "http://localhost:9222"

# 超时常量（秒/毫秒）
_CDP_PROBE_TIMEOUT = 3           # CDP 探测 HTTP 超时（秒）
_NAV_TIMEOUT_MS = 15000          # 页面导航超时（毫秒）
_NETWORKIDLE_GRACE_MS = 3000     # 首页进入 networkidle 的额外宽限（毫秒）
_POST_LOGIN_WAIT = 3             # 登录成功后等待 cookie 传播（秒）
_STOKEN_GENERATION_WAIT = 2      # stoken 生成等待（秒）

_PLATFORM_BROWSER_CONFIG: dict[str, dict[str, str]] = {
	"zhipin": {
		"login_page_url": LOGIN_PAGE_URL,
		"home_url": HOME_URL,
		"cookie_domain": "zhipin",
		"success_cookie": "wt2",
	},
	"zhilian": {
		"login_page_url": "https://rd6.zhaopin.com/app/im",
		"home_url": "https://rd6.zhaopin.com/app/im",
		"cookie_domain": "zhaopin",
		"success_cookie": "at",
	},
}
_ZHILIAN_HOST = "zhaopin.com"
_ZHIPIN_HOST = "zhipin.com"


def _get_platform_config(platform: str) -> dict[str, str]:
	config = _PLATFORM_BROWSER_CONFIG.get(platform)
	if config is None:
		raise ValueError(f"unsupported platform: {platform}")
	return config


def _extract_zhilian_client_id(page: Any) -> str:
	try:
		return cast("str", page.evaluate("""
			() => {
				const keys = ["x-zp-client-id", "x_zp_client_id", "clientId"];
				for (const key of keys) {
					const value = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
					if (value) return value;
				}
				return '';
			}
		"""))
	except Exception:
		return ""


def _is_zhilian_url(url: str) -> bool:
	host = urlparse(url).hostname
	if host is None:
		return False
	host = host.rstrip(".").lower()
	return host == _ZHILIAN_HOST or host.endswith(f".{_ZHILIAN_HOST}")


def _is_zhipin_url(url: str) -> bool:
	"""判断页面是否属于 BOSS 官方域名，拒绝仅在查询参数中伪造域名的地址。"""
	host = urlparse(url).hostname
	if host is None:
		return False
	host = host.rstrip(".").lower()
	return host == _ZHIPIN_HOST or host.endswith(f".{_ZHIPIN_HOST}")


def _find_zhipin_login_page(pages: list[Any]) -> Any | None:
	"""优先复用已由 CDP 打开的官方登录页，避免再创建 ``about:blank`` 标签。"""
	for page in pages:
		url = getattr(page, "url", "")
		if isinstance(url, str) and _is_zhipin_url(url) and "/web/user" in url:
			return page
	for page in pages:
		if _is_zhipin_url(getattr(page, "url", "")):
			return page
	return None


def _is_zhipin_recruiter_page(url: str) -> bool:
	"""判断是否已进入只对招聘方登录态开放的 BOSS 工作页。"""
	if not _is_zhipin_url(url):
		return False
	path = urlparse(url).path.rstrip("/")
	return path.startswith("/web/chat") or path.startswith("/web/recommend")


def _find_zhilian_recruiter_page(pages: list[Any]) -> Any | None:
	for page in pages:
		url = getattr(page, "url", "")
		if _is_zhilian_url(url) and any(path in url for path in ("/app/im", "/app/recommend")):
			return page
	for page in pages:
		if _is_zhilian_url(getattr(page, "url", "")):
			return page
	return None


def _zhilian_client_id_from(cookies: dict[str, str], page: Any) -> str:
	return cookies.get("x-zp-client-id") or _extract_zhilian_client_id(page)


def _warm_home_for_runtime(page: Any, home_url: str, *, stage: str) -> None:
	"""预热首页运行时；networkidle 只尽力等待，不作为必须条件。"""
	try:
		page.goto(home_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	except Exception as e:
		print(f"[boss] {stage}：首页导航未在预期时间完成（{e}），继续尝试提取凭证", file=sys.stderr)
	try:
		page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	except Exception as e:
		print(f"[boss] {stage}：首页未进入 networkidle（{e}），继续提取凭证", file=sys.stderr)


def probe_cdp(cdp_url: str | None = None) -> str | None:
	"""探测 CDP 是否可用，返回 WebSocket URL 或 None。"""
	import httpx
	base = cdp_url or _DEFAULT_CDP_URL
	try:
		resp = httpx.get(f"{base}/json/version", timeout=_CDP_PROBE_TIMEOUT)
		return cast("str | None", resp.json().get("webSocketDebuggerUrl"))
	except (httpx.HTTPError, ValueError, KeyError):
		return None


def login_via_cdp(
	*,
	cdp_url: str | None = None,
	timeout: int = 120,
	platform: str = "zhipin",
	keep_page_open: bool = False,
	require_login_confirmation: bool = False,
) -> dict[str, Any]:
	"""
	通过 CDP 连接用户 Chrome 扫码登录。
	返回 token dict，失败抛异常。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用，请先运行 boss-chrome 启动带调试端口的 Chrome")

	print("[boss] 正在 CDP Chrome 中打开登录页...", file=sys.stderr)
	# ``start()`` 与 CDP 连接都放在同一个 try/finally 生命周期中。持久 Chrome
	# 可能在用户扫码期间重启，连接异常时也必须回收 Playwright 驱动，避免每次
	# 重试都残留一个 Node 子进程。
	pw = sync_playwright().start()
	page: Any | None = None
	created_page = False
	login_confirmed = False
	try:
		browser = pw.chromium.connect_over_cdp(ws_url)
		ctx = browser.contexts[0] if browser.contexts else browser.new_context()
		if platform == "zhilian":
			page = _find_zhilian_recruiter_page(ctx.pages)
		elif platform == "zhipin":
			page = _find_zhipin_login_page(ctx.pages)
		else:
			page = None
		created_page = page is None
		if page is None:
			page = ctx.new_page()

		# 持久 profile 中可能保留旧的 wt2/at。Web 控制台只有在平台已确认
		# 过期后才调用本函数的确认模式，此时必须观察 Cookie 发生变化，不能
		# 因旧 Cookie 仍存在就立即把登录任务标成成功。默认模式保留“复用已有
		# 官方页面会话”的能力，供 CLI/CDP 兼容路径使用。
		login_confirmation_seen = False
		def _on_login_response(response: Any) -> None:
			nonlocal login_confirmation_seen
			url = getattr(response, "url", "")
			if not isinstance(url, str):
				return
			if platform == "zhipin" and (
				"/wapi/zppassport/qrcode/loginConfirm" in url
				or "/wapi/zppassport/login/phoneV2" in url
			):
				login_confirmation_seen = True

		if require_login_confirmation:
			try:
				page.on("response", _on_login_response)
			except Exception:
				# 某些 CDP 兼容层不暴露 Playwright response 事件；此时仍可
				# 依靠页面跳转离开登录页作为成功信号。
				pass

		if created_page or platform != "zhilian":
			try:
				page.goto(
					login_page_url,
					wait_until="commit", timeout=_NAV_TIMEOUT_MS,
				)
			except Exception:
				pass

		print(f"[boss] 请在 Chrome 中扫码登录，等待中...（超时 {timeout}s）", file=sys.stderr)

		for i in range(timeout):
			time.sleep(1)
			cookies = ctx.cookies()
			success = [c for c in cookies if c["name"] == success_cookie and cookie_domain in c.get("domain", "")]
			page_left_login = False
			page_url = getattr(page, "url", "")
			if require_login_confirmation and isinstance(page_url, str):
				page_left_login = page_url.rstrip("/") != login_page_url.rstrip("/")
			# 部分 Chrome CDP 会话无法稳定读取刚写入的 Cookie，但招聘沟通页
			# 仅在招聘方认证后才能进入。页面已到该位置时，以它作为登录完成的
			# 等价证据，避免用户已登录却被 120 秒超时错误覆盖。
			recruiter_page_reached = (
				require_login_confirmation
				and platform == "zhipin"
				and isinstance(page_url, str)
				and _is_zhipin_recruiter_page(page_url)
			)
			if (success and (not require_login_confirmation or login_confirmation_seen or page_left_login)) or recruiter_page_reached:
				print("[boss] 检测到登录成功！", file=sys.stderr)
				login_confirmed = True
				break
			if i > 0 and i % 15 == 0:
				print(f"[boss] 等待中... {i}s", file=sys.stderr)
		else:
			raise TimeoutError(f"CDP 扫码登录超时（{timeout}s）")

		if created_page or platform != "zhilian":
			try:
				page.goto(home_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
			except Exception:
				pass
		all_cookies = {c["name"]: c["value"] for c in ctx.cookies() if cookie_domain in c.get("domain", "")}
		ua = page.evaluate("navigator.userAgent")
		# BOSS 招聘读取接口要求浏览器运行时生成的 stoken。CDP 已连接到用户
		# 完成登录的同一上下文，必须在这里读取并随 Cookie 一并持久化，否则
		# 会出现“登录成功但招聘接口仍不可用”的部分会话状态。
		stoken = _extract_stoken(page) if platform == "zhipin" else ""
		x_zp_client_id = _zhilian_client_id_from(all_cookies, page) if platform == "zhilian" else ""

		result: dict[str, Any] = {"cookies": all_cookies, "stoken": stoken, "user_agent": ua}
		if x_zp_client_id:
			result["x_zp_client_id"] = x_zp_client_id
		return result
	finally:
		try:
			# 登录成功且要求保留页面时才留下官方 tab；超时、连接中断或用户
			# 关闭流程都清理新建页面，避免反复尝试后出现一堆空白登录页。
			if page is not None and created_page and (not keep_page_open or not login_confirmed):
				page.close()
		finally:
			pw.stop()


def login_via_browser(*, timeout: int = 120, platform: str = "zhipin") -> dict[str, Any]:
	"""
	使用 patchright（Playwright 兼容 fork）打开登录页。
	通过 BOSS 登录确认响应检测成功。不能仅凭 ``wt2`` Cookie 判断，因为登录页
	在扫码前也可能写入预登录 Cookie，误判会把失效会话保存为新登录态。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	with sync_playwright() as p:
		# 交互式登录优先使用用户安装的正式 Chrome。BOSS 的二维码页面对测试版
		# Chromium 可能存在资源或兼容性差异。禁用 GPU 可避开部分 Windows 设备上
		# Chromium 内容区透明、二维码无法绘制的硬件合成故障；Chrome 通道不可用时
		# 才退回 bundled Chromium，保证未安装 Chrome 的环境仍保留原有可见登录能力。
		try:
			browser = p.chromium.launch(headless=False, channel="chrome", args=["--disable-gpu"])
		except Exception:
			browser = p.chromium.launch(headless=False, args=["--disable-gpu"])
		context = browser.new_context(
			viewport={"width": 1280, "height": 800},
			locale="zh-CN",
			timezone_id="Asia/Shanghai",
		)
		page = context.new_page()

		page.goto(login_page_url, wait_until="domcontentloaded")
		print("已打开 BOSS 直聘登录页。", file=sys.stderr)
		print(f"请扫码或手机号登录（超时 {timeout} 秒）...", file=sys.stderr)

		# 只接受 BOSS 的登录确认响应。预登录 Cookie 不代表账号已完成认证，不能
		# 用它持久化会话，否则会让后续招聘读取接口误用无效登录态。
		login_detected = False

		def _on_response(response: Any) -> None:
			nonlocal login_detected
			url = response.url
			if (url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/loginConfirm")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/dispatcher")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/login/phoneV2")):
				login_detected = True

		page.on("response", _on_response)

		deadline = time.time() + timeout
		while time.time() < deadline and not login_detected:
			# 登录页是用户主动操作的官方窗口。窗口被关闭时继续等待没有意义，且会让
			# Web 控制台一直停在 running 状态并禁用其余操作；页面对象访问失败也等同于
			# 窗口已不可用，统一按取消登录处理，避免泄露底层浏览器异常。
			try:
				login_window_closed = page.is_closed()
			except Exception:
				login_window_closed = True
			# Playwright 的 is_closed() 契约是 bool；严格比较可以避免测试替身或
			# 未来的代理对象因 truthy 被误判成窗口已关闭，导致登录流程提前中止。
			if login_window_closed is True:
				browser.close()
				raise RuntimeError("官方登录窗口已关闭")
			time.sleep(1)

		if not login_detected:
			browser.close()
			raise TimeoutError(f"扫码登录超时（{timeout}秒）")

		print("检测到登录成功，正在提取凭证...", file=sys.stderr)
		time.sleep(_POST_LOGIN_WAIT)

		# 跳转主站提取完整 cookies 和 stoken
		_warm_home_for_runtime(page, home_url, stage="登录后回到首页")

		cookies_list = context.cookies()
		cookies = {c["name"]: c["value"] for c in cookies_list if cookie_domain in c.get("domain", "")}
		user_agent = page.evaluate("navigator.userAgent")
		stoken = _extract_stoken(page) if platform == "zhipin" else ""
		x_zp_client_id = _extract_zhilian_client_id(page) if platform == "zhilian" else ""

		browser.close()

	result: dict[str, Any] = {
		"cookies": cookies,
		"stoken": stoken,
		"user_agent": user_agent,
	}
	if x_zp_client_id:
		result["x_zp_client_id"] = x_zp_client_id
	return result


def refresh_stoken_via_cdp(cdp_url: str | None = None) -> str:
	"""通过 CDP Chrome 刷新 stoken（指纹一致，不会被拒）。"""
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用")

	pw = sync_playwright().start()
	browser = pw.chromium.connect_over_cdp(ws_url)
	ctx = browser.contexts[0] if browser.contexts else browser.new_context()
	page = ctx.new_page()

	try:
		page.goto(HOME_URL, wait_until="commit", timeout=15000)
	except Exception:
		pass
	time.sleep(_STOKEN_GENERATION_WAIT)

	stoken = _extract_stoken(page)
	page.close()
	pw.stop()

	if not stoken:
		raise RuntimeError("CDP 刷新 stoken 失败：页面未生成 stoken")
	return stoken


def refresh_stoken(cookies: dict[str, Any], user_agent: str) -> str:
	"""通过 headless patchright 刷新 stoken（兜底方案）。"""
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		context = browser.new_context(user_agent=user_agent)
		context.add_cookies([
			{"name": name, "value": value, "domain": ".zhipin.com", "path": "/"}
			for name, value in cookies.items()
		])
		page = context.new_page()
		_warm_home_for_runtime(page, HOME_URL, stage="刷新 stoken")
		stoken = _extract_stoken(page)
		browser.close()

	return stoken


def _extract_stoken(page: Any) -> str:
	try:
		stoken = page.evaluate("""
			() => {
				const match = document.cookie.match(/__zp_stoken__=([^;]+)/);
				return match ? match[1] : '';
			}
		""")
		if not stoken:
			stoken = page.evaluate("() => window.__zp_stoken__ || ''")
		return cast("str", stoken)
	except Exception:
		return ""
