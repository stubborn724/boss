"""本地控制台 HTTP 边界测试。"""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def _runtime() -> LocalConsoleRuntime:
	return LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
	)


def test_post_routes_require_same_origin_and_session_token() -> None:
	"""其他本地网页不能借由回环地址触发登录或个人数据下载。"""
	async def scenario() -> None:
		app = create_console_app(_runtime(), session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			response = await client.post("/api/login")
			assert response.status == 403
			payload = await response.json()
			assert payload["error"]["code"] == "FORBIDDEN"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_download_response_never_contains_resume_body() -> None:
	"""即使请求参数恶意包含正文片段，HTTP 响应也不得回显它。"""
	async def scenario() -> None:
		app = create_console_app(_runtime(), session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
			response = await client.post(
				"/api/resume-download",
				headers=headers,
				json={"geek_id": "g", "job_id": "j", "security_id": "s", "ignored": "候选人简历正文"},
			)
			payload = await response.json()
			assert response.status == 202
			assert payload == {"ok": True, "data": {"state": "running"}}
			assert "候选人简历正文" not in str(payload)
		finally:
			await client.close()

	asyncio.run(scenario())
