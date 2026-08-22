"""BOSS 沟通消息 RPA 的定位失败回归测试。"""

from __future__ import annotations

import json
from threading import Lock, Thread
import time
from typing import Any

import pytest

from boss_agent_cli.rpa.boss_client import BossRPAClient, BossRPALoginRequiredError, CHAT_PAGE


class _NonBossTargetClient(BossRPAClient):
	"""模拟 CDP 端口被其他本地项目占用的场景。

	RPA 只能操作 BOSS 招聘页面。这个测试桩明确返回一个可调试、但并非
	BOSS 的页面，用于防止会话选择逻辑为了“可用”而错误连接其他项目。
	"""

	def _cdp_get(self, path: str) -> object:
		"""返回一个非 BOSS 页面，模拟端口误绑定。"""
		self.cdp_reads += 1
		return [{
			"type": "page",
			"title": "Relay - Email Operations",
			"url": "http://127.0.0.1:5176/",
			"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/other-project",
		}]

	def _ws_connect(self) -> None:
		"""非 BOSS 页面不得进入 WebSocket 连接阶段。"""
		raise AssertionError("非 BOSS 页面不应被用作 RPA 会话")

	def __init__(self) -> None:
		"""记录 CDP 探测次数，验证失败后不会再尝试页面导航。"""
		super().__init__()
		self.cdp_reads = 0


def test_ensure_session_rejects_cdp_without_boss_page() -> None:
	"""调试端口没有 BOSS 页面时必须给出受控错误，不能回退到第一张页面。"""
	client = _NonBossTargetClient()

	with pytest.raises(RuntimeError, match="未连接 BOSS 招聘页面"):
		client.ensure_session()


def test_ensure_session_prefers_existing_recruiter_page_over_new_login_page(monkeypatch) -> None:
	"""新开登录页不能抢走已登录聊天页，否则后续列表会被误判为未登录。"""
	client = BossRPAClient()
	monkeypatch.setattr(client, "_cdp_get", lambda _path: [
		{"type": "page", "url": "https://www.zhipin.com/web/user/", "webSocketDebuggerUrl": "ws://127.0.0.1/login"},
		{"type": "page", "url": "https://www.zhipin.com/web/chat/index", "webSocketDebuggerUrl": "ws://127.0.0.1/chat"},
	])
	monkeypatch.setattr(client, "_ws_connect", lambda: None)

	client.ensure_session()

	assert client._ws_url == "ws://127.0.0.1/chat"


def test_navigation_stops_after_rpa_target_validation_fails() -> None:
	"""RPA 未绑定 BOSS 页面时不能吞掉错误后再次尝试执行导航。"""
	client = _NonBossTargetClient()

	with pytest.raises(RuntimeError, match="未连接 BOSS 招聘页面"):
		client.navigate_to(CHAT_PAGE)

	assert client.cdp_reads == 1


class _LoginRedirectRPAClient(BossRPAClient):
	"""模拟 BOSS 招聘页被重定向到登录页的专用浏览器。"""

	def navigate_to(self, url: str) -> None:
		"""测试只验证重定向识别，不打开真实浏览器页面。"""

	def wait_loaded(self, timeout: float = 5) -> None:
		"""模拟已完成页面加载。"""

	def human_delay(self, lo: float = 0.3, hi: float = 1.5) -> None:
		"""测试不引入随机等待。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> object:
		"""登录重定向后，不应继续读取沟通候选人 DOM。"""
		if js == "window.location.href":
			return "https://www.zhipin.com/web/user/?ka=bticket"
		raise AssertionError("登录页不应被当作沟通列表页面继续解析")


def test_friend_list_reports_rpa_browser_login_redirect() -> None:
	"""BOSS 把沟通页重定向至登录页时，不能伪装为成功的空列表。"""
	client = _LoginRedirectRPAClient()

	with pytest.raises(BossRPALoginRequiredError, match="项目 RPA 浏览器尚未登录"):
		client.friend_list()


class _ConversationMissingClient(BossRPAClient):
	"""模拟当前聊天页没有目标会话卡片的 RPA 客户端。

	测试只验证发送前的定位边界，不能让测试通过 CDP 或浏览器产生真实沟通。
	"""

	def navigate_to(self, url: str) -> None:
		"""屏蔽真实页面导航，保持测试纯内存执行。"""

	def wait_loaded(self, timeout: float = 15.0) -> None:
		"""屏蔽真实页面等待，定位失败无需加载页面。"""

	def human_delay(self, minimum: float = 0.1, maximum: float = 0.2) -> None:
		"""屏蔽真实随机等待，避免单元测试引入时间成本。"""

	def _find_card_by_friend_id(self, friend_id: int) -> int | None:
		"""明确模拟 RPA 未能在当前列表定位候选人。"""
		return None

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""定位失败时不允许继续执行任何页面脚本。"""
		raise AssertionError("未定位到会话时不应继续操作页面元素")


def test_send_message_stops_before_dom_access_when_conversation_is_missing() -> None:
	"""目标会话缺失必须返回受控错误，不能把空索引注入 RPA 脚本。"""
	client = _ConversationMissingClient()

	result = client.send_message_by_friend(123, "您好，方便发一份简历看看吗？")

	assert result == {"code": -1, "message": "未找到 friend_id=123 的会话"}


def test_friend_list_restores_user_scroll_position_after_snapshot_scan(monkeypatch) -> None:
	"""同步全量虚拟列表后必须回到用户原来的位置，避免列表跳动成自动刷新。"""
	client = object.__new__(BossRPAClient)
	restored = False

	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "_move_friend_list_viewport", lambda _page: 321)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str, **_kwargs: Any) -> Any:
		nonlocal restored
		if "list.scrollTop = 321" in script:
			restored = True
			return True
		return [{
			"_idx": 0, "_fid": "42", "name": "候选人", "job": "Java",
			"company": "", "city": "", "time": "", "unread": "0",
			"geekId": "", "jobId": "", "securityId": "",
		}]

	monkeypatch.setattr(client, "_eval", evaluate)

	client.friend_list()

	assert restored is True


class _EmptyCDPFrameClient(BossRPAClient):
	"""模拟 CDP 先发空帧/事件帧，再返回当前命令结果的浏览器。"""

	def __init__(self) -> None:
		super().__init__()
		# 该用例仅验证收到空帧后的接收循环，不允许回退到真实 CDP。
		# 先用一个无网络语义的哨兵占位，暴露当前实现是否错误地尝试建连。
		self._ws_sock = object()
		self._msg_id = 7
		self.sent_payload = b""
		self.frames = iter((
			b"",
			b"not-json",
			json.dumps({"method": "Runtime.consoleAPICalled", "params": {}}).encode("utf-8"),
			json.dumps({"id": 8, "result": {"result": {"value": True}}}).encode("utf-8"),
		))

	def _send_ws_payload(self, payload: bytes) -> None:
		"""测试不建立真实 WebSocket，只记录发送出的命令帧。"""
		self.sent_payload = payload

	def _receive_cdp_response(self, expected_id: int) -> dict[str, Any]:
		"""使用真实接收循环，确保无效帧不会直接冒泡成 JSONDecodeError。"""
		return super()._receive_cdp_response(expected_id)

	def _receive_ws_payload(self) -> bytes:
		"""按顺序返回 CDP 可能出现的中间帧。"""
		return next(self.frames)


def test_cdp_command_ignores_empty_intermediate_frame() -> None:
	"""CDP 空帧不能冒泡成 JSONDecodeError，必须继续等待命令响应。"""
	client = _EmptyCDPFrameClient()

	assert client._cdp_send("Runtime.evaluate", {"expression": "true"}) == {"result": {"value": True}}
	assert client.sent_payload


class _MalformedCDPFrameClient(BossRPAClient):
	"""模拟连接混入无法按 UTF-8 解码的帧，后续仍能返回当前命令响应。"""

	def __init__(self) -> None:
		"""准备异常帧与目标响应，避免测试依赖真实浏览器。"""
		super().__init__()
		self.frames = iter((
			b"\x81",
			json.dumps({"id": 1, "result": {"result": {"value": True}}}).encode("utf-8"),
		))

	def _receive_ws_payload(self) -> bytes:
		"""按顺序返回孤立异常字节和合法 CDP 响应。"""
		return next(self.frames)


def test_cdp_response_ignores_malformed_utf8_frame() -> None:
	"""异常字节帧不能中断后续对应命令响应的读取。"""
	client = _MalformedCDPFrameClient()

	assert client._receive_cdp_response(1) == {"id": 1, "result": {"result": {"value": True}}}


def test_cdp_transport_failure_invalidates_reused_session() -> None:
	"""CDP 传输失败后必须释放旧会话，下一次调用才能重新发现页面。"""
	client = BossRPAClient()
	client._ws_sock = object()
	client._ws_url = "ws://127.0.0.1:9222/devtools/page/stale"

	def fail_send(_payload: str, *, expected_id: int) -> dict[str, Any]:
		"""模拟已关闭的 WebSocket 在发送阶段抛出超时。"""
		raise TimeoutError("socket timed out")

	client._ws_send = fail_send  # type: ignore[method-assign]

	with pytest.raises(TimeoutError, match="socket timed out"):
		client._cdp_send("Runtime.evaluate")

	assert client._ws_sock is None
	assert client._ws_url is None


class _ConcurrentCDPCommandClient(BossRPAClient):
	"""记录 CDP 命令临界区，验证一条连接不会并发收取不同响应。"""

	def __init__(self) -> None:
		"""设置伪连接并初始化并发观测状态。"""
		super().__init__()
		self._ws_sock = object()
		self._probe_lock = Lock()
		self._active_commands = 0
		self.maximum_active_commands = 0

	def _ws_send(self, payload: str, *, expected_id: int) -> dict[str, Any]:
		"""人为拉长收发周期，使没有会话互斥时稳定暴露并发重叠。"""
		with self._probe_lock:
			self._active_commands += 1
			self.maximum_active_commands = max(self.maximum_active_commands, self._active_commands)
		try:
			time.sleep(0.03)
			return {"message_id": expected_id}
		finally:
			with self._probe_lock:
				self._active_commands -= 1


def test_cdp_commands_serialize_send_and_receive_on_shared_socket() -> None:
	"""并发调用必须完整独占一次发送至接收，避免 WebSocket 响应串线。"""
	client = _ConcurrentCDPCommandClient()
	results: list[dict[str, Any]] = []

	threads = [Thread(target=lambda: results.append(client._cdp_send("Runtime.evaluate"))) for _ in range(2)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()

	assert len(results) == 2
	assert client.maximum_active_commands == 1


class _UnconfirmedSendClient(BossRPAClient):
	"""模拟编辑器接受输入但 BOSS 没有产生招聘方气泡的发送失败。"""

	def navigate_to(self, url: str) -> None:
		"""测试不打开真实页面。"""

	def wait_loaded(self, timeout: float = 15.0) -> None:
		"""测试不等待页面加载。"""

	def human_delay(self, minimum: float = 0.1, maximum: float = 0.2) -> None:
		"""测试不引入随机等待。"""

	def _find_card_by_friend_id(self, friend_id: int) -> int | None:
		"""返回已精确定位的会话卡片。"""
		return 0

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""发送控件可点击，但回读时始终没有招聘方消息。"""
		if "cards.length" in js or "for (let attempt" in js or ".submit.active" in js:
			return True
		if ".item-myself .text-content" in js:
			return False
		raise AssertionError(f"unexpected script: {js[:80]}")

	def _cdp_send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		"""模拟 CDP 输入事件成功返回，证明事件成功不足以等同于消息送达。"""
		return {}

	def _chat_editor_draft(self) -> str:
		"""模拟已聚焦但为空的聊天编辑器。"""
		return ""

	def _wait_for_recruiter_message(self, content: str, timeout: float = 5.0) -> bool:
		"""明确模拟 BOSS 未把待发草稿渲染成招聘方消息。"""
		return False


def test_send_message_requires_recruiter_bubble_confirmation() -> None:
	"""编辑器和提交按钮未报错时，仍必须等待会话出现对应招聘方气泡。"""
	client = _UnconfirmedSendClient()

	result = client.send_message_by_friend(123, "请问您目前住在广州吗？")

	assert result == {"code": -1, "message": "消息未在 BOSS 会话中确认送达"}


class _HistoricCandidateMessageClient(BossRPAClient):
	"""模拟页面停在招聘方最新问题、但仍渲染更早候选人消息的会话。

	这正是候选人尚未回复当前问题时最常见的 DOM 形态。读取器必须明确把它
	识别为“没有新候选人回复”，否则历史基础回答会被错误交给专业问答阶段。
	"""

	def _ensure_chat_page(self) -> None:
		"""测试不连接真实浏览器，仅验证消息顺序判定。"""

	def _find_card_by_friend_id(self, friend_id: int) -> int | None:
		"""目标会话已在当前渲染窗口内。"""
		return 0 if friend_id == 123 else None

	def human_delay(self, minimum: float = 0.1, maximum: float = 0.2) -> None:
		"""单元测试不等待页面动画。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""按 ``last_messages`` 的页面交互顺序返回确定性 DOM 快照。"""
		if "cards.length" in js:
			return True
		if ".chat-message-list .message-item" in js:
			return [
				{"role": "candidate", "text": "我在广州，可以尽快到岗"},
				{"role": "recruiter", "text": "请结合一个 Java 项目说明技术方案和结果。"},
			]
		raise AssertionError(f"unexpected script: {js[:80]}")


def test_last_messages_ignores_historic_candidate_text_before_latest_recruiter_question() -> None:
	"""招聘方最新问题之后没有候选人回复时，历史文本不能触发新一轮 AI。"""
	client = _HistoricCandidateMessageClient()

	result = client.last_messages([123])

	assert result == {"code": 0, "zpData": {"lastMessageList": []}}


class _ConversationPreviewSelectorClient(BossRPAClient):
	"""模拟 BOSS 当前卡片将最后消息预览渲染在 ``.push-text`` 的版本。"""

	def _ensure_chat_page(self) -> None:
		"""测试不连接真实浏览器。"""

	def _ensure_recruiter_page_ready(self) -> None:
		"""测试中页面已完成渲染。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""仅接受包含真实预览字段的卡片投影脚本。"""
		if "const targetScrollTop" in js:
			return {"changed": False, "top": 0}
		if "const preview" in js and "bossAgentSnapshotChunk" not in js:
			if ".push-text" not in js:
				raise AssertionError("会话版本未读取 BOSS 当前的 .push-text 最新消息预览")
			return []
		if "bossAgentSnapshotMeta" in js:
			return {"originalTop": 0, "maxTop": 0, "step": 1}
		if "bossAgentSnapshotChunk" in js:
			if ".push-text" not in js:
				raise AssertionError("会话版本未读取 BOSS 当前的 .push-text 最新消息预览")
			return {"items": []} if await_promise else []
		if "bossAgentSnapshotRestore" in js:
			return True
		raise AssertionError(f"unexpected script: {js[:80]}")


def test_conversation_version_reads_push_text_preview_when_unread_badge_is_gone() -> None:
	"""人工点开清除红点后，最新消息预览仍必须参与版本变化检测。"""
	client = _ConversationPreviewSelectorClient()

	assert client.friend_list() == {"code": 0, "zpData": {"friendList": []}}
	assert client.fast_conversation_snapshot() == {"code": 0, "zpData": {"friendList": []}}


def test_attachment_preview_url_is_scoped_to_the_visible_dialog(monkeypatch, tmp_path) -> None:
	"""历史预览 iframe 存在时，附件下载不能复用上一位候选人的 PDF 链接。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda _friend_id: 0)
	monkeypatch.setattr(client, "_cdp_send", lambda *_args, **_kwargs: {})
	monkeypatch.setattr(client, "_log", lambda _message: None)
	scripts: list[str] = []

	def evaluate(script: str, *, await_promise: bool = False) -> object:
		scripts.append(script)
		if "attempt = 0; attempt < 50" in script:
			return "not-found"
		if "var buttons = document.querySelectorAll('.resume-btn-file" in script:
			return json.dumps({"disabled": False, "text": "附件简历"})
		if "var pdfUrl = ''" in script:
			return json.dumps({"method": "none", "url": ""})
		return None

	monkeypatch.setattr(client, "_eval", evaluate)

	client.download_attachment_via_ui(friend_id=42, save_dir=str(tmp_path))

	preview_script = next(script for script in scripts if "var pdfUrl = ''" in script)
	assert "dialog.querySelectorAll('iframe')" in preview_script
	assert "document.querySelectorAll('iframe')" not in preview_script


def test_attachment_download_accepts_the_visible_bottom_confirmation_bar(monkeypatch, tmp_path) -> None:
	"""底部固定确认栏的同意按钮也属于当前附件会话，必须能被准确点击。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda _friend_id: 0)
	monkeypatch.setattr(client, "_cdp_send", lambda *_args, **_kwargs: {})
	monkeypatch.setattr(client, "_log", lambda _message: None)

	def evaluate(script: str, *, await_promise: bool = False) -> object:
		if "fixed-attachment-acceptance" in script:
			return "fixed-bar-clicked:同意"
		if "var buttons = document.querySelectorAll('.resume-btn-file" in script:
			return json.dumps({"disabled": True, "text": "附件简历"})
		return None

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client.download_attachment_via_ui(friend_id=42, save_dir=str(tmp_path))

	assert result["zpData"]["attachment_state"] == "acceptance_pending"


class _ManualDraftClient(_UnconfirmedSendClient):
	"""模拟招聘方正在编辑另一条人工草稿的会话。"""

	def _chat_editor_draft(self) -> str:
		"""自动流程不能覆盖人工未发送的内容。"""
		return "人工正在编辑的草稿"


def test_send_message_refuses_to_overwrite_a_different_manual_draft() -> None:
	"""编辑器已有不同草稿时，自动发送必须失败而不能把两段内容拼接。"""
	client = _ManualDraftClient()

	result = client.send_message_by_friend(123, "请问您目前住在广州吗？")

	assert result == {"code": -1, "message": "聊天输入框存在未发送草稿，已停止自动发送"}


def test_chat_editor_draft_uses_a_chat_local_visibility_guard(monkeypatch) -> None:
	"""聊天页脚本不能引用只在招呼语设置页声明的可见性函数。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []

	def evaluate(script: str, **_kwargs: object) -> str:
		scripts.append(script)
		return "人工草稿"

	monkeypatch.setattr(client, "_eval", evaluate)

	assert client._chat_editor_draft() == "人工草稿"
	assert len(scripts) == 1
	assert "const isChatElementVisible" in scripts[0]
	assert "isGreetingElementVisible" not in scripts[0]
