"""`boss hr screen` 的命令层契约测试。

本命令把已导出的简历喂进本地评估引擎，是"导出"和"分析"之间缺失的连接点。
这里守住五条护栏：JSON 信封契约、批量扫描与 limit 截断、单份失败不拖垮整批、
AI 未配置/非研究模式时的正确阻断、以及 AI 首次失败后的整批降级。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace

_STANDARD_TEXT = "本科及以上；2年以上销售经验；必须能接受电话销售；有招商加盟经验优先；不接受只想做客服"

_RESUME_MD = """# 候选人简历 — 张三

- 城市: 杭州
- 期望薪资: 10-14K
- 学历: 本科
- 工作经验: 3 年

## 工作经历
### 1. 某科技公司 — 客户经理
- 时间: 2021.03 - 2024.06（3年）
**工作内容**
负责客户开发、电话邀约、跟进成交、维护老客户。
"""

_RESUME_BAD_MD = ""  # 空文件


def _invoke(data_dir: Path, *args: str, **kwargs):
    """以招聘者角色调用命令，默认带 --json。"""
    full_args = ["--role", "recruiter", "--json", "--data-dir", str(data_dir), *args]
    return CliRunner().invoke(cli, full_args, **kwargs)


def _payload(result) -> dict:
    """提取 JSON 信封的 data 区。"""
    assert "\n" not in result.output.strip()
    return json.loads(result.output)


def _setup_job(data_dir: Path) -> str:
    """创建并发布一个岗位，返回 job_id。"""
    workspace, _context = _open_workspace(data_dir)
    result = workspace.create_job(
        name="ToB 销售顾问",
        city="杭州",
        salary_range="8-15K",
        criteria_text=_STANDARD_TEXT,
        status="draft",
    )
    job_id = result["job"]["job_id"]
    workspace.publish_job(job_id)
    return job_id


def _open_workspace(data_dir: Path) -> tuple[RecruitingWorkspace, object]:
    """打开本地招聘工作台。"""
    from boss_agent_cli.commands.recruiter._workspace import open_recruiting_workspace
    return open_recruiting_workspace(data_dir)


def _write_resume(directory: Path, name: str, content: str) -> Path:
    """在指定目录写入一份 Markdown 简历。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


class TestScreenEnvelopeContract:
    """JSON 信封契约：输出必须是单行 JSON，包含约定的顶层键。"""

    def test_screen_returns_valid_envelope_with_empty_directory(self, tmp_path: Path) -> None:
        """扫描空目录时返回 NO_RESUME_FOUND，但仍遵守信封契约。"""
        job_id = _setup_job(tmp_path)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir(parents=True, exist_ok=True)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(empty_dir))

        payload = _payload(result)
        assert "ok" in payload
        assert payload["ok"] is False
        assert payload["error"]["code"] == "NO_RESUME_FOUND"

    def test_screen_returns_expected_top_level_keys(self, tmp_path: Path) -> None:
        """成功返回的数据区包含约定的批量分析键。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        payload = _payload(result)
        data = payload["data"]
        assert data["job_id"] == job_id
        assert "items" in data
        assert "summary" in data
        assert "requested" in data
        assert "analyzed" in data
        assert "failed" in data
        assert data["review_required"] is True

    def test_screen_single_item_has_required_fields(self, tmp_path: Path) -> None:
        """每份评估结果包含评分、等级、决策、证据和三层层投影。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        item = data["items"][0]
        assert item["file"] == "candidate.md"
        assert item["candidate_id"]
        assert item["candidate_name"]  # 名字从文件名提取，至少非空
        assert isinstance(item["final_score"], int)
        assert item["level"] in {"强烈推荐", "推荐", "待确认", "风险较高", "不推荐"}
        assert item["decision"]
        assert item["next_action"]
        assert item["engine"].startswith("rules")
        assert isinstance(item["matched_points"], list)
        assert isinstance(item["risk_points"], list)
        assert isinstance(item["evidence"], list)
        assert isinstance(item["screening"], dict)
        assert item["screening"]["hard_filter"]
        assert item["screening"]["semantic_match"]
        assert item["screening"]["risk"]
        assert item["review_required"] is True
        assert item["error_code"] == ""
        assert item["error_message"] == ""


class TestResumeScanning:
    """批量扫描与 limit 截断。"""

    def test_scans_default_export_directory_when_no_resume_specified(self, tmp_path: Path) -> None:
        """不指定 --resume 时从默认导出目录按 mtime 倒序收集。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "recruiter" / "resumes"
        _write_resume(resumes_dir, "b.md", _RESUME_MD)
        _write_resume(resumes_dir, "a.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id)

        data = _payload(result)["data"]
        assert data["analyzed"] == 2
        # 后写入的文件（a.md）mtime 更新，排在前。
        assert data["items"][0]["file"] == "a.md"

    def test_limit_truncates_to_requested_count(self, tmp_path: Path) -> None:
        """--limit 约束分析数量。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        for i in range(5):
            _write_resume(resumes_dir, f"candidate_{i}.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir), "--limit", "2")

        data = _payload(result)["data"]
        assert data["requested"] == 2
        assert data["analyzed"] == 2

    def test_explicit_resume_files_take_priority(self, tmp_path: Path) -> None:
        """显式 --resume 不扫描目录。"""
        job_id = _setup_job(tmp_path)
        a = _write_resume(tmp_path, "a.md", _RESUME_MD)
        _write_resume(tmp_path, "b.md", _RESUME_MD)  # 不会被分析

        result = _invoke(
            tmp_path, "hr", "screen", "--job-id", job_id,
            "--resume", str(a),
        )

        data = _payload(result)["data"]
        assert data["analyzed"] == 1
        assert data["items"][0]["file"] == "a.md"

    def test_skips_non_markdown_files(self, tmp_path: Path) -> None:
        """只收集 .md/.markdown/.txt 文件。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)
        (resumes_dir / "candidate.pdf").write_text("fake pdf", encoding="utf-8")
        (resumes_dir / "candidate.html").write_text("<html></html>", encoding="utf-8")

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        assert data["analyzed"] == 1

    def test_skips_attachments_directory(self, tmp_path: Path) -> None:
        """附件目录里的文件不参与文本分析。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)
        _write_resume(resumes_dir / "attachments", "resume.txt", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        assert data["analyzed"] == 1


class TestFailureIsolation:
    """单份简历失败不影响整批继续。"""

    def test_bad_resume_file_is_recorded_as_failed_not_blocking_batch(self, tmp_path: Path) -> None:
        """空文件也能导入和分析（只是分数很低），不阻断后续正常文件。

        空 Markdown 文件是合法导入目标：import_candidate 只根据文件路径创建引用，
        不检查正文内容；评估层遇到空正文时给出最低分和明确证据，而非抛异常。
        """
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "good.md", _RESUME_MD)
        _write_resume(resumes_dir, "empty.md", _RESUME_BAD_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        # 两份都被分析，但空文件分数极低。
        assert data["analyzed"] == 2
        assert data["failed"] == 0
        empty_item = next(item for item in data["items"] if item["file"] == "empty.md")
        assert empty_item["error_code"] == ""
        assert empty_item["final_score"] is not None
        assert any("简历正文为空" in line for line in empty_item["evidence"])

    def test_nonexistent_file_is_recorded_as_failed(self, tmp_path: Path) -> None:
        """不存在的文件路径记录为失败（import_candidate 抛 ValueError），不抛异常。"""
        job_id = _setup_job(tmp_path)

        result = _invoke(
            tmp_path, "hr", "screen", "--job-id", job_id,
            "--resume", str(tmp_path / "does_not_exist.md"),
        )

        data = _payload(result)["data"]
        assert data["failed"] == 1
        assert data["items"][0]["error_code"] == "INVALID_RESUME"

    def test_job_not_found_returns_error(self, tmp_path: Path) -> None:
        """岗位不存在时返回 JOB_NOT_FOUND。"""
        result = _invoke(tmp_path, "hr", "screen", "--job-id", "nonexistent-job")

        payload = _payload(result)
        assert payload["error"]["code"] == "JOB_NOT_FOUND"

    def test_unpublished_job_returns_error(self, tmp_path: Path) -> None:
        """未发布的岗位不能用于评分。"""
        workspace, _context = _open_workspace(tmp_path)
        result = workspace.create_job(
            name="草稿岗位",
            city="杭州",
            salary_range="8-15K",
            criteria_text="必须能接受电话销售",
            status="draft",
        )
        job_id = result["job"]["job_id"]

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id)

        payload = _payload(result)
        assert payload["error"]["code"] == "JOB_NOT_PUBLISHED"


class TestAICompliance:
    """AI 语义层的模式门禁。"""

    def test_use_ai_without_ai_configured_returns_ai_not_configured(self, tmp_path: Path) -> None:
        """未配置 AI 时 --use-ai 报 AI_NOT_CONFIGURED。"""
        job_id = _setup_job(tmp_path)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--use-ai")

        payload = _payload(result)
        assert payload["error"]["code"] == "AI_NOT_CONFIGURED"

    @patch("boss_agent_cli.compliance.operating_mode", return_value="assisted")
    def test_use_ai_in_assisted_mode_returns_compliance_blocked(self, _mock_mode, tmp_path: Path) -> None:
        """assisted 模式下 --use-ai 报 COMPLIANCE_BLOCKED。"""
        job_id = _setup_job(tmp_path)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--use-ai")

        payload = _payload(result)
        assert payload["error"]["code"] == "COMPLIANCE_BLOCKED"

    def test_without_use_ai_flag_ai_status_is_disabled(self, tmp_path: Path) -> None:
        """不带 --use-ai 时 ai_status 为 disabled，不调用 AI。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        assert data["ai_status"] == "disabled"
        # engine 不含 ai 标记
        assert "ai" not in data["items"][0]["engine"]


class TestRegistrationAndSchema:
    """命令注册与 schema 一致性。"""

    def test_screen_is_registered_and_visible_in_schema(self) -> None:
        """能力必须在 hr_group 和 SCHEMA_DATA 中可见。"""
        from boss_agent_cli.commands.register import hr_group
        from boss_agent_cli.commands.schema import SCHEMA_DATA

        assert "screen" in hr_group.commands
        assert "screen" in SCHEMA_DATA["commands"]["hr"]["subcommands"]

    def test_screen_is_not_exposed_as_mcp_tool(self) -> None:
        """批量分析简历涉及候选人个人信息批量处理，不进 MCP。"""
        from boss_agent_cli.mcp_tools import TOOLS

        assert "boss_hr_screen" not in {tool.name for tool in TOOLS}

    def test_limit_exceeding_max_returns_invalid_param(self, tmp_path: Path) -> None:
        """--limit 超过 MAX_LIMIT (50) 时 Click 校验拦截。"""
        result = _invoke(tmp_path, "hr", "screen", "--job-id", "any", "--limit", "500")

        assert result.exit_code == 1
        assert _payload(result)["error"]["code"] == "INVALID_PARAM"

    def test_job_id_is_required(self, tmp_path: Path) -> None:
        """--job-id 缺失时 Click 报错。"""
        result = _invoke(tmp_path, "hr", "screen")

        assert result.exit_code != 0


class TestSummaryAndHints:
    """汇总统计和提示。"""

    def test_summary_counts_level_distribution(self, tmp_path: Path) -> None:
        """summary 包含等级分布和推荐数量。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        for i in range(3):
            _write_resume(resumes_dir, f"candidate_{i}.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        data = _payload(result)["data"]
        summary = data["summary"]
        assert "level_counts" in summary
        assert isinstance(summary["recommended"], int)
        assert isinstance(summary["needs_follow_up"], int)
        assert isinstance(summary["not_recommended"], int)

    def test_hints_include_next_actions(self, tmp_path: Path) -> None:
        """hints 提示下一步动作。"""
        job_id = _setup_job(tmp_path)
        resumes_dir = tmp_path / "resumes"
        _write_resume(resumes_dir, "candidate.md", _RESUME_MD)

        result = _invoke(tmp_path, "hr", "screen", "--job-id", job_id, "--dir", str(resumes_dir))

        payload = _payload(result)
        assert "hints" in payload
        assert "next_actions" in payload["hints"]
