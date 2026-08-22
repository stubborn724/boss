"""沟通会话简历导出服务测试。"""

from pathlib import Path

import pytest

from boss_agent_cli.commands.recruiter.conversation_resume_export import (
	ConversationResumePlatformError,
	ConversationResumeExportService,
	ConversationResumeNotFoundError,
)
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult


class FakeConversationPlatform:
	"""最小平台替身，记录服务对会话和附件边界的访问。"""

	def __init__(self, *, attachment: dict | None = None) -> None:
		self.attachment = attachment
		self.detail_calls: list[list[int]] = []
		self.attachment_calls: list[int] = []

	def is_success(self, response: dict) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		return "UNKNOWN", str(response.get("message", ""))

	def friend_detail(self, friend_ids: list[int]) -> dict:
		self.detail_calls.append(friend_ids)
		return {
			"code": 0,
			"zpData": {
				"friendList": [{
					"uid": 7,
					"encryptUid": "geek-7",
					"encryptJobId": "job-9",
					"securityId": "security-3",
					"name": "张三",
				}],
			},
		}

	def exchange_content(self, uid: int) -> dict:
		self.attachment_calls.append(uid)
		return {"code": 0, "zpData": self.attachment or {}}


def _online_exporter(**kwargs) -> ResumeExportResult:
	return ResumeExportResult(
		path=Path("C:/exports/online.md"),
		filename="online.md",
		bytes_written=12,
		candidate_name="张三",
		geek_id=kwargs["geek_id"],
		exported_at="2026-07-30T14:00:00",
		sections=["basic"],
	)


def test_export_resolves_chat_candidate_and_exports_online_resume(tmp_path: Path) -> None:
	platform = FakeConversationPlatform()
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: (_ for _ in ()).throw(AssertionError(url)),
	)

	result = service.export(friend_id=42, data_dir=tmp_path, output_dir=tmp_path)

	assert platform.detail_calls == [[42]]
	assert platform.attachment_calls == [7]
	assert result.online_resume.filename == "online.md"
	assert result.attachment.status == "absent"
	assert result.candidate_name == "张三"
	assert "security-3" not in str(result)


def test_export_accepts_alternate_chat_identity_envelope(tmp_path: Path) -> None:
	"""沟通详情换成 data.items 和下划线字段时仍能定位同一候选人。"""
	platform = FakeConversationPlatform()
	platform.friend_detail = lambda friend_ids: {  # type: ignore[method-assign]
		"code": 0,
		"data": {
			"items": [{
				"friendId": "17",
				"geekId": "geek-17",
				"job_id": "job-11",
				"security_id": "security-8",
				"candidateName": "李四",
			}],
		},
	}
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: b"",
	)

	result = service.export(friend_id=42, data_dir=tmp_path)

	assert result.online_resume.geek_id == "geek-17"
	assert result.candidate_name == "张三"  # 在线导出结果优先保留平台简历姓名
	assert platform.attachment_calls == [17]


def test_export_downloads_only_declared_attachment_to_safe_filename(tmp_path: Path) -> None:
	platform = FakeConversationPlatform(attachment={
		"resumeUrl": "https://files.example.test/resume.pdf",
		"resumeName": "../../张三.pdf",
	})
	seen_urls: list[str] = []
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: seen_urls.append(url) or b"pdf-bytes",
	)

	result = service.export(friend_id=42, data_dir=tmp_path, output_dir=tmp_path)

	assert seen_urls == ["https://files.example.test/resume.pdf"]
	assert result.attachment.status == "downloaded"
	assert result.attachment.filename == "张三.pdf"
	assert result.attachment.bytes_written == len(b"pdf-bytes")
	assert (tmp_path / "attachments" / "张三.pdf").read_bytes() == b"pdf-bytes"
	assert "files.example.test" not in str(result)


def test_export_rejects_missing_chat_identity_without_platform_payload(tmp_path: Path) -> None:
	platform = FakeConversationPlatform()
	platform.friend_detail = lambda friend_ids: {"code": 0, "zpData": {"friendList": []}}  # type: ignore[method-assign]
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: b"",
	)

	with pytest.raises(ConversationResumeNotFoundError) as exc_info:
		service.export(friend_id=42, data_dir=tmp_path)

	assert str(exc_info.value) == "未找到该沟通会话对应的候选人信息"


def test_export_keeps_online_resume_when_attachment_download_fails(tmp_path: Path) -> None:
	platform = FakeConversationPlatform(attachment={"resumeUrl": "https://files.example.test/resume.pdf"})
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: (_ for _ in ()).throw(OSError("network failed")),
	)

	result = service.export(friend_id=42, data_dir=tmp_path, output_dir=tmp_path)

	assert result.online_resume.filename == "online.md"
	assert result.attachment.status == "failed"
	assert result.attachment.path is None


def test_export_preserves_login_expiry_code_from_chat_detail() -> None:
	"""会话详情确认过期时保留统一错误码，供 Web 解锁重新登录。"""
	platform = FakeConversationPlatform()
	platform.friend_detail = lambda friend_ids: {"code": 7, "message": "expired"}  # type: ignore[method-assign]
	platform.parse_error = lambda response: ("LOGIN_EXPIRED", "expired")  # type: ignore[method-assign]
	service = ConversationResumeExportService(
		platform=platform,
		online_exporter=_online_exporter,
		attachment_downloader=lambda url: b"",
	)

	with pytest.raises(ConversationResumePlatformError) as exc_info:
		service.export(friend_id=42, data_dir=Path("C:/exports"))

	assert exc_info.value.code == "LOGIN_EXPIRED"
