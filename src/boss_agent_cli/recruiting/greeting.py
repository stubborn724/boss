"""岗位打招呼语的本地配置与 RPA 同步判定。

该模块只负责内容校验和幂等哈希。实际写入 BOSS 设置页面必须由 RPA 客户端完成，
从而让平台副作用与本地岗位配置明确分层。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_MAX_GREETING_LENGTH = 100


def build_default_job_greeting(job_name: str) -> str:
	"""生成推荐牛人的默认首轮话术。

	推荐页面的开场只承担基础信息确认，不应混入专业题或拆成多轮追问。把学历、
	相关经验、所在城市和到岗时间合并为一个明确问题，既让后续硬筛有足够事实，
	也避免候选人因连续短消息产生额外沟通成本。岗位名称必须完整写入，因此超出
	BOSS 字符限制时明确拒绝，由 HR 缩短岗位名称或改用手工话术，而不是静默截断。
	"""
	clean_job_name = re.sub(r"\s+", " ", job_name).strip()
	if not clean_job_name:
		raise ValueError("岗位名称不能为空，无法生成推荐打招呼语")
	content = (
		f"您好，感谢关注{clean_job_name}岗位。"
		"方便确认您的最高学历、相关工作年限、所在城市及到岗时间吗？"
	)
	if not GreetingConfiguration(content=content).is_valid:
		raise ValueError("岗位名称过长，生成的推荐打招呼语超过 BOSS 100 字限制")
	return content


@dataclass(frozen=True)
class GreetingConfiguration:
	"""一份岗位或通用招呼语配置，兼容 BOSS 页面字符上限。"""

	content: str

	@property
	def normalised_content(self) -> str:
		"""压缩展示空白，使内容哈希只反映真实话术变化。"""
		return re.sub(r"\s+", " ", self.content).strip()

	@property
	def content_hash(self) -> str:
		"""生成用于跳过重复同步的稳定 SHA-256 标识。"""
		return hashlib.sha256(self.normalised_content.encode("utf-8")).hexdigest()

	@property
	def validation_error(self) -> str:
		"""返回受控校验码，调用方可显示中文解释而不泄露内容。"""
		if not self.normalised_content:
			return "greeting_empty"
		if len(self.normalised_content) > _MAX_GREETING_LENGTH:
			return "greeting_too_long"
		return ""

	@property
	def is_valid(self) -> bool:
		"""只允许非空且在 BOSS 页面限制内的话术进入同步。"""
		return not self.validation_error

	def needs_sync(self, *, last_synced_hash: str) -> bool:
		"""内容合法且哈希变化时才需要触发 RPA 设置页面操作。"""
		return self.is_valid and self.content_hash != last_synced_hash
