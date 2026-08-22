"""按岗位持久化 BOSS 约面试配置。

设置与 ``JobProfile`` 分开保存：岗位本身描述招聘标准，面试地点和联系人属于
执行配置，变化频率与权限都不同。分离后修改面试安排不会让岗位重新进入草稿，
也避免候选人操作入口自行拼接易出错的表单参数。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date as calendar_date
from datetime import time as calendar_time
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any


_FILENAME = "interview-invitation-settings.json"
_MODES = {"online", "offline"}
_PHONE_PATTERN = re.compile(r"^[0-9+() -]{6,32}$")


@dataclass(frozen=True)
class InterviewInvitationSettings:
	"""一项岗位级面试邀请配置，只保留 BOSS 表单实际需要的字段。"""

	mode: str = "online"
	address: str = ""
	note: str = ""
	date: str = ""
	time: str = ""
	contact_name: str = ""
	contact_phone: str = ""

	def validated(self) -> "InterviewInvitationSettings":
		"""返回清洗后的设置，提交前集中约束必填字段和长度。

		线上面试允许没有地点和联系人，由 BOSS 的在线会议流程补齐；线下面试
		必须有可抵达地点及联系人电话。日期和时间固定采用 ISO 格式，避免本地
		浏览器显示语言不同导致 RPA 填入错误日期。
		"""
		mode = self.mode.strip().casefold()
		if mode not in _MODES:
			raise ValueError("面试方式只能是 online 或 offline")
		address = self.address.strip()[:200]
		note = self.note.strip()[:500]
		date_value = self.date.strip()
		time_value = self.time.strip()
		contact_name = self.contact_name.strip()[:60]
		contact_phone = self.contact_phone.strip()[:32]
		if not date_value or not time_value:
			raise ValueError("面试日期和时间不能为空")
		try:
			calendar_date.fromisoformat(date_value)
			calendar_time.fromisoformat(time_value)
		except ValueError as exc:
			raise ValueError("面试日期或时间格式无效") from exc
		if mode == "offline":
			if not address:
				raise ValueError("线下面试地点不能为空")
			if not contact_name:
				raise ValueError("线下面试联系人不能为空")
			if not _PHONE_PATTERN.fullmatch(contact_phone):
				raise ValueError("联系人电话格式无效")
		elif contact_phone and not _PHONE_PATTERN.fullmatch(contact_phone):
			raise ValueError("联系人电话格式无效")
		return InterviewInvitationSettings(
			mode=mode,
			address=address,
			note=note,
			date=date_value,
			time=time_value,
			contact_name=contact_name,
			contact_phone=contact_phone,
		)

	def to_dict(self) -> dict[str, str]:
		"""只输出面试表单允许的固定字段。"""
		return {key: str(value) for key, value in asdict(self).items()}

	@classmethod
	def from_dict(cls, value: Any) -> "InterviewInvitationSettings | None":
		"""恢复历史 JSON；不完整或损坏记录不影响其它岗位。"""
		if not isinstance(value, dict):
			return None
		try:
			return cls(
				mode=str(value.get("mode") or "online"),
				address=str(value.get("address") or ""),
				note=str(value.get("note") or ""),
				date=str(value.get("date") or ""),
				time=str(value.get("time") or ""),
				contact_name=str(value.get("contact_name") or ""),
				contact_phone=str(value.get("contact_phone") or ""),
			).validated()
		except ValueError:
			return None


class InterviewInvitationSettingsStore:
	"""维护面试设置的原子 JSON 快照，按岗位 ID 隔离。"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / _FILENAME
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	def _read(self) -> dict[str, InterviewInvitationSettings]:
		if not self._path.exists():
			return {}
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		if not isinstance(raw, dict):
			return {}
		return {
			job_id: settings
			for key, value in raw.items()
			if (job_id := str(key).strip()) and (settings := InterviewInvitationSettings.from_dict(value)) is not None
		}

	def _write(self, values: dict[str, InterviewInvitationSettings]) -> None:
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump({job_id: settings.to_dict() for job_id, settings in values.items()}, stream, ensure_ascii=False, indent=2, sort_keys=True)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass

	def get(self, *, job_id: str) -> InterviewInvitationSettings:
		"""读取岗位配置；未设置时返回空默认值供页面编辑，不伪造可提交设置。"""
		clean_job_id = job_id.strip()
		if not clean_job_id:
			raise ValueError("岗位标识不能为空")
		with self._lock:
			return self._read().get(clean_job_id, InterviewInvitationSettings())

	def save(self, *, job_id: str, settings: InterviewInvitationSettings) -> InterviewInvitationSettings:
		"""验证并原子保存单个岗位配置，保证失败不会覆盖旧设置。"""
		clean_job_id = job_id.strip()
		if not clean_job_id:
			raise ValueError("岗位标识不能为空")
		validated = settings.validated()
		with self._lock:
			values = self._read()
			values[clean_job_id] = validated
			self._write(values)
		return validated
