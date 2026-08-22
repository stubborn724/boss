"""BOSS 职位只读镜像的领域服务测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from boss_agent_cli.recruiting.models import RecruitingCriteria
from boss_agent_cli.recruiting.platform_job_sync import PlatformJobSyncService
from boss_agent_cli.recruiting.store import RecruitingStore
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


def test_sync_reuses_platform_job_and_keeps_hr_criteria(tmp_path: Path) -> None:
	"""重复同步只更新平台快照，绝不覆盖 HR 已确认的筛选标准。"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	first = service.sync([{"job_id": "boss-java", "name": "Java 开发工程师"}])
	local_job_id = first["jobs"][0]["job_id"]
	current = store.get_job(local_job_id)
	assert current is not None
	store.update_job(replace(current, criteria=RecruitingCriteria(must_have=["Spring Boot"])))

	second = service.sync([{"job_id": "boss-java", "name": "高级 Java 开发工程师"}])
	reloaded = store.get_job(local_job_id)

	assert second["created"] == 0
	assert second["updated"] == 1
	assert reloaded is not None
	assert reloaded.name == "高级 Java 开发工程师"
	assert reloaded.criteria.must_have == ["Spring Boot"]
	assert reloaded.source == "boss"
	assert reloaded.platform_job_id == "boss-java"


def test_sync_writes_boss_hard_fields_without_overwriting_reviewed_rules(tmp_path: Path) -> None:
	"""BOSS 同步详情进入岗位基础字段，HR 审核过的四类规则必须原样保留。"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	first = service.sync([{
		"job_id": "boss-java",
		"name": "Java",
		"city": "广州",
		"salary_range": "150-200元/天",
		"education_requirement": "本科",
		"description": "负责软件系统开发、维护与功能迭代。",
		"keywords": "Java、Spring、MySQL",
		"experience_requirement": "在校/应届",
		"internship_requirement": "4个月",
		"work_days": "每周5天",
		"work_address": "天河区创新创业孵化基地",
	}])
	job_id = first["jobs"][0]["job_id"]
	job = store.get_job(job_id)
	assert job is not None
	job.criteria = RecruitingCriteria(must_have=["掌握 Spring"])
	store.update_job(job)

	service.sync([{
		"job_id": "boss-java",
		"name": "Java",
		"city": "广州",
		"salary_range": "150-200元/天",
		"education_requirement": "本科",
		"description": "负责软件系统开发、维护与功能迭代。",
		"keywords": "Java、Spring、MySQL",
	}])
	reloaded = store.get_job(job_id)

	assert reloaded is not None
	assert reloaded.city == "广州"
	assert reloaded.salary_range == "150-200元/天"
	assert reloaded.education_requirement == "本科"
	assert reloaded.skills == ["Java", "Spring", "MySQL"]
	assert reloaded.platform_snapshot["description"] == "负责软件系统开发、维护与功能迭代。"
	assert reloaded.platform_snapshot["experience_requirement"] == "在校/应届"
	assert reloaded.platform_snapshot["internship_requirement"] == "4个月"
	assert reloaded.platform_snapshot["work_days"] == "每周5天"
	assert reloaded.platform_snapshot["work_address"] == "天河区创新创业孵化基地"
	assert reloaded.criteria.must_have == ["掌握 Spring"]


def test_sync_marks_missing_platform_job_as_not_discovered_without_deleting(tmp_path: Path) -> None:
	"""平台列表短暂缺项只能保留历史镜像并标记状态，不能删除本地审计记录。"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	created = service.sync([{"job_id": "boss-sales", "name": "销售顾问"}])

	result = service.sync([])
	job = store.get_job(created["jobs"][0]["job_id"])

	assert result["not_discovered"] == 1
	assert job is not None
	assert job.platform_sync_status == "not_discovered"


def test_workspace_hides_undiscovered_boss_jobs_but_keeps_manual_jobs(tmp_path: Path) -> None:
	"""页面只展示当前 BOSS 岗位，历史镜像仍留在 Store 供审计追溯。

	同步期间不能物理删除已有关联数据的 BOSS 岗位；但它已不在职位管理页时，
	继续作为可选岗位展示会误导招聘人员把候选人归到错误职位。
	"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	service.sync([{"job_id": "boss-obsolete", "name": "历史岗位"}])
	store.create_job(name="手工维护岗位", status="draft")
	service.sync([{"job_id": "boss-current", "name": "当前 BOSS 岗位"}])

	snapshot = RecruitingWorkspace(tmp_path).snapshot()
	visible_names = {job["name"] for job in snapshot["jobs"]}

	assert visible_names == {"当前 BOSS 岗位", "手工维护岗位"}
	assert snapshot["selected_job_id"] == snapshot["jobs"][0]["job_id"]
	assert snapshot["jobs"][0]["name"] == "当前 BOSS 岗位"


def test_workspace_hides_archived_manual_job_but_keeps_its_history(tmp_path: Path) -> None:
	"""不再使用的本地历史岗位应退出选择器，避免被误当作 BOSS 同步岗位。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="电话销售顾问", status="published")
	job_id = created["job"]["job_id"]
	workspace.archive_job(job_id)

	snapshot = workspace.snapshot()

	assert snapshot["jobs"] == []
	assert workspace.store.get_job(job_id) is not None


def test_workspace_keeps_undiscovered_boss_job_when_it_has_saved_assessment(tmp_path: Path) -> None:
	"""已关闭的 BOSS 岗位仍须保留已完成的候选人分析和岗位关联。

	职位是否仍在 BOSS 开放列表中，只决定是否可以继续作为当前招聘岗位使用；
	一旦本地已保存评估报告，隐藏岗位会让历史候选人无法查看原有分数和证据，
	因此必须在工作台与评分看板中继续可选。
	"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	created = service.sync([{"job_id": "boss-support", "name": "售后技术支持"}])
	historical_job_id = created["jobs"][0]["job_id"]
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("姓名：张三\n工作经验：3年", encoding="utf-8")
	candidate = store.import_candidate(resume_path, job_id=historical_job_id)
	store.save_assessment(historical_job_id, candidate.candidate_id, {"final_score": 82, "level": "推荐"})
	service.sync([{"job_id": "boss-current", "name": "当前 BOSS 岗位"}])

	snapshot = RecruitingWorkspace(tmp_path).snapshot(historical_job_id)

	assert {job["name"] for job in snapshot["jobs"]} == {"售后技术支持", "当前 BOSS 岗位"}
	assert [candidate["name"] for candidate in snapshot["candidates"]] == ["张三"]
	assert snapshot["assessments"][0]["final_score"] == 82


def test_sync_keeps_closed_boss_job_visible_for_future_reopening(tmp_path: Path) -> None:
	"""BOSS 明确关闭的职位仍应显示为历史岗位，不应被当作读取失败。"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	result = service.sync([{"job_id": "boss-support", "name": "售后技术支持", "status": "closed"}])
	job_id = result["jobs"][0]["job_id"]

	snapshot = RecruitingWorkspace(tmp_path).snapshot(job_id)

	assert snapshot["jobs"][0]["name"] == "售后技术支持"
	assert snapshot["jobs"][0]["platform_sync_status"] == "closed"


def test_sync_reconciles_same_named_synthetic_rpa_job_without_creating_duplicate(tmp_path: Path) -> None:
	"""RPA 未返回真实职位 ID 时，同名镜像应继续指向原岗位与历史评估。"""
	store = RecruitingStore(tmp_path)
	service = PlatformJobSyncService(store)
	first = service.sync([{"job_id": "rpa-legacy-java", "name": "Java"}])
	local_job_id = first["jobs"][0]["job_id"]
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text("姓名：张三\nJava 开发经验：3 年", encoding="utf-8")
	candidate = store.import_candidate(resume_path, job_id=local_job_id)
	store.save_assessment(local_job_id, candidate.candidate_id, {"final_score": 82, "level": "推荐"})

	result = service.sync([{"job_id": "rpa-current-java", "name": "Java", "status": "active"}])
	reloaded = store.get_job(local_job_id)

	assert result["created"] == 0
	assert result["updated"] == 1
	assert result["jobs"] == [{"job_id": local_job_id, "name": "Java", "platform_job_id": "rpa-current-java"}]
	assert reloaded is not None
	assert reloaded.platform_job_id == "rpa-current-java"
	assert store.has_assessments_for_job(local_job_id) is True
