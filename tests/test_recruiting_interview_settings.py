"""按岗位保存约面试设置的测试。"""

from boss_agent_cli.recruiting.interview_settings import (
	InterviewInvitationSettings,
	InterviewInvitationSettingsStore,
)


def test_interview_settings_store_isolated_by_job_and_persists(tmp_path) -> None:
	"""不同岗位的面试地点和联系人不得互相覆盖。"""
	store = InterviewInvitationSettingsStore(tmp_path)
	java = InterviewInvitationSettings(
		mode="offline", address="广州市天河区科韵路 1 号", note="请携带作品", date="2026-08-19",
		time="14:30", contact_name="陈老师", contact_phone="13800138000",
	)
	support = InterviewInvitationSettings(mode="online", note="请提前 5 分钟进入会议", date="2026-08-20", time="10:00")

	store.save(job_id="job-java", settings=java)
	store.save(job_id="job-support", settings=support)

	assert store.get(job_id="job-java") == java
	assert store.get(job_id="job-support") == support
	assert InterviewInvitationSettingsStore(tmp_path).get(job_id="job-java") == java


def test_interview_settings_rejects_invalid_offline_contact_phone(tmp_path) -> None:
	"""线下面试必须留下可用联系人电话，避免 BOSS 提交无效邀约。"""
	store = InterviewInvitationSettingsStore(tmp_path)
	settings = InterviewInvitationSettings(
		mode="offline", address="广州", date="2026-08-19", time="14:30", contact_name="陈老师", contact_phone="not-a-phone",
	)

	try:
		store.save(job_id="job-java", settings=settings)
	except ValueError as exc:
		assert str(exc) == "联系人电话格式无效"
	else:
		raise AssertionError("非法电话不应写入面试设置")
