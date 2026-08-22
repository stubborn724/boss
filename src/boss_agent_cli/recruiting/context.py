"""招聘工作台的账号、企业和角色上下文。

上下文只负责定义隔离边界和本地注册表，不承载 Cookie、令牌或任何平台凭据。
默认上下文沿用旧版单工作区路径，非默认上下文按稳定标识写入独立目录，既能
兼容已有安装，又能防止多企业之间混读岗位、候选人和知识库。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Any


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTEXT_STATE_VERSION = 1


def _validate_identifier(value: str, field_name: str) -> str:
	"""校验只能用于目录和页面选择器的标识，拒绝路径穿越与控制字符。"""
	clean = str(value).strip()
	if not clean:
		raise ValueError(f"{field_name}不能为空")
	if not _IDENTIFIER_PATTERN.fullmatch(clean):
		raise ValueError(f"{field_name}只能包含字母、数字、点、下划线和短横线")
	return clean


@dataclass(frozen=True, slots=True)
class RecruitingContext:
	"""一组彼此隔离的招聘数据和平台登录范围。"""

	workspace_id: str = "default"
	account_id: str = "default"
	company_id: str = "default"
	role: str = "recruiter"

	def __post_init__(self) -> None:
		"""在对象边界完成标识校验，避免不安全值进入文件路径。"""
		for field_name in ("workspace_id", "account_id", "company_id", "role"):
			_validate_identifier(getattr(self, field_name), field_name)

	@property
	def context_key(self) -> str:
		"""返回稳定可读的注册表主键，不包含任何敏感信息。"""
		return ":".join((self.workspace_id, self.account_id, self.company_id, self.role))

	@property
	def is_default(self) -> bool:
		"""判断是否应继续使用旧版单工作区文件布局。"""
		return self == DEFAULT_RECRUITING_CONTEXT

	@property
	def storage_parts(self) -> tuple[str, ...]:
		"""返回目录层级；默认上下文使用空元组以保持旧路径兼容。"""
		return () if self.is_default else (self.workspace_id, self.account_id, self.company_id, self.role)

	def to_dict(self) -> dict[str, str]:
		"""生成可供 API 和注册表保存的非敏感元数据。"""
		return {
			"context_key": self.context_key,
			"workspace_id": self.workspace_id,
			"account_id": self.account_id,
			"company_id": self.company_id,
			"role": self.role,
			"label": f"{self.company_id} / {self.account_id} / {self.role}",
		}

	@classmethod
	def from_dict(cls, raw: Any) -> "RecruitingContext":
		"""从注册表恢复上下文，缺失值回退到默认上下文。"""
		if not isinstance(raw, dict):
			return DEFAULT_RECRUITING_CONTEXT
		return cls(
			workspace_id=str(raw.get("workspace_id") or "default"),
			account_id=str(raw.get("account_id") or "default"),
			company_id=str(raw.get("company_id") or "default"),
			role=str(raw.get("role") or "recruiter"),
		)


DEFAULT_RECRUITING_CONTEXT = RecruitingContext()


def context_data_dir(data_dir: Path, context: RecruitingContext) -> Path:
	"""返回上下文专属运行目录，供认证、浏览器 Profile 和导出文件复用。"""
	if context.is_default:
		return data_dir
	return data_dir / "recruiting" / "contexts" / Path(*context.storage_parts)


class RecruitingContextRegistry:
	"""持久化可切换上下文清单和当前选择，不保存认证材料。"""

	def __init__(self, data_dir: Path) -> None:
		"""初始化注册表；旧安装没有注册表时自动创建默认项。"""
		self._directory = data_dir / "recruiting"
		self._directory.mkdir(parents=True, exist_ok=True)
		self._path = self._directory / "contexts.json"
		self._lock = RLock()
		if not self._path.exists():
			self._write(
				{
					"version": _CONTEXT_STATE_VERSION,
					"active_context": DEFAULT_RECRUITING_CONTEXT.to_dict(),
					"contexts": [DEFAULT_RECRUITING_CONTEXT.to_dict()],
				}
			)

	@property
	def path(self) -> Path:
		"""返回注册表路径，供诊断和测试确认数据落点。"""
		return self._path

	def _read(self) -> dict[str, Any]:
		"""读取注册表并清理损坏或重复条目，避免状态污染后续切换。"""
		try:
			raw = json.loads(self._path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			raise RuntimeError("招聘上下文注册表读取失败，请检查本地数据目录") from exc
		if not isinstance(raw, dict):
			raise RuntimeError("招聘上下文注册表格式无效")
		contexts: dict[str, dict[str, str]] = {}
		for item in raw.get("contexts", []):
			try:
				context = RecruitingContext.from_dict(item)
			except ValueError:
				continue
			contexts[context.context_key] = context.to_dict()
		contexts.setdefault(DEFAULT_RECRUITING_CONTEXT.context_key, DEFAULT_RECRUITING_CONTEXT.to_dict())
		try:
			active = RecruitingContext.from_dict(raw.get("active_context"))
		except ValueError:
			active = DEFAULT_RECRUITING_CONTEXT
		if active.context_key not in contexts:
			active = DEFAULT_RECRUITING_CONTEXT
		return {"version": _CONTEXT_STATE_VERSION, "active_context": active.to_dict(), "contexts": list(contexts.values())}

	def _write(self, state: dict[str, Any]) -> None:
		"""原子写入注册表，临时文件与目标文件保持同目录。"""
		tmp = self._directory / f".contexts.{os.getpid()}.{secrets.token_hex(4)}.tmp"
		try:
			with tmp.open("w", encoding="utf-8", newline="\n") as handle:
				handle.write(json.dumps(state, ensure_ascii=False, indent=2))
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(tmp, self._path)
		except OSError as exc:
			try:
				tmp.unlink(missing_ok=True)
			except OSError:
				pass
			raise RuntimeError("招聘上下文注册表写入失败，请检查本地数据目录") from exc

	def list_contexts(self) -> list[RecruitingContext]:
		"""按注册顺序返回上下文，不暴露账号凭据。"""
		with self._lock:
			state = self._read()
		return [RecruitingContext.from_dict(item) for item in state["contexts"]]

	def active(self) -> RecruitingContext:
		"""读取当前上下文；注册表异常由调用方转换成安全错误。"""
		with self._lock:
			return RecruitingContext.from_dict(self._read()["active_context"])

	def activate(self, context: RecruitingContext) -> RecruitingContext:
		"""登记并切换上下文；只变更本地工作区选择，不自动登录外部平台。"""
		if not isinstance(context, RecruitingContext):
			raise TypeError("context 必须是 RecruitingContext")
		with self._lock:
			state = self._read()
			items = {item.context_key: item for item in (RecruitingContext.from_dict(raw) for raw in state["contexts"])}
			items[context.context_key] = context
			state["active_context"] = context.to_dict()
			state["contexts"] = [item.to_dict() for item in items.values()]
			self._write(state)
		return context

	def as_dict(self) -> dict[str, Any]:
		"""返回前端上下文选择器使用的完整元数据快照。"""
		with self._lock:
			state = self._read()
		return {
			"active_context": dict(state["active_context"]),
			"contexts": [dict(item) for item in state["contexts"]],
		}
