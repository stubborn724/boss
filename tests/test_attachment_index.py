"""附件扫描索引测试。

索引只是徽标缓存：读取必须永不抛错、过期条目必须自动失效、且不得保存候选人
姓名或平台地址。
"""

from datetime import datetime, timedelta, timezone
import json

from boss_agent_cli.commands.recruiter.attachment_index import (
	STATUS_ABSENT,
	STATUS_AVAILABLE,
	AttachmentIndex,
)


def test_index_round_trips_statuses(tmp_path) -> None:
	"""写入后应能按会话标识读回判定结果。"""
	index = AttachmentIndex.for_data_dir(tmp_path)

	index.update({1: STATUS_AVAILABLE, 2: STATUS_ABSENT})

	assert index.read() == {1: STATUS_AVAILABLE, 2: STATUS_ABSENT}


def test_index_merges_without_losing_other_candidates(tmp_path) -> None:
	"""只扫描一部分人时，其他人的既有判定不能被清掉。"""
	index = AttachmentIndex.for_data_dir(tmp_path)
	index.update({1: STATUS_AVAILABLE})

	index.update({2: STATUS_ABSENT})

	assert index.read() == {1: STATUS_AVAILABLE, 2: STATUS_ABSENT}


def test_index_only_stores_status_and_timestamp(tmp_path) -> None:
	"""落盘内容必须只有状态和时间，不能出现姓名或下载地址。"""
	index = AttachmentIndex.for_data_dir(tmp_path)
	index.update({7: STATUS_AVAILABLE})

	raw = json.loads(index.path.read_text(encoding="utf-8"))

	assert raw["version"] == 1
	assert set(raw["entries"]["7"]) == {"status", "checked_at"}


def test_index_ignores_expired_entries(tmp_path) -> None:
	"""附件是候选人随时可能补发的东西，旧结论不能一直当事实用。"""
	index = AttachmentIndex.for_data_dir(tmp_path)
	index.path.parent.mkdir(parents=True, exist_ok=True)
	stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
	index.path.write_text(
		json.dumps({"version": 1, "entries": {"3": {"status": STATUS_AVAILABLE, "checked_at": stale}}}),
		encoding="utf-8",
	)

	assert index.read() == {}


def test_index_read_survives_corrupted_file(tmp_path) -> None:
	"""坏文件只能让徽标降级为未检测，绝不能让沟通列表读取失败。"""
	index = AttachmentIndex.for_data_dir(tmp_path)
	index.path.parent.mkdir(parents=True, exist_ok=True)
	index.path.write_text("{not json", encoding="utf-8")

	assert index.read() == {}


def test_index_read_returns_empty_when_missing(tmp_path) -> None:
	"""从未扫描过时读取应返回空字典，而不是抛文件缺失。"""
	assert AttachmentIndex.for_data_dir(tmp_path).read() == {}


def test_index_rejects_unknown_status_values(tmp_path) -> None:
	"""未知状态不能进入索引，避免页面渲染出无意义徽标。"""
	index = AttachmentIndex.for_data_dir(tmp_path)

	index.update({1: "maybe"})

	assert index.read() == {}


def test_index_ignores_non_positive_friend_ids(tmp_path) -> None:
	"""非法会话标识不落盘，防止索引被写进无法对应的条目。"""
	index = AttachmentIndex.for_data_dir(tmp_path)

	index.update({0: STATUS_AVAILABLE, -1: STATUS_ABSENT, True: STATUS_AVAILABLE})

	assert index.read() == {}
