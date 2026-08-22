"""防止附件沟通流程退回在线简历导出的回归测试。"""


def test_legacy_online_conversation_export_is_not_registered() -> None:
	"""候选人“分析并获取简历”只能走附件状态机，不能恢复旧在线导出命令。"""
	from boss_agent_cli.commands.register import hr_group
	from boss_agent_cli.commands.schema import SCHEMA_DATA

	assert "download-conversation-resume" not in hr_group.commands
	assert "download-conversation-resume" not in SCHEMA_DATA["commands"]["hr"]["subcommands"]


def test_legacy_online_conversation_export_is_not_exposed_as_mcp_tool() -> None:
	"""Agent 不得绕过页面附件沟通，自动调用旧在线简历导出能力。"""
	from boss_agent_cli.mcp_tools import TOOLS

	assert "boss_hr_download_conversation_resume" not in {tool.name for tool in TOOLS}
