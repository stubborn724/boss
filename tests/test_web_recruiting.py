"""招聘工作台 Web API 的边界和脱敏行为测试。"""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock

from boss_agent_cli.commands.recruiter.communication_pipeline import PipelineReport, PipelineStepResult
from aiohttp.test_utils import TestClient, TestServer

from boss_agent_cli.rpa.boss_client import BossRPAConnectionError, BossRPALoginRequiredError
from boss_agent_cli.recruiting.ai_review import AIResumeReview, SemanticHit
from boss_agent_cli.recruiting.job_standard_agent import JobStandardAgent
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.recruiting.context import RecruitingContextRegistry
from boss_agent_cli.recruiting.automation_coordinator import AutomationCoordinator, ConversationSeed
from boss_agent_cli.recruiting.automation_queue import AutomationQueueStore
from boss_agent_cli.recruiting.dialogue_transcript import DialogueTranscriptStore
from boss_agent_cli.web.app import create_console_app
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def test_automation_runtime_guard_accepts_live_conversation_read_when_probe_is_unstable(monkeypatch) -> None:
	"""实时列表读取成功时，不能因轻量登录探测偶发异常误停自动化。"""
	from boss_agent_cli.commands import web as web_command

	class _Platform:
		def probe_live_login(self) -> bool:
			raise RuntimeError("CDP probe response is temporarily unavailable")

	assert web_command._probe_automation_platform(_Platform(), lambda: [{"friend_id": 1}]) is True


def test_automation_runtime_guard_rejects_explicitly_logged_out_session() -> None:
	"""明确探测到未登录时，不能用空沟通列表把会话误判为可用。"""
	from boss_agent_cli.commands import web as web_command

	class _Platform:
		def probe_live_login(self) -> bool:
			return False

	assert web_command._probe_automation_platform(_Platform(), lambda: []) is False


def test_all_conversation_scope_switches_boss_back_to_all_jobs_before_reading() -> None:
	"""手动读取全部岗位时，不能复用 BOSS 上一次停留的单岗筛选状态。"""
	from boss_agent_cli.commands import web as web_command

	class _Platform:
		def __init__(self) -> None:
			self.calls = 0

		def select_all_conversation_jobs(self) -> dict[str, object]:
			self.calls += 1
			return {"code": 0, "zpData": {"selectedScope": "all"}}

		def is_success(self, response: object) -> bool:
			return isinstance(response, dict) and response.get("code") == 0

		def unwrap_data(self, response: object) -> dict[str, object]:
			return response["zpData"] if isinstance(response, dict) else {}

	platform = _Platform()

	web_command._ensure_all_conversation_jobs(platform)

	assert platform.calls == 1


def _runtime(data_dir: Path) -> LocalConsoleRuntime:
	return LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=RecruitingWorkspace(data_dir),
		recruiting_job_standard_agent=JobStandardAgent(),
	)


def test_automation_start_blocks_unconfirmed_job_rules(tmp_path: Path) -> None:
	"""手动和定时启动都必须经过岗位规则确认门禁。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="Java", status="draft")
	job = workspace.store.get_job(created["job"]["job_id"])
	assert job is not None
	job.rules_confirmed = False
	workspace.store.update_job(job)
	coordinator = MagicMock()
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		probe_live_login=lambda: True,
		download_resume=lambda **kwargs: None,
		recruiting_workspace=workspace,
		automation_coordinator=coordinator,
	)

	result = runtime.start_automation(job_id=job.job_id, source="conversation", limit=20)

	assert result["state"] == "blocked"
	assert "未确认" in result["error"]["message"]
	coordinator.start.assert_not_called()


def test_automation_web_sync_and_score_ordered_candidates(tmp_path: Path) -> None:
	"""Web 控制台应同步沟通会话，并按终审评分输出本岗位合格候选人。"""
	async def scenario() -> None:
		queue = AutomationQueueStore(tmp_path)
		first = tmp_path / "first.pdf"
		first.write_bytes(b"first")
		second = tmp_path / "second.pdf"
		second.write_bytes(b"second")
		queue.upsert_candidate(friend_id=1, job_id="job-java", candidate_name="低分", source="conversation")
		queue.upsert_candidate(friend_id=2, job_id="job-java", candidate_name="高分", source="recommendation")
		queue.record_final_review(friend_id=1, job_id="job-java", score=71, recommendation="review", resume_path=first)
		queue.record_final_review(friend_id=2, job_id="job-java", score=90, recommendation="invite_to_interview", resume_path=second)
		coordinator = AutomationCoordinator(
			queue=queue,
			sync_conversations=lambda _job_id: [ConversationSeed(friend_id=3, candidate_name="待处理")],
			process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
			finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
			greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		)
		runtime = LocalConsoleRuntime(
			operating_mode="research", login_in_browser=lambda: None, has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		list_recent_conversations=lambda: [
			{"friend_id": 1, "candidate_name": "低分"},
			{"friend_id": 2, "candidate_name": "高分"},
			{"friend_id": 3, "candidate_name": "待处理"},
		],
		list_recent_conversations_for_job=lambda _job_id: [
			{"friend_id": 1, "candidate_name": "低分"},
			{"friend_id": 2, "candidate_name": "高分"},
			{"friend_id": 3, "candidate_name": "待处理"},
		],
		recruiting_workspace=RecruitingWorkspace(tmp_path), automation_coordinator=coordinator,
	)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post("/api/recruiting/automation/sync", headers=headers, json={"job_id": "job-java"})
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			payload = await (await client.get("/api/recruiting/automation/candidates?job_id=job-java")).json()
			assert [item["candidate_name"] for item in payload["data"]["qualified"]] == ["高分"]
			assert payload["data"]["qualified"][0]["resume_path"] == str(second)
			assert any(item["candidate_name"] == "待处理" for item in payload["data"]["candidates"])
		finally:
			await client.close()

	asyncio.run(scenario())


def test_automation_resume_open_only_uses_verified_queue_attachment(tmp_path: Path, monkeypatch) -> None:
	"""本地打开接口只能使用队列确认过的附件，不能接受浏览器任意指定路径。"""
	async def scenario() -> None:
		attachment = tmp_path / "verified.pdf"
		attachment.write_bytes(b"verified attachment")
		queue = AutomationQueueStore(tmp_path)
		candidate = queue.upsert_candidate(friend_id=8, job_id="job-java", candidate_name="附件候选人", source="conversation")
		queue.record_final_review(friend_id=8, job_id="job-java", score=80, recommendation="review", resume_path=attachment)
		coordinator = AutomationCoordinator(
			queue=queue,
			sync_conversations=lambda _job_id: [],
			process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
			finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
			greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		)
		opened: list[str] = []
		monkeypatch.setattr("boss_agent_cli.web.runtime.os.startfile", opened.append)
		runtime = LocalConsoleRuntime(
			operating_mode="research", login_in_browser=lambda: None, has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path), automation_coordinator=coordinator,
		)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			allowed = await client.post(f"/api/recruiting/automation/candidates/{candidate.candidate_key}/resume/open", headers=headers, json={})
			assert allowed.status == 202
			assert opened == [str(attachment)]
			blocked = await client.post("/api/recruiting/automation/candidates/job:job-java:friend:999/resume/open", headers=headers, json={})
			assert blocked.status == 404
			assert opened == [str(attachment)]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_online_resume_preview_refreshes_job_list_and_revalidates_friend_id(tmp_path: Path) -> None:
	"""在线简历预览必须先刷新岗位列表，再以真实会话身份打开且不下载文件。"""
	async def scenario() -> None:
		reads: list[str] = []
		opened: list[int] = []
		runtime = LocalConsoleRuntime(
			operating_mode="research",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("预览不能下载")),
			list_recent_conversations_for_job=lambda job_id: reads.append(job_id) or [
				{"friend_id": 81, "candidate_name": "在线候选人"},
			],
		open_online_resume=lambda friend_id: opened.append(friend_id) or {
			"code": 0,
			"candidate_name": "在线候选人",
			"resume_text": "本科 软件工程 Java 项目经历",
		},
			recruiting_workspace=RecruitingWorkspace(tmp_path),
		)
		# 初始页面选择来自上一轮快照；执行时必须重新读取，而不是盲用 DOM 位置。
		runtime._conversation_selections = {"conversation-old": 81}
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post(
				"/api/conversations/conversation-old/online-resume/open",
				headers=headers,
				json={"job_id": "job-java"},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			assert reads == ["job-java"]
			assert opened == [81]
			assert runtime.status()["online_resume_preview"] == {
				"state": "succeeded",
				"job_id": "job-java",
				"friend_id": 81,
				"candidate_name": "在线候选人",
				"resume_text": "本科 软件工程 Java 项目经历",
			}
		finally:
			await client.close()

	asyncio.run(scenario())


def test_online_resume_preview_rejects_boss_conversation_shell_text(tmp_path: Path) -> None:
	"""运行时不能把 BOSS 沟通侧栏文本标记为在线简历成功。"""
	async def scenario() -> None:
		runtime = LocalConsoleRuntime(
			operating_mode="research",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("预览不能下载")),
			list_recent_conversations_for_job=lambda _job_id: [{"friend_id": 81, "candidate_name": "谭钧译"}],
			open_online_resume=lambda _friend_id: {
				"code": 0,
				"candidate_name": "谭钧译",
				"resume_text": (
					"收藏\n转发\n举报\n继续沟通\n沟通中，08月05日 向您发起沟通，沟通职位 Java\n"
					"同事沟通进度\n我的沟通进度\nTa向 王勤径 发起沟通[Java]\n2026-08-05 21:16"
				),
			},
			recruiting_workspace=RecruitingWorkspace(tmp_path),
		)
		runtime._conversation_selections = {"conversation-shell": 81}
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post(
				"/api/conversations/conversation-shell/online-resume/open",
				headers=headers,
				json={"job_id": "job-java"},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			state = runtime.status()["online_resume_preview"]
			assert state["state"] == "failed"
			assert state["error"]["code"] == "ONLINE_RESUME_PREVIEW_FAILED"
			assert "收藏" not in state["error"]["message"]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_automation_web_exposes_job_scoped_timeline_and_final_candidate_pool(tmp_path: Path) -> None:
	"""自动化过程与附件终审候选人池应走独立接口，且时间线不能跨岗位读取。"""
	async def scenario() -> None:
		attachment = tmp_path / "verified.pdf"
		attachment.write_bytes(b"verified attachment")
		queue = AutomationQueueStore(tmp_path)
		candidate = queue.upsert_candidate(friend_id=18, job_id="job-java", candidate_name="候选人", source="recommendation")
		queue.record_final_review(
			friend_id=18,
			job_id="job-java",
			score=88,
			recommendation="invite_to_interview",
			resume_path=attachment,
		)
		transcripts = DialogueTranscriptStore(tmp_path)
		transcripts.record_candidate_message(job_id="job-java", friend_id=18, message_id="m1", text="我有三年 Java 经验。")
		transcripts.record_recruiter_reply(job_id="job-java", friend_id=18, message_id="m1", text="请介绍一个 Spring Boot 项目。")
		coordinator = AutomationCoordinator(
			queue=queue,
			sync_conversations=lambda _job_id: [],
			process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
			finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
			greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		)
		runtime = LocalConsoleRuntime(
			operating_mode="research", login_in_browser=lambda: None, has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path),
			automation_coordinator=coordinator,
			automation_transcript_store=transcripts,
		)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		try:
			detail = await (await client.get(
				f"/api/recruiting/automation/candidates/{candidate.candidate_key}?job_id=job-java"
			)).json()
			assert [turn["role"] for turn in detail["data"]["timeline"]] == ["candidate", "recruiter"]
			assert detail["data"]["candidate"]["candidate_key"] == candidate.candidate_key
			pool = await (await client.get("/api/recruiting/automation/candidate-pool")).json()
			assert [(row["job_id"], row["score"]) for row in pool["data"]["qualified"]] == [("job-java", 88)]
			missing = await client.get(
				f"/api/recruiting/automation/candidates/{candidate.candidate_key}?job_id=job-sales"
			)
			assert missing.status == 404
		finally:
			await client.close()

	asyncio.run(scenario())


def test_automation_web_saves_interview_settings_and_runs_candidate_action(tmp_path: Path) -> None:
	"""候选人操作 API 只能以当前岗位候选人键发起，并返回异步状态。"""
	from boss_agent_cli.recruiting.interview_settings import InterviewInvitationSettingsStore

	async def scenario() -> None:
		queue = AutomationQueueStore(tmp_path)
		candidate = queue.upsert_candidate(friend_id=18, job_id="job-java", candidate_name="候选人", source="conversation")
		coordinator = AutomationCoordinator(
			queue=queue,
			sync_conversations=lambda _job_id: [],
			process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
			finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
			greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		)
		requests: list[tuple[int, str]] = []
		runtime = LocalConsoleRuntime(
			operating_mode="research", login_in_browser=lambda: None, has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path), automation_coordinator=coordinator,
			interview_settings_store=InterviewInvitationSettingsStore(tmp_path),
			request_contact_exchange=lambda friend_id, contact_type: requests.append((friend_id, contact_type)) or {"code": 0},
		)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			saved = await client.post("/api/recruiting/automation/settings", headers=headers, json={
				"job_id": "job-java", "mode": "online", "date": "2026-08-20", "time": "10:00", "note": "请提前进入会议",
			})
			assert saved.status == 200
			action = await client.post(
				f"/api/recruiting/automation/candidates/{candidate.candidate_key}/actions",
				headers=headers,
				json={"job_id": "job-java", "action": "phone"},
			)
			assert action.status == 202
			runtime.wait_for_idle(timeout=1)
			assert requests == [(18, "phone")]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_automation_web_saves_two_independent_schedule_settings(tmp_path: Path) -> None:
	"""设置 API 应分别保存两个按钮配置，并在读取时同时返回。"""
	from boss_agent_cli.recruiting.automation_schedule_settings import AutomationScheduleSettingsStore

	async def scenario() -> None:
		runtime = LocalConsoleRuntime(
			operating_mode="research", login_in_browser=lambda: None, has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path),
			automation_schedule_store=AutomationScheduleSettingsStore(tmp_path),
		)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			for source, job_id, limit in (("conversation", "job-java", 20), ("recommendation", "job-support", 8)):
				response = await client.post("/api/recruiting/automation/schedules", headers=headers, json={
					"source": source, "enabled": True, "job_id": job_id, "start_time": "09:00",
					"end_time": "18:00", "interval_minutes": 30, "limit": limit,
					"daily_quota": 50, "weekdays": [0, 1, 2, 3, 4],
				})
				assert response.status == 200
			payload = await (await client.get("/api/recruiting/automation/schedules")).json()
			assert payload["data"]["conversation"]["job_id"] == "job-java"
			assert payload["data"]["recommendation"]["job_id"] == "job-support"
			assert payload["data"]["recommendation"]["limit"] == 8
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_directly_sets_job_from_natural_language(tmp_path: Path) -> None:
	"""岗位 Agent 接口应直接创建岗位，不能返回待确认的临时草案。"""
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
			response = await client.post(
				"/api/recruiting/jobs/interpret",
				headers=headers,
				json={
					"requirements": "销售顾问；必须有电话销售经验；招商加盟经验优先；不要频繁跳槽",
					"hard_conditions": {"city": "杭州", "salary_range": "10-15K"},
				},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			result = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			assert result["job"]["status"] == "published"
			assert result["job"]["city"] == "杭州"
			assert result["analysis_source"] == "rules"
			assert "draft_id" not in result
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_job_rule_preview_does_not_change_boss_fields(tmp_path: Path) -> None:
	"""规则预览只能返回 AI 拆解结果，不能修改 BOSS 同步岗位的基础字段。"""
	async def scenario() -> None:
		workspace = RecruitingWorkspace(tmp_path)
		created = workspace.create_job(
			name="Java",
			city="广州",
			salary_range="150-200 元/天",
			education_requirement="本科",
			min_experience_years=0,
			criteria_text="必须：Java",
		)
		job_id = created["job"]["job_id"]
		boss_job = workspace.store.get_job(job_id)
		assert boss_job is not None
		boss_job.source = "boss"
		boss_job.platform_job_id = "boss-java"
		workspace.store.update_job(boss_job)
		runtime = LocalConsoleRuntime(
			operating_mode="research",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=workspace,
			recruiting_job_standard_agent=JobStandardAgent(),
		)
		app = create_console_app(runtime, session_token="test-token")
		client = TestClient(TestServer(app))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post(
				"/api/recruiting/jobs/rules/analyze",
				headers=headers,
				json={"job_id": job_id, "requirements": "必须掌握 Spring；分布式经验优先；不要频繁跳槽"},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			recruiting_state = (await (await client.get("/api/state")).json())["data"]["recruiting"]
			assert recruiting_state["operation"] == "analyze-job-rules"
			assert "must_have" in recruiting_state["result"]["analysis"]
			after = workspace.store.get_job(job_id)
			assert after is not None
			assert after.name == "Java"
			assert after.city == "广州"
			assert after.salary_range == "150-200 元/天"
			assert after.education_requirement == "本科"
			assert after.criteria.must_have == ["Java"]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_job_rule_save_updates_only_reviewed_rules(tmp_path: Path) -> None:
	"""规则保存 API 只能提交四类列表，保存后不覆盖同步岗位的职位字段。"""
	async def scenario() -> None:
		workspace = RecruitingWorkspace(tmp_path)
		created = workspace.create_job(
			name="Java",
			city="广州",
			salary_range="150-200 元/天",
			education_requirement="本科",
			criteria_text="必须：Java",
			status="published",
		)
		job_id = created["job"]["job_id"]
		runtime = LocalConsoleRuntime(
			operating_mode="research",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=workspace,
			recruiting_job_standard_agent=JobStandardAgent(),
		)
		client = TestClient(TestServer(create_console_app(runtime, session_token="test-token")))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post(
				"/api/recruiting/jobs/rules",
				headers=headers,
				json={
					"job_id": job_id,
					"rules": {
						"must_have": ["掌握 Spring"],
						"nice_to_have": [],
						"reject_if": [],
						"risk_signals": ["短期多次换工作"],
					},
					"scoring": {
						"weights": {
							"hard_match": 30,
							"experience": 15,
							"professional_qa": 30,
							"communication": 10,
							"stability": 10,
							"location_salary": 5,
						},
						"screening_threshold": 75,
						"recommendation_threshold": 85,
						"professional_qa_threshold": 65,
					},
				},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			after = workspace.store.get_job(job_id)
			assert after is not None
			assert after.name == "Java"
			assert after.city == "广州"
			assert after.criteria.must_have == ["掌握 Spring"]
			assert after.criteria.risk_signals == ["短期多次换工作"]
			assert after.weights["professional_qa"] == 30
			assert after.screening_threshold == 75
			assert after.recommendation_threshold == 85
			assert after.professional_qa_threshold == 65
		finally:
			await client.close()

	asyncio.run(scenario())


def _context_runtime(data_dir: Path) -> LocalConsoleRuntime:
	"""构造支持上下文切换的运行时，平台操作仍使用测试桩。"""
	registry = RecruitingContextRegistry(data_dir)
	return LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=RecruitingWorkspace(data_dir),
		recruiting_context_registry=registry,
		recruiting_workspace_factory=lambda context: RecruitingWorkspace(data_dir, context=context),
	)


def test_single_analysis_allows_the_first_resume_request_only(tmp_path: Path) -> None:
	"""点击分析可触发首次索要简历，重复发送由流水线状态机阻止。

	首次分析在没有已有资料时需要主动打招呼；是否已发过消息、是否已接收附件
	以及是否已有简历必须由流水线统一判断，运行时不能提前关闭这条业务路径。
	"""
	pipeline = MagicMock()
	pipeline.analyze_one.return_value = PipelineStepResult(
		candidate_name="王小明", friend_id=101, status="no_resume",
	)
	pipeline.logger.entries.return_value = []
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		pipeline_operation=pipeline,
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)
	runtime._analysis_tracker = MagicMock()

	runtime._run_single_analysis(friend_id=101, candidate_name="王小明")

	pipeline.analyze_one.assert_called_once_with(
		friend_id=101, candidate_name="王小明", ask_for_resume=True,
	)
	# 未取得简历不能被登记为“已分析”，否则下次检查附件会被错误跳过。
	runtime._analysis_tracker.mark_analyzed.assert_not_called()


def test_batch_analysis_keeps_the_first_resume_request_flow(tmp_path: Path) -> None:
	"""批量入口也必须交给同一状态机，不能悄悄关闭首次打招呼。"""
	pipeline = MagicMock()
	pipeline._platform.friend_list.return_value = {"code": 0}
	pipeline._platform.is_success.return_value = True
	pipeline._platform.unwrap_data.return_value = {
		"friendList": [{"friendId": 101, "name": "王小明"}],
	}
	pipeline.analyze_batch.return_value = PipelineReport(state="succeeded")
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		pipeline_operation=pipeline,
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)
	runtime._analysis_tracker = MagicMock()
	runtime._analysis_tracker.unanalyzed_ids.return_value = [101]

	runtime._run_batch_analysis(limit=20, platform_generation=0)

	assert pipeline.analyze_batch.call_args.kwargs["ask_for_resume"] is True


def test_conversation_list_reports_rpa_target_error_instead_of_empty_success(tmp_path: Path) -> None:
	"""CDP 连到其他项目页面时，工作台必须提示连接目标错误。

	候选人列表为空与 RPA 没有连接到 BOSS 是两种完全不同的状态。这里固定
	错误码，防止前端把错误环境误渲染为“当前没有沟通候选人”。
	"""
	def unavailable_boss_page() -> list[dict[str, object]]:
		raise BossRPAConnectionError("未连接 BOSS 招聘页面")

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		list_recent_conversations=unavailable_boss_page,
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)

	assert runtime.start_conversation_list()["state"] == "running"
	runtime.wait_for_idle(timeout=1)
	state = runtime.status()["conversation_list"]

	assert state["state"] == "failed"
	assert state["error"] == {
		"code": "RPA_TARGET_NOT_READY",
		"message": "RPA 当前未连接 BOSS 招聘页面，请在已登录的 Chrome 中打开 BOSS 招聘端并连接 Bridge 后刷新",
	}


def test_refreshing_conversation_list_does_not_start_attachment_retry_poller(tmp_path: Path, monkeypatch) -> None:
	"""在线简历列表刷新只读 BOSS 会话，不能恢复附件索要或下载重试。"""
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		list_recent_conversations=lambda: [{"friend_id": 101, "candidate_name": "在线候选人"}],
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)
	monkeypatch.setattr(
		runtime,
		"start_retry_poller",
		lambda: (_ for _ in ()).throw(AssertionError("刷新列表不应启动附件重试")),
	)

	assert runtime.start_conversation_list(force=True)["state"] == "running"
	runtime.wait_for_idle(timeout=1)

	assert runtime.status()["conversation_list"]["state"] == "succeeded"


def test_web_command_does_not_start_scheduled_automation_implicitly() -> None:
	"""打开本地控制台不能因历史定时配置而立即恢复附件或消息自动化。"""
	from boss_agent_cli.commands import web as web_command

	assert web_command.web_cmd.callback is not None
	assert "start_automation_schedule_monitor()" not in inspect.getsource(web_command.web_cmd.callback)


def test_saving_enabled_schedule_explicitly_arms_schedule_monitor(tmp_path: Path, monkeypatch) -> None:
	"""只有本次保存并启用定时任务，才允许激活会产生平台写操作的调度器。"""
	from boss_agent_cli.recruiting.automation_schedule_settings import AutomationScheduleSettingsStore

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=RecruitingWorkspace(tmp_path),
		automation_coordinator=MagicMock(),
		automation_schedule_store=AutomationScheduleSettingsStore(tmp_path),
	)
	started: list[bool] = []
	monkeypatch.setattr(runtime, "start_automation_schedule_monitor", lambda: started.append(True))

	runtime.save_automation_schedule_settings(
		source="conversation",
		values={
			"enabled": True,
			"job_id": "job-java",
			"start_time": "09:00",
			"end_time": "18:00",
			"interval_minutes": 20,
			"limit": 20,
			"daily_quota": 100,
			"weekdays": [0, 1, 2, 3, 4],
		},
	)

	assert started == [True]


def test_boss_job_sync_reports_rpa_target_error(tmp_path: Path) -> None:
	"""岗位同步遇到错误 CDP 上下文时，也必须复用相同的恢复提示。"""
	def unavailable_boss_page() -> list[dict[str, str]]:
		raise BossRPAConnectionError("未连接 BOSS 招聘页面")

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=RecruitingWorkspace(tmp_path),
		list_boss_jobs_for_sync=unavailable_boss_page,
	)

	assert runtime.sync_boss_jobs_to_recruiting_workspace() == {
		"state": "failed",
		"error": {
			"code": "RPA_TARGET_NOT_READY",
		"message": "RPA 当前未连接 BOSS 招聘页面，请在已登录的 Chrome 中打开 BOSS 招聘端并连接 Bridge 后刷新",
		},
	}


def test_conversation_list_explains_when_rpa_browser_needs_its_own_login(tmp_path: Path) -> None:
	"""用户主浏览器已登录也不能掩盖 RPA 专用浏览器未登录的状态。"""
	def rpa_browser_needs_login() -> list[dict[str, object]]:
		raise BossRPALoginRequiredError("项目 RPA 浏览器尚未登录 BOSS")

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		list_recent_conversations=rpa_browser_needs_login,
		recruiting_workspace=RecruitingWorkspace(tmp_path),
	)

	assert runtime.start_conversation_list()["state"] == "running"
	runtime.wait_for_idle(timeout=1)
	state = runtime.status()["conversation_list"]

	assert state["error"] == {
		"code": "RPA_BROWSER_LOGIN_REQUIRED",
		"message": "当前 Chrome 的 BOSS 招聘页面尚未登录，请完成登录后刷新",
	}
	assert runtime.status()["login"] == {
		"state": "failed",
		"error": {
			"code": "RPA_BROWSER_LOGIN_REQUIRED",
		"message": "当前 Chrome 的 BOSS 招聘页面尚未登录，请完成登录后刷新",
		},
	}


def test_recruiting_boss_job_sync_writes_platform_jobs_to_workspace(tmp_path: Path) -> None:
	"""同步按钮请求必须将 BOSS 职位镜像到当前岗位工作台。"""
	async def scenario() -> None:
		runtime = LocalConsoleRuntime(
			operating_mode="research",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path),
			list_boss_jobs_for_sync=lambda: [
				{"job_id": "boss-java", "name": "Java 开发工程师", "status": "online"},
				{"job_id": "boss-sales", "name": "销售顾问", "status": "closed"},
			],
		)
		app = create_console_app(runtime, session_token="test-token")
		client = TestClient(TestServer(app))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			response = await client.post("/api/recruiting/jobs/sync-boss", headers=headers)
			assert response.status == 200
			payload = await response.json()
			assert payload["data"]["created"] == 2
			workspace = await (await client.get("/api/recruiting/workspace")).json()
			assert {job["name"] for job in workspace["data"]["jobs"]} == {"Java 开发工程师", "销售顾问"}
		finally:
			await client.close()

	asyncio.run(scenario())


def test_runtime_assessment_uses_configured_ai_reviewer(tmp_path: Path) -> None:
	"""Web 触发评估时应把已配置的 AI 语义层传入工作区。

	运行时只负责注入调用方明确提供的评审器；评分和人工确认仍由工作区领域层
	统一控制。该回归测试防止页面看似有 AI 分析区，实际评估始终退化为规则引擎。
	"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有企业客户开发经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有企业客户开发经验")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	reviewed: list[str] = []

	def ai_reviewer(job_profile: object, resume_text: str) -> AIResumeReview:
		reviewed.append(f"{getattr(job_profile, 'job_id', '')}:{len(resume_text)}")
		return AIResumeReview(
			model="test-model",
			summary="候选人具备岗位要求的企业客户开发经验。",
			semantic_hits=(SemanticHit(criterion="企业客户开发经验", quote="有企业客户开发经验"),),
		)

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
		recruiting_workspace=workspace,
		recruiting_ai_reviewer=ai_reviewer,
	)

	started = runtime.start_recruiting_assessment(job_id=job["job"]["job_id"], candidate_id=candidate["candidate_id"])
	assert started["state"] == "running"
	runtime.wait_for_idle(timeout=1)
	report = runtime.status()["recruiting"]["result"]

	assert reviewed and reviewed[0].startswith(f"{job['job']['job_id']}:")
	assert report["ai_review"]["model"] == "test-model"
	assert report["ai_review"]["summary"] == "候选人具备岗位要求的企业客户开发经验。"
	assert report["review_required"] is True


def test_recruiting_context_switch_keeps_job_data_isolated(tmp_path: Path) -> None:
	"""工作台切换企业后只能看到当前上下文的岗位。"""

	async def scenario() -> None:
		runtime = _context_runtime(tmp_path)
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		headers = {
			"Origin": str(client.make_url("/")).rstrip("/"),
			"X-Boss-Web-Token": "test-token",
		}
		try:
			contexts = await client.get("/api/recruiting/contexts")
			assert contexts.status == 200
			assert (await contexts.json())["data"]["active_context"]["context_key"] == "default:default:default:recruiter"

			switched = await client.post(
				"/api/recruiting/context",
				headers=headers,
				json={"workspace_id": "sales", "account_id": "account-a", "company_id": "company-a", "role": "recruiter"},
			)
			assert switched.status == 202
			assert (await switched.json())["data"]["active_context"]["company_id"] == "company-a"

			created = await client.post(
				"/api/recruiting/jobs",
				headers=headers,
				json={"name": "A 企业销售", "status": "draft"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			first_snapshot = await client.get("/api/recruiting/workspace")
			first_payload = await first_snapshot.json()
			assert first_payload["data"]["context"]["company_id"] == "company-a"
			assert first_payload["data"]["jobs"][0]["name"] == "A 企业销售"

			back = await client.post(
				"/api/recruiting/context",
				headers=headers,
				json={"workspace_id": "default", "account_id": "default", "company_id": "default", "role": "recruiter"},
			)
			assert back.status == 202
			default_snapshot = await client.get("/api/recruiting/workspace")
			default_payload = await default_snapshot.json()
			assert default_payload["data"]["context"]["context_key"] == "default:default:default:recruiter"
			assert all(job["name"] != "A 企业销售" for job in default_payload["data"]["jobs"])
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_persists_professional_qa_toggle(tmp_path: Path) -> None:
	"""岗位 Web 表单关闭专业问答后，工作区读取结果必须保持关闭状态。"""

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
				json={"name": "销售顾问", "professional_qa_enabled": False, "status": "draft"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			payload = await (await client.get("/api/recruiting/workspace")).json()
			assert payload["data"]["jobs"][0]["professional_qa_enabled"] is False
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_boss_job_sync_requires_trusted_research_request(tmp_path: Path) -> None:
	"""职位镜像必须同时受同源令牌与 Research Mode 约束，不能成为平台读取旁路。"""
	async def scenario() -> None:
		runtime = LocalConsoleRuntime(
			operating_mode="assisted",
			login_in_browser=lambda: None,
			has_saved_login=lambda: True,
			download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("本测试不下载")),
			recruiting_workspace=RecruitingWorkspace(tmp_path),
			list_boss_jobs=lambda: [{"job_id": "boss-java", "name": "Java 开发"}],
		)
		app = create_console_app(runtime, session_token="test-token")
		client = TestClient(TestServer(app))
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			forbidden = await client.post("/api/recruiting/jobs/sync-boss")
			assert forbidden.status == 403
			blocked = await client.post("/api/recruiting/jobs/sync-boss", headers=headers)
			assert blocked.status == 403
			assert (await blocked.json())["error"]["code"] == "COMPLIANCE_BLOCKED"
		finally:
			await client.close()
	asyncio.run(scenario())


def test_recruiting_web_answers_question_with_source_or_safe_refusal(tmp_path: Path) -> None:
	"""Web 工作台应提供岗位范围内的受控试答，不命中时明确拒答。"""

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
				json={"name": "销售顾问", "status": "draft"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			faq = await client.post(
				"/api/recruiting/faq",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"question": "工作时间？",
					"answer": "工作时间为 9:00-18:00。",
					"allowed_variation": "",
					"source_title": "员工手册",
					"source_version": "v1",
				},
			)
			assert faq.status == 202
			runtime.wait_for_idle(timeout=1)

			answered = await client.get(
				"/api/recruiting/answer",
				params={"job_id": job["job_id"], "q": "请问工作时间？"},
			)
			assert answered.status == 200
			answered_payload = await answered.json()
			assert answered_payload["data"]["status"] == "answered"
			assert answered_payload["data"]["source_title"] == "员工手册"

			refused = await client.get(
				"/api/recruiting/answer",
				params={"job_id": job["job_id"], "q": "公司是否提供海外搬迁？"},
			)
			assert refused.status == 200
			refused_payload = await refused.json()
			assert refused_payload["data"]["status"] == "no_source"
			assert "暂无可基于当前岗位本地事实" in refused_payload["data"]["answer"]
			workspace_payload = await (await client.get("/api/recruiting/workspace", params={"job_id": job["job_id"]})).json()
			workspace_data = workspace_payload["data"]
			assert len(workspace_data["question_demands"]) == 2
			assert workspace_data["optimization"]["metrics"]["question_demand_rates"]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_knowledge_audience_is_persisted_and_used_for_answers(tmp_path: Path) -> None:
	"""Web 知识范围应持久化，候选人试答不能命中内部资料。"""

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
			await client.post(
				"/api/recruiting/jobs",
				headers=headers,
				json={"name": "销售顾问", "status": "published"},
			)
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/recruiting/workspace")).json())["data"]["jobs"][0]
			created = await client.post(
				"/api/recruiting/knowledge",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"category": "sales",
					"title": "内部规则",
					"content": "底价和客户分层规则不对外公开。",
					"audience": "internal",
				},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert workspace["knowledge"][0]["audience"] == "internal"

			answer = await client.get(
				"/api/recruiting/answer",
				params={"job_id": job["job_id"], "q": "底价和客户分层规则是什么？"},
			)
			assert answer.status == 200
			result = (await answer.json())["data"]
			assert result["status"] == "no_source"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_records_private_professional_qa(tmp_path: Path) -> None:
	"""Web 工作台应把私域专业核验接入同一待办和状态机。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：私域候选人\n有销售经验。", encoding="utf-8")

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
				json={"name": "销售顾问", "professional_qa_enabled": False, "status": "published"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/recruiting/workspace")).json()
			job_id = state["data"]["selected_job_id"]

			imported = await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"resume_path": str(resume_path), "job_id": job_id},
			)
			assert imported.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/recruiting/workspace")).json()
			candidate_id = state["data"]["candidates"][0]["candidate_id"]
			await client.post("/api/recruiting/assess", headers=headers, json={"job_id": job_id, "candidate_id": candidate_id})
			runtime.wait_for_idle(timeout=1)
			await client.post("/api/recruiting/basic-intent", headers=headers, json={"job_id": job_id, "candidate_id": candidate_id, "note": "已确认基础意向"})
			runtime.wait_for_idle(timeout=1)

			response = await client.post(
				"/api/recruiting/private-professional-qa",
				headers=headers,
				json={
					"job_id": job_id,
					"candidate_id": candidate_id,
					"question": "请说明客户开发项目？",
					"answer": "我负责客户开发并完成签约。",
					"question_id": "private-web-question",
					"question_version": "v1",
					"source_ids": ["faq-sales"],
					"outcome": "passed",
				},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			final = await (await client.get("/api/recruiting/workspace")).json()
			assert final["data"]["candidates"][0]["stage"] == "professional_passed"
			assert final["data"]["workflow"]["next_step"] == "review_assessment"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_faq_draft_requires_explicit_save(tmp_path: Path) -> None:
	"""FAQ 草稿接口应返回来源，只有显式保存请求才会进入岗位 FAQ。"""
	knowledge_path = tmp_path / "福利.md"
	knowledge_path.write_text("双休，入职缴纳社保。", encoding="utf-8")

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
			await client.post(
				"/api/recruiting/jobs",
				headers=headers,
				json={"name": "销售顾问", "status": "published"},
			)
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/recruiting/workspace")).json())["data"]["jobs"][0]
			await client.post(
				"/api/recruiting/knowledge/import",
				headers=headers,
				json={"job_id": job["job_id"], "category": "company", "source_path": str(knowledge_path)},
			)
			runtime.wait_for_idle(timeout=1)

			drafts_response = await client.get(f"/api/recruiting/faq-drafts?job_id={job['job_id']}")
			assert drafts_response.status == 200
			drafts_payload = await drafts_response.json()
			draft = drafts_payload["data"]["drafts"][0]
			assert draft["status"] == "pending_review"
			assert draft["source_document_id"]
			before = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert before["faq"] == []

			saved = await client.post(
				"/api/recruiting/faq",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"question": draft["question"],
					"answer": draft["answer"],
					"allowed_variation": "保持原意",
					"source_document_id": draft["source_document_id"],
					"source_title": draft["source_title"],
					"source_version": draft["source_version"],
				},
			)
			assert saved.status == 202
			runtime.wait_for_idle(timeout=1)
			after = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert after["faq"][0]["source_document_id"] == draft["source_document_id"]
			assert after["faq"][0]["review_status"] == "approved"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_post_routes_require_origin_and_token(tmp_path: Path) -> None:
	"""工作台写接口不能被其他本地网页借用。"""
	async def scenario() -> None:
		app = create_console_app(_runtime(tmp_path), session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			response = await client.post("/api/recruiting/jobs", json={"name": "销售顾问"})
			assert response.status == 403
			assert (await response.json())["error"]["code"] == "FORBIDDEN"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_records_message_usage_without_platform_send(tmp_path: Path) -> None:
	"""话术使用接口只能生成本地事实，并返回 manual_only 标记。"""
	async def scenario() -> None:
		runtime = _runtime(tmp_path)
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		headers = {"Origin": str(client.make_url("/")).rstrip("/"), "X-Boss-Web-Token": "test-token"}
		try:
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问", "status": "draft"})
			runtime.wait_for_idle(timeout=1)
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			job_id = workspace["jobs"][0]["job_id"]
			response = await client.post(
				"/api/recruiting/message-usage",
				headers=headers,
				json={"job_id": job_id, "candidate_id": "candidate-1", "template_key": "greeting", "template_version": "v1"},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			payload = (await response.json())["data"]
			assert payload["state"] == "running"
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert workspace["message_template_usages"][0]["platform_action"] == "manual_only"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_search_route_returns_job_scoped_citations(tmp_path: Path) -> None:
	"""知识检索 API 应返回当前岗位引用，并对空问题给出明确参数错误。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	job_id = job["job"]["job_id"]
	workspace.add_knowledge(job_id, category="sales", title="客户开发", content="先做需求诊断，再进行电话跟进。")

	async def scenario() -> None:
		runtime = _runtime(tmp_path)
		app = create_console_app(runtime, session_token="test-token")
		server = TestServer(app)
		client = TestClient(server)
		await client.start_server()
		try:
			response = await client.get(f"/api/recruiting/search?job_id={job_id}&q=电话跟进")
			assert response.status == 200
			payload = await response.json()
			assert payload["data"]["hits"][0]["source_title"] == "客户开发"

			invalid = await client.get(f"/api/recruiting/search?job_id={job_id}&q=")
			assert invalid.status == 400
			assert (await invalid.json())["error"]["code"] == "INVALID_PARAM"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_imports_knowledge_file_and_returns_source_metadata(tmp_path: Path) -> None:
	"""Web 工作台应能导入用户指定的知识文件并返回来源元数据。"""
	source_path = tmp_path / "销售流程.md"
	source_path.write_text("# 销售流程\n\n先做需求诊断，再进行电话跟进。", encoding="utf-8")

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
			job = RecruitingWorkspace(tmp_path).create_job(name="销售顾问", status="published")
			job_id = job["job"]["job_id"]
			response = await client.post(
				"/api/recruiting/knowledge/import",
				headers=headers,
				json={"job_id": job_id, "category": "sales", "source_path": str(source_path)},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			payload = await (await client.get(f"/api/recruiting/workspace?job_id={job_id}")).json()
			row = payload["data"]["knowledge"][0]
			assert row["source_type"] == "markdown"
			assert row["source_path"] == str(source_path.resolve())
			assert row["source_sha256"]
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_answer_keeps_question_metadata(tmp_path: Path) -> None:
	"""回答接口应保留问题版本和知识来源，但状态快照不含回答正文。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

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
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问", "status": "published"})
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"resume_path": str(resume_path), "job_id": job["job_id"]},
			)
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			answer_body = "我会先做需求诊断，再按照客户阶段安排电话跟进。"
			response = await client.post(
				"/api/recruiting/answers",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"candidate_id": candidate["candidate_id"],
					"question": "请结合销售流程说明如何跟进？",
					"answer": answer_body,
					"question_id": "question-kb-1",
					"question_version": "v-kb-1",
					"source_ids": ["kb-1"],
				},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/state")).json()
			result = state["data"]["recruiting"]["result"]
			assert result["question_id"] == "question-kb-1"
			assert result["question_version"] == "v-kb-1"
			assert result["source_ids"] == ["kb-1"]
			assert result["answer_version"] == 1
			assert answer_body not in str(result)
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_workflow_persists_and_returns_only_safe_metadata(tmp_path: Path) -> None:
	"""岗位、简历导入、评估和快照 API 应形成可轮询的人工确认闭环。"""
	resume_body = "# 候选人简历\n\n姓名：周七\n有电话销售经验。"
	resume_path = tmp_path / "周七.md"
	resume_path.write_text(resume_body, encoding="utf-8")

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
				json={"name": "销售顾问", "criteria_text": "必须有电话销售经验；不要按性别筛选"},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/state")).json()
			job = state["data"]["recruiting"]["result"]["job"]
			assert state["data"]["recruiting"]["state"] == "succeeded"
			assert job["readiness"]["ready"] is False
			assert "city" in job["readiness"]["missing_required_fields"]

			imported = await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"resume_path": str(resume_path)},
			)
			assert imported.status == 202
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]

			transition = await client.post(
				f"/api/recruiting/candidates/{candidate['candidate_id']}/stage",
				headers=headers,
				json={
					"stage": "basic_passed",
					"action": "基础条件人工确认",
					"note": "已确认城市和工作节奏",
					"ai_judgment": "基础条件满足",
					"candidate_quote": "我可以接受杭州和单休",
				},
			)
			assert transition.status == 202
			runtime.wait_for_idle(timeout=1)
			pipeline = await (await client.get("/api/recruiting/workspace")).json()
			assert pipeline["data"]["candidates"][0]["stage"] == "basic_passed"
			assert "我可以接受杭州和单休" not in str(pipeline)

			answer = await client.post(
				"/api/recruiting/answers",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"candidate_id": candidate["candidate_id"],
					"question": "请举一个从陌生客户到成交的案例？",
					"answer": "我负责电话开发企业客户，先确认需求，再处理异议并跟进成交。",
				},
			)
			assert answer.status == 202
			runtime.wait_for_idle(timeout=1)

			assessed = await client.post(
				"/api/recruiting/assess",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]},
			)
			assert assessed.status == 202
			runtime.wait_for_idle(timeout=1)
			report = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			assert report["review_required"] is True
			assert report["answer_count"] == 1
			assert set(report["screening"]) >= {"hard_filter", "semantic_match", "risk", "professional_qa"}
			assert "我负责电话开发企业客户" not in str(report)
			assert resume_body not in str(report)

			workspace = await client.get("/api/recruiting/workspace")
			payload = await workspace.json()
			assert workspace.status == 200
			assert payload["data"]["assessments"][0]["decision"] == "待人工确认"
			assert payload["data"]["workflow"]["queue"][0]["candidate_id"] == candidate["candidate_id"]
			assert payload["data"]["workflow"]["queue"][0]["next_action"]
			assert resume_body not in str(payload)

			review = await client.post(
				"/api/recruiting/review",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "proceed", "note": "已人工核对", "manual_override": True, "override_reason": "集成测试已明确模拟 HR 例外放行。"},
			)
			assert review.status == 202
			runtime.wait_for_idle(timeout=1)
			reviewed = await (await client.get("/api/recruiting/workspace")).json()
			assert reviewed["data"]["assessments"][0]["review_status"] == "proceed"
			assert reviewed["data"]["assessments"][0]["review_required"] is False
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_task_completion_advances_stage_through_web_api(tmp_path: Path) -> None:
	"""Web 待办完成接口只推进准备动作，不能把沟通事实伪造成已完成。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")

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
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问", "criteria_text": "必须有电话销售经验"})
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			await client.post("/api/recruiting/candidates/import", headers=headers, json={"resume_path": str(resume_path)})
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			await client.post("/api/recruiting/assess", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]})
			runtime.wait_for_idle(timeout=1)
			await client.post("/api/recruiting/review", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "proceed", "manual_override": True, "override_reason": "集成测试已明确模拟 HR 例外放行。"})
			runtime.wait_for_idle(timeout=1)

			workspace_payload = await (await client.get("/api/recruiting/workspace")).json()
			pending = next(task for task in workspace_payload["data"]["tasks"] if task["status"] == "pending")
			assert pending["kind"] == "prepare_resume_exchange"
			completed = await client.post(
				f"/api/recruiting/tasks/{pending['task_id']}",
				headers=headers,
				json={"status": "completed", "note": "已人工完成简历交换"},
			)
			assert completed.status == 202
			runtime.wait_for_idle(timeout=1)
			result = await (await client.get("/api/recruiting/workspace")).json()
			assert result["data"]["candidates"][0]["stage"] == "resume_exchanged"
			assert any(task["kind"] == "review_resume" and task["status"] == "pending" for task in result["data"]["tasks"])
			assert "候选人简历" not in str(result)
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_reopens_skipped_task(tmp_path: Path) -> None:
	"""跳过的待办可以通过同一 Web 接口恢复，避免前端留下死路。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

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
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问"})
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"resume_path": str(resume_path), "job_id": job["job_id"]},
			)
			runtime.wait_for_idle(timeout=1)
			workspace_payload = await (await client.get(f"/api/recruiting/workspace?job_id={job['job_id']}" )).json()
			pending = next(task for task in workspace_payload["data"]["tasks"] if task["status"] == "pending")
			skipped = await client.post(
				f"/api/recruiting/tasks/{pending['task_id']}",
				headers=headers,
				json={"status": "skipped", "note": "稍后处理"},
			)
			assert skipped.status == 202
			runtime.wait_for_idle(timeout=1)
			reopened = await client.post(
				f"/api/recruiting/tasks/{pending['task_id']}",
				headers=headers,
				json={"status": "pending", "note": "重新安排处理"},
			)
			assert reopened.status == 202
			runtime.wait_for_idle(timeout=1)
			final = await (await client.get(f"/api/recruiting/workspace?job_id={job['job_id']}" )).json()
			assert final["data"]["workflow"]["pending_task_id"] == pending["task_id"]
			assert next(task for task in final["data"]["tasks"] if task["task_id"] == pending["task_id"])["status"] == "pending"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_closes_private_domain_and_interview_loop(tmp_path: Path) -> None:
	"""Web 层必须能把本地私域、面试和录用决定串起来并保持同源保护。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

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
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问", "criteria_text": "必须有销售经验"})
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			await client.post("/api/recruiting/candidates/import", headers=headers, json={"resume_path": str(resume_path)})
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			assessed = await client.post(
				"/api/recruiting/assess",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]},
			)
			assert assessed.status == 202
			runtime.wait_for_idle(timeout=1)
			review = await client.post(
				"/api/recruiting/review",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "proceed", "manual_override": True, "override_reason": "集成测试已明确模拟 HR 例外放行。"},
			)
			assert review.status == 202
			runtime.wait_for_idle(timeout=1)

			contact = await client.post(
				"/api/recruiting/private-contacts",
				headers=headers,
				json={"candidate_id": candidate["candidate_id"], "channel": "wechat", "status": "added", "note": "已手动添加"},
			)
			assert contact.status == 202
			runtime.wait_for_idle(timeout=1)
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			prepare = next(task for task in workspace["tasks"] if task["kind"] == "prepare_interview" and task["status"] == "pending")
			await client.post(f"/api/recruiting/tasks/{prepare['task_id']}", headers=headers, json={"status": "completed"})
			runtime.wait_for_idle(timeout=1)

			invite = await client.post(
				"/api/recruiting/interviews",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "scheduled_at": "2026-08-03 14:00", "interviewer": "王主管"},
			)
			assert invite.status == 202
			runtime.wait_for_idle(timeout=1)
			interview_result = await client.post(
				"/api/recruiting/interviews/result",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "passed", "note": "通过"},
			)
			assert interview_result.status == 202
			runtime.wait_for_idle(timeout=1)
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			decision_task = next(task for task in workspace["tasks"] if task["kind"] == "record_hiring_decision" and task["status"] == "pending")
			completed = await client.post(
				f"/api/recruiting/tasks/{decision_task['task_id']}",
				headers=headers,
				json={"status": "completed", "target_stage": "rejected", "note": "岗位编制调整"},
			)
			assert completed.status == 202
			runtime.wait_for_idle(timeout=1)
			final = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert final["candidates"][0]["stage"] == "rejected"
			assert final["decisions"][0]["outcome"] == "rejected"
			assert final["interviews"][0]["status"] == "completed"
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_records_communication_round_and_follow_up(tmp_path: Path) -> None:
	"""Web 层应能提交一轮沟通，并在快照里返回下一轮待跟进信息。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

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
			await client.post("/api/recruiting/jobs", headers=headers, json={"name": "销售顾问", "criteria_text": "必须有销售经验"})
			runtime.wait_for_idle(timeout=1)
			job = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]["job"]
			await client.post("/api/recruiting/candidates/import", headers=headers, json={"resume_path": str(resume_path)})
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]
			await client.post("/api/recruiting/assess", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]})
			runtime.wait_for_idle(timeout=1)
			await client.post("/api/recruiting/review", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "proceed", "manual_override": True, "override_reason": "集成测试已明确模拟 HR 例外放行。"})
			runtime.wait_for_idle(timeout=1)
			workspace_payload = await (await client.get("/api/recruiting/workspace")).json()
			resume_exchange = next(task for task in workspace_payload["data"]["tasks"] if task["kind"] == "prepare_resume_exchange" and task["status"] == "pending")
			await client.post(
				f"/api/recruiting/tasks/{resume_exchange['task_id']}",
				headers=headers,
				json={"status": "completed", "note": "已人工完成简历交换"},
			)
			runtime.wait_for_idle(timeout=1)
			await client.post("/api/recruiting/assess", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]})
			runtime.wait_for_idle(timeout=1)
			await client.post("/api/recruiting/review", headers=headers, json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"], "outcome": "proceed", "manual_override": True, "override_reason": "集成测试覆盖简历复评后的沟通入口。"})
			runtime.wait_for_idle(timeout=1)

			response = await client.post(
				"/api/recruiting/communications",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"candidate_id": candidate["candidate_id"],
					"round_number": 1,
					"outcome": "follow_up",
					"candidate_reply_summary": "需要确认薪资结构",
					"next_follow_up_at": "2026-08-04 10:00",
					"template_key": "greeting",
					"template_version": "v1",
				},
			)
			assert response.status == 202
			runtime.wait_for_idle(timeout=1)
			payload = await (await client.get("/api/recruiting/workspace")).json()
			assert payload["data"]["communications"][0]["outcome"] == "follow_up"
			assert payload["data"]["communications"][0]["template_key"] == "greeting"
			assert payload["data"]["communications"][0]["template_version"] == "v1"
			assert payload["data"]["candidates"][0]["next_follow_up_at"] == "2026-08-04 10:00"
			assert any(task["communication_round"] == 2 for task in payload["data"]["tasks"] if task["status"] == "pending")
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_publishes_job_and_records_mismatch_feedback(tmp_path: Path) -> None:
	"""Web 工作台应把岗位发布、岗位绑定和不匹配回填串成一条可轮询流程。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

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
				json={
					"name": "销售顾问",
					"city": "杭州",
					"salary_range": "10-20K",
					"criteria_text": "本科；3年以上工作经验；必须有销售经验",
					"status": "draft",
				},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/state")).json()
			job = state["data"]["recruiting"]["result"]["job"]
			assert job["status"] == "draft"

			imported = await client.post(
				"/api/recruiting/candidates/import",
				headers=headers,
				json={"resume_path": str(resume_path), "job_id": job["job_id"]},
			)
			assert imported.status == 202
			runtime.wait_for_idle(timeout=1)
			candidate = (await (await client.get("/api/state")).json())["data"]["recruiting"]["result"]

			blocked = await client.post(
				"/api/recruiting/assess",
				headers=headers,
				json={"job_id": job["job_id"], "candidate_id": candidate["candidate_id"]},
			)
			assert blocked.status == 202
			runtime.wait_for_idle(timeout=1)
			blocked_state = (await (await client.get("/api/state")).json())["data"]["recruiting"]
			assert blocked_state["state"] == "failed"
			assert "岗位尚未发布" in blocked_state["error"]["message"]

			published = await client.post(
				f"/api/recruiting/jobs/{job['job_id']}/status",
				headers=headers,
				json={"status": "published"},
			)
			assert published.status == 202
			runtime.wait_for_idle(timeout=1)
			published_state = (await (await client.get("/api/state")).json())["data"]["recruiting"]
			assert published_state["state"] == "succeeded"
			assert published_state["result"]["status"] == "published"

			feedback = await client.post(
				"/api/recruiting/mismatch-feedback",
				headers=headers,
				json={
					"job_id": job["job_id"],
					"candidate_id": candidate["candidate_id"],
					"reason_code": "city_mismatch",
					"stage": "hard_filter",
					"note": "候选人期望城市不一致",
				},
			)
			assert feedback.status == 202
			runtime.wait_for_idle(timeout=1)
			workspace = (await (await client.get("/api/recruiting/workspace")).json())["data"]
			assert workspace["mismatch_feedback"][0]["reason_code"] == "city_mismatch"
			assert workspace["mismatch_feedback"][0]["submitted_to_platform"] is False
			assert workspace["optimization"]["metrics"]["mismatch_reason_rates"]["city_mismatch"] == {
				"count": 1,
				"rate": 100.0,
			}
		finally:
			await client.close()

	asyncio.run(scenario())


def test_recruiting_web_api_accepts_explicit_job_readiness_fields(tmp_path: Path) -> None:
	"""Web 表单填写学历和年限后，发布接口应能直接通过完整性门禁。"""

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
				json={
					"name": "电话销售顾问",
					"city": "杭州",
					"salary_range": "10-20K",
					"education_requirement": "大专及以上",
					"min_experience_years": 2,
					"criteria_text": "必须有电话销售经验",
					"status": "draft",
				},
			)
			assert created.status == 202
			runtime.wait_for_idle(timeout=1)
			state = await (await client.get("/api/state")).json()
			job = state["data"]["recruiting"]["result"]["job"]
			assert job["education_requirement"] == "大专及以上"
			assert job["min_experience_years"] == 2
			assert job["readiness"]["ready"] is True

			published = await client.post(
				f"/api/recruiting/jobs/{job['job_id']}/status",
				headers=headers,
				json={"status": "published"},
			)
			assert published.status == 202
			runtime.wait_for_idle(timeout=1)
			published_state = (await (await client.get("/api/state")).json())["data"]["recruiting"]
			assert published_state["state"] == "succeeded"
			assert published_state["result"]["status"] == "published"
		finally:
			await client.close()

	asyncio.run(scenario())
