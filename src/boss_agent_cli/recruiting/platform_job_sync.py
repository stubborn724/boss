"""BOSS 职位到本地招聘工作台的只读镜像服务。

本模块的职责只限于将已登录账号可读取的职位标识和名称镜像到本地岗位；它不
调用任何平台写接口，也不修改 HR 已维护的筛选标准、权重、知识库、FAQ 或岗位
发布状态。这样平台职位变更与本地评估规则可以独立演进，并保留完整审计边界。
"""

from __future__ import annotations

import re
from typing import Any

from boss_agent_cli.recruiting.models import utc_now_iso
from boss_agent_cli.recruiting.store import RecruitingStore


_SYNTHETIC_RPA_JOB_ID_PREFIX = "rpa-"
_BOSS_DETAIL_FIELDS = (
	"city",
	"salary_range",
	"education_requirement",
	"description",
	"keywords",
	"experience_requirement",
	"internship_requirement",
	"work_days",
	"work_address",
)


def _normalized_job_name(name: str) -> str:
	"""生成 RPA 降级镜像使用的保守名称键。

	职位管理页未暴露真实职位 ID 时，RPA 只能依据当前页面可见名称构造临时
	标识。这里仅折叠空白并忽略大小写，不进行模糊语义匹配，避免把名称相近的
	两个真实职位错误合并。
	"""
	return re.sub(r"\s+", " ", name).strip().casefold()


def _is_synthetic_rpa_job_id(platform_job_id: str) -> bool:
	"""判断标识是否来自无真实 ID 的 RPA 降级读取。"""
	return platform_job_id.startswith(_SYNTHETIC_RPA_JOB_ID_PREFIX)


class PlatformJobSyncService:
	"""把归一化的平台职位列表幂等同步为当前工作区的本地岗位镜像。"""

	def __init__(self, store: RecruitingStore) -> None:
		"""注入 Store，方便 CLI/Web 共享同一份岗位和审计数据。"""
		self._store = store

	@staticmethod
	def _snapshot_from_platform_record(raw: dict[str, object], *, platform_job_id: str, name: str, status: str) -> dict[str, str]:
		"""构造字段白名单快照，拒绝将平台原始记录整体持久化。

		职位描述属于用户要求展示的 BOSS 事实，但原始响应还可能夹带内部标识、
		埋点和联系人数据。仅保留岗位编辑器需要的基础字段，既支持后续展示，
		又保持镜像数据最小化。
		"""
		snapshot = {"job_id": platform_job_id, "name": name, "status": status}
		for field_name in _BOSS_DETAIL_FIELDS:
			value = str(raw.get(field_name) or "").strip()
			if value:
				snapshot[field_name] = value
		return snapshot

	@staticmethod
	def _apply_boss_detail_fields(job: Any, snapshot: dict[str, str]) -> None:
		"""将已同步的 BOSS 硬条件写入岗位基础字段，不触碰 HR 的筛选规则。"""
		for field_name in ("city", "salary_range", "education_requirement"):
			value = snapshot.get(field_name, "").strip()
			if value:
				setattr(job, field_name, value)
		keywords = snapshot.get("keywords", "").strip()
		if keywords:
			# BOSS 的关键词既要展示，也用于第一层硬条件和技术能力的稳定匹配。
			job.skills = list(dict.fromkeys(item.strip() for item in re.split(r"[,，、/／|]+", keywords) if item.strip()))

	def sync(self, platform_jobs: list[dict[str, object]]) -> dict[str, Any]:
		"""同步职位快照并返回新增、更新和未再发现的数量。

		只有带非空 ``job_id`` 与 ``name`` 的归一化记录会被处理。平台本次未
		返回的历史镜像仅标记为 ``not_discovered``，不能删除，避免候选人报告
		因暂时网络异常失去关联岗位。
		"""
		now = utc_now_iso()
		incoming: dict[str, dict[str, str]] = {}
		for raw in platform_jobs:
			platform_job_id = str(raw.get("job_id") or "").strip()
			name = str(raw.get("name") or "").strip()
			if platform_job_id and name:
				# 只有 BOSS 明确返回关闭状态时才标记关闭；未知和未来枚举一律按
				# 可用处理，避免平台字段漂移把仍在招聘的岗位误关停。
				status = "closed" if str(raw.get("status") or "").strip().casefold() in {"closed", "offline"} else "active"
				incoming[platform_job_id] = self._snapshot_from_platform_record(
					raw,
					platform_job_id=platform_job_id,
					name=name,
					status=status,
				)

		existing_by_platform_id = {
			job.platform_job_id: job
			for job in self._store.list_jobs()
			if job.source == "boss" and job.platform_job_id
		}
		# 同一职位管理页在不同加载阶段可能先只暴露名称、后又暴露另一个 RPA
		# 临时键。真实 BOSS ID 仍必须严格按 ID 对齐；仅对 ``rpa-`` 临时键允许
		# 以规范化名称恢复同一镜像，防止每次同步都新增一个同名岗位。
		existing_synthetic_by_name: dict[str, list[Any]] = {}
		for existing_job in existing_by_platform_id.values():
			if not _is_synthetic_rpa_job_id(existing_job.platform_job_id):
				continue
			existing_synthetic_by_name.setdefault(_normalized_job_name(existing_job.name), []).append(existing_job)
		jobs: list[dict[str, str]] = []
		created = 0
		updated = 0
		matched_existing_platform_ids: set[str] = set()
		for platform_job_id, snapshot in incoming.items():
			job = existing_by_platform_id.get(platform_job_id)
			if job is None and _is_synthetic_rpa_job_id(platform_job_id):
				candidates = existing_synthetic_by_name.get(_normalized_job_name(snapshot["name"]), [])
				if candidates:
					# 多条历史临时镜像只能保留一个承接当前职位。优先选择已有
					# 评估记录的岗位，确保候选人分数和审计关系不会丢失；其余
					# 记录随后会按未发现状态保留，而不会继续污染当前选择器。
					job = max(
						candidates,
						key=lambda item: (
							self._store.has_assessments_for_job(item.job_id),
							item.last_synced_at,
							item.job_id,
						),
					)
			if job is None:
				job = self._store.create_job(
					name=snapshot["name"],
					status="draft",
				)
				job.source = "boss"
				job.platform_job_id = platform_job_id
				job.platform_snapshot = snapshot
				# 新同步岗位只有平台基础事实，没有人工确认的补充规则，不能直接
				# 进入自动筛选。岗位管理审核保存后再启用。
				job.rules_confirmed = False
				self._apply_boss_detail_fields(job, snapshot)
				job.last_synced_at = now
				job.platform_sync_status = snapshot["status"]
				job = self._store.update_job(job)
				created += 1
			else:
				# 此处禁止重新构造 JobProfile；直接更新镜像字段可保留 HR 对
				# criteria、weights 与 published/draft 生命周期的所有人工决定。
				job.name = snapshot["name"]
				# 临时键没有跨页面稳定性。当前条目仍然没有真实 BOSS ID 时，
				# 更新为本次可复用的临时键；将来读取到真实 ID 的路径不会进入
				# 此分支，因此不会覆盖真实平台关联。
				if _is_synthetic_rpa_job_id(platform_job_id):
					job.platform_job_id = platform_job_id
				# RPA 详情读取可能因页面加载时序只返回摘要。合并快照可以
				# 保留上次已确认的 BOSS 字段，避免一次不完整同步清空描述或
				# 实习条件；本次返回的非空字段仍然优先更新。
				merged_snapshot = dict(job.platform_snapshot)
				merged_snapshot.update(snapshot)
				job.platform_snapshot = merged_snapshot
				self._apply_boss_detail_fields(job, merged_snapshot)
				job.last_synced_at = now
				job.platform_sync_status = snapshot["status"]
				job = self._store.update_job(job)
				updated += 1
			matched_existing_platform_ids.add(job.platform_job_id)
			jobs.append({"job_id": job.job_id, "name": job.name, "platform_job_id": platform_job_id})

		not_discovered = 0
		for platform_job_id, job in existing_by_platform_id.items():
			if (
				platform_job_id not in incoming
				and platform_job_id not in matched_existing_platform_ids
				and job.platform_sync_status != "not_discovered"
			):
				job.platform_sync_status = "not_discovered"
				job.last_synced_at = now
				self._store.update_job(job)
				not_discovered += 1

		return {
			"jobs": jobs,
			"created": created,
			"updated": updated,
			"not_discovered": not_discovered,
			"synced_at": now,
		}
