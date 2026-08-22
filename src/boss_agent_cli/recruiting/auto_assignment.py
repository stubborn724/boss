"""本地简历的自动评分与岗位分配服务。

本模块负责把“扫描用户明确选择的目录”这一批量操作与既有单份简历评估解耦。
它只访问本地文件和 :class:`RecruitingStore`，不会触碰 BOSS 页面、更不会发出候选人
沟通消息。岗位标准完整时复用正式评分器；BOSS 同步后仅有职位名称时，使用可解释的
职位名相关度作为临时分配依据，并在报告中明确标注，防止把猜测伪装成岗位事实。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import json
from collections.abc import Callable
from typing import Any

from boss_agent_cli.recruiting.assessment import evaluate_job_readiness, score_candidate
from boss_agent_cli.recruiting.ai_review import AIResumeReview
from boss_agent_cli.recruiting.models import AssessmentReport, JobProfile, RecruitingCriteria
from boss_agent_cli.recruiting.resume_text_reader import SUPPORTED_RESUME_SUFFIXES, ResumeTextReadError, read_resume_text
from boss_agent_cli.recruiting.store import RecruitingStore


# 自动分配用于“先把本地已有简历归到合适岗位”的低风险入口。阈值刻意高于
# 不推荐分组上限，且要求明显领先次优岗位，避免两个岗位都沾边时擅自归属。
_AUTO_ASSIGN_MIN_SCORE = 60
_AUTO_ASSIGN_MIN_MARGIN = 10
_TITLE_STOP_WORDS = frozenset({"工程师", "开发", "顾问", "岗位", "技术", "支持", "售后", "高级", "初级", "专员"})
_TITLE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*|[\u4e00-\u9fff]{2,}")


def _score_level(score: int) -> str:
	"""按需求文档的固定区间返回评分标签，避免临时分数使用另一套分组语义。"""
	if score >= 90:
		return "强烈推荐"
	if score >= 80:
		return "推荐"
	if score >= 70:
		return "待确认"
	if score >= 60:
		return "人工复核"
	return "不推荐"


def _normalise(value: str) -> str:
	"""清除空白与英文大小写差异，供职位名和简历文字做稳定包含匹配。"""
	return re.sub(r"\s+", "", value.casefold())


def _title_terms(job_name: str) -> list[str]:
	"""提取职位名中的业务锚点，排除无法区分岗位方向的泛化职称词。"""
	terms: list[str] = []
	for raw in _TITLE_TOKEN_RE.findall(job_name or ""):
		clean = raw.strip()
		if not clean or clean in _TITLE_STOP_WORDS:
			continue
		if clean not in terms:
			terms.append(clean)
	return terms or ([job_name.strip()] if job_name.strip() else [])


def _eligible_jobs(store: RecruitingStore) -> list[JobProfile]:
	"""返回可接收新简历的岗位，关闭、归档和平台未发现的岗位只保留历史。"""
	return [
		job
		for job in store.list_jobs()
		if job.status != "archived" and job.platform_sync_status not in {"closed", "not_discovered"}
	]


def _provisional_report(job: JobProfile, *, candidate_id: str, candidate_name: str, resume_text: str) -> AssessmentReport:
	"""对未完整配置的岗位生成临时的职位名匹配报告。

	临时模式不补写城市、薪资和学历等平台未提供的数据。评分只衡量候选人是否在
	简历中明确出现职位名中的业务锚点，最高限制在 79 分，因此不会被误当作已完成
	岗位标准与专业问答后的正式推荐结论。
	"""
	terms = _title_terms(job.name)
	matched = [term for term in terms if _normalise(term) in _normalise(resume_text)]
	match_ratio = len(matched) / len(terms) if terms else 0.0
	# 40 分是不命中时的保守起点；完全命中为 78 分，刚好进入“待确认”，仍须
	# 补齐岗位标准并人工复核。多项职位名锚点按比例提升，避免只命中一个泛词。
	score = min(79, round(40 + match_ratio * 38))
	provisional_job = replace(
		job,
		criteria=RecruitingCriteria(must_have=list(terms)),
		skills=list(terms),
	)
	report = score_candidate(
		provisional_job,
		candidate_id=candidate_id,
		candidate_name=candidate_name,
		resume_text=resume_text,
	)
	report.final_score = score
	report.level = _score_level(score)
	report.decision = "待完善岗位标准"
	report.next_action = "补齐岗位标准后重新生成正式评估"
	report.evidence = [
		"临时职位名匹配：" + ("、".join(matched) if matched else "未找到职位名业务锚点"),
		"岗位标准未完整，当前分数仅用于自动分配和人工确认，不用于录用决策。",
		*report.evidence,
	]
	return report


class AutoResumeAssignmentService:
	"""扫描本地目录并将简历按最高匹配分归入现有岗位。

	服务直接依赖 Store 是为了让目录批处理保持为一个清晰的本地领域能力；Web 和
	CLI 都可经由 Workspace 调用它，而不需要复制目录规则、阈值或关闭岗位边界。
	"""

	def __init__(
		self,
		store: RecruitingStore,
		*,
		ai_reviewer: Callable[[JobProfile, str], AIResumeReview | None] | None = None,
	) -> None:
		"""保存存储与可选 AI 评审器；未配置 AI 时保持确定性规则评分。"""
		self._store = store
		self._ai_reviewer = ai_reviewer

	def scan_and_assign(self, directory: Path | str) -> dict[str, Any]:
		"""扫描目录一级文件，分别评分并保存明确满足自动条件的最佳岗位结果。"""
		root = Path(directory).expanduser().resolve()
		if not root.is_dir():
			raise ValueError("本地简历目录不存在或不可访问")
		jobs = _eligible_jobs(self._store)
		paths = sorted(
			(path for path in root.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_RESUME_SUFFIXES),
			key=lambda path: path.name.casefold(),
		)
		items = [self._assign_one(path, jobs) for path in paths]
		return {
			"directory": str(root),
			"scanned": len(paths),
			"auto_assigned": sum(1 for item in items if item["assignment_status"] == "auto_assigned"),
			"unassigned": sum(1 for item in items if item["assignment_status"] == "unassigned"),
			"failed": sum(1 for item in items if item["assignment_status"] == "failed"),
			"eligible_jobs": len(jobs),
			"items": items,
		}

	def _name_hint(self, path: Path) -> str:
		"""从既有本地分析索引读取同名文件的姓名提示，索引损坏时静默回退。"""
		index_path = self._store.state_path.parent.parent / "recruiter" / "analyzed.json"
		try:
			raw = json.loads(index_path.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			return ""
		if not isinstance(raw, dict):
			return ""
		match = re.search(r"(\d+)$", path.stem)
		key = match.group(1) if match else ""
		entry = raw.get(key)
		return str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""

	def _assign_one(self, path: Path, jobs: list[JobProfile]) -> dict[str, Any]:
		"""导入、评分并按阈值决定一份简历是否自动绑定到最高分岗位。"""
		base: dict[str, Any] = {"resume_path": str(path), "filename": path.name, "candidate_id": "", "assigned_job_id": "", "assigned_job_name": "", "job_scores": []}
		try:
			resume_text = read_resume_text(path)
			candidate = self._store.import_candidate(path, source="local_auto_import")
		except (ResumeTextReadError, ValueError) as exc:
			return {**base, "assignment_status": "failed", "reason": str(exc)}
		base["candidate_id"] = candidate.candidate_id
		name_hint = self._name_hint(path)
		if name_hint:
			candidate = self._store.update_candidate_name(candidate.candidate_id, name_hint)
		job_scores = [self._score_job(job, candidate.candidate_id, candidate.name, resume_text) for job in jobs]
		# 岗位归属只比较岗位本身的匹配证据，不能使用候选人总评。总评包含经验、
		# 沟通和稳定性等跨岗位共用维度；把它用于岗位排序会让严格专业证据降权
		# 反过来压缩不同岗位之间的差距，导致明显匹配的简历无法归属。
		job_scores.sort(key=lambda item: (-int(item["assignment_score"]), str(item["job_name"]).casefold(), str(item["job_id"])))
		base["job_scores"] = [{key: value for key, value in item.items() if key != "report"} for item in job_scores]
		if not job_scores:
			return {**base, "assignment_status": "unassigned", "reason": "没有可接收新简历的开放岗位"}
		best = job_scores[0]
		second_score = int(job_scores[1]["assignment_score"]) if len(job_scores) > 1 else 0
		margin = int(best["assignment_score"]) - second_score
		if int(best["assignment_score"]) < _AUTO_ASSIGN_MIN_SCORE or (len(job_scores) > 1 and margin < _AUTO_ASSIGN_MIN_MARGIN):
			return {
				**base,
				"assignment_status": "unassigned",
				"reason": "最高匹配分未达到自动分配阈值或与次优岗位差距不足",
				"best_score": int(best["assignment_score"]),
				"score_margin": margin,
			}
		self._store.link_candidate_to_job(candidate.candidate_id, str(best["job_id"]))
		report = best["report"]
		report_payload = report.to_dict()
		report_payload["auto_assignment"] = {
			"status": "auto_assigned",
			"score_basis": best["score_basis"],
			"job_standard_ready": best["job_standard_ready"],
			"score_margin": margin,
			"note": "已按最高匹配分自动分配。" if best["job_standard_ready"] else "已按职位名相关度临时自动分配，待完善岗位标准后重新评估。",
		}
		self._store.save_assessment(str(best["job_id"]), candidate.candidate_id, report_payload)
		return {
			**base,
			"assignment_status": "auto_assigned",
			"assigned_job_id": best["job_id"],
			"assigned_job_name": best["job_name"],
			"best_score": int(best["assignment_score"]),
			"score_margin": margin,
			"reason": report_payload["auto_assignment"]["note"],
		}

	def _score_job(self, job: JobProfile, candidate_id: str, candidate_name: str, resume_text: str) -> dict[str, Any]:
		"""为一个岗位返回分数、证据摘要和待落盘报告，保持排名输入可审计。"""
		readiness = evaluate_job_readiness(job)
		if readiness["ready"]:
			report = score_candidate(
				job,
				candidate_id=candidate_id,
				candidate_name=candidate_name,
				resume_text=resume_text,
				ai_review=self._review_with_ai(job, resume_text),
			)
			basis = "job_standard"
		else:
			report = _provisional_report(job, candidate_id=candidate_id, candidate_name=candidate_name, resume_text=resume_text)
			basis = "title_provisional"
		return {
			"job_id": job.job_id,
			"job_name": job.name,
			"score": report.final_score,
			# ``hard_match`` 汇总岗位条件、结构化学历/经验和简历证据，正是“这份
			# 简历更像哪个岗位”的可解释信号。候选人是否进入严格池仍由总评和专业
			# 问答门禁决定，两种决策不得共用一个分数。
			"assignment_score": int(report.score_breakdown["hard_match"]["score"]),
			"level": report.level,
			"score_basis": basis,
			"job_standard_ready": bool(readiness["ready"]),
			"reason": report.evidence[0] if report.evidence else "已生成本地匹配评分",
			"report": report,
		}

	def _review_with_ai(self, job: JobProfile, resume_text: str) -> AIResumeReview | None:
		"""调用已显式配置的 AI 评审器，失败时保留规则结果而不中断整批简历。"""
		if self._ai_reviewer is None:
			return None
		try:
			return self._ai_reviewer(job, resume_text)
		except Exception:
			# 自动分配首先要保证本地目录可以完整处理；AI 是证据增强层，服务
			# 暂时不可用时不应让所有文件退化成失败，也不能将异常原文暴露给页面。
			return None


__all__ = ["AutoResumeAssignmentService"]
