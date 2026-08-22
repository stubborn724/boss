"""招聘 AI 对话命令的首轮上下文恢复与分页扫描测试。"""

from pathlib import Path

from boss_agent_cli.commands.recruiter.ai_dialogue import (
	_conversation_friend_id,
	_conversation_scan_limit,
	_load_conversation_items,
	_load_dialogue_state,
	_message_scan_items,
	_attachment_finalization_states,
	DialogueProcessingReport,
	_preserve_resume_handoff_state,
	_pending_conversation_items,
	process_dialogue_once,
	_pending_message_items,
	_restore_state_after_delivery_failure,
	_sync_resume_request_after_delivery,
	_merge_profile_facts,
)
from boss_agent_cli.commands.recruiter.conversation_state import ConversationStateStore
from boss_agent_cli.recruiting.dialogue_models import CandidateDialogueState, DialogueStage, InterviewPhase
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore
from boss_agent_cli.recruiting.models import JobProfile
from boss_agent_cli.recruiting.conversation_profile import ConversationProfile


def test_initial_state_uses_previous_recruiter_question_for_a_short_candidate_reply(tmp_path: Path) -> None:
	"""首次看到“是的”时，AI 必须知道它回答的是哪一个招聘问题。"""
	store = DialogueStateStore(tmp_path)

	state = _load_dialogue_state(
		state_store=store,
		friend_id=42,
		job_id="job-1",
		previous_recruiter_message="请问您目前可接受到广州上班，通勤在 60 分钟内吗？",
	)

	assert state.stage is DialogueStage.WAITING_CANDIDATE
	assert state.last_assistant_message == "请问您目前可接受到广州上班，通勤在 60 分钟内吗？"


def test_profile_education_is_saved_as_confirmed_dialogue_fact(tmp_path: Path) -> None:
	"""顶部资料已明确显示本科时，应写入事实账本，阻止 AI 再次询问学历。"""
	store = DialogueStateStore(tmp_path)
	state = _load_dialogue_state(state_store=store, friend_id=42, job_id="job-1", previous_recruiter_message="")

	updated = _merge_profile_facts(
		state,
		ConversationProfile.from_display_fields(education_text="2023-2027 南昌交通学院 计算机科学与技术 本科"),
	)

	assert updated.facts["education"] == "本科"
	assert updated.facts["education_major"] == "计算机科学与技术"


def test_existing_state_is_not_overwritten_by_a_history_snapshot(tmp_path: Path) -> None:
	"""已由系统发送的问题是权威状态，不能被页面历史中的旧消息替换。"""
	store = DialogueStateStore(tmp_path)
	existing = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请描述一次你处理 Redis 缓存一致性的经历。",
	)
	store.save(existing)

	state = _load_dialogue_state(
		state_store=store,
		friend_id=42,
		job_id="job-1",
		previous_recruiter_message="请问您目前可接受到广州上班吗？",
	)

	assert state == existing


def test_current_professional_question_repairs_an_outdated_persisted_phase(tmp_path: Path) -> None:
	"""BOSS 当前待答专业题比旧账本更可信，不能再把项目回答当基础回复。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.BASIC,
		last_assistant_message="请确认是否方便到广州实习。",
	))

	state = _load_dialogue_state(
		state_store=store,
		friend_id=42,
		job_id="job-1",
		previous_recruiter_message="请结合一个 Java 项目说明核心模块、技术方案、问题和结果。",
	)

	assert state.interview_phase is InterviewPhase.PROFESSIONAL
	assert state.last_assistant_message == "请结合一个 Java 项目说明核心模块、技术方案、问题和结果。"


def test_current_technical_knowledge_question_repairs_an_outdated_persisted_phase(tmp_path: Path) -> None:
	"""专业题不含“项目”时，仍须以 BOSS 当前问题恢复专业阶段。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.BASIC,
		last_assistant_message="请确认是否可以到广州到岗。",
	))

	state = _load_dialogue_state(
		state_store=store,
		friend_id=42,
		job_id="job-1",
		previous_recruiter_message="接下来问一个专业知识问题：Spring 框架依赖注入是怎么实现的？",
	)

	assert state.interview_phase is InterviewPhase.PROFESSIONAL


def test_current_resume_request_preserves_the_resume_handoff_after_restart(tmp_path: Path) -> None:
	"""BOSS 已索要附件时重启后不得把候选人确认消息重新交给专业问答。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.PROFESSIONAL,
		last_assistant_message="请说明一个 Java 项目的技术方案。",
	))

	state = _load_dialogue_state(
		state_store=store,
		friend_id=42,
		job_id="job-1",
		previous_recruiter_message="感谢回复，请发送附件简历。",
	)

	assert state.stage is DialogueStage.READY_FOR_RESUME
	assert state.last_assistant_message == "感谢回复，请发送附件简历。"


def test_process_dialogue_once_requests_resume_when_boss_shows_a_answered_professional_question(tmp_path: Path) -> None:
	"""BOSS 已有专业题和有效回答时，旧基础账本只能校正后索简历。"""
	DialogueStateStore(tmp_path).save(CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		interview_phase=InterviewPhase.BASIC,
		last_assistant_message="请确认是否可以到广州到岗。",
	))

	class Platform:
		"""模拟 BOSS 返回专业题紧邻候选人项目回答的最小会话快照。"""

		def __init__(self) -> None:
			self.sent_messages: list[tuple[int, str]] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"list": [{"friendId": 42, "unreadCount": 1}] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			return {"code": 0, "zpData": {"lastMessageList": [{
				"friendId": 42,
				"content": "我负责订单核心模块，使用 Spring Boot 和 Redis 设计缓存方案，排查了缓存穿透问题并用布隆过滤器优化，接口稳定性明显提升。",
				"previousRecruiterText": "请结合一个 Java 项目说明核心模块、技术方案、问题和结果。",
			}]}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

		def send_message_by_friend(self, friend_id: int, message: str) -> dict[str, object]:
			self.sent_messages.append((friend_id, message))
			return {"code": 0}

	platform = Platform()
	calls = 0

	def chat(_messages: object) -> str:
		nonlocal calls
		calls += 1
		raise AssertionError("已回答专业题时不应再次调用 AI 生成专业问题")

	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=platform,
		job=JobProfile(job_id="job-1", name="Java 后端", skills=["Java"]),
		chat=chat,
		limit=1,
		force_waiting_recheck=False,
	)

	assert calls == 0
	assert report.processed_friend_ids == (42,)
	assert report.sent_friend_ids == (42,)
	assert platform.sent_messages == [(42, "感谢回复，请发送附件简历。")]


def test_conversation_friend_id_rejects_invalid_platform_values() -> None:
	"""平台字段漂移或布尔值不能进入后续 RPA 会话定位。"""
	assert _conversation_friend_id({"friend_id": "42"}) == 42
	assert _conversation_friend_id({"friend_id": True}) is None
	assert _conversation_friend_id({"friend_id": "not-a-number"}) is None


def test_load_conversation_items_reads_later_pages_when_first_page_is_full() -> None:
	"""第一页未处理完不能阻断后续页的已回复候选人进入扫描范围。"""

	class PagePlatform:
		"""用分页响应模拟 BOSS 每页固定数量的沟通列表。"""

		def __init__(self) -> None:
			self.pages_requested: list[int] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			self.pages_requested.append(page)
			pages = {
				1: [{"friendId": 101}, {"friendId": 102}],
				2: [{"friendId": 201}, {"friendId": 202}],
				3: [],
			}
			return {"code": 0, "zpData": {"list": pages[page]}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = PagePlatform()

	items = _load_conversation_items(platform, job_id="job-1", limit=4)

	assert [item["friend_id"] for item in items] == [101, 102, 201, 202]
	assert platform.pages_requested == [1, 2]


def test_conversation_scan_limit_keeps_full_search_when_targeting_one_friend() -> None:
	"""指定候选人只限制处理对象，不能把列表扫描缩成首个窗口。"""
	assert _conversation_scan_limit(limit=1, friend_id=690533787) == 50
	assert _conversation_scan_limit(limit=12, friend_id=None) == 12


def test_load_conversation_items_reuses_snapshot_records_without_scanning_pages() -> None:
	"""已有首轮快照时，定向对话处理不能再次滚动读取整张沟通列表。"""

	class Platform:
		"""任何分页读取都应失败，用于证明调用方确实复用了快照。"""

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			raise AssertionError("已提供首轮快照，不应再次调用 friend_list")

	items = _load_conversation_items(
		Platform(),
		job_id="job-1",
		limit=1,
		target_friend_ids=(99,),
		snapshot_records=[{"friendId": 99, "name": "快照候选人"}],
	)

	assert items == [{"friend_id": 99, "candidate_name": "快照候选人", "updated_at": "-"}]


def test_ready_for_resume_delivery_marks_attachment_request_as_sent(tmp_path: Path) -> None:
	"""AI 已成功索要附件时，附件状态机必须识别为已发送而不再重复沟通。"""
	store = ConversationStateStore(tmp_path)

	_sync_resume_request_after_delivery(
		state_store=store,
		friend_id=42,
		outbound_message="方便发送一份附件简历吗？",
		should_finalize_resume=True,
	)

	assert store.has_resume_request_sent(42) is True
	assert store.get(42)["resume_request_message"] == "方便发送一份附件简历吗？"


def test_resume_handoff_message_preserves_ready_state(tmp_path: Path) -> None:
	"""候选人确认发送附件后，必须交给附件流程而不能回退到人工复核。"""
	store = DialogueStateStore(tmp_path)
	state = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.READY_FOR_RESUME,
		last_assistant_message="请发送一份附件简历。",
	)

	next_state = _preserve_resume_handoff_state(
		state_store=store,
		state=state,
		message_id="attachment-sent-message",
	)

	assert next_state.stage is DialogueStage.READY_FOR_RESUME
	assert next_state.last_processed_message_id == "attachment-sent-message"
	assert store.get("friend:42") == next_state


def test_attachment_finalization_recovers_requested_resume_after_manual_review(tmp_path: Path) -> None:
	"""历史交接消息被误标人工复核时，已确认的附件请求仍可进入附件检测。"""
	dialogue_store = DialogueStateStore(tmp_path)
	conversation_store = ConversationStateStore(tmp_path)
	manual = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.MANUAL_REVIEW,
	)
	waiting = CandidateDialogueState(
		candidate_key="friend:43",
		job_id="job-1",
		stage=DialogueStage.MANUAL_REVIEW,
	)
	dialogue_store.save(manual)
	dialogue_store.save(waiting)
	conversation_store.mark_resume_request_sent(42, message="请发送附件简历。")

	states = _attachment_finalization_states(
		dialogue_store=dialogue_store,
		conversation_store=conversation_store,
		job_id="job-1",
		limit=10,
	)

	assert states == [manual]


def test_delivery_failure_restores_the_state_before_ai_turn(tmp_path: Path) -> None:
	"""页面未确认送达时不能消费候选人消息或把阶段推进到下一步。"""
	store = DialogueStateStore(tmp_path)
	previous = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_assistant_message="请问您目前住在广州吗？",
	)
	advanced = CandidateDialogueState(
		candidate_key="friend:42",
		job_id="job-1",
		stage=DialogueStage.READY_FOR_RESUME,
		last_assistant_message="方便发送一份附件简历吗？",
		last_processed_message_id="candidate-message-1",
		ai_turn_count=1,
	)
	store.save(advanced)

	_restore_state_after_delivery_failure(state_store=store, previous_state=previous)

	assert store.get("friend:42") == previous


def test_pending_message_items_skips_waiting_candidates_without_a_new_reply(tmp_path: Path) -> None:
	"""等待回复只是异步队列状态，不能占用本轮 AI 处理名额或触发资料读取。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_processed_message_id="already-seen",
	))
	items = [{"friend_id": 1}, {"friend_id": 2}]
	latest = {
		1: ("already-seen", "旧回复", "请问通勤情况？"),
		2: ("new-reply", "新回复", "请问通勤情况？"),
	}

	pending = _pending_message_items(items=items, latest=latest, state_store=store)

	assert [friend_id for friend_id, _message_id, _message, _previous in pending] == [2]


def test_pending_conversation_items_excludes_waiting_candidates_before_profile_reads(tmp_path: Path) -> None:
	"""顶部资料 RPA 只能读取真正有新回复的会话，等待队列不能占用页面操作。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		last_processed_message_id="already-seen",
	))
	items = [
		{"friend_id": 1, "candidate_name": "未回复"},
		{"friend_id": 2, "candidate_name": "新回复"},
	]
	latest = {
		1: ("already-seen", "旧回复", "请问通勤情况？"),
		2: ("new-reply", "新回复", "请问通勤情况？"),
	}

	pending = _pending_conversation_items(items=items, latest=latest, state_store=store)

	assert [(item["friend_id"], message_id) for item, message_id, _message, _previous in pending] == [(2, "new-reply")]


def test_message_scan_items_skips_timestamped_waiting_candidate_without_unread_signal(tmp_path: Path) -> None:
	"""已发送后等待的候选人没有未读回信时，不应再打开会话读取消息。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		waiting_since="2026-08-10T10:00:00+00:00",
	))
	items = [
		{"friend_id": 1, "unread_count": 0},
		{"friend_id": 2, "unread_count": 1},
	]

	assert [item["friend_id"] for item in _message_scan_items(items=items, state_store=store)] == [2]


def test_message_scan_items_rechecks_explicit_waiting_candidate_without_unread_signal(tmp_path: Path) -> None:
	"""定向轮询不能依赖易丢失的未读徽标，消息指纹会阻止重复 AI 调用。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		waiting_since="2026-08-10T10:00:00+00:00",
	))
	store.save(CandidateDialogueState(
		candidate_key="friend:2",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		waiting_since="2026-08-10T10:00:00+00:00",
	))
	items = [
		{"friend_id": 1, "unread_count": 0},
		{"friend_id": 2, "unread_count": 0},
	]

	assert [item["friend_id"] for item in _message_scan_items(
		items=items,
		state_store=store,
		force_friend_id=1,
	)] == [1]


def test_message_scan_items_rechecks_waiting_candidate_when_conversation_version_changes(tmp_path: Path) -> None:
	"""红点被人工点开后，列表版本变化仍必须触发最新候选人消息读取。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		waiting_since="2026-08-10T10:00:00+00:00",
		conversation_version="v1-old",
	))

	items = [{"friend_id": 1, "unread_count": 0, "conversation_version": "v1-new"}]

	assert [item["friend_id"] for item in _message_scan_items(items=items, state_store=store)] == [1]


def test_message_scan_items_does_not_force_automation_target_without_unread_signal(tmp_path: Path) -> None:
	"""后台自动化指定会话不等于人工强制查询，等待中的人仍应跳过。"""
	store = DialogueStateStore(tmp_path)
	store.save(CandidateDialogueState(
		candidate_key="friend:1",
		job_id="job-1",
		stage=DialogueStage.WAITING_CANDIDATE,
		waiting_since="2026-08-10T10:00:00+00:00",
	))

	assert _message_scan_items(
		items=[{"friend_id": 1, "unread_count": 0}],
		state_store=store,
		force_friend_id=1,
		force_waiting_recheck=False,
	) == []


def test_process_dialogue_once_stops_before_reading_the_next_candidate(tmp_path: Path) -> None:
	"""停止请求到达后，当前批次剩余候选人不得继续读取资料或发送消息。"""
	class Platform:
		"""仅实现硬筛前读取所需的最小平台协议。"""

		def __init__(self) -> None:
			self.profile_reads: list[int] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"list": [
				{"friendId": 1, "name": "第一位", "unreadCount": 1},
				{"friendId": 2, "name": "第二位", "unreadCount": 1},
			] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			return {"code": 0, "zpData": {"lastMessageList": [
				{"friendId": friend_id, "content": "您好"}
				for friend_id in friend_ids
			]}}

		def read_conversation_profile(self, friend_id: int) -> dict[str, object]:
			self.profile_reads.append(friend_id)
			return {"zpData": {"profile": {"expectation_city": "深圳"}}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = Platform()
	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=platform,
		job=JobProfile(job_id="job-1", name="Java", city="广州"),
		chat=lambda _messages: "",
		limit=2,
		stop_requested=lambda: len(platform.profile_reads) >= 1,
	)

	assert platform.profile_reads == [1]
	assert report.hard_rejected_friend_ids == (1,)


def test_process_dialogue_once_batches_targets_without_rescanning_the_list_per_candidate(tmp_path: Path) -> None:
	"""自动化批次应一次读取列表和最新消息，不能每个人重复翻页。"""
	class Platform:
		"""记录列表和消息读取次数，模拟目标位于第二页的沟通列表。"""

		def __init__(self) -> None:
			self.pages: list[int] = []
			self.last_message_calls: list[list[int]] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			self.pages.append(page)
			return {"code": 0, "zpData": {"list": [
				{"friendId": 1, "name": "第一位", "unreadCount": 1},
				{"friendId": 2, "name": "第二位", "unreadCount": 1},
			] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			self.last_message_calls.append(friend_ids)
			return {"code": 0, "zpData": {"lastMessageList": [
				{"friendId": friend_id, "content": f"回复-{friend_id}"}
				for friend_id in friend_ids
			]}}

		def read_conversation_profile(self, friend_id: int) -> dict[str, object]:
			return {"zpData": {"profile": {"expectation_city": "广州"}}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

		def send_message_by_friend(self, friend_id: int, message: str) -> dict[str, object]:
			return {"code": 0}

	platform = Platform()
	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=platform,
		job=JobProfile(job_id="job-1", name="Java", city="广州"),
		chat=lambda _messages: "",
		limit=1,
		friend_ids=(1, 2),
		force_waiting_recheck=False,
	)

	# 同一段已定位全部目标时立即停止，不能为了确认空页再多做一次 RPA 读取。
	assert platform.pages == [1]
	assert platform.last_message_calls == [[1, 2]]
	assert report.processed_friend_ids == (1, 2)


def test_process_dialogue_once_reuses_profile_captured_with_latest_message(tmp_path: Path) -> None:
	"""读取最后消息时已得到学历资料后，硬筛不能再次逐人定位聊天顶部。"""

	class Platform:
		"""模拟消息读取已携带当前聊天页的最小资料快照。"""

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"list": [{"friendId": 9, "name": "学历不符", "unreadCount": 1}] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			return {"code": 0, "zpData": {"lastMessageList": [{
				"friendId": 9,
				"content": "我可以到岗",
				"profile": {"education_degree": "大专"},
			}]}}

		def read_conversation_profile(self, friend_id: int) -> dict[str, object]:
			raise AssertionError("消息读取已经携带 profile，不应再次定位聊天窗口")

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=Platform(),
		job=JobProfile(job_id="job-1", name="Java", education_requirement="本科及以上"),
		chat=lambda _messages: "",
		limit=1,
		friend_ids=(9,),
		force_waiting_recheck=False,
	)

	assert report.hard_rejected_friend_ids == (9,)


def test_process_dialogue_once_does_not_rescan_when_captured_profile_is_empty(tmp_path: Path) -> None:
	"""当前聊天页资料为空时，应按未知资料处理，不能再次滚动列表寻找同一人。"""

	class Platform:
		"""消息读取已尝试资料选择器但没有得到字段。"""

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"list": [{"friendId": 10, "unreadCount": 1}] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			return {"code": 0, "zpData": {"lastMessageList": [{"friendId": 10, "content": "您好", "profile": {}}]}}

		def read_conversation_profile(self, friend_id: int) -> dict[str, object]:
			raise AssertionError("空资料已在当前聊天页确认，不应再次扫描")

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=Platform(),
		job=JobProfile(job_id="job-1", name="Java"),
		chat=lambda _messages: '{"next_action":"manual_review","next_question_phase":"none"}',
		limit=1,
		friend_ids=(10,),
		force_waiting_recheck=False,
	)

	assert report.processed_friend_ids == (10,)
	assert report.manual_review_friend_ids == (10,)


def test_process_dialogue_once_locates_target_beyond_first_fifty_conversations(tmp_path: Path) -> None:
	"""未读快照选中的深层会话必须进入消息读取，不能在前五十条列表记录中丢失。"""
	class Platform:
		"""前两段列表刚好填满五十条，目标候选人位于第三段。"""

		def __init__(self) -> None:
			self.pages: list[int] = []
			self.last_message_calls: list[list[int]] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			self.pages.append(page)
			pages = {
				1: [{"friendId": friend_id, "unreadCount": 0} for friend_id in range(1, 26)],
				2: [{"friendId": friend_id, "unreadCount": 0} for friend_id in range(26, 51)],
				3: [{"friendId": 99, "unreadCount": 1}],
				4: [],
			}
			return {"code": 0, "zpData": {"list": pages[page]}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			self.last_message_calls.append(friend_ids)
			# 故意不返回正文，只验证目标已经进入消息读取并保留为可重试状态。
			return {"code": 0, "zpData": {"lastMessageList": []}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = Platform()
	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=platform,
		job=JobProfile(job_id="job-1", name="Java", city="广州"),
		chat=lambda _messages: "",
		limit=1,
		friend_ids=(99,),
		force_waiting_recheck=False,
	)

	assert platform.pages == [1, 2, 3]
	assert platform.last_message_calls == [[99]]
	assert report.unresolved_friend_ids == (99,)


def test_process_dialogue_once_keeps_unread_target_retryable_when_latest_message_is_missing(tmp_path: Path) -> None:
	"""未读徽标存在但消息读取缺失时，报告必须保留重试标识。"""
	class Platform:
		"""只返回沟通卡片，不返回消息正文，模拟 RPA 短暂不同步。"""

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"list": [{"friendId": 9, "unreadCount": 1}] if page == 1 else []}}

		def last_messages(self, friend_ids: list[int]) -> dict[str, object]:
			return {"code": 0, "zpData": {"lastMessageList": []}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	report = process_dialogue_once(
		data_dir=tmp_path,
		platform=Platform(),
		job=JobProfile(job_id="job-1", name="Java", city="广州"),
		chat=lambda _messages: "",
		limit=1,
		friend_ids=(9,),
		force_waiting_recheck=False,
	)

	assert report.unresolved_friend_ids == (9,)


def test_dialogue_report_exposes_hard_screening_reason_codes_by_candidate() -> None:
	"""Web 队列需要按候选人读取硬筛原因，而不是只知道有人被跳过。"""
	report = DialogueProcessingReport(
		hard_rejected_friend_ids=(42,),
		hard_rejection_reasons=((42, ("city_mismatch", "education_mismatch")),),
	)

	assert report.hard_rejection_reason_codes(42) == ("city_mismatch", "education_mismatch")
	assert report.hard_rejection_reason_codes(99) == ()
