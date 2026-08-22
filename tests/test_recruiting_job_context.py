"""岗位专属自动化上下文的行为契约。"""

from pathlib import Path

import pytest

from boss_agent_cli.recruiting.job_context import JobContextError, resolve_job_context
from boss_agent_cli.recruiting.models import JobProfile, RecruitingCriteria
from boss_agent_cli.recruiting.store import RecruitingStore
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


def _job(tmp_path: Path, *, job_id: str, name: str, education: str, skills: list[str]) -> JobProfile:
	store = RecruitingStore(tmp_path / job_id)
	job = store.create_job(
		name=name,
		city="广州",
		salary_range="150-200元/天",
		education_requirement=education,
		skills=skills,
		criteria=RecruitingCriteria(must_have=["能吃苦"]),
	)
	job.job_id = job_id
	job.source = "boss"
	job.platform_job_id = f"boss-{job_id}"
	job.platform_snapshot = {
		"name": name,
		"education_requirement": education,
		"keywords": ",".join(skills),
	}
	return job


def test_context_keeps_boss_hard_conditions_and_local_rules_separate(tmp_path: Path) -> None:
	"""BOSS 学历和技能是平台事实，本地自然语言规则只能补充，不能覆盖。"""
	java = _job(tmp_path, job_id="java", name="Java", education="本科", skills=["Java", "Spring"])
	java.criteria = RecruitingCriteria(must_have=["能吃苦"], nice_to_have=["有微服务经验"])

	context = resolve_job_context(java)

	assert context.job_id == "java"
	assert context.boss_requirements.education_requirement == "本科"
	assert context.boss_requirements.skills == ("Java", "Spring")
	assert context.local_criteria.nice_to_have == ["有微服务经验"]
	assert context.all_skills == ("Java", "Spring")
	assert context.job.education_requirement == "本科"
	assert context.job.skills == ["Java", "Spring"]


def test_context_rejects_unconfirmed_rules_for_automation(tmp_path: Path) -> None:
	"""未确认的岗位规则不能被自动沟通或附件分析使用。"""
	job = _job(tmp_path, job_id="support", name="售后技术支持", education="本科", skills=[])
	job.rules_confirmed = False

	with pytest.raises(JobContextError, match="未确认"):
		resolve_job_context(job, require_confirmed=True)


def test_context_isolated_by_job_and_version(tmp_path: Path) -> None:
	"""两个岗位的上下文和配置版本必须独立。"""
	java = _job(tmp_path, job_id="java", name="Java", education="本科", skills=["Java"])
	support = _job(tmp_path, job_id="support", name="售后技术支持", education="大专", skills=["故障排查"])
	java.rules_version = "v-java-2"
	support.rules_version = "v-support-1"

	java_context = resolve_job_context(java)
	support_context = resolve_job_context(support)

	assert java_context.version == "v-java-2"
	assert support_context.version == "v-support-1"
	assert java_context.job_id != support_context.job_id
	assert java_context.boss_requirements.education_requirement != support_context.boss_requirements.education_requirement


def test_reviewed_natural_language_rules_confirm_and_increment_version(tmp_path: Path) -> None:
	"""现有规则审核保存入口应确认配置并生成新版本。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="Java", status="draft")
	job_id = created["job"]["job_id"]
	job = workspace.store.get_job(job_id)
	assert job is not None
	job.rules_confirmed = False
	workspace.store.update_job(job)

	result = workspace.update_job_rules(job_id, criteria_text="必须熟悉 Java；Spring 经验优先")

	assert result["job"]["rules_confirmed"] is True
	assert result["job"]["rules_version"] == "v2"
	assert result["job"]["rules_confirmed_at"]


def test_reviewed_rules_persist_job_specific_scoring_configuration(tmp_path: Path) -> None:
	"""岗位规则确认时应把独立权重和阈值持久化，重新加载后仍保持不变。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="Java", status="draft")
	job_id = created["job"]["job_id"]
	weights = {
		"hard_match": 30,
		"experience": 15,
		"professional_qa": 30,
		"communication": 10,
		"stability": 10,
		"location_salary": 5,
	}

	result = workspace.update_job_rules(
		job_id,
		criteria_text="必须熟悉 Java；Spring 经验优先",
		weights=weights,
		screening_threshold=75,
		recommendation_threshold=85,
		professional_qa_threshold=65,
	)
	reloaded = RecruitingWorkspace(tmp_path).store.get_job(job_id)

	assert result["job"]["weights"] == weights
	assert result["job"]["screening_threshold"] == 75
	assert result["job"]["recommendation_threshold"] == 85
	assert result["job"]["professional_qa_threshold"] == 65
	assert reloaded is not None
	assert reloaded.weights == weights
	assert reloaded.screening_threshold == 75
	assert reloaded.recommendation_threshold == 85
	assert reloaded.professional_qa_threshold == 65
