"""招聘工作台本地 JSON 存储的行为契约测试。"""

from pathlib import Path
import json

import pytest

from boss_agent_cli.recruiting.models import RecruitingCriteria
from boss_agent_cli.recruiting.store import RecruitingStore


def test_store_migrates_legacy_communication_task_target_stage(tmp_path: Path) -> None:
	"""旧工作区的首轮沟通待办必须迁移到新的私域前置阶段。"""
	legacy_state = {
		"version": 1,
		"jobs": {"job-1": {"job_id": "job-1", "name": "销售顾问", "status": "published"}},
		"candidates": {
			"candidate-1": {
				"candidate_id": "candidate-1",
				"name": "候选人",
				"stage": "private_domain_pending",
				"source": "boss_conversation",
				"resume_path": str(tmp_path / "candidate.md"),
			}
		},
		"candidate_tasks": {
			"task-1": {
				"task_id": "task-1",
				"candidate_id": "candidate-1",
				"job_id": "job-1",
				"kind": "continue_conversation",
				"title": "在 BOSS 沟通页继续沟通",
				"target_stage": "private_domain_added",
				"status": "pending",
				"communication_round": 1,
			}
		},
	}
	workspace_path = tmp_path / "recruiting" / "workspace.json"
	workspace_path.parent.mkdir(parents=True, exist_ok=True)
	workspace_path.write_text(json.dumps(legacy_state, ensure_ascii=False), encoding="utf-8")

	store = RecruitingStore(tmp_path)
	task = store.get_task("task-1")

	assert task is not None
	assert task.target_stage == "private_domain_pending"
	persisted = json.loads(workspace_path.read_text(encoding="utf-8"))
	assert persisted["version"] == 3
	assert "candidate_job_states" in persisted
	assert persisted["candidate_tasks"]["task-1"]["target_stage"] == "private_domain_pending"


def test_store_legacy_job_defaults_professional_qa_to_enabled(tmp_path: Path) -> None:
	"""旧岗位缺少专业问答字段时必须保持原有的启用语义。"""
	legacy_state = {
		"version": 2,
		"jobs": {"job-legacy": {"job_id": "job-legacy", "name": "销售顾问", "status": "published"}},
	}
	workspace_path = tmp_path / "recruiting" / "workspace.json"
	workspace_path.parent.mkdir(parents=True, exist_ok=True)
	workspace_path.write_text(json.dumps(legacy_state, ensure_ascii=False), encoding="utf-8")

	job = RecruitingStore(tmp_path).get_job("job-legacy")

	assert job is not None
	assert job.professional_qa_enabled is True
	assert job.to_dict()["professional_qa_enabled"] is True


def test_store_routes_disabled_professional_qa_to_resume_exchange(tmp_path: Path) -> None:
	"""岗位关闭专业问答后，基础意向确认应先进入私域专业核验待办。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("姓名：候选人\n有销售经验。", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问", professional_qa_enabled=False)
	candidate = store.import_candidate(resume_path, job_id=job.job_id)

	store.confirm_basic_intent(job.job_id, candidate.candidate_id, note="已确认基础意向")
	pending = [task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.job_id == job.job_id]

	kinds = [task.kind for task in pending]
	assert "private_professional_qa" in kinds
	assert "prepare_resume_exchange" not in kinds
	assert "start_professional_qa" not in kinds
	private_task = next(task for task in pending if task.kind == "private_professional_qa")
	assert private_task.target_stage == "professional_passed"


def test_store_persists_jobs_knowledge_faq_candidates_and_assessments(tmp_path: Path) -> None:
	"""重建 Store 实例后，岗位和候选人评估仍应可恢复。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")

	store = RecruitingStore(tmp_path)
	job = store.create_job(
		name="销售顾问",
		city="杭州",
		salary_range="10-20K",
		criteria=RecruitingCriteria(must_have=["销售经验"]),
	)
	document = store.add_knowledge(job.job_id, category="sales", title="销售流程", content="客户开发与成交流程")
	faq = store.add_faq(job.job_id, question="是否双休？", answer="目前是单休。", allowed_variation="不能改成双休")
	candidate = store.import_candidate(resume_path)
	store.save_assessment(
		job.job_id,
		candidate.candidate_id,
		{
			"final_score": 82,
			"decision": "待人工确认",
		},
	)

	reloaded = RecruitingStore(tmp_path)
	assert reloaded.get_job(job.job_id).name == "销售顾问"
	assert reloaded.list_knowledge(job.job_id)[0].document_id == document.document_id
	assert reloaded.list_faq(job.job_id)[0].faq_id == faq.faq_id
	assert reloaded.get_candidate(candidate.candidate_id).name == "赵六"
	assert reloaded.get_assessment(job.job_id, candidate.candidate_id)["final_score"] == 82


def test_store_persists_structured_candidate_profile_and_refreshes_it_on_reimport(tmp_path: Path) -> None:
	"""导入简历时提取的业务画像应可恢复，重复导入只刷新画像而不重置阶段。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text(
		"姓名：赵六\n城市：杭州\n期望薪资：12-18K\n学历：本科\n"
		"工作经验：3年\n最近职位：电话销售顾问\n行业：企业服务\n技能：电话销售、客户开发",
		encoding="utf-8",
	)
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path, job_id=job.job_id)

	assert candidate.profile["city"] == "杭州"
	assert candidate.profile["expected_salary"] == "12-18K"
	assert candidate.profile["education"] == "本科"
	assert candidate.profile["experience_years"] == 3.0
	assert candidate.profile["skills"] == ["电话销售", "客户开发"]

	store.transition_candidate(candidate.candidate_id, stage="initial_pass", action="人工初筛通过")
	resume_path.write_text(
		"姓名：赵六\n城市：上海\n期望薪资：20K\n学历：硕士\n"
		"工作经验：5年\n最近职位：商务拓展\n技能：商务拓展",
		encoding="utf-8",
	)
	refreshed = store.import_candidate(resume_path, job_id=job.job_id)
	reloaded = RecruitingStore(tmp_path).get_candidate(candidate.candidate_id)

	assert refreshed.stage == "initial_pass"
	assert refreshed.profile["city"] == "上海"
	assert refreshed.profile["experience_years"] == 5.0
	assert reloaded is not None
	assert reloaded.profile["education"] == "硕士"
	assert "industry" in reloaded.profile


def test_store_records_four_round_communications_and_creates_follow_up_tasks(tmp_path: Path) -> None:
	"""四轮沟通必须逐轮落盘，并在最后一轮通过后转入私域待记录。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)
	store.save_assessment(job.job_id, candidate.candidate_id, {"final_score": 82, "decision": "待人工确认"})
	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="proceed",
		decision="已确认继续沟通",
		next_action="人工确认后继续沟通",
		note="进入沟通流程",
		candidate_stage="private_domain_pending",
	)

	first = store.record_communication(
		job.job_id,
		candidate.candidate_id,
		round_number=1,
		outcome="follow_up",
		candidate_reply_summary="候选人愿意了解岗位，但需要补充工作时间信息",
		next_follow_up_at="2026-08-04 10:00",
	)
	assert first["communication"]["round_number"] == 1
	assert first["candidate"]["stage"] == "private_domain_pending"
	second_task = next(
		task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending")
		if task.communication_round == 2
	)
	assert second_task.due_at == "2026-08-04 10:00"

	for round_number in (2, 3):
		store.record_communication(
			job.job_id,
			candidate.candidate_id,
			round_number=round_number,
			outcome="continue",
			candidate_reply_summary=f"第 {round_number} 轮已完成",
		)

	last = store.record_communication(
		job.job_id,
		candidate.candidate_id,
		round_number=4,
		outcome="qualified",
		candidate_reply_summary="双方确认岗位方向和沟通意愿",
		note="四轮沟通完成",
	)

	assert last["candidate"]["stage"] == "private_domain_pending"
	assert len(store.list_communications(job.job_id, candidate.candidate_id)) == 4
	assert any(
		task.kind == "record_private_contact" and task.status == "pending"
		for task in store.list_candidate_tasks(candidate.candidate_id, status="pending")
	)


def test_store_requires_communication_record_interface(tmp_path: Path) -> None:
	"""沟通待办不能用通用完成接口跳过轮次和回复摘要审计。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)
	store.save_assessment(job.job_id, candidate.candidate_id, {"final_score": 82, "decision": "待人工确认"})
	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="proceed",
		decision="已确认继续沟通",
		next_action="人工确认后继续沟通",
		note="进入沟通流程",
		candidate_stage="private_domain_pending",
	)
	communication_task = next(
		task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending")
		if task.kind == "continue_conversation"
	)

	with pytest.raises(ValueError, match="必须通过对应记录接口完成"):
		store.complete_task(communication_task.task_id)


def test_store_rejects_out_of_order_communication_round(tmp_path: Path) -> None:
	"""沟通轮次必须按 1、2、3、4 顺序记录，避免时间线被覆盖。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)
	store.save_assessment(job.job_id, candidate.candidate_id, {"final_score": 82, "decision": "待人工确认"})
	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="proceed",
		decision="已确认继续沟通",
		next_action="人工确认后继续沟通",
		note="进入沟通流程",
		candidate_stage="private_domain_pending",
	)

	try:
		store.record_communication(
			job.job_id,
			candidate.candidate_id,
			round_number=2,
			outcome="continue",
			candidate_reply_summary="跳过首轮",
		)
	except ValueError as exc:
		assert "轮" in str(exc)
	else:
		raise AssertionError("out-of-order communication round should be rejected")


def test_import_candidate_rejects_unsupported_or_oversized_files(tmp_path: Path) -> None:
	"""导入只接受受控文本文件，并限制大小避免把任意文件读入内存。"""
	store = RecruitingStore(tmp_path)
	bad_path = tmp_path / "candidate.exe"
	bad_path.write_bytes(b"binary")

	try:
		store.import_candidate(bad_path)
	except ValueError as exc:
		assert "文本" in str(exc)
	else:
		raise AssertionError("unsupported file should be rejected")


def test_store_persists_private_contact_interview_and_hiring_decision(tmp_path: Path) -> None:
	"""私域、面试和终局决定必须落在同一条可恢复的本地候选人链路上。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问", criteria=RecruitingCriteria(must_have=["销售经验"]))
	candidate = store.import_candidate(resume_path)

	store.transition_candidate(candidate.candidate_id, stage="private_domain_pending", action="人工确认继续沟通")
	contact = store.record_private_contact(
		candidate.candidate_id,
		channel="wechat",
		status="added",
		note="已由 HR 在官方页面确认添加",
	)

	assert contact["contact"]["status"] == "added"
	assert contact["candidate"]["stage"] == "private_domain_added"
	assert any(task.kind == "prepare_interview" for task in store.list_candidate_tasks(candidate.candidate_id, status="pending"))

	prepare = next(task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.kind == "prepare_interview")
	store.complete_task(prepare.task_id)
	assert store.get_candidate(candidate.candidate_id).stage == "interview_pending"

	invite = store.schedule_interview(
		job.job_id,
		candidate.candidate_id,
		scheduled_at="2026-08-03 14:00",
		interviewer="王主管",
		note="视频面试，已由 HR 在官方页面发送邀请",
	)
	assert invite["invite"]["status"] == "scheduled"
	assert invite["candidate"]["stage"] == "interview_scheduled"

	result = store.record_interview_result(
		job.job_id,
		candidate.candidate_id,
		outcome="passed",
		note="专业能力和沟通表现符合岗位要求",
	)
	assert result["invite"]["status"] == "completed"
	assert result["candidate"]["stage"] == "interview_completed"

	decision_task = next(task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.kind == "record_hiring_decision")
	assert set(decision_task.allowed_target_stages) == {"hired", "rejected", "paused"}
	decision = store.complete_task(decision_task.task_id, target_stage="hired", note="审批通过")
	assert decision["candidate"]["stage"] == "hired"
	assert store.list_candidate_decisions(job.job_id, candidate.candidate_id)[0]["outcome"] == "hired"

	reloaded = RecruitingStore(tmp_path)
	assert reloaded.list_private_contacts(candidate.candidate_id)[0]["channel"] == "wechat"
	assert reloaded.list_interview_invites(job.job_id, candidate.candidate_id)[0]["scheduled_at"] == "2026-08-03 14:00"
	assert reloaded.list_candidate_decisions(job.job_id, candidate.candidate_id)[0]["outcome"] == "hired"


def test_store_rejects_invalid_private_contact_and_hiring_target(tmp_path: Path) -> None:
	"""外部状态值不能绕过本地状态机或制造无效的终局记录。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)

	try:
		store.record_private_contact(candidate.candidate_id, channel="unknown", status="added")
	except ValueError as exc:
		assert "渠道" in str(exc)
	else:
		raise AssertionError("unsupported private contact channel should be rejected")

	store.transition_candidate(candidate.candidate_id, stage="interview_completed", action="面试完成")
	decision_task = next(task for task in store.list_candidate_tasks(candidate.candidate_id, status="pending") if task.kind == "record_hiring_decision")
	try:
		store.complete_task(decision_task.task_id, target_stage="interview_pending")
	except ValueError as exc:
		assert "终局" in str(exc) or "阶段" in str(exc)
	else:
		raise AssertionError("invalid hiring target should be rejected")


def test_store_does_not_allow_generic_stage_transition_to_close_candidate(tmp_path: Path) -> None:
	"""普通阶段记录不能绕过面试结果和终局待办直接写入录用状态。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)

	with pytest.raises(ValueError, match="终局"):
		store.transition_candidate(candidate.candidate_id, stage="hired", action="直接录用")

	assert store.get_candidate(candidate.candidate_id).stage == "pending_screening"
	assert store.list_candidate_decisions(None, candidate.candidate_id) == []


def test_store_records_rejection_decision_when_assessment_is_declined(tmp_path: Path) -> None:
	"""评估阶段暂不推进时，阶段和终局决定必须同时落盘。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path, job_id=job.job_id)
	store.save_assessment(job.job_id, candidate.candidate_id, {"review_required": True})

	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="reject",
		decision="已确认暂不推进",
		next_action="人工确认后结束本轮流程",
		note="经验方向不匹配",
		candidate_stage="rejected",
	)

	decisions = store.list_candidate_decisions(job.job_id, candidate.candidate_id)
	assert decisions[0]["outcome"] == "rejected"
	assert decisions[0]["reason"] == "经验方向不匹配"


def test_store_records_terminal_decision_for_declined_private_contact(tmp_path: Path) -> None:
	"""私域未添加进入暂缓时，也必须留下可复盘的终局决定。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path, job_id=job.job_id)
	store.transition_candidate(candidate.candidate_id, stage="private_domain_pending", action="等待私域确认")

	store.record_private_contact(candidate.candidate_id, channel="wechat", status="declined", note="候选人暂不添加")

	decisions = store.list_candidate_decisions(job.job_id, candidate.candidate_id)
	assert decisions[0]["outcome"] == "paused"
	assert decisions[0]["reason"] == "候选人暂不添加"


def test_store_records_terminal_decision_after_declined_communication(tmp_path: Path) -> None:
	"""沟通明确拒绝时，应和阶段更新一起生成淘汰决定。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path, job_id=job.job_id)
	store.save_assessment(job.job_id, candidate.candidate_id, {"review_required": True})
	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="proceed",
		decision="已确认继续沟通",
		next_action="人工确认后继续沟通",
		note="先完成沟通",
		candidate_stage="private_domain_pending",
		manual_override=True,
		override_reason="测试明确模拟业务例外放行。",
	)

	store.record_communication(
		job.job_id,
		candidate.candidate_id,
		round_number=1,
		outcome="declined",
		candidate_reply_summary="暂时不考虑",
	)

	decisions = store.list_candidate_decisions(job.job_id, candidate.candidate_id)
	assert decisions[0]["outcome"] == "rejected"
	assert decisions[0]["reason"] == "暂时不考虑"


def test_store_searches_knowledge_and_faq_with_ranked_sources(tmp_path: Path) -> None:
	"""知识检索必须只命中当前岗位，并返回可复核的来源摘要。"""
	store = RecruitingStore(tmp_path)
	first = store.create_job(name="销售顾问")
	second = store.create_job(name="门店店长")
	store.add_knowledge(first.job_id, category="sales", title="客户开发", content="先做需求诊断，再进行电话跟进。")
	store.add_faq(first.job_id, question="工作时间如何安排？", answer="周一至周六，具体以岗位说明为准。")
	store.add_knowledge(second.job_id, category="company", title="门店排班", content="按门店营业时间排班。")

	knowledge_hits = store.search_knowledge(first.job_id, "电话跟进")
	faq_hits = store.search_faq(first.job_id, "工作时间")

	assert knowledge_hits[0]["title"] == "客户开发"
	assert knowledge_hits[0]["kind"] == "knowledge"
	assert "电话跟进" in knowledge_hits[0]["snippet"]
	assert faq_hits[0]["question"] == "工作时间如何安排？"
	assert faq_hits[0]["kind"] == "faq"
	assert all("门店" not in str(hit) for hit in knowledge_hits + faq_hits)


def test_store_records_message_template_usage_without_sending_platform_message(tmp_path: Path) -> None:
	"""话术使用是本地审计事实，不应伪装成平台发送结果。"""
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	usage = store.record_message_template_usage(
		job.job_id,
		candidate_id="candidate-1",
		template_key="greeting",
		template_version="v1",
	)

	assert usage["template_key"] == "greeting"
	assert usage["template_version"] == "v1"
	assert usage["candidate_id"] == "candidate-1"
	assert usage["platform_action"] == "manual_only"
	assert store.list_message_template_usages(job.job_id)[0]["usage_id"] == usage["usage_id"]


def test_store_records_candidate_question_demand_in_job_context(tmp_path: Path) -> None:
	"""候选人问题需求只写入当前岗位上下文，并保留命中来源元数据。"""
	store = RecruitingStore(tmp_path)
	first = store.create_job(name="销售顾问")
	second = store.create_job(name="门店店长")
	record = store.record_question_demand(
		first.job_id,
		query=" 薪资怎么发？ ",
		status="answered",
		source_type="faq",
		source_id="faq-1",
		source_title="薪资说明",
	)

	assert record["job_id"] == first.job_id
	assert record["normalized_query"] == "薪资怎么发"
	assert store.list_question_demands(first.job_id)[0]["source_id"] == "faq-1"
	assert store.list_question_demands(second.job_id) == []


def test_store_communication_can_keep_template_reference(tmp_path: Path) -> None:
	"""沟通摘要可关联话术版本，但仍只记录人工回填内容。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六", encoding="utf-8")
	store = RecruitingStore(tmp_path)
	job = store.create_job(name="销售顾问")
	candidate = store.import_candidate(resume_path)
	store.save_assessment(job.job_id, candidate.candidate_id, {"final_score": 80, "decision": "待人工确认"})
	store.review_assessment(
		job.job_id,
		candidate.candidate_id,
		outcome="proceed",
		decision="已确认继续沟通",
		next_action="人工确认后继续沟通",
		note="准备记录沟通",
		candidate_stage="private_domain_pending",
	)
	store.record_communication(
		job.job_id,
		candidate.candidate_id,
		round_number=1,
		outcome="continue",
		candidate_reply_summary="愿意继续了解",
		template_key="greeting",
		template_version="v1",
	)

	communication = store.list_communications(job.job_id, candidate.candidate_id)[0]
	assert communication["template_key"] == "greeting"
	assert communication["template_version"] == "v1"
