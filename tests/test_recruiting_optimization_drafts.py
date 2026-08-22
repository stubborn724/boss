"""招聘复盘改进草稿的持久化、工作台和 Web 闭环测试。"""

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def _runtime(data_dir: Path) -> LocalConsoleRuntime:
	"""创建不触碰 BOSS 平台的本地测试运行时。"""
	return LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=RecruitingWorkspace(data_dir),
	)


def test_workspace_persists_optimization_draft_idempotently_and_reviews_it(tmp_path: Path) -> None:
	"""同一复盘建议重复生成不重复落盘，审核后重建工作台仍保留状态。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("姓名：候选人\n有销售经验。", encoding="utf-8")
	workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	suggestion = next(item for item in workspace.snapshot(job["job"]["job_id"])["optimization"]["suggestions"] if item["kind"] == "knowledge_gap")

	first = workspace.create_optimization_draft(job["job"]["job_id"], suggestion["suggestion_id"])
	second = workspace.create_optimization_draft(job["job"]["job_id"], suggestion["suggestion_id"])

	assert first["draft_id"] == second["draft_id"]
	assert first["status"] == "pending_review"
	assert len(workspace.snapshot(job["job"]["job_id"])["optimization_drafts"]) == 1

	reviewed = workspace.review_optimization_draft(first["draft_id"], status="accepted", note="已安排补充销售流程资料")
	reloaded = RecruitingWorkspace(tmp_path).snapshot(job["job"]["job_id"])

	assert reviewed["status"] == "accepted"
	assert reviewed["review_note"] == "已安排补充销售流程资料"
	assert reloaded["optimization_drafts"][0]["status"] == "accepted"
	assert reloaded["optimization"]["mutations"] == []


def test_recruiting_web_api_creates_and_reviews_optimization_draft(tmp_path: Path) -> None:
	"""Web 页面可以显式生成和审核改进草稿，接口不触发外部平台动作。"""

	async def scenario() -> None:
		runtime = _runtime(tmp_path)
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		headers = {
			"Origin": str(client.make_url("/")).rstrip("/"),
			"X-Boss-Web-Token": "test-token",
		}
		try:
			created = await client.post(
				"/api/recruiting/jobs",
				headers=headers,
				json={"name": "销售顾问", "status": "published"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			workspace_payload = await (await client.get("/api/recruiting/workspace")).json()
			job = workspace_payload["data"]["jobs"][0]
			resume_path = tmp_path / "候选人.md"
			resume_path.write_text("姓名：候选人\n有销售经验。", encoding="utf-8")
			await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"job_id": job["job_id"], "resume_path": str(resume_path)},
			)
			runtime.wait_for_idle(timeout=1)
			before = (await (await client.get(f"/api/recruiting/workspace?job_id={job['job_id']}" )).json())["data"]
			suggestion = next(item for item in before["optimization"]["suggestions"] if item["kind"] == "knowledge_gap")

			created_draft = await client.post(
				"/api/recruiting/optimization-drafts",
				headers=headers,
				json={"job_id": job["job_id"], "suggestion_id": suggestion["suggestion_id"]},
			)
			assert created_draft.status == 202
			runtime.wait_for_idle(timeout=1)
			draft = (await (await client.get(f"/api/recruiting/workspace?job_id={job['job_id']}" )).json())["data"]["optimization_drafts"][0]

			duplicate = await client.post(
				"/api/recruiting/optimization-drafts",
				headers=headers,
				json={"job_id": job["job_id"], "suggestion_id": suggestion["suggestion_id"]},
			)
			assert duplicate.status == 202
			runtime.wait_for_idle(timeout=1)
			accepted = await client.post(
				f"/api/recruiting/optimization-drafts/{draft['draft_id']}",
				headers=headers,
				json={"status": "accepted", "note": "已纳入岗位复盘计划"},
			)
			assert accepted.status == 202
			runtime.wait_for_idle(timeout=1)
			final = (await (await client.get(f"/api/recruiting/workspace?job_id={job['job_id']}" )).json())["data"]
			assert len(final["optimization_drafts"]) == 1
			assert final["optimization_drafts"][0]["status"] == "accepted"
		finally:
			await client.close()

	asyncio.run(scenario())
