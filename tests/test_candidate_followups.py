from pathlib import Path

from boss_agent_cli.recruiting.candidate_followups import (
	CandidateFollowUpExecutor,
	CandidateFollowUpSettings,
	CandidateFollowUpStore,
)


def test_followup_settings_are_isolated_by_job(tmp_path: Path) -> None:
	store = CandidateFollowUpStore(tmp_path)
	store.save_settings("java", CandidateFollowUpSettings(phone_enabled=True, interview_enabled=True))

	assert store.settings("java").phone_enabled is True
	assert store.settings("support") == CandidateFollowUpSettings()


def test_followup_results_survive_restart_and_export_current_job_only(tmp_path: Path) -> None:
	store = CandidateFollowUpStore(tmp_path)
	store.update("job:java:friend:1", phone="13800138000", phone_status="succeeded")
	store.update("job:support:friend:2", wechat="wx-two", wechat_status="succeeded")

	path = store.export_csv(
		job_id="java",
		candidates=[
			{"candidate_key": "job:java:friend:1", "candidate_name": "甲", "job_id": "java", "score": 85},
			{"candidate_key": "job:support:friend:2", "candidate_name": "乙", "job_id": "support", "score": 90},
		],
		path=tmp_path / "contacts.csv",
	)

	content = path.read_text(encoding="utf-8-sig")
	assert "甲" in content and "13800138000" in content
	assert "乙" not in content and "wx-two" not in content


def test_contact_retries_are_independent_and_one_success_unlocks_interview(tmp_path: Path) -> None:
	"""电话失败的退避不能阻塞微信；任一联系方式成功后立即允许约面试。"""
	store = CandidateFollowUpStore(tmp_path)
	store.save_settings("java", CandidateFollowUpSettings(phone_enabled=True, wechat_enabled=True, interview_enabled=True))
	calls: list[str] = []

	def request_contact(_friend_id: int, action: str) -> dict[str, object]:
		calls.append(action)
		return {"code": 0, "zpData": {"value": "wx-value"}} if action == "wechat" else {"code": -1}

	class InterviewSettings:
		def get(self, *, job_id: str):
			return self

		def validated(self):
			return self

		def to_dict(self):
			return {"date": "2026-08-22", "time": "10:00"}

	def invite(_friend_id: int, _payload: dict[str, str]) -> dict[str, int]:
		calls.append("interview")
		return {"code": 0}

	record = CandidateFollowUpExecutor(
		store=store,
		request_contact=request_contact,
		invite_interview=invite,
		interview_settings=InterviewSettings(),
	).execute(
		job_id="java",
		candidate_key="job:java:friend:1",
		friend_id=1,
		candidate={"score": 90, "recommendation": "recommend"},
	)

	assert calls == ["wechat", "phone", "interview"]
	assert record.wechat == "wx-value"
	assert record.phone_status == "failed"
	assert record.interview_status == "succeeded"
	assert record.phone_next_retry_at == ""
