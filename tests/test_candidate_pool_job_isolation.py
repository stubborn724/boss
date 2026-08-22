"""候选人池按岗位隔离的行为契约。"""

from pathlib import Path

from boss_agent_cli.commands.recruiter.candidate_pool import CandidatePool, PoolEntry


def test_same_friend_can_enter_two_different_job_pools(tmp_path: Path) -> None:
	"""同一候选人匹配两个岗位时应保留两份独立评估结果。"""
	pool = CandidatePool(tmp_path)

	assert pool.add(PoolEntry(candidate_name="张三", friend_id=42, job_id="java", score=85, recommendation="review")) is True
	assert pool.add(PoolEntry(candidate_name="张三", friend_id=42, job_id="support", score=72, recommendation="review")) is True
	assert pool.add(PoolEntry(candidate_name="张三", friend_id=42, job_id="java", score=90, recommendation="review")) is False

	assert [(item.job_id, item.friend_id) for item in pool.list_all()] == [("java", 42), ("support", 42)]
