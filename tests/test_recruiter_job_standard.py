"""`boss hr job-standard` / `hr job-standards` 的命令层契约测试。

岗位标准是后续所有评分的依据，因此这里守住三条护栏：预览绝不写工作区、必答项
不齐时必须以 ``JOB_NOT_READY`` 追问而不是静默发布、敏感人口属性不进标准。
"""

import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli

_STANDARD_TEXT = "本科及以上；2年以上销售经验；必须能接受电话销售；有招商加盟经验优先；不接受只想做客服"


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def _payload(result) -> dict:
	assert "\n" not in result.output.strip()
	return json.loads(result.output)


def test_preview_parses_criteria_without_touching_workspace(tmp_path: Path) -> None:
	"""不带 --save 时只返回解析结果，本地工作区不应被创建或写入。"""
	result = _invoke(
		tmp_path, "hr", "job-standard", "--name", "ToB 销售顾问",
		"--city", "杭州", "--salary", "8-15K", "--text", _STANDARD_TEXT,
	)

	assert result.exit_code == 0
	data = _payload(result)["data"]
	assert data["saved"] is False
	assert data["criteria"]["must_have"] == ["能接受电话销售"]
	assert data["criteria"]["nice_to_have"] == ["招商加盟经验"]
	assert data["criteria"]["reject_if"] == ["只想做客服"]
	assert data["structured"]["education_requirement"] == "本科及以上"
	assert data["structured"]["min_experience_years"] == 2
	assert data["readiness"]["ready"] is True
	assert data["review_required"] is True
	assert _invoke(tmp_path, "hr", "job-standards")
	assert json.loads(_invoke(tmp_path, "hr", "job-standards").output)["data"]["total"] == 0


def test_missing_required_fields_return_clarification_questions(tmp_path: Path) -> None:
	"""必答项不齐时保持草稿，并把该问 HR 的问题放进信封，供 Agent 直接转述。"""
	result = _invoke(tmp_path, "hr", "job-standard", "--name", "销售顾问", "--text", "必须能接受电话销售", "--save")

	assert result.exit_code == 1
	payload = _payload(result)
	assert payload["error"]["code"] == "JOB_NOT_READY"
	hints = payload["hints"]
	assert "city" in hints["missing_required_fields"]
	assert "salary_range" in hints["missing_required_fields"]
	assert any("工作城市" in question for question in hints["clarification_questions"])

	listed = json.loads(_invoke(tmp_path, "hr", "job-standards").output)["data"]
	assert listed["total"] == 1
	assert listed["published"] == 0
	assert listed["jobs"][0]["status"] == "draft"


def test_save_publishes_job_once_required_fields_are_present(tmp_path: Path) -> None:
	"""必答项齐全时才发布，并返回可直接用于 hr screen 的岗位标识。"""
	result = _invoke(
		tmp_path, "hr", "job-standard", "--name", "ToB 销售顾问",
		"--city", "杭州", "--salary", "8-15K", "--text", _STANDARD_TEXT, "--save",
	)

	assert result.exit_code == 0
	data = _payload(result)["data"]
	assert data["saved"] is True
	assert data["job"]["status"] == "published"
	assert data["job"]["job_id"]

	listed = json.loads(_invoke(tmp_path, "hr", "job-standards").output)["data"]
	assert listed["published"] == 1
	assert listed["jobs"][0]["ready"] is True


def test_sensitive_conditions_are_dropped_with_a_warning(tmp_path: Path) -> None:
	"""性别、婚育等条件不得进入岗位标准，并且必须回显忽略原因。"""
	result = _invoke(
		tmp_path, "hr", "job-standard", "--name", "销售顾问",
		"--city", "杭州", "--salary", "8-15K",
		"--text", "必须能接受电话销售；只要未婚女性；不接受只想做客服",
	)

	data = _payload(result)["data"]
	criteria = json.dumps(data["criteria"], ensure_ascii=False)
	assert "未婚" not in criteria
	assert any("敏感人口属性" in warning for warning in data["warnings"])


def test_update_requires_existing_job(tmp_path: Path) -> None:
	"""指定不存在的 --job-id 时给出可恢复的错误，而不是新建一个岗位。"""
	result = _invoke(
		tmp_path, "hr", "job-standard", "--name", "销售顾问", "--job-id", "job-missing",
		"--city", "杭州", "--salary", "8-15K", "--text", _STANDARD_TEXT, "--save",
	)

	assert result.exit_code == 1
	payload = _payload(result)
	assert payload["error"]["code"] == "JOB_NOT_FOUND"
	assert payload["error"]["recoverable"] is True


def test_job_standard_commands_are_registered_and_absent_from_mcp() -> None:
	"""能力必须在 schema 可见；本地分析命令不进入 MCP 工具面。"""
	from boss_agent_cli.commands.register import hr_group
	from boss_agent_cli.commands.schema import SCHEMA_DATA
	from boss_agent_cli.mcp_tools import TOOLS

	subcommands = SCHEMA_DATA["commands"]["hr"]["subcommands"]

	assert {"job-standard", "job-standards"} <= set(hr_group.commands)
	assert {"job-standard", "job-standards"} <= set(subcommands)
	assert not {tool.name for tool in TOOLS} & {"hr_job_standard", "hr_job_standards"}
