"""管理 BOSS 推荐牛人账号级每日沟通上限。

本模块只保存 BOSS 已明确反馈的账号级额度事实，不能依据网络错误、登录错误或
候选人卡片状态猜测上限。状态按本地自然日自动失效，使次日无需人工清理即可恢复
推荐自动化；沟通列表和附件流程不依赖此文件。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any


RECOMMENDATION_DAILY_QUOTA_REACHED = "RECOMMENDATION_DAILY_QUOTA_REACHED"
_FILENAME = "recommendation-daily-quota.json"
_DEFAULT_MESSAGE = "BOSS 推荐牛人今日沟通已达上限，今天已停止所有岗位的推荐牛人自动化，仅继续处理沟通列表；次日自动恢复。"


class RecommendationDailyQuotaReached(RuntimeError):
	"""表示 BOSS 已明确告知推荐牛人当天沟通额度耗尽。

	异常是推荐发送边界与协调器之间的稳定业务信号。它不承载 BOSS 原始页面文本，
	以免底层 DOM 内容进入活动日志或被误用于其它失败分类。
	"""

	def __init__(self, message: str = _DEFAULT_MESSAGE) -> None:
		super().__init__(message)


class RecommendationQuotaStore:
	"""用原子 JSON 快照保存当前招聘账号的每日推荐额度状态。

	实例由账号上下文目录构造，所以状态天然跨岗位共享。读取失败时安全降级为未阻断：
	本地状态损坏不能让用户永远无法使用推荐入口，且不会影响仍可执行的沟通列表。
	"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / _FILENAME
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = RLock()

	@property
	def path(self) -> Path:
		"""暴露状态路径，便于诊断和无副作用测试。"""
		return self._path

	def is_blocked(self, *, now: datetime | None = None) -> bool:
		"""判断今天是否已触发推荐沟通上限，跨日记录自动失效。"""
		return bool(self.status(now=now)["blocked"])

	def mark_reached(self, *, message: str, now: datetime | None = None) -> None:
		"""记录已由 BOSS 明确确认的当天上限，不保存候选人或页面原文。"""
		current = now or datetime.now().astimezone()
		payload = {
			"day": current.date().isoformat(),
			"reached_at": current.isoformat(timespec="seconds"),
			"message": (message.strip() or _DEFAULT_MESSAGE)[:240],
		}
		with self._lock:
			self._write(payload)

	def status(self, *, now: datetime | None = None) -> dict[str, object]:
		"""返回页面和协调器可消费的最小额度状态。"""
		current = now or datetime.now().astimezone()
		with self._lock:
			payload = self._read()
		day = str(payload.get("day") or "")
		blocked = day == current.date().isoformat()
		return {
			"blocked": blocked,
			"day": day if blocked else "",
			"reached_at": str(payload.get("reached_at") or "") if blocked else "",
			"error_code": RECOMMENDATION_DAILY_QUOTA_REACHED if blocked else "",
			"message": str(payload.get("message") or _DEFAULT_MESSAGE) if blocked else "",
		}

	def _read(self) -> dict[str, Any]:
		"""读取并校验快照；任何损坏都退化为空状态。"""
		if not self._path.exists():
			return {}
		try:
			payload = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		return payload if isinstance(payload, dict) else {}

	def _write(self, payload: dict[str, object]) -> None:
		"""同目录原子写入，防止服务异常中断留下半截状态文件。"""
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass
