"""招聘自动化已处理对话的紧凑时间线。

本模块不是 BOSS 聊天备份。它只记录自动化已经读取并处理的候选人消息，以及
平台确认发送成功的 AI 回复，使工作台可以展示 AI 的实际沟通过程，同时避免每次
轮询都重新读取完整历史或把无关聊天、附件正文重复落盘。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value, sanitize_unicode_text


_TRANSCRIPT_FILENAME = "dialogue_transcripts.json"
_ALLOWED_ROLES = {"candidate", "recruiter"}
_MAX_TEXT_LENGTH = 1000


def _transcript_key(*, job_id: str, friend_id: int) -> str:
	"""以岗位和会话组合定位时间线，防止转岗时复用旧问题与回答。"""
	return f"job:{job_id}:friend:{friend_id}"


class DialogueTranscriptStore:
	"""保存供本地工作台查看的已处理对话轮次。

	状态文件使用同目录临时文件替换，保证浏览器服务或自动化线程中断时不会留下
	半个 JSON。每个 ``role + message_id`` 只能保存一次，轮询重试会自然幂等；AI
	回复由调用方在 BOSS 确认发送后才写入，页面不会展示实际上没有发出的内容。
	"""

	def __init__(self, data_dir: Path) -> None:
		"""初始化当前招聘上下文专属的时间线文件。"""
		self._path = data_dir / "recruiter" / _TRANSCRIPT_FILENAME
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	def _read(self) -> dict[str, list[dict[str, str]]]:
		"""容错读取并清洗历史数据，单条损坏记录不能影响其它候选人。"""
		if not self._path.exists():
			return {}
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		if not isinstance(raw, dict):
			return {}
		cleaned: dict[str, list[dict[str, str]]] = {}
		for key, entries in raw.items():
			if not isinstance(key, str) or not isinstance(entries, list):
				continue
			valid_entries = [entry for value in entries if (entry := self._clean_entry(value)) is not None]
			if valid_entries:
				cleaned[key] = valid_entries
		return cleaned

	@staticmethod
	def _clean_entry(value: object) -> dict[str, str] | None:
		"""收敛历史条目字段，阻止任意对象或超长文本进入页面投影。"""
		if not isinstance(value, dict):
			return None
		role = str(value.get("role") or "").strip()
		message_id = str(value.get("message_id") or "").strip()[:128]
		text = str(value.get("text") or "").strip()[:_MAX_TEXT_LENGTH]
		at = str(value.get("at") or "").strip()[:64]
		if role not in _ALLOWED_ROLES or not message_id or not text:
			return None
		return {"role": role, "message_id": message_id, "text": text, "at": at}

	def _write(self, transcripts: dict[str, list[dict[str, str]]]) -> None:
		"""原子写入整份账本，避免并发轮询将时间线写成无效 JSON。"""
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump(sanitize_json_value(transcripts), stream, ensure_ascii=False, indent=2, sort_keys=True)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass

	def record_candidate_message(self, *, job_id: str, friend_id: int, message_id: str, text: str) -> None:
		"""写入已进入 AI 判断的候选人消息，不记录硬筛前被跳过的原文。"""
		self._record(job_id=job_id, friend_id=friend_id, message_id=message_id, role="candidate", text=text)

	def record_recruiter_reply(self, *, job_id: str, friend_id: int, message_id: str, text: str) -> None:
		"""写入 BOSS 已确认投递的 AI 回复，失败发送不能调用此方法。"""
		self._record(job_id=job_id, friend_id=friend_id, message_id=message_id, role="recruiter", text=text)

	def _record(self, *, job_id: str, friend_id: int, message_id: str, role: str, text: str) -> None:
		"""统一执行字段校验与幂等追加，保证每次轮询仅增加真实新轮次。"""
		clean_job_id = job_id.strip()
		clean_message_id = message_id.strip()[:128]
		clean_text = sanitize_unicode_text(text.strip()[:_MAX_TEXT_LENGTH])
		if not clean_job_id or friend_id <= 0 or not clean_message_id or role not in _ALLOWED_ROLES or not clean_text:
			return
		key = _transcript_key(job_id=clean_job_id, friend_id=friend_id)
		with self._lock:
			transcripts = self._read()
			entries = transcripts.setdefault(key, [])
			if any(entry["role"] == role and entry["message_id"] == clean_message_id for entry in entries):
				return
			entries.append({
				"role": role,
				"message_id": clean_message_id,
				"text": clean_text,
				"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
			})
			self._write(transcripts)

	def list_for_candidate(self, *, job_id: str, friend_id: int) -> list[dict[str, str]]:
		"""返回一位候选人在一个岗位下的已处理轮次副本，供 Web 安全渲染。"""
		if not job_id.strip() or friend_id <= 0:
			return []
		key = _transcript_key(job_id=job_id.strip(), friend_id=friend_id)
		with self._lock:
			return [dict(entry) for entry in self._read().get(key, [])]
