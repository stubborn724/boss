"""招聘者单份在线简历下载服务。

本模块把“平台读取 -> 结构化解析 -> 本地导出”从 Click 命令中抽离出来，供
CLI 与本地 Web 控制台复用。它不接触 Click 上下文、认证流程或 stdout，因此
入口层可以各自负责合规检查、参数校验和安全呈现。

隐私边界
--------
* 服务仅向调用方返回 :class:`ResumeExportResult` 的最小导出元数据；简历正文
  只能由 exporter 写入本地文件；
* 失败对象绝不保留平台原始响应或解析后的简历，避免错误处理链路意外泄露正文；
* 平台错误只保留适合展示的统一错误码及平台适配器解析出的错误消息。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
import re

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportError, ResumeExportResult


class RecruiterResumePlatform(Protocol):
	"""下载流程实际依赖的平台最小接口。

	使用窄协议而不是耦合具体招聘者平台类，既明确服务边界，也让 Web 与 CLI
	能够在测试中注入轻量替身，无需建立真实登录会话。
	"""

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		"""按候选人、职位和安全 ID 读取在线简历。"""

	def is_success(self, response: dict[str, Any]) -> bool:
		"""判断平台响应是否为成功包络。"""

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		"""把失败包络转换为统一错误码和可展示消息。"""


class ResumeParser(Protocol):
	"""将平台响应转换为导出模块所需结构化简历的函数接口。"""

	def __call__(self, raw: dict[str, Any]) -> dict[str, Any]:
		"""解析成功的 ``view_geek`` 响应。"""


class ResumeExporter(Protocol):
	"""把结构化简历原子写入本地 Markdown 文件的函数接口。"""

	def __call__(
		self,
		resume: dict[str, Any],
		*,
		geek_id: str,
		data_dir: Path,
		output: Path | None = None,
		output_dir: Path | None = None,
	) -> ResumeExportResult:
		"""导出简历并返回可安全呈现的最小元数据。"""


class ResumeDownloadError(RuntimeError):
	"""简历下载领域错误的基类。

	领域错误只承载对调用方有意义且可安全呈现的信息；原始响应与简历正文在
	失败时立即丢弃，避免被 Web 任务状态、CLI 日志或异常跟踪再次保存。
	"""


class ResumeDownloadPlatformError(ResumeDownloadError):
	"""平台未能返回简历时的脱敏错误。

	``code`` 和 ``message`` 由平台适配器的 ``parse_error`` 提供，供入口层映射
	到已有的 JSON 错误信封。实例不保存 ``response``，这是正文不进入错误输出
	链路的关键约束。
	"""

	def __init__(self, code: str, message: str) -> None:
		super().__init__(message)
		self.code = code
		self.message = message


class ResumeDownloadExportError(ResumeDownloadError):
	"""本地导出失败时的脱敏领域错误。

	导出器已保证 ``ResumeExportError`` 的文本只含目标路径与系统原因；服务将其
	封装为稳定的领域类型，入口层无需依赖底层导出模块的实现细节。
	"""


_SAFE_PLATFORM_FAILURE_MESSAGE = "候选人简历获取失败，请稍后重试"
_SAFE_EXPORT_FAILURE_MESSAGE = "导出简历文件失败，请检查输出目录后重试"
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _safe_platform_error_code(value: object) -> str:
	"""保留平台标准错误码，拒绝把不受控文本当作错误码输出。

	错误码同样会进入 CLI/Web 信封，不能假设适配器返回的一定是可信枚举。
	不符合既有机器可读命名约束时统一退化，避免原始响应片段进入元数据。
	"""
	text = str(value)
	return text if _SAFE_ERROR_CODE.fullmatch(text) else "PLATFORM_REQUEST_FAILED"


@dataclass(frozen=True)
class ResumeDownloadService:
	"""单份候选人简历下载编排器。

	依赖全部通过构造参数注入，使服务可由多个入口复用，并可用真实平台适配器
	或测试替身替换。服务本身不创建平台客户端，因此资源生命周期仍由调用方
	管理，符合既有 ``get_recruiter_platform_instance`` 上下文管理模式。
	"""

	platform: RecruiterResumePlatform
	parser: ResumeParser
	exporter: ResumeExporter

	def download(
		self,
		*,
		geek_id: str,
		job_id: str,
		security_id: str,
		data_dir: Path,
		output: Path | None = None,
		output_dir: Path | None = None,
	) -> ResumeExportResult:
		"""下载并导出一位候选人的简历。

		三个平台定位 ID 由入口层校验后传入。成功时只返回导出元数据；平台失败
		和写盘失败分别转换为类型化领域错误，既方便 CLI/Web 统一呈现，也不会把
		原始响应或正文暴露给调用方。
		"""
		try:
			response = self.platform.view_geek(geek_id, job_id, security_id=security_id)
			is_success = self.platform.is_success(response)
		except Exception as exc:
			# HTTP/传输异常的文本可能包含 URL 查询参数或平台原始响应，绝不向入口层透传。
			raise ResumeDownloadPlatformError(
				"PLATFORM_REQUEST_FAILED", _SAFE_PLATFORM_FAILURE_MESSAGE
			) from exc

		if not is_success:
			try:
				code, _ = self.platform.parse_error(response)
			except Exception as exc:
				# 适配器的错误解析也属于不可信平台边界；解析失败不能破坏脱敏契约。
				raise ResumeDownloadPlatformError(
					"PLATFORM_REQUEST_FAILED", _SAFE_PLATFORM_FAILURE_MESSAGE
				) from exc
			raise ResumeDownloadPlatformError(_safe_platform_error_code(code), _SAFE_PLATFORM_FAILURE_MESSAGE)

		try:
			resume = self.parser(response)
		except Exception as exc:
			# 字段漂移或解析器缺陷可能让异常带有原始响应，入口层只能得到固定提示。
			raise ResumeDownloadPlatformError(
				"PLATFORM_RESPONSE_INVALID", _SAFE_PLATFORM_FAILURE_MESSAGE
			) from exc
		try:
			return self.exporter(
				resume,
				geek_id=geek_id,
				data_dir=data_dir,
				output=output,
				output_dir=output_dir,
			)
		except ResumeExportError as exc:
			raise ResumeDownloadExportError(str(exc)) from exc
		except Exception as exc:
			# 生产导出器会抛 ResumeExportError；兜底分支保护未来实现或集成替身的异常文本。
			raise ResumeDownloadExportError(_SAFE_EXPORT_FAILURE_MESSAGE) from exc
