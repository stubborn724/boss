"""招聘自动化的统一候选人队列。

本模块是 BOSS 来源数据、AI 对话状态和工作台展示之间的事实边界。它只保存
候选人的最小可操作元数据，不保存完整聊天或简历正文；这样推荐牛人回流沟通列表
后能以同一个 ``friend_id`` 继续，又不会让前端自行拼接多份临时状态。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any

from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value


class AutomationCandidateStage(StrEnum):
	"""自动化队列的可展示阶段。

	阶段是 UI 的业务语言，不直接复用 AI 内部枚举，避免对话编排调整时破坏已保存
	的工作台记录。只有 ``ANALYZED`` 且附件通过本地校验时，候选人才会进入合格列表。
	"""

	SYNCED = "synced"
	HARD_REJECTED = "hard_rejected"
	BASIC_DIALOGUE = "basic_dialogue"
	PROFESSIONAL_DIALOGUE = "professional_dialogue"
	WAITING_CANDIDATE = "waiting_candidate"
	WAITING_ATTACHMENT = "waiting_attachment"
	ANALYZED = "analyzed"
	MANUAL_REVIEW = "manual_review"
	FAILED = "failed"
	PAUSED = "paused"


_QUEUE_FILENAME = "automation_queue.json"
_VALID_SOURCES = {"conversation", "recommendation"}


def _candidate_key(*, job_id: str, friend_id: int) -> str:
	"""生成岗位内稳定身份，避免同一 BOSS 会话在跨岗位评估时互相覆盖。

	BOSS 的 ``friend_id`` 只代表沟通会话，不能代表一次岗位申请。评分、附件和
	对话进度都是岗位事实，因此键必须同时包含岗位和会话；所有旧版 JSON 在读取
	时也会按这条规则重建键，不需要单独的迁移命令。
	"""
	return f"job:{job_id}:friend:{friend_id}"


@dataclass(frozen=True, slots=True)
class AutomationCandidate:
	"""一位已进入平台管理范围的候选人。

	``candidate_key`` 由岗位和真实 ``friend_id`` 共同生成，因此列表重复同步、推荐
	来源回流和服务重启都不会创造重复记录，也不会把候选人在另一岗位下的终审结果
	覆盖掉。来源首次为推荐牛人时永久保留，便于在同一沟通列表中追溯渠道效果。
	"""

	candidate_key: str
	friend_id: int
	job_id: str
	candidate_name: str
	source: str
	stage: AutomationCandidateStage
	score: int | None = None
	recommendation: str = ""
	resume_path: str = ""
	last_action: str = ""
	reason_codes: tuple[str, ...] = ()
	last_message_id: str = ""
	# 仅保存列表卡片生成的不可逆版本值，不保存消息预览或正文。它用于发现
	# 用户手动点开会话后红点被清除、但候选人随后又回复的情况。
	conversation_version: str = ""
	# 下次无强信号附件复查时间。它是调度事实而不是临时内存状态，服务重启后
	# 仍能跳过长期不回复的人；未读和会话版本变化由协调器作为强信号即时绕过。
	attachment_retry_at: str = ""
	created_at: str = ""
	updated_at: str = ""

	def to_dict(self) -> dict[str, Any]:
		"""输出白名单字段，避免未来调用方意外落入完整聊天内容。"""
		return {
			"candidate_key": self.candidate_key,
			"friend_id": self.friend_id,
			"job_id": self.job_id,
			"candidate_name": self.candidate_name,
			"source": self.source,
			"stage": self.stage.value,
			"score": self.score,
			"recommendation": self.recommendation,
			"resume_path": self.resume_path,
			"last_action": self.last_action,
			"reason_codes": list(self.reason_codes),
			"last_message_id": self.last_message_id,
			"conversation_version": self.conversation_version,
			"attachment_retry_at": self.attachment_retry_at,
			"created_at": self.created_at,
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, value: object) -> "AutomationCandidate | None":
		"""从历史 JSON 恢复有效候选人；损坏数据不阻塞其它记录。"""
		if not isinstance(value, dict):
			return None
		friend_id = value.get("friend_id")
		job_id = str(value.get("job_id") or "").strip()
		if not isinstance(friend_id, int) or isinstance(friend_id, bool) or friend_id <= 0 or not job_id:
			return None
		try:
			stage = AutomationCandidateStage(str(value.get("stage") or AutomationCandidateStage.SYNCED))
		except ValueError:
			stage = AutomationCandidateStage.SYNCED
		score = value.get("score")
		valid_score = score if isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100 else None
		raw_reasons = value.get("reason_codes")
		reasons = tuple(str(item)[:80] for item in raw_reasons if str(item).strip()) if isinstance(raw_reasons, list) else ()
		source = str(value.get("source") or "conversation")
		return cls(
			candidate_key=_candidate_key(job_id=job_id, friend_id=friend_id),
			friend_id=friend_id,
			job_id=job_id,
			candidate_name=str(value.get("candidate_name") or "").strip()[:120],
			source=source if source in _VALID_SOURCES else "conversation",
			stage=stage,
			score=valid_score,
			recommendation=str(value.get("recommendation") or "").strip()[:80],
			resume_path=str(value.get("resume_path") or "").strip()[:1024],
			last_action=str(value.get("last_action") or "").strip()[:300],
			reason_codes=reasons,
			last_message_id=str(value.get("last_message_id") or "").strip()[:128],
			conversation_version=str(value.get("conversation_version") or "").strip()[:128],
			attachment_retry_at=str(value.get("attachment_retry_at") or "").strip()[:64],
			created_at=str(value.get("created_at") or "").strip()[:64],
			updated_at=str(value.get("updated_at") or "").strip()[:64],
		)


@dataclass(frozen=True, slots=True)
class AutomationCandidateUpsert:
	"""一次队列同步所需的候选人增量字段。

		该对象只描述列表同步允许更新的字段，不暴露终审分数、附件路径等事实字段。
		将输入对象放在队列模块内，能让批量写入保持领域边界，同时避免协调器直接
		拼装持久化字典。
	"""

	friend_id: int
	job_id: str
	candidate_name: str
	source: str
	stage: AutomationCandidateStage | None = None
	last_action: str = ""
	last_message_id: str = ""
	conversation_version: str = ""
	reason_codes: tuple[str, ...] = ()


class AutomationQueueStore:
	"""按 BOSS 会话保存自动化队列，并提供工作台所需投影。

	读写采用同目录临时文件替换，避免浏览器服务或自动化进程重启时留下半个状态
	文件。队列不拥有 RPA 行为，任何平台操作应由上层协调器完成后再更新事实。
	"""

	def __init__(self, data_dir: Path) -> None:
		self._path = data_dir / "recruiter" / _QUEUE_FILENAME
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = Lock()

	def _read(self) -> dict[str, AutomationCandidate]:
		if not self._path.exists():
			return {}
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return {}
		if not isinstance(raw, dict):
			return {}
		return {
			candidate.candidate_key: candidate
			for value in raw.values()
			if (candidate := AutomationCandidate.from_dict(value)) is not None
		}

	def _write(self, candidates: dict[str, AutomationCandidate]) -> None:
		temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
		try:
			with temporary.open("w", encoding="utf-8") as stream:
				json.dump(
					sanitize_json_value({key: value.to_dict() for key, value in candidates.items()}),
					stream,
					ensure_ascii=False,
					indent=2,
					sort_keys=True,
				)
				stream.flush()
				os.fsync(stream.fileno())
			os.replace(temporary, self._path)
		finally:
			try:
				temporary.unlink()
			except FileNotFoundError:
				pass

	@staticmethod
	def _timestamp() -> str:
		return datetime.now(timezone.utc).isoformat(timespec="seconds")

	def upsert_candidate(
		self,
		*,
		friend_id: int,
		job_id: str,
		candidate_name: str,
		source: str,
		stage: AutomationCandidateStage | None = None,
		last_action: str = "",
		last_message_id: str = "",
		conversation_version: str = "",
		reason_codes: tuple[str, ...] = (),
	) -> AutomationCandidate:
		"""同步或推进一位候选人，推荐来源不能被沟通列表来源覆盖。"""
		return self.upsert_candidates([
			AutomationCandidateUpsert(
				friend_id=friend_id,
				job_id=job_id,
				candidate_name=candidate_name,
				source=source,
				stage=stage,
				last_action=last_action,
				last_message_id=last_message_id,
				conversation_version=conversation_version,
				reason_codes=reason_codes,
			)
		])[0]

	def upsert_candidates(self, updates: list[AutomationCandidateUpsert]) -> list[AutomationCandidate]:
		"""一次合并并原子写入多位候选人，避免同步时重复读写整份 JSON。

		BOSS 沟通列表通常包含数百人。旧的逐人接口会对每个候选人读取并 fsync
		整个队列文件，耗时随候选人数平方增长。这里在同一把锁内只读取一次、在
		内存中完成所有合并、最后只写一次；单人更新也通过本方法复用同一语义。
		"""
		if not updates:
			return []
		for update in updates:
			if update.friend_id <= 0 or not update.job_id.strip():
				raise ValueError("候选人会话标识和岗位标识不能为空")
			if update.source not in _VALID_SOURCES:
				raise ValueError("候选人来源无效")
		with self._lock:
			candidates = self._read()
			now = self._timestamp()
			result: list[AutomationCandidate] = []
			for update in updates:
				job_id = update.job_id.strip()
				key = _candidate_key(job_id=job_id, friend_id=update.friend_id)
				existing = candidates.get(key)
				candidate = AutomationCandidate(
					candidate_key=key,
					friend_id=update.friend_id,
					job_id=job_id,
					candidate_name=update.candidate_name.strip()[:120] or (existing.candidate_name if existing else ""),
					source="recommendation" if existing and existing.source == "recommendation" else update.source,
					# 纯列表同步只能补充名称和最新消息，不能把“等待附件”等真实阶段
					# 回退为“已同步”。调用方显式给出阶段时才允许推进。
					stage=update.stage or (existing.stage if existing else AutomationCandidateStage.SYNCED),
					score=existing.score if existing else None,
					recommendation=existing.recommendation if existing else "",
					resume_path=existing.resume_path if existing else "",
					last_action=update.last_action.strip()[:300] or (existing.last_action if existing else ""),
					reason_codes=update.reason_codes or (existing.reason_codes if existing else ()),
					last_message_id=update.last_message_id.strip()[:128] or (existing.last_message_id if existing else ""),
					conversation_version=update.conversation_version.strip()[:128] or (existing.conversation_version if existing else ""),
					attachment_retry_at=existing.attachment_retry_at if existing else "",
					created_at=existing.created_at if existing else now,
					updated_at=now,
				)
				candidates[key] = candidate
				result.append(candidate)
			self._write(candidates)
			return result

	def update_stage(
		self,
		candidate_key: str,
		*,
		stage: AutomationCandidateStage,
		last_action: str = "",
		reason_codes: tuple[str, ...] = (),
		last_message_id: str = "",
	) -> AutomationCandidate | None:
		"""仅推进已存在候选人的平台阶段，未知会话必须先经过同步。"""
		with self._lock:
			candidates = self._read()
			current = candidates.get(candidate_key)
			if current is None:
				return None
			updated = replace(
				current,
				stage=stage,
				last_action=last_action.strip()[:300] or current.last_action,
				reason_codes=reason_codes or current.reason_codes,
				last_message_id=last_message_id.strip()[:128] or current.last_message_id,
				updated_at=self._timestamp(),
			)
			candidates[candidate_key] = updated
			self._write(candidates)
			return updated

	def set_attachment_retry_at(self, candidate_key: str, *, retry_at: str) -> AutomationCandidate | None:
		"""记录等待附件的下一次低频复查时间。

		附件未到达不是失败，也不应让后台每轮重复点击聊天框。将时间写入队列，
		让调度器在进程重启后继续遵守退避；一旦出现未读或版本变化，协调器会
		直接走强信号路径，不受该时间限制。
		"""
		with self._lock:
			candidates = self._read()
			current = candidates.get(candidate_key)
			if current is None:
				return None
			updated = replace(current, attachment_retry_at=retry_at.strip()[:64], updated_at=self._timestamp())
			candidates[candidate_key] = updated
			self._write(candidates)
			return updated

	def record_final_review(
		self,
		*,
		friend_id: int,
		job_id: str,
		score: int,
		recommendation: str,
		resume_path: Path,
	) -> AutomationCandidate:
		"""写入附件终审事实；路径仅在文件真实存在时保留。"""
		if not 0 <= score <= 100:
			raise ValueError("评分必须在 0 到 100 之间")
		key = _candidate_key(job_id=job_id.strip(), friend_id=friend_id)
		with self._lock:
			candidates = self._read()
			current = candidates.get(key)
			if current is None or current.job_id != job_id:
				raise KeyError("候选人尚未同步到该岗位队列")
			path = resume_path.resolve() if resume_path.is_file() and resume_path.stat().st_size > 0 else None
			updated = replace(
				current,
				stage=AutomationCandidateStage.ANALYZED if path is not None else AutomationCandidateStage.MANUAL_REVIEW,
				score=score,
				recommendation=recommendation.strip()[:80],
				resume_path=str(path) if path is not None else "",
				last_action="附件简历终审完成" if path is not None else "附件文件不可用，待人工复核",
				updated_at=self._timestamp(),
			)
			candidates[key] = updated
			self._write(candidates)
			return updated

	def list_for_job(self, job_id: str) -> list[AutomationCandidate]:
		"""返回岗位候选人，终审分数优先且同分按最近更新时间排序。"""
		with self._lock:
			candidates = [candidate for candidate in self._read().values() if candidate.job_id == job_id]
		return sorted(candidates, key=lambda item: (item.score is not None, item.score or -1, item.updated_at), reverse=True)

	def candidate_for_job(self, *, job_id: str, candidate_key: str) -> AutomationCandidate | None:
		"""读取岗位内候选人，防止详情页利用其它岗位的键跨岗位读取状态。"""
		with self._lock:
			candidate = self._read().get(candidate_key)
		return candidate if candidate is not None and candidate.job_id == job_id else None

	def snapshot(
		self,
		job_id: str,
		*,
		qualified_threshold: int,
		visible_friend_ids: tuple[int, ...] | None = None,
	) -> dict[str, list[dict[str, Any]]]:
		"""构造前端投影，合格候选人必须满足终审、阈值和附件三重条件。

		``AutomationQueueStore`` 保存的是长期事实，里面会包含历史沟通和已终审
		记录；自动化执行页展示的却应是“本次 BOSS 列表同步”的短期快照。因此
		调用方可传入 ``visible_friend_ids``，让执行队列按平台当前可见顺序投影，
		避免旧候选人因为分数或更新时间更高而排到实时列表前面。
		"""
		if not 0 <= qualified_threshold <= 100:
			raise ValueError("入选阈值必须在 0 到 100 之间")
		candidates = self.list_for_job(job_id)
		if visible_friend_ids is not None:
			visible_order = {
				friend_id: index
				for index, friend_id in enumerate(dict.fromkeys(visible_friend_ids))
				if friend_id > 0
			}
			candidates = [candidate for candidate in candidates if candidate.friend_id in visible_order]
			candidates = sorted(candidates, key=lambda candidate: visible_order[candidate.friend_id])
		all_rows = [candidate.to_dict() for candidate in candidates]
		qualified = sorted(
			[
			row for row in all_rows
			if row["stage"] == AutomationCandidateStage.ANALYZED.value
			and isinstance(row["score"], int)
			and row["score"] >= qualified_threshold
			and row["recommendation"] != "reject"
			and self.verified_resume_path(str(row["candidate_key"])) is not None
			],
			key=lambda row: (int(row["score"]) if isinstance(row["score"], int) else -1, str(row["updated_at"])),
			reverse=True,
		)
		return {"candidates": all_rows, "qualified": qualified}

	def qualified_pool(self, *, qualified_threshold: int) -> list[dict[str, Any]]:
		"""返回所有岗位的附件终审候选人，供候选人库按岗位分组展示。

		自动化执行页只展示过程队列；最终候选人库则需要跨岗位读取。这里仍沿用
		``snapshot`` 的三重门禁，确保在线简历、无附件和已拒绝记录不会混入结果。
		"""
		if not 0 <= qualified_threshold <= 100:
			raise ValueError("入选阈值必须在 0 到 100 之间")
		with self._lock:
			candidates = list(self._read().values())
		qualified = []
		for candidate in candidates:
			path = Path(candidate.resume_path) if candidate.resume_path else None
			if (
				candidate.stage is AutomationCandidateStage.ANALYZED
				and isinstance(candidate.score, int)
				and candidate.score >= qualified_threshold
				and candidate.recommendation != "reject"
				and path is not None
				and path.is_file()
				and path.stat().st_size > 0
			):
				qualified.append(candidate.to_dict())
		return sorted(
			qualified,
			key=lambda row: (str(row["job_id"]), -(int(row["score"]) if isinstance(row["score"], int) else -1), str(row["candidate_name"])),
		)

	def verified_resume_path(self, candidate_key: str) -> Path | None:
		"""解析队列已验证的附件路径，供 Web 打开接口进行白名单校验。"""
		with self._lock:
			candidate = self._read().get(candidate_key)
		if candidate is None or candidate.stage is not AutomationCandidateStage.ANALYZED or not candidate.resume_path:
			return None
		path = Path(candidate.resume_path)
		return path if path.is_file() and path.stat().st_size > 0 else None
