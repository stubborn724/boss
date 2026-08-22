"""批量简历导出服务测试。

覆盖三类不变量：翻页与去重、护栏（额度停批 / 熔断 / 停止开关）、以及扫描模式
绝不写文件也绝不向候选人索要附件。
"""

from pathlib import Path
from threading import Event

from boss_agent_cli.commands.recruiter.batch_resume_export import (
	MODE_SCAN,
	SOURCE_CONVERSATION,
	SOURCE_RECOMMENDATION,
	BatchResumeExportService,
	BatchTarget,
	collect_conversation_targets,
	collect_recommendation_targets,
	collect_targets,
)
from boss_agent_cli.commands.recruiter.conversation_listing import conversation_item_from_record
from boss_agent_cli.commands.recruiter.conversation_resume_export import (
	ConversationAttachmentResult,
	ConversationResumeExportResult,
)
from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult


def _online_result(name: str = "张三") -> ResumeExportResult:
	return ResumeExportResult(
		path=Path("C:/exports/online.md"),
		filename="online.md",
		bytes_written=10,
		candidate_name=name,
		geek_id="geek-1",
		exported_at="2026-08-01T10:00:00",
		sections=["basic"],
	)


def _conversation_result(name: str = "张三", *, attachment_status: str = "downloaded") -> ConversationResumeExportResult:
	return ConversationResumeExportResult(
		friend_id=1,
		candidate_name=name,
		online_resume=_online_result(name),
		attachment=ConversationAttachmentResult(status=attachment_status, filename="resume.pdf"),
	)


class FakeScanPlatform:
	"""记录扫描过程中的平台访问，用于断言请求数量和边界。"""

	def __init__(self, *, available_uids: set[int] | None = None) -> None:
		self.available_uids = available_uids or set()
		self.detail_calls: list[list[int]] = []
		self.exchange_calls: list[int] = []

	def is_success(self, response: dict) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		return "UNKNOWN", ""

	def friend_detail(self, friend_ids: list[int]) -> dict:
		self.detail_calls.append(list(friend_ids))
		return {
			"code": 0,
			"zpData": {
				"friendList": [
					{
						"uid": friend_id,
						"encryptUid": f"geek-{friend_id}",
						"encryptJobId": "job-1",
						"securityId": "sid-1",
						"name": f"候选人{friend_id}",
					}
					for friend_id in friend_ids
				]
			},
		}

	def exchange_content(self, uid: int) -> dict:
		self.exchange_calls.append(uid)
		if uid in self.available_uids:
			return {"code": 0, "zpData": {"resume": {"resumeUrl": "https://cdn.example.com/a.pdf", "resumeName": "a.pdf"}}}
		return {"code": 0, "zpData": {}}


def test_conversation_collection_pages_until_limit_and_deduplicates() -> None:
	"""翻页要攒够数量，并且平台重复返回同一会话时只处理一次。"""
	pages = {
		1: [{"friend_id": 1, "candidate_name": "A"}, {"friend_id": 2, "candidate_name": "B"}],
		2: [{"friend_id": 2, "candidate_name": "B"}, {"friend_id": 3, "candidate_name": "C"}],
	}
	seen_pages: list[int] = []

	def read_page(page: int) -> list[dict]:
		seen_pages.append(page)
		return pages.get(page, [])

	targets = collect_conversation_targets(read_page, 3)

	assert [target.friend_id for target in targets] == [1, 2, 3]
	assert seen_pages == [1, 2]


def test_conversation_collection_stops_when_a_page_adds_nothing_new() -> None:
	"""整页都是已见过的会话时必须停止翻页，避免无限读取平台列表。"""

	def read_page(page: int) -> list[dict]:
		return [{"friend_id": 1, "candidate_name": "A"}]

	targets = collect_conversation_targets(read_page, 10)

	assert [target.friend_id for target in targets] == [1]


def test_recommendation_collection_skips_cards_without_locator_fields() -> None:
	"""推荐卡片缺少定位三元组时不能进入批次，否则导出必然失败。"""

	def read_page(page: int, job_id: str | None) -> list[dict]:
		if page > 1:
			return []
		return [
			{"geek_id": "g1", "job_id": "j1", "security_id": "s1", "candidate_name": "A"},
			{"geek_id": "g2", "job_id": "", "security_id": "s2", "candidate_name": "缺字段"},
		]

	targets = collect_recommendation_targets(read_page, 5, None)

	assert [target.geek_id for target in targets] == ["g1"]


def test_export_stops_when_pacing_gate_denies_and_reports_reason() -> None:
	"""额度耗尽必须立刻停批，并把原因原样带回给调用方。"""
	calls = {"count": 0}

	def pacing_gate() -> tuple[bool, str]:
		calls["count"] += 1
		return (calls["count"] <= 1, "" if calls["count"] <= 1 else "daily_quota")

	service = BatchResumeExportService(export_conversation=lambda **kwargs: _conversation_result())
	targets = [BatchTarget(name="A", friend_id=1), BatchTarget(name="B", friend_id=2)]

	report = service.run(targets, source=SOURCE_CONVERSATION, pacing_gate=pacing_gate)

	assert report.processed == 1
	assert report.stopped_reason == "daily_quota"
	assert report.succeeded == 1


def test_export_stops_after_repeated_failures() -> None:
	"""连续失败通常意味着登录或磁盘问题，必须熔断而不是继续刷失败。"""

	def failing_export(**kwargs) -> ConversationResumeExportResult:
		raise RuntimeError("boom")

	service = BatchResumeExportService(export_conversation=failing_export)
	targets = [BatchTarget(name=f"候选人{index}", friend_id=index) for index in range(1, 6)]

	report = service.run(targets, source=SOURCE_CONVERSATION)

	assert report.processed == 3
	assert report.failed == 3
	assert report.stopped_reason == "repeated_failure"
	assert all(item.error_message == "boom" for item in report.items)


def test_export_stops_on_user_request_before_next_candidate() -> None:
	"""停止开关只在候选人边界生效，已完成的人必须保留在结果里。"""
	stop_event = Event()

	def export_and_stop(**kwargs) -> ConversationResumeExportResult:
		stop_event.set()
		return _conversation_result()

	service = BatchResumeExportService(export_conversation=export_and_stop)
	targets = [BatchTarget(name="A", friend_id=1), BatchTarget(name="B", friend_id=2)]

	report = service.run(targets, source=SOURCE_CONVERSATION, stop_event=stop_event)

	assert report.processed == 1
	assert report.stopped_reason == "stopped_by_user"


def test_export_login_expired_aborts_whole_batch() -> None:
	"""登录失效后继续跑只会得到一串同样的失败，应立刻停批。"""

	def expired(**kwargs) -> ConversationResumeExportResult:
		raise PermissionError("expired")

	service = BatchResumeExportService(export_conversation=expired)
	report = service.run([BatchTarget(name="A", friend_id=1)], source=SOURCE_CONVERSATION)

	assert report.processed == 0
	assert report.stopped_reason == "login_expired"


def test_recommendation_export_marks_attachment_unavailable_without_conversation() -> None:
	"""推荐卡片没有会话时不请求附件，状态如实标为暂不可用。"""
	service = BatchResumeExportService(export_online=lambda **kwargs: _online_result("李四"))
	targets = [BatchTarget(name="李四", geek_id="g1", job_id="j1", security_id="s1")]

	report = service.run(targets, source=SOURCE_RECOMMENDATION)

	assert report.items[0].attachment_status == "unavailable"
	assert report.items[0].online_status == "exported"
	assert report.with_attachment == 0


def test_scan_mode_uses_one_detail_request_and_writes_nothing() -> None:
	"""扫描只用一次会话详情覆盖整批，并且绝不调用任何导出器。"""
	platform = FakeScanPlatform(available_uids={2})
	exports: list[dict] = []

	def export_conversation(**kwargs):
		exports.append(kwargs)
		raise AssertionError("扫描模式不得导出文件")

	service = BatchResumeExportService(export_conversation=export_conversation, scan_platform=platform)
	targets = [BatchTarget(name="A", friend_id=1), BatchTarget(name="B", friend_id=2)]

	report = service.run(targets, source=SOURCE_CONVERSATION, mode=MODE_SCAN)

	assert platform.detail_calls == [[1, 2]]
	assert platform.exchange_calls == [1, 2]
	assert exports == []
	assert [item.attachment_status for item in report.items] == ["no_attachment", "can_export_pdf"]
	assert report.with_attachment == 1
	assert report.attachment_statuses() == {1: "no_attachment", 2: "can_export_pdf"}


def test_scan_mode_never_requests_an_attachment_from_the_candidate() -> None:
	"""扫描平台协议里不存在索要附件的方法，误用会立刻暴露。"""
	platform = FakeScanPlatform()

	assert not hasattr(platform, "exchange_request_by_friend")
	service = BatchResumeExportService(scan_platform=platform)
	service.run([BatchTarget(name="A", friend_id=1)], source=SOURCE_CONVERSATION, mode=MODE_SCAN)

	assert platform.exchange_calls == [1]


def test_public_items_never_expose_platform_identifiers() -> None:
	"""结果投影必须剔除会话标识，页面不能反推候选人身份。"""
	service = BatchResumeExportService(export_conversation=lambda **kwargs: _conversation_result())
	report = service.run([BatchTarget(name="A", friend_id=42)], source=SOURCE_CONVERSATION)

	public = report.to_public_dict()

	assert "friend_id" not in public["items"][0]
	assert "42" not in str(public["items"][0])
	assert report.items[0].friend_id == 42


def test_progress_callback_reports_every_processed_candidate() -> None:
	"""页面进度依赖逐人回调，缺一个就会出现"卡住"的错觉。"""
	seen: list[str] = []
	service = BatchResumeExportService(export_conversation=lambda **kwargs: _conversation_result("张三"))
	targets = [BatchTarget(name="A", friend_id=1), BatchTarget(name="B", friend_id=2)]

	service.run(targets, source=SOURCE_CONVERSATION, progress=lambda item: seen.append(item.name))

	assert seen == ["张三", "张三"]


def test_empty_batch_reports_no_target_reason() -> None:
	"""平台没返回人时要给稳定原因，而不是假装成功。"""
	service = BatchResumeExportService()

	report = service.run([], source=SOURCE_CONVERSATION)

	assert report.stopped_reason == "no_target"
	assert report.processed == 0


class FakeListPlatform(FakeScanPlatform):
	"""带列表接口的平台替身，用于验证 collect_targets 的登录失效映射。"""

	def __init__(self, *, login_expired: bool = False) -> None:
		super().__init__()
		self.login_expired = login_expired

	def unwrap_data(self, response: dict) -> dict:
		return response.get("zpData") or {}

	def is_success(self, response: dict) -> bool:
		return not self.login_expired and response.get("code") == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		return ("LOGIN_EXPIRED", "expired") if self.login_expired else ("UNKNOWN", "")

	def friend_list(self, page: int = 1, label_id: int = 0) -> dict:
		if page > 1:
			return {"code": 0, "zpData": {"result": []}}
		return {"code": 0, "zpData": {"result": [{"friendId": 5, "name": "王五", "jobName": "销售"}]}}


def test_collect_targets_projects_conversation_whitelist_fields() -> None:
	"""共享投影必须给出姓名，同时保留内部会话标识供导出使用。"""
	targets = collect_targets(FakeListPlatform(), source=SOURCE_CONVERSATION, limit=5)

	assert [(target.name, target.friend_id) for target in targets] == [("王五", 5)]


def test_conversation_projection_treats_title_as_candidate_name_not_position() -> None:
	"""RPA 沟通卡片里的 title 是候选人姓名，不能误投影成职位。

	截图中的“未命名候选人 / 王文少”就是因为平台只给了 ``title``，
	旧投影没有把它归到 ``candidate_name``，反而让前端把姓名当作上下文展示。
	"""
	item = conversation_item_from_record({"friendId": 7, "title": "王文少"})

	assert item == {"friend_id": 7, "candidate_name": "王文少", "updated_at": "-"}


def test_conversation_projection_recovers_name_from_position_when_rpa_has_already_normalized_title() -> None:
	"""RPA 桥接层可能已把卡片 title 归到 position，列表投影仍要回收姓名。

	Web 运行态里看到的实际 API 是 ``candidate_name=未命名`` 且
	``position=肖奕旭``。这种单字段中文姓名不能继续作为职位展示。
	"""
	item = conversation_item_from_record({"friend_id": 8, "position": "肖奕旭", "unread_count": 1})

	assert item == {
		"friend_id": 8,
		"candidate_name": "肖奕旭",
		"updated_at": "-",
		"unread_count": 1,
	}


def test_collect_targets_maps_login_expiry_to_permission_error() -> None:
	"""列表读取遇到登录失效时要抛 PermissionError，让上层提示重新登录。"""
	try:
		collect_targets(FakeListPlatform(login_expired=True), source=SOURCE_CONVERSATION, limit=5)
	except PermissionError:
		return
	raise AssertionError("登录失效必须转成 PermissionError")


def test_collect_targets_distinguishes_read_failure_from_an_empty_list() -> None:
	"""平台读取失败不能退化成空列表，否则登录失效会伪装成"没有候选人"。"""
	from boss_agent_cli.commands.recruiter.batch_resume_export import BatchTargetReadError

	class FailingPlatform(FakeListPlatform):
		def is_success(self, response: dict) -> bool:
			return False

		def parse_error(self, response: dict) -> tuple[str, str]:
			return "RISK_CONTROL", "请稍后重试"

	try:
		collect_targets(FailingPlatform(), source=SOURCE_CONVERSATION, limit=5)
	except BatchTargetReadError as exc:
		assert exc.code == "RISK_CONTROL"
		assert "读取沟通候选人列表失败" in str(exc)
		return
	raise AssertionError("列表读取失败必须抛 BatchTargetReadError")


def test_collect_targets_keeps_earlier_pages_when_a_later_page_fails() -> None:
	"""已经攒到人时，后续页失败只应停止翻页，而不是让整批失败。"""
	from boss_agent_cli.commands.recruiter.batch_resume_export import (
		BatchTargetReadError,
		collect_conversation_targets,
	)

	def read_page(page: int) -> list[dict]:
		if page == 1:
			return [{"friend_id": 1, "candidate_name": "A"}]
		raise BatchTargetReadError("第二页失败", code="RISK_CONTROL")

	targets = collect_conversation_targets(read_page, 10)

	assert [target.friend_id for target in targets] == [1]
