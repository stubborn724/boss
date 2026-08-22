"""通过本机 Chrome Bridge 操作已登录 BOSS 页面。

本客户端复用 :class:`BossRPAClient` 的 DOM 读取能力，但不建立 CDP WebSocket，
也不读取 Cookie。Bridge 只附着用户已经打开的 BOSS 标签页并执行页面级 DOM
操作；附件只点击可见下载按钮，再从 Chrome 下载记录确认文件已经写入磁盘。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from boss_agent_cli.bridge.client import BridgeClient
from boss_agent_cli.rpa.boss_client import (
	BossRPAConnectionError,
	BossRPAClient,
	BossRPALoginRequiredError,
)


class BridgeBossRPAClient(BossRPAClient):
	"""使用 Bridge 传输的 BOSS DOM RPA 客户端。

	继承现有 RPA 的列表、职位和会话 DOM 投影，避免两种浏览器连接维护两套选择器。
	覆盖的部分仅限传输差异：页面计算、导航、消息输入和附件下载确认。
	"""

	def __init__(
		self,
		*,
		bridge: BridgeClient | None = None,
		poll_interval_seconds: float = 0.5,
	) -> None:
		super().__init__(cdp_url="bridge://existing-chrome")
		self._bridge = bridge or BridgeClient()
		self._poll_interval_seconds = max(0.0, poll_interval_seconds)
		# 保存最近一次附件动作的页面诊断，供上层区分“未分享”和“同意后尚未生效”。
		self._last_attachment_action_state = "not_shared"

	def ensure_session(self) -> None:
		"""验证 Bridge 已连接且其当前目标确实是 BOSS 页面。"""
		if not self._bridge.is_extension_connected():
			raise BossRPAConnectionError("Chrome Bridge 未连接")
		try:
			current_url = self._eval("window.location.href")
		except RuntimeError as exc:
			raise BossRPAConnectionError("未连接 BOSS 招聘页面") from exc
		if not isinstance(current_url, str) or not self._is_zhipin_url(current_url):
			raise BossRPAConnectionError("未连接 BOSS 招聘页面")
		if "/web/user" in current_url:
			raise BossRPALoginRequiredError("当前 Chrome 的 BOSS 招聘页面尚未登录")

	def _eval(
		self,
		js: str,
		*,
		await_promise: bool = False,
		timeout_seconds: float | None = None,
	) -> Any:
		"""在既有 BOSS 标签页执行 DOM 脚本，不经 HTTP 接口请求平台数据。"""
		# Bridge 的 HTTP 命令上限由客户端统一控制；此参数仅与 CDP 客户端保持
		# 同一调用契约，使继承的虚拟列表扫描可以声明其页面计算时长。
		del await_promise, timeout_seconds
		result = self._bridge.evaluate(js, workspace="boss")
		# BridgeClient 为了保持 dict 返回约定，会把 JavaScript 的字符串、数字
		# 和布尔值包装为 {"result": value}。RPA 选择器大量依赖原始标量，必须
		# 在传输边界一次性解包；对象结果仍原样保留，避免丢失 DOM 投影字段。
		if isinstance(result, dict) and set(result) == {"result"}:
			return result["result"]
		return result

	def navigate_to(self, url: str) -> None:
		"""只允许在已附着的 BOSS 标签页内导航。"""
		self.ensure_session()
		self._bridge.navigate(url, workspace="boss")

	def _ensure_chat_page(self) -> None:
		"""复用当前沟通页，只有离开该页面时才导航。

		用户正在查看 BOSS 时，重复导航会清空会话面板并被感知为页面刷新。Bridge
		模式以现有标签页为主，因此先读取 URL；CDP 基类仍保留原来的强制导航行为。
		"""
		self.ensure_session()
		current_url = self._eval("window.location.href")
		if isinstance(current_url, str):
			from urllib.parse import urlparse
			path = urlparse(current_url).path.rstrip("/")
			# ``/web/chat/recommend`` 也是沟通模块的子路由，但它只展示推荐牛人，
			# 没有沟通卡片。只有 index 才能承载会话列表，因此不能用宽泛的
			# ``/web/chat/`` 前缀判断页面已经就绪。
			if path == "/web/chat/index":
				return
		self.navigate_to("https://www.zhipin.com/web/chat/index")
		self.wait_loaded()
		self.human_delay()

	def _ensure_recruiter_page_ready(self) -> None:
		"""等待沟通列表组件挂载完成，再允许读取候选人。

		BOSS 的沟通页是“外层导航壳 + 异步列表组件”结构，
		``document.readyState == complete`` 只代表外层页面加载结束，不能说明
		候选人卡片已经出现在 DOM 中。Bridge 如果在此刻立即读取，会把暂时的
		空数组误报成“没有沟通候选人”。这里沿用基类的登录页校验，再以短间隔
		轮询真实卡片；超时只返回空结果，让上层保留原有错误处理，不触发导航或
		重复发送消息。
		"""
		super()._ensure_recruiter_page_ready()
		self._wait_for_chat_list()

	def _wait_for_chat_list(self, *, timeout_seconds: float = 10.0) -> None:
		"""等待候选人卡片或明确的空列表提示出现。

		轮询只执行 DOM 读取，不点击、不刷新、不切换会话。页面明确显示“暂无
		沟通”等空态时立即结束；否则最多等待给定时长，避免网络异常拖住工作台。
		"""
		deadline = time.monotonic() + max(0.0, timeout_seconds)
		while True:
			card_count = self._eval("document.querySelectorAll('.geek-item-wrap').length")
			if isinstance(card_count, (int, float)) and card_count > 0:
				return
			empty_state = self._eval(
				"""(() => {
					const text = (document.body?.innerText || '').replace(/\\s+/g, '');
					return /暂无沟通|暂无候选人|没有更多候选人/.test(text);
				})()""",
			)
			if empty_state is True or time.monotonic() >= deadline:
				return
			self.human_delay(0.2, 0.4)

	def human_delay(self, lo: float = 0.3, hi: float = 1.5) -> None:
		"""Bridge RPA 保留轻量等待，给 BOSS 的异步 DOM 渲染留下时间。"""
		del lo, hi
		if self._poll_interval_seconds:
			time.sleep(self._poll_interval_seconds)

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		"""在已精确定位的会话内输入并点击可见“发送”按钮。"""
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			return {"code": -1, "message": "未找到 friend_id 对应的会话"}
		self._ensure_chat_page()
		opened = self._open_exact_conversation(friend_id)
		if not opened:
			return {"code": -1, "message": "无法打开目标候选人会话"}
		payload = json.dumps(content, ensure_ascii=False)
		result = self._eval(f"""
		(() => {{
			const input = document.querySelector('textarea, [contenteditable="true"], [role="textbox"]');
			if (!input || input.offsetParent === null) return false;
			input.focus();
			if (input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {{
				const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value');
				if (descriptor && descriptor.set) descriptor.set.call(input, {payload});
				else input.value = {payload};
			}} else {{
				input.textContent = {payload};
			}}
			input.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: {payload} }}));
			const send = Array.from(document.querySelectorAll(
				'button, [role="button"], .submit.active, .submit'
			)).find((button) => {{
				const text = (button.textContent || '').trim();
				return button.offsetParent !== null
					&& button.getAttribute('aria-disabled') !== 'true'
					&& !button.hasAttribute('disabled')
					&& (text === '发送' || text === '发 送');
			}});
			if (!send) return false;
			send.click();
			return true;
		}})()
		""")
		return {"code": 0, "message": "ok"} if result is True else {"code": -1, "message": "未找到可用发送按钮"}

	def download_attachment_via_ui(self, friend_id: int, save_dir: str | None = None) -> dict[str, Any]:
		"""点击 BOSS 可见附件按钮，并将本次完成的下载移动至目标目录。"""
		output_dir = Path(save_dir) if save_dir else Path.home() / "Desktop" / "简历"
		before_ids = {self._download_id(item) for item in self._bridge.list_downloads()}
		self._ensure_chat_page()
		if not self._open_exact_conversation(friend_id):
			return {"code": -1, "message": "未找到该候选人的会话"}
		if not self._click_attachment_acceptance_and_download():
			return {
				"code": 0,
				"message": "附件分享尚未完成或附件按钮仍禁用"
				if self._last_attachment_action_state == "acceptance_pending"
				else "候选人未分享可下载的附件简历",
				"zpData": {"attachment_state": self._last_attachment_action_state},
			}
		download = self._wait_for_new_completed_download(before_ids=before_ids)
		if download is None:
			return {
				"code": 0,
				"message": "附件下载尚未完成",
				"zpData": {"attachment_state": "download_pending"},
			}
		return self._move_completed_download(download, output_dir=output_dir, friend_id=friend_id)

	def _open_exact_conversation(self, friend_id: int) -> bool:
		"""通过 friend_id 再次确认并打开会话，避免索引漂移误点其他人。"""
		target_idx = self._find_card_by_friend_id(friend_id)
		if target_idx is None:
			return False
		result = self._eval(f"""
		(() => {{
			const cards = document.querySelectorAll('.geek-item-wrap');
			const card = cards[{target_idx}];
			if (!card) return false;
			const inner = card.querySelector('.geek-item, [data-id]');
			if (!inner || (inner.getAttribute('data-id') || '').split('-')[0] !== {json.dumps(str(friend_id))}) return false;
			card.scrollIntoView({{ block: 'center' }});
			card.click();
			return true;
		}})()
		""")
		self.human_delay()
		return result is True

	def _click_attachment_acceptance_and_download(self) -> bool:
		"""按页面顺序点击同意、附件简历及预览下载按钮。

		元素文案需同时满足附件/简历上下文，不能对页面上任意“同意”按钮点击。
		"""
		self._last_attachment_action_state = "not_shared"
		result = self._eval("""
		(() => {
			const visible = (element) => element && element.offsetParent !== null;
			const containers = Array.from(document.querySelectorAll('[class*="attachment"], [class*="exchange"], [class*="resume"], [class*="dialog"], [role="dialog"]'))
				.filter((element) => visible(element) && /附件|简历/.test(element.textContent || ''));
			let accepted = false;
			for (const container of containers) {
				const controls = Array.from(container.querySelectorAll('button, a, [role="button"], [class*="btn"]'));
				const accept = controls.find((button) => visible(button)
					&& ['同意', '接收', '接受', '同意接收'].includes((button.textContent || '').trim())
					&& !button.disabled && button.getAttribute('aria-disabled') !== 'true');
				if (accept) { accept.click(); accepted = true; }
			}
			const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
			const attachment = buttons.find((button) => visible(button)
				&& /^附件简历$/.test((button.textContent || '').trim())
				&& !button.hasAttribute('disabled') && button.getAttribute('aria-disabled') !== 'true'
				&& !button.classList.contains('disabled'));
			if (!attachment) return {accepted, ready: false};
			attachment.click();
			return {accepted, ready: true};
		})()
		""")
		if isinstance(result, dict) and result.get("accepted") and not result.get("ready"):
			self._last_attachment_action_state = "acceptance_pending"
			for _ in range(12):
				self.human_delay(0.3, 0.5)
				ready = self._eval("""
				(() => {
					const button = Array.from(document.querySelectorAll('button, a, [role="button"]'))
						.find((element) => element.offsetParent !== null && /^附件简历$/.test((element.textContent || '').trim())
							&& !element.hasAttribute('disabled') && element.getAttribute('aria-disabled') !== 'true'
							&& !element.classList.contains('disabled'));
					if (!button) return false;
					button.click();
					return true;
				})()
				""")
				if ready is True:
					break
			else:
				return False
		elif result is not True and not (isinstance(result, dict) and result.get("ready")):
			return False
		self._last_attachment_action_state = "download_pending"
		self.human_delay()
		return self._eval("""
		(() => {
			const download = Array.from(document.querySelectorAll('button, a, [role="button"]')).find((button) => {
				const text = (button.textContent || '').trim();
				return button.offsetParent !== null && (text === '下载' || text === '下载附件');
			});
			if (!download) return false;
			download.click();
			return true;
		})()
		""") is True

	def _wait_for_new_completed_download(self, *, before_ids: set[int]) -> dict[str, Any] | None:
		"""只接受点击后出现的完整下载，拒绝历史文件和未完成文件。"""
		deadline = time.monotonic() + 30
		while time.monotonic() < deadline:
			for item in self._bridge.list_downloads():
				if not isinstance(item, dict) or self._download_id(item) in before_ids:
					continue
				if item.get("state") == "complete" and isinstance(item.get("filename"), str):
					return item
			self.human_delay()
		return None

	@staticmethod
	def _download_id(item: dict[str, Any]) -> int:
		"""Chrome 下载编号缺失时使用 -1，使不完整记录无法误匹配。"""
		value = item.get("id")
		return int(value) if isinstance(value, int) else -1

	def _move_completed_download(
		self, download: dict[str, Any], *, output_dir: Path, friend_id: int,
	) -> dict[str, Any]:
		"""转移 Chrome 已完成的附件，验证非空后才向上层声明成功。"""
		source = Path(str(download["filename"]))
		if not source.is_file() or source.stat().st_size <= 0:
			return {"code": 0, "message": "附件文件不存在或为空"}
		output_dir.mkdir(parents=True, exist_ok=True)
		suffix = source.suffix.lower() or ".bin"
		target = output_dir / f"resume_{friend_id}{suffix}"
		index = 1
		while target.exists():
			target = output_dir / f"resume_{friend_id}_{index}{suffix}"
			index += 1
		shutil.move(str(source), str(target))
		return {
			"code": 0,
			"message": "ok",
			"zpData": {
				"attachment_path": str(target),
				"attachment_size": target.stat().st_size,
				"attachment_name": target.name,
			},
		}
