"""BOSS Zhipin web page element selectors and DOM structure mappings.

These selectors are designed to be resilient to minor DOM changes — each
target element has multiple fallback selectors. When BOSS updates their
frontend, only this file needs to be updated.
"""

from __future__ import annotations

from typing import Any

# ------------------------------------------------------------------
# Conversation (沟通) list page — /web/chat/index
# ------------------------------------------------------------------

CONVERSATION_PAGE_URL = "https://www.zhipin.com/web/chat/index"

# Each conversation card in the left sidebar list
CONVERSATION_CARD = [
	".chat-list .chat-item",
	".conversation-item",
	"[class*='chat-item']",
	"[class*='conversation'] [class*='item']",
]

# Candidate name inside a card
CONVERSATION_CARD_NAME = [
	".name",
	".user-name",
	"[class*='name']",
	"span:first-child",
]

# Position/job title
CONVERSATION_CARD_POSITION = [
	".position",
	".job-name",
	"[class*='position']",
	"[class*='job-name']",
]

# Company name
CONVERSATION_CARD_COMPANY = [
	".company",
	".brand-name",
	"[class*='company']",
	"[class*='brand']",
]

# City
CONVERSATION_CARD_CITY = [
	".city",
	"[class*='city']",
	"[class*='location']",
]

# Unread badge
CONVERSATION_UNREAD_BADGE = [
	".unread-badge",
	".unread-count",
	"[class*='unread']",
	"[aria-label*='未读']",
	"[aria-label*='unread' i]",
]

# Search/filter input
CONVERSATION_SEARCH_INPUT = [
	"input[placeholder*='搜索']",
	".search-input input",
	"input.search",
]

# ------------------------------------------------------------------
# Recommendation (推荐牛人) page
# ------------------------------------------------------------------

RECOMMENDATION_PAGE_URL = "https://www.zhipin.com/web/chat/index?tab=recommend"

RECOMMENDATION_CARD = [
	".recommend-card",
	"[class*='recommend'] [class*='card']",
	"[class*='geek-card']",
	".candidate-card",
]

RECOMMENDATION_CARD_NAME = [
	".geek-name",
	".name",
	"[class*='name']",
]

RECOMMENDATION_JOB_FILTER_TABS = [
	".job-filter-tab",
	"[class*='job-tab']",
	"[class*='filter'] [class*='tab']",
]

# "在线简历" button inside recommendation card
VIEW_RESUME_BUTTON = [
	"text=在线简历",
	"text=查看简历",
	"a:has-text('简历')",
	"[class*='resume']",
]

# ------------------------------------------------------------------
# Chat / Candidate detail page
# ------------------------------------------------------------------

# Message list container
CHAT_MESSAGE_LIST = [
	".message-list",
	".chat-content",
	"[class*='message-list']",
	"[class*='chat-content']",
]

# Individual message
CHAT_MESSAGE_ITEM = [
	".message-item",
	".msg-item",
	"[class*='message-item']",
	"[class*='msg']",
]

# Message text content
CHAT_MESSAGE_TEXT = [
	".message-text",
	".msg-content",
	"[class*='text']",
	"[class*='content']",
]

# Candidate name at top of chat
CHAT_CANDIDATE_NAME = [
	".chat-header .name",
	".user-info .name",
	"[class*='header'] [class*='name']",
]

# Chat input field
CHAT_INPUT = [
	"textarea",
	".chat-input textarea",
	"[class*='input'] textarea",
	"[contenteditable='true']",
	"div[placeholder*='输入']",
]

# Send button
CHAT_SEND_BUTTON = [
	"button:has-text('发送')",
	".send-btn",
	"[class*='send']",
	"button[type='submit']",
]

# "查看在线简历" link in chat header
CHAT_VIEW_RESUME_LINK = [
	"text=在线简历",
	"a:has-text('简历')",
	".resume-link",
]

# "交换微信" / "交换电话" button
EXCHANGE_CONTACT_BUTTON = [
	"text=交换微信",
	"text=交换电话",
	"text=交换联系方式",
	"[class*='exchange']",
]

# ------------------------------------------------------------------
# Online resume popup / modal
# ------------------------------------------------------------------

RESUME_CONTAINER = [
	".resume-modal",
	".resume-dialog",
	"[class*='resume'] [class*='modal']",
	"[class*='resume'] [class*='dialog']",
	".geek-resume",
]

RESUME_SECTION_TITLE = [
	".resume-section-title",
	".section-title",
	"[class*='section'] [class*='title']",
	"h3",
]

RESUME_SECTION_CONTENT = [
	".resume-section-content",
	".section-content",
	"[class*='section'] [class*='content']",
]

# Scrollable container inside resume modal
RESUME_SCROLL_AREA = [
	".resume-modal .content",
	".resume-body",
	"[class*='resume'] [class*='body']",
	"[class*='resume'] [class*='scroll']",
]

# Close button
RESUME_CLOSE_BUTTON = [
	".resume-modal .close",
	".close-btn",
	"[class*='close']",
	"button:has-text('×')",
]

# ------------------------------------------------------------------
# Job (职位) management page
# ------------------------------------------------------------------

# 与招聘端“职位管理”菜单的当前 href 保持一致，避免 RPA 导航至失效的旧路由。
JOB_LIST_PAGE_URL = "https://www.zhipin.com/web/chat/job/list"

JOB_CARD = [
	".job-item-container",
	".job-list-content > li",
]

JOB_TITLE = [
	".job-main-info-wrapper .job-name",
	".job-title",
	".job-name",
	"[class*='job'] [class*='title']",
	"[class*='job'] [class*='name']",
]

JOB_STATUS = [
	".job-status-wrapper .status-box",
	".status-box",
	".job-status",
	"[class*='job'] [class*='status']",
]

# ------------------------------------------------------------------
# Common / Navigation
# ------------------------------------------------------------------

# Bottom tab bar items
TAB_MESSAGES = ["text=消息", ".tab-messages", "[class*='tab']:has-text('消息')"]
TAB_CONTACTS = ["text=沟通", ".tab-contacts", "[class*='tab']:has-text('沟通')"]
TAB_RECOMMEND = ["text=推荐", ".tab-recommend", "[class*='tab']:has-text('推荐')"]
TAB_JOBS = ["text=职位", ".tab-jobs", "[class*='tab']:has-text('职位')"]

# Page loading indicator
PAGE_LOADING = [
	".loading",
	".spinner",
	"[class*='loading']",
	"[class*='spin']",
]

# Login page elements
LOGIN_PHONE_INPUT = [
	"input[type='tel']",
	"input[placeholder*='手机']",
	"input[placeholder*='电话']",
]

LOGIN_CODE_INPUT = [
	"input[placeholder*='验证码']",
	"input[placeholder*='code']",
]

LOGIN_GET_CODE_BUTTON = [
	"button:has-text('获取验证码')",
	"button:has-text('发送验证码')",
]

LOGIN_SUBMIT_BUTTON = [
	"button:has-text('登录')",
	"button:has-text('登錄')",
	"button[type='submit']",
]

# ------------------------------------------------------------------
# Selector matching helper
# ------------------------------------------------------------------

def find_element(page: Any, selectors: list[str]) -> Any | None:
	"""Try multiple selectors and return the first matching element."""
	for sel in selectors:
		try:
			el = page.locator(sel).first
			if el:
				return el
		except Exception:
			continue
	return None


def find_all_elements(page: Any, selectors: list[str]) -> list[Any]:
	"""Try selectors and return all matching elements from the first working one."""
	for sel in selectors:
		try:
			els = page.locator(sel).all()
			if els:
				return list(els)
		except Exception:
			continue
	return []


def safe_text(el: Any, fallback: str = "") -> str:
	"""Safely extract text content from a Playwright element."""
	try:
		return (el.text_content() or "").strip()
	except Exception:
		return fallback
