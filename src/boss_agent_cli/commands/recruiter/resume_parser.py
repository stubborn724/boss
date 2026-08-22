"""招聘者 — 简历数据结构化解析。

将 BOSS 直聘 view_geek 原始响应转为干净的 JSON 结构，
方便 Agent 和 CLI 消费。
"""
from __future__ import annotations

from typing import Any


def _safe_str(val: Any) -> str:
	if val is None:
		return ""
	return str(val)


def _as_mapping(value: Any) -> dict[str, Any]:
	"""把平台可选对象收敛为字典，避免字段漂移或 null 让导出中断。"""
	return value if isinstance(value, dict) else {}


def _first_value(source: dict[str, Any], *keys: str) -> Any:
	"""按字段别名顺序取值，统一处理 BOSS 不同页面版本的命名差异。"""
	for key in keys:
		value = source.get(key)
		if value not in (None, ""):
			return value
	return ""


def _record_list(source: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
	"""只保留对象列表项，忽略平台偶发返回的 null 或字符串占位项。"""
	for key in keys:
		value = source.get(key)
		if isinstance(value, list):
			return [item for item in value if isinstance(item, dict)]
	return []


def _parse_base(info: dict[str, Any]) -> dict[str, Any]:
	base = _as_mapping(info.get("geekBaseInfo") or info.get("baseInfo"))
	return {
		"name": _first_value(base, "name", "userName"),
		"gender": "男" if base.get("gender") == 1 else "女",
		"age": _first_value(base, "ageDesc", "age"),
		"degree": _first_value(base, "degreeCategory", "degreeName"),
		"work_years": _first_value(base, "workYearDesc", "experience"),
		"active_status": _first_value(base, "activeTimeDesc", "activeStatus"),
		"avatar": base.get("large", ""),
	}


def _parse_expect(info: dict[str, Any]) -> dict[str, Any]:
	ex = _as_mapping(info.get("showExpectPosition") or info.get("expectation"))
	return {
		"position": _first_value(ex, "positionName", "position"),
		"salary": _first_value(ex, "salaryDesc", "expectedSalary"),
		"city": _first_value(ex, "locationName", "city"),
	}


def _parse_works(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for w in _record_list(info, "geekWorkExpList", "workExperienceList"):
		result.append({
			"company": _first_value(w, "company", "companyName"),
			"position": _first_value(w, "positionName", "position"),
			"department": w.get("department", ""),
			"start": _first_value(w, "startYearMonStr", "start"),
			"end": _first_value(w, "endYearMonStr", "end"),
			"duration": w.get("workYearDesc", ""),
			"responsibility": _first_value(w, "responsibility", "description"),
			"performance": w.get("workPerformance", ""),
			"keywords": w.get("workEmphasis", "").split("#&#") if w.get("workEmphasis") else [],
		})
	return result


def _parse_projects(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for p in _record_list(info, "geekProjExpList", "projectList"):
		result.append({
			"name": _first_value(p, "name", "projectName"),
			"role": _first_value(p, "roleName", "role"),
			"start": p.get("startDateDesc", ""),
			"end": p.get("endDateDesc", ""),
			"duration": p.get("workYearDesc", ""),
			"description": _first_value(p, "projectDescription", "description"),
			"achievement": p.get("performance", ""),
		})
	return result


def _parse_education(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for e in _record_list(info, "geekEduExpList", "educationList"):
		result.append({
			"school": _first_value(e, "school", "schoolName"),
			"major": _first_value(e, "major", "majorName"),
			"degree": _first_value(e, "degreeDesc", "degreeName"),
			"start": e.get("startYearMonStr", ""),
			"end": e.get("endYearMonStr", ""),
		})
	return result


def _parse_competitive(info: dict[str, Any]) -> list[str]:
	jc = _as_mapping(info.get("jobCompetitive"))
	return [str(t.get("content") or "") for t in _record_list(jc, "tips")]


def _detail_info(payload: dict[str, Any]) -> dict[str, Any]:
	"""兼容在线简历详情的两套包络，调用方只面对一个稳定信息对象。"""
	return _as_mapping(payload.get("geekDetailInfo") or payload.get("geekInfo"))


def _certifications(info: dict[str, Any]) -> list[str]:
	"""兼容对象或文本证书，并丢弃空值和未知列表项。"""
	values = info.get("geekCertificationList")
	if not isinstance(values, list):
		values = info.get("certifications")
	if not isinstance(values, list):
		return []
	result: list[str] = []
	for value in values:
		if isinstance(value, str) and value.strip():
			result.append(value.strip())
		elif isinstance(value, dict):
			name = _first_value(value, "certName", "name")
			if name:
				result.append(_safe_str(name))
	return result


def parse_resume(raw: dict[str, Any]) -> dict[str, Any]:
	"""从 view_geek 响应解析结构化简历。

	Parameters
	----------
	raw : dict
		view_geek 返回的完整响应（含 code/zpData 或 code/data），
		也可直接传入已解包的数据体。

	Returns
	-------
	dict
		结构化简历：basic / expectation / work_experience /
		project_experience / education / competitive_analysis / certifications
	"""
	payload = raw.get("zpData") if "zpData" in raw else raw.get("data", raw)
	if not isinstance(payload, dict):
		payload = {}
	info = _detail_info(payload)

	certs = _certifications(info)

	return {
		"basic": _parse_base(info),
		"expectation": _parse_expect(info),
		"work_experience": _parse_works(info),
		"project_experience": _parse_projects(info),
		"education": _parse_education(info),
		"competitive_analysis": _parse_competitive(info),
		"certifications": certs,
	}
