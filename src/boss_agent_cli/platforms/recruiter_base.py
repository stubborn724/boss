"""招聘者平台抽象基类。

RecruiterPlatform 接口定义跨平台招聘者侧统一契约
（候选人管理 / 职位管理 / 沟通 / 面试 等），
让 CLI 命令层通过 RecruiterPlatform 抽象调用，不耦合具体平台协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any


class RecruiterPlatform(ABC):
	"""招聘者平台抽象基类。

	每个平台实现需覆盖：
	- 基础元信息（name / display_name / base_url）
	- 包络适配方法（is_success / unwrap_data / parse_error）
	- 候选人列表与筛选（friend_list / greet_list）
	- 候选人搜索与简历（search_geeks / view_geek）
	- 职位管理（list_jobs）
	- 消息（chat_history / send_message）

	可选写操作（job_offline / job_online / exchange_request /
	interview_list / interview_invite / mark_unsuitable）
	平台不支持时抛 NotImplementedError。

	资源管理：支持 ``with`` 上下文管理器语法，``__exit__`` 自动调用 ``close()``
	释放底层 client 持有的 httpx / 浏览器资源。
	"""

	name: str
	display_name: str
	base_url: str

	def __init__(self, client: Any) -> None:
		"""ABC 构造签名：所有实现都接收一个平台专用 client。"""
		self._client: Any = client

	# ── 资源生命周期 ───────────────────────────────────

	def close(self) -> None:
		"""释放底层资源。默认委托给 ``client.close()``（若存在）。"""
		close_fn = getattr(self._client, "close", None)
		if callable(close_fn):
			close_fn()

	def __enter__(self) -> "RecruiterPlatform":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()

	# ── 包络适配 ────────────────────────────────────────

	@abstractmethod
	def is_success(self, response: dict[str, Any]) -> bool:
		"""判断响应是否成功。"""

	@abstractmethod
	def unwrap_data(self, response: dict[str, Any]) -> Any:
		"""从响应包络提取 data。"""

	@abstractmethod
	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		"""解析错误响应，返回 (统一错误码, 原始消息)。"""

	def probe_live_login(self) -> bool:
		"""只读检查当前页面 RPA 是否已处于招聘端登录状态。

		基类默认返回 ``False``，使不具备页面会话能力的平台保持保守锁定；
		BOSS RPA 实现会覆盖该方法并读取当前标签页 URL。
		"""
		return False

	# ── 候选人列表与筛选 ────────────────────────────────

	@abstractmethod
	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		"""沟通列表（按标签/职位筛选）。"""

	def select_conversation_job(self, job_name: str) -> dict[str, Any]:
		"""切换沟通列表顶部职位筛选器。

		该能力只由具备页面 RPA 的平台实现。自动化调用方必须以此方法的成功回显
		作为读取岗位会话的前置条件；未实现的平台显式拒绝，不能回退到全职位列表。
		"""
		raise NotImplementedError(f"{self.name} does not implement select_conversation_job")

	def select_all_conversation_jobs(self) -> dict[str, Any]:
		"""将 BOSS 沟通列表恢复为全部职位。

		“无岗位参数”不能被解释为保持浏览器当前筛选，因为当前筛选可能来自
		上一次 Java、售后等单岗操作。实现方必须主动切回全部职位并验证回显。
		"""
		raise NotImplementedError(f"{self.name} does not implement select_all_conversation_jobs")

	def fast_conversation_snapshot(self, *, include_all: bool = False) -> dict[str, Any]:
		"""读取沟通列表快照。

		后台轮询默认只定位未读会话，不应每 20 秒完整翻阅所有历史沟通；首轮
		同步可通过 ``include_all`` 请求完整快照，以一次浏览器内扫描替代逐页
		读取。未实现的平台显式拒绝，调用方再按兼容策略决定是否退回分页读取，
		避免因适配器遗漏而无声降级。
		"""
		raise NotImplementedError(f"{self.name} does not implement fast_conversation_snapshot")

	@abstractmethod
	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""打招呼列表。"""

	# ── 候选人搜索与简历 ────────────────────────────────

	@abstractmethod
	def search_geeks(self, query: str, *, city: str | None = None, page: int = 1, job_id: str | None = None, experience: str | None = None, degree: str | None = None, age: str | None = None, school_level: str | None = None, activeness: str | None = None, source: str | None = None, select: bool = False, salary: str | None = None) -> dict[str, Any]:
		"""搜索候选人。"""

	@abstractmethod
	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		"""查看候选人简历。"""

	def open_online_resume_preview(self, *, friend_id: int) -> dict[str, Any]:
		"""仅打开在线简历预览；默认平台不支持该页面级能力。"""
		raise NotImplementedError(f"{self.name} does not implement open_online_resume_preview")

	# ── 消息 / 聊天 ──────────────────────────────────────

	@abstractmethod
	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		"""聊天历史记录。"""

	@abstractmethod
	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		"""发送消息。"""

	# ── 职位管理 ──────────────────────────────────────────

	@abstractmethod
	def list_jobs(self) -> dict[str, Any]:
		"""查看职位列表。"""

	# ── 可选操作（默认抛 NotImplementedError）──────

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		"""批量获取好友详情。"""
		raise NotImplementedError(f"{self.name} does not implement friend_detail")

	def friend_labels(self) -> dict[str, Any]:
		"""获取好友标签。"""
		raise NotImplementedError(f"{self.name} does not implement friend_labels")

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""推荐牛人招呼列表。"""
		raise NotImplementedError(f"{self.name} does not implement greet_rec_list")

	def set_recommendation_job(self, job_name: str) -> dict[str, Any]:
		"""设置本轮推荐页要校验的岗位名称。"""
		return {"code": 0, "zpData": {"job_name": job_name}}

	def sync_job_greeting(self, job_name: str, content: str) -> dict[str, Any]:
		"""同步岗位自定义招呼语；平台不支持时禁止继续推荐页打招呼。"""
		raise NotImplementedError(f"{self.name} does not implement sync_job_greeting")

	def greet_recommendation_by_geek_id(self, geek_id: str) -> dict[str, Any]:
		"""按推荐页稳定候选人标识打招呼，并返回页面确认状态。"""
		raise NotImplementedError(f"{self.name} does not implement greet_recommendation_by_geek_id")

	def chat_geek_info(self, geek_id: str, security_id: str, job_id: int) -> dict[str, Any]:
		"""获取候选人聊天信息。"""
		raise NotImplementedError(f"{self.name} does not implement chat_geek_info")

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		"""获取最近消息。"""
		raise NotImplementedError(f"{self.name} does not implement last_messages")

	def session_enter(self, geek_id: str, expect_id: str, job_id: str, security_id: str) -> dict[str, Any]:
		"""进入聊天会话。"""
		raise NotImplementedError(f"{self.name} does not implement session_enter")

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		"""按 friend_id 发送消息（issue #217 A' 路径，`hr reply` 在用）。"""
		raise NotImplementedError(f"{self.name} does not implement send_message_by_friend")

	def has_existing_resume_request(self, friend_id: int) -> bool:
		"""检查页面历史是否已经索要附件，供恢复性轮询防重使用。"""
		raise NotImplementedError(f"{self.name} does not implement has_existing_resume_request")

	def download_attachment_via_ui(self, friend_id: int, save_dir: str | None = None) -> dict[str, Any]:
		"""通过页面同意并下载候选人真实附件，不提供在线简历替代。"""
		raise NotImplementedError(f"{self.name} does not implement download_attachment_via_ui")

	def job_offline(self, job_id: str) -> dict[str, Any]:
		"""下线职位。"""
		raise NotImplementedError(f"{self.name} does not implement job_offline")

	def job_online(self, job_id: str) -> dict[str, Any]:
		"""上线职位。"""
		raise NotImplementedError(f"{self.name} does not implement job_online")

	def job_detail(self, enc_job_id: str) -> dict[str, Any]:
		"""查看职位详情（`hr jobs detail` 在用）。"""
		raise NotImplementedError(f"{self.name} does not implement job_detail")

	def exchange_request(self, exchange_type: int, uid: int, job_id: int, gid: int) -> dict[str, Any]:
		"""请求交换联系方式。DEPRECATED — 见 exchange_request_by_friend (issue #217)。"""
		raise NotImplementedError(f"{self.name} does not implement exchange_request")

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict[str, Any]:
		"""请求交换联系方式 / 求附件简历（issue #217 修复）。

		type: 1=换手机号, 2=换微信, 4=求附件简历
		"""
		raise NotImplementedError(f"{self.name} does not implement exchange_request_by_friend")

	def request_contact_exchange(self, *, friend_id: int, contact_type: str) -> dict[str, Any]:
		"""通过平台页面请求电话或微信，并完成页面二次确认。"""
		raise NotImplementedError(f"{self.name} does not implement request_contact_exchange")

	def invite_interview_via_ui(self, *, friend_id: int, payload: dict[str, str]) -> dict[str, Any]:
		"""通过平台页面提交一项已验证的面试邀请。"""
		raise NotImplementedError(f"{self.name} does not implement invite_interview_via_ui")

	def exchange_content(self, uid: int) -> dict[str, Any]:
		"""获取交换内容。"""
		raise NotImplementedError(f"{self.name} does not implement exchange_content")

	def interview_list(self) -> dict[str, Any]:
		"""面试列表。"""
		raise NotImplementedError(f"{self.name} does not implement interview_list")

	def interview_invite(self, geek_id: str, job_id: str, security_id: str, **kwargs: Any) -> dict[str, Any]:
		"""邀请面试。"""
		raise NotImplementedError(f"{self.name} does not implement interview_invite")

	def mark_unsuitable(self, geek_id: str, job_id: str) -> dict[str, Any]:
		"""标记不合适。"""
		raise NotImplementedError(f"{self.name} does not implement mark_unsuitable")
