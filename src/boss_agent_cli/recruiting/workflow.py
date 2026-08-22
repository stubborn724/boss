"""招聘工作台的闭环进度投影。

本模块只负责把岗位、候选人、评估和待办等领域数据整理成一个稳定的
``workflow`` 快照，供 CLI/Web 展示下一步。它不修改 Store，也不触发任何
BOSS、私域或面试动作，因此前端可以安全地反复刷新这份投影。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_TERMINAL_STAGES = {"hired", "rejected", "paused"}
_STEP_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
	("job_setup", "岗位标准", "创建岗位并确认筛选标准"),
	("candidate_import", "候选人导入", "导入平台导出的在线简历"),
	("assessment", "简历评估", "选择岗位并生成本地评估"),
	("human_review", "人工确认", "查看证据并确认下一步"),
	("follow_up", "后续跟进", "在官方页面完成动作后回填结果"),
)

_TASK_ACTIONS: dict[str, str] = {
	"publish_job": "补齐岗位标准并人工发布",
	"assess_candidate": "选择岗位并生成简历评估",
	"reassess_candidate": "补充回答后重新生成评估",
	"review_assessment": "查看证据并完成人工确认",
	"confirm_basic": "确认基础条件",
	"complete_basic": "完成基础条件确认",
	"start_professional_qa": "发起专业问答",
	"private_professional_qa": "记录私域专业核验",
	"prepare_resume_exchange": "准备交换简历",
	"review_resume": "完成简历评估",
	"continue_conversation": "完成第 1 轮 BOSS 沟通并回填",
	"communication_round": "完成下一轮 BOSS 沟通并回填",
	"communication_follow_up": "处理到期沟通跟进并回填",
	"record_private_contact": "记录私域联系结果",
	"prepare_interview": "确认面试安排并进入邀约",
	"schedule_interview": "记录正式面试时间",
	"record_interview": "记录面试结果",
	"record_hiring_decision": "记录录用、淘汰或暂缓决定",
	"recover_task": "恢复已跳过的待办",
	"record_stage": "记录候选人阶段",
}

# 同一岗位可能同时存在多个候选人的待办。优先处理会阻塞后续阶段的本地
# 动作，保证工作台顶部“下一步”和待办列表中真正可执行的入口保持一致。
_TASK_PRIORITIES: dict[str, int] = {
	"publish_job": 0,
	"assess_candidate": 10,
	"confirm_basic": 20,
	"complete_basic": 20,
	"start_professional_qa": 30,
	"private_professional_qa": 35,
	"reassess_candidate": 40,
	"review_assessment": 50,
	"prepare_resume_exchange": 60,
	"review_resume": 70,
	"continue_conversation": 80,
	"communication_follow_up": 80,
	"communication_round": 85,
	"record_private_contact": 90,
	"prepare_interview": 100,
	"schedule_interview": 110,
	"record_interview": 120,
	"record_hiring_decision": 130,
}

# 队列优先级只用于提醒 HR 先看哪一张卡，不会替代评估门禁，也不会自动
# 推进候选人阶段。把“执行状态”和“评估关注点”拆开，才能避免高风险候选人
# 被误当成淘汰，同时让同一待办类型下的候选人有可解释的处理顺序。
_QUEUE_EXECUTION_PRIORITIES = {
	"pending": 0,
	"skipped": 1,
	"active": 2,
	"terminal": 3,
}

# 分数段属于展示与人工复核的共同语言，保持固定顺序和边界，不能让页面根据
# 当前数据动态产生组名。评分仍由 assessment 模块负责，本投影不会重新计算分数。
_SCORE_GROUPS: tuple[tuple[str, str, str, int | None, int | None], ...] = (
	("strong_recommend", "强烈推荐", "90-100 分", 90, 100),
	("recommend", "推荐", "80-89 分", 80, 89),
	("pending_confirmation", "待确认", "70-79 分", 70, 79),
	("manual_review", "人工复核", "60-69 分", 60, 69),
	("not_recommend", "不推荐", "60 分以下", 0, 59),
	("unassessed", "未评估", "尚未生成评估", None, None),
)


def build_score_groups(
	*,
	candidates: Iterable[Mapping[str, Any]],
	assessments: Iterable[Mapping[str, Any]],
	selected_job_id: str | None,
) -> list[dict[str, Any]]:
	"""构造指定岗位候选人的六档分数分组。

	报告的唯一键是 ``(job_id, candidate_id)``；因此同一候选人即使同时投递两个
	岗位，也只会使用当前 ``selected_job_id`` 的那份报告。输出只保留候选人名称、
	流程阶段和评分元数据，不复制简历路径或正文。
	"""
	group_rows: dict[str, list[dict[str, Any]]] = {key: [] for key, *_ in _SCORE_GROUPS}
	if not selected_job_id:
		return [
			{"key": key, "label": label, "range": score_range, "count": 0, "candidate_ids": [], "candidates": []}
			for key, label, score_range, _minimum, _maximum in _SCORE_GROUPS
		]
	assessment_by_candidate: dict[str, Mapping[str, Any]] = {}
	for report in assessments:
		if str(report.get("job_id") or "") == selected_job_id:
			candidate_id = str(report.get("candidate_id") or "")
			if candidate_id:
				assessment_by_candidate[candidate_id] = report
	for candidate in candidates:
		candidate_id = str(candidate.get("candidate_id") or "")
		if not candidate_id:
			continue
		report = assessment_by_candidate.get(candidate_id)
		raw_score = report.get("final_score") if report is not None else None
		score = round(raw_score) if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool) else None
		group_key = "unassessed"
		if score is not None:
			if score >= 90:
				group_key = "strong_recommend"
			elif score >= 80:
				group_key = "recommend"
			elif score >= 70:
				group_key = "pending_confirmation"
			elif score >= 60:
				group_key = "manual_review"
			else:
				group_key = "not_recommend"
		group_rows[group_key].append(
			{
				"candidate_id": candidate_id,
				"name": str(candidate.get("name") or "未命名候选人"),
				"stage": str(candidate.get("stage") or "pending_screening"),
				"stage_label": str(candidate.get("stage_label") or "待筛选"),
				"final_score": score,
				"review_required": bool(report.get("review_required", True)) if report is not None else False,
			}
		)
	for rows in group_rows.values():
		rows.sort(key=lambda row: (-1 if row["final_score"] is None else -int(row["final_score"]), str(row["name"])))
	return [
		{
			"key": key,
			"label": label,
			"range": score_range,
			"count": len(group_rows[key]),
			"candidate_ids": [str(row["candidate_id"]) for row in group_rows[key]],
			"candidates": group_rows[key],
		}
		for key, label, score_range, _minimum, _maximum in _SCORE_GROUPS
	]


def _assessment_signals(
	candidate_id: str,
	assessment_by_candidate: Mapping[str, Mapping[str, Any]],
) -> tuple[int | None, str, str, str]:
	"""从评估快照提取队列可展示的最小信号，不泄露简历正文或联系方式。"""
	report = assessment_by_candidate.get(candidate_id, {})
	raw_score = report.get("final_score")
	final_score: int | None = None
	if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
		final_score = max(0, min(100, round(raw_score)))
	raw_screening = report.get("screening")
	screening = raw_screening if isinstance(raw_screening, Mapping) else {}
	raw_risk = screening.get("risk")
	risk = raw_risk if isinstance(raw_risk, Mapping) else {}
	risk_level = str(risk.get("level") or "unknown")
	raw_hard_filter = screening.get("hard_filter")
	hard_filter = raw_hard_filter if isinstance(raw_hard_filter, Mapping) else {}
	hard_status = str(hard_filter.get("status") or "unknown")
	raw_qa = screening.get("professional_qa")
	professional_qa = raw_qa if isinstance(raw_qa, Mapping) else {}
	qa_status = str(professional_qa.get("status") or "unknown")
	return final_score, risk_level, hard_status, qa_status


def _queue_attention(
	*,
	final_score: int | None,
	risk_level: str,
	hard_status: str,
	qa_status: str,
	is_terminal: bool,
	has_pending_task: bool,
	has_skipped_task: bool,
) -> tuple[int, str, list[str]]:
	"""计算解释性关注级别；该结果只影响排序和文案，不产生招聘决定。"""
	if is_terminal:
		return 9, "已完成", ["候选人已进入终局"]
	if has_skipped_task:
		return 0, "需恢复", ["存在已跳过待办"]
	reasons: list[str] = []
	if risk_level == "high":
		reasons.append("风险等级高，需人工核对")
	if hard_status in {"fail", "review"}:
		reasons.append("硬条件结果需要人工确认")
	if reasons:
		return 0, "需人工复核", reasons
	if qa_status in {"follow_up", "not_started"}:
		reasons.append("专业问答证据尚未完整")
	if final_score is not None and final_score < 70:
		reasons.append("综合评分偏低，先核对证据")
	if reasons:
		return 1, "需补充证据", reasons
	if final_score is not None and final_score >= 80 and risk_level in {"low", "unknown"}:
		return 1, "高匹配", ["综合评分较高，建议优先处理"]
	if has_pending_task:
		return 2, "常规处理", ["已有可执行待办"]
	return 3, "待建立动作", ["尚未形成可执行待办"]


def _as_list(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
	"""把一次性迭代器物化，避免投影多个字段时重复消耗输入。"""
	return [row for row in rows if isinstance(row, Mapping)]


def build_workflow_projection(
	*,
	jobs: Iterable[Mapping[str, Any]],
	candidates: Iterable[Mapping[str, Any]],
	tasks: Iterable[Mapping[str, Any]],
	assessments: Iterable[Mapping[str, Any]],
	selected_job_id: str | None,
) -> dict[str, Any]:
	"""生成不含简历正文的闭环状态。

	下一步优先依据待办而不是页面顺序计算：这样刷新页面、切换岗位或从
	导出结果跳转进来时，用户仍会落在唯一可执行的动作上。评估待办没有
	绑定岗位时使用当前选择的岗位作为 UI 默认值，但不会在这里自动创建
	或修改岗位。
	"""
	job_rows = _as_list(jobs)
	candidate_rows = _as_list(candidates)
	task_rows = _as_list(tasks)
	assessment_rows = _as_list(assessments)
	assessment_by_candidate = {
		str(row.get("candidate_id") or ""): row
		for row in assessment_rows
		if str(row.get("candidate_id") or "")
	}
	candidate_by_id = {
		str(row.get("candidate_id") or ""): row
		for row in candidate_rows
		if str(row.get("candidate_id") or "")
	}
	visible_candidate_ids = set(candidate_by_id)
	visible_task_rows = [
		row
		for row in task_rows
		if str(row.get("candidate_id") or "") in visible_candidate_ids
		and (
			not selected_job_id
			or str(row.get("job_id") or "") in {"", str(selected_job_id)}
		)
	]
	visible_task_rows.sort(
		key=lambda row: (
			0 if row.get("status") == "pending" else 1,
			_TASK_PRIORITIES.get(str(row.get("kind") or ""), 999),
			0 if str(row.get("due_at") or "") else 1,
			str(row.get("due_at") or ""),
			str(row.get("updated_at") or row.get("created_at") or ""),
		)
	)
	pending_tasks = [row for row in visible_task_rows if row.get("status") == "pending"]
	pending_task = pending_tasks[0] if pending_tasks else None
	# 跳过不是完成：如果没有待办但仍有跳过记录，顶部必须引导用户恢复
	# 历史动作，否则“去处理”会落到一个没有可点击控件的空列表。
	skipped_tasks = [row for row in visible_task_rows if row.get("status") == "skipped"]
	skipped_tasks.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
	skipped_task = skipped_tasks[0] if skipped_tasks else None
	pending_candidate = (
		candidate_by_id.get(str(pending_task.get("candidate_id") or ""), {})
		if pending_task is not None
		else {}
	)
	selected_job = next((row for row in job_rows if row.get("job_id") == selected_job_id), None)
	reviewed_count = sum(1 for row in assessment_rows if row.get("review_required") is False)
	terminal_count = sum(1 for row in candidate_rows if row.get("stage") in _TERMINAL_STAGES)

	if not job_rows:
		next_step = "create_job"
		next_action = "先创建一个岗位标准"
	elif selected_job is not None and selected_job.get("status") == "draft":
		next_step = "publish_job"
		next_action = (
		"补齐岗位标准后人工发布"
		if not bool((selected_job.get("readiness") or {}).get("ready"))
		else "确认岗位标准并发布"
	)
	elif selected_job is not None and selected_job.get("status") == "archived":
		next_step = "create_job"
		next_action = "当前岗位已归档，请选择其他岗位或创建新岗位"
	elif not candidate_rows:
		next_step = "import_candidate"
		next_action = "导入一份本地简历或从平台导出"
	elif terminal_count == len(candidate_rows):
		next_step = "closed"
		next_action = "当前候选人已进入终局"
	elif pending_task is not None:
		next_step = str(pending_task.get("kind") or "follow_up")
		next_action = _TASK_ACTIONS.get(next_step, "处理招聘工作台待办")
	elif skipped_task is not None:
		next_step = "recover_task"
		next_action = _TASK_ACTIONS["recover_task"]
	else:
		next_step = "record_stage"
		next_action = _TASK_ACTIONS["record_stage"]

	if selected_job_id:
		# 类型上显式保留“岗位标识存在但记录缺失”的旧数据分支，避免
		# 投影阶段把 Optional Mapping 当成必定存在的岗位对象。
		completed_job = bool(selected_job and selected_job.get("status", "published") == "published")
	else:
		completed_job = bool(job_rows) and all(row.get("status", "published") == "published" for row in job_rows)
	completed_candidate = bool(candidate_rows)
	assessment_candidate_ids = {
		str(row.get("candidate_id") or "")
		for row in assessment_rows
		if str(row.get("candidate_id") or "") in visible_candidate_ids
	}
	reviewed_candidate_ids = {
		str(row.get("candidate_id") or "")
		for row in assessment_rows
		if row.get("review_required") is False and str(row.get("candidate_id") or "") in visible_candidate_ids
	}
	completed_assessment = bool(candidate_rows) and assessment_candidate_ids >= visible_candidate_ids
	completed_review = bool(candidate_rows) and reviewed_candidate_ids >= visible_candidate_ids
	# “后续跟进”只有所有候选人都进入终局才算完成。即使历史待办全部
	# 被标记为已完成，只要候选人仍在中间阶段，工作流就不能显示闭环。
	completed_follow_up = bool(candidate_rows) and terminal_count == len(candidate_rows) and not pending_tasks
	completed_by_key = {
		"job_setup": completed_job,
		"candidate_import": completed_candidate,
		"assessment": completed_assessment,
		"human_review": completed_review,
		"follow_up": completed_follow_up,
	}
	step_key_by_next = {
		"create_job": "job_setup",
		"publish_job": "job_setup",
		"import_candidate": "candidate_import",
		"assess_candidate": "assessment",
		"reassess_candidate": "assessment",
		"start_professional_qa": "assessment",
		"private_professional_qa": "assessment",
		"review_resume": "assessment",
		"review_assessment": "human_review",
		"confirm_basic": "follow_up",
		"complete_basic": "follow_up",
		"prepare_resume_exchange": "follow_up",
		"recover_task": "follow_up",
		"record_stage": "follow_up",
		"closed": "follow_up",
	}
	current_key = step_key_by_next.get(next_step, "follow_up")
	focus_task = pending_task or skipped_task
	focus_candidate = (
		candidate_by_id.get(str(focus_task.get("candidate_id") or ""), {})
		if focus_task is not None
		else (candidate_rows[0] if candidate_rows else {})
	)
	# 页面需要一个“先处理谁”的候选人队列，而不是把所有候选人按导入顺序
	# 平铺。队列只投影可执行动作和脱敏元数据：简历路径、哈希和候选人原话
	# 都不属于此处的展示契约。待办候选人优先，终局候选人沉底，避免 HR
	# 在多个岗位或多个候选人之间反复猜测下一步。
	queue: list[dict[str, Any]] = []
	tasks_by_candidate: dict[str, list[Mapping[str, Any]]] = {}
	for row in visible_task_rows:
		candidate_id = str(row.get("candidate_id") or "")
		if candidate_id:
			tasks_by_candidate.setdefault(candidate_id, []).append(row)
	for candidate in candidate_rows:
		candidate_id = str(candidate.get("candidate_id") or "")
		candidate_tasks = tasks_by_candidate.get(candidate_id, [])
		pending_for_candidate = [row for row in candidate_tasks if row.get("status") == "pending"]
		pending_for_candidate.sort(
			key=lambda row: (
				_TASK_PRIORITIES.get(str(row.get("kind") or ""), 999),
				0 if str(row.get("due_at") or "") else 1,
				str(row.get("due_at") or ""),
				str(row.get("updated_at") or row.get("created_at") or ""),
			)
		)
		skipped_for_candidate = [row for row in candidate_tasks if row.get("status") == "skipped"]
		skipped_for_candidate.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
		candidate_task = pending_for_candidate[0] if pending_for_candidate else None
		is_terminal = str(candidate.get("stage") or "") in _TERMINAL_STAGES
		final_score, risk_level, hard_status, qa_status = _assessment_signals(
			candidate_id,
			assessment_by_candidate,
		)
		if candidate_task is not None:
			action = str(candidate_task.get("title") or _TASK_ACTIONS.get(str(candidate_task.get("kind") or ""), "处理招聘工作台待办"))
			queue_priority = _TASK_PRIORITIES.get(str(candidate_task.get("kind") or ""), 999)
		elif skipped_for_candidate and not is_terminal:
			candidate_task = skipped_for_candidate[0]
			action = _TASK_ACTIONS["recover_task"]
			queue_priority = 700
		elif is_terminal:
			action = "流程已完成"
			queue_priority = 1_000
		else:
			action = str(candidate.get("next_action") or _TASK_ACTIONS["record_stage"])
			queue_priority = 800
		has_skipped_task = bool(skipped_for_candidate and not is_terminal)
		execution_state = (
			"pending"
			if candidate_task is not None and candidate_task.get("status") == "pending"
			else "skipped"
			if has_skipped_task
			else "terminal"
			if is_terminal
			else "active"
		)
		attention_rank, priority_label, priority_reasons = _queue_attention(
			final_score=final_score,
			risk_level=risk_level,
			hard_status=hard_status,
			qa_status=qa_status,
			is_terminal=is_terminal,
			has_pending_task=bool(candidate_task and candidate_task.get("status") == "pending"),
			has_skipped_task=has_skipped_task,
		)
		queue.append(
			{
				"candidate_id": candidate_id,
				"name": str(candidate.get("name") or "未命名候选人"),
				"stage": str(candidate.get("stage") or "pending_screening"),
				"stage_label": str(candidate.get("stage_label") or "待筛选"),
				"source": str(candidate.get("source") or "local_markdown"),
				"next_action": action,
				"pending_task_id": str(candidate_task.get("task_id") or "") if candidate_task and candidate_task.get("status") == "pending" else "",
				"pending_task_kind": str(candidate_task.get("kind") or "") if candidate_task and candidate_task.get("status") == "pending" else ("recover_task" if skipped_for_candidate and not is_terminal else ""),
				"pending_task_title": str(candidate_task.get("title") or "") if candidate_task and candidate_task.get("status") == "pending" else "",
				"pending_job_id": str(candidate_task.get("job_id") or selected_job_id or "") if candidate_task else str(selected_job_id or ""),
				"focus_task_id": str(candidate_task.get("task_id") or "") if candidate_task else "",
				"focus_task_kind": str(candidate_task.get("kind") or "") if candidate_task else "",
				"due_at": str(candidate_task.get("due_at") or "") if candidate_task else "",
				"is_terminal": is_terminal,
				"queue_priority": queue_priority,
				"execution_state": execution_state,
				"attention_rank": attention_rank,
				"priority_label": priority_label,
				"priority_reasons": priority_reasons,
				"assessment_score": final_score,
				"risk_level": risk_level,
			}
		)
	queue.sort(
		key=lambda row: (
			_QUEUE_EXECUTION_PRIORITIES.get(str(row.get("execution_state") or "active"), 2),
			int(str(row.get("attention_rank"))) if row.get("attention_rank") is not None else 9,
			int(str(row.get("queue_priority"))) if row.get("queue_priority") is not None else 999,
			0 if row.get("due_at") else 1,
			str(row.get("due_at") or ""),
			str(row.get("name") or ""),
		)
	)
	steps: list[dict[str, Any]] = []
	for key, label, description in _STEP_DEFINITIONS:
		# 当前待办优先显示为 current，即使其他候选人已经完成过同一阶段；
		# 否则聚合计数会把多候选人的未完成动作伪装成已完成。
		if key == current_key and next_step != "closed":
			status = "current"
		elif completed_by_key[key]:
			status = "complete"
		else:
			status = "pending"
		steps.append({"key": key, "label": label, "description": description, "status": status})

	return {
		"next_step": next_step,
		"next_action": next_action,
		"pending_task_kind": str(pending_task.get("kind") or "") if pending_task else "",
		"pending_task_id": str(pending_task.get("task_id") or "") if pending_task else "",
		# 页面顶部需要和待办卡片显示同一候选人，避免用户在多候选人时
		# 把评估或沟通结果记到错误对象上；这里仅返回姓名和标题元数据。
		"pending_task_title": str(pending_task.get("title") or "") if pending_task else "",
		"pending_candidate_name": str(pending_candidate.get("name") or "") if pending_task else "",
		"pending_candidate_id": str(pending_task.get("candidate_id") or "") if pending_task else "",
		"pending_job_id": str(pending_task.get("job_id") or selected_job_id or "") if pending_task else str(selected_job_id or ""),
		# ``focus_*`` 是顶部“去处理”按钮使用的稳定定位信息。没有待办时
		# 可定位到跳过任务或候选人阶段卡片，不再把用户送到无动作的空白区域。
		"focus_task_id": str(focus_task.get("task_id") or "") if focus_task else "",
		"focus_candidate_id": str(focus_candidate.get("candidate_id") or ""),
		"selected_job_id": selected_job_id or "",
		"queue": queue,
		"queue_summary": {
			"total": len(queue),
			"actionable": sum(1 for row in queue if not row["is_terminal"]),
			"terminal": sum(1 for row in queue if row["is_terminal"]),
		},
		"counts": {
			"jobs": len(job_rows),
			"candidates": len(candidate_rows),
			"assessments": len(assessment_rows),
			"reviewed": reviewed_count,
			"pending_tasks": len(pending_tasks),
		},
		"steps": steps,
	}
