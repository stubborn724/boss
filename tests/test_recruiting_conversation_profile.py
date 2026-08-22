"""沟通页候选人资料快照测试。"""

from boss_agent_cli.recruiting.conversation_profile import ConversationProfile
from boss_agent_cli.commands.recruiter.conversation_listing import load_conversation_items


def test_profile_from_display_fields_keeps_only_recruiting_fields() -> None:
	"""页面红框只允许进入职业匹配需要的非敏感字段。"""
	profile = ConversationProfile.from_display_fields(
		work_text="2026.03-2026.06 致象商务服务 其他后端开发",
		education_text="2024-2028 广东工业大学 大数据管理与应用 本科",
		communication_job="Java",
		expectation_text="广州 Java 2-6K",
	)

	assert profile.work_position == "其他后端开发"
	assert profile.education_degree == "本科"
	assert profile.expectation_city == "广州"
	assert profile.expected_salary == "2-6K"
	assert "age" not in profile.to_dict()
	assert "address" not in profile.to_dict()


def test_profile_keeps_commute_only_at_coarse_granularity() -> None:
	"""通勤信息只保留区、地铁站和分钟数，不接受精确门牌地址。"""
	profile = ConversationProfile.from_dialogue_fact(
		"通勤情况",
		"住在天河区珠江新城地铁站附近，可接受 50 分钟，不保存具体地址",
	)

	assert profile.commute_district == "天河区"
	assert profile.commute_station == "珠江新城"
	assert profile.acceptable_commute_minutes == 50
	assert "具体地址" not in str(profile.to_dict())


def test_load_conversation_items_collects_all_distinct_pages() -> None:
	"""沟通列表同步应跨页收集所有可见会话，并在重复末页安全停止。"""
	class Platform:
		def __init__(self) -> None:
			self.pages: list[int] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			self.pages.append(page)
			items = {
				1: [{"friendId": 1, "name": "第一位"}, {"friendId": 2, "name": "第二位"}],
				2: [{"friendId": 2, "name": "第二位"}, {"friendId": 3, "name": "第三位"}],
				3: [{"friendId": 3, "name": "第三位"}],
			}[page]
			return {"code": 0, "zpData": {"friendList": items}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = Platform()

	items = load_conversation_items(platform, job_id="job-java", max_pages=10)

	assert [item["friend_id"] for item in items] == [1, 2, 3]
	assert platform.pages == [1, 2, 3]


def test_load_conversation_items_can_find_unread_candidate_beyond_first_page() -> None:
	"""轻量轮询必须跨页发现首屏之外的未读候选人。"""
	class Platform:
		"""以 20 页固定窗口模拟大量旧会话遮挡后的未读记录。"""

		def __init__(self) -> None:
			self.pages: list[int] = []

		def friend_list(self, *, page: int, label_id: int, job_id: str) -> dict[str, object]:
			self.pages.append(page)
			if page < 20:
				return {"code": 0, "zpData": {"list": [{"friendId": page, "name": f"候选人{page}", "unreadCount": 0}]}}
			if page == 20:
				return {"code": 0, "zpData": {"list": [{"friendId": 2000, "name": "第20页未读", "unreadCount": 1}]}}
			return {"code": 0, "zpData": {"list": []}}

		def is_success(self, response: dict[str, object]) -> bool:
			return response.get("code") == 0

		def unwrap_data(self, response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = Platform()
	items = load_conversation_items(platform, job_id="", max_pages=50)

	assert items[-1]["friend_id"] == 2000
	assert items[-1]["unread_count"] == 1
	assert platform.pages[-1] == 21
