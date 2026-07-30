"""启动仅本机可访问的招聘简历控制台。"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import click
from aiohttp import web

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter.resume_download_service import ResumeDownloadService
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult, export_candidate_resume
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
from boss_agent_cli.compliance import operating_mode
from boss_agent_cli.output import emit_success
from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime


@click.command("web")
@click.option("--port", default=8765, show_default=True, type=click.IntRange(1024, 65535), help="本地控制台端口（仅绑定 127.0.0.1）")
@click.option("--login-timeout", default=120, show_default=True, type=click.IntRange(30, 600), help="官方页面登录等待秒数")
@click.pass_context
def web_cmd(ctx: click.Context, port: int, login_timeout: int) -> None:
	"""启动本地招聘简历控制台（仅监听 127.0.0.1）"""
	data_dir: Path = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	def download_resume(**kwargs: Any) -> ResumeExportResult:
		"""在现有平台上下文内复用 CLI 的单份简历下载服务。"""
		with get_recruiter_platform_instance(ctx, auth) as platform:
			service = ResumeDownloadService(
				platform=platform,
				parser=parse_resume,
				exporter=export_candidate_resume,
			)
			return service.download(data_dir=data_dir, **kwargs)

	runtime = LocalConsoleRuntime(
		operating_mode=operating_mode(ctx),
		login_in_browser=lambda: auth.login_in_browser(timeout=login_timeout),
		has_saved_login=lambda: auth.check_status() is not None,
		download_resume=download_resume,
	)
	app = create_console_app(runtime, session_token=secrets.token_urlsafe(32))
	url = f"http://127.0.0.1:{port}"
	emit_success("web", {"url": url, "host": "127.0.0.1", "port": port})
	try:
		asyncio.run(_serve(app, port))
	except KeyboardInterrupt:
		return


async def _serve(app: web.Application, port: int) -> None:
	"""以手动 runner 启动服务，避免 aiohttp 把非 JSON 启动横幅写入 stdout。"""
	runner = web.AppRunner(app, access_log=None)
	await runner.setup()
	site = web.TCPSite(runner, host="127.0.0.1", port=port)
	await site.start()
	try:
		await asyncio.Event().wait()
	finally:
		await runner.cleanup()
