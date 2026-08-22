"""Android visual recognition platform adapter.

Implements ``RecruiterPlatform`` ABC by controlling a BOSS Zhipin
Android app through ADB + OCR instead of calling reverse-engineered APIs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.mobile.adb_controller import ADBController
from boss_agent_cli.mobile.boss_app import BossApp
from boss_agent_cli.mobile.ocr import OCRDriver
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
	pass


class AndroidRecruiterPlatform(RecruiterPlatform):
	"""BOSS 直聘 Android 视觉识别平台适配器。

	不调用逆向 API，而是通过 ADB 控制手机 App，截图后用 OCR
	识别界面内容并模拟人工点击。风控风险远低于 API 级别操作。
	"""

	name = "android-recruiter"
	display_name = "Android 视觉识别平台"
	base_url = ""

	def __init__(self, adb: ADBController | None = None, ocr: OCRDriver | None = None) -> None:
		self._adb = adb or ADBController()
		self._ocr = ocr or OCRDriver()
		self._app = BossApp(self._adb, self._ocr)

	# ------------------------------------------------------------------
	# Envelope (visual platform doesn't have an API envelope)
	# ------------------------------------------------------------------

	def is_success(self, response: dict[str, Any]) -> bool:
		"""Visual platform considers any non-empty dict as success."""
		return bool(response) and response.get("_ok", True)

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("_data", response)

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		code = str(response.get("_error_code") or "UNKNOWN")
		msg = str(response.get("_error") or "操作失败")
		return (code, msg)

	# ------------------------------------------------------------------
	# Conversation list
	# ------------------------------------------------------------------

	def friend_list(
		self, page: int = 1, label_id: int = 0, job_id: str | None = None,
	) -> dict[str, Any]:
		"""OCR the conversation list page."""
		try:
			self._app.conversations.ensure_on_page()
			# Scroll to page if needed (each page ~10 cards visible)
			if page > 1:
				for _ in range(page - 1):
					self._app.conversations.scroll_down()
			cards = self._app.conversations.get_visible_cards()
			return {
				"_ok": True,
				"_data": cards,
				"code": 0,
				"zpData": {"friendList": cards},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""OCR the 'new greetings' tab."""
		try:
			self._app.go_to_home()
			time = __import__("time")
			time.sleep(1.0)
			# The '新招呼' is accessible from the messages tab
			cards = self._app.conversations.get_visible_cards()
			return {
				"_ok": True,
				"_data": cards,
				"code": 0,
				"zpData": {"list": cards},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	def greet_rec_list(
		self, page: int = 1, job_id: str | None = None,
	) -> dict[str, Any]:
		"""OCR the recommendation list."""
		try:
			self._app.recommendations.ensure_on_page()
			if page > 1:
				for _ in range(page - 1):
					self._app.recommendations.scroll_down()
			cards = self._app.recommendations.get_visible_cards()
			return {
				"_ok": True,
				"_data": cards,
				"code": 0,
				"zpData": {"geekList": cards},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	# ------------------------------------------------------------------
	# Candidate search
	# ------------------------------------------------------------------

	def search_geeks(
		self,
		query: str,
		*,
		city: str | None = None,
		page: int = 1,
		job_id: str | None = None,
		experience: str | None = None,
		degree: str | None = None,
		age: str | None = None,
		school_level: str | None = None,
		activeness: str | None = None,
		source: str | None = None,
		select: bool = False,
		salary: str | None = None,
	) -> dict[str, Any]:
		"""Search candidates — limited implementation via visual.

		Full search with all filters requires navigating the search UI
		which is complex to automate visually. This provides a basic
		search via the recommendation page (which already filters by job).
		"""
		return self.greet_rec_list(page=page, job_id=job_id)

	# ------------------------------------------------------------------
	# Candidate resume
	# ------------------------------------------------------------------

	def view_geek(
		self, geek_id: str, job_id: str, security_id: str | None = None,
	) -> dict[str, Any]:
		"""View a candidate's online resume via visual automation.

		Note: The visual approach can't look up candidates by ID directly.
		Instead, this navigates to the recommendations list and opens
		the first candidate. For targeted lookup, use conversation-based
		navigation first.
		"""
		try:
			self._app.recommendations.ensure_on_page()
			resume_page = self._app.recommendations.view_resume(0)
			full_text = resume_page.read_full_resume()
			return {
				"_ok": True,
				"_data": {"resume_text": full_text},
				"code": 0,
				"zpData": {"resume_text": full_text},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	# ------------------------------------------------------------------
	# Chat
	# ------------------------------------------------------------------

	def chat_history(
		self, gid: int, *, count: int = 20, max_msg_id: int | None = None,
	) -> dict[str, Any]:
		"""Read chat history from the current conversation."""
		try:
			chat_page = self._app.chat
			messages = chat_page.get_chat_history(max_scrolls=min(count // 5, 10))
			return {
				"_ok": True,
				"_data": {"messages": messages},
				"code": 0,
				"zpData": {"messages": messages},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		"""Send a chat message."""
		try:
			self._app.chat.send_message(content)
			return {"_ok": True, "code": 0}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		"""Send a message by friend ID — opens the conversation first."""
		try:
			self._app.conversations.ensure_on_page()
			# Open first conversation (ID-based targeting not supported visually)
			self._app.conversations.open_conversation(0)
			self._app.chat.send_message(content)
			return {"_ok": True, "code": 0}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	# ------------------------------------------------------------------
	# Job management
	# ------------------------------------------------------------------

	def list_jobs(self) -> dict[str, Any]:
		"""OCR the job list from the profile page."""
		try:
			self._app.go_to_tab(self._app.layout.tab_profile)
			time = __import__("time")
			time.sleep(1.0)
			jobs = self._app.profile.get_visible_jobs()
			return {
				"_ok": True,
				"_data": jobs,
				"code": 0,
				"zpData": {"list": jobs},
			}
		except Exception as exc:
			return {"_ok": False, "_error": str(exc), "code": -1}

	# ------------------------------------------------------------------
	# Optional: friend list detail, last messages, etc.
	# ------------------------------------------------------------------

	def last_messages(self, gid: int) -> dict[str, Any]:
		return self.chat_history(gid, count=5)

	def chat_geek_info(self, geek_id: str, job_id: str) -> dict[str, Any]:
		"""Get candidate info — opens resume page."""
		return self.view_geek(geek_id, job_id)

	# ------------------------------------------------------------------
	# Resource management
	# ------------------------------------------------------------------

	def close(self) -> None:
		"""No persistent resources to close."""
		pass

	def __enter__(self) -> "AndroidRecruiterPlatform":
		return self

	def __exit__(self, *args: Any) -> None:
		self.close()
