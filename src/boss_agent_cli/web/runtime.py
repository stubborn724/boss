"""本地招聘控制台的异步任务运行时。

HTTP 层不能在事件循环中直接执行浏览器登录或平台访问；本模块将两类耗时操作
放到受控后台线程，并只保存用户界面需要的最小状态和导出元数据。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult
from boss_agent_cli.compliance import RESEARCH_MODE

_SAFE_LOGIN_FAILURE_MESSAGE = "登录未完成，请在官方 BOSS 页面确认后重试"
_SAFE_DOWNLOAD_FAILURE_MESSAGE = "简历下载失败，请检查参数、登录态和输出目录后重试"

LoginOperation = Callable[[], object]
SavedLoginProbe = Callable[[], bool]
DownloadOperation = Callable[..., ResumeExportResult]


class LocalConsoleRuntime:
	"""协调一次登录和一次简历下载的本地运行时。

	运行时是 Web 层与业务服务之间的边界：它不保存认证结果、原始平台响应或简历
	正文。登录和下载各自单飞，重复点击只返回现有任务状态，避免重复打开官方页面
	或在同一候选人快照写入期间产生并发竞争。
	"""

	def __init__(
		self,
		*,
		operating_mode: str,
		login_in_browser: LoginOperation,
		has_saved_login: SavedLoginProbe,
		download_resume: DownloadOperation,
	) -> None:
		self.operating_mode = operating_mode
		self._login_in_browser = login_in_browser
		self._has_saved_login = has_saved_login
		self._download_resume = download_resume
		self._state_lock = Lock()
		self._login_lock = Lock()
		self._download_lock = Lock()
		self._login = {"state": "succeeded" if has_saved_login() else "idle"}
		self._download: dict[str, Any] = {"state": "idle"}
		self._workers: list[Thread] = []

	def status(self) -> dict[str, Any]:
		"""返回可以安全发送给浏览器的状态快照。"""
		with self._state_lock:
			return {
				"operating_mode": self.operating_mode,
				"login": dict(self._login),
				"download": dict(self._download),
			}

	def start_login(self) -> dict[str, Any]:
		"""启动官方页面登录任务；已有任务运行时不重复打开浏览器。"""
		with self._state_lock:
			if self._login["state"] == "running":
				return dict(self._login)
			self._login = {"state": "running"}
		worker = Thread(target=self._run_login, name="boss-web-login", daemon=True)
		self._workers.append(worker)
		worker.start()
		return {"state": "running"}

	def start_download(
		self,
		*,
		geek_id: str,
		job_id: str,
		security_id: str,
		output: Path | None,
		output_dir: Path | None,
	) -> dict[str, Any]:
		"""启动一份候选人简历下载，严格保留现有研究模式边界。"""
		if self.operating_mode != RESEARCH_MODE:
			return {
				"state": "blocked",
				"error": {"code": "COMPLIANCE_BLOCKED", "message": "下载在线简历需要显式启用 research 模式"},
			}
		with self._state_lock:
			if self._download["state"] == "running":
				return dict(self._download)
			self._download = {"state": "running"}
		worker = Thread(
			target=self._run_download,
			kwargs={
				"geek_id": geek_id,
				"job_id": job_id,
				"security_id": security_id,
				"output": output,
				"output_dir": output_dir,
			},
			name="boss-web-resume-download",
			daemon=True,
		)
		self._workers.append(worker)
		worker.start()
		return {"state": "running"}

	def wait_for_idle(self, timeout: float) -> None:
		"""供测试等待当前后台任务结束，生产请求不应阻塞等待。"""
		for worker in tuple(self._workers):
			worker.join(timeout=timeout)

	def _run_login(self) -> None:
		"""执行官方浏览器登录，并把任意底层异常收敛为安全状态。"""
		with self._login_lock:
			try:
				self._login_in_browser()
			except Exception:
				result: dict[str, Any] = {"state": "failed", "error": {"code": "LOGIN_FAILED", "message": _SAFE_LOGIN_FAILURE_MESSAGE}}
			else:
				result = {"state": "succeeded"}
		with self._state_lock:
			self._login = result

	def _run_download(
		self,
		*,
		geek_id: str,
		job_id: str,
		security_id: str,
		output: Path | None,
		output_dir: Path | None,
	) -> None:
		"""调用共享服务并仅把 ResumeExportResult 投影到浏览器状态。"""
		with self._download_lock:
			try:
				exported = self._download_resume(
					geek_id=geek_id,
					job_id=job_id,
					security_id=security_id,
					output=output,
					output_dir=output_dir,
				)
			except Exception:
				result: dict[str, Any] = {"state": "failed", "error": {"code": "DOWNLOAD_FAILED", "message": _SAFE_DOWNLOAD_FAILURE_MESSAGE}}
			else:
				result = {"state": "succeeded", "result": self._export_metadata(exported)}
		with self._state_lock:
			self._download = result

	@staticmethod
	def _export_metadata(exported: ResumeExportResult) -> dict[str, Any]:
		"""显式白名单导出元数据，防止以后新增字段意外进入 Web 响应。"""
		return {
			"geek_id": exported.geek_id,
			"candidate_name": exported.candidate_name,
			"path": str(exported.path),
			"filename": exported.filename,
			"bytes_written": exported.bytes_written,
			"sections": exported.sections,
			"exported_at": exported.exported_at,
		}
