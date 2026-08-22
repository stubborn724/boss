"""统一自动化协调器的来源汇合与可恢复调度测试。"""

from pathlib import Path
from threading import Event, Lock
from time import sleep
from datetime import datetime, timedelta, timezone

from boss_agent_cli.recruiting.automation_coordinator import (
	AutomationCandidateEvent,
	AutomationCoordinator,
	AutomationPhaseError,
	ConversationSeed,
)
from boss_agent_cli.recruiting.automation_queue import (
	AutomationCandidateStage,
	AutomationQueueStore,
)


def test_automation_deadline_blocks_platform_work_after_cutoff(tmp_path: Path) -> None:
	"""到达截止时间后，启动自动化不能再读取列表或执行任何平台动作。"""
	platform_calls: list[str] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: platform_calls.append("sync") or [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: platform_calls.append("dialogue") or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: platform_calls.append("attachment") or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: platform_calls.append("greet") or [],
		hard_stop_at=datetime(2026, 8, 15, 21, 0, tzinfo=timezone.utc),
		clock=lambda: datetime(2026, 8, 15, 21, 0, 1, tzinfo=timezone.utc),
	)

	result = coordinator.start(job_id="job-java", source="conversation", limit=20)

	assert result["state"] == "blocked"
	assert platform_calls == []
	assert coordinator.status()["activities"][0]["status"] == "blocked"


def test_run_once_syncs_then_processes_and_finalizes_same_queue(tmp_path: Path) -> None:
	"""沟通同步、AI 推进和附件终审必须汇入同一 friend_id 候选人。"""
	seen: list[tuple[str, tuple[int, ...]]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=9, candidate_name="赵六")],
		process_dialogue=lambda job_id, friend_ids, _stop_event: (
			seen.append((job_id, friend_ids))
			or [AutomationCandidateEvent(friend_id=9, stage=AutomationCandidateStage.WAITING_ATTACHMENT, action="已发送附件请求")]
		),
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [
			AutomationCandidateEvent(friend_id=9, stage=AutomationCandidateStage.ANALYZED, action="附件终审完成", score=86, recommendation="invite_to_interview", resume_path=_attachment(tmp_path))
		],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	report = coordinator.run_once(job_id="job-java", include_recommendations=False, limit=10)

	assert seen == [("job-java", (9,))]
	assert report["synced"] == 1
	assert report["analyzed"] == 1
	assert coordinator.queue.snapshot("job-java", qualified_threshold=70)["qualified"][0]["candidate_name"] == "赵六"


def test_sync_does_not_overwrite_existing_candidate_action(tmp_path: Path) -> None:
	"""增量同步只能更新列表元数据，不能把已发送问题的动作覆盖成普通同步。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=9,
		job_id="job-java",
		candidate_name="已问基础问题",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
		last_action="已发送基础问题，等待候选人回复",
	)
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=9, candidate_name="已问基础问题")],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)

	candidate = store.candidate_for_job(job_id="job-java", candidate_key="job:job-java:friend:9")
	assert candidate is not None
	assert candidate.last_action == "已发送基础问题，等待候选人回复"


def test_recommendation_activity_does_not_create_unbound_queue_candidate(tmp_path: Path) -> None:
	"""推荐卡片没有 friend_id 前只能记录已招呼活动，不能猜测沟通会话。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: ["已向推荐候选人发送招呼"],
	)

	report = coordinator.run_once(job_id="job-java", include_recommendations=True, limit=1)

	assert report["greeted"] == 1
	assert coordinator.queue.list_for_job("job-java") == []
	assert coordinator.status()["activities"][0]["action"] == "已向推荐候选人发送招呼"


def test_run_once_processes_existing_dialogue_before_greeting_recommendations(tmp_path: Path) -> None:
	"""已有候选人对话必须先推进，避免自动化不断打开新沟通却不收口旧沟通。"""
	actions: list[str] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=31, candidate_name="已回复候选人")],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: actions.append("process") or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: actions.append("finalize") or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: actions.append("greet") or ["已向推荐候选人发送招呼"],
	)

	report = coordinator.run_once(job_id="job-java", include_recommendations=True, limit=10)

	# 没有附件目标时不应再调用空终审器；这能避免无意义的平台操作，真实对话
	# 处理仍必须在推荐招呼之前完成。
	assert actions == ["process", "greet"]
	assert report["greeted"] == 1


def test_daily_recommendation_quota_skips_recommendations_but_keeps_conversation_processing(tmp_path: Path) -> None:
	"""账号级推荐上限只停推荐来源，沟通列表处理仍要继续。"""
	actions: list[str] = []
	class _Quota:
		def is_blocked(self) -> bool:
			return True

		def status(self) -> dict[str, object]:
			return {"blocked": True, "message": "BOSS 推荐牛人今日沟通已达上限"}

	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=8, candidate_name="已回复")],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: actions.append("conversation") or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: actions.append("recommendation") or ["不应发送"],
		recommendation_quota=_Quota(),
	)

	report = coordinator.run_once(job_id="job-java", include_recommendations=True, limit=1)

	assert actions == ["conversation"]
	assert report["greeted"] == 0
	assert report["recommendation_blocked"] == 1


def test_daily_recommendation_quota_does_not_overwrite_running_conversation_automation(tmp_path: Path) -> None:
	"""运行中的沟通列表不能被一次推荐上限提示覆盖为 stopped 或 blocked。"""
	from threading import Event

	release = Event()
	started = Event()

	class _Quota:
		def is_blocked(self) -> bool:
			return True

		def status(self) -> dict[str, object]:
			return {"blocked": True, "message": "BOSS 推荐牛人今日沟通已达上限"}

	def sync(_job_id: str) -> list[ConversationSeed]:
		started.set()
		release.wait(timeout=1)
		return []

	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=sync,
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		recommendation_quota=_Quota(),
	)

	assert coordinator.start(job_id="job-java", source="conversation", limit=1)["state"] == "running"
	assert started.wait(timeout=1)
	result = coordinator.start(job_id="job-java", source="recommendation", limit=1)

	assert result["state"] == "running"
	assert result["error"]["code"] == "RECOMMENDATION_DAILY_QUOTA_REACHED"
	release.set()
	coordinator.stop()
	assert coordinator.wait_until_stopped(timeout=1)


def test_run_once_rechecks_waiting_attachment_candidates_on_the_next_cycle(tmp_path: Path) -> None:
	"""进入等待附件后，即使候选人没有未读标记也必须继续检查并下载。"""
	finalized: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=17, candidate_name="待发简历")],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [
			AutomationCandidateEvent(
				friend_id=17,
				stage=AutomationCandidateStage.WAITING_ATTACHMENT,
				action="已发送索要简历消息",
			)
		],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: (
			finalized.append(friend_ids)
			or []
		),
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	# 首轮索要附件后立即检查一次；没有新信号时，后续轮询遵守五分钟退避。
	assert finalized == [(17,)]


def test_waiting_attachment_is_deferred_until_retry_time(tmp_path: Path) -> None:
	"""没有新消息时，等待附件候选人不能被每个轮询周期反复打开。"""
	current_time = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
	finalized: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=17, candidate_name="待发简历")],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [
			AutomationCandidateEvent(
				friend_id=17,
				stage=AutomationCandidateStage.WAITING_ATTACHMENT,
				action="已发送索要简历消息",
			)
		],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: (
			finalized.append(friend_ids)
			or [
				AutomationCandidateEvent(
					friend_id=17,
					stage=AutomationCandidateStage.WAITING_ATTACHMENT,
					action="等待候选人发送附件",
				)
			]
		),
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		clock=lambda: current_time,
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert finalized == [(17,)]

	current_time += timedelta(minutes=5)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert finalized == [(17,), (17,)]


def test_run_once_prioritizes_unread_attachment_before_regular_dialogue(tmp_path: Path) -> None:
	"""候选人已发送附件的未读会话必须先同意下载，不能再被新人沟通排到后面。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=1,
		job_id="job-java",
		candidate_name="已发附件",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_ATTACHMENT,
	)
	order: list[tuple[str, tuple[int, ...]]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="已发附件", unread_count=1),
			ConversationSeed(friend_id=2, candidate_name="待首次检查"),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: order.append(("dialogue", friend_ids)) or [],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: order.append(("attachment", friend_ids)) or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert order == [("attachment", (1,)), ("dialogue", (2,))]


def test_attachment_version_change_is_prioritized_after_unread_badge_is_cleared(tmp_path: Path) -> None:
	"""人工点开清除红点后，附件会话的新版本仍必须优先进入终审。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=1,
		job_id="job-java",
		candidate_name="已发附件",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_ATTACHMENT,
		conversation_version="old-version",
	)
	finalized: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="已发附件", conversation_version="new-version"),
		],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: finalized.append(friend_ids) or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert finalized == [(1,)]


def test_pause_interrupts_the_current_cycle_before_attachment_or_greeting(tmp_path: Path) -> None:
	"""暂停发生在沟通批次中时，当前单人动作结束后不得继续附件或推荐动作。"""
	actions: list[str] = []
	coordinator: AutomationCoordinator

	def process_dialogue(_job_id: str, _friend_ids: tuple[int, ...], control: object) -> list[AutomationCandidateEvent]:
		actions.append("dialogue")
		coordinator.pause()
		assert control.is_set() is True
		return []

	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=1, candidate_name="候选人")],
		process_dialogue=process_dialogue,
		finalize_attachments=lambda _job_id, _friend_ids, _control: actions.append("attachment") or [],
		greet_recommendations=lambda _job_id, _limit, _control: actions.append("greet") or [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=True, limit=1)

	assert actions == ["dialogue"]


def test_run_once_limits_and_rotates_attachment_checks(tmp_path: Path) -> None:
	"""附件终审每轮只处理小批次，不能长时间阻塞沟通回复轮询。"""
	store = AutomationQueueStore(tmp_path)
	for friend_id in (1, 2, 3):
		store.upsert_candidate(
			friend_id=friend_id,
			job_id="job-java",
			candidate_name=f"待附件{friend_id}",
			source="conversation",
			stage=AutomationCandidateStage.WAITING_ATTACHMENT,
		)
	finalized: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1),
			ConversationSeed(friend_id=2),
			ConversationSeed(friend_id=3),
		],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: finalized.append(friend_ids) or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)

	assert finalized == [(1, 2), (3, 1)]


def test_run_once_prioritizes_current_conversation_order_over_historical_stage(tmp_path: Path) -> None:
	"""点击启动后应从本轮 BOSS 沟通列表第一位开始，不被历史阶段抢占。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=42,
		job_id="job-java",
		candidate_name="正在沟通",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
	)
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=11, candidate_name="新同步候选人"),
			ConversationSeed(friend_id=42, candidate_name="正在沟通"),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert processed == [(11,)]


def test_run_once_appends_active_candidate_missing_from_current_snapshot(tmp_path: Path) -> None:
	"""本轮快照之外的历史候选人应留到下一轮，避免列表变化打断当前批次。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=42,
		job_id="job-java",
		candidate_name="正在沟通",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
	)
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=11, candidate_name="当前第一位")],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=10)

	assert processed == [(11,)]


def test_run_once_prioritizes_unread_candidates_while_preserving_snapshot_order(tmp_path: Path) -> None:
	"""本轮有未读时只处理未读候选人，不能顺手重开无回复会话。"""
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="未回复", unread_count=0),
			ConversationSeed(friend_id=2, candidate_name="已回复二", unread_count=1),
			ConversationSeed(friend_id=3, candidate_name="已回复三", unread_count=2),
			ConversationSeed(friend_id=4, candidate_name="未回复四", unread_count=0),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=10)

	assert processed == [(2, 3)]
	assert [
		coordinator.queue.candidate_for_job(job_id="job-java", candidate_key=f"job:job-java:friend:{friend_id}").stage
		for friend_id in (2, 3)
	] == [AutomationCandidateStage.WAITING_CANDIDATE, AutomationCandidateStage.WAITING_CANDIDATE]


def test_run_once_advances_to_the_next_unchecked_snapshot_candidate(tmp_path: Path) -> None:
	"""首屏旧会话无未读时，必须继续首轮快照中下一位未检查候选人。"""
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="首屏已检查"),
			ConversationSeed(friend_id=2, candidate_name="下一位新人"),
			ConversationSeed(friend_id=3, candidate_name="更靠后新人"),
		],
		sync_recent_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="首屏已检查", unread_count=0),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert processed == [(1,), (2,)]


def test_run_once_prioritizes_recent_unread_over_the_next_snapshot_candidate(tmp_path: Path) -> None:
	"""候选人回复后必须打断新候选人的首次检查，优先完成已有会话。"""
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="首屏已检查"),
			ConversationSeed(friend_id=2, candidate_name="下一位新人"),
			ConversationSeed(friend_id=3, candidate_name="已回复候选人"),
		],
		sync_recent_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="首屏已检查", unread_count=0),
			ConversationSeed(friend_id=3, candidate_name="已回复候选人", unread_count=1),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert processed == [(1,), (3,)]


def test_run_once_rechecks_waiting_candidate_when_list_message_version_changes(tmp_path: Path) -> None:
	"""红点被点掉后，列表消息版本变化仍应让等待中的候选人回到处理队列。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=1,
		job_id="job-java",
		candidate_name="红点已清除的回复",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
		conversation_version="previous-version",
	)
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="红点已清除的回复", conversation_version="previous-version"),
			ConversationSeed(friend_id=2, candidate_name="首轮未检查候选人"),
		],
		sync_recent_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, candidate_name="红点已清除的回复", conversation_version="new-version"),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert processed == [(2,), (1,)]


def test_run_once_keeps_excess_changed_candidates_for_following_batches(tmp_path: Path) -> None:
	"""版本变化超过批量上限时，剩余候选人必须在下一轮继续核验。"""
	store = AutomationQueueStore(tmp_path)
	for friend_id in (1, 2, 3):
		store.upsert_candidate(
			friend_id=friend_id,
			job_id="job-java",
			candidate_name=f"等待回复{friend_id}",
			source="conversation",
			stage=AutomationCandidateStage.WAITING_CANDIDATE,
			conversation_version="old-version",
		)
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, conversation_version="old-version"),
			ConversationSeed(friend_id=2, conversation_version="old-version"),
			ConversationSeed(friend_id=3, conversation_version="old-version"),
			ConversationSeed(friend_id=4),
		],
		sync_recent_conversations=lambda _job_id: [
			ConversationSeed(friend_id=1, conversation_version="new-version"),
			ConversationSeed(friend_id=2, conversation_version="new-version"),
			ConversationSeed(friend_id=3, conversation_version="new-version"),
		],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert processed == [(4,), (1,), (2,)]


def test_sync_once_updates_queue_without_processing_or_sending_messages(tmp_path: Path) -> None:
	"""单独同步只能写入 BOSS 沟通身份，不能触发 AI、消息或附件动作。"""
	called: list[str] = []
	synced_jobs: list[str] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda job_id: synced_jobs.append(job_id) or [ConversationSeed(friend_id=12, candidate_name="只读同步")],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: called.append("dialogue") or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: called.append("attachment") or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: called.append("greeting") or [],
	)

	synced = coordinator.sync_once(job_id="job-java")

	assert synced == 1
	assert called == []
	assert synced_jobs == ["job-java"]
	assert coordinator.queue.list_for_job("job-java")[0].candidate_name == "只读同步"


def test_run_once_uses_incremental_unread_sync_after_initial_snapshot_is_drained(tmp_path: Path) -> None:
	"""全量快照处理完后，轮询只能检查轻量未读窗口，不能反复翻页读取全部会话。"""
	full_sync_calls: list[str] = []
	incremental_sync_calls: list[str] = []
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda job_id: full_sync_calls.append(job_id) or [ConversationSeed(friend_id=1, candidate_name="首轮候选人")],
		sync_recent_conversations=lambda job_id: incremental_sync_calls.append(job_id) or [ConversationSeed(friend_id=2, candidate_name="新回复", unread_count=1)],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert full_sync_calls == ["job-java"]
	assert incremental_sync_calls == ["job-java"]
	assert processed == [(1,), (2,)]


def test_run_once_keeps_incremental_sync_when_snapshot_is_drained_without_new_reply(tmp_path: Path) -> None:
	"""首轮新人检查完且无人回复时，20 秒轮询不能重扫整张沟通列表。"""
	full_sync_calls: list[str] = []
	incremental_sync_calls: list[str] = []
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda job_id: full_sync_calls.append(job_id) or [ConversationSeed(friend_id=1)],
		sync_recent_conversations=lambda job_id: incremental_sync_calls.append(job_id) or [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert full_sync_calls == ["job-java"]
	assert incremental_sync_calls == ["job-java", "job-java"]


def test_run_once_reports_normal_idle_when_incremental_sync_has_no_new_work(tmp_path: Path) -> None:
	"""无新回复且无附件待处理时，轮询结果必须与同步失败明确区分。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=1, candidate_name="已检查候选人")],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	report = coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert report["state"] == "idle"
	assert report["idle_reason"] == "no_new_candidate_activity"
	assert report["observed"] == 0


def test_run_once_rechecks_waiting_candidates_on_a_low_frequency_recovery_cycle(tmp_path: Path) -> None:
	"""平台未提供未读信号时，等待中的会话仍要周期性核对一次。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=77,
		job_id="job-java",
		candidate_name="漏掉未读信号的候选人",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_CANDIDATE,
	)
	processed: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=77)],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)
	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)

	assert processed == [(77,)]


def test_run_once_recovery_does_not_open_waiting_attachment_before_other_dialogue(tmp_path: Path) -> None:
	"""等待附件只能进入附件检查，不能遮挡其它需要恢复核对的对话。"""
	store = AutomationQueueStore(tmp_path)
	store.upsert_candidate(
		friend_id=10,
		job_id="job-java",
		candidate_name="等待附件",
		source="conversation",
		stage=AutomationCandidateStage.WAITING_ATTACHMENT,
	)
	store.upsert_candidate(
		friend_id=11,
		job_id="job-java",
		candidate_name="等待回复",
		source="conversation",
		stage=AutomationCandidateStage.PROFESSIONAL_DIALOGUE,
	)
	processed: list[tuple[int, ...]] = []
	finalized: list[tuple[int, ...]] = []
	coordinator = AutomationCoordinator(
		queue=store,
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=10), ConversationSeed(friend_id=11)],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, friend_ids, _stop_event: finalized.append(friend_ids) or [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	for _ in range(3):
		coordinator.run_once(job_id="job-java", include_recommendations=False, limit=20)

	assert processed == [(11,)]
	assert finalized[0] == (10,)
	assert finalized[-1] == (10,)


def test_run_once_reports_waiting_attachment_as_idle_without_new_attachment(tmp_path: Path) -> None:
	"""附件尚未到达时，重复检查不能伪装成自动化持续推进。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=1, candidate_name="待附件候选人")],
		sync_recent_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [
			AutomationCandidateEvent(
				friend_id=1,
				stage=AutomationCandidateStage.WAITING_ATTACHMENT,
				action="等待候选人发送附件",
			)
		],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [
			AutomationCandidateEvent(
				friend_id=1,
				stage=AutomationCandidateStage.WAITING_ATTACHMENT,
				action="等待候选人发送附件",
			)
		],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	report = coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)

	assert report["state"] == "idle"
	assert report["idle_reason"] == "waiting_for_attachment"


def test_background_loop_stops_before_platform_work_when_runtime_guard_reports_login_lost(tmp_path: Path) -> None:
	"""后台轮询要在平台动作前复检 RPA 登录态，掉线后停止而不是刷底层解析错误。"""
	processed: list[tuple[int, ...]] = []

	def guard() -> str | None:
		return "当前 RPA 浏览器尚未登录 BOSS，请先完成官方登录"

	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=21, candidate_name="掉线候选人")],
		process_dialogue=lambda _job_id, friend_ids, _stop_event: processed.append(friend_ids) or [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		runtime_guard=guard,
		poll_interval_seconds=5,
	)

	coordinator.start(job_id="job-java", source="conversation", limit=20)
	coordinator.wait_until_stopped(timeout=1)

	status = coordinator.status()
	assert processed == []
	assert status["state"] == "stopped"
	assert status["activities"][0]["action"] == "自动化轮询已停止"
	assert status["activities"][0]["detail"] == "当前 RPA 浏览器尚未登录 BOSS，请先完成官方登录"


def test_background_loop_records_waiting_state_before_the_next_sync(tmp_path: Path) -> None:
	"""轮询间隔内应显示等待状态，避免页面把正常等待误解为自动化已停止。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		poll_interval_seconds=5,
	)

	coordinator.start(job_id="job-java", source="conversation", limit=20)
	for _ in range(20):
		if any(item["status"] == "waiting" for item in coordinator.status()["activities"]):
			break
		sleep(0.01)
	else:
		coordinator.stop()
		raise AssertionError("后台轮询没有记录等待状态")
	coordinator.stop()
	coordinator.wait_until_stopped(timeout=1)

	waiting = next(item for item in coordinator.status()["activities"] if item["status"] == "waiting")
	assert waiting["action"] == "等待下一轮轮询"
	assert waiting["detail"] == "5 秒后继续同步 BOSS 沟通列表"


def test_background_cycle_holds_shared_platform_lock(tmp_path: Path) -> None:
	"""后台轮询期间必须独占平台锁，避免和手动同步并发操作同一个 BOSS 页面。"""
	entered = Event()
	release = Event()
	platform_lock = Lock()

	def sync(_job_id: str) -> list[ConversationSeed]:
		entered.set()
		release.wait(timeout=2)
		return []

	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=sync,
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		runtime_guard=lambda: None,
		platform_operation_lock=platform_lock,
		poll_interval_seconds=5,
	)

	coordinator.start(job_id="job-java", source="conversation", limit=20)
	assert entered.wait(timeout=1)
	assert platform_lock.acquire(blocking=False) is False
	release.set()
	coordinator.stop()
	assert coordinator.wait_until_stopped(timeout=1)


def test_run_once_preserves_failed_platform_phase(tmp_path: Path) -> None:
	"""底层 RPA 异常必须带上“对话处理”阶段，便于区分同步和附件故障。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [ConversationSeed(friend_id=8, candidate_name="阶段诊断")],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: (_ for _ in ()).throw(OSError(22, "Invalid argument")),
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	try:
		coordinator.run_once(job_id="job-java", include_recommendations=False, limit=1)
	except AutomationPhaseError as exc:
		assert str(exc) == "候选人对话处理: OSError: [Errno 22] Invalid argument"
	else:
		raise AssertionError("未保留 RPA 失败阶段")


def test_activity_log_replaces_unpaired_surrogate_in_platform_error(tmp_path: Path) -> None:
	"""平台异常文本含坏字符时，活动日志仍必须能被前端读取。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
	)

	coordinator._add_activity(action="附件\ud83d", status="failed\udc00", detail="平台返回\ud83d")

	activity = coordinator.status()["activities"][0]
	assert activity["action"] == "附件\ufffd"
	assert activity["status"] == "failed\ufffd"
	assert activity["detail"] == "平台返回\ufffd"


def test_stop_source_keeps_other_source_running_for_the_same_job(tmp_path: Path) -> None:
	"""两个按钮共享一个岗位循环时，停止其中一个不能连带停止另一个。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		poll_interval_seconds=5,
	)
	coordinator.start(job_id="job-java", source="conversation", limit=20)
	coordinator.start(job_id="job-java", source="recommendation", limit=10)

	remaining = coordinator.stop_source("recommendation")

	assert remaining["state"] == "running"
	assert remaining["sources"] == ["conversation"]
	stopping = coordinator.stop_source("conversation")
	assert stopping["state"] == "stopping"
	assert coordinator.wait_until_stopped(timeout=1)


def test_sources_keep_independent_processing_limits(tmp_path: Path) -> None:
	"""沟通处理数量和推荐招呼数量不得因启动顺序而互相覆盖。"""
	coordinator = AutomationCoordinator(
		queue=AutomationQueueStore(tmp_path),
		sync_conversations=lambda _job_id: [],
		process_dialogue=lambda _job_id, _friend_ids, _stop_event: [],
		finalize_attachments=lambda _job_id, _friend_ids, _stop_event: [],
		greet_recommendations=lambda _job_id, _limit, _stop_event: [],
		poll_interval_seconds=5,
	)
	coordinator.start(job_id="job-java", source="conversation", limit=20)
	coordinator.start(job_id="job-java", source="recommendation", limit=7)

	assert coordinator.status()["source_limits"] == {"conversation": 20, "recommendation": 7}
	coordinator.stop()
	assert coordinator.wait_until_stopped(timeout=1)


def _attachment(tmp_path: Path) -> Path:
	path = tmp_path / "resume.pdf"
	path.write_bytes(b"attachment")
	return path
