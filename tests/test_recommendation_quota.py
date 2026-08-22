"""推荐牛人账号级每日沟通上限的行为测试。"""

from datetime import datetime
from pathlib import Path

from boss_agent_cli.recruiting.recommendation_quota import (
	RECOMMENDATION_DAILY_QUOTA_REACHED,
	RecommendationQuotaStore,
)


def test_recommendation_quota_blocks_all_jobs_for_the_same_day(tmp_path: Path) -> None:
	"""一个岗位触发上限后，同账号其它岗位当天也必须被阻断。"""
	store = RecommendationQuotaStore(tmp_path)
	now = datetime(2026, 8, 21, 10, 30)

	assert store.is_blocked(now=now) is False
	store.mark_reached(message="BOSS 推荐牛人今日沟通已达上限", now=now)

	assert store.is_blocked(now=datetime(2026, 8, 21, 18, 0)) is True
	status = store.status(now=now)
	assert status["blocked"] is True
	assert status["error_code"] == RECOMMENDATION_DAILY_QUOTA_REACHED


def test_recommendation_quota_recovers_on_the_next_local_day(tmp_path: Path) -> None:
	"""推荐上限只对触发当天有效，次日自动恢复。"""
	store = RecommendationQuotaStore(tmp_path)
	store.mark_reached(message="达到上限", now=datetime(2026, 8, 21, 23, 59))

	assert store.is_blocked(now=datetime(2026, 8, 22, 0, 1)) is False
	assert store.status(now=datetime(2026, 8, 22, 0, 1))["blocked"] is False


def test_corrupt_recommendation_quota_state_fails_open(tmp_path: Path) -> None:
	"""损坏的本地状态不能把推荐入口永久锁死，也不能影响沟通列表。"""
	store = RecommendationQuotaStore(tmp_path)
	store.path.write_text("not-json", encoding="utf-8")

	assert store.is_blocked(now=datetime(2026, 8, 21, 10, 0)) is False
	assert store.status(now=datetime(2026, 8, 21, 10, 0))["blocked"] is False
