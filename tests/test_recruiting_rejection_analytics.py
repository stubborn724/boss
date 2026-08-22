"""岗位级不合格原因统计测试。"""

from boss_agent_cli.recruiting.rejection_analytics import build_rejection_reason_statistics
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


def test_rejection_reason_statistics_groups_explainable_assessment_findings() -> None:
	"""统计只能聚合已记录的评估原因，并按不推荐候选人数量计算占比。"""
	reports = [
		{
			"candidate_id": "candidate-a",
			"decision": "不推荐",
			"screening": {
				"hard_filter": {"mismatches": ["城市不匹配：岗位杭州，候选人上海"], "unknowns": []},
				"semantic_match": {"missing": ["Java 后端经验"]},
				"risk": {"signals": [{"code": "frequent_job_change", "message": "频繁跳槽"}]},
			},
		},
		{
			"candidate_id": "candidate-b",
			"decision": "不推荐",
			"screening": {
				"hard_filter": {"mismatches": [], "unknowns": ["缺少学历证据"]},
				"semantic_match": {"missing": []},
				"risk": {"signals": []},
			},
		},
		{
			"candidate_id": "candidate-c",
			"decision": "待人工确认",
			"screening": {"hard_filter": {"mismatches": ["城市不匹配"], "unknowns": []}},
		},
	]

	statistics = build_rejection_reason_statistics(reports)

	assert statistics["rejected_candidate_count"] == 2
	assert statistics["reasons"] == [
		{"code": "city_mismatch", "label": "城市不匹配", "count": 1, "rate": 50.0},
		{"code": "information_incomplete", "label": "简历信息不足", "count": 1, "rate": 50.0},
		{"code": "skill_mismatch", "label": "岗位经验或技能不匹配", "count": 1, "rate": 50.0},
		{"code": "stability_risk", "label": "稳定性风险", "count": 1, "rate": 50.0},
	]


def test_workspace_snapshot_exposes_selected_job_rejection_reason_statistics(tmp_path) -> None:
	"""工作台快照应提供当前岗位的评估统计，不混入其他岗位数据。"""
	resume_path = tmp_path / "候选人.md"
	resume_path.write_text(
		"姓名：候选人\n城市：上海\n期望薪资：10K\n学历：本科\n工作经验：3年\n"
		"最近职位：Java 工程师\n技能：Java、Spring Boot\n负责过后端接口开发与问题排查。",
		encoding="utf-8",
	)
	workspace = RecruitingWorkspace(tmp_path)
	job = workspace.create_job(name="Java", city="杭州")
	candidate = workspace.import_candidate(resume_path, job_id=job["job"]["job_id"])
	workspace.assess(job["job"]["job_id"], candidate["candidate_id"])

	snapshot = workspace.snapshot(job["job"]["job_id"])

	assert snapshot["rejection_reason_statistics"] == {
		"rejected_candidate_count": 1,
		"reasons": [
			{"code": "city_mismatch", "label": "城市不匹配", "count": 1, "rate": 100.0},
		],
	}
