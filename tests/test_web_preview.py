"""评分预览页面的只读路由测试。"""

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def _runtime(data_dir: Path) -> LocalConsoleRuntime:
	"""构造不触发平台调用的运行时，保证预览测试只验证 HTTP 页面契约。"""
	return LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: None,
		recruiting_workspace=RecruitingWorkspace(data_dir),
	)


def test_score_board_preview_is_live_read_only_workbench(tmp_path: Path) -> None:
	"""评分看板必须读取工作台快照，不能再渲染固定演示候选人。"""

	async def scenario() -> None:
		runtime = _runtime(tmp_path)
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			response = await client.get("/preview/score-board")
			assert response.status == 200
			assert response.headers["Content-Type"].startswith("text/html")
			body = await response.text()
			assert "招聘工作台 · 候选人评分看板" in body
			assert "/api/recruiting/workspace" in body
			assert 'id="score-board-job"' in body
			assert 'id="score-board-candidates"' in body
			assert "候选人评分分组" in body
			assert "不合格原因统计" in body
			assert 'id="score-board-auto-analyze"' in body
			assert "/api/recruiting/candidates/auto-assign" in body
			assert "董旻奇" not in body
		finally:
			await client.close()

	asyncio.run(scenario())


def test_score_board_can_start_local_auto_assignment_without_platform_access(tmp_path: Path) -> None:
	"""看板按钮必须启动本地任务，且不要求 BOSS 平台访问或登录写操作。"""

	async def scenario() -> None:
		resume_dir = tmp_path / "resumes"
		resume_dir.mkdir()
		(resume_dir / "候选人.md").write_text("姓名：候选人\n技能：Java\n有 Java 开发经验。", encoding="utf-8")
		runtime = _runtime(tmp_path / "workspace")
		runtime._recruiting_workspace.store.create_job(name="Java", status="draft")
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			response = await client.post(
				"/api/recruiting/candidates/auto-assign",
				headers={"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"},
				json={"directory": str(resume_dir)},
			)
			assert response.status == 202
			assert (await response.json())["data"]["state"] == "running"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_score_board_waits_for_the_api_state_envelope_before_refreshing_results() -> None:
	"""任务状态在 API 的 data 信封内，前端轮询必须读取该层而非永久显示运行中。"""
	from boss_agent_cli.web.score_board_preview import render_score_board_preview

	page = render_score_board_preview(session_token="test-token")

	assert "(payload.data||{}).recruiting" in page


def test_score_board_keeps_selected_job_outside_the_rebuilt_select_element() -> None:
	"""岗位下拉重渲染时必须保留用户的选择，不能回退到旧快照默认岗位。"""
	from boss_agent_cli.web.score_board_preview import render_score_board_preview

	page = render_score_board_preview(session_token="test-token")

	assert "let activeJobId=''" in page
	assert "activeJobId=jobSelect.value" in page
