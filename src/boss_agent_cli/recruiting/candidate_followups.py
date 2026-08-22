"""合格候选人的联系方式交换、约面试和导出状态。

本模块只负责岗位级配置与结果事实，不执行 BOSS 页面操作。将配置、结果和 RPA
职责分开，能让自动流程、候选人页面按钮和批量导出共享同一套防重复规则。
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateFollowUpSettings:
	"""一个岗位的后续动作开关。"""

	phone_enabled: bool = False
	wechat_enabled: bool = False
	interview_enabled: bool = False

	def to_dict(self) -> dict[str, bool]:
		return {key: bool(value) for key, value in asdict(self).items()}

	@classmethod
	def from_dict(cls, value: object) -> "CandidateFollowUpSettings":
		if not isinstance(value, dict):
			return cls()
		return cls(
			phone_enabled=value.get("phone_enabled") is True,
			wechat_enabled=value.get("wechat_enabled") is True,
			interview_enabled=value.get("interview_enabled") is True,
		)


@dataclass(frozen=True, slots=True)
class CandidateFollowUpRecord:
	"""一位岗位候选人的后续动作结果，所有字段都可安全展示。"""

	phone: str = ""
	wechat: str = ""
	phone_status: str = "pending"
	wechat_status: str = "pending"
	interview_status: str = "pending"
	phone_error: str = ""
	wechat_error: str = ""
	interview_error: str = ""
	next_retry_at: str = ""
	phone_next_retry_at: str = ""
	wechat_next_retry_at: str = ""
	interview_next_retry_at: str = ""

	def to_dict(self) -> dict[str, str]:
		return {key: str(value) for key, value in asdict(self).items()}

	@classmethod
	def from_dict(cls, value: object) -> "CandidateFollowUpRecord":
		if not isinstance(value, dict):
			return cls()
		fields = {field: str(value.get(field) or "")[:500] for field in asdict(cls()).keys()}
		return cls(**fields)


class CandidateFollowUpStore:
	"""按岗位候选人键保存设置和结果，并提供当前岗位 CSV 导出。"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / "candidate-followups.json"
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	def _read(self) -> dict[str, Any]:
		if not self._path.exists():
			return {"settings": {}, "records": {}}
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {"settings": {}, "records": {}}
		return raw if isinstance(raw, dict) else {"settings": {}, "records": {}}

	def _write(self, data: dict[str, Any]) -> None:
		tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
			tmp.replace(self._path)
		finally:
			try:
				tmp.unlink()
			except FileNotFoundError:
				pass

	def settings(self, job_id: str) -> CandidateFollowUpSettings:
		with self._lock:
			return CandidateFollowUpSettings.from_dict(self._read().get("settings", {}).get(job_id, {}))

	def save_settings(self, job_id: str, settings: CandidateFollowUpSettings) -> CandidateFollowUpSettings:
		with self._lock:
			data = self._read()
			data.setdefault("settings", {})[job_id] = settings.to_dict()
			self._write(data)
		return settings

	def get(self, candidate_key: str) -> CandidateFollowUpRecord:
		with self._lock:
			return CandidateFollowUpRecord.from_dict(self._read().get("records", {}).get(candidate_key, {}))

	def update(self, candidate_key: str, **changes: str) -> CandidateFollowUpRecord:
		with self._lock:
			data = self._read()
			current = CandidateFollowUpRecord.from_dict(data.get("records", {}).get(candidate_key, {}))
			values = current.to_dict()
			values.update({key: str(value)[:500] for key, value in changes.items() if key in values})
			data.setdefault("records", {})[candidate_key] = values
			self._write(data)
			return CandidateFollowUpRecord.from_dict(values)

	def export_csv(self, *, job_id: str, candidates: list[dict[str, Any]], path: Path) -> Path:
		"""只导出当前岗位候选人池，不读取或导出其它岗位数据。"""
		path.parent.mkdir(parents=True, exist_ok=True)
		fields = ["candidate_name", "job_id", "score", "recommendation", "phone", "wechat", "interview_status"]
		with path.open("w", encoding="utf-8-sig", newline="") as stream:
			writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
			writer.writeheader()
			for candidate in candidates:
				if str(candidate.get("job_id") or "") != job_id:
					continue
				record = self.get(str(candidate.get("candidate_key") or ""))
				row = candidate | record.to_dict()
				writer.writerow({field: row.get(field, "") for field in fields})
		return path

	def export_csv_bytes(self, *, job_id: str, candidates: list[dict[str, Any]]) -> bytes:
		"""生成当前岗位 CSV 响应体，供 Web 下载而不依赖临时文件。"""
		stream = io.StringIO(newline="")
		fields = ["candidate_name", "job_id", "score", "recommendation", "phone", "wechat", "interview_status"]
		writer = csv.DictWriter(stream, fieldnames=fields)
		writer.writeheader()
		for candidate in candidates:
			if str(candidate.get("job_id") or "") != job_id:
				continue
			record = self.get(str(candidate.get("candidate_key") or ""))
			row = candidate | record.to_dict()
			writer.writerow({field: row.get(field, "") for field in fields})
		return ("\ufeff" + stream.getvalue()).encode("utf-8")

	def export_xlsx(self, *, job_id: str, candidates: list[dict[str, Any]], path: Path) -> Path:
		"""用 openpyxl（项目可选依赖）导出当前岗位结果。"""
		try:
			from openpyxl import Workbook
		except ImportError as exc:
			raise RuntimeError("导出 Excel 需要安装 openpyxl") from exc
		path.parent.mkdir(parents=True, exist_ok=True)
		workbook = Workbook()
		worksheet = workbook.active
		worksheet.append(["候选人", "岗位", "评分", "推荐结果", "手机号", "微信号", "面试状态"])
		for candidate in candidates:
			if str(candidate.get("job_id") or "") != job_id:
				continue
			record = self.get(str(candidate.get("candidate_key") or ""))
			worksheet.append([
				candidate.get("candidate_name", ""), job_id, candidate.get("score", ""),
				candidate.get("recommendation", ""), record.phone, record.wechat, record.interview_status,
			])
		workbook.save(path)
		return path

	def export_xlsx_bytes(self, *, job_id: str, candidates: list[dict[str, Any]]) -> bytes:
		"""生成当前岗位 Excel 响应体，避免 Web 下载暴露本地临时路径。"""
		try:
			from openpyxl import Workbook
		except ImportError as exc:
			raise RuntimeError("导出 Excel 需要安装 openpyxl") from exc
		workbook = Workbook()
		worksheet = workbook.active
		worksheet.append(["候选人", "岗位", "评分", "推荐结果", "手机号", "微信号", "面试状态"])
		for candidate in candidates:
			if str(candidate.get("job_id") or "") != job_id:
				continue
			record = self.get(str(candidate.get("candidate_key") or ""))
			worksheet.append([
				candidate.get("candidate_name", ""), job_id, candidate.get("score", ""),
				candidate.get("recommendation", ""), record.phone, record.wechat, record.interview_status,
			])
		stream = io.BytesIO()
		workbook.save(stream)
		return stream.getvalue()

	def retry_due(self, record: CandidateFollowUpRecord) -> bool:
		"""无时间或时间到期时允许重试，坏值按立即重试兼容旧数据。"""
		return self.retry_due_at(record.next_retry_at)

	@staticmethod
	def retry_due_at(retry_at: str) -> bool:
		"""判断单个动作的退避时间，动作之间互不阻塞。"""
		if not retry_at:
			return True
		try:
			return datetime.now(timezone.utc) >= datetime.fromisoformat(retry_at)
		except ValueError:
			return True

	def schedule_retry(self, candidate_key: str, *, field: str = "next_retry_at", minutes: int = 5) -> CandidateFollowUpRecord:
		"""为指定动作写入退避时间，保留旧版共享字段的兼容默认值。"""
		if field not in {"next_retry_at", "phone_next_retry_at", "wechat_next_retry_at", "interview_next_retry_at"}:
			raise ValueError("退避字段无效")
		return self.update(candidate_key, **{field: (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")})


class CandidateFollowUpExecutor:
	"""执行岗位级后续动作，严格保持微信、电话、面试顺序和幂等性。"""

	def __init__(self, *, store: CandidateFollowUpStore, request_contact: Any, invite_interview: Any, interview_settings: Any) -> None:
		self._store = store
		self._request_contact = request_contact
		self._invite_interview = invite_interview
		self._interview_settings = interview_settings

	def execute(self, *, job_id: str, candidate_key: str, friend_id: int, candidate: dict[str, Any]) -> CandidateFollowUpRecord:
		settings = self._store.settings(job_id)
		record = self._store.get(candidate_key)
		if not settings.phone_enabled and not settings.wechat_enabled and not settings.interview_enabled:
			return record
		if not isinstance(candidate.get("score"), int) or candidate.get("recommendation") == "reject":
			return record
		contact_success = bool(record.phone or record.wechat)
		for action, enabled, status_field, value_field, retry_field in (
			("wechat", settings.wechat_enabled, "wechat_status", "wechat", "wechat_next_retry_at"),
			("phone", settings.phone_enabled, "phone_status", "phone", "phone_next_retry_at"),
		):
			if not enabled or getattr(record, value_field):
				continue
			if getattr(record, status_field) == "failed" and settings.interview_enabled and contact_success:
				continue
			if not self._store.retry_due_at(getattr(record, retry_field)):
				continue
			self._store.update(candidate_key, **{status_field: "running", retry_field: ""})
			try:
				response = self._request_contact(friend_id, action)
				if not isinstance(response, dict) or response.get("code") != 0:
					raise RuntimeError("BOSS 未确认联系方式交换")
				data = response.get("zpData") if isinstance(response.get("zpData"), dict) else {}
				value = str(data.get("value") or data.get(action) or "").strip()
				if not value:
					raise RuntimeError("候选人尚未返回联系方式")
				record = self._store.update(candidate_key, **{status_field: "succeeded", value_field: value, retry_field: ""})
				contact_success = True
			except Exception as exc:
				record = self._store.update(candidate_key, **{status_field: "failed", f"{action}_error": str(exc)[:300]})
				if not contact_success or not settings.interview_enabled:
					self._store.schedule_retry(candidate_key, field=retry_field)
				continue
		if (
			settings.interview_enabled
			and contact_success
			and record.interview_status != "succeeded"
			and self._store.retry_due_at(record.interview_next_retry_at)
		):
			try:
				payload = self._interview_settings.get(job_id=job_id).validated().to_dict()
				response = self._invite_interview(friend_id, payload)
				if not isinstance(response, dict) or response.get("code") != 0:
					raise RuntimeError("BOSS 未确认约面试")
				record = self._store.update(candidate_key, interview_status="succeeded", interview_next_retry_at="")
			except Exception as exc:
				record = self._store.update(candidate_key, interview_status="failed", interview_error=str(exc)[:300])
				self._store.schedule_retry(candidate_key, field="interview_next_retry_at")
		return record
