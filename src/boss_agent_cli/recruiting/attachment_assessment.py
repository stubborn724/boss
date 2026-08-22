"""附件简历到正式岗位评估的适配边界。

附件下载属于 BOSS RPA，岗位评分属于招聘领域。该模块只负责把下载后的简历文本
交给正式评分器，并将可解释报告投影成现有流水线展示模型，避免 RPA 流水线继续
维护一套由大模型直接给总分的岗位无关算法。
"""

from __future__ import annotations

from dataclasses import dataclass

from boss_agent_cli.commands.recruiter.resume_analysis import ResumeAnalysisResult
from boss_agent_cli.recruiting.ai_review import AIResumeReview
from boss_agent_cli.recruiting.assessment import score_candidate
from boss_agent_cli.recruiting.job_context import JobAutomationContext
from boss_agent_cli.recruiting.models import AssessmentReport


@dataclass(frozen=True, slots=True)
class AttachmentAssessmentResult:
	"""附件终审结果，保留岗位和配置版本供后续审计。"""

	job_id: str
	config_version: str
	analysis: ResumeAnalysisResult
	report: AssessmentReport


def _recommendation(score: int, *, threshold: int, has_high_risk: bool) -> str:
	"""将正式评分结论映射到旧流水线枚举，不绕过人工复核门禁。"""
	if score >= threshold and not has_high_risk:
		return "invite_to_interview"
	if score >= 60:
		return "review"
	return "reject"


def assess_attachment_resume(
	context: JobAutomationContext,
	*,
	candidate_name: str,
	friend_id: int,
	resume_text: str,
	ai_review: AIResumeReview | None = None,
) -> AttachmentAssessmentResult:
	"""按岗位上下文执行正式评分并生成现有页面可消费的分析摘要。"""
	report = score_candidate(
		context.job,
		candidate_id=f"boss-friend-{friend_id}",
		candidate_name=candidate_name,
		resume_text=resume_text,
		ai_review=ai_review,
	)
	hard_filter = report.screening.get("hard_filter", {})
	risk = report.screening.get("risk", {})
	high_risk = isinstance(risk, dict) and risk.get("level") == "high"
	matched = "、".join(report.matched_points) or "未找到明确的岗位技能命中"
	required = "、".join(context.all_skills) or "未配置结构化技能"
	gaps = tuple(
		line.removeprefix("简历未找到明确证据：")
		for line in report.evidence
		if line.startswith("简历未找到明确证据：")
	)
	analysis = ResumeAnalysisResult(
		overall_score=report.final_score,
		skill_match=f"岗位 {context.job_name} 要求：{required}；已命中：{matched}",
		experience_assessment="；".join(report.evidence[-3:]) if report.evidence else "缺少可核验经历证据",
		strengths=tuple(report.matched_points),
		gaps=gaps,
		risk_flags=tuple(report.risk_points),
		follow_up_questions=tuple(report.professional_questions),
		recommendation=_recommendation(
			report.final_score,
			threshold=context.job.recommendation_threshold,
			has_high_risk=high_risk or (isinstance(hard_filter, dict) and hard_filter.get("status") == "fail"),
		),
		raw_response="",
		source="formal_job_assessment",
	)
	return AttachmentAssessmentResult(
		job_id=context.job_id,
		config_version=context.version,
		analysis=analysis,
		report=report,
	)


__all__ = ["AttachmentAssessmentResult", "assess_attachment_resume"]
