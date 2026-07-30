"""单份候选人简历下载服务的契约测试。

服务层负责连接平台读取、简历解析与本地导出；CLI 和 Web 入口只负责各自的
参数校验、合规控制及输出呈现。测试使用注入的轻量替身，确保该服务不依赖
Click 上下文或真实平台登录状态。
"""

from pathlib import Path

import pytest

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult
from boss_agent_cli.commands.recruiter.resume_download_service import (
	ResumeDownloadExportError,
	ResumeDownloadPlatformError,
	ResumeDownloadService,
)
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportError


class _SuccessfulPlatform:
	"""记录调用参数的最小平台替身，模拟一次成功的在线简历读取。"""

	def __init__(self) -> None:
		self.received_ids: tuple[str, str, str | None] | None = None

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, object]:
		self.received_ids = (geek_id, job_id, security_id)
		return {"code": 0, "payload": "raw-resume"}

	def is_success(self, response: dict[str, object]) -> bool:
		return response["code"] == 0


def test_download_reads_all_ids_and_returns_export_metadata(tmp_path: Path) -> None:
	"""服务应完整传递平台定位参数，并且只把导出元数据交给调用方。"""
	platform = _SuccessfulPlatform()
	expected = ResumeExportResult(
		path=tmp_path / "candidate.md",
		filename="candidate.md",
		bytes_written=128,
		candidate_name="张三",
		geek_id="geek_001",
		exported_at="2026-07-30T10:00:00",
		sections=["basic"],
	)

	def parse(raw: dict[str, object]) -> dict[str, object]:
		assert raw["payload"] == "raw-resume"
		return {"basic": {"name": "张三"}}

	def export(resume: dict[str, object], **kwargs: object) -> ResumeExportResult:
		assert resume == {"basic": {"name": "张三"}}
		assert kwargs == {
			"geek_id": "geek_001",
			"data_dir": tmp_path,
			"output": None,
			"output_dir": tmp_path / "exports",
		}
		return expected

	service = ResumeDownloadService(platform=platform, parser=parse, exporter=export)

	actual = service.download(
		geek_id="geek_001",
		job_id="job_001",
		security_id="security_001",
		data_dir=tmp_path,
		output_dir=tmp_path / "exports",
	)

	assert platform.received_ids == ("geek_001", "job_001", "security_001")
	assert actual == expected


class _FailedPlatform:
	"""模拟失败响应，确保服务不会把原始错误包络向上层传播。"""

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, object]:
		return {"code": 9, "resume_body": "不应出现在异常中的简历正文"}

	def is_success(self, response: dict[str, object]) -> bool:
		return False

	def parse_error(self, response: dict[str, object]) -> tuple[str, str]:
		return "RATE_LIMITED", "访问过于频繁"


def test_download_maps_platform_failure_without_retaining_raw_resume() -> None:
	"""平台失败应变为只含错误码和安全消息的领域异常。"""
	service = ResumeDownloadService(
		platform=_FailedPlatform(),
		parser=lambda response: {"unexpected": response},
		exporter=lambda resume, **kwargs: (_ for _ in ()).throw(AssertionError("不应导出")),
	)

	with pytest.raises(ResumeDownloadPlatformError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=Path("data"),
		)

	error = captured.value
	assert error.code == "RATE_LIMITED"
	assert error.message == "候选人简历获取失败，请稍后重试"
	assert "简历正文" not in str(error)
	assert "resume_body" not in vars(error)


class _LeakingErrorPlatform:
	"""模拟把原始平台敏感字段塞进错误消息的异常适配器。"""

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, object]:
		return {"code": 9}

	def is_success(self, response: dict[str, object]) -> bool:
		return False

	def parse_error(self, response: dict[str, object]) -> tuple[str, str]:
		return "RATE_LIMITED", "wt2=secret-token；候选人简历正文"


def test_download_never_trusts_platform_error_message() -> None:
	"""适配器错误文本可能带平台原文，服务必须改用稳定的安全提示。"""
	service = ResumeDownloadService(
		platform=_LeakingErrorPlatform(),
		parser=lambda response: {"unexpected": response},
		exporter=lambda resume, **kwargs: (_ for _ in ()).throw(AssertionError("不应导出")),
	)

	with pytest.raises(ResumeDownloadPlatformError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=Path("data"),
		)

	assert captured.value.code == "RATE_LIMITED"
	assert str(captured.value) == "候选人简历获取失败，请稍后重试"
	assert "secret-token" not in str(captured.value)
	assert "简历正文" not in str(captured.value)


class _RaisingPlatform:
	"""模拟网络层把含鉴权查询参数的请求地址写进异常文本。"""

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, object]:
		raise RuntimeError("https://api.example.test/?stoken=secret-token")

	def is_success(self, response: dict[str, object]) -> bool:
		return True


def test_download_redacts_platform_transport_exception() -> None:
	"""平台调用抛异常时，服务也只能返回稳定安全的领域错误。"""
	service = ResumeDownloadService(
		platform=_RaisingPlatform(),
		parser=lambda response: {"unexpected": response},
		exporter=lambda resume, **kwargs: (_ for _ in ()).throw(AssertionError("不应导出")),
	)

	with pytest.raises(ResumeDownloadPlatformError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=Path("data"),
		)

	assert captured.value.code == "PLATFORM_REQUEST_FAILED"
	assert str(captured.value) == "候选人简历获取失败，请稍后重试"
	assert "secret-token" not in str(captured.value)


def test_download_redacts_parser_exception() -> None:
	"""解析器遇到字段漂移时，异常文本不得把原始候选人数据传到入口层。"""
	service = ResumeDownloadService(
		platform=_SuccessfulPlatform(),
		parser=lambda response: (_ for _ in ()).throw(RuntimeError("候选人简历正文 secret-token")),
		exporter=lambda resume, **kwargs: (_ for _ in ()).throw(AssertionError("不应导出")),
	)

	with pytest.raises(ResumeDownloadPlatformError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=Path("data"),
		)

	assert captured.value.code == "PLATFORM_RESPONSE_INVALID"
	assert str(captured.value) == "候选人简历获取失败，请稍后重试"
	assert "secret-token" not in str(captured.value)


def test_download_redacts_unexpected_exporter_exception() -> None:
	"""非预期写入异常也必须脱敏，避免自定义导出器把正文带到 CLI 输出。"""
	service = ResumeDownloadService(
		platform=_SuccessfulPlatform(),
		parser=lambda response: {"basic": {"name": "张三"}},
		exporter=lambda resume, **kwargs: (_ for _ in ()).throw(RuntimeError("候选人简历正文 secret-token")),
	)

	with pytest.raises(ResumeDownloadExportError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=Path("data"),
		)

	assert str(captured.value) == "导出简历文件失败，请检查输出目录后重试"
	assert "secret-token" not in str(captured.value)


def test_download_maps_export_failure_without_resume_text(tmp_path: Path) -> None:
	"""写盘失败应脱离底层异常类型，且错误链路不携带解析后的正文。"""
	platform = _SuccessfulPlatform()

	def export(resume: dict[str, object], **kwargs: object) -> ResumeExportResult:
		assert resume["basic"] == {"name": "张三"}
		raise ResumeExportError("写入简历文件失败: target.md (permission denied)")

	service = ResumeDownloadService(
		platform=platform,
		parser=lambda response: {"basic": {"name": "张三"}, "work": "不应出现在异常中的简历正文"},
		exporter=export,
	)

	with pytest.raises(ResumeDownloadExportError) as captured:
		service.download(
			geek_id="geek_001",
			job_id="job_001",
			security_id="security_001",
			data_dir=tmp_path,
		)

	assert "permission denied" in str(captured.value)
	assert "简历正文" not in str(captured.value)
