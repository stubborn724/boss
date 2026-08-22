"""附件简历沟通状态机的行为契约。"""

from pathlib import Path
from unittest.mock import Mock

from boss_agent_cli.commands.recruiter.attachment_resume_flow import (
	AttachmentResumeFlow,
)
from boss_agent_cli.commands.recruiter.conversation_state import ConversationStateStore


def _platform(*, attachment: dict | None = None, history: bool = False) -> Mock:
	platform = Mock()
	platform.is_success.side_effect = lambda response: response.get("code") == 0
	platform.download_attachment_via_ui.return_value = attachment or {
		"code": 0,
		"message": "未分享附件简历",
	}
	platform.has_existing_resume_request.return_value = history
	platform.send_message_by_friend.return_value = {"code": 0, "message": "ok"}
	return platform


def test_existing_attachment_downloads_without_sending_request(tmp_path: Path) -> None:
	attachment = tmp_path / "candidate.pdf"
	attachment.write_bytes(b"%PDF-1.4 attachment")
	platform = _platform(attachment={"code": 0, "zpData": {"attachment_path": str(attachment)}})
	flow = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	)

	result = flow.advance(friend_id=3, candidate_name="候选人", resume_admitted=True)

	assert result.status == "downloaded"
	assert result.path == attachment
	platform.send_message_by_friend.assert_not_called()


def test_duplicate_attachment_from_another_candidate_is_not_admitted(tmp_path: Path) -> None:
	"""跨候选人复用同一字节附件时，不能把错配文件送入 AI 终审。"""
	attachment = tmp_path / "candidate.pdf"
	attachment.write_bytes(b"%PDF-1.4 same-resume")
	store = ConversationStateStore(tmp_path)
	store.mark_resume_downloaded(1, path=str(attachment), kind="attachment")
	platform = _platform(attachment={"code": 0, "zpData": {"attachment_path": str(attachment)}})

	result = AttachmentResumeFlow(
		platform=platform,
		state_store=store,
		output_dir=tmp_path,
	).advance(friend_id=2, candidate_name="另一位候选人", resume_admitted=True)

	assert result.status == "identity_conflict"
	assert store.get(2) == {}


def test_previous_request_waits_without_resending(tmp_path: Path) -> None:
	platform = _platform()
	store = ConversationStateStore(tmp_path)
	store.mark_resume_request_sent(2, message="可以看看你的简历吗？")

	result = AttachmentResumeFlow(
		platform=platform,
		state_store=store,
		output_dir=tmp_path,
	).advance(friend_id=2, candidate_name="候选人", resume_admitted=True)

	assert result.status == "waiting_reply"
	platform.send_message_by_friend.assert_not_called()


def test_job_scoped_flow_adopts_legacy_request_from_other_flow(tmp_path: Path) -> None:
	"""旧流程写入无岗位键后，新岗位流程也必须识别并禁止第二次索要。"""
	platform = _platform()
	store = ConversationStateStore(tmp_path)
	store.mark_resume_request_sent(2, message="感谢回复，请发送附件简历。")

	result = AttachmentResumeFlow(
		platform=platform,
		state_store=store,
		output_dir=tmp_path,
		job_id="job-java",
	).advance(friend_id=2, candidate_name="候选人", resume_admitted=True)

	assert result.status == "waiting_reply"
	platform.send_message_by_friend.assert_not_called()


def test_unasked_candidate_is_requested_once_and_persisted(tmp_path: Path) -> None:
	platform = _platform()
	flow = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	)

	first = flow.advance(friend_id=1, candidate_name="候选人", resume_admitted=True)
	second = flow.advance(friend_id=1, candidate_name="候选人", resume_admitted=True)

	assert first.status == "requested"
	assert second.status == "waiting_reply"
	platform.send_message_by_friend.assert_called_once()


def test_online_resume_fields_are_never_used(tmp_path: Path) -> None:
	platform = _platform(attachment={"code": 0, "zpData": {}})
	platform.export_online_resume = Mock(side_effect=AssertionError("在线简历不能作为兜底"))
	result = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	).advance(friend_id=4, candidate_name="候选人", resume_admitted=True)

	assert result.status == "requested"
	platform.export_online_resume.assert_not_called()


def test_attachment_is_not_downloaded_before_dialogue_admission(tmp_path: Path) -> None:
	"""基础与专业问题未完成时，即使候选人已上传附件也不能下载或分析。"""
	attachment = tmp_path / "candidate.pdf"
	attachment.write_bytes(b"%PDF-1.4 attachment")
	platform = _platform(attachment={"code": 0, "zpData": {"attachment_path": str(attachment)}})
	flow = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	)

	result = flow.advance(friend_id=5, candidate_name="候选人", resume_admitted=False)

	assert result.status == "waiting_dialogue"
	assert result.path is None
	platform.download_attachment_via_ui.assert_not_called()
	platform.send_message_by_friend.assert_not_called()


def test_acceptance_pending_waits_without_sending_a_second_request(tmp_path: Path) -> None:
	"""同意动作尚未让附件按钮生效时，不能再次索要简历。"""
	platform = _platform(
		attachment={
			"code": 0,
			"message": "已点击同意，附件按钮仍禁用",
			"zpData": {"attachment_state": "acceptance_pending"},
		}
	)
	flow = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	)

	result = flow.advance(friend_id=8, candidate_name="候选人", resume_admitted=True)

	assert result.status == "waiting_attachment"
	assert "同意" in result.message
	platform.send_message_by_friend.assert_not_called()


def test_attachment_download_failure_is_not_treated_as_missing_attachment(tmp_path: Path) -> None:
	"""已经进入下载阶段但失败时，不能退化成再次询问候选人。"""
	platform = _platform(
		attachment={
			"code": 0,
			"message": "附件下载失败",
			"zpData": {"attachment_state": "download_failed"},
		}
	)
	flow = AttachmentResumeFlow(
		platform=platform,
		state_store=ConversationStateStore(tmp_path),
		output_dir=tmp_path,
	)

	result = flow.advance(friend_id=9, candidate_name="候选人", resume_admitted=True)

	assert result.status == "error"
	assert result.message == "附件下载失败"
	platform.send_message_by_friend.assert_not_called()
