"""把候选人评估投影为岗位级不合格原因统计。

模块只消费已落库的脱敏评估快照，不读取简历正文、不调用 AI，也不改变候选人
阶段。这样统计可被 Web、CLI 和后续报表共同复用，且永远保持为人工判断的辅助
信息，而非自动淘汰规则。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


_REASON_LABELS: dict[str, str] = {
	"city_mismatch": "城市不匹配",
	"salary_mismatch": "薪资不匹配",
	"education_mismatch": "学历不匹配",
	"experience_mismatch": "工作年限不足",
	"skill_mismatch": "岗位经验或技能不匹配",
	"direction_mismatch": "职业方向不一致",
	"stability_risk": "稳定性风险",
	"information_incomplete": "简历信息不足",
	"professional_qa": "专业问答证据不足",
	"other": "其他已记录原因",
}


def _mapping(value: object) -> Mapping[str, Any]:
	"""把可能缺失或不合法的嵌套对象收敛为空映射，兼容历史报告。"""
	return value if isinstance(value, Mapping) else {}


def _text_items(value: object) -> list[str]:
	"""提取非空文本列表，防止异常报告让整个岗位统计失败。"""
	if not isinstance(value, list):
		return []
	return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _hard_mismatch_code(message: str) -> str:
	"""按稳定的字段前缀归类硬条件失败，不根据候选人自由文本做推断。"""
	if "城市" in message:
		return "city_mismatch"
	if "薪资" in message:
		return "salary_mismatch"
	if "学历" in message:
		return "education_mismatch"
	if "年限" in message or "经验" in message:
		return "experience_mismatch"
	return "other"


def _risk_code(reason_code: str) -> str:
	"""将筛选层的风险码映射为面向 HR 的固定统计类别。"""
	if reason_code in {"frequent_job_change", "employment_gap"}:
		return "stability_risk"
	if reason_code == "short_resume":
		return "information_incomplete"
	if reason_code == "direction_mismatch":
		return "direction_mismatch"
	if reason_code == "salary_mismatch":
		return "salary_mismatch"
	if reason_code == "qa_inconsistency":
		return "professional_qa"
	return "other"


def _reasons_for_report(report: Mapping[str, Any]) -> set[str]:
	"""提取单份不推荐报告的可解释原因；同类原因在一人身上只计一次。"""
	screening = _mapping(report.get("screening"))
	hard_filter = _mapping(screening.get("hard_filter"))
	semantic_match = _mapping(screening.get("semantic_match"))
	risk = _mapping(screening.get("risk"))
	professional_qa = _mapping(screening.get("professional_qa"))

	reasons = {_hard_mismatch_code(message) for message in _text_items(hard_filter.get("mismatches"))}
	if _text_items(hard_filter.get("unknowns")):
		reasons.add("information_incomplete")
	if _text_items(semantic_match.get("missing")):
		reasons.add("skill_mismatch")
	for signal in risk.get("signals", []):
		if isinstance(signal, Mapping):
			reasons.add(_risk_code(str(signal.get("code") or "")))
	if str(professional_qa.get("status") or "") == "follow_up":
		reasons.add("professional_qa")
	return reasons or {"other"}


def build_rejection_reason_statistics(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
	"""按岗位聚合已评为“不推荐”的原因与占比。

	每个候选人在同一类别最多贡献一次，分母是该岗位的“不推荐”候选人数，因此
	一名候选人存在多个独立问题时，多个原因的占比总和可能超过 100%。这比强行
	只保留一个原因更符合 HR 复盘需要，也不会把推测性的 AI 文本计入统计。
	"""
	rejected_reports = [
		report
		for report in reports
		if isinstance(report, Mapping)
		and (
			str(report.get("decision") or "") == "不推荐"
			or str(_mapping(report.get("screening")).get("decision") or "") == "不推荐"
		)
	]
	total = len(rejected_reports)
	counts: Counter[str] = Counter()
	for report in rejected_reports:
		counts.update(_reasons_for_report(report))
	return {
		"rejected_candidate_count": total,
		"reasons": [
			{
				"code": code,
				"label": _REASON_LABELS.get(code, _REASON_LABELS["other"]),
				"count": count,
				"rate": round(count / total * 100, 1) if total else 0.0,
			}
			for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
		],
	}
