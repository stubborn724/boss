"""招聘闭环新增状态的失败测试。

这些测试先固定三条用户可感知的契约：岗位必须明确发布、候选人要绑定岗位，
以及不匹配原因要能回填并进入本地复盘。实现仍然保持所有 BOSS 外部动作由 HR
手工完成，测试只验证本地工作台状态。
"""

import json
from pathlib import Path

import pytest

from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.recruiting.workflow import build_workflow_projection


def test_draft_job_requires_human_publish_before_assessment(tmp_path: Path) -> None:
	"""草稿岗位不能直接进入评估，发布后才允许筛选。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		criteria_text="本科；3年以上工作经验；必须有销售经验",
		status="draft",
	)
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	assert job["job"]["status"] == "draft"
	assert workspace.snapshot(job["job"]["job_id"])["workflow"]["next_step"] == "publish_job"
	with pytest.raises(ValueError, match="岗位尚未发布"):
		workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	published = workspace.publish_job(job["job"]["job_id"])
	assert published["status"] == "published"
	report = workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	assert report["job_id"] == job["job"]["job_id"]


def test_job_scopes_candidates_and_records_mismatch_feedback(tmp_path: Path) -> None:
	"""候选人绑定岗位后不能泄漏到其他岗位，不匹配要有可复盘记录。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first = workspace.create_job(name="销售顾问", status="published")
	second = workspace.create_job(name="产品经理", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first["job"]["job_id"])

	first_snapshot = workspace.snapshot(first["job"]["job_id"])
	second_snapshot = workspace.snapshot(second["job"]["job_id"])
	assert [row["candidate_id"] for row in first_snapshot["candidates"]] == [candidate["candidate_id"]]
	assert second_snapshot["candidates"] == []

	feedback = workspace.record_mismatch_feedback(
		first["job"]["job_id"],
		candidate["candidate_id"],
		reason_code="city_mismatch",
		stage="hard_filter",
		note="候选人期望城市不在岗位范围内",
	)
	assert feedback["reason_code"] == "city_mismatch"
	assert feedback["submitted_to_platform"] is False
	assert first_snapshot["pipeline"]["total"] == 1

	updated = workspace.snapshot(first["job"]["job_id"])
	assert updated["mismatch_feedback"][0]["candidate_id"] == candidate["candidate_id"]
	assert updated["mismatch_feedback"][0]["stage"] == "hard_filter"


def test_updating_published_job_requires_republish(tmp_path: Path) -> None:
	"""修改岗位标准后自动回到草稿，避免旧发布确认覆盖新条件。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="销售顾问", status="published")
	updated = workspace.update_job(
		created["job"]["job_id"],
		name="高级销售顾问",
		city="杭州",
		salary_range="15-25K",
		criteria_text="本科；3年以上工作经验；必须有电话销售经验",
	)
	assert updated["job"]["status"] == "draft"
	assert workspace.snapshot(created["job"]["job_id"])["workflow"]["next_step"] == "publish_job"


@pytest.mark.parametrize(
	("task_kind", "expected_action", "expected_step"),
	[
		("confirm_basic", "确认基础条件", "follow_up"),
		("complete_basic", "完成基础条件确认", "follow_up"),
		("start_professional_qa", "发起专业问答", "assessment"),
		("prepare_resume_exchange", "准备交换简历", "follow_up"),
		("review_resume", "完成简历评估", "assessment"),
	],
)
def test_intermediate_tasks_have_explicit_next_actions(
	task_kind: str,
	expected_action: str,
	expected_step: str,
) -> None:
	"""状态机生成的中间待办必须能映射到具体动作和闭环步骤。"""
	projection = build_workflow_projection(
		jobs=[{"job_id": "job-1", "status": "published"}],
		candidates=[{"candidate_id": "candidate-1", "name": "候选人", "stage": "basic_passed"}],
		tasks=[
			{
				"task_id": "task-1",
				"candidate_id": "candidate-1",
				"job_id": "job-1",
				"kind": task_kind,
				"title": expected_action,
				"status": "pending",
			}
		],
		assessments=[],
		selected_job_id="job-1",
	)

	assert projection["next_step"] == task_kind
	assert projection["next_action"] == expected_action
	step = next(item for item in projection["steps"] if item["key"] == expected_step)
	assert step["status"] == "current"


def test_assessment_consumes_intermediate_generation_tasks(tmp_path: Path) -> None:
	"""生成评估时应关闭专业问答/简历复核待办，避免同一动作重复出现。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n本科，3年销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.transition_candidate(
		candidate["candidate_id"],
		stage="basic_passed",
		action="基础意向人工确认",
	)
	first_intermediate = next(
		task for task in workspace.snapshot(job["job"]["job_id"])["tasks"]
		if task["kind"] == "start_professional_qa" and task["status"] == "pending"
	)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	state_after_qa = workspace.snapshot(job["job"]["job_id"])
	assert next(task for task in state_after_qa["tasks"] if task["task_id"] == first_intermediate["task_id"])["status"] == "completed"

	workspace.transition_candidate(
		candidate["candidate_id"],
		stage="resume_exchanged",
		action="人工完成简历交换",
	)
	resume_review = next(
		task for task in workspace.snapshot(job["job"]["job_id"])["tasks"]
		if task["kind"] == "review_resume" and task["status"] == "pending"
	)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	state_after_resume_review = workspace.snapshot(job["job"]["job_id"])
	assert next(task for task in state_after_resume_review["tasks"] if task["task_id"] == resume_review["task_id"])["status"] == "completed"


def test_initial_assessment_creates_basic_intent_checkpoint(tmp_path: Path) -> None:
	"""首次评估后必须生成基础意向待办，不能让评估门禁卡在隐藏阶段记录上。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text(
		"# 候选人简历\n\n姓名：待确认候选人\n城市：杭州\n期望薪资：10K\n"
		"学历：本科\n工作经验：3年\n技能：电话销售\n电话销售经验。",
		encoding="utf-8",
	)
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		education_requirement="本科",
		min_experience_years=2,
		criteria_text="必须有电话销售经验",
		status="published",
	)
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert snapshot["workflow"]["next_step"] == "confirm_basic"
	intent_task = next(task for task in snapshot["tasks"] if task["kind"] == "confirm_basic")
	assert intent_task["status"] == "pending"
	assert intent_task["target_stage"] == "basic_passed"

	with pytest.raises(ValueError, match="基础意向"):
		workspace.review_assessment(job["job"]["job_id"], candidate["candidate_id"], outcome="proceed")

	workspace.confirm_basic_intent(job["job"]["job_id"], candidate["candidate_id"], note="已确认城市、薪资和工作节奏")
	after_intent = workspace.snapshot(job["job"]["job_id"])
	assert after_intent["candidates"][0]["stage"] == "basic_passed"
	assert after_intent["workflow"]["next_step"] == "start_professional_qa"
	assert next(task for task in after_intent["tasks"] if task["task_id"] == intent_task["task_id"])["status"] == "completed"


def test_basic_intent_checkpoint_is_isolated_per_job(tmp_path: Path) -> None:
	"""同一候选人切换岗位后，基础意向事实不能跨岗位复用。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：多岗位候选人\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first_job = workspace.create_job(name="销售顾问", status="published")
	second_job = workspace.create_job(name="客户成功", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first_job["job"]["job_id"])
	workspace.import_candidate(resume_path, job_id=second_job["job"]["job_id"])

	workspace.confirm_basic_intent(first_job["job"]["job_id"], candidate["candidate_id"], note="第一岗位已确认基础意向")
	workspace.assess(second_job["job"]["job_id"], candidate["candidate_id"])

	second_snapshot = workspace.snapshot(second_job["job"]["job_id"])
	assert second_snapshot["workflow"]["next_step"] == "confirm_basic"
	assert any(
		task["kind"] == "confirm_basic" and task["status"] == "pending"
		for task in second_snapshot["tasks"]
	)


def test_candidate_stage_projection_is_isolated_per_job(tmp_path: Path) -> None:
	"""同一候选人的岗位阶段必须独立，避免 A 岗位推进后污染 B 岗位卡片。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：多岗位候选人\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first_job = workspace.create_job(name="销售顾问", status="published")
	second_job = workspace.create_job(name="客户成功", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first_job["job"]["job_id"])
	workspace.import_candidate(resume_path, job_id=second_job["job"]["job_id"])

	workspace.confirm_basic_intent(first_job["job"]["job_id"], candidate["candidate_id"], note="第一岗位已确认基础意向")

	first_snapshot = workspace.snapshot(first_job["job"]["job_id"])
	second_snapshot = workspace.snapshot(second_job["job"]["job_id"])

	assert first_snapshot["candidates"][0]["stage"] == "basic_passed"
	assert second_snapshot["candidates"][0]["stage"] == "pending_screening"
	assert second_snapshot["workflow"]["next_step"] == "assess_candidate"


def test_legacy_blank_job_events_are_only_read_for_single_job_candidates(tmp_path: Path) -> None:
	"""旧版无岗位事件只在候选人唯一绑定岗位时兼容，不能跨岗位猜归属。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：历史候选人\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first_job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first_job["job"]["job_id"])
	workspace.confirm_basic_intent(first_job["job"]["job_id"], candidate["candidate_id"], note="历史岗位已确认")

	state = json.loads(workspace.store.state_path.read_text(encoding="utf-8"))
	for raw_event in state["candidate_events"].values():
		if isinstance(raw_event, dict) and raw_event.get("candidate_id") == candidate["candidate_id"]:
			raw_event.pop("job_id", None)
	workspace.store.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

	assert workspace.list_candidate_events(candidate["candidate_id"], job_id=first_job["job"]["job_id"])

	second_job = workspace.create_job(name="客户成功", status="published")
	workspace.import_candidate(resume_path, job_id=second_job["job"]["job_id"])
	assert workspace.list_candidate_events(candidate["candidate_id"], job_id=first_job["job"]["job_id"]) == []
	assert workspace.list_candidate_events(candidate["candidate_id"], job_id=second_job["job"]["job_id"]) == []


def test_review_resume_task_cannot_be_marked_complete_without_assessment(tmp_path: Path) -> None:
	"""简历复核待办必须生成评估，不能用普通完成按钮绕过证据。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n本科，3年销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.transition_candidate(candidate["candidate_id"], stage="resume_exchanged", action="人工完成简历交换")
	resume_review = next(
		task for task in workspace.snapshot(job["job"]["job_id"])["tasks"]
		if task["kind"] == "review_resume" and task["status"] == "pending"
	)

	with pytest.raises(ValueError, match="评估待办必须通过评估接口完成"):
		workspace.complete_task(resume_review["task_id"])


def test_workflow_projection_scopes_next_task_to_selected_job_and_candidate() -> None:
	"""多岗位、多候选人时，闭环下一步必须来自当前岗位的可见候选人。"""
	projection = build_workflow_projection(
		jobs=[
			{"job_id": "job-1", "status": "published"},
			{"job_id": "job-2", "status": "draft"},
		],
		candidates=[
			{"candidate_id": "candidate-1", "name": "当前候选人", "stage": "pending_screening"},
		],
		tasks=[
			{
				"task_id": "other-task",
				"candidate_id": "candidate-2",
				"job_id": "job-2",
				"kind": "review_assessment",
				"title": "其他岗位人工确认",
				"status": "pending",
			},
			{
				"task_id": "current-task",
				"candidate_id": "candidate-1",
				"job_id": "job-1",
				"kind": "assess_candidate",
				"title": "选择岗位并生成简历评估",
				"status": "pending",
			},
		],
		assessments=[],
		selected_job_id="job-1",
	)

	assert projection["pending_task_id"] == "current-task"
	assert projection["pending_candidate_id"] == "candidate-1"
	assert projection["next_step"] == "assess_candidate"
	job_step = next(item for item in projection["steps"] if item["key"] == "job_setup")
	assert job_step["status"] == "complete"


def test_snapshot_does_not_leak_blank_job_tasks_from_another_candidate(tmp_path: Path) -> None:
	"""旧数据中缺少岗位标识的待办也必须按当前岗位候选人集合隔离。"""
	resume_one = tmp_path / "候选人一.md"
	resume_two = tmp_path / "候选人二.md"
	resume_one.write_text("# 候选人简历\n\n姓名：甲", encoding="utf-8")
	resume_two.write_text("# 候选人简历\n\n姓名：乙", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first_job = workspace.create_job(name="销售顾问", status="published")
	second_job = workspace.create_job(name="产品经理", status="published")
	workspace.import_candidate(resume_one, job_id=first_job["job"]["job_id"])
	second_candidate = workspace.import_candidate(resume_two, job_id=second_job["job"]["job_id"])
	workspace.transition_candidate(second_candidate["candidate_id"], stage="basic_passed", action="历史阶段回填")

	first_snapshot = workspace.snapshot(first_job["job"]["job_id"])

	assert all(task["candidate_id"] != second_candidate["candidate_id"] for task in first_snapshot["tasks"])


def test_review_assessment_cannot_reopen_an_already_reviewed_candidate(tmp_path: Path) -> None:
	"""人工确认后的评估必须先重新生成，不能重复提交把候选人从终局拉回流程。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"],
		candidate["candidate_id"],
		outcome="proceed",
		manual_override=True,
		override_reason="测试覆盖重复点击保护。",
	)

	with pytest.raises(ValueError, match="重新生成评估"):
		workspace.review_assessment(
			job["job"]["job_id"], candidate["candidate_id"], outcome="proceed", manual_override=True,
			override_reason="不应重复打开候选人。",
		)


def test_terminal_candidate_cannot_start_a_new_assessment(tmp_path: Path) -> None:
	"""录用、淘汰和暂缓是闭环终点，不能从旧候选人卡片重新启动评估。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(job["job"]["job_id"], candidate["candidate_id"], outcome="reject")

	with pytest.raises(ValueError, match="终局"):
		workspace.assess(job["job"]["job_id"], candidate["candidate_id"])


def test_local_recruiting_workflow_reaches_terminal_state_without_orphan_tasks(tmp_path: Path) -> None:
	"""按工作台真实接口顺序走完一名候选人，终局不能留下未承接的待办。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text(
		"# 候选人简历\n\n姓名：闭环候选人\n城市：杭州\n期望薪资：10-20K\n"
		"学历：本科\n工作经验：3年\n电话销售经验。",
		encoding="utf-8",
	)
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		education_requirement="本科",
		min_experience_years=2,
		criteria_text="必须有电话销售经验",
		status="published",
	)
	candidate = workspace.import_candidate(resume_path, source="boss_conversation", job_id=job["job"]["job_id"])
	job_id = job["job"]["job_id"]
	candidate_id = candidate["candidate_id"]

	assert workspace.snapshot(job_id)["workflow"]["next_step"] == "assess_candidate"
	workspace.assess(job_id, candidate_id)
	workspace.confirm_basic_intent(job_id, candidate_id, note="已确认城市、薪资和工作节奏")
	workspace.assess(job_id, candidate_id)
	workspace.record_answer(
		job_id,
		candidate_id,
		question="你做过什么电话销售项目？",
		answer="我负责企业客户开发和电话销售，完成了多个签约项目。",
	)
	workspace.assess(job_id, candidate_id)
	workspace.review_assessment(job_id, candidate_id, outcome="proceed")
	assert workspace.snapshot(job_id)["workflow"]["next_step"] == "prepare_resume_exchange"
	resume_exchange = next(
		task for task in workspace.snapshot(job_id)["tasks"]
		if task["kind"] == "prepare_resume_exchange" and task["status"] == "pending"
	)
	workspace.complete_task(resume_exchange["task_id"], note="已在 BOSS 官方页面完成简历交换")
	workspace.assess(job_id, candidate_id)
	workspace.review_assessment(job_id, candidate_id, outcome="proceed")
	assert workspace.snapshot(job_id)["workflow"]["next_step"] == "continue_conversation"

	for round_number in (1, 2, 3):
		workspace.record_communication(
			job_id,
			candidate_id,
			round_number=round_number,
			outcome="continue",
			candidate_reply_summary=f"第 {round_number} 轮沟通继续",
		)
	workspace.record_communication(
		job_id,
		candidate_id,
		round_number=4,
		outcome="qualified",
		candidate_reply_summary="双方确认岗位方向和入职意愿",
	)
	workspace.record_private_contact(candidate_id, channel="wechat", status="added", note="已人工确认添加")
	prepare_interview = next(
		task
		for task in workspace.snapshot(job_id)["tasks"]
		if task["kind"] == "prepare_interview" and task["status"] == "pending"
	)
	workspace.complete_task(prepare_interview["task_id"])
	workspace.schedule_interview(
		job_id,
		candidate_id,
		scheduled_at="2026-08-03 14:00",
		interviewer="王主管",
	)
	workspace.record_interview_result(job_id, candidate_id, outcome="passed", note="面试通过")
	decision_task = next(
		task
		for task in workspace.snapshot(job_id)["tasks"]
		if task["kind"] == "record_hiring_decision" and task["status"] == "pending"
	)
	workspace.complete_task(decision_task["task_id"], target_stage="hired", note="审批通过")

	closed = workspace.snapshot(job_id)
	assert closed["workflow"]["next_step"] == "closed"
	assert closed["candidates"][0]["stage"] == "hired"
	assert not [task for task in closed["tasks"] if task["status"] == "pending"]
	assert next(step for step in closed["workflow"]["steps"] if step["key"] == "follow_up")["status"] == "complete"
	assert closed["pipeline"]["active"] == 0


def test_assessment_review_keeps_resume_exchange_checkpoint(tmp_path: Path) -> None:
	"""评估通过必须先完成简历交换，再进入简历评估和沟通，不能跳到私域。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：阶段候选人\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	job_id = job["job"]["job_id"]
	candidate_id = candidate["candidate_id"]

	workspace.assess(job_id, candidate_id)
	workspace.review_assessment(
		job_id,
		candidate_id,
		outcome="proceed",
		manual_override=True,
		override_reason="测试阶段路径，不代表跳过人工确认。",
	)

	after_professional_review = workspace.snapshot(job_id)
	assert after_professional_review["candidates"][0]["stage"] == "professional_passed"
	resume_exchange = next(
		task
		for task in after_professional_review["tasks"]
		if task["kind"] == "prepare_resume_exchange" and task["status"] == "pending"
	)
	assert resume_exchange["target_stage"] == "resume_exchanged"
	assert not any(task["kind"] == "continue_conversation" and task["status"] == "pending" for task in after_professional_review["tasks"])

	workspace.complete_task(resume_exchange["task_id"], note="已在 BOSS 官方沟通页完成简历交换")
	after_exchange = workspace.snapshot(job_id)
	assert after_exchange["candidates"][0]["stage"] == "resume_exchanged"
	resume_review = next(
		task
		for task in after_exchange["tasks"]
		if task["kind"] == "review_resume" and task["status"] == "pending"
	)

	workspace.assess(job_id, candidate_id)
	workspace.review_assessment(
		job_id,
		candidate_id,
		outcome="proceed",
		manual_override=True,
		override_reason="测试第二次人工确认。",
	)

	after_resume_review = workspace.snapshot(job_id)
	assert after_resume_review["candidates"][0]["stage"] == "resume_passed"
	assert next(task for task in after_resume_review["tasks"] if task["task_id"] == resume_review["task_id"])["status"] == "completed"
	communication_task = next(
		task for task in after_resume_review["tasks"]
		if task["kind"] == "continue_conversation" and task["status"] == "pending"
	)
	assert communication_task["target_stage"] == "private_domain_pending"


def test_paused_candidate_is_not_counted_as_active(tmp_path: Path) -> None:
	"""暂缓是终局，不应继续占用活跃候选人数量。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.transition_candidate(candidate["candidate_id"], stage="interview_completed", action="面试完成")
	decision_task = next(
		task
		for task in workspace.snapshot(job["job"]["job_id"])["tasks"]
		if task["kind"] == "record_hiring_decision" and task["status"] == "pending"
	)
	workspace.complete_task(decision_task["task_id"], target_stage="paused", note="候选人暂缓")

	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert snapshot["pipeline"]["active"] == 0
	assert snapshot["workflow"]["next_step"] == "closed"


def test_pipeline_counts_resume_checkpoints_as_reviewed(tmp_path: Path) -> None:
	"""漏斗复核率必须覆盖专业通过和已交换简历两个中间检查点。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.transition_candidate(
		candidate["candidate_id"],
		job_id=job["job"]["job_id"],
		stage="professional_passed",
		action="专业问答通过，等待简历交换",
	)

	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert snapshot["pipeline"]["conversion"]["reviewed_rate"] == 100


def test_manual_stage_follow_up_stays_bound_to_the_selected_job(tmp_path: Path) -> None:
	"""同一候选人绑定多个岗位时，阶段回填产生的待办不能串到其他岗位。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first = workspace.create_job(name="销售顾问", status="published")
	second = workspace.create_job(name="客户成功", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first["job"]["job_id"])
	workspace.import_candidate(resume_path, job_id=second["job"]["job_id"])

	workspace.transition_candidate(
		candidate["candidate_id"],
		job_id=first["job"]["job_id"],
		stage="basic_passed",
		action="第一岗位基础条件确认",
	)

	first_tasks = workspace.snapshot(first["job"]["job_id"])["tasks"]
	first_snapshot = workspace.snapshot(first["job"]["job_id"])
	second_snapshot = workspace.snapshot(second["job"]["job_id"])
	second_tasks = second_snapshot["tasks"]

	assert first_snapshot["workflow"]["next_step"] == "start_professional_qa"
	assert any(
		task["kind"] == "assess_candidate" and task["job_id"] == second["job"]["job_id"]
		for task in second_tasks
		if task["status"] == "pending"
	)
	assert any(task["kind"] == "start_professional_qa" for task in first_tasks if task["status"] == "pending")
	assert not any(task["kind"] == "start_professional_qa" for task in second_tasks if task["status"] == "pending")


def test_skipped_task_can_be_reopened_from_the_workflow_history(tmp_path: Path) -> None:
	"""跳过外部动作后应能从历史待办恢复，不需要重新导入候选人。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.transition_candidate(
		candidate["candidate_id"],
		job_id=job["job"]["job_id"],
		stage="private_domain_pending",
		action="等待人工完成私域确认",
	)
	pending = next(
		task for task in workspace.snapshot(job["job"]["job_id"])["tasks"] if task["status"] == "pending"
	)
	workspace.complete_task(pending["task_id"], status="skipped", note="稍后处理")

	history = workspace.snapshot(job["job"]["job_id"])
	assert history["workflow"]["next_step"] == "recover_task"
	assert history["workflow"]["next_action"] == "恢复已跳过的待办"
	assert any(task["task_id"] == pending["task_id"] and task["status"] == "skipped" for task in history["tasks"])

	reopened = workspace.complete_task(pending["task_id"], status="pending", note="重新安排处理")
	assert reopened["task"]["status"] == "pending"
	final = workspace.snapshot(job["job"]["job_id"])
	assert final["workflow"]["next_step"] == "record_private_contact"
	assert final["workflow"]["pending_task_id"] == pending["task_id"]


def test_non_terminal_candidate_without_tasks_needs_a_real_stage_action() -> None:
	"""没有待办但仍未终局时，顶部下一步不能指向空的通用待办区。"""
	projection = build_workflow_projection(
		jobs=[{"job_id": "job-1", "status": "published"}],
		candidates=[{"candidate_id": "candidate-1", "name": "候选人", "stage": "greeted"}],
		tasks=[],
		assessments=[],
		selected_job_id="job-1",
	)

	assert projection["next_step"] == "record_stage"
	assert projection["next_action"] == "记录候选人阶段"
	assert projection["focus_candidate_id"] == "candidate-1"
	assert next(item for item in projection["steps"] if item["key"] == "follow_up")["status"] == "current"


def test_duplicate_terminal_decision_also_closes_legacy_pending_tasks(tmp_path: Path) -> None:
	"""旧数据重复写终局决定时，也必须清理后来遗留的待办。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n姓名：赵六", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	store = workspace.store

	with store._lock:
		state = store._read_state()
		store._record_terminal_decision_in_state(
			state,
			job_id=job["job"]["job_id"],
			candidate_id=candidate["candidate_id"],
			outcome="paused",
			reason="历史终局",
		)
		store._ensure_task_in_state(
			state,
			candidate_id=candidate["candidate_id"],
			job_id=job["job"]["job_id"],
			kind="record_hiring_decision",
			title="遗留终局待办",
			description="兼容旧数据",
			target_stage="paused",
			allowed_target_stages=("hired", "rejected", "paused"),
		)
		store._write_state(state)

	with store._lock:
		state = store._read_state()
		store._record_terminal_decision_in_state(
			state,
			job_id=job["job"]["job_id"],
			candidate_id=candidate["candidate_id"],
			outcome="paused",
			reason="历史终局再次同步",
		)
		store._write_state(state)

	assert not [
		task for task in store.list_candidate_tasks(candidate["candidate_id"], status="pending")
		if task.job_id == job["job"]["job_id"]
	]


def test_private_contact_is_scoped_to_the_selected_job(tmp_path: Path) -> None:
	"""同一候选人对应多个岗位时，私域回填只能关闭当前岗位待办。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	first = workspace.create_job(name="销售顾问", status="published")
	second = workspace.create_job(name="客户成功", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=first["job"]["job_id"])
	workspace.import_candidate(resume_path, job_id=second["job"]["job_id"])
	workspace.transition_candidate(
		candidate["candidate_id"],
		job_id=first["job"]["job_id"],
		stage="private_domain_pending",
		action="第一岗位等待私域确认",
	)
	workspace.transition_candidate(
		candidate["candidate_id"],
		job_id=second["job"]["job_id"],
		stage="private_domain_pending",
		action="第二岗位等待私域确认",
	)

	workspace.record_private_contact(
		candidate["candidate_id"],
		job_id=first["job"]["job_id"],
		channel="wechat",
		status="added",
		note="第一岗位已人工确认",
	)

	first_snapshot = workspace.snapshot(first["job"]["job_id"])
	second_snapshot = workspace.snapshot(second["job"]["job_id"])
	assert first_snapshot["private_contacts"][0]["job_id"] == first["job"]["job_id"]
	assert not [task for task in first_snapshot["tasks"] if task["kind"] == "record_private_contact" and task["status"] == "pending"]
	assert any(task["kind"] == "record_private_contact" and task["status"] == "pending" for task in second_snapshot["tasks"])
	assert second_snapshot["private_contacts"] == []
