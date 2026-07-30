"""`boss hr download-resume` 的命令层契约测试。

只覆盖 MVP 边界：单个候选人、用户显式触发、落盘到本地文件。
不涉及批量、自动分析、自动沟通与 MCP 暴露。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportError
from boss_agent_cli.main import cli


def _view_geek_response() -> dict:
	"""构造 view_geek 的原始响应（parse_resume 的输入形状）。"""
	return {
		"code": 0,
		"zpData": {
			"geekDetailInfo": {
				"geekBaseInfo": {
					"name": "张三",
					"gender": 1,
					"ageDesc": "28岁",
					"degreeCategory": "本科",
					"workYearDesc": "5年",
					"activeTimeDesc": "刚刚活跃",
					"large": "https://img.example.com/a.png",
				},
				"showExpectPosition": {"positionName": "后端工程师", "salaryDesc": "25-35K", "locationName": "上海"},
				"geekWorkExpList": [{
					"company": "示例科技",
					"positionName": "后端工程师",
					"department": "平台组",
					"startYearMonStr": "2020.03",
					"endYearMonStr": "2024.06",
					"workYearDesc": "4年3个月",
					"responsibility": "负责订单系统重构",
					"workPerformance": "核心接口 QPS 提升 3 倍",
					"workEmphasis": "Python#&#MySQL",
				}],
				"geekProjExpList": [],
				"geekEduExpList": [{
					"school": "示例大学",
					"major": "计算机科学与技术",
					"degreeDesc": "本科",
					"startYearMonStr": "2015.09",
					"endYearMonStr": "2019.06",
				}],
				"geekCertificationList": [],
				"jobCompetitive": {"tips": []},
			},
		},
	}


def _ctx_mock(mock_cls):
	"""复用仓库既有的招聘者平台适配器桩写法。"""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") or response.get("data")
	return instance


def _invoke(data_dir: Path, *args: str):
	runner = CliRunner()
	return runner.invoke(cli, ["--data-dir", str(data_dir), "--role", "recruiter", *args])


_MODULE = "boss_agent_cli.commands.recruiter.download_resume"


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_writes_markdown_and_returns_metadata(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = _view_geek_response()

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--job-id", "job_1", "--security-id", "sec_1")

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "recruiter-download-resume"
	data = parsed["data"]
	assert data["geek_id"] == "geek_001"
	assert data["candidate_name"] == "张三"
	assert data["filename"] == "张三-geek_001.md"
	assert data["bytes_written"] > 0
	assert "basic" in data["sections"]
	written = Path(data["path"])
	assert written.is_file()
	assert written.parent == tmp_path / "recruiter" / "resumes"
	assert "示例科技" in written.read_text(encoding="utf-8")
	platform.view_geek.assert_called_once_with("geek_001", "job_1", security_id="sec_1")


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_envelope_carries_no_resume_body(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	"""简历正文只进文件，不进 stdout 信封，避免被日志/管道二次留存。"""
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = _view_geek_response()

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--job-id", "job_1", "--security-id", "sec_1")

	assert result.exit_code == 0
	assert "示例科技" not in result.output
	assert "负责订单系统重构" not in result.output


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_honors_explicit_output(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = _view_geek_response()
	target = tmp_path / "exports" / "zhangsan.md"

	result = _invoke(
		tmp_path, "hr", "download-resume", "geek_001",
		"--job-id", "job_1", "--security-id", "sec_1",
		"--output", str(target),
	)

	assert result.exit_code == 0
	assert json.loads(result.output)["data"]["path"] == str(target)
	assert target.is_file()


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_honors_output_dir(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = _view_geek_response()

	result = _invoke(
		tmp_path, "hr", "download-resume", "geek_001",
		"--job-id", "job_1", "--security-id", "sec_1",
		"--output-dir", str(tmp_path / "inbox"),
	)

	assert result.exit_code == 0
	assert Path(json.loads(result.output)["data"]["path"]).parent == tmp_path / "inbox"


# ── 缺参数 ──────────────────────────────────────────────────────────


def _assert_invalid_param(result, *, message: str) -> None:
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["command"] == "recruiter-download-resume"
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["error"]["message"] == message
	assert parsed["error"]["recoverable"] is False


_MISSING_PARAM_MESSAGE = "下载在线简历需要 geek_id 参数以及 --job-id 和 --security-id"


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_requires_security_id(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	platform = _ctx_mock(mock_platform_cls)

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--job-id", "job_1")

	_assert_invalid_param(result, message=_MISSING_PARAM_MESSAGE)
	platform.view_geek.assert_not_called()


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_requires_job_id(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	_ctx_mock(mock_platform_cls)

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--security-id", "sec_1")

	_assert_invalid_param(result, message=_MISSING_PARAM_MESSAGE)


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_requires_geek_id(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	_ctx_mock(mock_platform_cls)

	result = _invoke(tmp_path, "hr", "download-resume", "--job-id", "job_1", "--security-id", "sec_1")

	_assert_invalid_param(result, message=_MISSING_PARAM_MESSAGE)


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_rejects_conflicting_output_options(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	_ctx_mock(mock_platform_cls)

	result = _invoke(
		tmp_path, "hr", "download-resume", "geek_001",
		"--job-id", "job_1", "--security-id", "sec_1",
		"--output", str(tmp_path / "a.md"),
		"--output-dir", str(tmp_path / "dir"),
	)

	_assert_invalid_param(result, message="--output 与 --output-dir 互斥，只能指定一个")


# ── 平台错误与写入失败 ──────────────────────────────────────────────


@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_reports_platform_error(mock_auth_cls, mock_platform_cls, tmp_path: Path):
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = {"code": 9, "message": "too fast"}
	platform.parse_error.return_value = ("RATE_LIMITED", "too fast")

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--job-id", "job_1", "--security-id", "sec_1")

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "候选人简历获取失败，请稍后重试"
	assert parsed["error"]["recoverable"] is True
	assert not (tmp_path / "recruiter").exists()


@patch(f"{_MODULE}.export_candidate_resume")
@patch(f"{_MODULE}.get_recruiter_platform_instance")
@patch(f"{_MODULE}.AuthManager")
def test_download_resume_maps_write_failure_to_export_failed(
	mock_auth_cls, mock_platform_cls, mock_export, tmp_path: Path,
):
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = _view_geek_response()
	mock_export.side_effect = ResumeExportError("写入简历文件失败: /x/y.md (permission denied)")

	result = _invoke(tmp_path, "hr", "download-resume", "geek_001", "--job-id", "job_1", "--security-id", "sec_1")

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["command"] == "recruiter-download-resume"
	assert parsed["error"]["code"] == "EXPORT_FAILED"
	assert "写入简历文件失败" in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "确认输出目录存在且可写，或改用 --output 指定其他路径"


# ── 发现性与暴露面 ──────────────────────────────────────────────────


def test_schema_exposes_download_resume_subcommand():
	from boss_agent_cli.commands.schema import SCHEMA_DATA

	subcommands = SCHEMA_DATA["commands"]["hr"]["subcommands"]
	assert "download-resume" in subcommands
	assert "受限" in subcommands["download-resume"]


def test_download_resume_registered_under_hr_group():
	from boss_agent_cli.commands.register import hr_group

	assert "download-resume" in hr_group.commands


def test_download_resume_is_not_exposed_as_mcp_tool():
	"""MVP 明确不给 Agent 自动调用入口：MCP 侧不得出现该工具。"""
	from boss_agent_cli.mcp_tools import TOOLS

	names = {tool.name for tool in TOOLS}
	assert "boss_hr_download_resume" not in names
	assert not any("download_resume" in name for name in names)
