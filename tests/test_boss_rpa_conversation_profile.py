"""BOSS 沟通页资料栏 RPA 读取测试。"""

import base64
import io
import sys
from types import SimpleNamespace

from PIL import Image

from boss_agent_cli.rpa.boss_client import BossRPAClient
from boss_agent_cli.recruiting.online_resume_validation import is_meaningful_online_resume_text


def test_online_resume_validation_rejects_short_ocr_garbage() -> None:
	"""Canvas OCR 只返回符号和乱码时，不能把非空字符串误报为在线简历。"""
	assert is_meaningful_online_resume_text("1 1=\nƷ\n--\n��\n1\n-\nD") is False


def test_online_resume_validation_accepts_long_technical_ocr_with_minor_chinese_errors() -> None:
	"""长技术简历即使中文字段被 OCR 误识，也应依据稳定技术词通过正文门禁。"""
	text = (
		"欧浪鸿 Java 程能劳和良好的系没计 项目经验 Spring Boot REST 接口 MySQL "
		"后端开发 Maven MyBatis 计算机网络 操作系统 用户登录 支付 购物车 "
		"项目描述 教能经历 专业技 Java Spring MySQL "
	) * 4

	assert is_meaningful_online_resume_text(text) is True


def test_online_resume_preview_reads_exact_friend_resume_without_writing_file(monkeypatch) -> None:
	"""在线预览应读取当前真实候选人的正文，不下载、不落盘也不关闭 BOSS 弹窗。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: 2 if friend_id == 42 else None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_candidate_name_from_card", lambda _index: "张三")
	monkeypatch.setattr(client, "_online_resume_frame_count", lambda: 3)
	read_frame_indexes: list[int] = []
	monkeypatch.setattr(
		client,
		"_read_open_online_resume_preview",
		lambda *, min_frame_index: (
			read_frame_indexes.append(min_frame_index)
			or {"candidate_name": "", "resume_text": "张三 本科 软件工程 Java 项目经历"}
		),
	)
	responses = iter((True, True))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.open_online_resume_preview(friend_id=42)

	assert result == {
		"code": 0,
		"candidate_name": "张三",
		"resume_text": "张三 本科 软件工程 Java 项目经历",
	}
	assert read_frame_indexes == [0]


def test_online_resume_preview_does_not_fallback_to_another_candidate(monkeypatch) -> None:
	"""精确身份未命中时只能失败，不能点击列表第一人或读取错误简历。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda _friend_id: None)
	monkeypatch.setattr(client, "_eval", lambda _script: (_ for _ in ()).throw(AssertionError("不应点击其它候选人")))

	assert client.open_online_resume_preview(friend_id=42) == {"code": -1, "message": "candidate not found"}


def test_online_resume_preview_rejects_conversation_shell_as_resume(monkeypatch) -> None:
	"""在线预览不能把收藏、沟通进度等页面外壳当作简历正文。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: 2 if friend_id == 42 else None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_candidate_name_from_card", lambda _index: "谭钧译")
	monkeypatch.setattr(client, "_online_resume_frame_count", lambda: 0)
	responses = iter((True, True, {
		"candidate_name": "谭钧译",
		"resume_text": "收藏\n转发\n举报\n继续沟通\n沟通中\n同事沟通进度\n我的沟通进度",
	}))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))
	monkeypatch.setattr(client, "_ocr_open_online_resume_preview", lambda *, min_frame_index: "")

	assert client.open_online_resume_preview(friend_id=42) == {
		"code": -1,
		"message": "online resume text unavailable",
	}


def test_online_resume_preview_rejects_reused_frame_for_another_candidate(monkeypatch) -> None:
	"""复用预览仍显示上一位候选人时，姓名门禁必须阻止串读。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda _friend_id: 2)
	monkeypatch.setattr(client, "_candidate_name_from_card", lambda _index: "张三")
	monkeypatch.setattr(client, "_online_resume_frame_count", lambda: 1)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_eval", lambda _script: True)
	monkeypatch.setattr(
		client,
		"_read_open_online_resume_preview",
		lambda *, min_frame_index: {
			"candidate_name": "李四",
			"resume_text": "李四 本科 软件工程 Java 项目经历 工作经历",
		},
	)

	assert client.open_online_resume_preview(friend_id=42) == {
		"code": -1,
		"message": "online resume candidate mismatch",
	}


def test_online_resume_ocr_uses_latest_rendered_canvas_and_rapidocr(monkeypatch) -> None:
	"""多个预览层共存时，只能 OCR 最后打开且已经完成渲染的候选人 Canvas。"""
	client = object.__new__(BossRPAClient)
	evaluated_scripts: list[str] = []
	evaluation_count = 0

	def evaluate(script: str, **kwargs):
		nonlocal evaluation_count
		evaluation_count += 1
		evaluated_scripts.append(script)
		# 每次 CDP 查询必须快速返回；总等待放在 Python 侧分段轮询，避免超过
		# socket 的单命令预算。首次空结果模拟 BOSS 的异步 WASM 渲染。
		assert kwargs.get("await_promise") is None
		if evaluation_count == 1:
			return None
		return {"data": base64.b64encode(buffer.getvalue()).decode("ascii"), "width": 734, "height": 945}

	image = Image.new("RGB", (120, 160), "white")
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	monkeypatch.setattr(client, "_eval", evaluate)
	monkeypatch.setattr("boss_agent_cli.rpa.boss_client.time.sleep", lambda _seconds: None)

	class FakeRapidOCR:
		"""用固定识别结果隔离模型加载，测试生产代码的 OCR 编排契约。"""

		def __call__(self, _image):
			return [([], "本科 软件工程 Java 项目经历", 0.99)], None

	monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))

	assert client._ocr_open_online_resume_preview(min_frame_index=4) == "本科 软件工程 Java 项目经历"
	combined = "\n".join(evaluated_scripts)
	assert ".slice(minFrameIndex)" in combined
	assert "renderedFrames[renderedFrames.length - 1]" in combined
	assert "canvas.width > 300 || canvas.height > 150" in combined
	assert evaluation_count == 2


def test_online_resume_ocr_captures_visible_iframe_when_canvas_keeps_default_size(monkeypatch) -> None:
	"""Canvas 内部尺寸未更新时，应截图已放大的可见 iframe，不能空等后报失败。"""
	client = object.__new__(BossRPAClient)
	image = Image.new("RGB", (734, 945), "white")
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	captured: list[tuple[str, dict[str, object]]] = []

	monkeypatch.setattr(
		client,
		"_eval",
		lambda _script: {"clip": {"x": 418, "y": 0, "width": 734, "height": 945}},
	)

	def capture(method: str, params: dict[str, object]) -> dict[str, str]:
		captured.append((method, params))
		return {"data": base64.b64encode(buffer.getvalue()).decode("ascii")}

	monkeypatch.setattr(client, "_cdp_send", capture)

	class FakeRapidOCR:
		"""固定 OCR 结果，专门验证默认 Canvas 尺寸下的截图降级链路。"""

		def __call__(self, _image):
			return [([], "本科 软件工程 Java Spring Boot 项目经历", 0.99)], None

	monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))

	assert client._ocr_open_online_resume_preview(min_frame_index=0) == "本科 软件工程 Java Spring Boot 项目经历"
	assert captured[0][0] == "Page.captureScreenshot"
	assert captured[0][1]["captureBeyondViewport"] is True
	assert captured[0][1]["clip"] == {"x": 418.0, "y": 0.0, "width": 734.0, "height": 945.0, "scale": 1}


def test_read_conversation_profile_uses_targeted_header_snapshot(monkeypatch) -> None:
	"""读取指定会话的红框资料，不得退化为整页聊天文本。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: 2)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	responses = iter((True, {
		"profile_snapshot": True,
		"work_text": "2026.03-2026.06 致象商务服务 其他后端开发",
		"education_text": "2024-2028 广东工业大学 大数据管理与应用 本科",
		"communication_job": "Java",
		"expectation_text": "广州 Java 2-6K",
	}))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.read_conversation_profile(42)

	assert result["code"] == 0
	assert result["zpData"]["profile"]["expected_salary"] == "2-6K"
	assert "messages" not in result["zpData"]


def test_read_conversation_profile_fails_without_target_conversation(monkeypatch) -> None:
	"""找不到真实会话时必须受控失败，不能读取当前错误会话。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: None)

	result = client.read_conversation_profile(42)

	assert result["code"] == -1


def test_last_messages_preserves_the_recruiter_question_before_the_latest_candidate_reply(monkeypatch) -> None:
	"""短回答必须带上其前一条招聘方问题，供 AI 恢复首轮上下文。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: 3 if friend_id == 42 else None)
	monkeypatch.setattr(client, "_find_cards_by_friend_ids", lambda _friend_ids: {42: (0, 3)})
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	responses = iter((True, True, [
		{"role": "candidate", "text": "第一条候选人消息"},
		{"role": "recruiter", "text": "请问您通勤是否方便？"},
		{"role": "candidate", "text": "是的"},
	], {"education_text": "本科"}))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.last_messages([42, 99])

	assert result == {
		"code": 0,
		"zpData": {
			"lastMessageList": [{
				"friendId": 42,
				"text": "是的",
				"previousRecruiterText": "请问您通勤是否方便？",
				"profile": {"work_text": "", "education_text": "本科", "communication_job": "", "expectation_text": ""},
			}],
		},
	}


def test_last_messages_merges_consecutive_candidate_replies(monkeypatch) -> None:
	"""候选人分两条补充同一题时，AI 必须在一轮中看到完整回答。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_find_card_by_friend_id", lambda friend_id: 3 if friend_id == 42 else None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	responses = iter((True, [
		{"role": "recruiter", "text": "请问您目前在读本科吗？"},
		{"role": "candidate", "text": "是的"},
		{"role": "candidate", "text": "可以实习6个月"},
	], {"education_text": "本科"}))
	monkeypatch.setattr(client, "_eval", lambda _script: next(responses))

	result = client.last_messages([42])

	assert result["zpData"]["lastMessageList"] == [{
		"friendId": 42,
		"text": "是的\n可以实习6个月",
		"previousRecruiterText": "请问您目前在读本科吗？",
		"profile": {"work_text": "", "education_text": "本科", "communication_job": "", "expectation_text": ""},
	}]


def test_friend_list_reads_every_rendered_conversation_card(monkeypatch) -> None:
	"""沟通列表已渲染的后续候选人不能被前 20 条硬编码截断。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str):
		if "targetScrollTop" in script:
			return {"changed": True, "top": 0}
		assert "Math.min(cards.length, 20)" not in script
		assert "idx < cards.length" in script
		return [{
			"_idx": index,
			"_fid": str(index + 1),
			"name": f"候选人{index + 1}",
			"job": "Java 实习生",
			"company": "测试公司",
			"city": "广州",
			"time": "",
			"unread": "0",
			"geekId": "",
			"jobId": "",
			"securityId": "",
		} for index in range(21)]

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client.friend_list()

	assert len(result["zpData"]["friendList"]) == 21
	assert result["zpData"]["friendList"][-1]["friendId"] == 21


def test_friend_list_treats_missing_unread_badge_as_zero(monkeypatch) -> None:
	"""没有未读徽标不等于有新候选人消息，轮询不得因此打开全部会话。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_move_friend_list_viewport", lambda _page: None)
	monkeypatch.setattr(client, "_eval", lambda _script: [{
		"_idx": 0, "_fid": "42", "name": "候选人", "job": "Java", "company": "",
		"city": "广州", "time": "", "unread": "", "geekId": "", "jobId": "", "securityId": "",
	}])

	result = client.friend_list()

	assert result["zpData"]["friendList"][0]["unreadMsgCount"] == 0


def test_friend_list_moves_the_virtual_list_for_later_pages(monkeypatch) -> None:
	"""请求后续页时必须滚动虚拟会话列表，不能重复读取当前窗口。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	scripts: list[str] = []

	def evaluate(script: str):
		scripts.append(script)
		if "targetScrollTop" in script:
			return {"changed": True, "top": 729}
		return []

	monkeypatch.setattr(client, "_eval", evaluate)

	client.friend_list(page=2)

	assert any("targetScrollTop" in script for script in scripts)


def test_friend_list_uses_a_full_render_window_for_later_pages(monkeypatch) -> None:
	"""虚拟列表分页至少跨越当前渲染窗口，避免相邻页只返回同一批卡片。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str):
		if "targetScrollTop" in script:
			assert "* 4" in script
			return {"changed": True, "top": 2916}
		return []

	monkeypatch.setattr(client, "_eval", evaluate)

	client.friend_list(page=2)


def test_fast_conversation_snapshot_collects_virtualized_unread_cards_in_bounded_chunks(monkeypatch) -> None:
	"""500 人虚拟列表必须分块读取，单个 CDP 命令不能承担整页滚动。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	scripts: list[str] = []

	def evaluate(script: str, **_kwargs):
		scripts.append(script)
		if "bossAgentSnapshotMeta" in script:
			return {"originalTop": 15, "maxTop": 900, "step": 300}
		if "bossAgentSnapshotChunk" in script:
			return {"items": [{"friendId": 9001, "name": "后页未读", "unreadMsgCount": 1}]}
		if "bossAgentSnapshotRestore" in script:
			return True
		raise AssertionError(f"unexpected script: {script[:80]}")

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client.fast_conversation_snapshot()

	assert result["zpData"]["friendList"] == [{"friendId": 9001, "name": "后页未读", "unreadMsgCount": 1}]
	assert sum("bossAgentSnapshotChunk" in script for script in scripts) == 4
	assert any("bossAgentSnapshotRestore" in script for script in scripts)


def test_fast_conversation_snapshot_can_return_full_ordered_snapshot(monkeypatch) -> None:
	"""首轮同步按滚动顺序合并短窗口，不能因拆分丢失候选人顺序。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	scripts: list[str] = []

	def evaluate(script: str, **_kwargs):
		scripts.append(script)
		if "bossAgentSnapshotMeta" in script:
			return {"originalTop": 0, "maxTop": 300, "step": 300}
		if "bossAgentSnapshotChunk" in script:
			return {"items": [
				{"friendId": 9001, "name": "首屏", "unreadMsgCount": 0},
				{"friendId": 9002, "name": "后屏", "unreadMsgCount": 0},
			]}
		if "bossAgentSnapshotRestore" in script:
			return True
		raise AssertionError(f"unexpected script: {script[:80]}")

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client.fast_conversation_snapshot(include_all=True)

	assert result["zpData"]["friendList"] == [
		{"friendId": 9001, "name": "首屏", "unreadMsgCount": 0},
		{"friendId": 9002, "name": "后屏", "unreadMsgCount": 0},
	]
	assert sum("bossAgentSnapshotChunk" in script for script in scripts) == 2
	assert any("includeAll = true" in script for script in scripts)


def test_conversation_snapshot_bounds_uses_four_viewports_per_chunk(monkeypatch) -> None:
	"""40 张虚拟卡片有足够缓冲区，扫描步长应跨四个视口减少重复读取。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []

	def evaluate(script: str):
		scripts.append(script)
		return {"originalTop": 0, "maxTop": 0, "step": 1}

	monkeypatch.setattr(client, "_eval", evaluate)

	assert client._conversation_snapshot_bounds() == (0, 0, 1)
	assert "list.clientHeight * 4" in scripts[0]


def test_batch_candidate_locator_scans_virtual_list_in_bounded_chunks(monkeypatch) -> None:
	"""对话处理的目标定位同样不能把 500 人滚动塞进一条 CDP Promise。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []

	def evaluate(script: str, **_kwargs):
		scripts.append(script)
		if "bossAgentSnapshotMeta" in script:
			return {"originalTop": 0, "maxTop": 600, "step": 300}
		if "bossAgentTargetChunk" in script:
			if "list.scrollTop = 300" in script:
				return {"positions": [{"friendId": 42, "scrollTop": 300, "index": 2}]}
			return {"positions": []}
		if "bossAgentSnapshotRestore" in script:
			return True
		raise AssertionError(f"unexpected script: {script[:80]}")

	monkeypatch.setattr(client, "_eval", evaluate)

	assert client._find_cards_by_friend_ids([42]) == {42: (300, 2)}
	# 找齐目标后立即停止，避免再扫描不相关的尾部窗口。
	assert sum("bossAgentTargetChunk" in script for script in scripts) == 2
	assert any("bossAgentSnapshotRestore" in script for script in scripts)


def test_unread_projection_uses_semantic_unread_selectors_only(monkeypatch) -> None:
	"""通用 badge 可能是其它状态徽标，不能把它当成候选人未读消息。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)
	monkeypatch.setattr(client, "_move_friend_list_viewport", lambda _page: None)
	scripts: list[str] = []

	def evaluate(script: str, **_kwargs):
		scripts.append(script)
		return []

	monkeypatch.setattr(client, "_eval", evaluate)

	client.friend_list()
	client.fast_conversation_snapshot()

	combined = "\n".join(scripts)
	assert "[class*='badge']" not in combined
	assert '[class*="badge"]' not in combined
	assert "unread-count" in combined


def test_unread_helper_can_be_evaluated_repeatedly_in_the_same_boss_page() -> None:
	"""CDP 共用页面上下文时，未读辅助函数不能以 const 重复声明。"""
	from boss_agent_cli.rpa.boss_client import _UNREAD_COUNT_JS

	assert "var __bossAgentReadUnreadCount" in _UNREAD_COUNT_JS
	assert "const readUnreadCount" not in _UNREAD_COUNT_JS


def test_find_card_by_friend_id_searches_virtual_list_beyond_current_window(monkeypatch) -> None:
	"""目标会话不在当前虚拟窗口时，定位器必须滚动后重新查找。"""
	client = object.__new__(BossRPAClient)
	moved = False
	restored_after_found = False

	def evaluate(script: str):
		nonlocal moved, restored_after_found
		if "const targetFriendId" in script:
			return 3 if moved else -1
		if "scrollHeight" in script and "clientHeight" in script:
			return {"top": 1600, "height": 2400, "viewport": 400}
		if "targetScrollTop" in script:
			moved = True
			return True
		if "list.scrollTop = 1600" in script:
			restored_after_found = True
			return True
		return -1

	monkeypatch.setattr(client, "_eval", evaluate)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	assert client._find_card_by_friend_id(690533787) == 3
	assert restored_after_found is False


def test_find_cards_by_friend_ids_shares_bounded_scan_for_a_batch(monkeypatch) -> None:
	"""批量读取消息时，多个目标共用一轮分块扫描，不能退回逐人长扫描。"""
	client = object.__new__(BossRPAClient)
	scripts: list[str] = []

	def evaluate(script: str, **kwargs):
		scripts.append(script)
		if "bossAgentSnapshotMeta" in script:
			assert kwargs.get("await_promise") is None
			return {"originalTop": 0, "maxTop": 800, "step": 800}
		if "bossAgentTargetChunk" in script:
			assert kwargs.get("await_promise") is True
			if "list.scrollTop = 800" in script:
				return {
					"positions": [
						{"friendId": 42, "scrollTop": 800, "index": 2},
						{"friendId": 43, "scrollTop": 800, "index": 3},
					]
				}
			return {"positions": []}
		if "bossAgentSnapshotRestore" in script:
			return True
		raise AssertionError(f"unexpected script: {script[:80]}")

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client._find_cards_by_friend_ids([42, 43])

	assert result == {42: (800, 2), 43: (800, 3)}
	assert sum("bossAgentTargetChunk" in script for script in scripts) == 2
	assert any("bossAgentSnapshotRestore" in script for script in scripts)
