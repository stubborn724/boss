"""招聘 AI 对话状态的原子持久化。

消息轮询会重复返回同一条记录，因此消息游标与事实状态必须一起保存。该文件不
保存完整聊天或简历正文，防止状态文件变成未经边界控制的个人资料副本。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import cast

from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage
from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value


class DialogueStateStore:
	"""以候选人稳定键保存紧凑对话状态，并通过替换文件防止半写入。"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / "dialogue_states.json"
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	def _read(self) -> dict[str, object]:
		"""读取文件损坏时返回空账本，避免一位候选人阻断整个沟通队列。"""
		if not self._path.exists():
			return {}
		try:
			value = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		return value if isinstance(value, dict) else {}

	def _write(self, states: dict[str, object]) -> None:
		"""同目录临时文件加 fsync 后替换，保证进程中断不会遗留半个 JSON。"""
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump(sanitize_json_value(states), stream, ensure_ascii=False, indent=2, sort_keys=True)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass

	def get(self, candidate_key: str) -> CandidateDialogueState | None:
		"""按候选人稳定键读取已保存状态。"""
		with self._lock:
			states = self._read()
			exact = CandidateDialogueState.from_dict(states.get(candidate_key))
			if exact is not None or not candidate_key.startswith("friend:"):
				return exact
			# 旧调用方只知道 friend_id 时，仅在所有岗位中唯一匹配才投影状态；
			# 岗位专属 get_for_job 不走这个模糊兼容分支。
			friend_id = candidate_key.removeprefix("friend:")
			matches = [
				state for key, raw in states.items()
				if key.endswith(f":friend:{friend_id}")
				and (state := CandidateDialogueState.from_dict(raw)) is not None
			]
			return matches[0] if len(matches) == 1 else None

	@staticmethod
	def candidate_key(*, job_id: str, friend_id: int) -> str:
		"""生成岗位与会话联合键，防止同一候选人跨岗位覆盖状态。"""
		return f"job:{job_id}:friend:{friend_id}"

	def get_for_job(self, *, job_id: str, friend_id: int) -> CandidateDialogueState | None:
		"""读取岗位专属状态，并窄范围兼容同岗位的历史 ``friend`` 键。

		旧记录只有内部 ``job_id`` 与当前岗位完全一致时才可复用；否则返回空，
		宁可重新建立当前岗位状态，也不能把另一岗位的问答和附件阶段串过来。
		"""
		current = self.get(self.candidate_key(job_id=job_id, friend_id=friend_id))
		if current is not None:
			return current
		with self._lock:
			legacy = CandidateDialogueState.from_dict(self._read().get(f"friend:{friend_id}"))
		return legacy if legacy is not None and legacy.job_id == job_id else None

	def map_for_job(self, *, job_id: str, friend_ids: set[int]) -> dict[int, CandidateDialogueState]:
		"""一次读取指定岗位的多个对话状态，避免大列表同步重复解析 JSON。"""
		if not friend_ids:
			return {}
		with self._lock:
			raw_states = self._read()
		result: dict[int, CandidateDialogueState] = {}
		for raw in raw_states.values():
			state = CandidateDialogueState.from_dict(raw)
			if state is None or state.job_id != job_id:
				continue
			key = state.candidate_key.rsplit(":friend:", 1)[-1]
			if key.isdigit() and int(key) in friend_ids:
				result[int(key)] = state
		return result

	def save(self, state: CandidateDialogueState) -> None:
		"""保存完整状态快照，调用方必须先完成一次合法的状态迁移。"""
		with self._lock:
			states = self._read()
			states[state.candidate_key] = state.to_dict()
			self._write(states)

	def has_processed_message(self, candidate_key: str, message_id: str) -> bool:
		"""判断本轮消息是否已处理，防止轮询和重启后重复调用 AI 或发送。"""
		state = self.get(candidate_key)
		return bool(message_id and state and state.last_processed_message_id == message_id)

	def list_by_job_stage(self, *, job_id: str, stage: DialogueStage) -> list[CandidateDialogueState]:
		"""读取指定岗位与阶段的候选人快照，供附件终审等后续阶段消费。

		状态文件可能包含多个岗位和历史入口记录，因此这里在持久化边界做精确过滤。
		调用方无需遍历私有 JSON，也不会误把仍在基础/专业问答中的附件提前下载。
		"""
		with self._lock:
			states = [
				state
				for raw in self._read().values()
				if (state := CandidateDialogueState.from_dict(raw)) is not None
				and state.job_id == job_id
				and state.stage is stage
			]
		return sorted(states, key=lambda state: state.candidate_key)

	def bind_unique_recommendation(self, *, candidate_name: str, friend_id: int, job_id: str = "") -> CandidateDialogueState | None:
		"""将唯一匹配的推荐开场记录绑定到回流后的真实沟通会话。

		推荐页不提供 ``friend_id``，因此只允许唯一同名候选人迁移。存在同名待
		绑定记录时返回 ``None``，宁可继续异步等待也不将两人的问答和附件错配。
		整个查找和迁移在一把锁内完成，避免多个轮询周期重复绑定。
		"""
		name = candidate_name.strip()
		if not name or friend_id <= 0:
			return None
		with self._lock:
			states = self._read()
			matches = [
				state
				for raw in states.values()
				if (state := CandidateDialogueState.from_dict(raw)) is not None
				and state.source == "recommendation"
				and state.stage is DialogueStage.OPENING_SENT
				and state.candidate_name == name
				and (not job_id or state.job_id == job_id)
			]
			if len(matches) != 1:
				return None
			original = matches[0]
			bound = replace(
				original,
				candidate_key=self.candidate_key(job_id=original.job_id, friend_id=friend_id),
				stage=cast(DialogueStage, DialogueStage.WAITING_CANDIDATE),
			)
			states.pop(original.candidate_key, None)
			states[bound.candidate_key] = bound.to_dict()
			self._write(states)
			return bound
