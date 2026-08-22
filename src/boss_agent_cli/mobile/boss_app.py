"""BOSS Zhipin Android app — page-level operations via visual recognition.

Each class represents one screen/page of the BOSS app and provides
methods to locate elements and perform actions using OCR + ADB.
"""

from __future__ import annotations

import time
from typing import Any

from boss_agent_cli.mobile.adb_controller import ADBController
from boss_agent_cli.mobile.ocr import OCRBox, OCRDriver
from boss_agent_cli.mobile.screen import ScreenLayout, ScreenRegion

# BOSS Zhipin Android app package
BOSS_PACKAGE = "com.hpbr.bosszhipin"
# Main activity
BOSS_MAIN_ACTIVITY = "com.hpbr.bosszhipin.module.main.MainActivity"


class BossApp:
	"""Top-level entry point for controlling the BOSS Android app.

	Usage::

		adb = ADBController()
		ocr = OCRDriver()
		app = BossApp(adb, ocr)
		app.ensure_foreground()
		conversations = app.conversations.get_list()
	"""

	def __init__(self, adb: ADBController, ocr: OCRDriver) -> None:
		self._adb = adb
		self._ocr = ocr
		self._layout = ScreenLayout(*adb.screen_size)

	@property
	def layout(self) -> ScreenLayout:
		"""Refresh and return the current screen layout."""
		self._layout = ScreenLayout(*self._adb.screen_size)
		return self._layout

	# ------------------------------------------------------------------
	# App lifecycle
	# ------------------------------------------------------------------

	def ensure_foreground(self) -> None:
		"""Bring the BOSS app to the foreground, launching if needed."""
		if not self._adb.is_app_running(BOSS_PACKAGE):
			self._adb.launch_app(BOSS_PACKAGE, BOSS_MAIN_ACTIVITY)
			time.sleep(2.0)  # Wait for app to start
			self._adb.wake_up()
		else:
			# App is already running, just bring to front
			self._adb.launch_app(BOSS_PACKAGE)

	def restart(self) -> None:
		"""Force-stop and relaunch the app."""
		self._adb.force_stop(BOSS_PACKAGE)
		time.sleep(1.0)
		self.ensure_foreground()

	# ------------------------------------------------------------------
	# Page accessors
	# ------------------------------------------------------------------

	@property
	def home(self) -> "BossHomePage":
		return BossHomePage(self._adb, self._ocr, self)

	@property
	def conversations(self) -> "ConversationListPage":
		return ConversationListPage(self._adb, self._ocr, self)

	@property
	def recommendations(self) -> "RecommendationListPage":
		return RecommendationListPage(self._adb, self._ocr, self)

	@property
	def chat(self) -> "ChatPage":
		return ChatPage(self._adb, self._ocr, self)

	@property
	def resume(self) -> "ResumePage":
		return ResumePage(self._adb, self._ocr, self)

	@property
	def login(self) -> "LoginPage":
		return LoginPage(self._adb, self._ocr, self)

	@property
	def profile(self) -> "ProfilePage":
		return ProfilePage(self._adb, self._ocr, self)

	# ------------------------------------------------------------------
	# Navigation helpers
	# ------------------------------------------------------------------

	def go_to_tab(self, tab: ScreenRegion) -> None:
		"""Tap a bottom tab."""
		layout = self.layout
		x, y = layout.tap_center(tab)
		self._adb.tap(x, y)
		time.sleep(0.8)

	def go_to_conversations(self) -> None:
		self.go_to_tab(self.layout.tab_contacts)

	def go_to_recommendations(self) -> None:
		self.go_to_tab(self.layout.tab_recommend)

	def go_to_home(self) -> None:
		self.go_to_tab(self.layout.tab_messages)

	def take_screenshot(self) -> bytes:
		return self._adb.screenshot()

	def find_and_tap(self, text: str, *, timeout: float = 5.0, region: ScreenRegion | None = None, **kwargs: Any) -> OCRBox | None:
		"""Find *text* on the current screen and tap it."""
		box = self._ocr.wait_for_text(self._adb, text, timeout=timeout, region=region, **kwargs)
		if box is not None:
			self._adb.tap(*box.center)
		return box

	def scroll_list(self, region: ScreenRegion, count: int = 1) -> None:
		"""Scroll within a list region."""
		layout = self.layout
		px, py, pw, ph = region.to_pixels(layout.w, layout.h)
		for _ in range(count):
			self._adb.swipe(
				px + pw // 2, py + int(ph * 0.7),
				px + pw // 2, py + int(ph * 0.3),
				duration_ms=200,
			)
			time.sleep(0.6)


class _BasePage:
	"""Base for page-level operations."""

	def __init__(self, adb: ADBController, ocr: OCRDriver, app: BossApp) -> None:
		self._adb = adb
		self._ocr = ocr
		self._app = app

	@property
	def layout(self) -> ScreenLayout:
		return self._app.layout

	def screenshot(self) -> bytes:
		return self._adb.screenshot()

	def ocr_region(self, region: ScreenRegion) -> str:
		"""Read all text in a region."""
		img = self.screenshot()
		layout = self.layout
		return self._ocr.read_text_in_region(img, region, layout.w, layout.h)

	def find_and_tap(self, text: str, **kwargs: Any) -> OCRBox | None:
		return self._app.find_and_tap(text, **kwargs)


# ------------------------------------------------------------------
# Home / Main Page
# ------------------------------------------------------------------

class BossHomePage(_BasePage):
	"""BOSS app home / main page."""

	def is_on_home(self) -> bool:
		"""Check if we're on the home page."""
		img = self.screenshot()
		return self._ocr.has_text(img, "消息", region=self.layout.bottom_nav)

	def open_conversations(self) -> "ConversationListPage":
		self._app.go_to_conversations()
		return self._app.conversations


# ------------------------------------------------------------------
# Conversation List Page
# ------------------------------------------------------------------

class ConversationListPage(_BasePage):
	"""沟通列表页面 — shows list of conversation cards."""

	def ensure_on_page(self) -> None:
		"""Navigate to the conversation list if not already there."""
		img = self.screenshot()
		layout = self.layout
		# Try to find conversation-related text
		if self._ocr.has_text(img, "沟通", region=layout.top_bar):
			return
		self._app.go_to_conversations()
		time.sleep(1.0)

	def get_visible_cards(self) -> list[dict[str, Any]]:
		"""OCR the visible conversation cards and return structured data."""
		img = self.screenshot()
		layout = self.layout
		texts = self._ocr.read_list_items(
			img, layout.conversation_list_area,
			item_height=layout.conversation_card_height,
			screen_w=layout.w, screen_h=layout.h,
		)
		cards: list[dict[str, Any]] = []
		for i, text in enumerate(texts):
			if not text.strip():
				continue
			cards.append({
				"_index": i,
				"_text": text,
				"candidate_name": _extract_first_line(text),
				"position": _extract_field(text, 2),
				"company": _extract_field(text, 3),
			})
		return cards

	def scroll_down(self) -> None:
		self._app.scroll_list(self.layout.conversation_list_area)

	def open_conversation(self, index: int = 0) -> "ChatPage":
		"""Tap on the Nth visible conversation card."""
		layout = self.layout
		card_top = layout.conversation_list_area.y1 + index * layout.conversation_card_height
		card_center_y = card_top + layout.conversation_card_height / 2
		x = int(layout.w * 0.5)
		y = int(card_center_y * layout.h)
		self._adb.tap(x, y)
		time.sleep(1.0)
		return self._app.chat


# ------------------------------------------------------------------
# Recommendation List Page
# ------------------------------------------------------------------

class RecommendationListPage(_BasePage):
	"""推荐牛人列表页."""

	def ensure_on_page(self) -> None:
		self._app.go_to_recommendations()
		time.sleep(1.0)

	def get_visible_cards(self) -> list[dict[str, Any]]:
		"""OCR the visible recommendation cards."""
		img = self.screenshot()
		layout = self.layout
		texts = self._ocr.read_list_items(
			img, layout.recommendation_list_area,
			item_height=layout.recommendation_card_height,
			screen_w=layout.w, screen_h=layout.h,
		)
		cards: list[dict[str, Any]] = []
		for i, text in enumerate(texts):
			if not text.strip():
				continue
			cards.append({
				"_index": i,
				"_text": text,
				"candidate_name": _extract_first_line(text),
			})
		return cards

	def scroll_down(self) -> None:
		self._app.scroll_list(self.layout.recommendation_list_area)

	def view_resume(self, index: int = 0) -> "ResumePage":
		"""Tap the Nth recommendation card to view resume."""
		layout = self.layout
		card_top = layout.recommendation_list_area.y1 + index * layout.recommendation_card_height
		card_center_y = card_top + layout.recommendation_card_height / 2
		x = int(layout.w * 0.5)
		y = int(card_center_y * layout.h)
		self._adb.tap(x, y)
		time.sleep(0.8)
		# Now tap "在线简历" button if visible
		img = self.screenshot()
		box = self._ocr.find_best(img, ["在线简历", "查看简历", "resume"])
		if box:
			self._adb.tap(*box.center)
			time.sleep(1.0)
		return self._app.resume


# ------------------------------------------------------------------
# Chat / Candidate Detail Page
# ------------------------------------------------------------------

class ChatPage(_BasePage):
	"""聊天页面 — chat with a candidate."""

	def get_visible_messages(self) -> list[dict[str, str]]:
		"""Read visible chat messages."""
		img = self.screenshot()
		layout = self.layout
		text = self._ocr.read_text_in_region(img, layout.chat_message_list, layout.w, layout.h)
		messages: list[dict[str, str]] = []
		for line in text.split("\n"):
			line = line.strip()
			if line:
				messages.append({"text": line})
		return messages

	def get_chat_history(self, max_scrolls: int = 5) -> list[dict[str, str]]:
		"""Read chat history by scrolling up repeatedly."""
		all_msgs: list[dict[str, str]] = []
		seen: set[str] = set()
		for _ in range(max_scrolls):
			msgs = self.get_visible_messages()
			for m in msgs:
				if m["text"] not in seen:
					seen.add(m["text"])
					all_msgs.append(m)
			# Scroll up to see older messages
			layout = self.layout
			px, py, pw, ph = layout.chat_message_list.to_pixels(layout.w, layout.h)
			self._adb.swipe(px + pw // 2, py + int(ph * 0.5), px + pw // 2, py + int(ph * 0.9), duration_ms=200)
			time.sleep(0.5)
		return all_msgs

	def send_message(self, content: str) -> None:
		"""Type and send a message."""
		layout = self.layout
		# Tap the input field
		x, y = layout.tap_center(layout.chat_input_area)
		self._adb.tap(x, y)
		time.sleep(0.3)
		# Type the message
		self._adb.input_text(content)
		time.sleep(0.3)
		# Tap send button
		x, y = layout.tap_center(layout.chat_send_button)
		self._adb.tap(x, y)
		time.sleep(0.5)

	def view_resume(self) -> "ResumePage":
		"""Tap the '查看在线简历' button."""
		self.find_and_tap("在线简历", timeout=3.0, region=self.layout.view_resume_button)
		return self._app.resume

	def go_back(self) -> None:
		self._adb.press_back()
		time.sleep(0.5)


# ------------------------------------------------------------------
# Resume / Candidate Detail Page
# ------------------------------------------------------------------

class ResumePage(_BasePage):
	"""在线简历页面."""

	def read_full_resume(self, max_scrolls: int = 10) -> str:
		"""Read the entire resume by scrolling down."""
		all_text: list[str] = []
		layout = self.layout
		for i in range(max_scrolls):
			img = self.screenshot()
			text = self._ocr.read_text_in_region(img, layout.resume_content_area, layout.w, layout.h)
			all_text.append(text)
			# Scroll down
			px, py, pw, ph = layout.resume_content_area.to_pixels(layout.w, layout.h)
			self._adb.swipe(px + pw // 2, py + int(ph * 0.8), px + pw // 2, py + int(ph * 0.3), duration_ms=300)
			time.sleep(0.7)
		return "\n".join(all_text)

	def get_section(self, section_name: str) -> str | None:
		"""Find a specific resume section by name."""
		img = self.screenshot()
		layout = self.layout
		hits = self._ocr.find_text(img, section_name, region=layout.resume_section_headers, screen_w=layout.w, screen_h=layout.h)
		if not hits:
			return None
		# Read from the section header downwards, approximately 20% of screen
		section_region = ScreenRegion(0.05, hits[0].y / layout.h, 0.95, min(1.0, (hits[0].y + hits[0].h) / layout.h + 0.25))
		text = self._ocr.read_text_in_region(img, section_region, layout.w, layout.h)
		return text

	def go_back(self) -> None:
		self._adb.press_back()
		time.sleep(0.5)


# ------------------------------------------------------------------
# Login Page
# ------------------------------------------------------------------

class LoginPage(_BasePage):
	"""BOSS App login page."""

	def is_on_login(self) -> bool:
		"""Check if we're on the login page."""
		img = self.screenshot()
		return self._ocr.has_text(img, "手机号登录") or self._ocr.has_text(img, "验证码登录")

	def enter_phone(self, phone: str) -> None:
		"""Enter phone number."""
		layout = self.layout
		x, y = layout.tap_center(layout.login_phone_input)
		self._adb.tap(x, y)
		time.sleep(0.3)
		self._adb.input_text(phone)

	def get_verification_code(self) -> None:
		"""Tap '获取验证码' button."""
		layout = self.layout
		x, y = layout.tap_center(layout.login_get_code_button)
		self._adb.tap(x, y)

	def enter_code_and_submit(self, code: str) -> None:
		"""Enter SMS code and submit."""
		layout = self.layout
		x, y = layout.tap_center(layout.login_code_input)
		self._adb.tap(x, y)
		time.sleep(0.3)
		self._adb.input_text(code)
		time.sleep(0.3)
		x, y = layout.tap_center(layout.login_submit_button)
		self._adb.tap(x, y)
		time.sleep(0.3)

	def accept_agreement_if_needed(self) -> None:
		"""Check and accept the user agreement."""
		img = self.screenshot()
		if self._ocr.has_text(img, "同意", region=self.layout.login_agreement_checkbox):
			x, y = self.layout.tap_center(self.layout.login_agreement_checkbox)
			self._adb.tap(x, y)
			time.sleep(0.2)


# ------------------------------------------------------------------
# Profile / Job Management Page
# ------------------------------------------------------------------

class ProfilePage(_BasePage):
	"""'我的' 页面 — profile and job management."""

	def ensure_on_page(self) -> None:
		self._app.go_to_tab(self.layout.tab_profile)
		time.sleep(0.8)

	def open_job_list(self) -> None:
		"""Navigate to the job list."""
		self.find_and_tap("职位管理", timeout=3.0)

	def get_visible_jobs(self) -> list[dict[str, Any]]:
		"""Read visible job listings."""
		img = self.screenshot()
		jobs: list[dict[str, Any]] = []
		# Search for job title patterns
		hits = self._ocr.find_text(img, "正在招聘", screen_w=self.layout.w, screen_h=self.layout.h)
		for hit in hits:
			jobs.append({"title": hit.text, "y": hit.y})
		return jobs


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _extract_first_line(text: str) -> str:
	"""Extract the first non-empty line of text."""
	lines = [l.strip() for l in text.replace("\n", " ").split("  ") if l.strip()]
	return lines[0] if lines else ""


def _extract_field(text: str, index: int) -> str:
	"""Extract the Nth field from text."""
	parts = [p.strip() for p in text.replace("\n", " ").split("  ") if p.strip()]
	return parts[index] if index < len(parts) else ""
