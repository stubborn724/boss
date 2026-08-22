"""Bridge 驱动的 BOSS 页面 RPA 回归测试。"""

from pathlib import Path

from boss_agent_cli.rpa.bridge_boss_client import BridgeBossRPAClient


class _Bridge:
	"""只记录 DOM 调用和下载记录，不连接真实 Chrome。"""

	def __init__(self, source: Path) -> None:
		self.source = source
		self.codes: list[str] = []
		self.download_calls = 0

	def is_extension_connected(self) -> bool:
		return True

	def evaluate(self, code: str, *, workspace: str = "boss") -> object:
		self.codes.append(code)
		if "window.location.href" in code:
			return "https://www.zhipin.com/web/chat/index"
		if "document.readyState" in code:
			return "complete"
		return True

	def navigate(self, url: str, *, workspace: str = "boss") -> dict[str, object]:
		return {"url": url}

	def list_downloads(self, *, since_ms: int = 0) -> list[dict[str, object]]:
		self.download_calls += 1
		if self.download_calls == 1:
			return []
		return [{"id": 9, "state": "complete", "filename": str(self.source), "endTime": "2099-01-01"}]


def test_bridge_attachment_download_uses_visible_click_and_moves_file(tmp_path: Path) -> None:
	"""附件下载必须由页面点击触发，Bridge 只读取完成下载记录。"""
	source = tmp_path / "Downloads" / "resume.pdf"
	source.parent.mkdir()
	source.write_bytes(b"%PDF-1.4 attachment")
	bridge = _Bridge(source)
	client = BridgeBossRPAClient(bridge=bridge, poll_interval_seconds=0)
	client._open_exact_conversation = lambda friend_id: True  # type: ignore[method-assign]
	client._click_attachment_acceptance_and_download = lambda: True  # type: ignore[method-assign]

	result = client.download_attachment_via_ui(friend_id=7, save_dir=str(tmp_path / "Desktop" / "简历"))

	path = Path(result["zpData"]["attachment_path"])
	assert result["code"] == 0
	assert path.is_file()
	assert path.stat().st_size > 0
	assert not source.exists()
	assert all("fetch(" not in code for code in bridge.codes)


def test_bridge_send_requires_exact_conversation_before_dom_input(tmp_path: Path) -> None:
	"""未定位到 friend_id 时不能访问输入框，更不能发送消息。"""
	bridge = _Bridge(tmp_path / "unused.pdf")
	client = BridgeBossRPAClient(bridge=bridge, poll_interval_seconds=0)
	client._find_card_by_friend_id = lambda friend_id: None  # type: ignore[method-assign]

	result = client.send_message_by_friend(7, "可以看看你的简历吗？")

	assert result["code"] == -1
	assert "未找到" in result["message"]
	assert bridge.codes == []


def test_bridge_rpa_exposes_platform_success_contract(tmp_path: Path) -> None:
	"""直接把 RPA 客户端交给附件状态机时，也能统一判断 BOSS 响应。"""
	client = BridgeBossRPAClient(bridge=_Bridge(tmp_path / "unused.pdf"), poll_interval_seconds=0)

	assert client.is_success({"code": 0}) is True
	assert client.is_success({"code": -1}) is False


def test_bridge_send_supports_current_div_submit_control(tmp_path: Path) -> None:
	"""BOSS 当前沟通页的发送控件是 div.submit，而不是 button。"""
	class _CurrentChatBridge(_Bridge):
		def evaluate(self, code: str, *, workspace: str = "boss") -> object:
			self.codes.append(code)
			if "window.location.href" in code:
				return "https://www.zhipin.com/web/chat/index"
			return ".submit.active" in code

	client = BridgeBossRPAClient(
		bridge=_CurrentChatBridge(tmp_path / "unused.pdf"),
		poll_interval_seconds=0,
	)
	client._find_card_by_friend_id = lambda friend_id: 0  # type: ignore[method-assign]
	client._open_exact_conversation = lambda friend_id: True  # type: ignore[method-assign]

	result = client.send_message_by_friend(7, "您好，方便发一份简历看看吗？")

	assert result["code"] == 0


def test_bridge_session_accepts_primitive_result_wrapped_by_client(tmp_path: Path) -> None:
	"""BridgeClient 对原始字符串的包装不能让已打开的 BOSS 页误判为未连接。"""
	class _WrappingBridge(_Bridge):
		def evaluate(self, code: str, *, workspace: str = "boss") -> object:
			self.codes.append(code)
			return {"result": "https://www.zhipin.com/web/chat/index"}

	client = BridgeBossRPAClient(bridge=_WrappingBridge(tmp_path / "unused.pdf"), poll_interval_seconds=0)

	client.ensure_session()


def test_bridge_reuses_visible_chat_page_without_forced_navigation(tmp_path: Path) -> None:
	"""状态读取应复用当前沟通页，不能每个候选人都让页面重新加载。"""
	bridge = _Bridge(tmp_path / "unused.pdf")
	navigations: list[str] = []
	bridge.navigate = lambda url, *, workspace="boss": navigations.append(url) or {"url": url}  # type: ignore[method-assign]
	client = BridgeBossRPAClient(bridge=bridge, poll_interval_seconds=0)

	client._ensure_chat_page()

	assert navigations == []


def test_bridge_leaves_recommend_route_before_reading_chat(tmp_path: Path) -> None:
	"""推荐牛人路由不能被误判为沟通页，需切回真实沟通入口。"""
	bridge = _Bridge(tmp_path / "unused.pdf")
	navigations: list[str] = []
	bridge.evaluate = lambda code, *, workspace="boss": (  # type: ignore[method-assign]
		"https://www.zhipin.com/web/chat/recommend"
		if "window.location.href" in code
		else True
	)
	bridge.navigate = lambda url, *, workspace="boss": navigations.append(url) or {"url": url}  # type: ignore[method-assign]
	client = BridgeBossRPAClient(bridge=bridge, poll_interval_seconds=0)

	client._ensure_chat_page()

	assert navigations == ["https://www.zhipin.com/web/chat/index"]


def test_bridge_waits_for_async_chat_list_to_mount(tmp_path: Path) -> None:
	"""沟通页壳加载完成时，Bridge 仍需等待异步候选人卡片挂载。"""
	class _AsyncChatBridge(_Bridge):
		def __init__(self) -> None:
			super().__init__(tmp_path / "unused.pdf")
			self.card_checks = 0

		def evaluate(self, code: str, *, workspace: str = "boss") -> object:
			self.codes.append(code)
			if "querySelectorAll('.geek-item-wrap').length" in code:
				self.card_checks += 1
				return 0 if self.card_checks == 1 else 2
			if "window.location.href" in code:
				return "https://www.zhipin.com/web/chat/index"
			if "暂无沟通" in code:
				return False
			return True

	bridge = _AsyncChatBridge()
	client = BridgeBossRPAClient(bridge=bridge, poll_interval_seconds=0)

	client._wait_for_chat_list(timeout_seconds=0.1)

	assert bridge.card_checks == 2
