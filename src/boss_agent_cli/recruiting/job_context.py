"""岗位专属自动化上下文。

本模块是自动沟通和简历终审读取岗位配置的唯一边界。BOSS 平台快照负责提供
基础事实，本地岗位字段负责提供人工确认的补充规则；调用方不得再自行拼接标准
或在缺失配置时回退到“通用岗位”。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from boss_agent_cli.recruiting.models import JobProfile, RecruitingCriteria


class JobContextError(ValueError):
	"""岗位上下文无法用于自动化时返回的稳定领域错误。"""


@dataclass(frozen=True, slots=True)
class BossJobRequirements:
	"""从 BOSS 白名单快照读取的只读基础条件。"""

	city: str
	salary_range: str
	education_requirement: str
	skills: tuple[str, ...]
	description: str = ""


@dataclass(frozen=True, slots=True)
class JobAutomationContext:
	"""一次自动化步骤使用的完整、可审计岗位配置。"""

	job_id: str
	platform_job_id: str
	job_name: str
	version: str
	boss_requirements: BossJobRequirements
	local_criteria: RecruitingCriteria
	all_skills: tuple[str, ...]
	job: JobProfile


def _snapshot_value(job: JobProfile, key: str, fallback: str = "") -> str:
	"""优先读取 BOSS 快照，缺失时兼容旧岗位的结构化基础字段。"""
	return str(job.platform_snapshot.get(key) or fallback).strip()


def resolve_job_context(job: JobProfile, *, require_confirmed: bool = True) -> JobAutomationContext:
	"""合并 BOSS 基础事实与本地规则，并拒绝未确认配置。

	BOSS 基础字段拥有事实优先级，本地自然语言规则只补充四类筛选标准和评分
	参数。这里不修改传入岗位，确保一个步骤开始后持有的上下文不会被页面编辑
	过程中的原地更新影响。
	"""
	if not job.job_id.strip():
		raise JobContextError("岗位标识为空，无法启动自动化")
	if require_confirmed and not job.rules_confirmed:
		raise JobContextError("岗位规则尚未确认，请先在岗位管理中审核并启用")

	raw_keywords = _snapshot_value(job, "keywords")
	platform_skills = tuple(
		dict.fromkeys(
			item.strip()
			for item in raw_keywords.replace("，", ",").replace("、", ",").replace("/", ",").split(",")
			if item.strip()
		)
	)
	all_skills = tuple(dict.fromkeys((*platform_skills, *(item.strip() for item in job.skills if item.strip()))))
	boss = BossJobRequirements(
		city=_snapshot_value(job, "city", job.city),
		salary_range=_snapshot_value(job, "salary_range", job.salary_range),
		education_requirement=_snapshot_value(job, "education_requirement", job.education_requirement),
		skills=all_skills,
		description=_snapshot_value(job, "description"),
	)
	# 正式评分器继续接收成熟的 JobProfile 接口，但必须使用本步骤的合并快照，
	# 不能把平台事实只放在展示对象里。复制可变字段也防止页面保存时原地修改
	# 正在执行步骤持有的标准。
	effective_job = replace(
		job,
		city=boss.city,
		salary_range=boss.salary_range,
		education_requirement=boss.education_requirement,
		skills=list(all_skills),
		criteria=RecruitingCriteria(
			must_have=list(job.criteria.must_have),
			nice_to_have=list(job.criteria.nice_to_have),
			reject_if=list(job.criteria.reject_if),
			risk_signals=list(job.criteria.risk_signals),
		),
		weights=dict(job.weights),
		platform_snapshot=dict(job.platform_snapshot),
	)
	return JobAutomationContext(
		job_id=job.job_id,
		platform_job_id=job.platform_job_id,
		job_name=job.name,
		version=job.rules_version or "v1",
		boss_requirements=boss,
		local_criteria=job.criteria,
		all_skills=all_skills,
		job=effective_job,
	)


__all__ = ["BossJobRequirements", "JobAutomationContext", "JobContextError", "resolve_job_context"]
