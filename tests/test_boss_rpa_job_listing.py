"""BOSS 职位管理页 RPA 读取回归测试。"""

from typing import Any

from boss_agent_cli.rpa.boss_client import BossRPAClient, JOB_PAGE
from boss_agent_cli.rpa.pages import JOB_CARD


def test_job_management_uses_current_recruiter_route() -> None:
	"""职位同步必须进入招聘端当前的职位管理页，不能落到已废弃的 404 路由。"""
	assert JOB_PAGE == "https://www.zhipin.com/web/chat/job/list"


def test_job_card_selectors_only_target_real_job_rows() -> None:
	"""职位列表选择器不能把 tab、操作容器等嵌套节点当成岗位卡片。"""
	assert JOB_CARD == [".job-item-container", ".job-list-content > li"]


def test_job_detail_reader_never_clicks_close_or_navigates_history() -> None:
	"""详情读取是只读流程，不能误触 BOSS 的关闭职位业务按钮。"""
	class _CaptureRPAClient(BossRPAClient):
		def _eval(self, js: str, *, await_promise: bool = False) -> Any:
			if "job-detail-rpa-read" in js:
				assert "history.back" not in js
				assert "close.click" not in js
				return {}
			return True

	client = _CaptureRPAClient()
	assert client._read_job_detail_panel(card_index=0, card_selectors="[]", title_selectors="[]") == {}


class _JobListingRPAClient(BossRPAClient):
	"""用内存页面响应隔离职位列表的 DOM 投影逻辑。"""

	def __init__(self, response: object) -> None:
		super().__init__()
		self._response = response

	def navigate_to(self, url: str) -> None:
		"""测试不打开真实页面。"""

	def wait_loaded(self, timeout: float = 10.0) -> bool:
		"""测试中的页面已经处于可读取状态。"""
		return True

	def human_delay(self, low: float = 0.5, high: float = 1.5) -> None:
		"""测试不引入随机等待。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""返回模拟的职位卡片投影。"""
		return self._response


def test_list_jobs_preserves_structured_job_cards() -> None:
	"""职位管理页卡片必须保留名称与平台职位标识，不能把字典转成文本。"""
	client = _JobListingRPAClient([
		{"encryptJobId": "encrypted-java", "jobId": "123", "jobName": "Java 后端工程师", "status": "online"},
		{"jobName": "销售顾问", "status": "online"},
	])

	assert client.list_jobs() == {
		"code": 0,
		"zpData": {
			"list": [
				{"encryptJobId": "encrypted-java", "jobId": "123", "jobName": "Java 后端工程师", "status": "online"},
				{"encryptJobId": "", "jobId": "", "jobName": "销售顾问", "status": "online"},
			]
		},
	}


def test_list_jobs_preserves_boss_detail_fields_for_workspace_sync() -> None:
	"""RPA 已读取到的职位详情必须保留，不能在客户端投影时丢弃。"""
	client = _JobListingRPAClient([{
		"encryptJobId": "encrypted-java",
		"jobId": "123",
		"jobName": "Java 后端工程师",
		"status": "online",
		"city": "广州",
		"salary_range": "150-200元/天",
		"education_requirement": "本科",
		"description": "负责软件系统开发、维护与功能迭代。",
		"keywords": "Java、Spring、MySQL",
	}])

	assert client.list_jobs()["zpData"]["list"][0] == {
		"encryptJobId": "encrypted-java",
		"jobId": "123",
		"jobName": "Java 后端工程师",
		"status": "online",
		"city": "广州",
		"salary_range": "150-200元/天",
		"education_requirement": "本科",
		"description": "负责软件系统开发、维护与功能迭代。",
		"keywords": "Java、Spring、MySQL",
	}


class _JobDetailRPAClient(_JobListingRPAClient):
	"""模拟点击职位卡片后从编辑面板读取完整 BOSS 条件。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		if "job-detail-rpa-click" in js:
			return True
		if "job-detail-rpa-read" in js:
			return {
				"city": "广州",
				"salary_range": "150-200元/天",
				"education_requirement": "本科",
				"description": "负责客户开发与电话邀约。",
				"keywords": "Java、Docker、MySQL",
				"experience_requirement": "在校/应届",
				"internship_requirement": "4个月，每周5天",
			}
		return super()._eval(js, await_promise=await_promise)


def test_list_jobs_reads_missing_fields_from_job_detail_panel() -> None:
	"""卡片摘要缺字段时，RPA 必须打开详情面板补齐岗位管理信息。"""
	client = _JobDetailRPAClient([{
		"encryptJobId": "encrypted-java",
		"jobId": "123",
		"jobName": "Java",
		"status": "online",
	}])

	item = client.list_jobs()["zpData"]["list"][0]

	assert item["salary_range"] == "150-200元/天"
	assert item["education_requirement"] == "本科"
	assert item["description"] == "负责客户开发与电话邀约。"
	assert item["experience_requirement"] == "在校/应届"
	assert item["internship_requirement"] == "4个月，每周5天"


class _ConversationFallbackRPAClient(_JobListingRPAClient):
	"""模拟职位管理页不可用时的沟通页岗位字段。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""职位卡片为空，但沟通卡片保留了岗位名称。"""
		if "source-job" in js:
			return ["Java", "售后技术支持", "Java"]
		return []


def test_list_jobs_does_not_use_historical_conversation_job_names() -> None:
	"""职位管理页异常时，候选人沟通卡片不能作为职位同步来源。"""
	client = _ConversationFallbackRPAClient([])

	assert client.list_jobs()["zpData"]["list"] == []


class _DelayedConversationFallbackRPAClient(_JobListingRPAClient):
	"""模拟沟通页在首次读取时尚未完成卡片渲染。"""

	def __init__(self) -> None:
		super().__init__([])
		self._conversation_reads = 0

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""第一次没有岗位标签，第二次出现页面岗位字段。"""
		if "source-job" in js:
			self._conversation_reads += 1
			return [] if self._conversation_reads == 1 else ["Java", "售后技术支持"]
		return []


def test_list_jobs_does_not_wait_for_historical_conversation_job_cards() -> None:
	"""候选人历史标签不是职位管理数据，不能因异步出现而被同步。"""
	client = _DelayedConversationFallbackRPAClient()

	assert client.list_jobs()["zpData"]["list"] == []


class _ChatJobSelectorFallbackRPAClient(_JobListingRPAClient):
	"""模拟职位管理页不可用时，聊天页“全部职位”筛选器已渲染的选项。"""

	def _eval(self, js: str, *, await_promise: bool = False) -> Any:
		"""仅向职位筛选器读取脚本返回可见文本，其他页面查询保持为空。"""
		if "ui-dropmenu-list" in js:
			return [
				"全部职位",
				"Java _ 广州 150-200元/天",
				"售后技术支持（关闭） _ 广州 150-200元/天",
			]
		return []


def test_list_jobs_falls_back_to_chat_job_selector_with_closed_status() -> None:
	"""职位管理页加载失败时，聊天页筛选器须保留已关闭岗位的明确状态。

	该控件直接反映当前账号可见的职位。关闭职位不能用于新的推荐读取，但其
	历史候选人与评估仍需能在本地评分看板中按原岗位查看。
	"""
	client = _ChatJobSelectorFallbackRPAClient([])

	assert client.list_jobs()["zpData"]["list"] == [
		{
			"encryptJobId": "",
			"jobId": "",
			"jobName": "Java",
			"status": "online",
			"rpaSource": "chat_job_selector",
		},
		{
			"encryptJobId": "",
			"jobId": "",
			"jobName": "售后技术支持",
			"status": "closed",
			"rpaSource": "chat_job_selector",
		},
	]


def test_select_conversation_job_chooses_the_exact_online_job_and_verifies_echo(monkeypatch) -> None:
	"""自动化必须将本地 Java 岗位切到 BOSS 的 Java 沟通列表。

	同一账号同时存在关闭岗位时，只能按筛选器解析后的岗位名匹配在线项，并在
	点击后验证顶部控件回显，避免在“全部职位”下跨岗位扫描。
	"""
	client = object.__new__(BossRPAClient)
	clicked: list[str] = []
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str, **_kwargs: Any) -> Any:
		if "trigger.getAttribute('title')" in script:
			return "Java _ 广州 150-200元/天" if clicked else "全部职位"
		if "targetOptionText" in script:
			assert "Java _ 广州 150-200元/天" in script
			assert "售后技术支持" not in script
			clicked.append("Java")
			return True
		if ".chat-select-job" in script:
			return True
		if "ui-dropmenu-list" in script:
			return [
				"全部职位",
				"Java _ 广州 150-200元/天",
				"售后技术支持（关闭） _ 广州 150-200元/天",
			]
		raise AssertionError(f"未预期的筛选器脚本: {script}")

	monkeypatch.setattr(client, "_eval", evaluate)

	result = client.select_conversation_job("Java")

	assert result == {"code": 0, "zpData": {"selectedJobName": "Java"}}
	assert clicked == ["Java"]


def test_select_conversation_job_does_not_reselect_the_current_job(monkeypatch) -> None:
	"""后台轮询命中当前岗位时必须保持页面不动，不能每 20 秒刷新沟通列表。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "_selected_chat_job_selector_text", lambda: "Java _ 广州 150-200元/天")

	def unexpected_open() -> bool:
		"""当前岗位已经正确时，职位菜单不应被打开或再次点击。"""
		raise AssertionError("当前岗位不应重复选择")

	monkeypatch.setattr(client, "_open_chat_job_selector", unexpected_open)

	assert client.select_conversation_job("Java") == {
		"code": 0,
		"zpData": {"selectedJobName": "Java"},
	}


def test_select_all_conversation_jobs_clears_current_job_and_verifies_echo(monkeypatch) -> None:
	"""全部岗位必须主动清除当前 Java 等单岗筛选，不能只读取当前页面。"""
	client = object.__new__(BossRPAClient)
	clicked: list[str] = []
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str, **_kwargs: Any) -> Any:
		if "trigger.getAttribute('title')" in script:
			return "全部职位" if clicked else "Java _ 广州 150-200元/天"
		if "targetOptionText" in script:
			assert "全部职位" in script
			clicked.append("全部职位")
			return True
		if ".chat-select-job" in script:
			return True
		if "ui-dropmenu-list" in script:
			return ["全部职位", "Java _ 广州 150-200元/天"]
		raise AssertionError(f"未预期的筛选器脚本: {script}")

	monkeypatch.setattr(client, "_eval", evaluate)

	assert client.select_all_conversation_jobs() == {"code": 0, "zpData": {"selectedScope": "all"}}
	assert clicked == ["全部职位"]


def test_open_chat_job_selector_does_not_toggle_an_already_open_menu(monkeypatch) -> None:
	"""筛选器已展开时不得再次点击触发器，否则会把菜单关闭。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_visible_chat_job_selector_names", lambda: ["全部职位", "Java _ 广州 150-200元/天"])

	def unexpected_click(_script: str, **_kwargs: Any) -> Any:
		raise AssertionError("已展开的职位筛选器不应再次点击")

	monkeypatch.setattr(client, "_eval", unexpected_click)

	assert client._open_chat_job_selector() is True


def test_select_conversation_job_allows_a_closed_job_for_historical_conversation_sync(monkeypatch) -> None:
	"""关闭职位仍须可筛选，以同步该岗位已经存在的沟通记录。"""
	client = object.__new__(BossRPAClient)
	monkeypatch.setattr(client, "_ensure_chat_page", lambda: None)
	monkeypatch.setattr(client, "_ensure_recruiter_page_ready", lambda: None)
	monkeypatch.setattr(client, "human_delay", lambda *_args: None)

	def evaluate(script: str, **_kwargs: Any) -> Any:
		if "trigger.getAttribute('title')" in script:
			return "售后技术支持（关闭） _ 广州 150-200元/天"
		if "targetOptionText" in script:
			assert "售后技术支持（关闭） _ 广州 150-200元/天" in script
			return True
		if ".chat-select-job" in script:
			return True
		if "ui-dropmenu-list" in script:
			return ["全部职位", "售后技术支持（关闭） _ 广州 150-200元/天"]
		raise AssertionError(f"未预期的筛选器脚本: {script}")

	monkeypatch.setattr(client, "_eval", evaluate)

	assert client.select_conversation_job("售后技术支持") == {
		"code": 0,
		"zpData": {"selectedJobName": "售后技术支持"},
	}
