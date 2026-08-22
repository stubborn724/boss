"""岗位招呼语配置持久化测试。"""

from boss_agent_cli.recruiting.models import JobProfile


def test_job_profile_round_trips_greeting_message() -> None:
	"""岗位话术应随岗位保存，供推荐 RPA 同步使用。"""
	job = JobProfile(job_id="job-1", name="Java", greeting_message="您好，请问方便聊聊吗？")

	restored = JobProfile.from_dict(job.to_dict())

	assert restored.greeting_message == "您好，请问方便聊聊吗？"
