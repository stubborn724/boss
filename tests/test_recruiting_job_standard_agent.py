"""岗位标准 Agent 的直接保存与安全降级测试。"""

from __future__ import annotations

from boss_agent_cli.ai.service import AIServiceError
from boss_agent_cli.recruiting.job_standard_agent import JobStandardAgent
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


class _StaticAI:
	"""返回固定 JSON 的极小 AI 替身，避免测试依赖网络。"""

	model = "test-model"

	def chat(self, _messages: list[dict[str, str]]) -> str:
		return '''{
			"name":"ToB 销售顾问",
			"must_have":["1年以上销售经验","能接受电话销售"],
			"nice_to_have":["招商加盟经验"],
			"reject_if":["不接受绩效考核"],
			"risk_signals":["频繁跳槽"],
			"city":"杭州",
			"education_requirement":"大专及以上",
			"min_experience_years":1,
			"industry":"企业服务",
			"skills":["电话销售"],
			"notes":["已按岗位需求生成可编辑标准"]
		}'''


class _UnavailableAI:
	"""模拟已配置但认证失败的模型服务。"""

	model = "test-model"

	def chat(self, _messages: list[dict[str, str]]) -> str:
		"""模拟第三方模型返回认证错误，验证招聘配置仍可用。"""
		raise AIServiceError("API 请求失败: HTTP 401", status_code=401)


def test_agent_directly_creates_editable_job_from_natural_language(tmp_path) -> None:
	"""自然语言应直接落盘为岗位，而非创建等待确认的中间草案。"""
	workspace = RecruitingWorkspace(tmp_path)
	agent = JobStandardAgent(ai_service=_StaticAI())

	result = agent.create_job(
		workspace,
		requirements="我想招一个销售，最好有电话销售经验，能吃苦，接受单休，有过招商加盟经验优先，不要频繁跳槽。",
	)

	job = result["job"]
	assert job["name"] == "ToB 销售顾问"
	assert job["city"] == "杭州"
	assert job["status"] == "published"
	# 年限进入独立硬条件字段，避免同一要求既做结构化门禁又在文本列表里重复计分。
	assert job["min_experience_years"] == 1
	assert job["criteria"]["must_have"] == ["能接受电话销售"]
	assert job["criteria"]["nice_to_have"] == ["招商加盟经验"]
	assert result["analysis_source"] == "ai"
	assert workspace.list_jobs()[0]["job_id"] == job["job_id"]


def test_agent_falls_back_to_rules_and_discards_sensitive_requirements(tmp_path) -> None:
	"""AI 不可用时仍直接保存，同时禁止敏感人口属性成为筛选条件。"""
	workspace = RecruitingWorkspace(tmp_path)
	agent = JobStandardAgent()

	result = agent.create_job(
		workspace,
		requirements="销售顾问；必须有电话销售经验；年龄35岁以下；招商加盟经验优先；不要频繁跳槽",
	)

	job = result["job"]
	all_criteria = "|".join(
		[
			*job["criteria"]["must_have"],
			*job["criteria"]["nice_to_have"],
			*job["criteria"]["reject_if"],
			*job["criteria"]["risk_signals"],
		]
	)
	assert result["analysis_source"] == "rules"
	assert "电话销售经验" in all_criteria
	assert "年龄" not in all_criteria
	assert any("敏感人口属性" in warning for warning in result["warnings"])


def test_agent_falls_back_to_rules_when_configured_ai_request_fails(tmp_path) -> None:
	"""模型认证或网络故障不能使岗位标准保存永久停在失败状态。"""
	workspace = RecruitingWorkspace(tmp_path)

	result = JobStandardAgent(ai_service=_UnavailableAI()).create_job(
		workspace,
		requirements="Java；必须掌握高并发；有分布式系统经验优先",
	)

	assert result["analysis_source"] == "rules"
	assert result["job"]["name"] == "Java"
	assert result["job"]["status"] == "published"


def test_agent_applies_hard_condition_overrides_when_updating_job(tmp_path) -> None:
	"""设置面板给出的硬条件必须覆盖 Agent 建议，并可更新既有岗位。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="销售顾问", criteria_text="必须有销售经验")
	agent = JobStandardAgent(ai_service=_StaticAI())

	result = agent.update_job(
		workspace,
		job_id=created["job"]["job_id"],
		requirements="招一位销售顾问，有电话销售经验优先",
		hard_conditions={"city": "上海", "salary_range": "12-18K", "min_experience_years": 3},
	)

	job = result["job"]
	assert job["city"] == "上海"
	assert job["salary_range"] == "12-18K"
	assert job["min_experience_years"] == 3
	assert job["status"] == "published"


def test_agent_supplements_boss_job_without_discarding_synced_base_fields(tmp_path) -> None:
	"""自然语言是对 BOSS 镜像岗位的补充，不能清空同步来的基础条件。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(
		name="ToB 销售顾问",
		city="广州",
		salary_range="10-20K",
		education_requirement="大专及以上",
		criteria_text="必须有客户开发经验",
	)
	job_id = created["job"]["job_id"]
	boss_job = workspace.store.get_job(job_id)
	assert boss_job is not None
	boss_job.source = "boss"
	workspace.store.update_job(boss_job)

	result = JobStandardAgent().update_job(
		workspace,
		job_id=job_id,
		requirements="电话销售经验优先；不要频繁跳槽",
	)

	job = result["job"]
	assert job["name"] == "ToB 销售顾问"
	assert job["city"] == "广州"
	assert job["salary_range"] == "10-20K"
	assert job["education_requirement"] == "大专及以上"
	assert "客户开发经验" in job["criteria"]["must_have"]
	assert "电话销售经验" in job["criteria"]["nice_to_have"]


def test_agent_keeps_boss_job_name_when_ai_returns_a_different_name(tmp_path) -> None:
	"""BOSS 岗位名称是同步事实源，AI 补充标准不得擅自改名。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(name="BOSS 商务顾问", criteria_text="必须有客户开发经验")
	job_id = created["job"]["job_id"]
	boss_job = workspace.store.get_job(job_id)
	assert boss_job is not None
	boss_job.source = "boss"
	boss_job.platform_job_id = "boss-sales-1"
	workspace.store.update_job(boss_job)

	result = JobStandardAgent(ai_service=_StaticAI()).update_job(
		workspace,
		job_id=job_id,
		requirements="电话销售经验优先；不要频繁跳槽",
	)

	assert result["job"]["name"] == "BOSS 商务顾问"


def test_agent_applies_reviewed_rules_without_changing_boss_synced_fields(tmp_path) -> None:
	"""人工审核后的四类规则只能更新筛选标准，不能改写 BOSS 同步基础信息。"""
	workspace = RecruitingWorkspace(tmp_path)
	created = workspace.create_job(
		name="Java",
		city="广州",
		salary_range="150-200 元/天",
		education_requirement="本科",
		min_experience_years=0,
		criteria_text="必须：Java",
		status="published",
	)
	job_id = created["job"]["job_id"]
	boss_job = workspace.store.get_job(job_id)
	assert boss_job is not None
	boss_job.source = "boss"
	boss_job.platform_job_id = "boss-java"
	workspace.store.update_job(boss_job)

	result = JobStandardAgent().apply_rules(
		workspace,
		job_id=job_id,
		rules={
			"must_have": ["掌握 Spring"],
			"nice_to_have": ["分布式系统经验"],
			"reject_if": ["不能接受代码评审"],
			"risk_signals": ["短期多次换工作"],
		},
	)

	job = result["job"]
	assert job["name"] == "Java"
	assert job["city"] == "广州"
	assert job["salary_range"] == "150-200 元/天"
	assert job["education_requirement"] == "本科"
	assert job["min_experience_years"] == 0
	assert job["status"] == "published"
	assert job["criteria"] == {
		"must_have": ["掌握 Spring"],
		"nice_to_have": ["分布式系统经验"],
		"reject_if": ["不能接受代码评审"],
		"risk_signals": ["短期多次换工作"],
	}
