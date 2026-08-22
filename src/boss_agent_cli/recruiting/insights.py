"""招聘工作台的只读复盘指标和优化建议。

这里不修改岗位、知识库、FAQ 或平台配置，只把已经由 HR 记录的本地事实
汇总成可解释的建议。单独抽成纯函数，既避免 Web 层拼接业务规则，也让
后续接入更完整的报表存储时可以保持相同的输入输出边界。
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import re
from typing import Any, Iterable


_MIN_RELIABLE_SAMPLE = 3
_HIRING_OUTCOMES = {"hired"}
_COMPARISON_OUTCOMES = {"rejected", "paused"}


def _number(value: object) -> float | None:
	"""把复盘输入中的数值安全转换为浮点数，拒绝布尔值和异常文本。"""
	if isinstance(value, bool):
		return None
	if isinstance(value, (int, float)):
		return float(value)
	if isinstance(value, str) and value.strip():
		try:
			return float(value.strip())
		except ValueError:
			return None
	return None


def _assessment_signal(report: dict[str, Any], key: str) -> float | None:
	"""读取评估报告的总分或分项分数，兼容历史报告缺失字段。"""
	if key == "final_score":
		return _number(report.get("final_score"))
	if key == "professional_qa":
		value = _number(report.get("professional_qa_score"))
		if value is not None:
			return value
	breakdown = report.get("score_breakdown")
	if isinstance(breakdown, dict):
		component = breakdown.get(key)
		if isinstance(component, dict):
			return _number(component.get("score"))
	return None


def _profile_signal_values(profile: dict[str, Any], field: str) -> list[str]:
	"""从脱敏画像读取可聚合的行业、学历或技能信号。"""
	value = profile.get(field)
	if field == "skills":
		return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
	if isinstance(value, str) and value.strip():
		return [value.strip()]
	return []


def _build_hiring_learning(
	candidate_rows: Iterable[dict[str, Any]],
	assessment_rows: Iterable[dict[str, Any]],
	decision_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
	"""比较录用组与其他终局组的脱敏信号，并明确小样本边界。

	该函数只接收工作台已经投影出的候选人画像、评估报告和终局元数据；输出
	全部是组级数量、均值和比例，不保留候选人标识。小样本仍可展示差异，但
	必须标记为趋势，调用方不能把它解释成自动调参依据。
	"""
	candidates = {
		str(row.get("candidate_id") or ""): row
		for row in candidate_rows
		if isinstance(row, dict) and str(row.get("candidate_id") or "").strip()
	}
	assessments = {
		str(row.get("candidate_id") or ""): row
		for row in assessment_rows
		if isinstance(row, dict) and str(row.get("candidate_id") or "").strip()
	}
	decisions: dict[str, dict[str, Any]] = {}
	for row in decision_rows:
		if not isinstance(row, dict):
			continue
		candidate_id = str(row.get("candidate_id") or "").strip()
		outcome = str(row.get("outcome") or "").strip()
		if candidate_id and outcome in _HIRING_OUTCOMES | _COMPARISON_OUTCOMES and candidate_id not in decisions:
			# Store 默认按时间倒序返回；保留第一条，避免历史重复决定重复计数。
			decisions[candidate_id] = row
	groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {"hired": [], "comparison": []}
	for candidate_id, decision in decisions.items():
		candidate = candidates.get(candidate_id)
		if candidate is None:
			continue
		group = "hired" if str(decision.get("outcome")) in _HIRING_OUTCOMES else "comparison"
		groups[group].append((candidate, assessments.get(candidate_id, {})))
	hired_count = len(groups["hired"])
	comparison_count = len(groups["comparison"])
	low_sample = min(hired_count, comparison_count) < _MIN_RELIABLE_SAMPLE
	if not hired_count or not comparison_count:
		notice = "录用结果学习需要至少一条录用和一条其他终局记录；当前只展示已有事实。"
	elif low_sample:
		notice = f"录用组 {hired_count} 条、其他终局组 {comparison_count} 条；至少各有 {_MIN_RELIABLE_SAMPLE} 条后再比较。"
	else:
		notice = ""
	numeric_signals: list[dict[str, Any]] = []
	for key, label in (
		("final_score", "综合评分"),
		("hard_match", "硬条件匹配"),
		("experience", "经验维度"),
		("professional_qa", "专业问答"),
		("communication", "沟通表达"),
		("stability", "稳定性"),
		("location_salary", "地点薪资匹配"),
	):
		hired_values = [value for _, report in groups["hired"] if (value := _assessment_signal(report, key)) is not None]
		comparison_values = [value for _, report in groups["comparison"] if (value := _assessment_signal(report, key)) is not None]
		if not hired_values or not comparison_values:
			continue
		hired_average = round(sum(hired_values) / len(hired_values), 1)
		comparison_average = round(sum(comparison_values) / len(comparison_values), 1)
		difference = round(hired_average - comparison_average, 1)
		numeric_signals.append(
			{
				"key": key,
				"label": label,
				"hired_average": hired_average,
				"comparison_average": comparison_average,
				"difference": difference,
				"direction": "hired_higher" if difference > 0 else ("hired_lower" if difference < 0 else "same"),
				"hired_sample_size": len(hired_values),
				"comparison_sample_size": len(comparison_values),
			}
		)
	profile_signals: list[dict[str, Any]] = []
	for field, label in (("experience_years", "经验年限"), ("industry", "行业"), ("education", "学历"), ("skills", "技能")):
		if field == "experience_years":
			hired_values = [
				value
				for candidate, _ in groups["hired"]
				if (value := _number((candidate.get("profile") or {}).get(field) if isinstance(candidate.get("profile"), dict) else None)) is not None
			]
			comparison_values = [
				value
				for candidate, _ in groups["comparison"]
				if (value := _number((candidate.get("profile") or {}).get(field) if isinstance(candidate.get("profile"), dict) else None)) is not None
			]
			if hired_values and comparison_values:
				hired_average = round(sum(hired_values) / len(hired_values), 1)
				comparison_average = round(sum(comparison_values) / len(comparison_values), 1)
				difference = round(hired_average - comparison_average, 1)
				profile_signals.append(
					{
						"field": field,
						"label": label,
						"signal": "平均年限",
						"hired_average": hired_average,
						"comparison_average": comparison_average,
						"difference": difference,
					}
				)
			continue
		hired_counts: Counter[str] = Counter()
		comparison_counts: Counter[str] = Counter()
		for candidate, _ in groups["hired"]:
			profile = candidate.get("profile")
			if isinstance(profile, dict):
				hired_counts.update(_profile_signal_values(profile, field))
		for candidate, _ in groups["comparison"]:
			profile = candidate.get("profile")
			if isinstance(profile, dict):
				comparison_counts.update(_profile_signal_values(profile, field))
		all_signals = set(hired_counts) | set(comparison_counts)
		for signal in all_signals:
			total = hired_counts[signal] + comparison_counts[signal]
			if total < 2:
				continue
			hired_rate = round(hired_counts[signal] / hired_count * 100, 1) if hired_count else 0.0
			comparison_rate = round(comparison_counts[signal] / comparison_count * 100, 1) if comparison_count else 0.0
			profile_signals.append(
				{
					"field": field,
					"label": label,
					"signal": signal,
					"hired_count": hired_counts[signal],
					"comparison_count": comparison_counts[signal],
					"hired_rate": hired_rate,
					"comparison_rate": comparison_rate,
					"difference": round(hired_rate - comparison_rate, 1),
				}
			)
	profile_signals.sort(key=lambda item: (-abs(float(item.get("difference") or 0)), str(item.get("field")), str(item.get("signal"))))
	return {
		"status": "ready" if hired_count and comparison_count else "insufficient_data",
		"hired_count": hired_count,
		"comparison_count": comparison_count,
		"low_sample": low_sample,
		"notice": notice,
		"numeric_signals": numeric_signals,
		"profile_signals": profile_signals[:8],
	}


def _suggestion(job_id: str, *, kind: str, title: str, reason: str, action: str, severity: str) -> dict[str, str]:
	"""构造稳定的建议元数据，不携带候选人正文或联系方式。"""
	seed = "|".join((job_id, kind, title, reason))
	return {
		"suggestion_id": f"suggestion-{sha256(seed.encode('utf-8')).hexdigest()[:16]}",
		"kind": kind,
		"severity": severity,
		"title": title,
		"reason": reason,
		"action": action,
	}


def _top_count(values: Iterable[str]) -> tuple[str, int]:
	"""返回出现次数最多的非空值；平局按字典序稳定选择。"""
	counts = Counter(str(value).strip() for value in values if str(value).strip())
	if not counts:
		return "", 0
	return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _rate_table(values: Iterable[str]) -> dict[str, dict[str, int | float]]:
	"""把结果枚举转换成稳定的数量/比例表，供页面直接展示。"""
	counts = Counter(str(value).strip() for value in values if str(value).strip())
	total = sum(counts.values())
	return {
		key: {"count": count, "rate": round(count / total * 100, 1) if total else 0.0}
		for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
	}


def _normalise_question(value: str) -> str:
	"""把候选人问题压缩成可聚合的本地键，不做语义改写。"""
	return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _question_demand_tables(rows: Iterable[dict[str, Any]]) -> tuple[
	dict[str, dict[str, Any]],
	dict[str, dict[str, Any]],
	list[dict[str, Any]],
]:
	"""汇总候选人问题和 FAQ 命中，保留展示文案与来源连接点。"""
	query_buckets: dict[str, dict[str, Any]] = {}
	faq_buckets: dict[str, dict[str, Any]] = {}
	for raw in rows:
		if not isinstance(raw, dict):
			continue
		query = str(raw.get("query") or "").strip()
		normalised = str(raw.get("normalized_query") or _normalise_question(query)).strip()
		if not normalised:
			continue
		query_entry = query_buckets.setdefault(
			normalised,
			{
				"question": query or normalised,
				"count": 0,
				"answered_count": 0,
				"unanswered_count": 0,
				"source_type": "",
				"source_id": "",
				"source_title": "",
			},
		)
		query_entry["count"] += 1
		if str(raw.get("status") or "") == "answered":
			query_entry["answered_count"] += 1
		else:
			query_entry["unanswered_count"] += 1
		for field in ("source_type", "source_id", "source_title"):
			if not query_entry[field] and str(raw.get(field) or "").strip():
				query_entry[field] = str(raw.get(field) or "").strip()
		source_type = str(raw.get("source_type") or "").strip()
		source_id = str(raw.get("source_id") or "").strip()
		if source_type != "faq" or not source_id:
			continue
		faq_entry = faq_buckets.setdefault(
			source_id,
			{
				"faq_id": source_id,
				"question": str(raw.get("source_title") or query or "未命名 FAQ").strip(),
				"count": 0,
				"answered_count": 0,
				"unanswered_count": 0,
			},
		)
		faq_entry["count"] += 1
		if str(raw.get("status") or "") == "answered":
			faq_entry["answered_count"] += 1
		else:
			faq_entry["unanswered_count"] += 1
	query_total = sum(int(item["count"]) for item in query_buckets.values())
	faq_total = sum(int(item["count"]) for item in faq_buckets.values())
	question_rates: dict[str, dict[str, Any]] = {}
	for key, item in sorted(query_buckets.items(), key=lambda pair: (-int(pair[1]["count"]), pair[0])):
		item["rate"] = round(int(item["count"]) / query_total * 100, 1) if query_total else 0.0
		item["low_sample"] = int(item["count"]) < _MIN_RELIABLE_SAMPLE
		question_rates[key] = item
	faq_rates: dict[str, dict[str, Any]] = {}
	for key, item in sorted(faq_buckets.items(), key=lambda pair: (-int(pair[1]["count"]), pair[0])):
		item["rate"] = round(int(item["count"]) / faq_total * 100, 1) if faq_total else 0.0
		item["low_sample"] = int(item["count"]) < _MIN_RELIABLE_SAMPLE
		faq_rates[key] = item
	return question_rates, faq_rates, list(faq_rates.values())[:5]


def build_optimization_projection(
	*,
	job_id: str,
	candidate_count: int,
	knowledge_count: int,
	faq_count: int,
	assessment_count: int,
	professional_qa_scores: Iterable[int],
	mismatch_reasons: Iterable[str],
	communication_outcomes: Iterable[str],
	decision_outcomes: Iterable[str],
	candidate_rows: Iterable[dict[str, Any]] = (),
	assessment_rows: Iterable[dict[str, Any]] = (),
	template_usages: Iterable[dict[str, Any]] = (),
	communication_rows: Iterable[dict[str, Any]] = (),
	decision_rows: Iterable[dict[str, Any]] = (),
	question_demands: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
	"""把招聘漏斗事实转换为稳定指标和人工可执行建议。

	阈值只用于提示复盘，不会自动改变岗位标准、话术或平台任务。输出中的
	``mutations`` 始终为空列表，明确给调用方和前端留下人工确认边界。
	"""
	qa_scores = [max(0, min(100, int(score))) for score in professional_qa_scores]
	mismatch_values = [str(value).strip() for value in mismatch_reasons if str(value).strip()]
	communication_values = [str(value).strip() for value in communication_outcomes if str(value).strip()]
	decision_values = [str(value).strip() for value in decision_outcomes if str(value).strip()]
	candidate_values = [dict(row) for row in candidate_rows if isinstance(row, dict)]
	assessment_values = [dict(row) for row in assessment_rows if isinstance(row, dict)]
	template_values = [dict(row) for row in template_usages if isinstance(row, dict)]
	communication_records = [dict(row) for row in communication_rows if isinstance(row, dict)]
	decision_records = [dict(row) for row in decision_rows if isinstance(row, dict)]
	question_records = [dict(row) for row in question_demands if isinstance(row, dict)]
	hiring_learning = _build_hiring_learning(candidate_values, assessment_values, decision_records)
	top_reason, top_reason_count = _top_count(mismatch_values)
	top_communication, top_communication_count = _top_count(communication_values)
	top_decision, top_decision_count = _top_count(decision_values)
	suggestions: list[dict[str, str]] = []
	terminal_stages = {"hired", "rejected", "paused"}
	source_conversion: dict[str, dict[str, int | float]] = {}
	for row in candidate_values:
		source = str(row.get("source") or "unknown")
		metrics_for_source = source_conversion.setdefault(source, {"candidate_count": 0, "terminal_count": 0, "terminal_rate": 0.0})
		metrics_for_source["candidate_count"] = int(metrics_for_source["candidate_count"]) + 1
		if str(row.get("stage") or "") in terminal_stages:
			metrics_for_source["terminal_count"] = int(metrics_for_source["terminal_count"]) + 1
	for values in source_conversion.values():
		count = int(values["candidate_count"])
		values["terminal_rate"] = round(int(values["terminal_count"]) / count * 100, 1) if count else 0.0
	stage_conversion: dict[str, int] = {}
	for row in candidate_values:
		stage = str(row.get("stage") or "unknown")
		stage_conversion[stage] = stage_conversion.get(stage, 0) + 1
	template_effectiveness: dict[str, dict[str, Any]] = {}
	template_candidates: dict[str, set[str]] = {}
	for usage in template_values:
		key = f"{str(usage.get('template_key') or 'unknown')}:{str(usage.get('template_version') or 'v1')}"
		entry = template_effectiveness.setdefault(
			key,
			{"usage_count": 0, "candidate_count": 0, "communication_count": 0, "qualified_count": 0, "qualified_rate": 0.0},
		)
		entry["usage_count"] += 1
		if usage.get("candidate_id"):
			entry["candidate_count"] += 1
			template_candidates.setdefault(key, set()).add(str(usage.get("candidate_id")))
	for communication in communication_records:
		key = f"{str(communication.get('template_key') or 'unknown')}:{str(communication.get('template_version') or 'v1')}"
		entry = template_effectiveness.setdefault(
			key,
			{"usage_count": 0, "candidate_count": 0, "communication_count": 0, "qualified_count": 0, "qualified_rate": 0.0},
		)
		entry["communication_count"] += 1
		if str(communication.get("outcome") or "") == "qualified":
			entry["qualified_count"] += 1
		if str(communication.get("outcome") or "") == "no_response":
			entry["no_response_count"] = int(entry.get("no_response_count", 0)) + 1
		if communication.get("candidate_id"):
			template_candidates.setdefault(key, set()).add(str(communication.get("candidate_id")))
	for entry in template_effectiveness.values():
		communication_count = int(entry["communication_count"])
		# 沟通记录本身也能证明候选人使用过该话术；兼容没有单独
		# message_template_usages 审计记录的旧数据。
		entry["candidate_count"] = max(int(entry.get("candidate_count", 0)), 0)
		entry["qualified_rate"] = round(int(entry["qualified_count"]) / communication_count * 100, 1) if communication_count else 0.0
		entry["response_count"] = communication_count - int(entry.get("no_response_count", 0))
		entry["response_rate"] = round(int(entry["response_count"]) / communication_count * 100, 1) if communication_count else 0.0
		entry["low_sample"] = communication_count < _MIN_RELIABLE_SAMPLE
	# 话术结果按候选人关联终局记录；一个候选人使用多个版本时，各版本都保留
	# 事实计数，不把系统推断包装成唯一归因。
	for decision in decision_records:
		candidate_id = str(decision.get("candidate_id") or "").strip()
		if not candidate_id:
			continue
		for key, candidates in template_candidates.items():
			if candidate_id not in candidates:
				continue
			entry = template_effectiveness[key]
			entry["decision_count"] = int(entry.get("decision_count", 0)) + 1
			if str(decision.get("outcome") or "") == "hired":
				entry["hired_count"] = int(entry.get("hired_count", 0)) + 1
	for key, candidates in template_candidates.items():
		if key in template_effectiveness:
			entry = template_effectiveness[key]
			entry["candidate_count"] = max(int(entry.get("candidate_count", 0)), len(candidates))
	for entry in template_effectiveness.values():
		candidate_count_for_template = int(entry.get("candidate_count", 0))
		entry["hired_rate"] = round(int(entry.get("hired_count", 0)) / candidate_count_for_template * 100, 1) if candidate_count_for_template else 0.0
		entry["low_sample"] = int(entry.get("communication_count", 0)) < _MIN_RELIABLE_SAMPLE
	question_demand_rates, faq_demand_rates, top_faq_questions = _question_demand_tables(question_records)
	template_outcome_rates = {
		key: {
			"sample_size": int(value.get("communication_count", 0)),
			"reply_rate": float(value.get("response_rate", 0.0)),
			"qualified_rate": float(value.get("qualified_rate", 0.0)),
			"hired_rate": float(value.get("hired_rate", 0.0)),
			"low_sample": bool(value.get("low_sample", False)),
		}
		for key, value in template_effectiveness.items()
	}
	low_sample_sources = []
	if question_records and len(question_records) < _MIN_RELIABLE_SAMPLE:
		low_sample_sources.append("候选人问题")
	if communication_records and len(communication_records) < _MIN_RELIABLE_SAMPLE:
		low_sample_sources.append("沟通结果")
	if decision_values and len(decision_values) < _MIN_RELIABLE_SAMPLE:
		low_sample_sources.append("终局结果")
	if hiring_learning["low_sample"] and hiring_learning["status"] == "ready":
		low_sample_sources.append("录用结果学习")
	if candidate_count > 0 and knowledge_count == 0:
		suggestions.append(
			_suggestion(
				job_id,
				kind="knowledge_gap",
				title="补充岗位知识来源",
				reason="当前岗位有候选人，但没有可引用的企业知识文档。",
				action="由 HR 导入销售流程、岗位说明或产品资料，再人工核对后用于提问。",
				severity="medium",
			)
		)
	if top_reason_count >= 2:
		suggestions.append(
			_suggestion(
				job_id,
				kind="mismatch_pattern",
				title="复核重复不匹配原因",
				reason=f"“{top_reason}”已出现 {top_reason_count} 次。",
				action="检查岗位标准、来源筛选条件和候选人预期是否需要人工调整。",
				severity="high",
			)
		)
	if qa_scores and any(score < 60 for score in qa_scores):
		low_count = sum(score < 60 for score in qa_scores)
		suggestions.append(
			_suggestion(
				job_id,
				kind="qa_threshold",
				title="补充专业问答追问",
				reason=f"{low_count} 份评估的专业问答低于 60 分门槛。",
				action="人工查看问题来源和候选人回答，补充追问或 FAQ 后重新评估。",
				severity="high",
			)
		)
	if top_communication == "no_response" and top_communication_count >= 2:
		suggestions.append(
			_suggestion(
				job_id,
				kind="communication_pattern",
				title="复核未回复沟通节奏",
				reason=f"未回复已记录 {top_communication_count} 次。",
				action="人工检查跟进时间和话术效果，必要时调整节奏；系统不会自动重发。",
				severity="medium",
			)
		)
	if top_decision == "rejected" and top_decision_count >= 2 and assessment_count > 0:
		suggestions.append(
			_suggestion(
				job_id,
				kind="decision_pattern",
				title="复盘淘汰结果",
				reason=f"终局淘汰已记录 {top_decision_count} 次。",
				action="人工对照不匹配反馈和评估证据，确认是否需要优化岗位画像。",
				severity="medium",
			)
		)
	if "hired" in decision_values:
		suggestions.append(
			_suggestion(
				job_id,
				kind="hiring_feedback",
				title="把录用结果反馈到岗位标准",
				reason=f"当前已有 {decision_values.count('hired')} 条录用结果可供复盘。",
				action="人工对照录用候选人的硬条件、专业问答和沟通证据，决定是否补充岗位标准或话术；实际调整由 HR 手动完成。",
				severity="low",
			)
		)
	if hiring_learning["status"] == "ready" and (hiring_learning["numeric_signals"] or hiring_learning["profile_signals"]):
		learning_signal = next(
			(
				item
				for item in [*hiring_learning["numeric_signals"], *hiring_learning["profile_signals"]]
				if abs(float(item.get("difference") or 0)) > 0
			),
			None,
		)
		if learning_signal is not None:
			label = str(learning_signal.get("label") or learning_signal.get("key") or "画像信号")
			if "hired_average" in learning_signal:
				reason = (
					f"录用组 {label} 平均 {learning_signal['hired_average']}，"
					f"其他终局组 {learning_signal['comparison_average']}，差 {learning_signal['difference']}。"
				)
			else:
				reason = (
					f"画像信号“{learning_signal.get('signal') or '未命名'}”在录用组占比 "
					f"{learning_signal.get('hired_rate', 0)}%，其他终局组占比 {learning_signal.get('comparison_rate', 0)}%。"
				)
			suggestions.append(
				_suggestion(
					job_id,
					kind="hiring_learning",
					title="复核录用结果信号",
					reason=reason,
					action="先人工核对录用样本与评估证据，再决定是否手动调整岗位标准；统计结果不会自动变成硬条件。",
					severity="low" if hiring_learning["low_sample"] else "medium",
				)
			)
	return {
		"metrics": {
			"candidate_count": max(0, int(candidate_count)),
			"knowledge_count": max(0, int(knowledge_count)),
			"faq_count": max(0, int(faq_count)),
			"assessment_count": max(0, int(assessment_count)),
			"qa_answered_count": len(qa_scores),
			"qa_below_threshold_count": sum(score < 60 for score in qa_scores),
			"mismatch_feedback_count": len(mismatch_values),
			# 不匹配原因按出现次数和占比输出，供复盘区识别结构性问题；这里只读
			# 已记录事实，不根据比例自动改岗位筛选条件或候选人阶段。
			"mismatch_reason_rates": _rate_table(mismatch_values),
			"communication_count": len(communication_values),
			"decision_count": len(decision_values),
			"top_mismatch_reason": top_reason,
			"top_communication_outcome": top_communication,
			"top_decision_outcome": top_decision,
			"communication_outcome_rates": _rate_table(communication_values),
			"decision_outcome_rates": _rate_table(decision_values),
			"source_conversion": source_conversion,
			"stage_conversion": stage_conversion,
			"template_effectiveness": template_effectiveness,
			"template_outcome_rates": template_outcome_rates,
			"question_demand_rates": question_demand_rates,
			"faq_demand_rates": faq_demand_rates,
			"top_faq_questions": top_faq_questions,
			"hiring_learning": hiring_learning,
			"sample_notice": (
				"当前样本量较少（少于 3 条），仅供趋势参考，建议继续记录后再比较。"
				+ (f"涉及：{'、'.join(low_sample_sources)}。" if low_sample_sources else "")
			),
		},
		"suggestions": suggestions,
		"mutations": [],
	}


def build_daily_snapshot_metrics(
	*,
	candidate_count: int = 0,
	active_candidate_count: int = 0,
	assessed_count: int = 0,
	hired_count: int = 0,
	avg_score: float | None = None,
	knowledge_count: int = 0,
	faq_count: int = 0,
	communication_count: int = 0,
	interview_count: int = 0,
) -> dict[str, Any]:
	"""按日聚合核心指标，供时间序列仪表盘使用。

	所有输入都是纯数值，函数只负责打包成标准字典。
	"""
	return {
		"candidate_count": max(0, int(candidate_count)),
		"active_candidate_count": max(0, int(active_candidate_count)),
		"assessed_count": max(0, int(assessed_count)),
		"hired_count": max(0, int(hired_count)),
		"avg_score": round(float(avg_score), 1) if avg_score is not None else None,
		"knowledge_count": max(0, int(knowledge_count)),
		"faq_count": max(0, int(faq_count)),
		"communication_count": max(0, int(communication_count)),
		"interview_count": max(0, int(interview_count)),
	}


__all__ = ["build_optimization_projection", "build_daily_snapshot_metrics"]
