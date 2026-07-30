"""招聘者 — 把单个候选人的在线简历下载为本地 Markdown 文件。

为什么单独一个命令而不是给 `hr resume` 加 `--output`
----------------------------------------------------
`hr resume` 的契约是「读平台数据并回显」，落盘是另一件事：它有自己的失败
模式（目录不可写、路径非法）、自己的错误码（EXPORT_FAILED）和自己的输出
形状（只回元数据、不回正文）。拆成独立命令后两边各自保持单一职责，
`hr resume` 的既有信封也不会因为多一个参数而产生两种形态。

边界（MVP 明确不做）
--------------------
* 一次只处理一个候选人 —— 没有批量参数，也不接受 ID 列表；
* 不做自动分析 —— 落盘后由用户或另一个 Agent 读文件自行处理；
* 不做自动沟通 —— 本命令不产生任何平台写操作；
* 不进 MCP —— `mcp_tools.py` 里没有对应工具，Agent 无法自动触发下载。

与 `hr resume` 一样，本命令只在显式 `operating_mode=research` 下可用，
默认 assisted 模式会被 compliance 守卫阻断。
"""

from pathlib import Path

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter.resume_download_service import (
	ResumeDownloadExportError,
	ResumeDownloadPlatformError,
	ResumeDownloadService,
)
from boss_agent_cli.commands.recruiter.resume_export import export_candidate_resume
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.display import (
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
)

# 命令标识按仓库约定内联成字面量：tests/test_compliance.py 用 AST 扫描
# require_compliance_allowed 的第二个实参来校验策略注册表没有漂移，
# 抽成变量会让这道守卫扫不到本命令。
_COMMAND = "recruiter-download-resume"

_MISSING_PARAM_MESSAGE = "下载在线简历需要 geek_id 参数以及 --job-id 和 --security-id"


@click.command("download-resume")
@click.argument("geek_id", required=False)
@click.option("--job-id", default="", help="职位 ID（必填）")
@click.option("--security-id", default=None, help="安全 ID（必填，来自 hr candidates / hr applications）")
@click.option(
	"--output",
	default=None,
	type=click.Path(dir_okay=False, path_type=Path),
	help="导出文件的完整路径（.md）；与 --output-dir 互斥",
)
@click.option(
	"--output-dir",
	default=None,
	type=click.Path(file_okay=False, path_type=Path),
	help="导出目录，文件名按 <姓名>-<geek_id>.md 自动生成；与 --output 互斥",
)
@click.pass_context
@handle_auth_errors("recruiter-download-resume")
def download_resume_cmd(
	ctx: click.Context,
	geek_id: str | None,
	job_id: str,
	security_id: str | None,
	output: Path | None,
	output_dir: Path | None,
) -> None:
	"""下载单个候选人的在线简历到本地 Markdown 文件

	默认落在 <data_dir>/recruiter/resumes/ 下；同一候选人重复下载会覆盖
	同一份快照，便于下游按确定路径读取。

	简历正文只写入文件，不进 stdout 信封、不进日志 —— 命令输出仅包含
	导出路径与最小元数据。
	"""
	if not require_compliance_allowed(ctx, "recruiter-download-resume"):
		return

	# 三个定位参数缺一不可：view_geek 需要 geek_id + jobId + securityId 才能取到简历。
	if not (geek_id and job_id and security_id):
		handle_error_output(
			ctx, _COMMAND,
			code="INVALID_PARAM",
			message=_MISSING_PARAM_MESSAGE,
			recoverable=False,
		)
		return

	# 两个输出参数语义冲突时不猜用户意图，直接报错。
	if output is not None and output_dir is not None:
		handle_error_output(
			ctx, _COMMAND,
			code="INVALID_PARAM",
			message="--output 与 --output-dir 互斥，只能指定一个",
			recoverable=False,
		)
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		# 命令层只负责呈现：读取、解析与落盘的可复用编排由服务层负责，
		# 因此后续 Web 控制台可共享同一条隐私边界明确的业务流程。
		service = ResumeDownloadService(
			platform=platform,
			parser=parse_resume,
			exporter=export_candidate_resume,
		)
		try:
			exported = service.download(
				geek_id=geek_id,
				job_id=job_id,
				security_id=security_id,
				data_dir=data_dir,
				output=output,
				output_dir=output_dir,
			)
		except ResumeDownloadPlatformError as exc:
			# 服务错误不含原始响应；沿用平台错误码的既有信封契约和恢复建议。
			recoverable, recovery_action = error_contract_for_code(exc.code)
			handle_error_output(
				ctx,
				_COMMAND,
				code=exc.code,
				message=exc.message,
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		except ResumeDownloadExportError as exc:
			handle_error_output(
				ctx, _COMMAND,
				code="EXPORT_FAILED",
				message=str(exc),
				recoverable=True,
				recovery_action="确认输出目录存在且可写，或改用 --output 指定其他路径",
			)
			return

		handle_output(
			ctx, _COMMAND,
			{
				"geek_id": exported.geek_id,
				"candidate_name": exported.candidate_name,
				"path": str(exported.path),
				"filename": exported.filename,
				"bytes_written": exported.bytes_written,
				"format": "markdown",
				"sections": exported.sections,
				"exported_at": exported.exported_at,
			},
			hints={"next_actions": [
				"用本地工具或另一个 Agent 读取导出的 Markdown 文件进行分析",
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 直接查看简历而不落盘",
			]},
		)
