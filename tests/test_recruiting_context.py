"""招聘上下文隔离与注册表行为测试。"""

from pathlib import Path

import pytest

from boss_agent_cli.recruiting.context import (
	DEFAULT_RECRUITING_CONTEXT,
	RecruitingContext,
	RecruitingContextRegistry,
)
from boss_agent_cli.recruiting.store import RecruitingStore


def test_non_default_contexts_use_independent_workspace_files(tmp_path: Path) -> None:
	"""不同企业上下文不能共享岗位状态文件。"""
	first = RecruitingContext(account_id="account-a", company_id="company-a")
	second = RecruitingContext(account_id="account-b", company_id="company-b")

	first_store = RecruitingStore(tmp_path, context=first)
	second_store = RecruitingStore(tmp_path, context=second)
	first_store.create_job(name="A 企业销售")
	second_store.create_job(name="B 企业销售")

	assert [job.name for job in first_store.list_jobs()] == ["A 企业销售"]
	assert [job.name for job in second_store.list_jobs()] == ["B 企业销售"]
	assert first_store.state_path != second_store.state_path
	assert first_store.state_path.exists()
	assert second_store.state_path.exists()


def test_default_context_keeps_legacy_workspace_path(tmp_path: Path) -> None:
	"""升级现有单账号用户时，默认上下文仍使用原来的文件位置。"""
	store = RecruitingStore(tmp_path, context=DEFAULT_RECRUITING_CONTEXT)

	assert store.state_path == tmp_path / "recruiting" / "workspace.json"


def test_context_registry_round_trips_active_context(tmp_path: Path) -> None:
	"""上下文注册表能持久化新增上下文和当前选择。"""
	registry = RecruitingContextRegistry(tmp_path)
	context = RecruitingContext(
		workspace_id="sales-workspace",
		account_id="account-a",
		company_id="company-a",
		role="recruiter",
	)

	assert registry.active() == DEFAULT_RECRUITING_CONTEXT
	registry.activate(context)

	reloaded = RecruitingContextRegistry(tmp_path)
	assert reloaded.active() == context
	assert context in reloaded.list_contexts()
	assert reloaded.as_dict()["active_context"]["context_key"] == context.context_key


@pytest.mark.parametrize("field", ["workspace_id", "account_id", "company_id", "role"])
def test_context_rejects_path_unsafe_identifiers(field: str) -> None:
	"""上下文标识只能用于目录名，必须拒绝路径穿越和控制字符。"""
	with pytest.raises(ValueError):
		RecruitingContext(**{field: "../outside"})
