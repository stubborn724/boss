"""岗位独立候选人评分分组的投影测试。"""

from __future__ import annotations

from boss_agent_cli.recruiting.workflow import build_score_groups


def test_score_groups_are_scoped_to_selected_job() -> None:
	"""同一候选人在另一岗位的分数不得污染当前岗位的分组。"""
	groups = build_score_groups(
		candidates=[{"candidate_id": "candidate-1", "name": "候选人"}],
		assessments=[
			{"job_id": "java", "candidate_id": "candidate-1", "final_score": 91},
			{"job_id": "sales", "candidate_id": "candidate-1", "final_score": 50},
		],
		selected_job_id="java",
	)

	assert groups[0]["key"] == "strong_recommend"
	assert groups[0]["candidate_ids"] == ["candidate-1"]
	assert all(group["count"] == 0 for group in groups[1:])


def test_score_groups_include_all_bands_and_unassessed_candidates() -> None:
	"""六档分组固定出现，避免前端在空档时改变列表结构。"""
	groups = build_score_groups(
		candidates=[
			{"candidate_id": "c90", "name": "甲"}, {"candidate_id": "c80", "name": "乙"},
			{"candidate_id": "c70", "name": "丙"}, {"candidate_id": "c60", "name": "丁"},
			{"candidate_id": "c59", "name": "戊"}, {"candidate_id": "none", "name": "己"},
		],
		assessments=[
			{"job_id": "job", "candidate_id": "c90", "final_score": 90},
			{"job_id": "job", "candidate_id": "c80", "final_score": 80},
			{"job_id": "job", "candidate_id": "c70", "final_score": 70},
			{"job_id": "job", "candidate_id": "c60", "final_score": 60},
			{"job_id": "job", "candidate_id": "c59", "final_score": 59},
		],
		selected_job_id="job",
	)

	assert [group["key"] for group in groups] == [
		"strong_recommend", "recommend", "pending_confirmation", "manual_review", "not_recommend", "unassessed",
	]
	assert [group["candidate_ids"] for group in groups] == [["c90"], ["c80"], ["c70"], ["c60"], ["c59"], ["none"]]
