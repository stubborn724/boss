"""本地控制台的 aiohttp 路由与同源写请求保护。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web

from boss_agent_cli.web.assets import render_console_page
from boss_agent_cli.web.runtime import LocalConsoleRuntime

_RUNTIME_KEY: web.AppKey[LocalConsoleRuntime] = web.AppKey("runtime", LocalConsoleRuntime)
_SESSION_TOKEN_KEY: web.AppKey[str] = web.AppKey("session_token", str)


def create_console_app(runtime: LocalConsoleRuntime, *, session_token: str) -> web.Application:
	"""创建本地控制台应用。

	会话令牌只注入启动页，不持久化且不出现在接口状态中。所有改变本地状态的
	请求还会检查同源 Origin，避免其他网页利用浏览器向回环地址发起跨站请求。
	"""
	app = web.Application()
	app[_RUNTIME_KEY] = runtime
	app[_SESSION_TOKEN_KEY] = session_token
	app.router.add_get("/", _index)
	app.router.add_get("/api/state", _state)
	app.router.add_post("/api/login", _login)
	app.router.add_post("/api/resume-download", _resume_download)
	return app


def _json_error(code: str, message: str, *, status: int) -> web.Response:
	"""构造不携带底层异常详情的统一 API 错误响应。"""
	return web.json_response({"ok": False, "error": {"code": code, "message": message}}, status=status)


def _is_trusted_write_request(request: web.Request) -> bool:
	"""校验同源 Origin 与启动页令牌，缩小本地 HTTP 写接口的攻击面。"""
	expected_origin = f"{request.scheme}://{request.host}"
	return (
		request.headers.get("Origin") == expected_origin
		and request.headers.get("X-Boss-Web-Token") == request.app[_SESSION_TOKEN_KEY]
	)


async def _index(request: web.Request) -> web.Response:
	"""返回唯一的本地控制台页面，并把临时写请求令牌注入脚本。"""
	return web.Response(text=render_console_page(request.app[_SESSION_TOKEN_KEY]), content_type="text/html")


async def _state(request: web.Request) -> web.Response:
	"""返回脱敏状态快照，供页面轮询更新控件。"""
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.status()})


async def _login(request: web.Request) -> web.Response:
	"""启动一次后台官方页面登录任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.start_login()}, status=202)


async def _resume_download(request: web.Request) -> web.Response:
	"""校验单份简历定位参数后启动后台下载任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	geek_id = _required_text(payload, "geek_id")
	job_id = _required_text(payload, "job_id")
	security_id = _required_text(payload, "security_id")
	if not all((geek_id, job_id, security_id)):
		return _json_error("INVALID_PARAM", "下载在线简历需要 geek_id、job_id 和 security_id", status=400)
	output = _optional_path(payload, "output")
	output_dir = _optional_path(payload, "output_dir")
	if output is not None and output_dir is not None:
		return _json_error("INVALID_PARAM", "output 与 output_dir 互斥，只能指定一个", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_download(
		geek_id=geek_id,
		job_id=job_id,
		security_id=security_id,
		output=output,
		output_dir=output_dir,
	)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


def _required_text(payload: dict[str, Any], key: str) -> str:
	"""读取必填文本；非字符串和纯空白统一按缺失处理。"""
	value = payload.get(key)
	return value.strip() if isinstance(value, str) else ""


def _optional_path(payload: dict[str, Any], key: str) -> Path | None:
	"""读取可选路径；空值代表采用既有默认导出位置。"""
	value = payload.get(key)
	return Path(value).expanduser() if isinstance(value, str) and value.strip() else None
