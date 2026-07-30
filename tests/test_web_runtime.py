"""本地招聘控制台运行时的行为测试。"""

from pathlib import Path
from threading import Event

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult
from boss_agent_cli.web.runtime import LocalConsoleRuntime


def test_login_is_single_flight_and_exposes_no_authentication_material(tmp_path: Path) -> None:
	"""重复点击登录只能启动一个官方页面任务，状态也不得包含凭据。"""
	entered = Event()
	release = Event()
	calls = 0

	def login() -> None:
		nonlocal calls
		calls += 1
		entered.set()
		release.wait(timeout=1)

	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=login,
		has_saved_login=lambda: False,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	assert runtime.start_login()["state"] == "running"
	assert entered.wait(timeout=1)
	assert runtime.start_login()["state"] == "running"
	assert calls == 1
	assert "token" not in runtime.status()["login"]

	release.set()
	runtime.wait_for_idle(timeout=1)
	assert runtime.status()["login"]["state"] == "succeeded"


def test_download_is_blocked_outside_research_mode() -> None:
	"""控制台不得以页面操作绕过既有 operating_mode 合规边界。"""
	runtime = LocalConsoleRuntime(
		operating_mode="assisted",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应下载")),
	)

	result = runtime.start_download(geek_id="g", job_id="j", security_id="s", output=None, output_dir=None)

	assert result == {
		"state": "blocked",
		"error": {"code": "COMPLIANCE_BLOCKED", "message": "下载在线简历需要显式启用 research 模式"},
	}


def test_download_status_contains_only_export_metadata(tmp_path: Path) -> None:
	"""下载完成后的任务状态只能保存文件元数据，不能保留简历正文。"""
	expected = ResumeExportResult(
		path=tmp_path / "candidate.md",
		filename="candidate.md",
		bytes_written=12,
		candidate_name="张三",
		geek_id="g",
		exported_at="2026-07-30T12:00:00",
		sections=["basic"],
	)
	runtime = LocalConsoleRuntime(
		operating_mode="research",
		login_in_browser=lambda: None,
		has_saved_login=lambda: True,
		download_resume=lambda **kwargs: expected,
	)

	assert runtime.start_download(geek_id="g", job_id="j", security_id="s", output=None, output_dir=None)["state"] == "running"
	runtime.wait_for_idle(timeout=1)

	result = runtime.status()["download"]
	assert result["state"] == "succeeded"
	assert result["result"] == {
		"geek_id": "g",
		"candidate_name": "张三",
		"path": str(tmp_path / "candidate.md"),
		"filename": "candidate.md",
		"bytes_written": 12,
		"sections": ["basic"],
		"exported_at": "2026-07-30T12:00:00",
	}
	assert "简历正文" not in str(result)
