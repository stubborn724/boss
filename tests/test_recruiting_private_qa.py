"""招聘闭环的私域专业核验与面试终局门禁测试。

这些测试固定两个容易断链的边界：岗位关闭 BOSS 专业问答后，专业核验仍然
必须在私域以结构化本地记录承接；面试未通过时，录用决定不能被普通待办绕过。
所有平台动作仍由 HR 手工完成，测试只验证本地工作台的状态和审计事实。
"""

from pathlib import Path

import pytest

from boss_agent_cli.recruiting.workspace import RecruitingWorkspace
from boss_agent_cli.recruiting.store import RecruitingStore


def test_private_professional_qa_is_a_real_checkpoint_when_boss_qa_is_disabled(tmp_path: Path) -> None:
	"""关闭 BOSS 问答后，基础意向不能直接跳过私域专业核验。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：私域候选人\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", professional_qa_enabled=False)
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	job_id = job["job"]["job_id"]

	workspace.assess(job_id, candidate["candidate_id"])
	workspace.confirm_basic_intent(job_id, candidate["candidate_id"], note="已确认城市、薪资和工作节奏")

	snapshot = workspace.snapshot(job_id)
	private_task = next(task for task in snapshot["tasks"] if task["status"] == "pending" and task["kind"] == "private_professional_qa")
	assert private_task["kind"] == "private_professional_qa"
	assert private_task["target_stage"] == "professional_passed"
	assert snapshot["workflow"]["next_step"] == "private_professional_qa"


def test_private_professional_qa_records_source_version_and_passes_to_resume_exchange(tmp_path: Path) -> None:
	"""私域专业核验应保存结构化来源和结论，并解锁交换简历待办。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：私域候选人\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", professional_qa_enabled=False)
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	job_id = job["job"]["job_id"]
	candidate_id = candidate["candidate_id"]

	workspace.assess(job_id, candidate_id)
	workspace.confirm_basic_intent(job_id, candidate_id, note="已确认基础意向")

	follow_up = workspace.record_private_professional_qa(
		job_id,
		candidate_id,
		question="请说明你负责过的客户开发项目？",
		answer="我负责企业客户开发，持续跟进并完成签约。",
		question_id="private-question-1",
		question_version="v2",
		source_ids=["faq-sales-process"],
		outcome="follow_up",
		note="需要补充可量化结果",
	)
	assert follow_up["answer"]["channel"] == "private_domain"
	assert follow_up["answer"]["verification_status"] == "follow_up"
	assert follow_up["answer"]["question_version"] == "v2"
	assert follow_up["answer"]["source_ids"] == ["faq-sales-process"]
	private_events = workspace.list_candidate_events(candidate_id, job_id=job_id)
	assert private_events[-1]["action"] == "私域专业核验待补充"
	assert private_events[-1]["job_id"] == job_id
	assert workspace.snapshot(job_id)["workflow"]["next_step"] == "private_professional_qa"

	passed = workspace.record_private_professional_qa(
		job_id,
		candidate_id,
		question="请补充项目结果和你的具体职责？",
		answer="我负责从需求诊断到签约，季度完成 12 个项目。",
		question_id="private-question-1-follow-up",
		question_version="v1",
		source_ids=["kb-sales-process-v3"],
		outcome="passed",
		note="已确认职责、过程和结果",
		follow_up_of="private-question-1",
	)
	assert passed["answer"]["verification_status"] == "passed"
	workspace.review_assessment(
		job_id,
		candidate_id,
		outcome="proceed",
		manual_override=True,
		override_reason="测试已完成私域专业核验，允许继续交换简历。",
	)
	after = workspace.snapshot(job_id)
	assert after["candidates"][0]["stage"] == "professional_passed"
	assert any(task["kind"] == "prepare_resume_exchange" and task["status"] == "pending" for task in after["tasks"])
	assert after["candidates"][0]["answer_count"] == 2


def test_failed_interview_cannot_be_recorded_as_hired(tmp_path: Path) -> None:
	"""面试明确未通过时，终局待办必须阻止录用并保留失败结果。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：面试候选人\n有销售经验。", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path, job_id=job.job_id)
	store.transition_candidate(candidate.candidate_id, job_id=job.job_id, stage="private_domain_pending", action="等待私域确认")
	store.record_private_contact(candidate.candidate_id, job_id=job.job_id, channel="wechat", status="added")
	prepare = next(task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.kind == "prepare_interview")
	store.complete_task(prepare.task_id)
	store.schedule_interview(job.job_id, candidate.candidate_id, scheduled_at="2026-08-03 14:00")
	store.record_interview_result(job.job_id, candidate.candidate_id, outcome="failed", note="面试未通过")

	decision_task = next(task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.kind == "record_hiring_decision")
	with pytest.raises(ValueError, match="面试未通过"):
		store.complete_task(decision_task.task_id, target_stage="hired", note="不应录用")

	interview = store.list_interview_invites(job.job_id, candidate.candidate_id)[0]
	assert interview["outcome"] == "failed"
	assert store.get_candidate(candidate.candidate_id).stage == "interview_completed"
