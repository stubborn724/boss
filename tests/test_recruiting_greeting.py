"""岗位招呼语本地配置测试。"""

from boss_agent_cli.recruiting.greeting import GreetingConfiguration, build_default_job_greeting


def test_greeting_configuration_normalises_content_for_idempotent_sync() -> None:
	"""仅空白差异不应导致再次进入 BOSS 同步页面。"""
	first = GreetingConfiguration(content="您好，方便确认一下您可接受天河区通勤吗？")
	second = GreetingConfiguration(content=" 您好，方便确认一下您可接受天河区通勤吗？ \n")

	assert first.content_hash == second.content_hash
	assert first.needs_sync(last_synced_hash=first.content_hash) is False


def test_greeting_configuration_rejects_boss_length_overflow() -> None:
	"""BOSS 页面限制 100 字，越界内容不能进入 RPA 写入步骤。"""
	configuration = GreetingConfiguration(content="问" * 101)

	assert configuration.is_valid is False
	assert configuration.validation_error == "greeting_too_long"


def test_default_job_greeting_combines_all_basic_questions_once() -> None:
	"""默认话术应一次问完基础条件，不能把候选人拆成多轮非专业问答。"""
	content = build_default_job_greeting("Java 开发工程师")

	assert content.startswith("您好，感谢关注Java 开发工程师岗位。")
	assert "最高学历" in content
	assert "相关工作年限" in content
	assert "所在城市" in content
	assert "到岗时间" in content
	assert GreetingConfiguration(content=content).is_valid is True
