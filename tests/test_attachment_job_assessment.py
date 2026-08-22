"""附件终审必须使用候选人所属岗位的正式评分标准。"""

import pytest

from boss_agent_cli.recruiting.attachment_assessment import assess_attachment_resume
from boss_agent_cli.recruiting.job_context import JobContextError, resolve_job_context
from boss_agent_cli.recruiting.models import JobProfile, RecruitingCriteria


_JAVA_RESUME = """本科，5年工作经验。负责 Java、Spring、MySQL 微服务项目开发。"""


def _job(*, job_id: str, name: str, skills: list[str], must_have: list[str]) -> JobProfile:
	return JobProfile(
		job_id=job_id,
		name=name,
		city="广州",
		education_requirement="本科",
		min_experience_years=2,
		skills=skills,
		criteria=RecruitingCriteria(must_have=must_have),
		rules_confirmed=True,
		rules_version=f"v-{job_id}",
	)


def test_same_resume_uses_each_jobs_independent_standard() -> None:
	"""同一份简历在 Java 和售后岗位下应产生不同岗位证据与分数。"""
	java = resolve_job_context(_job(job_id="java", name="Java", skills=["Java", "Spring"], must_have=["MySQL"]))
	support = resolve_job_context(_job(job_id="support", name="售后技术支持", skills=["客户沟通", "故障排查"], must_have=["工单处理"]))

	java_result = assess_attachment_resume(java, candidate_name="张三", friend_id=42, resume_text=_JAVA_RESUME)
	support_result = assess_attachment_resume(support, candidate_name="张三", friend_id=42, resume_text=_JAVA_RESUME)

	assert java_result.job_id == "java"
	assert support_result.job_id == "support"
	assert java_result.config_version == "v-java"
	assert java_result.analysis.overall_score > support_result.analysis.overall_score
	assert "Java" in java_result.analysis.skill_match
	assert "故障排查" in support_result.analysis.skill_match


def test_unconfirmed_job_cannot_be_used_for_attachment_assessment() -> None:
	"""附件终审不能在岗位规则未确认时回退到通用评分。"""
	job = _job(job_id="java", name="Java", skills=["Java"], must_have=[])
	job.rules_confirmed = False

	with pytest.raises(JobContextError, match="未确认"):
		resolve_job_context(job)


def test_recommendation_threshold_is_job_specific() -> None:
	"""相同分数应按各岗位自己的推荐阈值决定推进建议。"""
	job = _job(job_id="java", name="Java", skills=["Java"], must_have=[])
	job.recommendation_threshold = 95

	result = assess_attachment_resume(resolve_job_context(job), candidate_name="张三", friend_id=42, resume_text=_JAVA_RESUME)

	assert result.analysis.overall_score < 95
	assert result.analysis.recommendation != "invite_to_interview"
