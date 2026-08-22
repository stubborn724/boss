"""`boss hr batch-download-resume` 的命令层契约测试。

批量导出是唯一会连续访问平台的招聘者命令，因此这里除了信封契约，还要守住
三条护栏：默认模式必须阻断、简历正文不得进 stdout、以及绝不向候选人索要附件。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli

_MODULE = "boss_agent_cli.commands.recruiter.batch_download_resume"


def _resume_response() -> dict:
	return {
		"code": 0,
		"zpData": {
			"geekDetailInfo": {
				"geekBaseInfo": {"name": "张三", "gender": 1},
				"geekWorkExpList": [{"company": "私密公司", "responsibility": "私密正文"}],
			},
		},
	}


def _friend_detail(friend_ids: list[int]) -> dict:
	return {
		"code": 0,
		"zpData": {
			"friendList": [
				{
					"uid": friend_id,
					"encryptUid": f"geek-{friend_id}",
					"encryptJobId": "job-1",
					"securityId": "sid-1",
					"name": f"候选人{friend_id}",
				}
				for friend_id in friend_ids
			]
		},
	}


def _platform(mock_cls, *, friend_ids: tuple[int, ...] = (41, 42)):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.is_success.side_effect = lambda response: response.get("code") == 0
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") or response.get("data")
	instance.parse_error.side_effect = lambda response: ("UNKNOWN", "")
	pages = {
		1: {
			"code": 0,
			"zpData": {"result": [{"friendId": friend_id, "name": f"候选人{friend_id}"} for friend_id in friend_ids]},
		},
	}
	instance.friend_list.side_effect = lambda page=1, label_id=0: pages.get(page, {"code": 0, "zpData": {"result": []}})
	instance.friend_detail.side_effect = _friend_detail
	instance.view_geek.return_value = _resume_response()
	instance.exchange_content.return_value = {"code": 0, "zpData": {}}
	return instance


def _invoke(data_dir: Path, *args: str, research: bool = True):
	config = data_dir / "config.json"
	if research:
		data_dir.mkdir(parents=True, exist_ok=True)
		config.write_text(json.dumps({"operating_mode": "research"}), encoding="utf-8")
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--role", "recruiter", *args])


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_batch_download_resume_exports_each_conversation_without_requesting_attachments(
	mock_auth_cls, mock_platform_cls, tmp_path: Path,
):
	platform = _platform(mock_platform_cls)

	result = _invoke(tmp_path, "hr", "batch-download-resume", "--limit", "2", "--output-dir", str(tmp_path / "out"))

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "recruiter-batch-download-resume"
	data = payload["data"]
	assert data["source"] == "conversation"
	assert data["mode"] == "export"
	assert data["processed"] == 2
	assert data["succeeded"] == 2
	assert [item["online_filename"] for item in data["items"]] == ["张三-geek-41.md", "张三-geek-42.md"]
	assert "私密公司" not in result.output
	assert "私密正文" not in result.output
	platform.exchange_request_by_friend.assert_not_called()


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_batch_scan_only_reports_attachment_availability_without_writing_files(
	mock_auth_cls, mock_platform_cls, tmp_path: Path,
):
	"""扫描模式只回答谁能导 PDF，不落盘也不读在线简历。"""
	platform = _platform(mock_platform_cls)
	platform.exchange_content.side_effect = lambda uid: (
		{"code": 0, "zpData": {"resume": {"resumeUrl": "https://cdn.example.com/a.pdf", "resumeName": "a.pdf"}}}
		if uid == 42
		else {"code": 0, "zpData": {}}
	)
	output_dir = tmp_path / "out"

	result = _invoke(
		tmp_path, "hr", "batch-download-resume", "--limit", "2", "--scan-only", "--output-dir", str(output_dir),
	)

	assert result.exit_code == 0
	data = json.loads(result.output)["data"]
	assert data["mode"] == "scan"
	assert [item["attachment_status"] for item in data["items"]] == ["no_attachment", "can_export_pdf"]
	assert data["with_attachment"] == 1
	assert not output_dir.exists()
	platform.view_geek.assert_not_called()
	platform.friend_detail.assert_called_once_with([41, 42])


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_batch_scan_persists_attachment_index_for_the_console_badges(
	mock_auth_cls, mock_platform_cls, tmp_path: Path,
):
	"""扫描结论要落本地索引，控制台刷新后徽标才不会消失。"""
	from boss_agent_cli.commands.recruiter.attachment_index import AttachmentIndex

	platform = _platform(mock_platform_cls)
	platform.exchange_content.side_effect = lambda uid: (
		{"code": 0, "zpData": {"resume": {"resumeUrl": "https://cdn.example.com/a.pdf"}}}
		if uid == 42
		else {"code": 0, "zpData": {}}
	)

	_invoke(tmp_path, "hr", "batch-download-resume", "--limit", "2", "--scan-only")

	assert AttachmentIndex.for_data_dir(tmp_path).read() == {41: "no_attachment", 42: "can_export_pdf"}


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_batch_download_resume_stops_when_the_daily_quota_is_exhausted(
	mock_auth_cls, mock_platform_cls, tmp_path: Path,
):
	"""额度耗尽必须停批并给出可执行的恢复动作，而不是继续刷平台。"""
	_platform(mock_platform_cls)
	data_dir = tmp_path
	data_dir.mkdir(parents=True, exist_ok=True)
	(data_dir / "config.json").write_text(
		json.dumps({"operating_mode": "research", "automation": {"daily_action_quota": 1}}), encoding="utf-8",
	)

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(data_dir), "--role", "recruiter", "hr", "batch-download-resume", "--limit", "2"],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["data"]["processed"] == 1
	assert payload["data"]["stopped_reason"] == "daily_quota"
	assert "额度" in payload["hints"]["recovery_action"]


def test_batch_download_resume_is_blocked_in_default_assisted_mode(tmp_path: Path) -> None:
	"""批量处理候选人个人数据在默认模式必须阻断。

	conftest 会把测试进程的默认模式抬到 research，所以这里显式写入 assisted
	配置，确保断言真的走到合规门禁而不是被全局默认放行。
	"""
	tmp_path.mkdir(parents=True, exist_ok=True)
	(tmp_path / "config.json").write_text(json.dumps({"operating_mode": "assisted"}), encoding="utf-8")

	result = _invoke(tmp_path, "hr", "batch-download-resume", "--limit", "2", research=False)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "COMPLIANCE_BLOCKED"


def test_batch_download_resume_is_registered_and_not_exposed_as_mcp_tool() -> None:
	"""能力必须在 schema 里可见，但批量不得进入 assisted-only 的 MCP。"""
	from boss_agent_cli.commands.register import hr_group
	from boss_agent_cli.commands.schema import SCHEMA_DATA
	from boss_agent_cli.mcp_tools import TOOLS

	assert "batch-download-resume" in hr_group.commands
	assert "batch-download-resume" in SCHEMA_DATA["commands"]["hr"]["subcommands"]
	assert "boss_hr_batch_download_resume" not in {tool.name for tool in TOOLS}


def test_batch_download_resume_limit_is_capped() -> None:
	"""数量上限由 Click 校验，避免有人用一条命令扫全平台。"""
	result = CliRunner().invoke(cli, ["--role", "recruiter", "hr", "batch-download-resume", "--limit", "500"])

	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
