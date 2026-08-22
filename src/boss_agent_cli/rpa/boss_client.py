"""RPA BOSS recruiter client — CDP 直连，纯 JS 操作。

通过 Chrome DevTools Protocol WebSocket 执行 JavaScript，
模拟真人操作：``window.location.href`` 跳转、``element.click()`` 点击、
``document.querySelectorAll`` 读内容。不使用 Playwright/patchright，
不修改 navigator，BOSS 看不出和真人浏览的区别。
"""

from __future__ import annotations

import json
import re
from threading import RLock
import time
from typing import Any
from urllib.request import Request, urlopen

from boss_agent_cli.recruiting.conversation_profile import ConversationProfile
from boss_agent_cli.recruiting.online_resume_validation import is_meaningful_online_resume_text
from boss_agent_cli.recruiting.unicode_safety import sanitize_unicode_text


def _to_timestamp(time_str: str) -> int:
	"""把 BOSS 时间格式 'MM-DD HH:MM' 或 'HH:MM' 转成毫秒时间戳。"""
	import datetime

	now = datetime.datetime.now()
	try:
		parts = time_str.strip().split()
		if len(parts) >= 2 and ":" in parts[-1]:
			# "08-05 12:30" format
			date_part = parts[0] if "-" in parts[0] else f"{now.month:02d}-{now.day:02d}"
			time_part = parts[-1]
			month, day = date_part.split("-")
			hour, minute = time_part.split(":")
			dt = datetime.datetime(now.year, int(month), int(day), int(hour), int(minute))
			return int(dt.timestamp() * 1000)
		elif ":" in time_str:
			# "12:30" format
			hour, minute = time_str.split(":")
			dt = datetime.datetime(now.year, now.month, now.day, int(hour), int(minute))
			return int(dt.timestamp() * 1000)
	except Exception:
		pass

# BOSS 页面 URL
CHAT_PAGE = "https://www.zhipin.com/web/chat/index"
# BOSS 招聘端当前的“职位管理”导航会跳转到该地址。旧的
# ``/web/user/chat/jobList`` 已返回 404，RPA 因而只能得到空列表。
JOB_PAGE = "https://www.zhipin.com/web/chat/job/list"

# CDP 只用于执行短小的 DOM 读写命令；如果页面已经关闭或 WebSocket 失效，
# 长时间占住同步串行线程只会让工作台一直显示“正在同步”。将网络读写限制在
# 10 秒内，配合会话失效回收，让上层可以尽快显示可恢复的错误并重新连接。
_CDP_SOCKET_TIMEOUT_SECONDS = 10

# 招呼语配置已经迁移到 info_v2 单页应用。聊天壳里的旧入口
# ``/web/chat/set/greeting`` 仍会嵌入 ``/web/frame/info/set/greeting``，但该
# iframe 会被新版前端错误地解析为重复路由，最终得到空白页。直接进入该应用
# 的规范 v2 路径，既保留登录态，也避免依赖已失效的聊天壳嵌入逻辑。
GREETING_SETTINGS_PAGE = "https://www.zhipin.com/web/frame/info_v2/set/greeting"

# 招呼语设置页在当前 BOSS 版本中由同源 iframe 承载。所有设置步骤都使用这一段
# 选择正确的 document，并保留顶层文档回退，以兼容平台未来将内容移出 iframe 的版本。
_GREETING_SETTINGS_DOCUMENT_JS = """
	const greetingFrame = document.querySelector('iframe[src*="/set/greeting"]');
	const greetingDocument = greetingFrame?.contentDocument || document;
	// info_v2 的编辑弹窗使用 position: fixed，offsetParent 会是 null，即使它已
	// 经真实渲染在屏幕上。使用渲染矩形和计算样式判断，兼容旧版普通流布局。
	const isGreetingElementVisible = element => {
		if (!element || element.getClientRects().length === 0) return false;
		const style = greetingDocument.defaultView?.getComputedStyle(element);
		return style?.display !== 'none' && style?.visibility !== 'hidden';
	};
"""

# 旧版设置页用 ``.dialog-container``，新版 info_v2 改为 ``.gjs-overlay``。
# 两者均代表岗位话术编辑浮层，统一声明可以让每一步都在同一个局部范围内查找，
# 避免误填设置页底层的通用招呼语控件。
_GREETING_DIALOG_SELECTOR = ".dialog-container, .gjs-overlay"

# BOSS 会话卡片里存在多种通用 badge（岗位标签、状态标签、数字提示等），不能
# 只要看见 badge 就认定候选人有新消息。未读判断必须绑定 unread/未读语义，避免
# 把整批等待中的会话误放入每轮优先队列，导致自动化反复打开同一批旧会话。
_UNREAD_COUNT_JS = """
	// 该脚本会在同一 BOSS 页面反复经 CDP 注入。必须使用可重复声明的 var，
	// 否则第二次执行会因 const 同名绑定残留而直接抛出 SyntaxError。
	var __bossAgentReadUnreadCount = (card) => {
		const nodes = Array.from(card.querySelectorAll(
			'.unread-badge, .unread-count, [class*="unread"], [aria-label*="未读"], [aria-label*="unread" i]'
		));
		for (const node of nodes) {
			if (node.offsetParent === null) continue;
			const text = (node.textContent || '').trim();
			const numeric = text.match(/^\\d+\\+?$/);
			if (numeric) return Math.min(Number(numeric[0].replace('+', '')), 999);
			// 有些版本只显示红点而没有数字；节点本身已有 unread 语义时按 1 处理。
			if (/unread/i.test(String(node.className || '')) || node.getAttribute('aria-label')?.includes('未读')) return 1;
		}
		return 0;
	};
"""


class BossRPAConnectionError(RuntimeError):
	"""表示本机 CDP 未指向可操作的 BOSS 招聘页面。

	该错误与“BOSS 登录失效”严格区分：前者说明 RPA 附着到了错误的浏览器
	上下文，系统尚未有机会读取 BOSS 登录态；将其暴露为独立错误，工作台才能
	引导用户回到正确的专用浏览器，而不是误报账号退出。
	"""


class BossRPALoginRequiredError(RuntimeError):
	"""表示项目专用 RPA 浏览器尚未完成 BOSS 招聘端登录。

	用户的日常浏览器与 RPA 专用 profile 是两份独立会话。BOSS 把招聘页
	重定向到 ``/web/user/`` 时，不能据此断言用户账号退出，只能要求在当前
	RPA 浏览器的官方页面完成登录，随后再恢复招聘自动化。
	"""


class BossRPAClient:
	"""CDP 直连 RPA 客户端 — 可读写，但只用 JS 模拟真人操作。"""

	def __init__(self, *, cdp_url: str = "http://127.0.0.1:9222") -> None:
		self._cdp_url = cdp_url.rstrip("/")
		self._ws_url: str | None = None
		self._msg_id: int = 0
		self._ws_sock: Any = None  # persistent socket
		# 一条 CDP WebSocket 是有序字节流而非请求复用连接。必须完整独占
		# "分配 id -> 发送 -> 读取对应响应"，否则页面状态轮询与自动化线程会
		# 分别读走对方的帧，破坏下一帧的 UTF-8 / WebSocket 边界。
		self._cdp_session_lock = RLock()
		# 推荐卡片是动态列表；保留“当前页已读取”标记，避免读取后再次导航
		# 导致卡片轮换，进而用旧 geek_id 点不到刚刚硬筛通过的人。
		self._recommendation_page_loaded = False

	def _log(self, msg: str) -> None:
		"""Internal debug logger — writes to stderr so stdout stays clean for JSON envelopes."""
		import sys

		print(f"[BossRPAClient] {msg}", file=sys.stderr, flush=True)

	def is_success(self, response: dict[str, Any]) -> bool:
		"""判断页面 RPA 响应是否成功，保持与招聘平台适配器一致。

		附件状态机既可以接收完整的 ``BossRecruiterPlatform`` 适配器，也可以在
		单元测试、诊断脚本和后台任务中直接接收 RPA 客户端。将最小的响应契约
		放在客户端基类，避免调用方为了同一个 ``code == 0`` 判断重复包装；非
		字典响应明确视为失败，防止页面异常返回被误认为已完成。
		"""
		return isinstance(response, dict) and response.get("code") == 0

	# ================================================================
	# CDP 底层
	# ================================================================

	def ensure_session(self) -> None:
		"""绑定当前 CDP 中的 BOSS 页面，拒绝跨项目页面回退。

		同一台电脑可能同时运行多个本地项目，它们都可能占用或暴露 CDP 端口。
		过去的“取第一张页面”回退会把其他项目页面当成 BOSS，再由后续 DOM
		查询返回空数组，最终在工作台被误显示为沟通列表为空。这里仅接受
		``zhipin.com`` 域下的普通页面，找不到时立即中止而不导航、不点击。
		"""
		if self._ws_sock:
			return
		targets = self._cdp_get("/json")
		if not targets:
			raise BossRPAConnectionError("未连接 BOSS 招聘页面")

		# 只能选择 BOSS 直聘的标准标签页；browser_ui 和其他本地页面即使可
		# 调试，也不能携带招聘端登录态，更不允许被导航操作覆盖。
		boss_pages: list[dict[str, Any]] = []
		for t in targets:
			if not isinstance(t, dict) or t.get("type") != "page":
				continue
			url = t.get("url")
			if isinstance(url, str) and self._is_zhipin_url(url):
				boss_pages.append(t)
		# 登录页和已登录招聘页可能同时存在。必须优先保持聊天/招聘工作区，
		# 否则“打开登录页”新建的标签会抢走已登录会话，让列表刷新误报未登录。
		boss_page = next((page for page in boss_pages if self._is_recruiter_workspace_url(str(page.get("url") or ""))), None)
		if boss_page is None:
			boss_page = next((page for page in boss_pages if self._is_login_page_url(str(page.get("url") or ""))), None)
		if boss_page is None and boss_pages:
			boss_page = boss_pages[0]
		if not boss_page:
			raise BossRPAConnectionError("未连接 BOSS 招聘页面")

		self._ws_url = boss_page.get("webSocketDebuggerUrl")
		if not self._ws_url:
			raise BossRPAConnectionError("BOSS 招聘页面不支持调试")
		self._log(f"connected to: {boss_page.get('url', '?')[:80]}")
		self._ws_connect()

	@staticmethod
	def _is_zhipin_url(url: str) -> bool:
		"""判断 URL 是否属于 BOSS 直聘的可信站点域名。

		使用解析后的 hostname 而不是简单子串匹配，避免把
		``zhipin.com.example.test`` 等非 BOSS 页面误选为 RPA 目标。
		解析失败的调试目标按不可信处理，保持“宁可不操作”的边界。
		"""
		try:
			from urllib.parse import urlparse

			host = (urlparse(url).hostname or "").lower()
		except ValueError:
			return False
		return host == "zhipin.com" or host.endswith(".zhipin.com")

	@staticmethod
	def _is_recruiter_workspace_url(url: str) -> bool:
		"""识别已登录后可承载招聘动作的 BOSS 工作区页面。"""
		try:
			from urllib.parse import urlparse

			return urlparse(url).path.rstrip("/").startswith("/web/chat")
		except ValueError:
			return False

	@staticmethod
	def _is_login_page_url(url: str) -> bool:
		"""识别官方登录路由；仅无招聘工作区页面时才允许绑定它。"""
		try:
			from urllib.parse import urlparse

			return urlparse(url).path.rstrip("/") == "/web/user"
		except ValueError:
			return False

	def _ensure_recruiter_page_ready(self) -> None:
		"""确认目标页面没有被 BOSS 重定向到登录页。

		BOSS 对未登录的招聘端请求通常返回 HTTP 成功再进行前端跳转，若只看
		DOM 选择器会得到空数组。此处在任何列表读取前检查当前 URL，将登录页
		转换为明确的领域错误，避免空数据掩盖真实的会话边界问题。
		"""
		current_url = self._eval("window.location.href")
		if not isinstance(current_url, str):
			return
		try:
			from urllib.parse import urlparse

			path = urlparse(current_url).path.rstrip("/")
		except ValueError:
			return
		if path == "/web/user":
			raise BossRPALoginRequiredError("项目 RPA 浏览器尚未登录 BOSS")

	def probe_live_login(self) -> bool:
		"""只读验证当前 RPA 标签页是否仍处于 BOSS 招聘端登录态。

		控制台状态不能凭本地 Cookie 推断账号在线。本方法只绑定当前已打开的
		BOSS 标签页并读取 URL，不导航、不点击、不读取候选人内容；因此可以
		安全用于页面轮询。连接不到 BOSS、读取失败或被重定向至登录页都视为
		未登录，调用方据此锁定自动化操作并引导用户走官方登录流程。
		"""
		self.ensure_session()
		current_url = self._eval("window.location.href")
		if not isinstance(current_url, str):
			return False
		try:
			from urllib.parse import urlparse

			urlparse(current_url)
		except ValueError:
			return False
		return self._is_zhipin_url(current_url) and self._is_recruiter_workspace_url(current_url)

	def _cdp_get(self, path: str) -> Any:
		req = Request(f"{self._cdp_url}{path}")
		with urlopen(req, timeout=5) as resp:
			return json.loads(resp.read())

	def _ws_connect(self) -> None:
		import socket

		ws = self._ws_url
		if ws.startswith("ws://"):
			ws = ws[5:]
		host_port, _, path = ws.partition("/")
		host, _, port = host_port.partition(":")
		port = int(port) if port else 80

		sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		sock.settimeout(_CDP_SOCKET_TIMEOUT_SECONDS)
		sock.connect((host, port))
		upgrade = (
			f"GET /{path} HTTP/1.1\r\n"
			f"Host: {host}:{port}\r\n"
			f"Upgrade: websocket\r\n"
			f"Connection: Upgrade\r\n"
			f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
			f"Sec-WebSocket-Version: 13\r\n\r\n"
		)
		sock.sendall(upgrade.encode())
		resp = b""
		while b"\r\n\r\n" not in resp:
			resp += sock.recv(1)
		self._ws_sock = sock

	def _invalidate_cdp_session(self) -> None:
		"""释放失效的 CDP 会话，确保下一条命令重新探测目标页面。

		CDP WebSocket 是长连接，但 BOSS 标签页可能被关闭、重载或被 Chrome
		替换目标。仅保留旧 socket 会让 ``ensure_session`` 误以为连接仍然可用，
		后续同步就会在同一条串行线程里持续等待。先清空客户端状态，再尽力关闭
		底层 socket，避免关闭过程中的异常再次覆盖真正的传输错误。
		"""
		sock = self._ws_sock
		self._ws_sock = None
		self._ws_url = None
		if sock is None:
			return
		try:
			close = getattr(sock, "close", None)
			if callable(close):
				close()
		except OSError:
			# 连接本来就已经断开时，释放本地引用仍然是成功的恢复动作。
			pass

	def reset_session(self) -> None:
		"""主动释放当前页面绑定，供登录页打开或标签页切换后重新定位 BOSS 页面。

		登录入口会新建官方 BOSS 标签。若继续复用旧 WebSocket，后续沟通列表
		刷新可能仍读到旧标签、已关闭标签或登录页，从而出现“已打开但无法刷新”
		的假失败。该方法不导航、不读取任何候选人内容，只使下一次操作重新选页。
		"""
		with self._cdp_session_lock:
			self._invalidate_cdp_session()

	def _send_ws_payload(self, payload: bytes) -> None:
		"""发送一个已编码的 WebSocket 文本帧。

		CDP 是基于 WebSocket 的双向协议。发送命令和接收响应必须拆开，后者才
		能跳过浏览器主动推送的事件帧，并等待与当前命令 ``id`` 对应的结果。
		"""
		sock = self._ws_sock
		if sock is None:
			raise RuntimeError("CDP WebSocket 尚未连接")
		sock.sendall(payload)

	def _receive_ws_payload(self) -> bytes:
		"""读取一帧 WebSocket 负载；空帧由上层响应循环决定是否忽略。"""
		sock = self._ws_sock
		if sock is None:
			raise RuntimeError("CDP WebSocket 尚未连接")
		header = sock.recv(2)
		if len(header) < 2:
			return b""
		plen = header[1] & 0x7F
		if plen == 126:
			plen = int.from_bytes(sock.recv(2), "big")
		elif plen == 127:
			plen = int.from_bytes(sock.recv(8), "big")
		buf = b""
		while len(buf) < plen:
			chunk = sock.recv(min(plen - len(buf), 65536))
			if not chunk:
				break
			buf += chunk
		return buf

	def _receive_cdp_response(self, expected_id: int) -> dict[str, Any]:
		"""等待指定 CDP 命令响应，跳过空帧和浏览器事件帧。

		Chrome 会在命令响应之间推送 ``Runtime.*``、``Page.*`` 等事件；旧实现
		把第一帧直接 ``json.loads``，遇到空帧就会冒出
		``Expecting value: line 1 column 1``，导致自动化轮询整轮失败。这里
		只接受 ``id`` 与当前命令一致的响应，其余帧都视为异步事件并继续等待。
		"""
		deadline = time.monotonic() + _CDP_SOCKET_TIMEOUT_SECONDS
		while time.monotonic() < deadline:
			payload = self._receive_ws_payload()
			if not payload:
				continue
			try:
				result = json.loads(payload.decode("utf-8"))
			except (json.JSONDecodeError, UnicodeDecodeError):
				continue
			if not isinstance(result, dict) or result.get("id") != expected_id:
				continue
			if "error" in result:
				raise RuntimeError(f"CDP: {result['error']}")
			return result
		raise RuntimeError("CDP 响应超时")

	def _ws_send(self, payload: str, *, expected_id: int) -> dict[str, Any]:
		data = payload.encode("utf-8")
		frame = bytearray([0x81])  # FIN text
		n = len(data)
		if n < 126:
			frame.append(0x80 | n)
		elif n < 65536:
			frame.append(0x80 | 126)
			frame.extend(n.to_bytes(2, "big"))
		else:
			frame.append(0x80 | 127)
			frame.extend(n.to_bytes(8, "big"))
		mask = bytes([0x12, 0x34, 0x56, 0x78])
		frame.extend(mask)
		for i, b in enumerate(data):
			frame.append(b ^ mask[i % 4])
		self._send_ws_payload(bytes(frame))
		return self._receive_cdp_response(expected_id).get("result", {})

	def _cdp_send(
		self,
		method: str,
		params: dict | None = None,
	) -> Any:
		"""串行执行 CDP 命令，并在连接失效时回收会话状态。

		同一条 socket 必须完整独占“发送到接收”过程，否则事件帧会与命令响应
		串线。异常处理也必须位于这个临界区内：只有当前命令确认传输层失效后，
		才能清空共享 socket，避免另一个调用在线程间隙继续复用坏连接。
		"""
		with self._cdp_session_lock:
			if self._ws_sock is None:
				self.ensure_session()
			self._msg_id += 1
			message_id = self._msg_id
			try:
				return self._ws_send(
					json.dumps(
						{
							"id": message_id,
							"method": method,
							"params": params or {},
						}
					),
					expected_id=message_id,
				)
			except (ConnectionError, OSError, TimeoutError):
				self._invalidate_cdp_session()
				raise
			except RuntimeError as exc:
				# “CDP 响应超时”意味着浏览器没有在期限内返回当前命令，
				# 继续复用该 socket 的结果不可预测；页面脚本自身的 RuntimeError
				# 则保留原状，避免把普通 JS 业务错误误判为连接断开。
				if str(exc) == "CDP 响应超时":
					self._invalidate_cdp_session()
				raise
	def _eval(
		self,
		js: str,
		*,
		await_promise: bool = False,
	) -> Any:
		r = self._cdp_send(
			"Runtime.evaluate",
			{
				"expression": js,
				"returnByValue": True,
				"awaitPromise": await_promise,
			},
		)
		val = r.get("result", {}).get("value")
		# Check for exception
		if r.get("result", {}).get("subtype") == "error":
			desc = r["result"].get("description", "")
			raise RuntimeError(f"JS 错误: {desc}")
		return val

	# ================================================================
	# 页面操作（纯 JS，不触发自动化检测）
	# ================================================================

	def navigate_to(self, url: str) -> None:
		"""跳转到指定 URL — 只在不在目标页面时才跳转。"""
		try:
			current = str(self._eval("window.location.href"))
			# 如果已经在目标页面（含参数），不跳
			if current.rstrip("/") == url.rstrip("/"):
				return
			# 如果路径匹配（query 参数不同），也不跳 — BOSS 单页应用内切换
			from urllib.parse import urlparse

			if urlparse(current).path == urlparse(url).path:
				return
		except BossRPAConnectionError:
			# 会话目标校验失败时绝不能再发起第二次 CDP 调用。否则日志看似在
			# 导航，且未来底层改动后可能误把错误上下文中的页面真正改写。
			raise
		except Exception:
			pass
		self._log(f"navigate → {url[:80]}")
		self._eval(f"window.location.href = {json.dumps(url)}")
		time.sleep(0.5)

	def wait_for_url(self, fragment: str, timeout: float = 10) -> bool:
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			try:
				cur = str(self._eval("window.location.href"))
				if fragment in cur:
					return True
			except Exception:
				pass
			time.sleep(0.5)
		return False

	def wait_loaded(self, timeout: float = 5) -> None:
		"""等页面 DOM 加载完成。"""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			try:
				ready = self._eval("document.readyState")
				if ready == "complete":
					return
			except Exception:
				pass
			time.sleep(0.3)

	def click_text(self, text: str) -> bool:
		"""点击页面上包含指定文字的第一个可点元素。"""
		js = f"""
		(() => {{
			const els = document.querySelectorAll('a, button, span, div, li');
			for (const el of els) {{
				if ((el.textContent || '').includes({json.dumps(text)}) &&
					el.offsetParent !== null) {{
					el.scrollIntoView({{block: 'center'}});
					el.click();
					return true;
				}}
			}}
			return false;
		}})()
		"""
		return bool(self._eval(js))

	def scroll_down(self, px: int = 300) -> None:
		self._eval(f"window.scrollBy(0, {px})")

	def human_delay(self, lo: float = 0.3, hi: float = 1.5) -> None:
		import random

		time.sleep(random.uniform(lo, hi))

	# ================================================================
	# 页面内容读取
	# ================================================================

	def _query_texts(self, selector: str) -> list[str]:
		js = f"""
		Array.from(document.querySelectorAll({json.dumps(selector)}))
			.map(el => (el.textContent || '').trim())
			.filter(t => t.length > 0)
		"""
		r = self._eval(js)
		return r if isinstance(r, list) else []

	def _query_attrs(self, selector: str, attr: str) -> list[str]:
		js = f"""
		Array.from(document.querySelectorAll({json.dumps(selector)}))
			.map(el => el.getAttribute({json.dumps(attr)}) || '')
			.filter(v => v)
		"""
		r = self._eval(js)
		return r if isinstance(r, list) else []

	def _page_text(self) -> str:
		r = self._eval("document.body ? document.body.textContent || '' : ''")
		return str(r) if r else ""

	def _visible_text(self, selector: str) -> list[str]:
		"""只返回可见元素的文字。"""
		js = f"""
		Array.from(document.querySelectorAll({json.dumps(selector)}))
			.filter(el => el.offsetParent !== null)
			.map(el => (el.textContent || '').trim())
			.filter(t => t.length > 0)
		"""
		r = self._eval(js)
		return r if isinstance(r, list) else []

	# ================================================================
	# 沟通列表
	# ================================================================

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		# 通过可覆写的会话准备方法进入沟通页。CDP 默认仍会导航；Bridge 模式
		# 则会复用用户当前可见的沟通页，避免每次刷新列表都触发整页重载。
		self._ensure_chat_page()
		self._ensure_recruiter_page_ready()
		# 全量同步会滚过 BOSS 的虚拟列表。记录并恢复原位置，避免后台同步
		# 把用户正在查看的列表拖到末尾，造成“页面自动刷新”的视觉误判。
		original_scroll_top = self._move_friend_list_viewport(page)

		# 从 DOM 提取数据：每个卡片里的 .geek-item 有 data-id="{friendId}-{idx}"
		# currentData$ 是共享的当前选中项，不能用
		try:
			raw = self._eval(_UNREAD_COUNT_JS + """
		(() => {
			// 只把时间和最后一条预览在浏览器内计算成版本摘要，正文不离开页面。
			// 这样可识别“红点已被手动清除但会话后来更新”，又不把候选人隐私
			// 写入自动化队列或活动日志。
			const digest = (value) => {
				let hash = 2166136261;
				for (let index = 0; index < value.length; index += 1) {
					hash ^= value.charCodeAt(index);
					hash = Math.imul(hash, 16777619);
				}
				return `v1-${(hash >>> 0).toString(16)}`;
			};
			// 完整快照会依次滚过全部虚拟列表窗口，顺手为快速轮询建立基线。
			// 基线只驻留当前 BOSS 页面，关闭或刷新页面后由下一次完整快照重建。
			const knownVersions = window.__bossAgentConversationVersions || new Map();
			window.__bossAgentConversationVersions = knownVersions;
			const items = [];
			const cards = document.querySelectorAll('.geek-item-wrap');
			for (let idx = 0; idx < cards.length; idx++) {
				const card = cards[idx];
				const getText = (sel) => {
					const el = card.querySelector(sel);
					return el ? (el.textContent || '').trim() : '';
				};
				const time = getText('.time-shadow, .time, [class*="time"]');
				// BOSS 当前会话卡片把最后一条消息渲染在 .push-text；保留旧选择器
				// 兼容历史版本。预览会参与版本摘要，红点被人工清除后仍可发现回复。
				const preview = getText('.push-text, .last-msg, .last-message, [class*="push-text"], [class*="last-msg"], [class*="last-message"], [class*="preview"]');
				// 从内层 .geek-item 的 data-id 提取 friendId 和 uniqueId
				const inner = card.querySelector('.geek-item, [data-id]');
				const dataId = (inner && inner.getAttribute('data-id')) || '';
				// data-id 格式: "744717579-0" → friendId=744717579
				const friendId = dataId ? dataId.split('-')[0] : String(idx + 1);
				const conversationVersion = time || preview ? digest(`${time}|${preview}`) : '';
				if (conversationVersion) knownVersions.set(friendId, conversationVersion);
				items.push({
					_idx: idx,
					_fid: friendId,
					name: getText('.geek-name, .name, [class*="name"]'),
					job: getText('.source-job, .job-name, [class*="job-name"], [class*="position"]'),
					company: getText('.company-name, [class*="company"], [class*="brand"]'),
					city: getText('.city, [class*="city"], [class*="location"]'),
					time,
					conversationVersion,
					unread: __bossAgentReadUnreadCount(card),
					geekId: '',
					jobId: '',
					securityId: '',
				});
			}
			return items;
		})()
			""")
		finally:
			if isinstance(original_scroll_top, int) and original_scroll_top >= 0:
				try:
					self._eval(f"""
					(() => {{
						const list = document.querySelector('.user-list');
						if (!list) return false;
						list.scrollTop = {original_scroll_top};
						list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
						return true;
					}})()
					""")
				except Exception as exc:
					# 恢复位置是体验补偿，不能覆盖列表读取本身的异常；记录类型即可，
					# 避免把页面正文或平台返回内容写入服务日志。
					self._log(f"[RPA] friend_list: restore viewport failed: {type(exc).__name__}")

		self._log(f"[RPA] friend_list: raw items={len(raw) if isinstance(raw, list) else 0}")

		items: list[dict[str, Any]] = []
		if isinstance(raw, list):
			for item in raw:
				if not isinstance(item, dict):
					continue
				idx = item.get("_idx", 0)
				fid_str = str(item.get("_fid", ""))
				try:
					real_fid = int(fid_str) if fid_str.isdigit() else None
				except (ValueError, TypeError):
					real_fid = None
				friend_id = real_fid if real_fid and real_fid > 0 else (idx + 1)
				items.append(
					{
						"friendId": friend_id,
						"friend_id": friend_id,
						"name": str(item.get("name") or f"候选人{idx + 1}"),
						"candidateName": str(item.get("name") or f"候选人{idx + 1}"),
						"jobName": str(item.get("job") or ""),
						"companyName": str(item.get("company") or ""),
						"cityName": str(item.get("city") or ""),
						"updateTime": _to_timestamp(str(item.get("time", "")))
						if item.get("time")
						else int(time.time() * 1000),
						"conversationVersion": str(item.get("conversationVersion") or ""),
						# 空徽标表示页面没有新候选人消息，不能为了兜底伪造成 1；
						# 否则异步轮询会逐个打开所有等待会话，既慢又浪费 RPA 预算。
						"unreadMsgCount": int(item.get("unread") or 0) if str(item.get("unread", "")).isdigit() else 0,
						"encryptGeekId": str(item.get("geekId") or ""),
						"geekId": str(item.get("geekId") or ""),
						"encryptJobId": str(item.get("jobId") or ""),
						"jobId": str(item.get("jobId") or ""),
						"securityId": str(item.get("securityId") or ""),
					}
				)
		else:
			# 兜底：JS 执行失败，回退到旧版文本选择器
			names = self._query_texts(".geek-name")
			jobs = self._query_texts(".source-job")
			times = self._query_texts(".time-shadow, .time")
			for i in range(min(len(names), 20)):
				items.append(
					{
						"friendId": i + 1,
						"friend_id": i + 1,
						"name": names[i],
						"candidateName": names[i],
						"jobName": jobs[i] if i < len(jobs) else "",
						"companyName": "",
						"updateTime": _to_timestamp(times[i])
						if i < len(times) and isinstance(times, list)
						else int(time.time() * 1000),
						"unreadMsgCount": 1,
						"conversationVersion": "",
					}
				)
		return {"code": 0, "zpData": {"friendList": items}}

	def fast_conversation_snapshot(self, *, include_all: bool = False) -> dict[str, Any]:
		"""分块扫描虚拟沟通列表，避免 500 人列表在一个 CDP Promise 中超时。

		旧实现把“滚到底部、等待每个窗口渲染、收集卡片”放进一条 JavaScript
		Promise。候选人数增多后，这条 CDP 请求会超过 socket 上限，整个轮询失败。
		现在每个滚动窗口单独执行并返回，Python 端按页面顺序合并结果；每一步都
		是短操作，且无论成功或失败都会恢复用户原本的列表位置。
		"""
		self._ensure_chat_page()
		self._ensure_recruiter_page_ready()
		bounds = self._conversation_snapshot_bounds()
		if bounds is None:
			return {"code": -1, "zpData": {"friendList": []}}
		original_top, max_top, step = bounds
		collected: dict[int, dict[str, Any]] = {}
		positions = self._snapshot_scroll_positions(max_top=max_top, step=step)
		started_at = time.monotonic()
		self._log(f"[RPA] conversation snapshot start windows={len(positions)} max_top={max_top} step={step}")
		try:
			for window_index, scroll_top in enumerate(positions, start=1):
				chunk = self._conversation_snapshot_chunk(scroll_top=scroll_top, include_all=include_all)
				self._log(f"[RPA] conversation snapshot window={window_index}/{len(positions)} items={len(chunk)} elapsed={time.monotonic() - started_at:.1f}s")
				for item in chunk:
					friend_id = item.get("friendId")
					if isinstance(friend_id, int) and friend_id > 0:
						collected[friend_id] = item
		finally:
			self._restore_conversation_snapshot_scroll(original_top)
		return {"code": 0, "zpData": {"friendList": list(collected.values())}}

	def _conversation_snapshot_bounds(self) -> tuple[int, int, int] | None:
		"""读取虚拟列表滚动边界，不在这个短操作内等待任何页面重绘。"""
		result = self._eval("""
		(() => {
			// bossAgentSnapshotMeta
			const list = document.querySelector('.user-list');
			if (!list) return null;
			return {
				originalTop: Math.max(0, Number(list.scrollTop) || 0),
				maxTop: Math.max(0, list.scrollHeight - list.clientHeight),
				// 每个窗口实际保留约 40 张卡片，跨四个视口仍有缓冲重叠；相比
				// 三个视口可减少约四分之一的重复 CDP 读取，避免全量同步过慢。
				step: Math.max(1, list.clientHeight * 4),
			};
		})()
		""")
		if not isinstance(result, dict):
			return None
		values = (result.get("originalTop"), result.get("maxTop"), result.get("step"))
		if not all(isinstance(value, (int, float)) for value in values):
			return None
		original_top, max_top, step = (max(0, int(value)) for value in values)
		return original_top, max_top, max(1, step)

	@staticmethod
	def _snapshot_scroll_positions(*, max_top: int, step: int) -> tuple[int, ...]:
		"""生成单调滚动位置并确保包含末尾窗口，避免边界候选人遗漏。"""
		positions = list(range(0, max_top + 1, max(1, step)))
		if not positions or positions[-1] != max_top:
			positions.append(max_top)
		return tuple(dict.fromkeys(positions))

	def _conversation_snapshot_chunk(self, *, scroll_top: int, include_all: bool) -> list[dict[str, Any]]:
		"""读取一个已定位滚动窗口的轻量卡片投影，单次只等待一次渲染。"""
		include_all_literal = "true" if include_all else "false"
		result = self._eval(
			(
				_UNREAD_COUNT_JS + f"""
			(async () => {{
				// bossAgentSnapshotChunk
				const includeAll = {include_all_literal};
				const list = document.querySelector('.user-list');
				if (!list) return {{items: []}};
				list.scrollTop = {scroll_top};
				list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
				await new Promise(resolve => setTimeout(resolve, 80));
				const digest = (value) => {{
					let hash = 2166136261;
					for (let index = 0; index < value.length; index += 1) {{
						hash ^= value.charCodeAt(index);
						hash = Math.imul(hash, 16777619);
					}}
					return `v1-${{(hash >>> 0).toString(16)}}`;
				}};
				const knownVersions = window.__bossAgentConversationVersions || new Map();
				window.__bossAgentConversationVersions = knownVersions;
				const items = Array.from(document.querySelectorAll('.geek-item-wrap'))
					.map((card, index) => {{
						const inner = card.querySelector('.geek-item, [data-id]');
						const friendId = (inner?.getAttribute('data-id') || '').split('-')[0];
						if (!/^\\d+$/.test(friendId)) return null;
						const text = selector => (card.querySelector(selector)?.textContent || '').trim();
						const time = text('.time-shadow, .time, [class*="time"]');
						const preview = text('.push-text, .last-msg, .last-message, [class*="push-text"], [class*="last-msg"], [class*="last-message"], [class*="preview"]');
						const conversationVersion = time || preview ? digest(`${{time}}|${{preview}}`) : '';
						const previousVersion = knownVersions.get(friendId);
						const versionChanged = Boolean(previousVersion && conversationVersion && previousVersion !== conversationVersion);
						if (conversationVersion) knownVersions.set(friendId, conversationVersion);
						return {{
							friendId: Number(friendId),
							name: text('.geek-name, .name, [class*="name"]') || `候选人${{index + 1}}`,
							unreadMsgCount: __bossAgentReadUnreadCount(card),
							conversationVersion,
							versionChanged,
						}};
					}})
					.filter(item => item && (includeAll || item.unreadMsgCount > 0 || item.versionChanged));
				return {{items}};
			}})()
			"""
			),
			await_promise=True,
		)
		if not isinstance(result, dict) or not isinstance(result.get("items"), list):
			return []
		return [item for item in result["items"] if isinstance(item, dict)]

	def _restore_conversation_snapshot_scroll(self, original_top: int) -> None:
		"""恢复只读扫描前的列表位置，恢复失败不覆盖原始读取结果。"""
		try:
			self._eval(f"""
			(() => {{
				// bossAgentSnapshotRestore
				const list = document.querySelector('.user-list');
				if (!list) return false;
				list.scrollTop = {original_top};
				list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
				return true;
			}})()
			""")
		except Exception as exc:
			self._log(f"[RPA] fast_conversation_snapshot: restore viewport failed: {type(exc).__name__}")

	def _move_friend_list_viewport(self, page: int) -> int | None:
		"""将虚拟化沟通列表移动到指定的可视窗口。

		BOSS 沟通列表不是传统的网络分页：页面通过 ``.user-list`` 的滚动位置动态
		替换 ``.geek-item-wrap`` 节点。过去 ``friend_list(page=...)`` 忽略页码且
		只取前 20 个节点，导致大量待回复会话遮蔽后续已经回复的候选人。这里将
		页号映射为一个视口高度的滚动步长，并在每次读取前回到确定的位置。相邻
		窗口会保留少量重叠，调用方再按 ``friend_id`` 去重，避免快速滚动时漏掉
		边界卡片。
		"""
		requested_page = max(page, 1)
		moved = self._eval(f"""
		(() => {{
			const list = document.querySelector('.user-list');
			if (!list) return {{changed: false, top: 0, originalTop: null}};
			const originalTop = Math.max(0, Number(list.scrollTop) || 0);
			const maxScrollTop = Math.max(0, list.scrollHeight - list.clientHeight);
			// BOSS 会一次保留约四个视口的虚拟卡片；每页跨过完整渲染窗口，
			// 才不会让 page=2 仍返回和 page=1 完全相同的节点集合。
			const targetScrollTop = Math.min(maxScrollTop, ({requested_page} - 1) * list.clientHeight * 4);
			const changed = Math.abs(list.scrollTop - targetScrollTop) > 1;
			list.scrollTop = targetScrollTop;
			list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
			return {{changed, top: list.scrollTop, originalTop}};
		}})()
		""")
		# 虚拟列表只在滚动事件后的下一轮渲染才会替换卡片；没有移动时不等待，
		# 保持读取当前首屏的速度。测试替身也可用任意返回值跳过这个等待。
		if isinstance(moved, dict) and moved.get("changed") is True:
			self.human_delay(0.2, 0.4)
		if not isinstance(moved, dict):
			return None
		original_top = moved.get("originalTop")
		return int(original_top) if isinstance(original_top, (int, float)) and original_top >= 0 else None

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		"""获取指定候选人的定位信息。

		RPA 模式下的关键设计：
		- encryptUid/encryptGeekId 编码为 "friendid:{friendId}" 格式
		- view_geek 会解析此格式，用 friendId 定位正确候选人
		- 真正的 geek_id/job_id/security_id 在 RPA 模式下不需要
		  （通过 DOM 点击导航，不走 API 参数）
		"""
		self._ensure_chat_page()
		self.human_delay(0.3, 0.6)

		result_list: list[dict[str, Any]] = []
		for fid in friend_ids:
			target_idx = self._find_card_by_friend_id(fid)
			if target_idx is None:
				self._log(f"[RPA] friend_detail: friend_id {fid} not found, skipping")
				continue
			# 从 DOM 读取卡片上显示的文本信息
			info = self._eval(f"""
			(() => {{
				const cards = document.querySelectorAll('.geek-item-wrap');
				if (cards.length <= {target_idx}) return null;
				const card = cards[{target_idx}];
				const getText = (sel) => {{
					const el = card.querySelector(sel);
					return el ? (el.textContent || '').trim() : '';
				}};
				return {{
					name: getText('.geek-name, .name, [class*=\"name\"]') || '候选人{fid}',
					jobName: getText('.source-job, .job-name, [class*=\"job-name\"]'),
					companyName: getText('.company-name, [class*=\"company\"]'),
				}};
			}})()
			""")
			name = info.get("name", f"候选人{fid}") if info and isinstance(info, dict) else f"候选人{fid}"
			# RPA 模式：用 friendid: 前缀编码 friendId，view_geek 会解析
			result_list.append(
				{
					"uid": fid,
					"friendId": fid,
					"encryptUid": f"friendid:{fid}",
					"encryptGeekId": f"friendid:{fid}",
					"encryptJobId": f"friendid:{fid}",
					"securityId": f"friendid:{fid}",
					"name": name,
				}
			)
			self._log(f"[RPA] friend_detail: #{target_idx} → {name} (friend_id={fid})")

		return {"code": 0, "zpData": {"friendList": result_list}}

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		return self.friend_list(page=page)

	def sync_job_greeting(self, job_name: str, content: str) -> dict[str, Any]:
		"""通过 BOSS 设置页保存岗位自定义招呼语并校验回显。

		同步只改变招聘者账号的招呼语配置，不向任何候选人发送消息；候选人发送动作
		仍由推荐流程在硬筛通过后单独执行，避免配置写入和候选人沟通混为一体。
		"""
		if not content.strip() or len(content.strip()) > 100:
			return {"code": -1, "message": "招呼语为空或超过 BOSS 允许长度"}
		self._recommendation_page_loaded = False
		self.navigate_to(GREETING_SETTINGS_PAGE)
		self.wait_loaded()
		self.human_delay(0.8, 1.2)
		# BOSS 的岗位级话术不使用通用页的“自定义”标签。切换到岗位页后，需要
		# 打开“添加打招呼语”弹窗，选择精确职位，再填写该职位的独立话术。
		if not self._click_greeting_job_tab():
			return {"code": -1, "message": "未找到 BOSS 的按职位设置招呼语入口"}
		# BOSS 对“目标职位且文本完全相同”的重复保存不会产生提交动作。推荐
		# 自动化每轮都会先同步话术，因此先读取平台回显并复用已生效的配置，既
		# 避免无意义写入，也防止弹窗停留导致后续候选人流程无法开始。
		if self._wait_for_existing_job_greeting(job_name, content.strip(), timeout=4.0):
			return {"code": 0, "zpData": {"verified": True, "job_name": job_name}}
		if not self._open_job_greeting_editor():
			return {"code": -1, "message": "无法打开 BOSS 的岗位招呼语编辑窗口"}
		if not self._select_job_for_greeting(job_name):
			return {"code": -1, "message": f"未找到 BOSS 中的目标职位：{job_name}"}
		if not self._wait_for_visible_textarea():
			return {"code": -1, "message": "BOSS 未展示自定义招呼语输入框"}
		# BOSS 点击保存时会立即销毁弹窗，CDP 可能来不及返回脚本结果。不能把
		# 这个瞬时返回值当作保存结果，必须读取职位列表中的最终话术进行确认。
		self._save_job_greeting(content.strip())
		# BOSS 会先处理保存请求、再异步重绘岗位列表；真实账号上该过程可超过
		# 编辑控件的 5 秒等待窗口。这里独立给回读留出时间，仍以完整文本回显为准。
		if not self._wait_for_job_greeting_persisted(job_name, content.strip(), timeout=12.0):
			return {"code": -1, "message": "BOSS 招呼语保存回显校验失败"}
		return {"code": 0, "zpData": {"verified": True, "job_name": job_name}}

	def _click_greeting_job_tab(self) -> bool:
		"""切换到 iframe 内的岗位级招呼语页签。

		“通用”与“按职位设置招呼语”拥有不同的 DOM 和保存语义。显式使用页签类名
		可以避免按文字匹配到外层说明容器，从而误把职位话术写成全局话术。
		"""
		clicked = self._eval(f"""
		(() => {{
			{_GREETING_SETTINGS_DOCUMENT_JS}
			const tab = Array.from(greetingDocument.querySelectorAll(
				'.greeting-tab .tab-item, .tab-header .tab-item'
			))
				.find(element => (element.textContent || '').trim() === '按职位设置招呼语' && isGreetingElementVisible(element));
			if (!tab) return false;
			tab.click();
			return true;
		}})()
		""")
		return bool(clicked)

	def _open_job_greeting_editor(self) -> bool:
		"""打开新增岗位话术弹窗；等待 v2 页签切换后的异步控件挂载。

		新版页面点击“按职位设置招呼语”后，``.greet-job-btn`` 并不会与页签同步
		出现在 DOM 中。先确认弹窗是否已打开，再仅点击一次已出现的触发器，随后
		按页面条件等待弹窗可见，避免固定延时在慢网络下误判为失败。
		"""
		deadline = time.monotonic() + 5.0
		click_sent = False
		while time.monotonic() < deadline:
			dialog_visible = self._eval(f"""
			(() => {{
				{_GREETING_SETTINGS_DOCUMENT_JS}
				return Array.from(greetingDocument.querySelectorAll('{_GREETING_DIALOG_SELECTOR}'))
					.some(element => isGreetingElementVisible(element));
			}})()
			""")
			if dialog_visible:
				return True
			if not click_sent:
				click_sent = bool(self._eval(f"""
				(() => {{
					{_GREETING_SETTINGS_DOCUMENT_JS}
					const trigger = greetingDocument.querySelector('.greeting-tab .greet-job-btn');
					if (!isGreetingElementVisible(trigger)) return false;
					trigger.click();
					return true;
				}})()
				"""))
			time.sleep(0.2)
		return False

	def _job_greeting_matches(self, job_name: str, content: str) -> bool:
		"""读取岗位配置列表，确认目标岗位的完整话术已经由 BOSS 持久化。

		此方法只读页面回显，不依赖弹窗是否打开。把“已有配置”判定独立出来，
		能让同步入口在重复执行时直接复用平台状态，而保存后的轮询仍可复用同一
		个精确比对，避免姓名或部分文本匹配到其他职位。
		"""
		matched = self._eval(f"""
		(() => {{
			{_GREETING_SETTINGS_DOCUMENT_JS}
			const target = {json.dumps(job_name, ensure_ascii=False)};
			const expectedContent = {json.dumps(content, ensure_ascii=False)};
			return Array.from(greetingDocument.querySelectorAll('.job-nav li')).some(item => {{
				const configuredNameNode = item.querySelector('.job-name .text-ellipsis') || item.querySelector('.job-name');
				const configuredName = (configuredNameNode?.textContent || '').trim();
				const configuredGreeting = (item.querySelector('.job-greeting')?.textContent || '').trim();
				return configuredName === target && configuredGreeting === expectedContent;
			}});
		}})()
		""")
		return bool(matched)

	def _wait_for_existing_job_greeting(self, job_name: str, content: str, *, timeout: float) -> bool:
		"""等待岗位页签的异步列表加载，并复用已生效的同内容配置。

		info_v2 在点击岗位页签后先挂载空容器，再异步填充 ``.job-nav``。一次
		读取无法区分“尚未渲染”和“确实没有该配置”，因此只在有限窗口内轮询
		精确回显；超时后才继续走新增或编辑流程。
		"""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			if self._job_greeting_matches(job_name, content):
				return True
			time.sleep(0.2)
		return False

	def _select_job_for_greeting(self, job_name: str) -> bool:
		"""在岗位弹窗中按职位名称选中唯一目标，防止同页面其他职位被错误修改。"""
		selected = self._eval(f"""
		(() => {{
			{_GREETING_SETTINGS_DOCUMENT_JS}
			const target = {json.dumps(job_name, ensure_ascii=False)};
			const dialog = Array.from(greetingDocument.querySelectorAll('{_GREETING_DIALOG_SELECTOR}'))
				.find(element => isGreetingElementVisible(element));
			if (!dialog) return false;
			const row = Array.from(dialog.querySelectorAll('.job-list .radio-item, .job-list label.b-radio'))
				.find(element => (element.querySelector('.job-name')?.textContent || '').trim() === target);
			if (!row) return false;
			row.click();
			return true;
		}})()
		""")
		return bool(selected)

	def _save_job_greeting(self, content: str) -> bool:
		"""填写已选岗位的输入框并点击该弹窗的保存按钮，不触碰任何通用话术控件。"""
		self._eval(f"""
		(() => {{
			{_GREETING_SETTINGS_DOCUMENT_JS}
			const target = {json.dumps(content, ensure_ascii=False)};
			const dialog = Array.from(greetingDocument.querySelectorAll('{_GREETING_DIALOG_SELECTOR}'))
				.find(element => element.offsetParent !== null);
			const input = dialog?.querySelector('textarea, [contenteditable="true"]');
			if (!input) return false;
			if (input.matches('textarea')) {{
				const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
				setter.call(input, target);
			}} else {{ input.textContent = target; }}
			input.dispatchEvent(new Event('input', {{bubbles: true}}));
			const save = dialog.querySelector('button.btn-sure-v2');
			if (!save) return false;
			save.click();
			return true;
		}})()
		""")

	def _wait_for_visible_textarea(self, timeout: float = 5.0) -> bool:
		"""等待“自定义”选项真正展开输入框，避免把内容写入错误的默认话术控件。"""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			visible = self._eval(
				"""
			(() => {
				%s
				return Array.from(greetingDocument.querySelectorAll('textarea, [contenteditable="true"]'))
					.some(element => isGreetingElementVisible(element));
			})()
			"""
				% _GREETING_SETTINGS_DOCUMENT_JS
			)
			if visible:
				return True
			time.sleep(0.2)
		return False

	def _wait_for_job_greeting_persisted(self, job_name: str, content: str, timeout: float = 5.0) -> bool:
		"""确认保存后目标职位及完整话术均出现在配置列表，作为平台持久化回显。"""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			dialog_open = self._eval(f"""
			(() => {{
				{_GREETING_SETTINGS_DOCUMENT_JS}
				return Array.from(greetingDocument.querySelectorAll('{_GREETING_DIALOG_SELECTOR}'))
					.some(element => isGreetingElementVisible(element));
			}})()
			""")
			if not dialog_open and self._job_greeting_matches(job_name, content):
				return True
			time.sleep(0.2)
		return False

	# ================================================================
	# 推荐牛人
	# ================================================================

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""打开推荐牛人页面并读取 iframe 内的候选人卡片。

		推荐页不是沟通页的顶层 DOM，而是 ``recommendFrame`` iframe。旧实现只
		查询顶层 ``.geek-name``，在真实页面始终得到空列表。这里先等待 iframe
		卡片出现，再从卡片及其 ``.card-inner`` 子节点提取最小定位字段；不猜测
		friend_id，推荐候选人必须在打招呼后回到沟通列表重新建立会话绑定。
		"""
		self.navigate_to(CHAT_PAGE)
		self.wait_loaded()
		self.human_delay(1.0, 2.0)
		self._click_nav("推荐牛人")
		# 推荐页与沟通页的职位筛选器互不共享。只有按本次调用传入的 BOSS
		# 职位标识筛选并验证回显，下面卡片才可以用于当前工作台岗位；匹配失败
		# 时返回空列表并带错误，调用方会停止打招呼而不会误触其它职位候选人。
		recommendation_selector = str(getattr(self, "_recommendation_job_name", "") or job_id or "").strip()
		if recommendation_selector and not self._select_recommendation_job(recommendation_selector):
			return {"code": -1, "message": "BOSS 推荐牛人页未切换到当前岗位"}
		deadline = time.monotonic() + 8.0
		items: list[dict[str, Any]] = []
		while time.monotonic() < deadline:
			raw = self._eval(f"""
			(() => {{
				const frame = document.querySelector('iframe[name="recommendFrame"]');
				const root = frame?.contentDocument;
				if (!root) return [];
				return Array.from(root.querySelectorAll('.candidate-card-wrap')).slice(0, 20).map(card => {{
					const inner = card.querySelector('.card-inner, [data-geek], [data-geekid]') || card;
					const attr = (...names) => {{
						for (const name of names) {{
							const value = inner.getAttribute(name) || card.getAttribute(name) || '';
							if (value) return value;
						}}
						return '';
					}};
					const text = selector => (card.querySelector(selector)?.textContent || '').trim();
					return {{
						geekId: attr('data-geek', 'data-geekid', 'data-geek-id'),
						jobId: attr('data-job', 'data-jobid', 'data-job-id') || {json.dumps(job_id or "", ensure_ascii=False)},
						securityId: attr('data-security', 'data-securityid', 'data-security-id'),
						name: text('.name, .geek-name'),
						title: text('.expect-wrap, .job-name, .source-job'),
						city: text('.expect-wrap, .city'),
						degree: text('.edu-wrap, .education'),
						tags: text('.tags-wrap, .tags'),
					}};
				}});
			}})()
			""")
			if isinstance(raw, list):
				items = [
					{
						"encryptGeekId": str(item.get("geekId") or ""),
						"geekId": str(item.get("geekId") or ""),
						"geek_id": str(item.get("geekId") or ""),
						"encryptJobId": str(item.get("jobId") or ""),
						"job_id": str(item.get("jobId") or ""),
						"jobId": str(item.get("jobId") or ""),
						"securityId": str(item.get("securityId") or ""),
						"security_id": str(item.get("securityId") or ""),
						"geekName": str(item.get("name") or ""),
						"name": str(item.get("name") or ""),
						"candidateName": str(item.get("name") or ""),
						"jobName": str(item.get("title") or ""),
						"cityName": str(item.get("city") or ""),
						"degreeName": str(item.get("degree") or ""),
						"tags": str(item.get("tags") or ""),
					}
					for item in raw
					if isinstance(item, dict)
				]
			if items:
				break
			time.sleep(0.2)
		self._recommendation_page_loaded = True
		return {"code": 0, "zpData": {"geekList": items}}

	def set_recommendation_job(self, job_name: str) -> dict[str, Any]:
		"""记录本轮推荐的岗位名称，兼容 RPA 临时岗位键没有真实 ID 的账号。"""
		clean_name = job_name.strip()
		if not clean_name:
			return {"code": -1, "message": "推荐岗位名称不能为空"}
		self._recommendation_job_name = clean_name
		return {"code": 0, "zpData": {"job_name": clean_name}}

	def _select_recommendation_job(self, platform_job_id: str) -> bool:
		"""切换并校验推荐页职位，兼容异步 iframe 和 BOSS 自定义下拉。

		推荐页是异步微前端：iframe 出现后，职位下拉和候选卡片还可能继续加载。
		因此“暂未加载”必须留在条件轮询中，而不是直接当成职位不存在。当前 BOSS
		版本使用 ``.job-item.curr`` 和 ``.ui-dropmenu-label``，同时保留原生
		``select``/数据属性路径，且岗位名称只按明确的“岗位名 + 分隔符”匹配，
		避免 Java 错配到 JavaScript 等相似岗位。
		"""
		target = platform_job_id.strip()
		if not target:
			return False
		target_json = json.dumps(target, ensure_ascii=False)
		deadline = time.monotonic() + 8.0
		clicked = False
		while time.monotonic() < deadline:
			result = self._eval(f"""
			(() => {{
				const target = {target_json};
				const normalise = value => String(value || '').replace(/\\s+/g, ' ').trim();
				const matchesName = value => {{
					const text = normalise(value);
					return text === target || text.startsWith(target + ' _') || text.startsWith(target + '_');
				}};
				const frame = document.querySelector('iframe[name="recommendFrame"]');
				const root = frame?.contentDocument;
				if (!root) return {{status: 'loading'}};
				const matches = element => [
					element.value, element.getAttribute('data-job'), element.getAttribute('data-jobid'),
					element.getAttribute('data-job-id'), element.getAttribute('data-value')
				].some(value => normalise(value) === target) || matchesName(element.textContent);
				const selected = Array.from(root.querySelectorAll(
					'select option:checked, [data-selected="true"], .selected, .job-item.curr, .job-selecter-wrap .ui-dropmenu-label'
				)).find(matches);
				if (selected) return {{status: 'selected'}};
				const select = Array.from(root.querySelectorAll('select')).find(element =>
					Array.from(element.options).some(matches));
				if (select) {{
					const option = Array.from(select.options).find(matches);
					select.value = option.value;
					select.dispatchEvent(new Event('change', {{bubbles: true}}));
					return {{status: 'changed_select'}};
				}}
				const option = Array.from(root.querySelectorAll(
					'.job-selecter-wrap .job-item, [data-job], [data-jobid], [data-job-id], [data-value]'
				)).find(element => matches(element) && element.offsetParent !== null);
				if (option) {{
					option.click();
					return {{status: 'clicked_option'}};
				}}
				const label = root.querySelector('.job-selecter-wrap .ui-dropmenu-label');
				if (label && label.offsetParent !== null) {{
					label.click();
					return {{status: 'loading'}};
				}}
				return {{status: 'loading'}};
			}})()
			""")
			if isinstance(result, dict) and result.get("status") == "selected":
				return True
			if isinstance(result, dict) and result.get("status") in {"changed_select", "clicked_option"}:
				clicked = True
				break
			time.sleep(0.2)
		if not clicked:
			return False

		deadline = time.monotonic() + 5.0
		while time.monotonic() < deadline:
			confirmed = self._eval(f"""
			(() => {{
				const target = {target_json};
				const normalise = value => String(value || '').replace(/\\s+/g, ' ').trim();
				const matches = element => [
					element.value, element.getAttribute('data-job'), element.getAttribute('data-jobid'),
					element.getAttribute('data-job-id'), element.getAttribute('data-value')
				].some(value => normalise(value) === target) ||
					normalise(element.textContent) === target ||
					normalise(element.textContent).startsWith(target + ' _');
				const root = document.querySelector('iframe[name="recommendFrame"]')?.contentDocument;
				if (!root) return false;
				return Array.from(root.querySelectorAll(
					'select option:checked, [data-selected="true"], .selected, .job-item.curr, .job-selecter-wrap .ui-dropmenu-label'
				)).some(matches);
			}})()
			""")
			if confirmed is True:
				return True
			time.sleep(0.2)
		return False

	def greet_recommendation_by_geek_id(self, geek_id: str) -> dict[str, Any]:
		"""在推荐牛人页向指定候选人发送已配置的岗位招呼语。

		推荐页候选人尚未产生 ``friend_id``，姓名和列表序号都可能随刷新变化，唯一
		可安全用于动作定位的是卡片暴露的 ``data-geek``。本方法不自行填写话术：
		BOSS 会发送岗位设置页中已同步的固定招呼语，保证开场白和本地岗位配置一致。
		点击后必须观察同一张卡片的禁用/已沟通回显，不能把 DOM 点击当成发送成功。
		"""
		target_geek_id = geek_id.strip()
		if not target_geek_id:
			return {"code": -1, "message": "geek_id 不能为空"}
		# 调用方通常刚从同一推荐页读取候选卡片。除非期间发生过设置页导航，
		# 否则不能再次刷新动态列表，否则旧卡片的 geek_id 可能已经轮换。
		if not getattr(self, "_recommendation_page_loaded", False):
			self.greet_rec_list()
		self._recommendation_page_loaded = True
		clicked = self._eval(f"""
		(() => {{
			const target = {json.dumps(target_geek_id, ensure_ascii=False)};
			const frame = document.querySelector('iframe[name="recommendFrame"]');
			const root = frame?.contentDocument;
			if (!root) return {{status: 'not_found'}};
			for (const card of root.querySelectorAll('.candidate-card-wrap')) {{
				const inner = card.querySelector('.card-inner, [data-geek], [data-geekid]') || card;
				const actual = inner.getAttribute('data-geek') || inner.getAttribute('data-geekid') ||
					inner.getAttribute('data-geek-id') || card.getAttribute('data-geek') || '';
				if (actual !== target) continue;
				const name = (card.querySelector('.name, .geek-name')?.textContent || '').trim();
				const button = card.querySelector('button.btn-greet, .btn-greet');
				if (!button || button.disabled || button.classList.contains('disabled')) {{
					return {{status: 'already_sent', geekId: actual, name}};
				}}
				button.scrollIntoView({{block: 'center'}});
				button.click();
				return {{status: 'clicked', geekId: actual, name}};
			}}
			return {{status: 'not_found'}};
		}})()
		""")
		if not isinstance(clicked, dict) or clicked.get("status") == "not_found":
			return {"code": -1, "message": "推荐页未找到目标候选人"}
		if clicked.get("status") == "already_sent":
			return {
				"code": 0,
				"zpData": {
					"geek_id": target_geek_id,
					"candidate_name": str(clicked.get("name") or ""),
					"status": "already_sent",
				},
			}
		if clicked.get("status") != "clicked":
			return {"code": -1, "message": "无法点击推荐候选人的打招呼按钮"}

		self.human_delay(0.8, 1.4)
		confirmed = self._eval(f"""
		(() => {{
			const target = {json.dumps(target_geek_id, ensure_ascii=False)};
			const frame = document.querySelector('iframe[name="recommendFrame"]');
			const root = frame?.contentDocument;
			const normalise = value => String(value || '').replace(/\\s+/g, ' ').trim();
			const pageText = normalise(document.body?.innerText);
			const frameText = normalise(root?.body?.innerText);
			const quotaText = `${{pageText}} ${{frameText}}`;
			// BOSS 的额度提示可能出现在顶层弹窗或推荐 iframe 中。只匹配明确的
			// “沟通”与“今日/上限”组合，不能把充值、登录或普通失败误判为额度耗尽。
			if (/沟通/.test(quotaText) && /(今日|当天|每日)/.test(quotaText) && /(上限|次数|额度)/.test(quotaText)) {{
				return {{status: 'quota', message: 'BOSS 推荐牛人今日沟通已达上限'}};
			}}
			if (!root) return {{status: 'unconfirmed'}};
			for (const card of root.querySelectorAll('.candidate-card-wrap')) {{
				const inner = card.querySelector('.card-inner, [data-geek], [data-geekid]') || card;
				const actual = inner.getAttribute('data-geek') || inner.getAttribute('data-geekid') ||
					inner.getAttribute('data-geek-id') || card.getAttribute('data-geek') || '';
				if (actual !== target) continue;
				const name = (card.querySelector('.name, .geek-name')?.textContent || '').trim();
				const button = card.querySelector('button.btn-greet, .btn-greet');
				const cardText = (card.textContent || '').replace(/\\s+/g, ' ').trim();
				const isSent = !button || button.disabled || button.classList.contains('disabled') ||
					/已打招呼|已沟通|已发送/.test(cardText);
				return {{status: isSent ? 'sent' : 'unconfirmed', geekId: actual, name}};
			}}
			return {{status: 'unconfirmed'}};
		}})()
		""")
		if isinstance(confirmed, dict) and confirmed.get("status") == "quota":
			return {
				"code": -1,
				"message": "BOSS 推荐牛人今日沟通已达上限",
				"error_code": "RECOMMENDATION_DAILY_QUOTA_REACHED",
			}
		if not isinstance(confirmed, dict) or confirmed.get("status") != "sent":
			return {"code": -1, "message": "打招呼未获得页面回显确认"}
		return {
			"code": 0,
			"zpData": {
				"geek_id": target_geek_id,
				"candidate_name": str(confirmed.get("name") or clicked.get("name") or ""),
				"status": "sent",
			},
		}

	def _click_nav(self, text: str) -> bool:
		"""点顶部导航栏中的标签（推荐牛人、搜索 等）。"""
		found = self._eval(f"""
		(() => {{
			const all = document.querySelectorAll('a, span, div, li, button');
			for (const el of all) {{
				const t = (el.textContent || '').trim();
				if (t === {json.dumps(text)} && el.offsetParent !== null) {{
					el.scrollIntoView({{block: 'center'}});
					el.click();
					return true;
				}}
			}}
			return false;
		}})()
		""")
		if found:
			self.human_delay(1.0, 2.0)
			self.wait_loaded()
		return bool(found)

	# ================================================================
	# 候选人联系方式与面试邀请
	# ================================================================

	def request_contact_exchange(self, *, friend_id: int, contact_type: str) -> dict[str, Any]:
		"""通过 BOSS 沟通页请求候选人电话或微信，并完成二次确认。

		联系方式属于候选人明确授权的敏感操作，必须使用当前页面真实 ``friend_id``
		定位会话。页面上可能残留上一位候选人的按钮，定位失败时直接中止，绝不按
		列表序号或当前选中项降级。点击后还要确认弹窗，避免只发送第一步请求。
		"""
		labels = {"phone": "换电话", "wechat": "换微信"}
		label = labels.get(contact_type.strip())
		if friend_id <= 0 or label is None:
			return {"code": -1, "message": "联系方式类型或 friend_id 无效"}
		if not self._open_conversation_for_candidate(friend_id):
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}
		if not self._click_visible_candidate_action(label):
			return {"code": -1, "message": f"未找到 BOSS 的{label}按钮"}
		if not self._confirm_contact_exchange(label):
			return {"code": -1, "message": f"BOSS 未确认{label}请求"}
		value = self._read_contact_value(contact_type)
		data: dict[str, Any] = {"friend_id": friend_id, "contact_type": contact_type, "confirmed": True}
		# BOSS 有时只回显“已同意”而不是号码；没有真实值时返回等待态，
		# 上层会按退避重试，不能把“按钮点击成功”误记成联系方式已获取。
		if value:
			data["value"] = value
		return {"code": 0, "zpData": data}

	def _read_contact_value(self, contact_type: str) -> str:
		"""从当前可见聊天面板读取已同意的联系方式，避免读取隐藏模板。"""
		needle = "手机" if contact_type == "phone" else "微信"
		try:
			result = self._eval(f"""
		(() => {{
			const needle = {json.dumps(needle, ensure_ascii=False)};
			const visible = Array.from(document.querySelectorAll('body *')).filter(el =>
				el.offsetParent !== null && (el.textContent || '').includes(needle));
			for (const el of visible.reverse()) {{
				const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
				const match = contact_type === 'phone'
					? text.match(/1[3-9]\\d{{9}}/)
					: text.match(/[a-zA-Z][a-zA-Z0-9._-]{{3,}}/);
				if (match) return match[0];
			}}
			return '';
		}})()
			""".replace("contact_type === 'phone'", json.dumps(contact_type) + " === 'phone'"))
		except Exception:
			# 单元测试、页面切换或 CDP 短暂断开时不能把“已确认”误报成
			# 已取得号码；返回空值让上层按退避策略等待下一轮。
			return ""
		return str(result or "").strip()[:120]

	def invite_interview_via_ui(self, *, friend_id: int, payload: dict[str, str]) -> dict[str, Any]:
		"""在精确会话内提交已验证的面试设置，并以页面回显确认。"""
		if friend_id <= 0:
			return {"code": -1, "message": "friend_id 无效"}
		if not self._open_conversation_for_candidate(friend_id):
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}
		if not self._submit_interview_invitation(payload):
			return {"code": -1, "message": "BOSS 约面试提交未获得页面回显"}
		return {"code": 0, "zpData": {"friend_id": friend_id, "confirmed": True}}

	def _open_conversation_for_candidate(self, friend_id: int) -> bool:
		"""打开与 ``friend_id`` 完全匹配的会话，并在点击前后二次核验 DOM 标识。"""
		self._ensure_chat_page()
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			return False
		clicked = self._eval(f"""
		(() => {{
			const expected = {json.dumps(str(friend_id))};
			const card = document.querySelectorAll('.geek-item-wrap')[{target_idx}];
			const inner = card?.querySelector('.geek-item, [data-id]');
			if (!inner || (inner.getAttribute('data-id') || '').split('-')[0] !== expected) return false;
			card.scrollIntoView({{block: 'center'}});
			card.click();
			return true;
		}})()
		""")
		if clicked is not True:
			return False
		self.human_delay(0.5, 1.0)
		return True

	def _click_visible_candidate_action(self, label: str) -> bool:
		"""点击当前聊天面板内的精确动作按钮，排除隐藏模板和其它会话 DOM。"""
		clicked = self._eval(f"""
		(() => {{
			const label = {json.dumps(label, ensure_ascii=False)};
			const button = Array.from(document.querySelectorAll('button, a, [role="button"]')).find(element =>
				element.offsetParent !== null && (element.textContent || '').trim() === label && !element.disabled);
			if (!button) return false;
			button.scrollIntoView({{block: 'center'}});
			button.click();
			return true;
		}})()
		""")
		return clicked is True

	def _confirm_contact_exchange(self, label: str, timeout: float = 5.0) -> bool:
		"""确认联系方式二次弹窗，并检查弹窗或按钮状态出现完成回显。"""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			confirmed = self._eval(f"""
			(() => {{
				const label = {json.dumps(label, ensure_ascii=False)};
				const dialog = Array.from(document.querySelectorAll('[role="dialog"], .dialog-container, [class*="dialog"], [class*="modal"]'))
					.find(element => element.offsetParent !== null && (element.textContent || '').includes(label));
				if (!dialog) return false;
				const confirm = Array.from(dialog.querySelectorAll('button, [role="button"]')).find(element =>
					element.offsetParent !== null && ['确认', '确定', '同意'].includes((element.textContent || '').trim()) && !element.disabled);
				if (!confirm) return false;
				confirm.click();
				return true;
			}})()
			""")
			if confirmed is True:
				self.human_delay(0.3, 0.6)
				return True
			time.sleep(0.2)
		return False

	def _submit_interview_invitation(self, payload: dict[str, str], timeout: float = 6.0) -> bool:
		"""填写 BOSS 约面试弹窗并确认提交；字段只来自本地已验证的设置。"""
		if not self._click_visible_candidate_action("约面试"):
			return False
		serialized = json.dumps(payload, ensure_ascii=False)
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			result = self._eval(f"""
			(() => {{
				const payload = {serialized};
				const dialog = Array.from(document.querySelectorAll('[role="dialog"], .dialog-container, [class*="dialog"], [class*="modal"]'))
					.find(element => element.offsetParent !== null && /面试/.test(element.textContent || ''));
				if (!dialog) return {{status: 'waiting'}};
				const setValue = (selectors, value) => {{
					if (!value) return true;
					const input = selectors.map(selector => dialog.querySelector(selector)).find(Boolean);
					if (!input) return false;
					const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
					if (setter && input instanceof HTMLInputElement) setter.call(input, value); else input.value = value;
					input.dispatchEvent(new Event('input', {{bubbles: true}})); input.dispatchEvent(new Event('change', {{bubbles: true}}));
					return true;
				}};
				if (!setValue(['input[name="date"]', 'input[placeholder*="日期"]'], payload.date) ||
					!setValue(['input[name="time"]', 'input[placeholder*="时间"]'], payload.time) ||
					!setValue(['input[name="address"]', 'input[placeholder*="地点"]'], payload.address) ||
					!setValue(['input[name="contactName"]', 'input[placeholder*="联系人"]'], payload.contact_name) ||
					!setValue(['input[name="contactPhone"]', 'input[placeholder*="电话"]'], payload.contact_phone)) return {{status: 'missing_field'}};
				const note = dialog.querySelector('textarea[name="note"], textarea[placeholder*="备注"]');
				if (note && payload.note) {{ note.value = payload.note; note.dispatchEvent(new Event('input', {{bubbles: true}})); }}
				const submit = Array.from(dialog.querySelectorAll('button, [role="button"]')).find(element =>
					element.offsetParent !== null && ['发送邀请', '确定', '确认'].includes((element.textContent || '').trim()) && !element.disabled);
				if (!submit) return {{status: 'waiting'}};
				submit.click();
				return {{status: 'submitted'}};
			}})()
			""")
			if isinstance(result, dict) and result.get("status") == "submitted":
				self.human_delay(0.4, 0.8)
				return True
			if isinstance(result, dict) and result.get("status") == "missing_field":
				return False
			time.sleep(0.2)
		return False

	# ================================================================
	# 在线简历 — 从沟通页
	# ================================================================

	def open_online_resume_preview(self, *, friend_id: int) -> dict[str, Any]:
		"""按真实会话身份打开 BOSS 在线简历并读取本地预览文本。

		候选人不存在时直接失败，不能沿用历史 ``view_geek`` 的首卡兜底，否则列表
		变化会打开错误候选人。读取结果只回传给本地控制台：不写文件、不下载附件、
		不关闭 BOSS 弹窗，方便用户同时核对官方页面。
		"""
		self._ensure_chat_page()
		self.human_delay(0.3, 0.6)
		target_index = self._find_card_by_friend_id(friend_id)
		if target_index is None:
			return {"code": -1, "message": "candidate not found"}
		target_candidate_name = self._candidate_name_from_card(target_index)
		clicked_candidate = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			const card = cards[{target_index}];
			if (!card) return false;
			card.scrollIntoView({{block: 'center'}});
			card.click();
			return true;
		}})()
		""")
		if clicked_candidate is not True:
			return {"code": -1, "message": "candidate click failed"}
		self.human_delay(1.0, 1.6)
		# 页面可能保留历史预览 iframe。记录打开动作前的数量，后续只允许读取
		# 本次新增的 iframe，避免重试或切换候选人时串读上一份简历。
		# BOSS 可能复用已有 iframe，也可能创建新 iframe；身份门禁依靠当前卡片
		# 姓名校验，因此这里必须扫描全部可见预览层，不能按数量排除旧层。
		min_frame_index = 0
		opened = self._eval("""
		(() => {
			const candidates = Array.from(document.querySelectorAll('a, button'));
			const target = candidates.find(element =>
				element.offsetParent && /^(查看)?在线简历$/.test((element.textContent || '').trim())
			);
			if (!target) return false;
			target.click();
			return true;
		})()
		""")
		if opened is not True:
			return {"code": -1, "message": "online resume entry not found"}
		self.human_delay(0.8, 1.2)
		preview = self._read_open_online_resume_preview(min_frame_index=min_frame_index)
		if not is_meaningful_online_resume_text(preview["resume_text"]):
			return {"code": -1, "message": "online resume text unavailable"}
		if target_candidate_name:
			normalized_name = re.sub(r"\s+", "", target_candidate_name)
			preview_name = re.sub(r"\s+", "", str(preview.get("candidate_name") or ""))
			if preview_name and normalized_name and preview_name != normalized_name:
				return {"code": -1, "message": "online resume candidate mismatch"}
		preview["candidate_name"] = target_candidate_name or preview["candidate_name"]
		return {"code": 0, **preview}

	def _candidate_name_from_card(self, target_index: int) -> str:
		"""读取已由 friend_id 精确定位的会话卡片姓名，供预览身份校验。

		姓名不参与候选人定位，只作为 OCR 结果的第二道身份门禁。页面结构漂移
		导致姓名不可读时返回空字符串，仍保留 friend_id 与 iframe 边界校验；
		绝不使用其他卡片姓名或通用“候选人”占位符代替。
		"""
		value = self._eval(f"""
		(() => {{
			const card = document.querySelectorAll('.geek-item-wrap')[{target_index}];
			if (!card) return '';
			const node = card.querySelector('.geek-name, .name, [class*="name"]');
			return (node?.textContent || '').trim();
		}})()
		""")
		return str(value or "").strip()[:120]

	def _online_resume_frame_count(self) -> int:
		"""返回打开动作前已有的在线简历 iframe 数量，作为本次读取下界。"""
		count = self._eval(
			"document.querySelectorAll('.new-resume-online-main-ui iframe, [class*=\"resume-online\"] iframe').length"
		)
		if isinstance(count, bool) or not isinstance(count, (int, float)):
			return 0
		return max(0, int(count))

	def _read_open_online_resume_preview(self, *, min_frame_index: int) -> dict[str, str]:
		"""读取已打开在线简历的可见内容，优先 DOM，跨域 iframe 时降级为本地 OCR。

		BOSS 在线简历有时把正文放入同源容器，有时放入无法直接访问的 iframe。
		因此先取可见文字，只有文字不足时才截取 iframe 区域做 OCR。此方法绝不
		关闭预览或写入磁盘。``min_frame_index`` 把历史预览排除在本次身份边界外，
		即使新预览渲染失败，也不能退回读取上一位候选人的内容。
		"""
		visible_script = """
		(() => {
			const frames = Array.from(document.querySelectorAll(
				'.new-resume-online-main-ui iframe, [class*="resume-online"] iframe'
			)).slice(__MIN_FRAME_INDEX__);
			const visibleFrames = frames.filter(frame => frame.offsetParent !== null);
			const frame = visibleFrames[visibleFrames.length - 1];
			const root = frame?.closest('.new-resume-online-main-ui, [class*="resume-online"]');
			if (!root || root.offsetParent === null) return {candidate_name: '', resume_text: ''};
			const nameNode = root.querySelector('[class*="name"], [class*="Name"]');
			return {
				candidate_name: (nameNode?.textContent || '').trim(),
				resume_text: (root.innerText || '').trim(),
			};
		})()
		"""
		visible = self._eval(visible_script.replace("__MIN_FRAME_INDEX__", str(max(0, int(min_frame_index)))))
		candidate_name = ""
		resume_text = ""
		if isinstance(visible, dict):
			candidate_name = str(visible.get("candidate_name") or "").strip()[:120]
			resume_text = str(visible.get("resume_text") or "").strip()
		# 页面根容器同时包含操作菜单和沟通进度；只有通过正文门禁的 DOM
		# 内容才可直接展示，否则走 iframe OCR，避免把页面壳误报成简历。
		if not is_meaningful_online_resume_text(resume_text):
			resume_text = ""
		if not resume_text:
			resume_text = self._ocr_open_online_resume_preview(min_frame_index=min_frame_index)
		if not is_meaningful_online_resume_text(resume_text):
			resume_text = ""
		return {"candidate_name": candidate_name, "resume_text": resume_text[:20_000]}

	def _ocr_open_online_resume_preview(self, *, min_frame_index: int) -> str:
		"""对最后打开且已完成渲染的在线简历 Canvas 做本地 OCR。

		BOSS 使用 WASM 把在线简历画到 iframe Canvas，DOM 中没有可提取正文；
		同一沟通页还可能保留上一次预览层。这里等待最后一个可见 iframe 的
		Canvas 脱离浏览器默认 300x150 尺寸，再截图并复用项目已有 RapidOCR。
		截图和识别正文都只存在于内存，不写入磁盘或运行日志。
		"""
		import base64
		import io

		canvas_script = """
			(() => {
				const minFrameIndex = __MIN_FRAME_INDEX__;
				const visibleFrames = Array.from(document.querySelectorAll(
						'.new-resume-online-main-ui iframe, [class*="resume-online"] iframe'
				)).slice(minFrameIndex).filter(frame => frame.offsetParent !== null);
				const renderedFrames = visibleFrames.map(frame => {
					try {
						return {frame, canvas: frame.contentDocument?.querySelector('canvas') || null};
					} catch (_error) {
						return {frame, canvas: null};
					}
				}).filter(({canvas}) => canvas && (canvas.width > 300 || canvas.height > 150));
				const rendered = renderedFrames[renderedFrames.length - 1];
				if (rendered?.canvas) {
					const dataUrl = rendered.canvas.toDataURL('image/png');
					return {
						data: dataUrl.slice(dataUrl.indexOf(',') + 1),
						width: rendered.canvas.width,
						height: rendered.canvas.height,
					};
				}
				// BOSS 当前版本会把简历画布以 CSS 放大到完整 iframe，但 Canvas
				// 内部尺寸仍停留在默认 300x150。此时 toDataURL 分辨率不足，改为
				// 返回最主要的可见 iframe 区域，由 CDP 按实际显示尺寸截图。
				const visibleFrame = visibleFrames
					.filter(frame => frame.clientWidth > 100 && frame.clientHeight > 100)
					.sort((left, right) => (left.clientWidth * left.clientHeight) - (right.clientWidth * right.clientHeight))
					.pop();
				if (visibleFrame) {
					const rect = visibleFrame.getBoundingClientRect();
					return {
						clip: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
					};
				}
				return null;
			})()
			"""
		# WASM 绘制在慢网络下可能超过 CDP 单命令的 10 秒 socket 预算。每次
		# 查询必须同步快速返回，Python 侧分段轮询承担 30 秒总预算；这样空白
		# Canvas 不会让 CDP 连接超时，暂停或其它任务也能在轮询间隙获得机会。
		canvas_snapshot: object = None
		deadline = time.monotonic() + 30.0
		resolved_script = canvas_script.replace("__MIN_FRAME_INDEX__", str(max(0, int(min_frame_index))))
		while time.monotonic() < deadline:
			canvas_snapshot = self._eval(resolved_script)
			if isinstance(canvas_snapshot, dict) and (
				isinstance(canvas_snapshot.get("data"), str) or isinstance(canvas_snapshot.get("clip"), dict)
			):
				break
			time.sleep(0.25)
		if not isinstance(canvas_snapshot, dict):
			return ""
		try:
			from PIL import Image, ImageEnhance
			from rapidocr_onnxruntime import RapidOCR

			encoded_image = canvas_snapshot.get("data")
			if not isinstance(encoded_image, str):
				clip = canvas_snapshot.get("clip")
				if not isinstance(clip, dict):
					return ""
				# 截图只覆盖 BOSS 当前可见在线简历 iframe，不包含其它页面区域，
				# 也不写入磁盘；浮点坐标可避免浏览器缩放下提前截断正文边缘。
				screenshot = self._cdp_send(
					"Page.captureScreenshot",
					{
						"format": "png",
						"captureBeyondViewport": True,
						"clip": {
							"x": float(clip.get("x") or 0),
							"y": float(clip.get("y") or 0),
							"width": float(clip.get("width") or 0),
							"height": float(clip.get("height") or 0),
							"scale": 1,
						},
					},
				)
				encoded_image = screenshot.get("data") if isinstance(screenshot, dict) else None
			if not isinstance(encoded_image, str):
				return ""
			# ``Image.open`` 的具体实现类型会随图片格式变化；后续只依赖通用
			# ``Image.Image`` 接口。适度放大和增强对比度可提高 Canvas 小字号
			# 识别率，同时复用项目锁定的 OCR 依赖，避免额外依赖外部可执行文件。
			image: Image.Image = Image.open(io.BytesIO(base64.b64decode(encoded_image)))
			# 超长简历本身已有足够像素，统一三倍放大会造成不必要的内存峰值。
			# 根据总像素选择倍率，在小画布识别率和长简历稳定性之间取平衡。
			scale = 3 if image.width * image.height <= 2_000_000 else 2
			image = ImageEnhance.Contrast(
				image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
			).enhance(1.3)
			result, _ = RapidOCR()(image)
			return "\n".join(
				str(item[1]).strip()
				for item in (result or [])
				if len(item) > 1 and str(item[1]).strip()
			).strip()
		except Exception as exc:
			# 仅保留失败类别，避免把 OCR 内容或截图数据写入运行日志。
			self._log(f"在线简历 OCR 读取失败：{type(exc).__name__}")
			return ""

	def view_geek(
		self, geek_id: str, job_id: str, security_id: str | None = None, friend_id: int | None = None
	) -> dict[str, Any]:
		"""打开指定候选人的在线简历并读取。"""
		# 尝试从 geek_id 中提取 friendId（RPA 模式下 geek_id 可能编码了 friendId）
		resolved_fid = friend_id
		if resolved_fid is None and geek_id.startswith("friendid:"):
			try:
				resolved_fid = int(geek_id.split(":")[1])
			except (ValueError, IndexError):
				pass
		return self._read_conversation_resume(friend_id=resolved_fid)

	def _read_conversation_resume(self, friend_id: int | None = None) -> dict[str, Any]:
		"""从沟通列表：点开指定对话 → 查看在线简历 → 截图OCR。"""
		import base64
		import io

		self._ensure_chat_page()
		self.human_delay(0.5, 1.0)

		# 点开指定候选人的对话
		if friend_id is not None:
			target_idx = self._find_card_by_friend_id(friend_id)
			if target_idx is None:
				self._log(f"[RPA] view_geek: friend_id {friend_id} not found, falling back to first card")
				target_idx = 0
		else:
			target_idx = 0  # 默认第一个

		self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			if (cards.length > {target_idx}) {{
				cards[{target_idx}].scrollIntoView({{block: 'center'}});
				cards[{target_idx}].click();
			}}
		}})()
		""")
		self.human_delay(2, 3)

		# 点"在线简历"
		self._eval(
			"""(() => { const all = document.querySelectorAll('a'); for (const a of all) { if ((a.textContent||'').trim()==='在线简历' && a.offsetParent) { a.click(); return; } } })()"""
		)
		self.human_delay(3, 4)

		resume_text = ""
		# 截图 iframe → OCR
		rect = self._eval(
			"""(() => { const f = document.querySelector('.new-resume-online-main-ui iframe'); if (!f) return null; const r = f.getBoundingClientRect(); return {x:r.x, y:r.y, w:r.width, h:r.height}; })()"""
		)
		if rect and isinstance(rect, dict) and rect.get("w", 0) > 100:
			try:
				from PIL import Image, ImageEnhance

				r = self._cdp_send(
					"Page.captureScreenshot",
					{
						"format": "png",
						"clip": {
							"x": int(rect["x"]),
							"y": int(rect["y"]),
							"width": int(rect["w"]),
							"height": int(rect["h"]),
							"scale": 1,
						},
					},
				)
				img = Image.open(io.BytesIO(base64.b64decode(r["data"])))
				img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
				img = ImageEnhance.Contrast(img).enhance(1.3)
				import pytesseract

				pytesseract.pytesseract.tesseract_cmd = r"D:\Program Files\Tesseract-OCR\tesseract.exe"
				resume_text = pytesseract.image_to_string(img, lang="chi_sim", config="--psm 4")
				self._log(f"OCR resume: {len(resume_text)} chars")
			except Exception as e:
				self._log(f"OCR failed: {e}")

		self._close_modal()

		# 直接保存到桌面
		import os as _os

		desktop = _os.path.join(_os.path.expanduser("~"), "Desktop")
		filepath = _os.path.join(desktop, f"BOSS_简历_RPA_{int(time.time())}.md")
		with open(filepath, "w", encoding="utf-8") as f:
			f.write(f"# BOSS 在线简历 (RPA+OCR)\n\n导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{resume_text}")

		return {
			"code": 0,
			"zpData": {
				"resume_text": resume_text[:20000],
				"geekDetailInfo": {"resume_text": resume_text[:20000]},
				"geekBaseInfo": {"name": "RPA导出"},
				"geekWorkExpList": [],
				"geekProjExpList": [],
				"geekEduExpList": [],
				"_rpa_file": filepath,
			},
		}

	# ================================================================
	# 在线简历 — 从推荐牛人
	# ================================================================

	def read_recommendation_resume(self, index: int = 0) -> dict[str, Any]:
		"""从推荐牛人列表：点第 N 个 → 查看在线简历 → 读内容。"""
		self.greet_rec_list()  # 导航到推荐页面

		text_before = self._page_text()

		# 点第 index 个可见 li（推荐卡片也是 li）
		clicked = self._eval(f"""
		(() => {{
			const items = document.querySelectorAll('li');
			let count = 0;
			for (const el of items) {{
				if (!el.offsetParent) continue;
				const t = (el.textContent || '').trim();
				if (t.length < 10) continue;
				if (count === {index}) {{
					el.scrollIntoView({{block: 'center'}});
					el.click();
					return true;
				}}
				count++;
			}}
			return false;
		}})()
		""")
		if clicked:
			self.human_delay(1.5, 2.5)

		# 找简历按钮（和沟通简历一样的逻辑）
		found = self._eval("""
		(() => {
			const all = document.querySelectorAll('a, span, div, button');
			for (const el of all) {
				const t = (el.textContent || '').trim();
				if ((t === '在线简历' || t === '查看简历' || (t.includes('在线') && t.includes('简历'))) &&
					el.offsetParent !== null && t.length < 20) {
					el.click(); return t;
				}
			}
			return '';
		})()
		""")
		if found:
			self.human_delay(1.5, 2.5)

		resume_text = self._read_modal_content(text_before)
		self._close_modal()

		return {"code": 0, "zpData": {"resume_text": resume_text, "geekDetailInfo": {"resume_text": resume_text}}}

	# ================================================================
	# 附件简历
	# ================================================================

	def read_attachment_resume(self, friend_id: int | None = None) -> dict[str, Any]:
		"""从沟通页面读附件简历。打开对话 → 找附件 → 读取。

		注意：附件可能是 PDF/图片，CDP 只能读到文件名和下载链接，
		实际文件内容需要通过 Chrome 下载后再解析。
		"""
		self.navigate_to(CHAT_PAGE)
		self.wait_loaded()
		self.human_delay(1.0, 2.0)

		# 打开对话
		self._eval("""
		(() => {
			const items = document.querySelectorAll('li');
			for (const el of items) {
				if (el.offsetParent && (el.textContent || '').length > 15) {
					el.click(); return true;
				}
			}
			return false;
		})()
		""")
		self.human_delay(1.5, 2.5)

		# 找附件链接
		attachments = self._eval("""
		Array.from(document.querySelectorAll('a[href*="file"], a[href*="download"], a[href*="attach"], [class*="file"], [class*="attach"]'))
			.filter(el => el.offsetParent !== null)
			.map(el => ({text: (el.textContent || '').trim(), href: el.getAttribute('href') || ''}))
		""")

		return {"code": 0, "zpData": {"attachments": attachments if isinstance(attachments, list) else []}}

	# ================================================================
	# 弹窗内容读取
	# ================================================================

	def _read_modal_content(self, text_before: str = "") -> str:
		"""读取弹窗内容。如果传了点击前的页面文字，自动做差量提取。"""
		# 等弹窗出来
		self.human_delay(0.5, 1.5)

		# 先尝试找弹窗容器
		for sel in [
			'[class*="modal"]',
			'[class*="dialog"]',
			'[class*="popup"]',
			'[class*="drawer"]',
			'[class*="resume"]',
			'[class*="geek"]',
			'[class*="panel"]',
			'[class*="overlay"]',
			'[role="dialog"]',
			'[aria-modal="true"]',
		]:
			try:
				text = self._eval(f"""
				(() => {{
					const els = document.querySelectorAll({json.dumps(sel)});
					for (const el of els) {{
						if (el.offsetParent !== null) {{
							const t = (el.textContent || '').trim();
							if (t.length > 100) return t;
						}}
					}}
					return '';
				}})()
				""")
				if isinstance(text, str) and len(text) > 100:
					return text[:20000]
			except Exception:
				continue

		# 差量法：读当前全页文字，扣除之前的
		current = self._page_text()
		if text_before and len(current) > len(text_before):
			# 取新增的部分
			diff = current[len(text_before) :]
			if len(diff) > 100:
				return diff[:20000]

		# 最后手段：返回全页文字中像简历的部分
		resume_keywords = ["工作经历", "教育经历", "项目经验", "个人优势", "技能", "求职意向", "基本信息", "工作经验"]
		for kw in resume_keywords:
			if kw in current:
				return current[:20000]

		return current[:20000]

	def _close_modal(self) -> None:
		"""关闭弹窗。"""
		# 找关闭按钮
		for text in ["×", "关闭", "Close", "close"]:
			clicked = self._eval(f"""
			(() => {{
				const els = document.querySelectorAll('button, a, span, [class*="close"], [class*="Close"]');
				for (const el of els) {{
					if ((el.textContent || '').trim() === {json.dumps(text)}) {{
						el.click(); return true;
					}}
				}}
				return false;
			}})()
			""")
			if clicked:
				self.human_delay(0.3, 0.8)
				return
		# ESC 兜底
		self._cdp_send(
			"Input.dispatchKeyEvent",
			{
				"type": "keyDown",
				"key": "Escape",
				"code": "Escape",
			},
		)

	# ================================================================
	# 其他
	# ================================================================

	def _find_cards_by_friend_ids(self, friend_ids: list[int]) -> dict[int, tuple[int, int]]:
		"""分块定位目标会话，返回后续点击所需的滚动位置和 DOM 索引。

		沟通列表采用虚拟渲染。不能把完整滚动和逐窗口等待放进一个浏览器 Promise：
		500 人以上时该 Promise 会独占 CDP 连接直至超时，导致整轮对话处理失败。
		这里复用快照的短窗口策略，每次只滚动、等待一次渲染并读取可见目标；全部
		目标命中即可提前退出，最后始终恢复用户原本看到的列表位置。
		"""
		target_ids = sorted({friend_id for friend_id in friend_ids if isinstance(friend_id, int) and friend_id > 0})
		if not target_ids:
			return {}
		positions: dict[int, tuple[int, int]] = {}
		bounds = self._conversation_snapshot_bounds()
		if bounds is None:
			return positions
		original_top, max_top, step = bounds
		try:
			for scroll_top in self._snapshot_scroll_positions(max_top=max_top, step=step):
				for friend_id, position in self._conversation_target_chunk(
					target_ids=target_ids,
					scroll_top=scroll_top,
				).items():
					positions.setdefault(friend_id, position)
				if len(positions) == len(target_ids):
					break
		finally:
			self._restore_conversation_snapshot_scroll(original_top)
		return positions

	def _conversation_target_chunk(
		self,
		*,
		target_ids: list[int],
		scroll_top: int,
	) -> dict[int, tuple[int, int]]:
		"""在一个虚拟列表窗口内读取目标会话，避免长时间 CDP Promise。"""
		result = self._eval(
			f"""
			(async () => {{
				// bossAgentTargetChunk
				const targetIds = new Set({json.dumps(target_ids)});
				const list = document.querySelector('.user-list');
				if (!list) return {{positions: []}};
				list.scrollTop = {scroll_top};
				list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
				await new Promise(resolve => setTimeout(resolve, 80));
				return {{
					positions: Array.from(document.querySelectorAll('.geek-item-wrap'))
						.map((card, index) => {{
							const inner = card.querySelector('.geek-item, [data-id]');
							const friendId = Number((inner?.getAttribute('data-id') || '').split('-')[0]);
							return targetIds.has(friendId)
								? {{friendId, scrollTop: list.scrollTop, index}}
								: null;
						}})
						.filter(Boolean),
				}};
			}})()
			""",
			await_promise=True,
		)
		if not isinstance(result, dict) or not isinstance(result.get("positions"), list):
			return {}
		positions: dict[int, tuple[int, int]] = {}
		for item in result["positions"]:
			if not isinstance(item, dict):
				continue
			try:
				friend_id = int(item["friendId"])
				item_scroll_top = int(item["scrollTop"])
				index = int(item["index"])
			except (KeyError, TypeError, ValueError):
				continue
			if friend_id in target_ids and item_scroll_top >= 0 and index >= 0:
				positions[friend_id] = (item_scroll_top, index)
		return positions

	def _find_card_by_friend_id(self, friend_id: int) -> int | None:
		"""按 BOSS 真实 friendId 定位虚拟沟通列表中的当前卡片索引。

		BOSS 仅把滚动窗口附近的卡片保留在 DOM 中。先查当前窗口可避免无谓滚动；
		未命中时再按完整渲染窗口搜索，并在结束后恢复原位置。这样上层可以先批量
		收集列表、再按任意候选人读取消息，而不会因最终停在另一窗口而漏掉目标。
		"""
		current_index = self._find_card_index_in_current_view(friend_id)
		if current_index is not None:
			return current_index
		viewport = self._eval("""
		(() => {
			const list = document.querySelector('.user-list');
			if (!list) return null;
			return {top: list.scrollTop, height: list.scrollHeight, viewport: list.clientHeight};
		})()
		""")
		if not isinstance(viewport, dict):
			return None
		original_top = viewport.get("top")
		scroll_height = viewport.get("height")
		client_height = viewport.get("viewport")
		if not all(isinstance(value, (int, float)) for value in (original_top, scroll_height, client_height)):
			return None
		max_scroll_top = max(0, int(scroll_height - client_height))
		# 与列表读取保持四个视口的步长，既跨过已渲染节点，又在窗口边界保留重叠。
		step = max(1, int(client_height) * 4)
		targets = list(range(0, max_scroll_top + 1, step))
		if targets[-1] != max_scroll_top:
			targets.append(max_scroll_top)
		located_index: int | None = None
		try:
			for target_top in targets:
				if abs(float(original_top) - target_top) > 1:
					self._eval(f"""
					(() => {{
						const list = document.querySelector('.user-list');
						if (!list) return false;
						const targetScrollTop = {target_top};
						list.scrollTop = targetScrollTop;
						list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
						return true;
					}})()
					""")
					self.human_delay(0.15, 0.3)
				found_index = self._find_card_index_in_current_view(friend_id)
				if found_index is not None:
					located_index = found_index
					break
		finally:
			# 成功时必须保留目标卡片所在窗口，调用方随后会按返回索引点击该 DOM 节点。
			# 只有完全找不到时才恢复原位置，避免失败搜索扰乱用户或下一轮处理位置。
			if located_index is None:
				self._eval(f"""
				(() => {{
					const list = document.querySelector('.user-list');
					if (!list) return false;
					list.scrollTop = {int(original_top)};
					list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
					return true;
				}})()
				""")
		return located_index

	def _find_card_index_in_current_view(self, friend_id: int) -> int | None:
		"""只在当前 DOM 窗口查询稳定 friendId，不做滚动副作用。"""
		result = self._eval(f"""
		(() => {{
			const targetFriendId = '{friend_id}';
			const cards = document.querySelectorAll('.geek-item-wrap');
			for (let i = 0; i < cards.length; i++) {{
				const inner = cards[i].querySelector('.geek-item, [data-id]');
				if (inner) {{
					const dataId = inner.getAttribute('data-id') || '';
					const fid = dataId.split('-')[0];
					if (fid === targetFriendId) return i;
				}}
			}}
			return -1;
		}})()
		""")
		if isinstance(result, (int, float)) and result >= 0:
			return int(result)
		return None

	def _ensure_chat_page(self) -> None:
		"""确保浏览器在沟通页面，最多等 10 秒让页面加载。"""
		self.navigate_to(CHAT_PAGE)
		self.wait_loaded()
		self.human_delay(0.5, 1.0)

	def list_jobs(self) -> dict[str, Any]:
		"""读取职位管理页的结构化岗位卡片。

		职位同步依赖岗位名称和平台标识建立稳定镜像。旧实现把任何含 ``job`` 的
		元素或 ``li`` 的全部文本直接当成职位，既会把导航、筛选项混入结果，也会
		在卡片已结构化返回时丢失标识。这里将选择器集中在 ``rpa.pages``，并只把
		卡片的最小字段投影给上层；未暴露平台 ID 的卡片仍保留名称，供本地镜像层
		生成仅限本地使用的关联键。
		"""
		from boss_agent_cli.rpa.pages import JOB_CARD, JOB_STATUS, JOB_TITLE

		self.navigate_to(JOB_PAGE)
		self.wait_loaded()
		self.human_delay(1.0, 2.0)
		jobs: list[dict[str, Any]] = []
		# 将 Python 选择器序列序列化为 JSON，避免把字符串直接拼进 JavaScript
		# 后因引号或转义差异导致选择器失效。
		card_selectors = json.dumps(JOB_CARD, ensure_ascii=False)
		title_selectors = json.dumps(JOB_TITLE, ensure_ascii=False)
		status_selectors = json.dumps(JOB_STATUS, ensure_ascii=False)
		# ``document.readyState`` 只代表外层壳页面完成加载，职位卡片实际在
		# 同源 iframe 内异步渲染。必须等到真实卡片出现才开始读取，不能因为
		# iframe 慢一拍就降级为聊天页职位下拉，后者没有完整岗位字段。
		card_ready_js = f"""
		(() => {{
			const cardSelectors = {card_selectors};
			const roots = [document];
			for (const frame of document.querySelectorAll('iframe')) {{
				try {{ if (frame.contentDocument) roots.push(frame.contentDocument); }} catch (_) {{}}
			}}
			for (const root of roots) for (const selector of cardSelectors) {{
				if (Array.from(root.querySelectorAll(selector)).some(element => element.offsetParent !== null)) return true;
			}}
			return false;
		}})()
		"""
		deadline = time.monotonic() + 8.0
		while time.monotonic() < deadline:
			try:
				ready = self._eval(card_ready_js)
				if ready is True:
					break
				# 测试替身只模拟最终卡片投影，不执行浏览器 JavaScript；遇到
				# 非布尔返回时交由后续投影逻辑处理，避免无意义地等满超时。
				if not isinstance(ready, bool):
					break
			except Exception:
				# 路由切换期间 iframe 会短暂失效，下一轮应继续轮询而不是
				# 立即把加载中误当作“没有职位”。
				pass
			time.sleep(0.25)
		js = f"""
		(() => {{
			const cardSelectors = {card_selectors};
			const titleSelectors = {title_selectors};
			const statusSelectors = {status_selectors};
			// 职位管理是同源 iframe 微前端，顶层 document 只有导航壳；
			// 将顶层和所有可访问 iframe 作为读取根，兼容 BOSS 页面拆分版本。
			const roots = [document];
			for (const frame of document.querySelectorAll('iframe')) {{
				try {{ if (frame.contentDocument) roots.push(frame.contentDocument); }} catch (_) {{}}
			}}
			const cards = [];
			const seen = new Set();
			for (const root of roots) {{
				for (const selector of cardSelectors) {{
					for (const element of root.querySelectorAll(selector)) {{
						if (element.offsetParent !== null && !seen.has(element)) {{
							seen.add(element);
							cards.push(element);
						}}
					}}
				}}
			}}
			const textFrom = (element, selectors) => {{
				for (const selector of selectors) {{
					const target = element.querySelector(selector);
					const text = (target && target.textContent || '').trim();
					if (text) return text;
				}}
				return '';
			}};
			const idFrom = (element) => {{
				for (const node of [element, element.querySelector('[data-encrypt-job-id], [data-encryptjobid], [data-job-id], a[href*="job"]')].filter(Boolean)) {{
					const encrypted = node.getAttribute('data-encrypt-job-id') || node.getAttribute('data-encryptjobid') || '';
					const plain = node.getAttribute('data-job-id') || '';
					const href = node.getAttribute('href') || '';
					try {{
						const url = new URL(href, location.href);
						const encryptedFromUrl = url.searchParams.get('encryptJobId') || url.searchParams.get('encrypt_job_id') || '';
						const plainFromUrl = url.searchParams.get('jobId') || url.searchParams.get('job_id') || '';
						if (encryptedFromUrl || plainFromUrl) return {{ encrypted: encryptedFromUrl, plain: plainFromUrl }};
					}} catch (_) {{}}
					if (encrypted || plain) return {{ encrypted, plain }};
				}}
				return {{ encrypted: '', plain: '' }};
			}};
			return cards.map(card => {{
				const ids = idFrom(card);
				const title = textFrom(card, titleSelectors) || (card.getAttribute('data-job-name') || '').trim();
				const infoLabels = Array.from(card.querySelectorAll('.info-labels .divider-label-text'))
					.map(node => (node.textContent || '').trim()).filter(Boolean);
				const pickInfo = pattern => infoLabels.find(value => pattern.test(value)) || '';
				const keywords = Array.from(card.querySelectorAll('.tag, .job-tag, [class*="keyword"], [class*="skill"]'))
					.map(node => (node.textContent || '').trim()).filter(Boolean).join('、');
				return {{
					encryptJobId: ids.encrypted,
					jobId: ids.plain,
					jobName: title,
					status: textFrom(card, statusSelectors) || 'online',
					city: textFrom(card, ['.city', '.job-city', '.job-area', '[class*="city"]', '[class*="location"]']) || infoLabels[0] || '',
					salary_range: textFrom(card, ['.salary', '.job-salary', '[class*="salary"]']) || pickInfo(/元\\s*[/]\\s*(?:天|月)|万\\s*[/]\\s*月|K$/i),
					education_requirement: textFrom(card, ['.degree', '.education', '[class*="degree"]', '[class*="education"]']) || pickInfo(/本科|大专|硕士|博士|学历/),
					internship_requirement: pickInfo(/\\d+\\s*个月/),
					work_days: pickInfo(/\\d+\\s*天\\s*[/]\\s*周/),
					description: textFrom(card, ['.job-description', '.job-desc', '.description', '[class*="description"]']),
					keywords,
				}};
			}}).filter(item => item.jobName).slice(0, 50);
		}})()
		"""
		try:
			raw = self._eval(js)
			if isinstance(raw, list):
				for raw_index, item in enumerate(raw):
					if isinstance(item, dict):
						name = str(item.get("jobName") or "").strip()
						if name:
							job = {
								"encryptJobId": str(item.get("encryptJobId") or "").strip(),
								"jobId": str(item.get("jobId") or "").strip(),
								"jobName": name,
								"status": str(item.get("status") or "online").strip() or "online",
							}
							# 职位卡片通常只显示摘要；缺字段时进入详情面板补读，避免
							# 把“卡片没有显示”误判成“BOSS 没有配置”。详情读取只点击
							# 查看/编辑入口并读取 DOM，不提交任何表单。
							basic_fields = (
								"city",
								"salary_range",
								"education_requirement",
								"description",
								"keywords",
								"experience_requirement",
								"internship_requirement",
								"work_days",
								"work_address",
							)
							missing_detail = any(
								not str(item.get(field_name) or "").strip() for field_name in basic_fields
							)
							if missing_detail:
								detail = self._read_job_detail_panel(
									card_index=raw_index,
									card_selectors=card_selectors,
									title_selectors=title_selectors,
								)
								if isinstance(detail, dict):
									for field_name in basic_fields:
										if (
											not str(item.get(field_name) or "").strip()
											and str(detail.get(field_name) or "").strip()
										):
											item[field_name] = str(detail[field_name]).strip()
									# 编辑详情页返回沟通页而不是职位列表页；恢复列表后，
									# 下一张卡片才能继续按同一套 iframe 选择器读取。
									self.navigate_to(JOB_PAGE)
									self.wait_loaded()
									self.human_delay(0.5, 1.0)
							# 详情字段只做白名单透传。RPA 未读到时不写空值，避免覆盖
							# 已有的 BOSS 快照或把旧页面的空选择器当作真实字段。
							for field_name in basic_fields:
								value = str(item.get(field_name) or "").strip()
								if value:
									job[field_name] = value
							jobs.append(job)
		except Exception:
			pass
		if not jobs:
			jobs = self._list_jobs_from_chat_job_selector()
		return {"code": 0, "zpData": {"list": jobs}}

	def _read_job_detail_panel(
		self,
		*,
		card_index: int,
		card_selectors: str,
		title_selectors: str,
	) -> dict[str, str]:
		"""点击一个职位卡片并读取 BOSS 详情面板中的硬条件。

		BOSS 职位卡片和编辑面板由不同的前端组件渲染，卡片摘要并不包含
		薪资单位、实习周期、工作日和完整职位描述。这里采用浏览器 DOM 的
		真实点击流程，等待面板渲染后按标签上下文提取控件值；关闭面板时只
		触发取消/关闭，不会保存任何内容。读取失败返回空字典，让上层保留
		卡片已读到的字段并继续同步其他岗位。
		"""
		# 第一次只在列表 iframe 中点击“编辑”。点击会让顶层路由重新导航，
		# 因此不能在同一个 Runtime.evaluate 中等待和读取，否则 CDP 会收到
		# “Inspected target navigated”并丢失详情结果。
		click_js = f"""
		(() => {{
			// job-detail-rpa-click：仅点击列表 iframe 中的详情入口。
			const cardSelectors = {card_selectors};
			const visible = element => Boolean(element && element.offsetParent !== null);
			const roots = [document];
			for (const frame of document.querySelectorAll('iframe')) {{
				try {{ if (frame.contentDocument) roots.push(frame.contentDocument); }} catch (_) {{}}
			}}
			const cards = [];
			const seen = new Set();
			for (const root of roots) for (const selector of cardSelectors) for (const element of root.querySelectorAll(selector)) {{
				if (visible(element) && !seen.has(element)) {{ seen.add(element); cards.push(element); }}
			}}
			const card = cards[{card_index}];
			if (!card) return false;
			const textOf = element => (element?.textContent || '').replace(/\\s+/g, ' ').trim();
			const opener = Array.from(card.querySelectorAll('button,a,[role="button"],.operate-btn'))
				.find(element => /编辑|修改|详情|查看/.test(textOf(element))) || card;
			opener.scrollIntoView({{block: 'center'}});
			opener.click();
			return true;
		}})()
		"""
		read_js = r"""
		(() => {
			// job-detail-rpa-read：在新页面上下文读取职位详情字段。
			const visible = element => Boolean(element && element.offsetParent !== null);
			const textOf = element => (element?.textContent || '').replace(/\s+/g, ' ').trim();
			const documents = [{root: document, isEditFrame: false}];
			for (const frame of document.querySelectorAll('iframe')) {
				try { if (frame.contentDocument) documents.push({root: frame.contentDocument, isEditFrame: /\/web\/frame\/job\/edit/.test(frame.src)}); } catch (_) {}
			}
			const detailEntry = documents.find(item => item.isEditFrame && /职位基本信息/.test(textOf(item.root.body)))
				|| documents.find(item => /职位基本信息/.test(textOf(item.root.body)) && /职位要求/.test(textOf(item.root.body)));
			const detailDocument = detailEntry && detailEntry.root;
			if (!detailDocument) return {};
			const panel = detailDocument.body;
			// 编辑 iframe 的外壳会先出现，表单字段随后才异步写入。若在这个
			// 时间窗读取，通用选择器会拾取“选择经验”“招聘规范”等占位内容。
			// 以职位描述这一核心控件作为就绪信号，不完整时交给 Python 轮询。
			const descriptionField = panel.querySelector('textarea');
			if (!descriptionField || !String(descriptionField.value || '').trim()) return {};
			const controls = Array.from(panel.querySelectorAll('input,textarea,select,[contenteditable="true"],[role="combobox"],bzl-select'))
				.filter(visible).map(element => {
					const enclosing = element.closest('label,[class*="form-item"],[class*="field"],[class*="item"],bzl-form-item');
					const label = textOf(enclosing || element.parentElement || element);
					const value = element.tagName === 'SELECT'
						? Array.from(element.selectedOptions || []).map(option => textOf(option)).join('、') || String(element.value || '')
						: String(element.value ?? textOf(element)).trim();
					return {label, value, context: textOf(enclosing || element.parentElement || element)};
				}).filter(item => item.value || item.label);
			const panelText = textOf(panel);
			const lines = (panel.innerText || '').split(/\n+/).map(line => line.trim()).filter(Boolean);
			const linePick = patterns => {
				for (let index = 0; index < lines.length; index += 1) {
					if (!patterns.some(pattern => pattern.test(lines[index]))) continue;
					const inline = lines[index].replace(/^[^：:]*[：:]/, '').trim();
					if (inline && !patterns.some(pattern => pattern.test(inline))) return inline;
					for (let next = index + 1; next < Math.min(lines.length, index + 4); next += 1) {
						if (lines[next] && !/^(经验|学历|薪资|职位关键词|岗位描述|工作地点|实习要求|工作日)/.test(lines[next])) return lines[next];
					}
				}
				return '';
			};
			const pick = patterns => {
				const match = controls.find(item => patterns.some(pattern => pattern.test(item.label + ' ' + item.context)) && item.value);
				return match ? match.value : linePick(patterns);
			};
			const result = {};
			result.city = pick([/工作地点/, /工作城市/, /城市/]);
			result.work_address = pick([/工作地点/, /详细地址/, /办公地址/]) || result.city;
			result.education_requirement = pick([/学历/, /教育背景/]);
			result.experience_requirement = pick([/经验/, /工作年限/, /经验要求/]);
			result.internship_requirement = pick([/实习要求/, /实习周期/, /实习时长/]);
			result.work_days = pick([/出勤/, /工作日/, /每周/]);
			result.description = pick([/岗位描述/, /职位描述/, /工作内容/, /岗位职责/]);
			result.keywords = pick([/职位关键词/, /岗位关键词/, /关键词/, /技能/]);
			const infoLabels = Array.from(panel.querySelectorAll('.info-labels .divider-label-text')).map(textOf).filter(Boolean);
			if (!result.city && infoLabels.length) result.city = infoLabels[0];
			const salaryMatch = panelText.match(/\d+(?:\.\d+)?\s*(?:-|~|至)\s*\d+(?:\.\d+)?\s*(?:元\s*\/\s*天|元\s*\/\s*月|万\s*\/\s*月|K|k)?/);
			if (salaryMatch) result.salary_range = salaryMatch[0].replace(/\s+/g, '');
			if (!result.description) {
				const textarea = panel.querySelector('textarea');
				if (textarea && String(textarea.value || '').trim()) result.description = String(textarea.value).trim();
			}
			if (!result.keywords) {
				const tags = Array.from(panel.querySelectorAll('[class*="tag"],[class*="keyword"]')).map(textOf).filter(Boolean);
				result.keywords = Array.from(new Set(tags)).join('、');
			}
			// 编辑器中的表单控件是事实源。优先使用稳定的业务容器，避免
			// 通用 [class*="item"] 选择器把隐藏配置项或规范提示误认成字段。
			if (String(descriptionField.value || '').trim()) {
				result.description = String(descriptionField.value).trim();
			}
			const addressInput = panel.querySelector('input[placeholder*="工作地点"]');
			if (addressInput && String(addressInput.value || '').trim()) {
				result.work_address = String(addressInput.value).trim();
			}
			const cleanDropdownChoice = value => textOf(value).split(/无匹配数据|加载中/)[0].trim();
			const experienceControls = Array.from(panel.querySelectorAll('.job-experience-row .ui-select-selection'))
				.map(element => cleanDropdownChoice(element.parentElement || element)).filter(Boolean);
			if (experienceControls[0] && !/^(选择|请选择|加载中|无匹配数据)/.test(experienceControls[0])) {
				result.experience_requirement = experienceControls[0];
			}
			if (experienceControls[1] && !/^(选择|请选择|加载中|无匹配数据)/.test(experienceControls[1])) {
				result.education_requirement = experienceControls[1];
			}
			const salaryValues = Array.from(panel.querySelectorAll('.salary-select .ui-select-selection'))
				.map(element => textOf(element.parentElement || element)).filter(value => /^\d+(?:\.\d+)?$/.test(value));
			if (salaryValues.length >= 2) {
				const unit = (panelText.match(/元\s*\/\s*(?:天|月)|万\s*\/\s*月|K/i) || [''])[0].replace(/\s+/g, '');
				result.salary_range = `${salaryValues[0]}-${salaryValues[1]}${unit}`;
			}
			const requirementValues = Array.from(panel.querySelectorAll('.job-requirements-row .ui-select-selection'))
				.map(element => textOf(element.parentElement || element)).filter(Boolean);
			for (const value of requirementValues) {
				if (/\d+\s*个月/.test(value)) result.internship_requirement = value;
				if (/\d+\s*天/.test(value)) result.work_days = value;
			}
			if (/招聘行为管理规范|请填写|可选标题/.test(result.description || '')) delete result.description;
			if (/招聘行为管理规范|请填写|可选标题/.test(result.keywords || '')) delete result.keywords;
			if (/^(选择|请选择|加载中|无匹配数据)/.test(result.experience_requirement || '')) delete result.experience_requirement;
			if (/招聘行为管理规范|请填写/.test(result.work_address || '')) delete result.work_address;
			// 此脚本只读取字段，绝不触发任何页面导航或业务按钮。特别是编辑
			// 页的“关闭”属于关闭职位的业务操作，不能作为返回列表的手段。
			// Python 调用方会在收集完字段后显式导航回职位列表。
			return result;
		})()
		"""
		try:
			clicked = self._eval(click_js)
			if clicked is not True:
				return {}
			value: Any = {}
			merged_value: dict[str, Any] = {}
			for _ in range(24):
				time.sleep(0.25)
				try:
					current_value = self._eval(read_js)
					if isinstance(current_value, dict):
						value = current_value
						merged_value.update(current_value)
						# 详情字段会分批挂载：先有描述，再有地址和下拉值。合并
						# 多轮结果，避免第一次非空响应把后续字段截断。
						if merged_value.get("description") and (merged_value.get("work_address") or _ >= 8):
							break
				except Exception:
					# 顶层刚导航时 CDP 可能短暂报告 target context 已替换，
					# 下一轮读取会落到新 iframe 上。
					continue
			sanitized = merged_value or (value if isinstance(value, dict) else {})
			# 详情读取完成后统一回到职位管理页。该 URL 导航不会提交表单，
			# 也不会改变 BOSS 上的职位状态。
			self.navigate_to(JOB_PAGE)
			self.wait_loaded()
			return {key: str(item).strip() for key, item in sanitized.items() if str(item).strip()}
		except Exception as exc:
			self._log(f"[RPA] job detail panel read failed: {exc}")
			return {}

	def _list_jobs_from_chat_job_selector(self) -> list[dict[str, Any]]:
		"""从聊天页“全部职位”筛选器读取当前可用的在线职位。

		职位管理微前端停在加载态时，不能退回 HTTP 接口，也不能使用候选人会话
		中的历史岗位标签。聊天页筛选器由当前招聘账号的可切换职位驱动，是唯一
		可接受的 RPA 降级来源；它没有公开职位 ID，故明确标记来源，交给上层以
		受限规则生成本地镜像关联键。
		"""
		self.navigate_to(CHAT_PAGE)
		self.wait_loaded()
		self.human_delay(0.5, 1.0)
		try:
			self._eval("""
			(() => {
				const trigger = document.querySelector('.chat-select-job');
				if (!trigger || trigger.offsetParent === null) return false;
				trigger.scrollIntoView({block: 'center'});
				trigger.click();
				return true;
			})()
			""")
		except Exception:
			return []

		# 下拉菜单在点击后由前端异步挂载；仅等待受控筛选器，不能读取候选人卡片
		# 来“补齐”结果，以免历史会话将已关闭岗位重新带回工作台。
		deadline = time.monotonic() + 5.0
		raw_names: list[Any] = []
		while time.monotonic() < deadline:
			raw_names = self._visible_chat_job_selector_names()
			if raw_names:
				break
			time.sleep(0.25)

		jobs: list[dict[str, Any]] = []
		seen_names: set[str] = set()
		for raw_name in raw_names:
			name, status = self._chat_job_selector_job(raw_name)
			name_key = name.casefold()
			if not name or name_key in seen_names:
				continue
			seen_names.add(name_key)
			jobs.append(
				{
					"encryptJobId": "",
					"jobId": "",
					"jobName": name,
					"status": status,
					"rpaSource": "chat_job_selector",
				}
			)
		return jobs

	def select_conversation_job(self, job_name: str) -> dict[str, Any]:
		"""将 BOSS 沟通列表切换至指定职位，并验证顶部筛选器回显。

		本地自动化队列按职位隔离；若 BOSS 页面仍停留在“全部职位”，全量快照会把
		其它岗位的会话混入当前周期，既造成错误沟通也会显著拖慢轮询。因此选择过程
		要求精确匹配一个可见项，点击后再读取当前回显，任一步失败都返回受控错误，
		调用方不得继续读取未筛选列表。
		"""
		target_name = job_name.strip()
		if not target_name:
			return {"code": -1, "message": "自动化岗位名称为空，无法筛选 BOSS 沟通列表"}
		self._ensure_chat_page()
		self._ensure_recruiter_page_ready()
		# 后台每轮都会先确认岗位筛选条件。若当前页面已经是目标岗位，必须
		# 直接复用现有列表；重复打开菜单并点击同一职位会让 BOSS 重载沟通列表，
		# 表现为页面自行刷新，也会打断正在读取的候选人会话。
		current_name, _current_status = self._chat_job_selector_job(self._selected_chat_job_selector_text())
		if current_name.casefold() == target_name.casefold():
			return {"code": 0, "zpData": {"selectedJobName": target_name}}
		if not self._open_chat_job_selector():
			return {"code": -1, "message": f"无法打开 BOSS 沟通列表的职位筛选器：{target_name}"}
		self.human_delay(0.15, 0.3)
		raw_names = self._visible_chat_job_selector_names()
		matches = [
			str(raw_name).strip()
			for raw_name in raw_names
			if self._chat_job_selector_job(raw_name)[0].casefold() == target_name.casefold()
		]
		if len(matches) != 1:
			return {
				"code": -1,
				"message": f"BOSS 沟通列表中未找到唯一的职位：{target_name}",
			}
		if not self._click_chat_job_selector_option(matches[0]):
			return {"code": -1, "message": f"无法选择 BOSS 沟通列表职位：{target_name}"}
		self.human_delay(0.2, 0.4)
		selected_name, _selected_status = self._chat_job_selector_job(self._selected_chat_job_selector_text())
		if selected_name.casefold() != target_name.casefold():
			return {
				"code": -1,
				"message": f"BOSS 沟通列表职位切换未生效，当前为：{selected_name or '全部职位'}",
			}
		return {"code": 0, "zpData": {"selectedJobName": target_name}}

	def select_all_conversation_jobs(self) -> dict[str, Any]:
		"""切回 BOSS 沟通列表的“全部职位”，并验证顶部筛选器回显。

		手动查看全部岗位时不能沿用上一次单岗自动化留下的浏览器状态，否则
		页面虽然显示“全部岗位”，实际候选人仍只来自 Java 等单个职位。
		"""
		all_jobs_option = "全部职位"
		self._ensure_chat_page()
		self._ensure_recruiter_page_ready()
		if self._selected_chat_job_selector_text() == all_jobs_option:
			return {"code": 0, "zpData": {"selectedScope": "all"}}
		if not self._open_chat_job_selector():
			return {"code": -1, "message": "无法打开 BOSS 沟通列表的职位筛选器：全部职位"}
		self.human_delay(0.15, 0.3)
		matches = [str(raw_name).strip() for raw_name in self._visible_chat_job_selector_names() if str(raw_name).strip() == all_jobs_option]
		if len(matches) != 1:
			return {"code": -1, "message": "BOSS 沟通列表中未找到唯一的全部职位筛选项"}
		if not self._click_chat_job_selector_option(all_jobs_option):
			return {"code": -1, "message": "无法选择 BOSS 沟通列表全部职位"}
		self.human_delay(0.2, 0.4)
		if self._selected_chat_job_selector_text() != all_jobs_option:
			return {"code": -1, "message": "BOSS 沟通列表未切换到全部职位"}
		return {"code": 0, "zpData": {"selectedScope": "all"}}

	def _open_chat_job_selector(self) -> bool:
		"""打开聊天页顶部职位筛选器，只操作筛选控件，不触碰候选人会话。"""
		# 同步失败的一个高频原因是上一次读取留下了已展开的菜单。再次点击
		# 触发器会把菜单关闭，后续自然读不到职位；先读可见选项即可判断当前
		# 状态，避免把一个幂等的“打开”动作实现成切换动作。
		if self._visible_chat_job_selector_names():
			return True
		try:
			opened = self._eval("""
			(() => {
				const trigger = document.querySelector('.chat-select-job');
				if (!trigger || trigger.offsetParent === null) return false;
				trigger.scrollIntoView({block: 'center'});
				trigger.click();
				return true;
			})()
			""")
		except Exception:
			return False
		return opened is True

	def _click_chat_job_selector_option(self, option_text: str) -> bool:
		"""点击可见筛选项的精确文本，避免名称相近职位被模糊匹配。"""
		try:
			clicked = self._eval(f"""
			(() => {{
				const targetOptionText = {json.dumps(option_text, ensure_ascii=False)};
				const option = Array.from(document.querySelectorAll('.chat-top-job .ui-dropmenu-list li, .chat-top-job .ui-dropmenu-list [role=\"option\"], .ui-dropmenu-list li, .ui-dropmenu-list [role=\"option\"]'))
					.find(element => element.offsetParent !== null && ((element.getAttribute('title') || element.textContent || '').trim() === targetOptionText));
				if (!option) return false;
				option.scrollIntoView({{block: 'center'}});
				option.click();
				return true;
			}})()
			""")
		except Exception:
			return False
		return clicked is True

	def _selected_chat_job_selector_text(self) -> str:
		"""读取顶部职位控件当前回显，供切换后强校验使用。"""
		try:
			selected = self._eval("""
			(() => {
				const trigger = document.querySelector('.chat-select-job');
				return trigger ? (trigger.getAttribute('title') || trigger.textContent || '').trim() : '';
			})()
			""")
		except Exception:
			return ""
		return str(selected or "").strip()

	def _visible_chat_job_selector_names(self) -> list[Any]:
		"""读取已展开职位筛选器的可见选项，不负责状态筛选或名称解析。"""
		try:
			raw_names = self._eval("""
			Array.from(document.querySelectorAll('.chat-top-job .ui-dropmenu-list li, .chat-top-job .ui-dropmenu-list [role="option"], .ui-dropmenu-list li, .ui-dropmenu-list [role="option"]'))
				.filter(element => element.offsetParent !== null)
				.map(element => (element.getAttribute('title') || element.textContent || '').trim())
				.filter(Boolean)
			""")
		except Exception:
			return []
		if not isinstance(raw_names, list):
			return []
		return raw_names

	@staticmethod
	def _chat_job_selector_job(raw_name: Any) -> tuple[str, str]:
		"""从职位筛选器文本提取名称和状态，保留 BOSS 明确关闭的岗位。"""
		text = str(raw_name).strip()
		if not text or text == "全部职位":
			return "", ""
		# 页面文本使用“职位名 _ 城市 薪资”格式。仅按带空白的分隔符切分，避免
		# 意外破坏职位名中原本存在的下划线。
		name = text.split(" _ ", 1)[0].strip()
		name_key = name.casefold()
		if any(marker in name_key for marker in ("关闭", "已下线", "暂停", "offline", "expired")):
			# 关闭状态来自 BOSS 已渲染的职位筛选器，而非候选人历史会话标签。
			# 因此保留它能让本地历史评估继续按原岗位查看，但上层推荐读取仍会
			# 依据状态排除该岗位，避免将关闭职位用于新的招聘动作。
			for suffix in ("（关闭）", "(关闭)", "（已下线）", "(已下线)", "（暂停）", "(暂停)"):
				if name.endswith(suffix):
					name = name[: -len(suffix)].strip()
					break
			return name, "closed"
		return name, "online"

	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		text = self._page_text()
		return {"code": 0, "zpData": {"messages": [{"text": text[:5000]}]}}

	def _visible_conversation_profile_snapshot(self) -> dict[str, str] | None:
		"""读取当前已打开聊天的最小职业资料，不触发第二次会话定位。

		批量消息读取已经为每位目标候选人点击了正确的聊天卡片。把顶部资料在此时
		一并读取，能让后续硬筛直接复用同一页面状态，避免再次滚动整张虚拟列表。
		返回值严格限制在工作、学历、沟通职位和期望文本四个展示区，消息正文不在
		这个方法的职责范围内。
		"""
		snapshot = self._eval("""
		(() => {
			const selectors = [
				'.base-info-single-top', '.base-info-single-top-detail',
				'.chat-top-wrap', '[class*="geek-info"]', '[class*="resume-info"]'
			];
			const containers = selectors.flatMap(selector => Array.from(document.querySelectorAll(selector)))
				.filter(element => element.offsetParent !== null)
				.filter(element => /在线简历|附件简历|沟通职位|最近关注/.test(element.textContent || ''));
			const container = containers[0];
			if (!container) return null;
			const text = (container.textContent || '').replace(/\\s+/g, ' ').trim();
			const lines = Array.from(container.querySelectorAll('div, span, li'))
				.filter(element => element.offsetParent !== null)
				.map(element => (element.textContent || '').replace(/\\s+/g, ' ').trim())
				.filter(Boolean);
			return {
				work_text: lines.find(value => /20\\d{2}.*(?:-|至今|现在).*20\\d{2}|20\\d{2}.*至今/.test(value)) || '',
				education_text: lines.find(value => /本科|大专|硕士|博士|高中/.test(value)) || text,
				communication_job: (text.match(/沟通职位[：:]?\\s*([^ ]+)/) || [])[1] || '',
				expectation_text: (text.match(/最近关注[：:]?\\s*([^ ]+\\s+[^ ]+\\s+[^ ]+)/) || [])[1] || '',
			};
		})()
		""")
		if not isinstance(snapshot, dict):
			return None
		return {
			key: str(snapshot.get(key) or "")[:500]
			for key in ("work_text", "education_text", "communication_job", "expectation_text")
		}

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		"""逐个打开指定会话，读取最后候选人回复及其前一条招聘方问题。

		BOSS 左侧会话卡片在部分状态下没有消息预览；把预览缺失解释为“没有消息”会
		让 AI 对话永远不触发。候选人的“是的”一类短回答只有和前一条招聘方问题
		配对后才有语义，因此这里按 DOM 消息顺序读取角色白名单，而不把两个角色的
		气泡分别汇总。只返回最后候选人回复和它前面的招聘方文本，不上传完整历史，
		避免跨候选人污染和 Token 随会话长度增长。
		"""
		self._ensure_chat_page()
		valid_friend_ids = []
		for raw_friend_id in friend_ids:
			try:
				friend_id = int(raw_friend_id)
			except (TypeError, ValueError):
				continue
			if friend_id > 0:
				valid_friend_ids.append(friend_id)
		# 单人读取继续走原路径，避免为一次会话付出全列表扫描成本；批量读取
		# 才启用共享定位，这是自动化一轮 20 人时的关键性能边界。
		batch_positions = self._find_cards_by_friend_ids(valid_friend_ids) if len(valid_friend_ids) > 1 else {}
		items: list[dict[str, Any]] = []
		for raw_friend_id in friend_ids:
			try:
				friend_id = int(raw_friend_id)
			except (TypeError, ValueError):
				continue
			if friend_id <= 0:
				continue
			position = batch_positions.get(friend_id)
			target_idx: int | None = None
			if position is not None:
				target_top, target_idx = position
				self._eval(f"""
				(() => {{
					const list = document.querySelector('.user-list');
					if (!list) return false;
					list.scrollTop = {target_top};
					list.dispatchEvent(new Event('scroll', {{bubbles: true}}));
					return true;
				}})()
				""")
				self.human_delay(0.15, 0.3)
			else:
				target_idx = self._find_card_by_friend_id(friend_id)
				if target_idx is None:
					continue
			clicked = self._eval(f"""
			(() => {{
				const cards = document.querySelectorAll('.geek-item-wrap');
				if (cards.length <= {target_idx}) return false;
				const inner = cards[{target_idx}].querySelector('.geek-item, [data-id]');
				if (!inner || (inner.getAttribute('data-id') || '').split('-')[0] !== {json.dumps(str(friend_id))}) return false;
				cards[{target_idx}].scrollIntoView({{block: 'center'}});
				cards[{target_idx}].click();
				return true;
			}})()
			""")
			if clicked is not True:
				continue
			self.human_delay(0.5, 0.9)
			turns = self._eval("""
			(() => Array.from(document.querySelectorAll('.chat-message-list .message-item'))
				.map(item => {
					const candidate = (item.querySelector('.item-friend .text-content')?.textContent || '').trim();
					if (candidate) return {role: 'candidate', text: candidate};
					const recruiter = (item.querySelector('.item-myself .text-content')?.textContent || '').trim();
					if (recruiter) return {role: 'recruiter', text: recruiter};
					return null;
				})
				.filter(Boolean))()
			""")
			if not isinstance(turns, list):
				continue
			parsed_turns = [
				{"role": str(turn.get("role") or ""), "text": str(turn.get("text") or "").strip()}
				for turn in turns
				if isinstance(turn, dict) and str(turn.get("text") or "").strip()
			]
			last_candidate_index = next(
				(index for index in range(len(parsed_turns) - 1, -1, -1) if parsed_turns[index]["role"] == "candidate"),
				None,
			)
			# 只有会话最后一条文本气泡属于候选人，才代表对方刚回复了当前问题。
			# 若最后一条是招聘方问题，前面候选人的历史基础回答不能再次进入
			# 编排器，否则状态缺失或跨轮切换时会被当作专业题答案而重复提问。
			if last_candidate_index is None or last_candidate_index != len(parsed_turns) - 1:
				continue
			# 当前目标聊天已经在页面上打开。同步采集顶部资料，避免硬筛阶段再为
			# 同一候选人重新滚动虚拟列表并点击聊天卡片。
			profile_snapshot = self._visible_conversation_profile_snapshot()
			# 候选人常把“是的”“可以实习六个月”等同一题答案拆成多条发送。
			# 合并最后一条招聘方消息后的连续候选人文本，避免 AI 只看尾句而重复提问。
			first_candidate_index = last_candidate_index
			while first_candidate_index > 0 and parsed_turns[first_candidate_index - 1]["role"] == "candidate":
				first_candidate_index -= 1
			previous_recruiter_text = next(
				(
					parsed_turns[index]["text"]
					for index in range(first_candidate_index - 1, -1, -1)
					if parsed_turns[index]["role"] == "recruiter"
				),
				"",
			)
			items.append({
				"friendId": friend_id,
				"text": "\n".join(
					parsed_turns[index]["text"]
					for index in range(first_candidate_index, last_candidate_index + 1)
				),
				"previousRecruiterText": previous_recruiter_text,
				"profile": profile_snapshot or {},
			})
		return {"code": 0, "zpData": {"lastMessageList": items}}

	def read_conversation_profile(self, friend_id: int) -> dict[str, Any]:
		"""读取指定会话顶部资料栏，作为 AI 前的硬筛输入。

		这里刻意不读取 ``document.body`` 或聊天消息列表：红框资料和聊天正文是两个
		不同的数据边界，前者可在本地做确定性筛选，后者只有硬筛通过后才允许交给 AI。
		页面选择器采用可见元素和文本白名单，页面结构变化时返回失败而不是猜测。
		"""
		self._ensure_chat_page()
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}
		clicked = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			if (cards.length <= {target_idx}) return false;
			cards[{target_idx}].scrollIntoView({{block: 'center'}});
			cards[{target_idx}].click();
			return true;
		}})()
		""")
		if clicked is not True:
			return {"code": -1, "message": f"无法打开 friend_id={friend_id} 的会话"}
		self.human_delay(0.8, 1.2)
		snapshot = self._visible_conversation_profile_snapshot()
		if snapshot is None:
			return {"code": -1, "message": "未读取到候选人顶部资料栏"}
		profile = ConversationProfile.from_display_fields(
			work_text=str(snapshot.get("work_text") or ""),
			education_text=str(snapshot.get("education_text") or ""),
			communication_job=str(snapshot.get("communication_job") or ""),
			expectation_text=str(snapshot.get("expectation_text") or ""),
		)
		if not any(profile.to_dict().values()):
			return {"code": -1, "message": "候选人顶部资料栏字段为空，页面选择器可能已变化"}
		return {"code": 0, "zpData": {"profile": profile.to_dict()}}

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		"""通过 CDP 在 BOSS 聊天页发送消息。

		步骤：导航到沟通页 → 按序号点开对话 → CDP Input.insertText 输入 →
		CDP Input.dispatchKeyEvent 按 Enter 发送。

		与之前的 JS DOM 操作不同，CDP 输入协议产生的是真实的浏览器级
		键盘事件（beforeInput → input → keyDown → keyUp），React 无法忽略。
		"""
		# AI 回复进入 CDP Input.insertText 前必须先清理孤立代理字符，否则
		# WebSocket JSON 编码会在发送阶段抛出 UnicodeEncodeError。
		content = sanitize_unicode_text(content)
		self.navigate_to(CHAT_PAGE)
		self.wait_loaded()
		self.human_delay(1.0, 2.0)

		# 1. 先用真实 friend_id 定位会话卡片。定位失败时不能把 None 拼入
		#    JavaScript 索引：那会掩盖 RPA 元素变化，并可能在错误会话上继续
		#    输入消息。受控失败会让上层保持未发送状态，避免误判为已沟通。
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			self._log(f"[RPA] send_message: friend_id {friend_id} not found")
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}
		clicked = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll(
				'.geek-item-wrap, [class*="chat-item"], [class*="conversation-item"], li[data-fid]'
			);
			if (cards.length > {target_idx}) {{
				const card = cards[{target_idx}];
				card.scrollIntoView({{block: 'center'}});
				card.click();
				return true;
			}}
			return false;
		}})()
		""")

		if not clicked:
			self._log(f"[RPA] send_message: friend_id {friend_id} not found at any index")
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}

		# 2. 等聊天面板渲染完成，找到并聚焦输入框
		self.human_delay(2.0, 3.0)
		focused = self._eval("""
		(() => {
			for (let attempt = 0; attempt < 40; attempt++) {
				let input = document.querySelector('textarea');
				if (!input) input = document.querySelector('[contenteditable="true"]');
				if (!input) input = document.querySelector('[role="textbox"]');
				if (!input) input = document.querySelector('[class*="chat"] textarea');
				if (!input) input = document.querySelector('[placeholder*="消息"]');
				if (!input) input = document.querySelector('[placeholder*="回复"]');
				if (input) {
					input.focus();
					input.click();
					return true;
				}
				const end = Date.now() + 200;
				while (Date.now() < end) {}
			}
			return false;
		})()
		""")

		if not focused:
			self._log("[RPA] send_message: input not found")
			return {"code": -1, "message": "未找到聊天输入框"}

		# 3. 不能覆盖人工草稿；若是上一次自动发送失败留下的同文本草稿，则直接
		# 复用它并点击发送，避免再次插入后形成重复内容。
		draft = self._chat_editor_draft()
		if draft and self._normalise_chat_text(draft) != self._normalise_chat_text(content):
			return {"code": -1, "message": "聊天输入框存在未发送草稿，已停止自动发送"}
		if not draft:
			try:
				self._cdp_send("Input.insertText", {"text": content})
			except Exception as exc:
				self._log(f"[RPA] send_message: insertText failed: {exc}")
				return {"code": -1, "message": f"文本输入失败: {exc}"}

		self.human_delay(0.3, 0.6)
		# 4. 当前 BOSS 编辑器不会将 Enter 视为提交；必须点击可见的“发送”控件。
		if not self._click_chat_submit():
			return {"code": -1, "message": "未找到可用的聊天发送按钮"}
		if not self._wait_for_recruiter_message(content):
			self._log(f"[RPA] send_message: no delivery confirmation for friend_id={friend_id}")
			return {"code": -1, "message": "消息未在 BOSS 会话中确认送达"}

		self._log(f"[RPA] send_message: confirmed for friend_id={friend_id}")
		return {"code": 0, "message": "ok"}

	@staticmethod
	def _normalise_chat_text(value: str) -> str:
		"""压缩 DOM 渲染空白后比较消息，避免换行和空格影响送达校验。"""
		return "".join(value.split())

	def _chat_editor_draft(self) -> str:
		"""读取唯一可见的 BOSS 编辑器草稿，用于保护人工尚未发送的内容。"""
		value = self._eval("""
		(() => {
			// 聊天页与招呼语设置页属于不同页面上下文，不能复用设置页注入的辅助函数。
			// 这里保持判断自包含，避免轮询时出现跨页面变量引用错误。
			const isChatElementVisible = element => {
				if (!element || element.getClientRects().length === 0) return false;
				const style = window.getComputedStyle(element);
				return style.display !== 'none' && style.visibility !== 'hidden';
			};
			const editor = Array.from(document.querySelectorAll('.boss-chat-editor-input[contenteditable="true"], [contenteditable="true"]'))
				.find(element => isChatElementVisible(element));
			return (editor?.textContent || '').trim();
		})()
		""")
		return value.strip() if isinstance(value, str) else ""

	def _click_chat_submit(self) -> bool:
		"""点击当前编辑器附近的可用发送控件，不依赖 Enter 的平台默认行为。"""
		clicked = self._eval("""
		(() => {
			const editor = Array.from(document.querySelectorAll('.boss-chat-editor-input[contenteditable="true"], [contenteditable="true"]'))
				.find(element => element.offsetParent !== null);
			if (!editor) return false;
			let container = editor.parentElement;
			for (let depth = 0; container && depth < 5; depth += 1, container = container.parentElement) {
				const submit = Array.from(container.querySelectorAll('.submit.active, .submit, [role="button"]'))
					.find(element => element.offsetParent !== null && (element.textContent || '').trim() === '发送' && !element.hasAttribute('disabled'));
				if (submit) {
					submit.click();
					return true;
				}
			}
			return false;
		})()
		""")
		return clicked is True

	def _wait_for_recruiter_message(self, content: str, timeout: float = 5.0) -> bool:
		"""等待 BOSS 将本次内容渲染为招聘方气泡，作为唯一的送达成功依据。"""
		expected = self._normalise_chat_text(content)
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			matched = self._eval(f"""
			(() => {{
				const expected = {json.dumps(expected, ensure_ascii=False)};
				return Array.from(document.querySelectorAll('.chat-message-list .message-item .item-myself .text-content'))
					.some(element => (element.textContent || '').replace(/\\s+/g, '') === expected);
			}})()
			""")
			if matched is True:
				return True
			time.sleep(0.2)
		return False

	def has_existing_resume_request(self, friend_id: int) -> bool:
		"""读取当前会话，判断招聘方是否已经索要过简历。

		这是恢复旧自动流程的防重兜底：早期版本未持久化状态时，历史索要话术
		只存在 BOSS 页面。检测只采用少量明确句式；宁可少匹配后走首次发送，也
		不把“附件简历”按钮等界面固定文字误认为一条真实沟通消息。
		"""
		self._ensure_chat_page()
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			self._log(f"[RPA] resume_request_history: friend_id {friend_id} not found")
			return False
		clicked = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			if (cards.length <= {target_idx}) return false;
			cards[{target_idx}].scrollIntoView({{block: 'center'}});
			cards[{target_idx}].click();
			return true;
		}})()
		""")
		if not clicked:
			return False
		self.human_delay(1.0, 1.5)
		matched = self._eval("""
		(() => {
			const panels = Array.from(document.querySelectorAll(
				'[class*="chat-content"], [class*="message-list"], [class*="chat-message"], [class*="dialogue"]'
			)).filter(el => el.offsetParent !== null);
			const source = panels.length
				? panels.map(el => el.textContent || '').join(' ')
				: (document.body ? document.body.textContent || '' : '');
			const text = source.replace(/\\s+/g, '');
			return /(方便|麻烦).{0,12}发.{0,10}简历|可以.{0,12}(看|发).{0,10}简历/.test(text);
		})()
		""")
		return matched is True

	def exchange_content(self, uid: int) -> dict[str, Any]:
		"""读取候选人的交换内容（附件简历下载地址等）。

		通过页面 JS fetch 调用 BOSS API，拿到附件简历的 HTTPS 下载链接。
		"""
		self._ensure_chat_page()
		result = self._eval(
			f"""
		(async () => {{
			try {{
				const resp = await fetch(
					'/wapi/zpboss/geek/exchangeContent?uid={uid}',
					{{ credentials: 'include' }}
				);
				const data = await resp.json();
				return JSON.stringify(data);
			}} catch(e) {{
				return JSON.stringify({{error: e.message}});
			}}
		}})()
		""",
			await_promise=True,
		)
		if isinstance(result, str):
			try:
				parsed = json.loads(result)
				if isinstance(parsed, dict):
					return {"code": 0, "zpData": parsed.get("zpData", parsed)}
			except json.JSONDecodeError:
				pass
		return {"code": 0, "zpData": {}}

	def download_attachment_via_ui(self, friend_id: int, save_dir: str | None = None) -> dict[str, Any]:
		"""通过 BOSS 官方 UI 下载附件简历 PDF。

		BOSS 聊天页顶部有两个按钮：「在线简历」和「附件简历」。
		附件简历按钮 class='btn resume-btn-file'，候选人分享附件后启用。
		点击后打开附件预览，预览界面有下载按钮。
		"""
		import glob
		import os

		if save_dir is None:
			save_dir = os.path.join(os.path.expanduser("~"), "Desktop", "简历")
		os.makedirs(save_dir, exist_ok=True)

		# 设置 Chrome 下载目录
		try:
			self._cdp_send(
				"Browser.setDownloadBehavior",
				{
					"behavior": "allowAndName",
					"downloadPath": save_dir,
					"eventsEnabled": True,
				},
			)
		except Exception:
			pass

		self._ensure_chat_page()
		self.human_delay(0.5, 1.0)

		# 1. 打开对话
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			return {"code": -1, "message": "未找到该候选人的会话"}

		self._eval("document.querySelectorAll('.geek-item-wrap')[%d].click();" % target_idx)
		self.human_delay(2.0, 3.0)

		# 2. 点击当前附件请求的「同意」。BOSS 会把同一请求渲染成消息卡片或
		#    底部固定确认栏，两者都必须由“附件简历”上下文约束，不能全页盲点
		#    任意同意按钮。这里故意只扫描一次：旧代码在页面 JS 内同步空转 10 秒，
		#    会阻塞 DOM 更新和后续沟通轮询，未找到时应交还给下一次正常轮询。
		agree_clicked = self._eval("""
		(function(){
			var labels = ['同意', '接收', '接受', '同意接收', '接收简历', '查看附件'];
			var isAvailable = function(el) {
				return el && el.offsetParent !== null && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
			};
			var hasAttachmentContext = function(el) {
				var node = el;
				for (var depth = 0; node && depth < 8; depth++, node = node.parentElement) {
					var text = node.textContent || '';
					if (/对方想发送附件简历给您|发送附件简历给您|附件简历|附件简历给您/.test(text)) return true;
				}
				return false;
			};
			var controls = document.querySelectorAll('button, a, [role=\"button\"], [class*=\"btn\"]');
			for (var index = 0; index < controls.length; index++) {
				var control = controls[index];
				var label = (control.textContent || '').trim();
				if (isAvailable(control) && labels.indexOf(label) >= 0 && hasAttachmentContext(control)) {
					control.click();
					return 'fixed-attachment-acceptance:' + label;
				}
			}
			return 'not-found';
		})()
		""")
		self._log("[RPA] download_attachment: agree button = " + str(agree_clicked))
		if agree_clicked and agree_clicked != "not-found":
			self.human_delay(2.0, 3.0)

		# 3. 同意后等待附件按钮真实解除禁用；固定睡眠不足以覆盖异步更新。
		file_btn_info = "no-button"
		for attempt in range(12 if agree_clicked and agree_clicked != "not-found" else 1):
			file_btn_info = self._eval("""
		(function(){
			var buttons = document.querySelectorAll('.resume-btn-file, [class*="resume-btn-file"]');
			var btn = null;
			for (var i = 0; i < buttons.length; i++) {
				if (buttons[i].offsetParent !== null) btn = buttons[i];
			}
			if (!btn) return 'no-button';
			var disabled = btn.classList.contains('disabled') || btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true';
			return JSON.stringify({disabled: disabled, text: (btn.textContent||'').trim()});
		})()
		""")
			self._log("[RPA] download_attachment: file button state = " + str(file_btn_info))
			try:
				if file_btn_info != "no-button" and not json.loads(str(file_btn_info)).get("disabled"):
					break
			except (json.JSONDecodeError, TypeError):
				pass
			if attempt < 11:
				self.human_delay(0.3, 0.5)

		if not file_btn_info or file_btn_info == "no-button":
			state = "acceptance_pending" if agree_clicked and agree_clicked != "not-found" else "not_shared"
			message = "已点击同意，等待 BOSS 展示附件简历按钮" if state == "acceptance_pending" else "候选人未分享附件简历（无附件简历按钮）"
			return {"code": 0, "message": message, "zpData": {"attachment_state": state}}

		try:
			info = json.loads(file_btn_info)
			if info.get("disabled"):
				state = "acceptance_pending" if agree_clicked and agree_clicked != "not-found" else "not_shared"
				message = "已点击同意，但附件简历按钮仍禁用" if state == "acceptance_pending" else "候选人未分享附件简历（按钮已禁用）"
				return {"code": 0, "message": message, "zpData": {"attachment_state": state}}
		except (json.JSONDecodeError, TypeError):
			pass

		# 4. 点击「附件简历」按钮
		self._eval("""
		(function(){
			var buttons = document.querySelectorAll('.resume-btn-file, [class*="resume-btn-file"]');
			for (var i = buttons.length - 1; i >= 0; i--) {
				if (buttons[i].offsetParent !== null) {
					buttons[i].click();
					return true;
				}
			}
			return false;
		})()
		""")
		self.human_delay(2.0, 3.0)

		# 5. 从 dialog 的 iframe 中提取 PDF 下载 URL，用 fetch 直接下载
		pdf_result = self._eval(
			"""
		(async function(){
			await new Promise(r => setTimeout(r, 2000));

			// BOSS 附件预览 dialog: class="resume-common-dialog"
			// 内含 iframe: src="/bzl-office/pdf-viewer-b?url=URL_ENCODED_PDF_PATH"
			var dialogs = document.querySelectorAll(
				'.resume-common-dialog, [class*=\"resume-common\"], .boss-dialog__wrapper, [class*=\"boss-dialog\"]'
			);
			var dialog = null;
			for (var d = 0; d < dialogs.length; d++) {
				if (dialogs[d].offsetParent !== null) dialog = dialogs[d];
			}
			if (!dialog) return JSON.stringify({method: 'none', url: ''});

			var pdfUrl = '';

			// 方法1：从 iframe src 提取 PDF URL
			var iframes = dialog.querySelectorAll('iframe');
			for (var i = 0; i < iframes.length; i++) {
				var src = iframes[i].getAttribute('src') || '';
				if (src.indexOf('pdf-viewer') >= 0 || src.indexOf('preview4boss') >= 0) {
					// src = "/bzl-office/pdf-viewer-b?url=%2Fwflow%2F...%2Fpreview4boss%2F..."
					var match = src.match(/url=([^&]+)/);
					if (match) {
						pdfUrl = decodeURIComponent(match[1]);
						if (!pdfUrl.startsWith('http')) {
							pdfUrl = window.location.origin + pdfUrl;
						}
						break;
					}
				}
			}

			// 方法2：找下载按钮并提取 URL
			if (!pdfUrl) {
				var btns = dialog.querySelectorAll('button, a, span, div');
				for (var j = 0; j < btns.length; j++) {
					var text = (btns[j].textContent || '').trim();
					if (text === '下载' && btns[j].offsetParent !== null) {
						btns[j].click();
							return JSON.stringify({method: 'click-download-btn', url: ''});
					}
				}
			}

			return JSON.stringify({method: pdfUrl ? 'iframe-url' : 'none', url: pdfUrl});
		})()
		""",
			await_promise=True,
		)
		self._log("[RPA] download_attachment: PDF info = " + str(pdf_result)[:300])

		try:
			pdf_info = json.loads(pdf_result) if isinstance(pdf_result, str) else {}
		except (json.JSONDecodeError, TypeError):
			pdf_info = {"method": "parse_error"}

		pdf_url = pdf_info.get("url", "")
		if pdf_url and pdf_url.startswith("http"):
			# 用页面 fetch 下载（带 auth cookies），base64 解码保存
			self._log("[RPA] download_attachment: fetching " + pdf_url[:100])
			js_fetch = (
				"(async function(){"
				"try {"
				"var resp = await fetch('" + pdf_url + "', {credentials: 'include'});"
				"var blob = await resp.blob();"
				"var reader = new FileReader();"
				"return new Promise(function(resolve) {"
				"reader.onload = function() { resolve(reader.result); };"
				"reader.readAsDataURL(blob);"
				"});"
				"} catch(e) { return 'ERROR:' + e.message; }"
				"})()"
			)
			pdf_data = self._eval(js_fetch, await_promise=True)
			if pdf_data and isinstance(pdf_data, str) and pdf_data.startswith("data:"):
				import base64

				header, b64 = pdf_data.split(",", 1)
				pdf_bytes = base64.b64decode(b64)
				filepath = os.path.join(save_dir, "resume_%d.pdf" % friend_id)
				with open(filepath, "wb") as f:
					f.write(pdf_bytes)
				size = len(pdf_bytes)
				self._log("[RPA] download_attachment: saved %s (%d bytes)" % (filepath, size))
				return {
					"code": 0,
					"message": "ok",
					"zpData": {
						"attachment_path": filepath,
						"attachment_size": size,
						"attachment_name": os.path.basename(filepath),
					},
				}

		# Fallback: check Chrome default Downloads
		import os as _os

		for check_dir in [save_dir, _os.path.join(_os.path.expanduser("~"), "Downloads")]:
			try:
				pdf_files = glob.glob(_os.path.join(check_dir, "*.pdf"))
				if pdf_files:
					latest = max(pdf_files, key=_os.path.getmtime)
					if _os.path.getmtime(latest) > _os.time() - 30:
						size = _os.path.getsize(latest)
						self._log("[RPA] download_attachment: %s (%d bytes)" % (_os.path.basename(latest), size))
						return {
							"code": 0,
							"message": "ok",
							"zpData": {
								"attachment_path": latest,
								"attachment_size": size,
								"attachment_name": _os.path.basename(latest),
							},
						}
			except Exception:
				pass

		return {
			"code": 0,
			"message": "附件下载未产生有效 PDF",
			"zpData": {"attachment_state": "download_failed", "pdf_url": str(pdf_url)[:80]},
		}

	def accept_attachment_share(self, friend_id: int) -> dict[str, Any]:
		"""接受候选人发送的附件简历。

		BOSS 流程：候选人分享附件简历后，招聘方页面会弹出
		「对方想发送附件简历给您，您是否同意」的卡片，需要点击「同意」
		按钮后附件才真正可用（才能通过 exchange_content 获取下载地址）。

		本方法打开对话，查找并点击同意按钮。
		"""
		self._ensure_chat_page()
		self.human_delay(0.5, 1.0)

		# 1. 按 BOSS 真实 friendId 查找并点开对话卡片
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			self._log(f"[RPA] accept_attachment: friend_id {friend_id} not found")
			return {"code": -1, "message": f"未找到 friend_id={friend_id} 的会话"}

		clicked = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			if (cards.length > {target_idx}) {{
				cards[{target_idx}].scrollIntoView({{block: 'center'}});
				cards[{target_idx}].click();
				return true;
			}}
			return false;
		}})()
		""")

		if not clicked:
			return {"code": -1, "message": f"无法点击 friend_id={friend_id} 的会话"}

		self.human_delay(1.5, 2.5)

		# 2. 查找并点击「同意」/「接收」按钮
		#    BOSS 的附件分享卡片通常是一段文字 + 按钮的形式，
		#    按钮文案可能是「同意」「接收」「查看附件」「接受」等。
		accepted = self._eval("""
		(() => {
			// 轮询等待按钮出现（React 异步渲染）
			for (let attempt = 0; attempt < 30; attempt++) {
				const all = document.querySelectorAll('button, a, span, div');
				for (const el of all) {
					const text = (el.textContent || '').trim();
					// 匹配同意/接收/接受按钮
					if (['同意', '接收', '接受', '查看附件', '接收简历', '同意接收'].includes(text)
						&& el.offsetParent !== null) {
						el.scrollIntoView({block: 'center'});
						el.click();
						return text;  // 返回点击的按钮文案
					}
					// 也匹配包含这些关键词且有「简历」/「附件」上下文的按钮
					if ((text === '同意' || text === '接收' || text === '接受')
						&& el.offsetParent !== null) {
						// 检查父级或附近是否有「简历」/「附件」关键词
						const parent = el.closest('[class*="card"], [class*="dialog"], [class*="popup"], [class*="exchange"], [class*="attachment"]');
						if (parent) {
							const parentText = (parent.textContent || '').trim();
							if (parentText.includes('简历') || parentText.includes('附件')) {
								el.scrollIntoView({block: 'center'});
								el.click();
								return text;
							}
						}
					}
				}
				const end = Date.now() + 200;
				while (Date.now() < end) {}
			}
			return '';
		})()
		""")

		if accepted:
			self._log(f"[RPA] accept_attachment: clicked '{accepted}'")
			self.human_delay(0.5, 1.0)
			return {"code": 0, "message": f"已点击「{accepted}」接受附件分享"}
		else:
			self._log("[RPA] accept_attachment: no accept button found")
			return {"code": 0, "message": "未找到附件分享同意按钮（可能候选人尚未分享或已自动接受）"}
