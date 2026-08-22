"""推荐牛人响应归一化测试。

这些用例只验证平台适配边界，不访问真实账号。推荐接口字段经常随页面版本
变化，因此测试明确覆盖常见包络和缺失字段，避免 Web 层直接依赖某一版 JSON。
"""

from boss_agent_cli.commands.recruiter.recommendation_service import (
	normalize_recommendation_response,
	prepare_recommendation_query,
	resolve_recommendation_query_target,
)
from boss_agent_cli.commands.recruiter.ai_dialogue import _eligible_recommendations, greet_recommendations_once
from boss_agent_cli.recruiting.models import JobProfile


def test_recommendation_query_target_keeps_platform_id_and_uses_job_name() -> None:
	"""推荐页读取必须用 BOSS 标识取数，并按岗位名称驱动 RPA 筛选回显。"""
	job = JobProfile(
		job_id="job-local-java",
		platform_job_id="rpa-java-snapshot",
		name="Java",
	)

	target = resolve_recommendation_query_target(job)

	assert target.platform_job_id == "rpa-java-snapshot"
	assert target.job_name == "Java"


def test_prepare_recommendation_query_sets_job_name_before_using_platform_id() -> None:
	"""RPA 临时职位 ID 不能阻止按岗位名称切换推荐页。"""
	class _Platform:
		def __init__(self) -> None:
			self.selected_job_names: list[str] = []

		def set_recommendation_job(self, job_name: str) -> dict[str, object]:
			self.selected_job_names.append(job_name)
			return {"code": 0}

		@staticmethod
		def is_success(response: dict[str, object]) -> bool:
			return response.get("code") == 0

	job = JobProfile(
		job_id="job-local-java",
		platform_job_id="rpa-java-snapshot",
		name="Java",
	)
	platform = _Platform()

	assert prepare_recommendation_query(platform, job) == "rpa-java-snapshot"
	assert platform.selected_job_names == ["Java"]


def test_normalize_recommendation_response_maps_ids_and_display_fields() -> None:
	"""标准 geekList 响应应同时保留下载定位和页面展示信息。"""
	items = normalize_recommendation_response(
		{
			"code": 0,
			"zpData": {
				"geekList": [
					{
						"encryptGeekId": "geek-1",
						"encryptJobId": "job-1",
						"securityId": "security-1",
						"friendId": 42,
						"geekName": "张三",
						"jobName": "Python 工程师",
						"cityName": "上海",
						"experienceName": "5 年",
						"degreeName": "本科",
						"salaryDesc": "20-30K",
						"activeTimeDesc": "今日活跃",
						"companyName": "示例公司",
					}
				]
			}
		}
	)

	assert len(items) == 1
	item = items[0]
	assert item.geek_id == "geek-1"
	assert item.job_id == "job-1"
	assert item.security_id == "security-1"
	assert item.friend_id == 42
	assert item.candidate_name == "张三"
	assert item.title == "Python 工程师"
	assert item.city == "上海"
	assert item.experience == "5 年"
	assert item.degree == "本科"
	assert item.salary == "20-30K"
	assert item.active_time == "今日活跃"
	assert item.company == "示例公司"
	assert item.can_download is True

	public = item.to_public_dict()
	assert public["candidate_name"] == "张三"
	assert public["can_download"] is True
	assert "geek_id" not in public
	assert "security_id" not in public
	assert "friend_id" not in public


def test_normalize_recommendation_response_supports_data_list_variant() -> None:
	"""平台切换到 data.list 包络时仍应能显示候选人。"""
	items = normalize_recommendation_response(
		{
			"code": 0,
			"data": {
				"list": [
					{
						"geekId": "geek-2",
						"jobId": "job-2",
						"security_id": "security-2",
						"name": "李四",
						"title": "数据分析师",
						"city": "杭州",
					}
				]
			}
		}
	)

	assert len(items) == 1
	assert items[0].candidate_name == "李四"
	assert items[0].title == "数据分析师"
	assert items[0].city == "杭州"


def test_normalize_recommendation_response_extracts_city_and_degree_from_boss_card_labels() -> None:
	"""推荐页的“最近关注 广州 Java”“学历 学校 专业 本科”须归一为硬筛字段。"""
	items = normalize_recommendation_response({
		"code": 0,
		"zpData": {"geekList": [{
			"geekId": "geek-3", "jobId": "job-3", "securityId": "security-3",
			"name": "王五", "cityName": "最近关注 广州 Java", "degreeName": "学历 广州大学 软件工程 本科",
		}]},
	})

	assert items[0].city == "广州"
	assert items[0].degree == "本科"
	assert items[0].can_download is True


def test_normalize_recommendation_response_keeps_incomplete_card_but_disables_download() -> None:
	"""缺少定位 ID 的卡片仍可展示，但不能被误认为可导出。"""
	items = normalize_recommendation_response(
		{
			"code": 0,
			"zpData": {"result": [{"name": "未完整候选人", "jobName": "运营"}, "invalid"]},
		}
	)

	assert len(items) == 1
	assert items[0].candidate_name == "未完整候选人"
	assert items[0].can_download is False
	assert items[0].to_public_dict()["download_hint"] == "平台未提供完整的简历定位信息"


def test_normalize_recommendation_response_returns_empty_for_unknown_envelope() -> None:
	"""未知包络不应抛出异常或把任意对象当作候选人。"""
	assert normalize_recommendation_response({"code": 0, "zpData": {"unexpected": {}}}) == []


def test_normalize_recommendation_response_does_not_guess_friend_id_from_plain_uid() -> None:
	"""普通候选人 UID 不能被猜成沟通会话 ID，避免附件导出错配对象。"""
	items = normalize_recommendation_response({
		"code": 0,
		"zpData": {"geekList": [{
			"geekId": "geek-3", "jobId": "job-3", "securityId": "security-3",
			"uid": 99, "name": "王五",
		}]},
	})

	assert items[0].friend_id is None


def test_eligible_recommendations_skips_explicit_hard_mismatch() -> None:
	"""推荐卡片明确显示城市或学历不符时，命令层不得对其发送招呼语。"""
	job = JobProfile(job_id="job-1", name="Java 实习生", city="广州", education_requirement="本科")
	candidates = normalize_recommendation_response({
		"code": 0,
		"zpData": {"geekList": [
			{"geekId": "reject", "jobId": "boss-job", "securityId": "sid-1", "name": "不匹配", "city": "上海", "degree": "大专"},
			{"geekId": "allow", "jobId": "boss-job", "securityId": "sid-2", "name": "信息缺失", "city": "", "degree": ""},
		]},
	})

	eligible, rejected = _eligible_recommendations(job=job, candidates=candidates)

	assert [candidate.geek_id for candidate in eligible] == ["allow"]
	assert rejected == 1


def test_recommendation_greeting_reads_boss_platform_job_id(tmp_path) -> None:
	"""推荐页必须传 BOSS 岗位标识，不能把本地工作台 UUID 当成平台职位。"""
	class _Platform:
		def __init__(self) -> None:
			self.list_job_ids: list[str] = []

		def greet_rec_list(self, *, page: int, job_id: str) -> dict[str, object]:
			self.list_job_ids.append(job_id)
			return {"code": 0, "zpData": {"geekList": [{
				"geekId": "geek-1", "jobId": "boss-java", "securityId": "sid-1",
				"name": "候选人", "city": "广州", "degree": "本科",
			}]}}

		def sync_job_greeting(self, _job_name: str, _content: str) -> dict[str, object]:
			return {"code": 0}

		def greet_recommendation_by_geek_id(self, _geek_id: str) -> dict[str, object]:
			return {"code": 0, "zpData": {"status": "sent"}}

		@staticmethod
		def is_success(response: dict[str, object]) -> bool:
			return response.get("code") == 0

		@staticmethod
		def unwrap_data(response: dict[str, object]) -> object:
			return response.get("zpData")

	platform = _Platform()
	job = JobProfile(
		job_id="local-job-id",
		platform_job_id="boss-java",
		name="Java 开发工程师",
		city="广州",
		education_requirement="本科",
		greeting_message="您好，感谢关注Java 开发工程师岗位。方便确认您的最高学历、相关工作年限、所在城市及到岗时间吗？",
	)

	activities = greet_recommendations_once(data_dir=tmp_path, platform=platform, job=job, limit=1)

	assert activities == ["已向推荐候选人 候选人 发送招呼"]
	assert platform.list_job_ids == ["boss-java", "boss-java"]
