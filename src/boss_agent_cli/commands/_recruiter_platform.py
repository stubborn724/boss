"""招聘端平台实例工厂。

招聘工作台的沟通、职位同步和附件下载都必须来自 BOSS 页面 RPA。这个工厂将
浏览器传输选择集中到一处，确保调用方不会在 RPA 不可用时无感回退到 HTTP API，
从而读到与用户当前 Chrome 账号不一致的数据。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from boss_agent_cli.bridge.client import BridgeClient
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.rpa.boss_client import BossRPAClient, BossRPAConnectionError
from boss_agent_cli.rpa.bridge_boss_client import BridgeBossRPAClient

if TYPE_CHECKING:
	import click
	from boss_agent_cli.auth.manager import AuthManager


def get_recruiter_platform_instance(
	ctx: "click.Context", auth: "AuthManager", *, cdp_url: str | None = None,
) -> RecruiterPlatform:
	"""创建页面 RPA 招聘平台，Bridge 优先且禁止 HTTP/API 回退。

	``auth`` 保留在签名中是为了兼容既有命令的依赖注入；RPA 直接使用当前浏览器
	页面的登录态，不读取 ``AuthManager`` 的 token 或 Cookie。Bridge 未连接时仅
	允许使用用户显式配置的旧 CDP 专用浏览器，否则给出可恢复错误。
	"""
	del auth
	obj = ctx.obj or {}
	name = obj.get("platform") or "zhipin"
	if name != "zhipin":
		raise ValueError(f"unsupported recruiter platform: {name}")

	resolved_cdp = cdp_url or obj.get("cdp_url")
	if isinstance(resolved_cdp, str) and resolved_cdp.strip():
		return BossRecruiterPlatform(BossRPAClient(cdp_url=resolved_cdp))

	# 专用 Chrome 是招聘工作台的默认边界。普通 Chrome Bridge 允许用户手动
	# 浏览 BOSS，但不能在工作台未明确指定时抢占页面；否则多个普通标签会导致
	# 沟通页、推荐页来回切换，产生肉眼可见的刷新和错误的空列表。
	bridge = BridgeClient()
	if bridge.is_extension_connected() and bool(obj.get("use_existing_chrome_bridge")):
		return BossRecruiterPlatform(BridgeBossRPAClient(bridge=bridge))

	raise BossRPAConnectionError(
		"专用 Chrome 未连接：请先启动项目专用 Chrome 并在其中登录 BOSS 招聘端"
	)


__all__ = ["get_recruiter_platform_instance"]
