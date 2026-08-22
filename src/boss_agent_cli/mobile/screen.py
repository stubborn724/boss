"""Screen coordinate regions for BOSS Zhipin Android app.

Defines relative coordinate mappings for key UI elements so the OCR and
tap logic works across different screen sizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScreenRegion:
	"""A rectangular region on the screen, expressed as fractions of screen size.

	Attributes:
	  x1, y1: top-left corner (0.0 - 1.0 fraction of width/height).
	  x2, y2: bottom-right corner (0.0 - 1.0 fraction of width/height).
	"""

	x1: float
	y1: float
	x2: float
	y2: float

	@property
	def center_x(self) -> float:
		return (self.x1 + self.x2) / 2

	@property
	def center_y(self) -> float:
		return (self.y1 + self.y2) / 2

	def to_pixels(self, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
		"""Convert to pixel coordinates: (x, y, width, height)."""
		return (
			int(self.x1 * screen_w),
			int(self.y1 * screen_h),
			int((self.x2 - self.x1) * screen_w),
			int((self.y2 - self.y1) * screen_h),
		)

	def to_center_pixels(self, screen_w: int, screen_h: int) -> tuple[int, int]:
		"""Return the center point in pixels."""
		return (
			int(self.center_x * screen_w),
			int(self.center_y * screen_h),
		)


class ScreenLayout:
	"""Predefined screen regions for BOSS Zhipin Android app.

	All coordinates are fractions of screen width/height (0.0 - 1.0).
	These were measured on a 1080x2400 device; the fraction-based
	approach makes them work across different resolutions.
	"""

	def __init__(self, screen_w: int, screen_h: int) -> None:
		self.w = screen_w
		self.h = screen_h

	# ------------------------------------------------------------------
	# System / Navigation
	# ------------------------------------------------------------------

	@property
	def status_bar(self) -> ScreenRegion:
		"""Android status bar area (time, battery, etc.)."""
		return ScreenRegion(0, 0, 1.0, 0.04)

	@property
	def top_bar(self) -> ScreenRegion:
		"""App top bar (title, back button)."""
		return ScreenRegion(0, 0.03, 1.0, 0.09)

	@property
	def bottom_nav(self) -> ScreenRegion:
		"""Android navigation bar or app bottom tab bar."""
		return ScreenRegion(0, 0.92, 1.0, 1.0)

	# ------------------------------------------------------------------
	# BOSS App: Bottom Tab Bar
	# ------------------------------------------------------------------

	@property
	def tab_messages(self) -> ScreenRegion:
		"""'消息' tab (leftmost)."""
		return ScreenRegion(0.02, 0.94, 0.20, 0.99)

	@property
	def tab_contacts(self) -> ScreenRegion:
		"""'联系人' or '沟通' tab."""
		return ScreenRegion(0.22, 0.94, 0.40, 0.99)

	@property
	def tab_recommend(self) -> ScreenRegion:
		"""'推荐' tab (third)."""
		return ScreenRegion(0.42, 0.94, 0.60, 0.99)

	@property
	def tab_jobs(self) -> ScreenRegion:
		"""'职位' tab."""
		return ScreenRegion(0.62, 0.94, 0.80, 0.99)

	@property
	def tab_profile(self) -> ScreenRegion:
		"""'我的' tab (rightmost)."""
		return ScreenRegion(0.82, 0.94, 1.0, 0.99)

	# ------------------------------------------------------------------
	# Conversation List Page
	# ------------------------------------------------------------------

	@property
	def conversation_list_area(self) -> ScreenRegion:
		"""Scrollable area containing conversation cards."""
		return ScreenRegion(0.02, 0.09, 0.98, 0.92)

	@property
	def conversation_card_height(self) -> float:
		"""Height of one conversation card as fraction of screen height."""
		return 0.10

	@property
	def first_conversation_card(self) -> ScreenRegion:
		"""First conversation card in the list."""
		top = 0.10
		return ScreenRegion(0.02, top, 0.98, top + 0.10)

	# ------------------------------------------------------------------
	# Recommendation List Page
	# ------------------------------------------------------------------

	@property
	def recommendation_list_area(self) -> ScreenRegion:
		"""Scrollable area for recommendation cards."""
		return ScreenRegion(0.02, 0.12, 0.98, 0.92)

	@property
	def recommendation_card_height(self) -> float:
		"""Height of one recommendation card."""
		return 0.16

	@property
	def job_filter_tab(self) -> ScreenRegion:
		"""Job filter tabs at top of recommendation page."""
		return ScreenRegion(0.02, 0.07, 0.98, 0.12)

	# ------------------------------------------------------------------
	# Chat / Candidate Detail
	# ------------------------------------------------------------------

	@property
	def chat_input_area(self) -> ScreenRegion:
		"""Chat message input field."""
		return ScreenRegion(0.05, 0.88, 0.85, 0.93)

	@property
	def chat_send_button(self) -> ScreenRegion:
		"""Send message button."""
		return ScreenRegion(0.85, 0.88, 0.98, 0.93)

	@property
	def chat_message_list(self) -> ScreenRegion:
		"""Scrollable message area."""
		return ScreenRegion(0.01, 0.07, 0.99, 0.87)

	@property
	def candidate_name_area(self) -> ScreenRegion:
		"""Candidate name at top of detail/chat page."""
		return ScreenRegion(0.15, 0.04, 0.85, 0.09)

	@property
	def view_resume_button(self) -> ScreenRegion:
		"""'查看在线简历' button in candidate detail."""
		return ScreenRegion(0.15, 0.12, 0.85, 0.18)

	# ------------------------------------------------------------------
	# Resume View Page
	# ------------------------------------------------------------------

	@property
	def resume_content_area(self) -> ScreenRegion:
		"""Scrollable resume content."""
		return ScreenRegion(0.02, 0.08, 0.98, 0.92)

	@property
	def resume_section_headers(self) -> ScreenRegion:
		"""Area where section headers (工作经历, 教育背景, etc.) appear."""
		return ScreenRegion(0.05, 0.08, 0.95, 0.92)

	# ------------------------------------------------------------------
	# Login Page
	# ------------------------------------------------------------------

	@property
	def login_phone_input(self) -> ScreenRegion:
		"""Phone number input field."""
		return ScreenRegion(0.10, 0.35, 0.90, 0.42)

	@property
	def login_code_input(self) -> ScreenRegion:
		"""SMS verification code input field."""
		return ScreenRegion(0.10, 0.45, 0.65, 0.52)

	@property
	def login_get_code_button(self) -> ScreenRegion:
		"""'获取验证码' button."""
		return ScreenRegion(0.68, 0.45, 0.90, 0.52)

	@property
	def login_submit_button(self) -> ScreenRegion:
		"""Login / submit button."""
		return ScreenRegion(0.10, 0.56, 0.90, 0.63)

	@property
	def login_agreement_checkbox(self) -> ScreenRegion:
		"""User agreement checkbox."""
		return ScreenRegion(0.10, 0.64, 0.25, 0.68)

	# ------------------------------------------------------------------
	# Utility — tap helpers
	# ------------------------------------------------------------------

	def tap_center(self, region: ScreenRegion) -> tuple[int, int]:
		"""Return pixel coordinates for the center of a region."""
		return region.to_center_pixels(self.w, self.h)

	def tap(self, region: ScreenRegion) -> tuple[int, int]:
		"""Alias for tap_center."""
		return self.tap_center(region)

	def to_dict(self) -> dict[str, Any]:
		"""Return layout info for debugging."""
		return {"screen": f"{self.w}x{self.h}", "orientation": "portrait"}
