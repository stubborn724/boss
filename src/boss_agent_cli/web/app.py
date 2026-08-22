"""本地控制台的 aiohttp 路由与同源写请求保护。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web

from boss_agent_cli.commands.recruiter.batch_resume_export import (
	AVAILABLE_MODES,
	AVAILABLE_SOURCES,
	MAX_LIMIT,
	MODE_EXPORT,
	SOURCE_CONVERSATION,
)
from boss_agent_cli.web.assets import render_console_page
from boss_agent_cli.web.runtime import LocalConsoleRuntime
from boss_agent_cli.recruiting.context import RecruitingContext
from boss_agent_cli.web.score_board_preview import render_score_board_preview

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
	app.router.add_get("/preview/score-board", _score_board_preview)
	app.router.add_get("/api/state", _state)
	app.router.add_get("/api/conversations", _conversations)
	app.router.add_get("/api/recommendations", _recommendations)
	app.router.add_get("/api/recruiting/workspace", _recruiting_workspace)
	app.router.add_get("/api/recruiting/automation/candidates", _recruiting_automation_candidates)
	app.router.add_get("/api/recruiting/automation/candidate-pool", _recruiting_automation_candidate_pool)
	app.router.add_get("/api/recruiting/automation/candidate-pool/export", _recruiting_automation_candidate_pool_export)
	app.router.add_get("/api/recruiting/automation/settings", _recruiting_automation_settings)
	app.router.add_get("/api/recruiting/automation/followup-settings", _recruiting_automation_followup_settings)
	app.router.add_get("/api/recruiting/automation/schedules", _recruiting_automation_schedules)
	app.router.add_get("/api/recruiting/automation/candidates/{candidate_key}", _recruiting_automation_candidate_detail)
	app.router.add_get("/api/recruiting/contexts", _recruiting_contexts)
	app.router.add_get("/api/recruiting/search", _recruiting_search)
	app.router.add_get("/api/recruiting/answer", _recruiting_question_answer)
	app.router.add_get("/api/recruiting/faq-drafts", _recruiting_faq_drafts)
	app.router.add_post("/api/recruiting/optimization-drafts", _recruiting_optimization_draft)
	app.router.add_post("/api/recruiting/optimization-drafts/{draft_id}", _recruiting_optimization_draft_review)
	app.router.add_post("/api/login", _login)
	app.router.add_post("/api/recruiting/context", _recruiting_context)
	app.router.add_post("/api/recruiting/automation/sync", _recruiting_automation_sync)
	app.router.add_post("/api/recruiting/automation/start", _recruiting_automation_start)
	app.router.add_post("/api/recruiting/automation/pause", _recruiting_automation_pause)
	app.router.add_post("/api/recruiting/automation/resume", _recruiting_automation_resume)
	app.router.add_post("/api/recruiting/automation/stop", _recruiting_automation_stop)
	app.router.add_post("/api/recruiting/automation/candidates/{candidate_key}/resume/open", _recruiting_automation_open_resume)
	app.router.add_post("/api/recruiting/automation/settings", _recruiting_automation_save_settings)
	app.router.add_post("/api/recruiting/automation/followup-settings", _recruiting_automation_save_followup_settings)
	app.router.add_post("/api/recruiting/automation/schedules", _recruiting_automation_save_schedule)
	app.router.add_post("/api/recruiting/automation/candidates/{candidate_key}/actions", _recruiting_automation_candidate_action)
	app.router.add_post("/api/recruiting/jobs/sync-boss", _recruiting_sync_boss_jobs)
	app.router.add_post("/api/recruiting/jobs/rules/analyze", _recruiting_job_rule_analysis)
	# 必须在 ``/jobs/{job_id}`` 动态路由前注册，否则 "rules" 会被误识别为岗位 ID。
	app.router.add_post("/api/recruiting/jobs/rules", _recruiting_job_rule_apply)
	app.router.add_post("/api/recruiting/jobs/interpret", _recruiting_job_interpret)
	app.router.add_post("/api/recruiting/jobs", _recruiting_jobs)
	app.router.add_post("/api/recruiting/jobs/{job_id}", _recruiting_job_update)
	app.router.add_post("/api/recruiting/jobs/{job_id}/status", _recruiting_job_status)
	app.router.add_post("/api/recruiting/knowledge", _recruiting_knowledge)
	app.router.add_post("/api/recruiting/knowledge/import", _recruiting_knowledge_import)
	app.router.add_post("/api/recruiting/faq", _recruiting_faq)
	app.router.add_post("/api/recruiting/candidates/import", _recruiting_candidate_import)
	app.router.add_post("/api/recruiting/candidates/auto-assign", _recruiting_auto_assignment)
	app.router.add_post("/api/recruiting/mismatch-feedback", _recruiting_mismatch_feedback)
	app.router.add_post("/api/recruiting/candidates/{candidate_id}/stage", _recruiting_candidate_stage)
	app.router.add_post("/api/recruiting/answers", _recruiting_answer)
	app.router.add_post("/api/recruiting/private-professional-qa", _recruiting_private_professional_qa)
	app.router.add_post("/api/recruiting/communications", _recruiting_communication)
	app.router.add_post("/api/recruiting/message-usage", _recruiting_message_usage)
	app.router.add_post("/api/recruiting/assess", _recruiting_assess)
	app.router.add_post("/api/recruiting/review", _recruiting_review)
	app.router.add_post("/api/recruiting/tasks/{task_id}", _recruiting_task_update)
	app.router.add_post("/api/recruiting/basic-intent", _recruiting_basic_intent)
	app.router.add_post("/api/recruiting/private-contacts", _recruiting_private_contact)
	app.router.add_post("/api/recruiting/interviews", _recruiting_interview)
	app.router.add_post("/api/recruiting/interviews/result", _recruiting_interview_result)
	app.router.add_post("/api/resume-download", _resume_download)
	app.router.add_post("/api/conversation-resume-download", _conversation_resume_download)
	app.router.add_post("/api/current-conversation-resume-download", _current_conversation_resume_download)
	app.router.add_post("/api/latest-conversation-resume-download", _latest_conversation_resume_download)
	app.router.add_post("/api/conversations/{selection_id}/resume-download", _selected_conversation_resume_download)
	app.router.add_post("/api/conversations/{selection_id}/details", _selected_conversation_detail)
	app.router.add_post("/api/conversations/{selection_id}/online-resume/open", _selected_conversation_online_resume_open)
	app.router.add_post("/api/recommendations/{selection_id}/resume-download", _selected_recommendation_resume_download)
	app.router.add_post("/api/batch-export", _batch_export)
	app.router.add_post("/api/batch-export/stop", _batch_export_stop)
	app.router.add_post("/api/pipeline/start", _pipeline_start)
	app.router.add_post("/api/pipeline/stop", _pipeline_stop)
	app.router.add_post("/api/pipeline/analyze-one", _pipeline_analyze_one)
	app.router.add_post("/api/pipeline/analyze-all", _pipeline_analyze_all)
	app.router.add_get("/api/boss-jobs", _boss_jobs)
	app.router.add_get("/api/recruiting/templates", _recruiting_templates)
	app.router.add_post("/api/recruiting/templates", _recruiting_template_save)
	app.router.add_post("/api/recruiting/templates/{template_id}/delete", _recruiting_template_delete)
	return app


def _json_error(code: str, message: str, *, status: int) -> web.Response:
	"""构造不携带底层异常详情的统一 API 错误响应。"""
	return web.json_response({"ok": False, "error": {"code": code, "message": message}}, status=status)


async def _json_payload(request: web.Request) -> dict[str, Any] | None:
	"""读取写接口 JSON；所有自动化入口共用相同的类型边界。"""
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return None
	return payload if isinstance(payload, dict) else None


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


async def _score_board_preview(request: web.Request) -> web.Response:
	"""返回评分看板视觉稿，供需求确认使用且不触发任何业务操作。"""
	return web.Response(
		text=render_score_board_preview(session_token=request.app[_SESSION_TOKEN_KEY]),
		content_type="text/html",
	)


async def _state(request: web.Request) -> web.Response:
	"""返回脱敏状态快照，供页面轮询更新控件。"""
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.status()})


async def _conversations(request: web.Request) -> web.Response:
	"""读取最近沟通候选人；岗位参数会驱动 BOSS 对应岗位的沟通列表。"""
	runtime = request.app[_RUNTIME_KEY]
	# GET 默认服务已有的成功快照，避免页面重载触发无意义的连续平台请求。
	# 仅本地页面“刷新列表”按钮传 ``refresh=1``。岗位标识只用于选择 BOSS
	# 沟通页的职位筛选，不携带候选人或会话数据。
	job_id = request.query.get("job_id")
	if job_id is not None:
		job_id = job_id.strip() or None
		if job_id is not None and len(job_id) > 128:
			return _json_error("INVALID_PARAM", "职位标识过长", status=400)
	result = runtime.start_conversation_list(force=request.query.get("refresh") == "1", job_id=job_id)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recommendations(request: web.Request) -> web.Response:
	"""读取推荐牛人列表；只有页面明确刷新时才触发平台请求。"""
	runtime = request.app[_RUNTIME_KEY]
	job_id = request.query.get("job_id")
	if job_id is not None:
		job_id = job_id.strip() or None
		if job_id is not None and len(job_id) > 128:
			return _json_error("INVALID_PARAM", "职位标识过长", status=400)
	result = runtime.start_recommendations(job_id=job_id, force=request.query.get("refresh") == "1")
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_workspace(request: web.Request) -> web.Response:
	"""读取岗位、知识库、FAQ 和候选人元数据快照。"""
	runtime = request.app[_RUNTIME_KEY]
	job_id = request.query.get("job_id")
	if job_id is not None:
		job_id = job_id.strip() or None
		if job_id is not None and len(job_id) > 128:
			return _json_error("INVALID_PARAM", "岗位标识过长", status=400)
	try:
		data = runtime.recruiting_snapshot(job_id)
	except KeyError:
		return _json_error("NOT_FOUND", "岗位不存在，请刷新工作区后重试", status=404)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘工作台", status=503)
	except Exception:
		return _json_error("WORKSPACE_FAILED", "招聘工作区读取失败，请检查本地数据后重试", status=500)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_contexts(request: web.Request) -> web.Response:
	"""读取可切换的招聘上下文元数据，不触碰账号凭据。"""
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.recruiting_contexts()})


async def _recruiting_context(request: web.Request) -> web.Response:
	"""登记并激活上下文；切换不会自动打开平台页面或伪造登录。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	try:
		context = RecruitingContext(
			workspace_id=_required_text(payload, "workspace_id") or "default",
			account_id=_required_text(payload, "account_id") or "default",
			company_id=_required_text(payload, "company_id") or "default",
			role=_required_text(payload, "role") or "recruiter",
		)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	runtime = request.app[_RUNTIME_KEY]
	try:
		data = runtime.switch_recruiting_context(context)
	except RuntimeError as exc:
		return _json_error("CONTEXT_SWITCH_FAILED", str(exc), status=409)
	return web.json_response({"ok": True, "data": data}, status=202)


async def _recruiting_search(request: web.Request) -> web.Response:
	"""检索当前岗位本地知识和 FAQ，返回带来源的短摘录。"""
	job_id = request.query.get("job_id", "").strip()
	query = request.query.get("q", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "检索需要有效的岗位标识", status=400)
	if not query or len(query) > 200:
		return _json_error("INVALID_PARAM", "检索问题不能为空且最多 200 个字符", status=400)
	runtime = request.app[_RUNTIME_KEY]
	try:
		data = runtime.search_recruiting_knowledge(job_id, query)
	except KeyError:
		return _json_error("NOT_FOUND", "岗位不存在，请刷新工作区后重试", status=404)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘工作台", status=503)
	except ValueError:
		return _json_error("INVALID_PARAM", "检索问题无效", status=400)
	except Exception:
		return _json_error("SEARCH_FAILED", "知识库检索失败，请检查本地数据后重试", status=500)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_candidates(request: web.Request) -> web.Response:
	"""读取岗位统一候选人队列及评分降序的合格候选人投影。"""
	job_id = request.query.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "读取自动化候选人需要有效岗位标识", status=400)
	try:
		threshold = int(request.query.get("threshold", "80"))
	except ValueError:
		threshold = 80
	if not 0 <= threshold <= 100:
		return _json_error("INVALID_PARAM", "入选阈值必须在 0 到 100 之间", status=400)
	try:
		data = request.app[_RUNTIME_KEY].automation_candidates(job_id=job_id, qualified_threshold=threshold)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘自动化", status=503)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_candidate_pool(request: web.Request) -> web.Response:
	"""读取只含附件终审通过记录的跨岗位候选人池。"""
	try:
		threshold = int(request.query.get("threshold", "80"))
	except ValueError:
		threshold = 80
	if not 0 <= threshold <= 100:
		return _json_error("INVALID_PARAM", "入选阈值必须在 0 到 100 之间", status=400)
	try:
		data = request.app[_RUNTIME_KEY].automation_candidate_pool(qualified_threshold=threshold)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘自动化", status=503)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_candidate_pool_export(request: web.Request) -> web.Response:
	"""下载当前岗位全部达标候选人的联系方式和面试状态。"""
	job_id = request.query.get("job_id", "").strip()
	file_format = request.query.get("format", "csv").strip().lower()
	if not job_id or len(job_id) > 128 or file_format not in {"csv", "xlsx"}:
		return _json_error("INVALID_PARAM", "导出需要有效岗位和 csv/xlsx 格式", status=400)
	try:
		content, content_type, filename = request.app[_RUNTIME_KEY].export_automation_candidate_pool(job_id=job_id, file_format=file_format)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘自动化", status=503)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	return web.Response(
		body=content,
		content_type=content_type.split(";", 1)[0],
		charset="utf-8" if file_format == "csv" else None,
		headers={"Content-Disposition": f'attachment; filename="{filename}"'},
	)


async def _recruiting_automation_candidate_detail(request: web.Request) -> web.Response:
	"""读取岗位内一位候选人的自动化阶段和已处理 AI 对话时间线。"""
	job_id = request.query.get("job_id", "").strip()
	candidate_key = request.match_info.get("candidate_key", "").strip()
	if not job_id or len(job_id) > 128 or not candidate_key or len(candidate_key) > 320:
		return _json_error("INVALID_PARAM", "读取对话需要有效岗位和候选人标识", status=400)
	try:
		data = request.app[_RUNTIME_KEY].automation_candidate_detail(job_id=job_id, candidate_key=candidate_key)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘自动化", status=503)
	if data is None:
		return _json_error("NOT_FOUND", "候选人不属于当前岗位或尚未同步", status=404)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_sync(request: web.Request) -> web.Response:
	"""显式触发只读 BOSS 沟通列表同步。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	if not job_id:
		return _json_error("INVALID_PARAM", "同步沟通列表需要岗位标识", status=400)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].start_automation_sync(job_id=job_id)}, status=202)


async def _recruiting_automation_start(request: web.Request) -> web.Response:
	"""启动指定来源的统一自动化循环。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	source = _required_text(payload, "source")
	limit = _required_positive_int(payload, "limit") or 20
	if not job_id or source not in {"conversation", "recommendation", "full_flow"} or limit > 50:
		return _json_error("INVALID_PARAM", "自动化需要岗位、有效来源和 1 到 50 的处理数量", status=400)
	result = request.app[_RUNTIME_KEY].start_automation(job_id=job_id, source=source, limit=limit)
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result.get("state") == "failed":
		return web.json_response({"ok": False, "error": result.get("error", {})}, status=503)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_automation_pause(request: web.Request) -> web.Response:
	"""暂停自动化循环。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].pause_automation()}, status=202)


async def _recruiting_automation_resume(request: web.Request) -> web.Response:
	"""恢复自动化循环。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].resume_automation()}, status=202)


async def _recruiting_automation_stop(request: web.Request) -> web.Response:
	"""停止自动化循环。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].stop_automation()}, status=202)


async def _recruiting_automation_open_resume(request: web.Request) -> web.Response:
	"""只打开该候选人已验证的本地附件，路径不从浏览器请求体接收。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	candidate_key = request.match_info.get("candidate_key", "").strip()
	# 候选人键已经同时包含岗位和 BOSS 会话，路径仍不由浏览器决定：后续必须
	# 经过队列中的已验证附件白名单解析，不能借此打开任意本地文件。
	if not candidate_key.startswith("job:") or len(candidate_key) > 320:
		return _json_error("INVALID_PARAM", "候选人标识无效", status=400)
	result = request.app[_RUNTIME_KEY].open_automation_resume(candidate_key=candidate_key)
	if result.get("state") == "failed":
		return web.json_response({"ok": False, "error": result.get("error", {})}, status=404)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_automation_settings(request: web.Request) -> web.Response:
	"""读取当前岗位的约面试设置，不暴露其它岗位的执行配置。"""
	job_id = request.query.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "读取面试设置需要有效岗位标识", status=400)
	try:
		data = request.app[_RUNTIME_KEY].automation_interview_settings(job_id=job_id)
	except (RuntimeError, ValueError):
		return _json_error("NOT_SUPPORTED", "当前控制台未配置面试设置", status=503)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_followup_settings(request: web.Request) -> web.Response:
	"""读取岗位独立的换联系方式和约面试自动化开关。"""
	job_id = request.query.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "读取后续动作设置需要有效岗位标识", status=400)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].automation_followup_settings(job_id=job_id)})


async def _recruiting_automation_save_settings(request: web.Request) -> web.Response:
	"""保存岗位级约面试设置，写请求须同时通过本地令牌和同源校验。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	if not job_id:
		return _json_error("INVALID_PARAM", "保存面试设置需要岗位标识", status=400)
	try:
		data = request.app[_RUNTIME_KEY].save_automation_interview_settings(job_id=job_id, values=payload)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置面试设置", status=503)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_save_followup_settings(request: web.Request) -> web.Response:
	"""保存岗位独立后续动作开关，不能影响面试表单或其它岗位。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	if not job_id:
		return _json_error("INVALID_PARAM", "保存后续动作设置需要岗位标识", status=400)
	return web.json_response({"ok": True, "data": request.app[_RUNTIME_KEY].save_automation_followup_settings(job_id=job_id, values=payload)})


async def _recruiting_automation_schedules(request: web.Request) -> web.Response:
	"""读取两个自动化按钮各自的定时配置和进程内状态。"""
	try:
		data = request.app[_RUNTIME_KEY].automation_schedule_settings()
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置自动化定时任务", status=503)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_automation_save_schedule(request: web.Request) -> web.Response:
	"""保存单一来源定时配置，另一个按钮的设置不受影响。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	source = _required_text(payload, "source")
	if source not in {"conversation", "recommendation", "full_flow"}:
		return _json_error("INVALID_PARAM", "定时任务来源无效", status=400)
	try:
		data = request.app[_RUNTIME_KEY].save_automation_schedule_settings(source=source, values=payload)
	except (TypeError, ValueError) as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置自动化定时任务", status=503)
	return web.json_response({"ok": True, "data": data})


async def _selected_conversation_online_resume_open(request: web.Request) -> web.Response:
	"""刷新岗位沟通列表后，仅打开所选候选人的 BOSS 在线简历预览。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	selection_id = request.match_info.get("selection_id", "").strip()
	job_id = _required_text(payload, "job_id")
	if not selection_id or len(selection_id) > 128 or not job_id:
		return _json_error("INVALID_PARAM", "在线简历预览需要候选人和岗位标识", status=400)
	result = request.app[_RUNTIME_KEY].start_online_resume_preview(selection_id=selection_id, job_id=job_id)
	if result.get("state") in {"blocked", "failed"}:
		return web.json_response({"ok": False, "error": result.get("error", {})}, status=403 if result.get("state") == "blocked" else 409)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_automation_candidate_action(request: web.Request) -> web.Response:
	"""提交候选人单项 BOSS 操作，后台只使用队列中的真实会话标识。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	payload = await _json_payload(request)
	if payload is None:
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	action = _required_text(payload, "action")
	candidate_key = request.match_info.get("candidate_key", "").strip()
	if not job_id or action not in {"phone", "wechat", "interview"} or not candidate_key.startswith("job:") or len(candidate_key) > 320:
		return _json_error("INVALID_PARAM", "候选人操作参数无效", status=400)
	result = request.app[_RUNTIME_KEY].start_automation_candidate_action(
		job_id=job_id,
		candidate_key=candidate_key,
		action=action,
	)
	if result.get("state") in {"blocked", "failed"}:
		return web.json_response({"ok": False, "error": result.get("error", {})}, status=403 if result.get("state") == "blocked" else 409)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_sync_boss_jobs(request: web.Request) -> web.Response:
	"""触发一次受保护的 BOSS 职位只读镜像，同步结果写入本地工作台。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.sync_boss_jobs_to_recruiting_workspace()
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result.get("state") == "failed":
		return web.json_response({"ok": False, "error": result["error"]}, status=503)
	return web.json_response({"ok": True, "data": result["result"]})


async def _recruiting_question_answer(request: web.Request) -> web.Response:
	"""基于当前岗位本地事实试答候选人问题，并保留来源元数据。"""
	job_id = request.query.get("job_id", "").strip()
	question = request.query.get("q", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "试答需要有效的岗位标识", status=400)
	if not question or len(question) > 200:
		return _json_error("INVALID_PARAM", "候选人问题不能为空且最多 200 个字符", status=400)
	runtime = request.app[_RUNTIME_KEY]
	try:
		data = runtime.answer_recruiting_question(job_id, question)
	except KeyError:
		return _json_error("NOT_FOUND", "岗位不存在，请刷新工作区后重试", status=404)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘工作台", status=503)
	except ValueError:
		return _json_error("INVALID_PARAM", "候选人问题无效", status=400)
	except Exception:
		return _json_error("ANSWER_FAILED", "本地知识试答失败，请检查岗位知识后重试", status=500)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_faq_drafts(request: web.Request) -> web.Response:
	"""读取当前岗位 FAQ 草稿，明确标记为待审核且不自动入库。"""
	job_id = request.query.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "读取 FAQ 草稿需要有效的岗位标识", status=400)
	runtime = request.app[_RUNTIME_KEY]
	try:
		data = runtime.recruiting_faq_drafts(job_id)
	except KeyError:
		return _json_error("NOT_FOUND", "岗位不存在，请刷新工作区后重试", status=404)
	except RuntimeError:
		return _json_error("NOT_SUPPORTED", "当前控制台未配置招聘工作台", status=503)
	except ValueError:
		return _json_error("INVALID_PARAM", "岗位标识无效", status=400)
	return web.json_response({"ok": True, "data": data})


async def _recruiting_optimization_draft(request: web.Request) -> web.Response:
	"""显式保存一条复盘建议，重复请求由 Store 按建议 ID 幂等处理。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	suggestion_id = _required_text(payload, "suggestion_id")
	if not job_id or not suggestion_id:
		return _json_error("INVALID_PARAM", "生成改进草稿需要岗位和建议标识", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_optimization_draft(job_id=job_id, suggestion_id=suggestion_id)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_optimization_draft_review(request: web.Request) -> web.Response:
	"""保存复盘草稿审核状态；状态变化只代表人工决定，不代表配置已生效。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	draft_id = request.match_info.get("draft_id", "").strip()
	if not draft_id or len(draft_id) > 128:
		return _json_error("INVALID_PARAM", "改进草稿标识无效", status=400)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	status = _required_text(payload, "status")
	if status not in {"pending_review", "accepted", "ignored"}:
		return _json_error("INVALID_PARAM", "改进草稿状态只能是 pending_review、accepted 或 ignored", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_optimization_draft_review(
		draft_id=draft_id,
		status=status,
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_jobs(request: web.Request) -> web.Response:
	"""校验岗位表单并启动岗位创建任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	name = _required_text(payload, "name")
	if not name:
		return _json_error("INVALID_PARAM", "岗位名称不能为空", status=400)
	runtime = request.app[_RUNTIME_KEY]
	status = _required_text(payload, "status") or "published"
	if status not in {"draft", "published"}:
		return _json_error("INVALID_PARAM", "岗位状态只能是 draft 或 published", status=400)
	try:
		min_experience_years = _optional_nonnegative_int(payload, "min_experience_years")
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	try:
		professional_qa_enabled = _optional_bool(payload, "professional_qa_enabled", default=True)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	if professional_qa_enabled is None:
		professional_qa_enabled = True
	result = runtime.start_recruiting_job(
		name=name,
		city=_required_text(payload, "city"),
		salary_range=_required_text(payload, "salary_range"),
		education_requirement=_required_text(payload, "education_requirement"),
		min_experience_years=min_experience_years,
		criteria_text=_required_text(payload, "criteria_text"),
		professional_qa_enabled=professional_qa_enabled,
		greeting_message=_required_text(payload, "greeting_message"),
		status=status,
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_job_status(request: web.Request) -> web.Response:
	"""提交岗位发布/归档确认，发布前由领域层再次校验完整性。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	job_id = request.match_info.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识无效", status=400)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	status = _required_text(payload, "status")
	if status not in {"published", "archived"}:
		return _json_error("INVALID_PARAM", "岗位状态只能是 published 或 archived", status=400)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.start_recruiting_job_status(job_id=job_id, status=status)}, status=202)


async def _recruiting_job_update(request: web.Request) -> web.Response:
	"""保存岗位修改并回到草稿状态，避免用户重复创建岗位。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	job_id = request.match_info.get("job_id", "").strip()
	if not job_id or len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识无效", status=400)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	name = _required_text(payload, "name")
	if not name:
		return _json_error("INVALID_PARAM", "岗位名称不能为空", status=400)
	try:
		min_experience_years = _optional_nonnegative_int(payload, "min_experience_years")
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	try:
		professional_qa_enabled = _optional_bool(payload, "professional_qa_enabled", default=None)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_job_update(
		job_id=job_id,
		name=name,
		city=_required_text(payload, "city"),
		salary_range=_required_text(payload, "salary_range"),
		education_requirement=_required_text(payload, "education_requirement"),
		min_experience_years=min_experience_years,
		criteria_text=_required_text(payload, "criteria_text"),
		professional_qa_enabled=professional_qa_enabled,
		greeting_message=_required_text(payload, "greeting_message"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_knowledge(request: web.Request) -> web.Response:
	"""校验知识库表单并启动保存任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	values = {key: _required_text(payload, key) for key in ("job_id", "category", "title", "content")}
	values["audience"] = _required_text(payload, "audience")
	if not all(values[key] for key in ("job_id", "category", "title", "content")):
		return _json_error("INVALID_PARAM", "知识库需要岗位、类别、标题和正文", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_knowledge(**values)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_knowledge_import(request: web.Request) -> web.Response:
	"""校验本地知识文件路径并启动导入任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	values = {key: _required_text(payload, key) for key in ("job_id", "category", "source_path")}
	values["audience"] = _required_text(payload, "audience")
	if not all(values[key] for key in ("job_id", "category", "source_path")):
		return _json_error("INVALID_PARAM", "知识文件导入需要岗位、类别和本地文件路径", status=400)
	if len(values["source_path"]) > 4_096:
		return _json_error("INVALID_PARAM", "知识文件路径过长", status=400)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.start_recruiting_knowledge_import(**values)}, status=202)


async def _recruiting_faq(request: web.Request) -> web.Response:
	"""校验 FAQ 表单并启动保存任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	values = {key: _required_text(payload, key) for key in ("job_id", "question", "answer")}
	values["allowed_variation"] = _required_text(payload, "allowed_variation")
	values["audience"] = _required_text(payload, "audience") or "candidate"
	values["source_document_id"] = _required_text(payload, "source_document_id")
	values["source_title"] = _required_text(payload, "source_title")
	values["source_version"] = _required_text(payload, "source_version")
	if not all(values[key] for key in ("job_id", "question", "answer")):
		return _json_error("INVALID_PARAM", "FAQ 需要岗位、问题和答案", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_faq(**values)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_candidate_import(request: web.Request) -> web.Response:
	"""校验本地简历路径并启动候选人引用导入。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	resume_path = _required_text(payload, "resume_path")
	if not resume_path:
		return _json_error("INVALID_PARAM", "简历路径不能为空", status=400)
	source_value = payload.get("source")
	source = source_value if isinstance(source_value, str) else "local_markdown"
	job_id_value = payload.get("job_id")
	job_id = job_id_value.strip() if isinstance(job_id_value, str) else ""
	if len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识过长", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_candidate_import(resume_path=resume_path, source=source, job_id=job_id or None)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_job_interpret(request: web.Request) -> web.Response:
	"""直接把自然语言岗位需求写入工作区，不创建待确认草案。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	requirements = _required_text(payload, "requirements")
	if not requirements:
		return _json_error("INVALID_PARAM", "请先输入岗位需求", status=400)
	job_id = _required_text(payload, "job_id")
	if len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识过长", status=400)
	raw_conditions = payload.get("hard_conditions")
	if raw_conditions is not None and not isinstance(raw_conditions, dict):
		return _json_error("INVALID_PARAM", "硬条件必须是对象", status=400)
	allowed_conditions = {
		key: value
		for key, value in (raw_conditions or {}).items()
		if key in {"name", "city", "salary_range", "education_requirement", "min_experience_years", "industry"}
	}
	try:
		if "min_experience_years" in allowed_conditions:
			allowed_conditions["min_experience_years"] = _optional_nonnegative_int(
				{"min_experience_years": allowed_conditions["min_experience_years"]}, "min_experience_years",
			)
	except ValueError as exc:
		return _json_error("INVALID_PARAM", str(exc), status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_job_interpretation(
		requirements=requirements,
		job_id=job_id,
		hard_conditions=allowed_conditions,
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_job_rule_analysis(request: web.Request) -> web.Response:
	"""解析岗位补充描述为四类规则，供弹窗审核，过程中不写入任何岗位。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	requirements = _required_text(payload, "requirements")
	if not job_id or not requirements:
		return _json_error("INVALID_PARAM", "规则分析需要岗位和补充要求", status=400)
	if len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识过长", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_job_rule_analysis(job_id=job_id, requirements=requirements)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_job_rule_apply(request: web.Request) -> web.Response:
	"""保存 HR 审核后的四类规则，拒绝任意岗位基础字段进入该接口。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	rules = payload.get("rules")
	scoring = payload.get("scoring")
	if not job_id or not isinstance(rules, dict):
		return _json_error("INVALID_PARAM", "保存规则需要岗位和四类规则对象", status=400)
	if len(job_id) > 128:
		return _json_error("INVALID_PARAM", "岗位标识过长", status=400)
	allowed_keys = {"must_have", "nice_to_have", "reject_if", "risk_signals"}
	if set(rules) != allowed_keys or any(not isinstance(value, list) for value in rules.values()):
		return _json_error("INVALID_PARAM", "规则仅支持四个列表：必须条件、加分条件、淘汰条件和风险信号", status=400)
	if scoring is not None and not isinstance(scoring, dict):
		return _json_error("INVALID_PARAM", "评分配置必须是对象", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_job_rule_apply(job_id=job_id, rules=rules, scoring=scoring or {})
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_auto_assignment(request: web.Request) -> web.Response:
	"""启动一次本地目录简历的自动评分与岗位分配任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	directory = _required_text(payload, "directory") or str(Path.home() / "Desktop" / "简历")
	if len(directory) > 4_096:
		return _json_error("INVALID_PARAM", "本地简历目录路径过长", status=400)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response(
		{"ok": True, "data": runtime.start_recruiting_auto_assignment(directory=directory)},
		status=202,
	)


async def _recruiting_mismatch_feedback(request: web.Request) -> web.Response:
	"""保存筛选不匹配原因，仅更新本地复盘数据。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	reason_code = _required_text(payload, "reason_code")
	stage = _required_text(payload, "stage")
	if not all((job_id, candidate_id, reason_code, stage)):
		return _json_error("INVALID_PARAM", "反馈需要岗位、候选人、原因和筛选阶段", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_mismatch_feedback(
		job_id=job_id,
		candidate_id=candidate_id,
		reason_code=reason_code,
		stage=stage,
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_assess(request: web.Request) -> web.Response:
	"""校验岗位和候选人标识并启动评估任务。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	if not job_id or not candidate_id:
		return _json_error("INVALID_PARAM", "评估需要岗位标识和候选人标识", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_assessment(job_id=job_id, candidate_id=candidate_id)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_candidate_stage(request: web.Request) -> web.Response:
	"""校验候选人阶段记录，并启动本地审计更新。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	candidate_id = request.match_info.get("candidate_id", "").strip()
	if not candidate_id or len(candidate_id) > 128:
		return _json_error("INVALID_PARAM", "候选人标识无效", status=400)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	stage = _required_text(payload, "stage")
	action = _required_text(payload, "action")
	if not stage or not action:
		return _json_error("INVALID_PARAM", "阶段记录需要阶段和动作", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_candidate_transition(
		candidate_id=candidate_id,
		job_id=_required_text(payload, "job_id"),
		stage=stage,
		action=action,
		note=_required_text(payload, "note"),
		ai_judgment=_required_text(payload, "ai_judgment"),
		candidate_quote=_required_text(payload, "candidate_quote"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_answer(request: web.Request) -> web.Response:
	"""校验专业问答并启动本地回答记录。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	question = _required_text(payload, "question")
	answer = _required_text(payload, "answer")
	if not job_id or not candidate_id or not question or not answer:
		return _json_error("INVALID_PARAM", "记录回答需要岗位、候选人、问题和回答", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_answer(
		job_id=job_id,
		candidate_id=candidate_id,
		question=question,
		answer=answer,
		question_id=_required_text(payload, "question_id"),
		question_version=_required_text(payload, "question_version") or "v1",
		source_ids=[str(item).strip() for item in payload.get("source_ids", []) if str(item).strip()]
		if isinstance(payload.get("source_ids"), list)
		else [],
		follow_up_of=_required_text(payload, "follow_up_of"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_private_professional_qa(request: web.Request) -> web.Response:
	"""校验私域专业核验表单并启动本地结构化记录。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	question = _required_text(payload, "question")
	answer = _required_text(payload, "answer")
	outcome = _required_text(payload, "outcome") or "passed"
	if not job_id or not candidate_id or not question or not answer:
		return _json_error("INVALID_PARAM", "私域专业核验需要岗位、候选人、问题和回答", status=400)
	source_value = payload.get("source_ids")
	source_ids = [str(item).strip() for item in source_value if str(item).strip()] if isinstance(source_value, list) else []
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_private_professional_qa(
		job_id=job_id,
		candidate_id=candidate_id,
		question=question,
		answer=answer,
		question_id=_required_text(payload, "question_id"),
		question_version=_required_text(payload, "question_version") or "v1",
		source_ids=source_ids,
		outcome=outcome,
		note=_required_text(payload, "note"),
		follow_up_of=_required_text(payload, "follow_up_of"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_communication(request: web.Request) -> web.Response:
	"""校验一轮人工沟通记录，并启动本地待跟进更新。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	outcome = _required_text(payload, "outcome")
	round_number = _required_positive_int(payload, "round_number")
	if round_number is None:
		return _json_error("INVALID_PARAM", "沟通轮次必须是 1 到 4 的整数", status=400)
	if not job_id or not candidate_id or not outcome:
		return _json_error("INVALID_PARAM", "沟通记录需要岗位、候选人、轮次和结果", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_communication(
		job_id=job_id,
		candidate_id=candidate_id,
		round_number=round_number,
		outcome=outcome,
		candidate_reply_summary=_required_text(payload, "candidate_reply_summary"),
		note=_required_text(payload, "note"),
		next_follow_up_at=_required_text(payload, "next_follow_up_at"),
		template_key=_required_text(payload, "template_key"),
		template_version=_required_text(payload, "template_version"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_message_usage(request: web.Request) -> web.Response:
	"""记录 HR 已人工使用某版话术；该接口永远不发送 BOSS 消息。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	template_key = _required_text(payload, "template_key")
	if not job_id or not template_key:
		return _json_error("INVALID_PARAM", "话术使用记录需要岗位和话术标识", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_message_usage(
		job_id=job_id,
		candidate_id=_required_text(payload, "candidate_id"),
		template_key=template_key,
		template_version=_required_text(payload, "template_version") or "v1",
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_review(request: web.Request) -> web.Response:
	"""校验人工确认结果并启动本地评估状态更新。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	outcome = _required_text(payload, "outcome")
	note = _required_text(payload, "note")
	manual_override = payload.get("manual_override", False)
	override_reason = _required_text(payload, "override_reason")
	if not job_id or not candidate_id or not outcome:
		return _json_error("INVALID_PARAM", "人工确认需要岗位、候选人和确认结果", status=400)
	if not isinstance(manual_override, bool):
		return _json_error("INVALID_PARAM", "人工强制继续标识必须是布尔值", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_review(
		job_id=job_id,
		candidate_id=candidate_id,
		outcome=outcome,
		note=note,
		manual_override=manual_override,
		override_reason=override_reason,
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_task_update(request: web.Request) -> web.Response:
	"""校验待办状态并提交本地完成/跳过操作。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	task_id = request.match_info.get("task_id", "").strip()
	if not task_id or len(task_id) > 128:
		return _json_error("INVALID_PARAM", "待办标识无效", status=400)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	status = _required_text(payload, "status")
	if status not in {"completed", "skipped", "pending"}:
		return _json_error("INVALID_PARAM", "待办状态只能是 completed、skipped 或 pending", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_task_update(
		task_id=task_id,
		status=status,
		note=_required_text(payload, "note"),
		target_stage=_required_text(payload, "target_stage") or None,
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_basic_intent(request: web.Request) -> web.Response:
	"""校验并保存基础意向确认，作为最终评估门禁的人工事实。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	note = _required_text(payload, "note")
	if not job_id or not candidate_id or not note:
		return _json_error("INVALID_PARAM", "基础意向确认需要岗位、候选人和备注", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_basic_intent(job_id=job_id, candidate_id=candidate_id, note=note)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_private_contact(request: web.Request) -> web.Response:
	"""校验并保存人工确认的私域联系结果，不触发平台动作。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	candidate_id = _required_text(payload, "candidate_id")
	channel = _required_text(payload, "channel")
	status = _required_text(payload, "status")
	if not candidate_id or not channel or not status:
		return _json_error("INVALID_PARAM", "私域记录需要候选人、渠道和状态", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_private_contact(
		job_id=_required_text(payload, "job_id"),
		candidate_id=candidate_id,
		channel=channel,
		status=status,
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_interview(request: web.Request) -> web.Response:
	"""校验并保存人工确认的面试邀约元数据。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	scheduled_at = _required_text(payload, "scheduled_at")
	if not job_id or not candidate_id or not scheduled_at:
		return _json_error("INVALID_PARAM", "面试邀约需要岗位、候选人和时间", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_interview(
		job_id=job_id,
		candidate_id=candidate_id,
		scheduled_at=scheduled_at,
		interviewer=_required_text(payload, "interviewer"),
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_interview_result(request: web.Request) -> web.Response:
	"""校验并保存面试结果，随后由本地待办承接终局决定。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	job_id = _required_text(payload, "job_id")
	candidate_id = _required_text(payload, "candidate_id")
	outcome = _required_text(payload, "outcome")
	if not job_id or not candidate_id or not outcome:
		return _json_error("INVALID_PARAM", "面试结果需要岗位、候选人和结果", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_recruiting_interview_result(
		job_id=job_id,
		candidate_id=candidate_id,
		outcome=outcome,
		note=_required_text(payload, "note"),
	)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _login(request: web.Request) -> web.Response:
	"""启动一次后台官方页面打开任务，不阻塞等待用户完成登录。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.start_open_login_page()}, status=202)


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
	workspace_job_id = _required_text(payload, "workspace_job_id") or None
	if output is not None and output_dir is not None:
		return _json_error("INVALID_PARAM", "output 与 output_dir 互斥，只能指定一个", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_download(
		geek_id=geek_id,
		job_id=job_id,
		security_id=security_id,
		workspace_job_id=workspace_job_id,
		output=output,
		output_dir=output_dir,
	)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _conversation_resume_download(request: web.Request) -> web.Response:
	"""按一个沟通会话启动在线和附件简历导出，不接受内部定位标识。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	friend_id = _required_positive_int(payload, "friend_id")
	if friend_id is None:
		return _json_error("INVALID_PARAM", "下载沟通会话简历需要正整数 friend_id", status=400)
	output = _optional_path(payload, "output")
	output_dir = _optional_path(payload, "output_dir")
	workspace_job_id = _required_text(payload, "workspace_job_id") or None
	if output is not None and output_dir is not None:
		return _json_error("INVALID_PARAM", "output 与 output_dir 互斥，只能指定一个", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_conversation_download(friend_id=friend_id, job_id=workspace_job_id, output=output, output_dir=output_dir)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _current_conversation_resume_download(request: web.Request) -> web.Response:
	"""从官方沟通页当前选择启动资料导出，不接收内部会话标识。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	output_dir = _optional_path(payload, "output_dir")
	output = _optional_path(payload, "output")
	workspace_job_id = _required_text(payload, "workspace_job_id") or None
	if output is not None and output_dir is not None:
		return _json_error("INVALID_PARAM", "output 与 output_dir 互斥，只能指定一个", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_current_chat_download(job_id=workspace_job_id, output=output, output_dir=output_dir)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _latest_conversation_resume_download(request: web.Request) -> web.Response:
	"""导出平台当前排序第一条沟通会话，不接收任何内部标识。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	workspace_job_id = await _optional_workspace_job_id(request)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_latest_conversation_download(job_id=workspace_job_id, output=None, output_dir=None)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _selected_conversation_resume_download(request: web.Request) -> web.Response:
	"""依据页面列表返回的不透明选择标识下载一位候选人的资料。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	workspace_job_id = await _optional_workspace_job_id(request)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_selected_conversation_download(
		selection_id=request.match_info["selection_id"], job_id=workspace_job_id, output=None, output_dir=None,
	)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result["state"] == "failed":
		return web.json_response({"ok": False, "error": result["error"]}, status=409)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _selected_conversation_detail(request: web.Request) -> web.Response:
	"""按列表不透明选择标识读取单条 BOSS 卡片上下文。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_selected_conversation_detail(selection_id=request.match_info["selection_id"])
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result["state"] == "failed":
		return web.json_response({"ok": False, "error": result["error"]}, status=409)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _selected_recommendation_resume_download(request: web.Request) -> web.Response:
	"""按推荐列表的不透明选择标识导出候选人资料。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	workspace_job_id = await _optional_workspace_job_id(request)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_selected_recommendation_download(
		selection_id=request.match_info["selection_id"], job_id=workspace_job_id, output=None, output_dir=None,
	)
	if result["state"] == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


def _required_text(payload: dict[str, Any], key: str) -> str:
	"""读取必填文本；非字符串和纯空白统一按缺失处理。"""
	value = payload.get(key)
	return value.strip() if isinstance(value, str) else ""


async def _boss_jobs(request: web.Request) -> web.Response:
	"""读取 BOSS 平台在线职位列表，返回 encryptJobId 和职位名称。"""
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.fetch_boss_jobs()
	return web.json_response({"ok": True, "data": result})


async def _recruiting_templates(request: web.Request) -> web.Response:
	"""返回当前工作区的话术模板列表。"""
	runtime = request.app[_RUNTIME_KEY]
	job_id = request.query.get("job_id", "")
	result = runtime.list_templates(job_id=job_id if job_id else None)
	return web.json_response({"ok": True, "data": result})


async def _recruiting_template_save(request: web.Request) -> web.Response:
	"""创建或更新话术模板。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	from boss_agent_cli.recruiting.models import CommunicationTemplate
	template = CommunicationTemplate(
		template_id=_required_text(payload, "template_id"),
		job_id=_required_text(payload, "job_id"),
		template_key=_required_text(payload, "template_key"),
		title=_required_text(payload, "title"),
		body=_required_text(payload, "body"),
		category=_required_text(payload, "category") or "greeting",
		version=_required_text(payload, "template_version") or "v1",
	)
	if not template.template_key or not template.body:
		return _json_error("INVALID_PARAM", "话术模板必须有标识和正文", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.save_template(template)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _recruiting_template_delete(request: web.Request) -> web.Response:
	"""删除指定话术模板。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	template_id = request.match_info.get("template_id", "").strip()
	if not template_id:
		return _json_error("INVALID_PARAM", "模板标识无效", status=400)
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.delete_template(template_id)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _pipeline_analyze_one(request: web.Request) -> web.Response:
	"""分析单一位候选人（按 selection_id 查 friend_id 后执行）。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	selection_id = _required_text(payload, "selection_id")
	if not selection_id:
		return _json_error("INVALID_PARAM", "需要 selection_id", status=400)
	runtime = request.app[_RUNTIME_KEY]
	with runtime._state_lock:
		friend_id = runtime._conversation_selections.get(selection_id)
	if friend_id is None:
		return _json_error("INVALID_PARAM", "候选人列表已更新，请刷新后重试", status=400)
	name = ""
	for item in runtime._conversation_list.get("items", []):
		if item.get("selection_id") == selection_id:
			name = str(item.get("candidate_name") or "")
			break
	result = runtime.start_single_analysis(friend_id=friend_id, candidate_name=name)
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result.get("state") == "failed":
		return web.json_response({"ok": False, "error": result["error"]}, status=409)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _pipeline_analyze_all(request: web.Request) -> web.Response:
	"""全局批量分析：只分析沟通列表中未分析过的候选人。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	limit = _required_positive_int(payload, "limit")
	if limit is None or limit > 50:
		limit = 20
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_batch_analysis(limit=limit)
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result.get("state") == "failed":
		return web.json_response({"ok": False, "error": result["error"]}, status=409)
	return web.json_response({"ok": True, "data": result}, status=202)



async def _pipeline_start(request: web.Request) -> web.Response:
	"""校验并启动流水线。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	limit = _required_positive_int(payload, "limit")
	if limit is None or limit > 50:
		return _json_error("INVALID_PARAM", "处理数量必须是 1 到 50 之间的整数", status=400)
	threshold = _required_positive_int(payload, "threshold")
	if threshold is None or not 1 <= threshold <= 100:
		threshold = 70
	ask_for_resume = payload.get("ask_for_resume", True)
	if not isinstance(ask_for_resume, bool):
		ask_for_resume = True
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_pipeline(limit=limit, threshold=threshold, ask_for_resume=ask_for_resume)
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _pipeline_stop(request: web.Request) -> web.Response:
	"""请求停止流水线。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.stop_pipeline()}, status=202)


async def _batch_export(request: web.Request) -> web.Response:
	"""启动一批候选人的简历导出或附件扫描。

	来源、数量和模式都在这里收敛成受控取值，运行时再做一次防御性校验；批量
	只读取已有资料，不会向候选人发送任何消息或附件请求。
	"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	try:
		payload = await request.json() if request.can_read_body else {}
	except (ValueError, TypeError):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	if not isinstance(payload, dict):
		return _json_error("INVALID_PARAM", "请求体必须是 JSON 对象", status=400)
	source = _required_text(payload, "source") or SOURCE_CONVERSATION
	if source not in AVAILABLE_SOURCES:
		return _json_error("INVALID_PARAM", "批量导出来源只能是 conversation 或 recommendation", status=400)
	mode = _required_text(payload, "mode") or MODE_EXPORT
	if mode not in AVAILABLE_MODES:
		return _json_error("INVALID_PARAM", "批量模式只能是 export 或 scan", status=400)
	limit = _required_positive_int(payload, "limit")
	if limit is None or limit > MAX_LIMIT:
		return _json_error("INVALID_PARAM", f"批量数量必须是 1 到 {MAX_LIMIT} 之间的整数", status=400)
	output_dir = _optional_path(payload, "output_dir")
	job_id = _required_text(payload, "job_id") or None
	runtime = request.app[_RUNTIME_KEY]
	result = runtime.start_batch_export(
		source=source, limit=limit, mode=mode, job_id=job_id, output_dir=output_dir,
	)
	if result.get("state") == "blocked":
		return web.json_response({"ok": False, "error": result["error"]}, status=403)
	if result.get("state") == "failed" and "error" in result:
		return web.json_response({"ok": False, "error": result["error"]}, status=400)
	return web.json_response({"ok": True, "data": result}, status=202)


async def _batch_export_stop(request: web.Request) -> web.Response:
	"""请求在下一个候选人边界停止当前批次。"""
	if not _is_trusted_write_request(request):
		return _json_error("FORBIDDEN", "请求来源校验失败", status=403)
	runtime = request.app[_RUNTIME_KEY]
	return web.json_response({"ok": True, "data": runtime.stop_batch_export()}, status=202)


async def _optional_workspace_job_id(request: web.Request) -> str | None:
	"""从按钮请求读取本地岗位 ID；平台内部 job_id 不会被误当成工作台岗位。"""
	if not request.can_read_body:
		return None
	try:
		payload = await request.json()
	except (ValueError, TypeError):
		return None
	return _required_text(payload, "workspace_job_id") or _required_text(payload, "job_id") or None if isinstance(payload, dict) else None


def _optional_path(payload: dict[str, Any], key: str) -> Path | None:
	"""读取可选路径；空值代表采用既有默认导出位置。"""
	value = payload.get(key)
	return Path(value).expanduser() if isinstance(value, str) and value.strip() else None


def _required_positive_int(payload: dict[str, Any], key: str) -> int | None:
	"""读取正整数会话 ID，拒绝布尔值、浮点数与其他宽松转换。"""
	value = payload.get(key)
	if isinstance(value, bool) or not isinstance(value, (str, int)):
		return None
	try:
		parsed = int(value)
	except ValueError:
		return None
	return parsed if parsed > 0 and str(parsed) == str(value).strip() else None


def _optional_nonnegative_int(payload: dict[str, Any], key: str) -> int | None:
	"""读取可选的非负整数；空值表示让自然语言标准解析器决定。"""
	value = payload.get(key)
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, bool) or not isinstance(value, (str, int)):
		raise ValueError("最低工作年限必须是 0 到 100 之间的整数")
	try:
		parsed = int(value)
	except (TypeError, ValueError) as exc:
		raise ValueError("最低工作年限必须是 0 到 100 之间的整数") from exc
	if parsed < 0 or parsed > 100 or str(parsed) != str(value).strip():
		raise ValueError("最低工作年限必须是 0 到 100 之间的整数")
	return parsed


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool | None) -> bool | None:
	"""读取可选布尔配置，拒绝字符串化真假导致的静默误配。"""
	value = payload.get(key, default)
	if value is None:
		return None
	if not isinstance(value, bool):
		raise ValueError("专业问答开关必须是布尔值")
	return value
