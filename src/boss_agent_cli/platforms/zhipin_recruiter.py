"""BOSS 直聘招聘者平台 adapter。

把 ``BossRecruiterClient`` 包装为 ``RecruiterPlatform`` 实现，零行为变化。
后续新平台实现同一 RecruiterPlatform 接口，
命令层可以通过 ``get_recruiter_platform(name)`` 无差别调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from boss_agent_cli.api.recruiter_endpoints import BASE_URL
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
	from boss_agent_cli.api.recruiter_client import BossRecruiterClient

# BOSS 直聘错误码 → 统一错误码映射
_ERROR_CODE_MAP: dict[int, str] = {
	9: "RATE_LIMITED",
	36: "ACCOUNT_RISK",
	37: "TOKEN_REFRESH_FAILED",
	121: "INVALID_PARAM",
}

# 端点路径片段 → 已知端点漂移场景（命中后将该路径下的 121 重映射为 ENDPOINT_DEPRECATED）
# 真源：issue #217 — qianjunye 抓包确认 fastReply/sendReplyMsg 已被 BOSS 替换为 WS+Protobuf 双通道。
_DEPRECATED_ENDPOINT_FRAGMENTS: tuple[str, ...] = ("fastReply/sendReplyMsg",)


class BossRecruiterPlatform(RecruiterPlatform):
	"""BOSS 直聘招聘者平台实现。"""

	name = "zhipin-recruiter"
	display_name = "BOSS 直聘（招聘者）"
	base_url = BASE_URL

	def __init__(self, client: Any) -> None:
		"""接收 HTTP 或页面 RPA 客户端。

		适配器负责统一招聘领域的响应包络，而不是限定底层传输。此前把类型写死
		为 HTTP 客户端会迫使 RPA 工厂使用忽略或回退路径，进而破坏“以当前
		Chrome 页面为准”的约束；统一为 ``Any`` 后，具体能力仍由各委托方法
		按平台协议收敛。
		"""
		super().__init__(client)
		# 既有委托方法的 HTTP 响应签名以 BossRecruiterClient 为类型真源；RPA
		# 客户端实现同名响应契约，因此在适配器边界完成一次窄化，避免把 Any
		# 扩散到每一个业务方法并丢失返回包络检查。
		self._client: "BossRecruiterClient" = cast("BossRecruiterClient", client)

	# ── 包络适配 ────────────────────────────────────────

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def probe_live_login(self) -> bool:
		"""委托 RPA 客户端只读检查当前 BOSS 招聘页登录态。"""
		method = getattr(self._client, "probe_live_login", None)
		return bool(method()) if callable(method) else False

	def reset_rpa_session(self) -> None:
		"""通知底层 RPA 丢弃旧页面绑定，让下一次操作重新选择官方页面。"""
		method = getattr(self._client, "reset_session", None)
		if callable(method):
			method()

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		code = response.get("code")
		message = str(response.get("message") or response.get("zpData") or "")
		unified = _ERROR_CODE_MAP.get(code, "UNKNOWN") if isinstance(code, int) else "UNKNOWN"
		# 端点漂移场景下重映射 121：调用方（_browser_request / _request）在 response dict 注入
		# __cli_endpoint_hint__ 字段（CLI 内部命名空间，避免与服务端字段冲突）。
		if code == 121:
			hint = response.get("__cli_endpoint_hint__")
			if isinstance(hint, str) and any(frag in hint for frag in _DEPRECATED_ENDPOINT_FRAGMENTS):
				unified = "ENDPOINT_DEPRECATED"
		return unified, message

	# ── 候选人列表与筛选 ────────────────────────────────

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		return self._client.friend_list(page=page, label_id=label_id, job_id=job_id)

	def select_conversation_job(self, job_name: str) -> dict[str, Any]:
		"""委托 RPA 切换 BOSS 沟通列表顶部职位，并保留回显校验结果。

		适配器层不能让命令层直接绕过 ``RecruiterPlatform`` 访问底层浏览器。底层
		客户端不存在该能力时返回受控错误，使自动化停止本轮同步而不混用全部职位。
		"""
		method = getattr(self._client, "select_conversation_job", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持筛选 BOSS 沟通列表职位"}
		result = method(job_name)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "BOSS 沟通列表岗位筛选返回格式异常"}

	def select_all_conversation_jobs(self) -> dict[str, Any]:
		"""委托 RPA 清除沟通页职位筛选，避免读取上一次单岗快照。"""
		method = getattr(self._client, "select_all_conversation_jobs", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持切换 BOSS 沟通列表全部职位"}
		result = method()
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "BOSS 沟通列表全部职位切换返回格式异常"}

	def fast_conversation_snapshot(self, *, include_all: bool = False) -> dict[str, Any]:
		"""委托 RPA 读取沟通列表快照，避免后台轮询退回慢速分页扫描。

		该方法是自动化调度与浏览器虚拟列表之间的唯一边界。适配器必须原样
		转发底层结果，才能让调用方区分“能力可用但暂无未读”和“能力不可用”；
		首轮同步的 ``include_all`` 也必须透传，不能重新退化为逐页读取。
		"""
		method = getattr(self._client, "fast_conversation_snapshot", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持快速读取 BOSS 沟通列表未读消息"}
		result = method(include_all=include_all) if include_all else method()
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "BOSS 沟通列表未读快照返回格式异常"}

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		return self._client.friend_detail(friend_ids)

	def friend_labels(self) -> dict[str, Any]:
		return self._client.friend_labels()

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		return self._client.greet_list(page=page, job_id=job_id)

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		return self._client.greet_rec_list(page=page, job_id=job_id)

	def set_recommendation_job(self, job_name: str) -> dict[str, Any]:
		"""把当前岗位名称传给页面 RPA，解决同步镜像没有真实职位 ID 的情况。"""
		method = getattr(self._client, "set_recommendation_job", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持推荐页岗位选择"}
		result = method(job_name)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "推荐页岗位选择返回格式异常"}

	def sync_job_greeting(self, job_name: str, content: str) -> dict[str, Any]:
		"""委托 RPA 保存岗位自定义招呼语，供推荐页打招呼使用。

		该方法保留在平台适配层，避免招聘命令直接依赖 RPA 客户端实现；HTTP
		客户端缺少此能力时明确返回失败，不能假装已在 BOSS 页面同步完成。
		"""
		method = getattr(self._client, "sync_job_greeting", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持同步岗位招呼语"}
		result = method(job_name, content)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "岗位招呼语同步返回格式异常"}

	def greet_recommendation_by_geek_id(self, geek_id: str) -> dict[str, Any]:
		"""按推荐卡片稳定标识执行打招呼，并保留页面确认结果。"""
		method = getattr(self._client, "greet_recommendation_by_geek_id", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持推荐牛人打招呼"}
		result = method(geek_id)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "推荐牛人打招呼返回格式异常"}

	# ── 候选人搜索与简历 ────────────────────────────────

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
		return self._client.search_geeks(
			query,
			city=city,
			page=page,
			job_id=job_id,
			experience=experience,
			degree=degree,
			age=age,
			school_level=school_level,
			activeness=activeness,
			source=source,
			select=select,
			salary=salary,
		)

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		return self._client.view_geek(geek_id, job_id=job_id, security_id=security_id)

	def open_online_resume_preview(self, *, friend_id: int) -> dict[str, Any]:
		"""只打开预览，避免复用会 OCR、落盘并关闭弹窗的简历读取流程。"""
		return self._client.open_online_resume_preview(friend_id=friend_id)

	def chat_geek_info(self, geek_id: str, security_id: str, job_id: int) -> dict[str, Any]:
		return self._client.chat_geek_info(geek_id, security_id, job_id)

	# ── 消息 / 聊天 ──────────────────────────────────────

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		return self._client.last_messages(friend_ids)

	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		return self._client.chat_history(gid, count=count, max_msg_id=max_msg_id)

	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		return self._client.send_message(gid, content)

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		return self._client.send_message_by_friend(friend_id, content)

	def has_existing_resume_request(self, friend_id: int) -> bool:
		"""通过 RPA 查询当前会话是否已有索要简历消息。

		该能力只读取页面已渲染的聊天内容，用于接管旧流程留下的候选人，避免
		本地状态文件缺失时再次发送相同话术。
		"""
		if hasattr(self._client, "has_existing_resume_request"):
			return bool(self._client.has_existing_resume_request(friend_id))
		return False

	def session_enter(self, geek_id: str, expect_id: str, job_id: str, security_id: str) -> dict[str, Any]:
		return self._client.session_enter(geek_id, expect_id, job_id, security_id)

	# ── 职位管理 ──────────────────────────────────────────

	def list_jobs(self) -> dict[str, Any]:
		return self._client.list_jobs()

	def job_offline(self, job_id: str) -> dict[str, Any]:
		return self._client.job_offline(job_id)

	def job_online(self, job_id: str) -> dict[str, Any]:
		return self._client.job_online(job_id)

	def job_detail(self, enc_job_id: str) -> dict[str, Any]:
		return self._client.job_detail(enc_job_id)

	# ── 交换联系方式 ──────────────────────────────────────

	def exchange_request(self, exchange_type: int, uid: int, job_id: int, gid: int) -> dict[str, Any]:
		return self._client.exchange_request(exchange_type, uid, job_id, gid)

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict[str, Any]:
		return self._client.exchange_request_by_friend(friend_id, exchange_type)

	def request_contact_exchange(self, *, friend_id: int, contact_type: str) -> dict[str, Any]:
		"""转发 RPA 联系方式交换，并拒绝不支持页面回执的旧客户端。"""
		method = getattr(self._client, "request_contact_exchange", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持 BOSS 联系方式确认"}
		result = method(friend_id=friend_id, contact_type=contact_type)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "BOSS 联系方式确认返回格式异常"}

	def invite_interview_via_ui(self, *, friend_id: int, payload: dict[str, str]) -> dict[str, Any]:
		"""转发 RPA 约面试操作，保持当前页面会话与本地岗位配置一致。"""
		method = getattr(self._client, "invite_interview_via_ui", None)
		if not callable(method):
			return {"code": -1, "message": "当前平台连接不支持 BOSS 约面试"}
		result = method(friend_id=friend_id, payload=payload)
		return dict(result) if isinstance(result, dict) else {"code": -1, "message": "BOSS 约面试返回格式异常"}

	def exchange_content(self, uid: int) -> dict[str, Any]:
		return self._client.exchange_content(uid)

	# ── 面试 ──────────────────────────────────────────────

	def interview_list(self) -> dict[str, Any]:
		return self._client.interview_list()

	def interview_invite(self, geek_id: str, job_id: str, security_id: str, **kwargs: Any) -> dict[str, Any]:
		return self._client.interview_invite(geek_id, job_id, security_id, **kwargs)

	# ── 附件下载（RPA 模式） ──────────────────────────────

	def download_attachment(self, url: str) -> bytes:
		if hasattr(self._client, 'download_attachment'):
			payload = self._client.download_attachment(url)
			return payload if isinstance(payload, bytes) else b""
		return b""

	def accept_attachment_share(self, friend_id: int) -> dict[str, Any]:
		if hasattr(self._client, 'accept_attachment_share'):
			result = self._client.accept_attachment_share(friend_id)
			return dict(result) if isinstance(result, dict) else {"code": -1, "message": "接受附件返回格式异常"}
		return {"code": -1, "message": "accept_attachment_share 不可用"}

	def download_attachment_via_ui(self, friend_id: int, save_dir: str | None = None) -> dict[str, Any]:
		if hasattr(self._client, 'download_attachment_via_ui'):
			result = self._client.download_attachment_via_ui(friend_id, save_dir=save_dir)
			return dict(result) if isinstance(result, dict) else {"code": -1, "message": "附件下载返回格式异常"}
		return {"code": -1, "message": "download_attachment_via_ui 不可用"}

	# ── 候选人操作 ────────────────────────────────────────

	def mark_unsuitable(self, geek_id: str, job_id: str) -> dict[str, Any]:
		return self._client.mark_unsuitable(geek_id, job_id)
