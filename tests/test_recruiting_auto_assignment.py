"""本地简历自动分析与岗位分配的行为契约测试。"""

from pathlib import Path
import json

from boss_agent_cli.recruiting.models import RecruitingCriteria
from boss_agent_cli.recruiting.store import RecruitingStore


def _write_resume(directory: Path, filename: str, body: str) -> Path:
	"""写入一份文字简历夹具，避免测试依赖真实桌面目录或 PDF 二进制。"""
	directory.mkdir(parents=True, exist_ok=True)
	path = directory / filename
	path.write_text(body, encoding="utf-8")
	return path


def test_auto_assignment_binds_resume_to_highest_eligible_job_and_saves_report(tmp_path: Path) -> None:
	"""完整岗位标准应按岗位匹配证据自动归组，并把评估保存到对应岗位。"""
	from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService

	store = RecruitingStore(tmp_path / "workspace")
	java_job = store.create_job(
		name="Java 开发工程师",
		city="杭州",
		salary_range="15-25K",
		education_requirement="本科",
		min_experience_years=3,
		skills=["Java", "Spring Boot"],
		criteria=RecruitingCriteria(must_have=["Java 开发经验"], nice_to_have=["微服务"]),
	)
	store.create_job(
		name="售后技术支持",
		city="杭州",
		salary_range="10-15K",
		education_requirement="大专",
		min_experience_years=1,
		skills=["客户支持"],
		criteria=RecruitingCriteria(must_have=["售后技术支持经验"]),
	)
	resume = _write_resume(
		tmp_path / "resumes",
		"张三.md",
		"姓名：张三\n城市：杭州\n期望薪资：20K\n学历：本科\n工作经验：4年\n"
		"技能：Java、Spring Boot、微服务\n有四年 Java 开发经验，负责后端服务开发。",
	)

	result = AutoResumeAssignmentService(store).scan_and_assign(resume.parent)

	assert result["scanned"] == 1
	assert result["auto_assigned"] == 1
	item = result["items"][0]
	assert item["resume_path"] == str(resume.resolve())
	assert item["assigned_job_id"] == java_job.job_id
	assert item["assignment_status"] == "auto_assigned"
	assert item["job_scores"][0]["job_id"] == java_job.job_id
	assert item["job_scores"][0]["assignment_score"] > item["job_scores"][1]["assignment_score"]
	candidate_id = item["candidate_id"]
	assert store.list_candidate_job_ids(candidate_id) == [java_job.job_id]
	report = store.get_assessment(java_job.job_id, candidate_id)
	assert report is not None
	assert report["final_score"] >= 80
	assert report["auto_assignment"]["status"] == "auto_assigned"


def test_auto_assignment_uses_title_match_provisionally_for_unconfigured_boss_job(tmp_path: Path) -> None:
	"""只有职位名的 BOSS 镜像可临时匹配，但必须标记岗位标准尚待完善。"""
	from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService

	store = RecruitingStore(tmp_path / "workspace")
	java_job = store.create_job(name="Java", status="draft")
	resume = _write_resume(
		tmp_path / "resumes",
		"李四.md",
		"姓名：李四\n工作经验：3年\n技能：Java、Spring Boot\n从事 Java 后端开发和接口设计。",
	)

	result = AutoResumeAssignmentService(store).scan_and_assign(resume.parent)

	item = result["items"][0]
	assert item["assigned_job_id"] == java_job.job_id
	assert item["assignment_status"] == "auto_assigned"
	assert item["job_scores"][0]["score_basis"] == "title_provisional"
	assert item["job_scores"][0]["job_standard_ready"] is False
	report = store.get_assessment(java_job.job_id, item["candidate_id"])
	assert report is not None
	assert report["auto_assignment"]["job_standard_ready"] is False
	assert "待完善岗位标准" in report["auto_assignment"]["note"]


def test_auto_assignment_excludes_closed_jobs_and_keeps_unmatched_resume_unassigned(tmp_path: Path) -> None:
	"""关闭岗位不得承接新简历，未达到门槛的简历也不能被强行分配。"""
	from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService

	store = RecruitingStore(tmp_path / "workspace")
	closed_job = store.create_job(name="售后技术支持", status="draft")
	closed_job.platform_sync_status = "closed"
	store.update_job(closed_job)
	_write_resume(tmp_path / "resumes", "王五.md", "姓名：王五\n工作经验：2年\n技能：平面设计\n负责海报设计。")

	result = AutoResumeAssignmentService(store).scan_and_assign(tmp_path / "resumes")

	item = result["items"][0]
	assert result["auto_assigned"] == 0
	assert item["assignment_status"] == "unassigned"
	assert item["assigned_job_id"] == ""
	assert item["job_scores"] == []
	assert store.list_candidate_job_ids(item["candidate_id"]) == []


def test_auto_assignment_is_idempotent_for_the_same_local_resume(tmp_path: Path) -> None:
	"""重复扫描同一路径只刷新同一候选人和同一份岗位报告，不制造重复记录。"""
	from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService

	store = RecruitingStore(tmp_path / "workspace")
	job = store.create_job(name="Java", status="draft")
	_write_resume(tmp_path / "resumes", "赵六.md", "姓名：赵六\n技能：Java\n有 Java 后端开发经验。")
	service = AutoResumeAssignmentService(store)

	first = service.scan_and_assign(tmp_path / "resumes")
	second = service.scan_and_assign(tmp_path / "resumes")

	assert first["items"][0]["candidate_id"] == second["items"][0]["candidate_id"]
	assert len(store.list_candidates()) == 1
	assert store.list_candidate_job_ids(first["items"][0]["candidate_id"]) == [job.job_id]


def test_auto_assignment_uses_existing_local_analysis_name_mapping(tmp_path: Path) -> None:
	"""已有本地分析索引能把 PDF 文件名恢复为候选人姓名，列表不显示内部文件名。"""
	from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService

	store_root = tmp_path / "workspace"
	store = RecruitingStore(store_root)
	store.create_job(name="Java", status="draft")
	analysis_dir = store_root / "recruiter"
	analysis_dir.mkdir(parents=True)
	(analysis_dir / "analyzed.json").write_text(
		json.dumps({"123": {"name": "张三"}}, ensure_ascii=False), encoding="utf-8",
	)
	_write_resume(tmp_path / "resumes", "resume_123.md", "姓名：候选人\n技能：Java")

	result = AutoResumeAssignmentService(store).scan_and_assign(tmp_path / "resumes")

	assert result["items"][0]["candidate_id"]
	assert store.get_candidate(result["items"][0]["candidate_id"]).name == "张三"
