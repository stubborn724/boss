"""招聘工作台服务的端到端本地行为测试。"""

from pathlib import Path

import pytest

from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


def test_workspace_keeps_job_knowledge_and_faq_isolated(tmp_path: Path) -> None:
	"""岗位标准、知识库和 FAQ 必须按岗位隔离，避免跨岗位污染话术。"""
	workspace = RecruitingWorkspace(tmp_path)
	first = workspace.create_job(
		name="电话销售顾问",
		city="杭州",
		salary_range="10-20K",
		criteria_text="必须有电话销售经验；招商加盟经验优先；不要按年龄筛选",
	)
	second = workspace.create_job(name="门店店长", criteria_text="必须有门店管理经验")
	workspace.add_knowledge(first["job"]["job_id"], category="sales", title="销售流程", content="先做需求诊断")
	workspace.add_faq(first["job"]["job_id"], question="是否单休？", answer="以岗位说明为准")

	first_snapshot = workspace.snapshot(first["job"]["job_id"])
	second_snapshot = workspace.snapshot(second["job"]["job_id"])

	assert first_snapshot["knowledge"][0]["content"] == "先做需求诊断"
	assert first_snapshot["faq"][0]["answer"] == "以岗位说明为准"
	assert second_snapshot["knowledge"] == []
	assert second_snapshot["faq"] == []
	assert first["warnings"]
	assert "年龄" in first["warnings"][0]


def test_snapshot_marks_candidate_when_resume_file_is_missing(tmp_path: Path) -> None:
	"""历史候选人引用的简历被移动后，页面必须明确标记而不能伪装成可分析数据。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="Java 工程师")
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("姓名：张三\n工作经验：3年", encoding="utf-8")
	workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	resume_path.unlink()

	candidate = workspace.snapshot(job["job"]["job_id"])["candidates"][0]

	assert candidate["resume_available"] is False


def test_workspace_persists_structured_job_requirements_from_natural_language(tmp_path: Path) -> None:
	"""创建岗位时解析出的结构化标准应可在重启后继续用于评估。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(
		name="SaaS 销售",
		criteria_text="本科及以上；3年以上工作经验；行业：SaaS；技能：CRM、电话销售",
	)

	job = workspace.store.get_job(created["job"]["job_id"])
	assert job is not None
	assert job.education_requirement == "本科及以上"
	assert job.min_experience_years == 3
	assert job.industry == "SaaS"
	assert job.skills == ["CRM", "电话销售"]


def test_workspace_accepts_explicit_job_requirements_for_publish_readiness(tmp_path: Path) -> None:
	"""页面显式填写学历和年限后，岗位应具备发布所需的完整标准。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(
		name="电话销售顾问",
		city="杭州",
		salary_range="10-20K",
		education_requirement="大专及以上",
		min_experience_years=2,
		criteria_text="必须有电话销售经验",
		status="draft",
	)

	job = workspace.store.get_job(created["job"]["job_id"])

	assert job is not None
	assert job.education_requirement == "大专及以上"
	assert job.min_experience_years == 2
	assert created["readiness"]["ready"] is True


def test_workspace_persists_explicit_industry_and_skills_for_job_standard(tmp_path: Path) -> None:
	"""岗位表单显式填写的行业和技能必须进入该岗位独立标准。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(
		name="ToB 销售顾问",
		industry="企业服务",
		skills=["客户开发", "电话销售"],
		criteria_text="必须有销售经验",
		status="draft",
	)

	job = workspace.store.get_job(created["job"]["job_id"])

	assert job is not None
	assert job.industry == "企业服务"
	assert job.skills == ["客户开发", "电话销售"]


def test_workspace_persists_job_professional_qa_toggle(tmp_path: Path) -> None:
	"""岗位快照和重建后的 Store 必须保留专业问答开关。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="销售顾问", professional_qa_enabled=False)

	assert created["job"]["professional_qa_enabled"] is False
	reloaded = RecruitingWorkspace(tmp_path).store.get_job(created["job"]["job_id"])
	assert reloaded is not None
	assert reloaded.professional_qa_enabled is False


def test_workspace_retrieves_job_scoped_knowledge_with_citations(tmp_path: Path) -> None:
	"""工作台检索结果应带来源类型和摘录，供人工复制前核对事实。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	workspace.add_knowledge(job["job"]["job_id"], category="sales", title="销售流程", content="先做需求诊断，再进行电话跟进。")
	workspace.add_faq(job["job"]["job_id"], question="是否双休？", answer="目前是单休。")

	result = workspace.search_knowledge(job["job"]["job_id"], "电话跟进")

	assert result["query"] == "电话跟进"
	assert result["hits"][0]["source_type"] == "knowledge"
	assert result["hits"][0]["source_title"] == "销售流程"
	assert result["hits"][0]["source_file_type"] == "manual"
	assert result["hits"][0]["score"] > 0
	assert "电话跟进" in result["hits"][0]["snippet"]


def test_workspace_answers_candidate_question_from_faq_with_provenance(tmp_path: Path) -> None:
	"""候选人问题优先使用当前岗位 FAQ，并返回可核对的来源版本。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	job_id = job["job"]["job_id"]
	faq = workspace.add_faq(
		job_id,
		question="工作时间是怎样的？",
		answer="工作时间为周一至周五 9:00-18:00。",
		source_title="员工手册",
		source_version="v-2026-08",
	)

	result = workspace.answer_question(job_id, "请问工作时间是怎样的？")

	assert result["status"] == "answered"
	assert result["answer"] == faq["answer"]
	assert result["source_type"] == "faq"
	assert result["source_id"] == faq["faq_id"]
	assert result["source_title"] == "员工手册"
	assert result["source_version"] == "v-2026-08"
	assert result["confidence"] == "faq"
	demand = workspace.store.list_question_demands(job_id)
	assert demand[0]["normalized_query"] == "请问工作时间是怎样的"
	assert demand[0]["source_id"] == faq["faq_id"]


def test_workspace_answers_knowledge_with_short_citation(tmp_path: Path) -> None:
	"""没有 FAQ 时可以引用知识库摘录，但不能返回整篇正文。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	job_id = job["job"]["job_id"]
	workspace.add_knowledge(
		job_id,
		category="company",
		title="福利说明",
		content="入职后缴纳社保，试用期为三个月。",
		source_sha256="sha-knowledge",
	)

	result = workspace.answer_question(job_id, "社保")

	assert result["status"] == "answered"
	assert result["source_type"] == "knowledge"
	assert result["source_title"] == "福利说明"
	assert result["source_version"] == "sha-knowledge"
	assert "入职后缴纳社保" in result["answer"]
	assert result["answer"] != "入职后缴纳社保，试用期为三个月。" or len(result["answer"]) <= 220


def test_workspace_refuses_unverifiable_question_and_keeps_job_isolation(tmp_path: Path) -> None:
	"""没有当前岗位来源时必须拒答，不能串用其他岗位事实。"""
	workspace = RecruitingWorkspace(tmp_path)
	first = workspace.create_job(name="销售顾问")
	second = workspace.create_job(name="门店店长")
	workspace.add_faq(first["job"]["job_id"], question="试用期多久？", answer="三个月。")

	result = workspace.answer_question(second["job"]["job_id"], "试用期多久？")

	assert result["status"] == "no_source"
	assert result["answer"] == "暂无可基于当前岗位本地事实确认的答案，请补充知识库或人工核实。"
	assert result["source_id"] == ""


def test_workspace_snapshot_includes_read_only_optimization_projection(tmp_path: Path) -> None:
	"""招聘快照应带回流建议，且明确没有自动变更。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	projection = workspace.snapshot(job["job"]["job_id"])["optimization"]

	assert projection["metrics"]["candidate_count"] == 1
	assert any(item["kind"] == "knowledge_gap" for item in projection["suggestions"])
	assert projection["mutations"] == []


def test_workspace_imports_and_assesses_resume_without_returning_body(tmp_path: Path) -> None:
	"""导入和评估只返回元数据与证据，简历正文不得进入工作台快照或报告。"""
	resume_body = "# 候选人简历\n\n姓名：赵六\n有电话销售经验和招商加盟经验。"
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text(resume_body, encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验；招商加盟经验优先")
	candidate = workspace.import_candidate(resume_path)

	report = workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert report["candidate_name"] == "赵六"
	assert report["review_required"] is True
	assert report["engine"] == "rules"
	assert "待人工确认" == report["decision"]
	assert resume_body not in str(report)
	assert resume_body not in str(snapshot)
	assert snapshot["candidates"][0]["resume_path"] == str(resume_path.resolve())


def test_workspace_snapshot_exposes_structured_candidate_profile_without_resume_body(tmp_path: Path) -> None:
	"""候选人卡片应能看到脱敏画像和缺失字段，但快照不能包含简历正文。"""
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", city="杭州", salary_range="10-20K")
	resume_path = tmp_path / "profile.md"
	resume_path.write_text(
		"姓名：钱七\n城市：杭州\n期望薪资：12K\n学历：大专\n"
		"工作经验：2年\n最近职位：电话销售\n技能：电话销售",
		encoding="utf-8",
	)
	workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	snapshot = workspace.snapshot(job["job"]["job_id"])
	candidate = snapshot["candidates"][0]

	assert candidate["profile"]["city"] == "杭州"
	assert candidate["profile"]["experience_years"] == 2.0
	assert candidate["profile"]["missing_fields"] == []
	assert "resume_text" not in candidate
	assert resume_path.read_text(encoding="utf-8") not in str(snapshot)


def test_workspace_persists_human_review_outcome_after_assessment(tmp_path: Path) -> None:
	"""评估后必须能记录人工决定，形成从初筛到下一步动作的闭环。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path, source="boss_conversation")
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	reviewed = workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed", note="已人工确认经历和城市",
		manual_override=True, override_reason="旧版流程测试：已由 HR 复核并允许继续。",
	)
	reloaded = workspace.snapshot(job["job"]["job_id"])

	assert candidate["source"] == "boss_conversation"
	assert reviewed["review_status"] == "proceed"
	assert reviewed["review_required"] is False
	assert reviewed["decision"] == "已确认继续沟通"
	assert reviewed["review_note"] == "已人工确认经历和城市"
	assert reloaded["assessments"][0]["next_action"] == "人工确认后继续沟通"
	assert reloaded["candidates"][0]["stage"] == "professional_passed"
	assert any(task["kind"] == "prepare_resume_exchange" and task["status"] == "pending" for task in reloaded["tasks"])


def test_workspace_tracks_candidate_pipeline_and_audit_without_exposing_quote(tmp_path: Path) -> None:
	"""候选人阶段变更应形成审计事件，但页面快照不能带出候选人原话。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	candidate = workspace.import_candidate(resume_path, source="boss_conversation")

	assert candidate["stage"] == "pending_screening"
	transition = workspace.transition_candidate(
		candidate["candidate_id"],
		stage="basic_passed",
		action="基础条件人工确认",
		note="已确认城市和工作节奏",
		ai_judgment="基础条件满足",
		candidate_quote="我可以接受杭州和单休",
	)

	snapshot = workspace.snapshot()
	row = snapshot["candidates"][0]
	assert transition["candidate"]["stage"] == "basic_passed"
	assert row["stage_label"] == "基础条件通过"
	assert row["event_count"] == 2
	assert row["timeline"][-1]["action"] == "基础条件人工确认"
	assert snapshot["pipeline"]["counts"]["basic_passed"] == 1
	assert "我可以接受杭州和单休" not in str(snapshot)
	assert workspace.list_candidate_events(candidate["candidate_id"])[-1]["candidate_quote"] == "我可以接受杭州和单休"


def test_workspace_records_professional_answers_and_uses_them_in_assessment(tmp_path: Path) -> None:
	"""专业问答回答应留在本地，并参与评估但不进入 Web 报告正文。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	candidate = workspace.import_candidate(resume_path)

	answer = workspace.record_answer(
		job["job"]["job_id"],
		candidate["candidate_id"],
		question="请举一个从陌生客户到成交的案例？",
		answer="我负责电话开发企业客户，先确认需求，再处理异议并跟进成交。",
	)
	report = workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert answer["answer_length"] > 0
	assert report["professional_qa_score"] is not None
	assert report["answer_count"] == 1
	assert snapshot["candidates"][0]["answer_count"] == 1
	assert "我负责电话开发企业客户" not in str(report)
	assert workspace.list_candidate_answers(job["job"]["job_id"], candidate["candidate_id"])[0]["answer"] == "我负责电话开发企业客户，先确认需求，再处理异议并跟进成交。"


def test_workspace_answers_keep_question_and_incrementing_version_metadata(tmp_path: Path) -> None:
	"""同一问题的重复回答应保留来源上下文并递增回答版本。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	candidate = workspace.import_candidate(resume_path)

	first = workspace.record_answer(
		job["job"]["job_id"],
		candidate["candidate_id"],
		question="请结合销售流程说明如何跟进？",
		answer="我会先做需求诊断，再根据客户阶段安排电话跟进。",
		question_id="question-kb-1",
		question_version="v-kb-1",
		source_ids=["kb-1"],
	)
	second = workspace.record_answer(
		job["job"]["job_id"],
		candidate["candidate_id"],
		question="请结合销售流程说明如何跟进？",
		answer="我会先确认需求，再记录下一步跟进时间。",
		question_id="question-kb-1",
		question_version="v-kb-1",
		source_ids=["kb-1"],
	)

	assert first["answer_version"] == 1
	assert second["answer_version"] == 2
	stored = workspace.list_candidate_answers(job["job"]["job_id"], candidate["candidate_id"])
	assert stored[-1]["question_id"] == "question-kb-1"
	assert stored[-1]["source_ids"] == ["kb-1"]


def test_workspace_rejects_unsupported_resume_and_missing_records(tmp_path: Path) -> None:
	"""工作台必须拒绝二进制简历，并对不存在的岗位和候选人给出明确错误。"""
	workspace = RecruitingWorkspace(tmp_path)
	bad_path = tmp_path / "candidate.exe"
	bad_path.write_bytes(b"MZ")

	with pytest.raises(ValueError, match="Markdown 或文本"):
		workspace.import_candidate(bad_path)
	with pytest.raises(KeyError):
		workspace.assess("missing-job", "missing-candidate")


def test_workspace_turns_assessment_review_into_completable_tasks(tmp_path: Path) -> None:
	"""评估、人工确认和后续动作必须通过待办串成可追踪的本地闭环。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path, source="boss_conversation")

	initial = workspace.snapshot(job["job"]["job_id"])
	assert [task["kind"] for task in initial["tasks"] if task["status"] == "pending"] == ["assess_candidate"]

	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	after_assessment = workspace.snapshot(job["job"]["job_id"])
	assert any(task["kind"] == "review_assessment" and task["status"] == "pending" for task in after_assessment["tasks"])
	assert any(task["kind"] == "assess_candidate" and task["status"] == "completed" for task in after_assessment["tasks"])
	review_task = next(task for task in after_assessment["tasks"] if task["kind"] == "review_assessment" and task["status"] == "pending")
	with pytest.raises(ValueError, match="人工确认"):
		workspace.complete_task(review_task["task_id"])

	workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed",
		manual_override=True, override_reason="测试覆盖外部沟通回填链路。",
	)
	after_review = workspace.snapshot(job["job"]["job_id"])
	pending = next(task for task in after_review["tasks"] if task["status"] == "pending")
	assert pending["kind"] == "prepare_resume_exchange"
	assert pending["target_stage"] == "resume_exchanged"
	assert after_review["candidates"][0]["stage"] == "professional_passed"

	workspace.complete_task(pending["task_id"], note="已在 BOSS 页面完成简历交换")
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed",
		manual_override=True, override_reason="测试覆盖简历复评后的沟通链路。",
	)
	communication_task = next(task for task in workspace.snapshot(job["job"]["job_id"])["tasks"] if task["kind"] == "continue_conversation" and task["status"] == "pending")
	workspace.record_communication(
		job["job"]["job_id"], candidate["candidate_id"], round_number=1,
		outcome="qualified", candidate_reply_summary="已确认继续推进",
	)
	workspace.record_private_contact(candidate["candidate_id"], channel="wechat", status="added", note="已在官方页面确认添加")
	closed = workspace.snapshot(job["job"]["job_id"])
	assert communication_task["target_stage"] == "private_domain_pending"
	assert closed["candidates"][0]["stage"] == "private_domain_added"
	assert any(task["kind"] == "prepare_interview" and task["target_stage"] == "interview_pending" and task["status"] == "pending" for task in closed["tasks"])


def test_snapshot_candidate_card_points_to_the_next_pending_task(tmp_path: Path) -> None:
	"""候选人卡片必须指向下一条待办，不能把已完成动作误显示成下一步。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人\n\n本科，3年销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	row = workspace.snapshot(job["job"]["job_id"])["candidates"][0]

	assert row["pending_task_kind"] == "confirm_basic"
	assert row["pending_task_title"] == "记录基础意向"
	assert row["pending_task_id"]
	assert row["last_task_title"] == "记录基础意向"


def test_workspace_answer_creates_reassessment_task(tmp_path: Path) -> None:
	"""记录专业回答后必须明确提示重新评估，避免回答保存后流程停在原报告。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	candidate = workspace.import_candidate(resume_path)

	workspace.record_answer(
		job["job"]["job_id"],
		candidate["candidate_id"],
		question="请举一个成交案例？",
		answer="我负责电话开发企业客户，处理异议并跟进成交。",
	)

	snapshot = workspace.snapshot(job["job"]["job_id"])
	assert any(task["kind"] == "reassess_candidate" and task["status"] == "pending" for task in snapshot["tasks"])
	assert snapshot["candidates"][0]["stage"] == "professional_qa"


def test_workspace_exposes_next_step_progress_for_an_end_to_end_handoff(tmp_path: Path) -> None:
	"""工作台快照必须告诉前端当前闭环卡在哪一步，而不是只返回一堆独立列表。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)

	initial = workspace.snapshot()
	assert initial["workflow"]["next_step"] == "create_job"
	assert initial["workflow"]["next_action"] == "先创建一个岗位标准"

	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path)
	ready_to_assess = workspace.snapshot(job["job"]["job_id"])
	assert ready_to_assess["workflow"]["next_step"] == "assess_candidate"
	assert ready_to_assess["workflow"]["pending_task_kind"] == "assess_candidate"
	assert ready_to_assess["workflow"]["pending_candidate_name"] == "赵六"
	assert ready_to_assess["workflow"]["pending_task_title"] == "选择岗位并生成简历评估"

	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	ready_to_review = workspace.snapshot(job["job"]["job_id"])
	assert ready_to_review["workflow"]["next_step"] == "confirm_basic"
	assert ready_to_review["workflow"]["pending_task_kind"] == "confirm_basic"

	workspace.review_assessment(job["job"]["job_id"], candidate["candidate_id"], outcome="reject")
	closed = workspace.snapshot(job["job"]["job_id"])
	assert closed["workflow"]["next_step"] == "closed"
	assert closed["workflow"]["next_action"] == "当前候选人已进入终局"


def test_workflow_exposes_candidate_queue_ordered_by_actionable_next_step(tmp_path: Path) -> None:
	"""多候选人场景必须提供按下一动作排序的队列，避免 HR 在长列表里猜顺序。"""
	first_resume = tmp_path / "甲.md"
	second_resume = tmp_path / "乙.md"
	first_resume.write_text("# 候选人\n\n姓名：甲\n有销售经验。", encoding="utf-8")
	second_resume.write_text("# 候选人\n\n姓名：乙\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	first = workspace.import_candidate(first_resume, job_id=job["job"]["job_id"])
	second = workspace.import_candidate(second_resume, job_id=job["job"]["job_id"])

	workspace.assess(job["job"]["job_id"], first["candidate_id"])
	workspace.assess(job["job"]["job_id"], second["candidate_id"])
	workspace.review_assessment(job["job"]["job_id"], second["candidate_id"], outcome="reject")

	snapshot = workspace.snapshot(job["job"]["job_id"])
	queue = snapshot["workflow"]["queue"]

	assert queue[0]["candidate_id"] == first["candidate_id"]
	assert queue[0]["pending_task_kind"] == "confirm_basic"
	assert queue[0]["next_action"] == "记录基础意向"
	assert queue[-1]["candidate_id"] == second["candidate_id"]
	assert queue[-1]["is_terminal"] is True
	assert "resume_path" not in str(queue)


def test_workflow_queue_exposes_explainable_priority_signals() -> None:
	"""候选人队列应按评估风险提示优先级，并只返回可解释的脱敏信号。"""
	from boss_agent_cli.recruiting.workflow import build_workflow_projection

	projection = build_workflow_projection(
		jobs=[{"job_id": "job-1", "status": "published"}],
		candidates=[
			{
				"candidate_id": "candidate-strong",
				"name": "高匹配候选人",
				"stage": "professional_qa",
				"stage_label": "专业问答中",
				"source": "local_markdown",
			},
			{
				"candidate_id": "candidate-risk",
				"name": "高风险候选人",
				"stage": "professional_qa",
				"stage_label": "专业问答中",
				"source": "local_markdown",
			},
		],
		tasks=[
			{
				"task_id": "task-strong",
				"candidate_id": "candidate-strong",
				"job_id": "job-1",
				"kind": "review_assessment",
				"title": "查看证据并完成人工确认",
				"status": "pending",
			},
			{
				"task_id": "task-risk",
				"candidate_id": "candidate-risk",
				"job_id": "job-1",
				"kind": "review_assessment",
				"title": "查看证据并完成人工确认",
				"status": "pending",
			},
		],
		assessments=[
			{
				"candidate_id": "candidate-strong",
				"final_score": 88,
				"screening": {"risk": {"level": "low"}, "hard_filter": {"status": "pass"}},
			},
			{
				"candidate_id": "candidate-risk",
				"final_score": 55,
				"screening": {"risk": {"level": "high"}, "hard_filter": {"status": "review"}},
			},
		],
		selected_job_id="job-1",
	)

	queue = projection["queue"]
	assert [row["candidate_id"] for row in queue] == ["candidate-risk", "candidate-strong"]
	strong = next(row for row in queue if row["candidate_id"] == "candidate-strong")
	risk = next(row for row in queue if row["candidate_id"] == "candidate-risk")
	assert strong["assessment_score"] == 88
	assert strong["risk_level"] == "low"
	assert strong["priority_label"] == "高匹配"
	assert any("综合评分较高" in reason for reason in strong["priority_reasons"])
	assert risk["assessment_score"] == 55
	assert risk["risk_level"] == "high"
	assert risk["priority_label"] == "需人工复核"
	assert any("风险等级高" in reason for reason in risk["priority_reasons"])
	assert "resume_path" not in str(queue)


def test_workflow_queue_points_to_skipped_task_for_recovery(tmp_path: Path) -> None:
	"""跳过待办后，候选人队列必须仍能定位到恢复入口，而不是回退到阶段下拉框。"""
	resume_path = tmp_path / "甲.md"
	resume_path.write_text("# 候选人\n\n姓名：甲\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", status="published")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"],
		candidate["candidate_id"],
		outcome="proceed",
		manual_override=True,
		override_reason="测试恢复路径",
	)
	pending = next(task for task in workspace.snapshot(job["job"]["job_id"])["tasks"] if task["status"] == "pending")

	workspace.complete_task(pending["task_id"], status="skipped", note="暂缓处理")

	queue_row = workspace.snapshot(job["job"]["job_id"])["workflow"]["queue"][0]

	assert queue_row["next_action"] == "恢复已跳过的待办"
	assert queue_row["focus_task_id"] == pending["task_id"]
	assert queue_row["focus_task_kind"] == "prepare_resume_exchange"


def test_workspace_blocks_proceed_when_professional_qa_needs_follow_up(tmp_path: Path) -> None:
	"""QA 低于门槛时不能直接把候选人推进到后续沟通。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	candidate = workspace.import_candidate(resume_path)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.record_answer(
		job["job"]["job_id"], candidate["candidate_id"], question="请举例", answer="还可以",
	)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	with pytest.raises(ValueError, match="专业问答低于 60 分"):
		workspace.review_assessment(job["job"]["job_id"], candidate["candidate_id"], outcome="proceed")


def test_workspace_blocks_proceed_until_all_review_gate_conditions_are_met(tmp_path: Path) -> None:
	"""综合分、基础意向和专业问答未完成时，默认推进必须停在人工补充步骤。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	with pytest.raises(ValueError, match="评估门禁未通过"):
		workspace.review_assessment(job["job"]["job_id"], candidate["candidate_id"], outcome="proceed")


def test_workspace_records_basic_intent_as_a_named_audit_action(tmp_path: Path) -> None:
	"""基础意向必须通过明确动作落成本地审计事件，而不是依赖阶段下拉猜测。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])

	result = workspace.confirm_basic_intent(
		job["job"]["job_id"], candidate["candidate_id"], note="已人工确认城市、薪资和工作节奏"
	)

	assert result["candidate"]["stage"] == "basic_passed"
	assert result["event"]["action"] == "基础意向人工确认"
	assert result["event"]["stage"] == "basic_passed"


def test_workspace_allows_explicit_manual_override_and_audits_reason(tmp_path: Path) -> None:
	"""业务例外必须显式强制继续并写入理由，不能静默绕过评估门禁。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	with pytest.raises(ValueError, match="强制继续理由"):
		workspace.review_assessment(
			job["job"]["job_id"], candidate["candidate_id"], outcome="proceed", manual_override=True,
		)

	reviewed = workspace.review_assessment(
		job["job"]["job_id"],
		candidate["candidate_id"],
		outcome="proceed",
		manual_override=True,
		override_reason="业务负责人已通过电话确认候选人意向，允许补充问答后推进。",
	)

	assert reviewed["manual_override"] is True
	assert reviewed["override_reason"].startswith("业务负责人")
	assert reviewed["review_gate"]["eligible"] is False


def test_workspace_does_not_call_an_active_candidate_closed_after_a_skipped_follow_up(tmp_path: Path) -> None:
	"""跳过外部动作后仍应提示复核，而不是把未终局候选人伪装成已闭环。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有电话销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")
	candidate = workspace.import_candidate(resume_path)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed",
		manual_override=True, override_reason="测试覆盖跳过待办后的状态投影。",
	)
	pending = next(task for task in workspace.snapshot(job["job"]["job_id"])["tasks"] if task["status"] == "pending")
	workspace.complete_task(pending["task_id"], status="skipped", note="暂不执行外部动作")

	skipped = workspace.snapshot(job["job"]["job_id"])
	assert skipped["workflow"]["next_step"] == "recover_task"
	assert skipped["workflow"]["next_action"] == "恢复已跳过的待办"


def test_workspace_exposes_funnel_conversion_source_and_stage_age(tmp_path: Path) -> None:
	"""漏斗应同时说明转化、来源和阶段停留，避免只看一组孤立数量。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	workspace.import_candidate(resume_path, source="boss_conversation")

	pipeline = workspace.snapshot(job["job"]["job_id"])["pipeline"]

	assert pipeline["funnel"][0]["stage"] == "pending_screening"
	assert pipeline["funnel"][0]["count"] == 1
	assert pipeline["funnel"][0]["share"] == 100
	assert pipeline["sources"] == [{"source": "boss_conversation", "label": "BOSS 沟通", "count": 1, "share": 100}]
	assert pipeline["funnel"][0]["avg_age_hours"] >= 0
	assert pipeline["conversion"]["screened_rate"] == 0


def test_workspace_exposes_local_interview_records_without_platform_actions(tmp_path: Path) -> None:
	"""工作区应暴露私域和面试记录接口，但不代替 HR 操作官方页面。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	candidate = workspace.import_candidate(resume_path)
	workspace.transition_candidate(candidate["candidate_id"], stage="private_domain_pending", action="人工确认继续沟通")

	contact = workspace.record_private_contact(
		candidate["candidate_id"], channel="wechat", status="added", note="已手动添加",
	)
	assert contact["candidate"]["stage"] == "private_domain_added"
	prepare = next(task for task in workspace.snapshot()["tasks"] if task["kind"] == "prepare_interview")
	workspace.complete_task(prepare["task_id"])

	invite = workspace.schedule_interview(
		job["job"]["job_id"], candidate["candidate_id"], scheduled_at="2026-08-03 14:00", interviewer="王主管",
	)
	assert invite["candidate"]["stage"] == "interview_scheduled"
	workspace.record_interview_result(job["job"]["job_id"], candidate["candidate_id"], outcome="passed", note="通过")

	snapshot = workspace.snapshot(job["job"]["job_id"])
	assert snapshot["private_contacts"][0]["channel"] == "wechat"
	assert snapshot["interviews"][0]["scheduled_at"] == "2026-08-03 14:00"
	assert snapshot["candidates"][0]["stage"] == "interview_completed"
	assert "候选人简历" not in str(snapshot)


def test_workspace_exposes_communication_timeline_and_next_follow_up(tmp_path: Path) -> None:
	"""工作区快照要把沟通轮次、回复摘要和待跟进时间交给前端。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("# 候选人简历\n\n姓名：赵六\n有销售经验。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问")
	candidate = workspace.import_candidate(resume_path)
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed",
		manual_override=True, override_reason="测试覆盖沟通时间线回填链路。",
	)
	resume_exchange = next(
		task for task in workspace.snapshot(job["job"]["job_id"])["tasks"]
		if task["kind"] == "prepare_resume_exchange" and task["status"] == "pending"
	)
	workspace.complete_task(resume_exchange["task_id"], note="已完成简历交换")
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])
	workspace.review_assessment(
		job["job"]["job_id"], candidate["candidate_id"], outcome="proceed",
		manual_override=True, override_reason="测试覆盖简历复评后的沟通时间线。",
	)

	workspace.record_communication(
		job["job"]["job_id"],
		candidate["candidate_id"],
		round_number=1,
		outcome="follow_up",
		candidate_reply_summary="需要确认薪资结构",
		next_follow_up_at="2026-08-04 10:00",
	)

	snapshot = workspace.snapshot(job["job"]["job_id"])
	row = snapshot["candidates"][0]
	assert row["communication_count"] == 1
	assert row["next_follow_up_at"] == "2026-08-04 10:00"
	assert row["communication_timeline"][0]["candidate_reply_summary"] == "需要确认薪资结构"
	assert snapshot["communications"][0]["round_number"] == 1
	assert any(task["communication_round"] == 2 and task["due_at"] == "2026-08-04 10:00" for task in snapshot["tasks"])


def test_workspace_exposes_job_readiness_and_three_layer_screening(tmp_path: Path) -> None:
	"""工作区快照应把岗位补充项和初筛三层结果一起交给前端。"""
	resume_path = tmp_path / "赵六.md"
	resume_path.write_text(
		"城市：杭州\n期望薪资：12-18K\n学历：本科\n工作经验：3年\n"
		"技能：电话邀约\n做过电话邀约和客户开发。",
		encoding="utf-8",
	)
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="销售顾问", criteria_text="必须有电话销售经验")

	assert job["readiness"]["ready"] is False
	assert "city" in job["readiness"]["missing_required_fields"]
	candidate = workspace.import_candidate(resume_path)
	report = workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	snapshot = workspace.snapshot(job["job"]["job_id"])
	assert snapshot["jobs"][0]["readiness"]["ready"] is False
	assert report["screening"]["semantic_match"]["matched"] == ["电话销售经验"]
	assert snapshot["assessments"][0]["screening"]["hard_filter"]["status"] == "pass"
	assert "城市：杭州" not in str(snapshot)
