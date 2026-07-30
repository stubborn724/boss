"""本地控制台 Click 命令的注册和默认安全边界测试。"""

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_web_help_exposes_loopback_default_without_starting_server() -> None:
	"""帮助信息应说明只绑定回环地址，且 --help 不能启动服务。"""
	result = CliRunner().invoke(cli, ["web", "--help"])

	assert result.exit_code == 0
	assert "127.0.0.1" in result.output
	assert "--port" in result.output
