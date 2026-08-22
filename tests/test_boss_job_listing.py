"""BOSS 已发布职位响应归一化测试。

职位列表接口的包络和字段名称会随平台页面版本变化。测试把这类兼容性固定在
纯函数边界，避免 Web 命令层直接依赖任意一版原始 JSON。
"""

from boss_agent_cli.commands.recruiter.boss_job_listing import (
	normalize_boss_job_response,
	normalize_boss_job_sync_response,
)


def test_normalize_boss_job_response_keeps_encrypted_online_jobs() -> None:
	"""页面只应获得可用于推荐读取的加密职位标识和显示名称。"""
	items = normalize_boss_job_response({
		"code": 0,
		"zpData": {
			"list": [
				{"encryptJobId": "enc-online", "jobName": "招商主管", "status": "online"},
				{"encryptJobId": "enc-offline", "jobName": "历史职位", "status": "offline"},
				{"jobId": "plain-id", "jobName": "缺少加密标识"},
			]
		},
	})

	assert items == [{"job_id": "enc-online", "name": "招商主管"}]


def test_normalize_boss_job_response_accepts_nested_job_list_and_numeric_ids() -> None:
	"""嵌套 ``data`` 包络和数值标识也应能稳定投影到页面选择器。"""
	items = normalize_boss_job_response({
		"data": {"result": {"jobList": [
			{"encryptJobId": 12345, "title": "Python 工程师", "jobStatus": 1},
		]}},
	})

	assert items == [{"job_id": "12345", "name": "Python 工程师"}]


def test_normalize_boss_job_response_uses_fallback_name_without_leaking_raw_records() -> None:
	"""缺失职位名称不应阻止选择，但不应把原始平台字段返回给页面。"""
	items = normalize_boss_job_response({"zpData": {"items": [{"encryptJobId": "enc-1"}]}})

	assert items == [{"job_id": "enc-1", "name": "未命名职位"}]


def test_normalize_boss_job_sync_response_keeps_only_identified_online_jobs() -> None:
	"""工作台镜像只能采用职位管理页的真实在线职位，不能猜测名称身份。

	沟通列表的岗位标签只反映候选人历史会话，既可能是已下线职位，也没有
	稳定的平台标识。同步时若把这类记录写入工作台，会造成平台与本地岗位不一致。
	"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [
			{"encryptJobId": "enc-active", "jobName": "招商主管", "status": "online"},
			{"encryptJobId": "enc-offline", "jobName": "历史职位", "status": "offline"},
			{"jobName": "沟通列表中的历史岗位", "status": "online"},
		]}
	})

	assert items == [{"job_id": "enc-active", "name": "招商主管"}]


def test_normalize_boss_job_sync_response_keeps_boss_hard_fields_and_description() -> None:
	"""职位管理页已有的硬条件和描述必须随同步快照传给本地岗位。"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [{
			"encryptJobId": "enc-java",
			"jobName": "Java",
			"cityName": "广州",
			"salaryDesc": "150-200元/天",
			"degree": "本科",
			"jobDescription": "负责软件系统开发、维护与功能迭代。",
			"keywords": ["Java", "Spring", "MySQL"],
		}]},
	})

	assert items == [{
		"job_id": "enc-java",
		"name": "Java",
		"city": "广州",
		"salary_range": "150-200元/天",
		"education_requirement": "本科",
		"description": "负责软件系统开发、维护与功能迭代。",
		"keywords": "Java、Spring、MySQL",
	}]


def test_normalize_boss_job_sync_response_keeps_full_detail_requirements() -> None:
	"""职位管理详情中的经验、实习周期和工作日不能在归一化时丢失。"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [{
			"encryptJobId": "enc-java",
			"jobName": "Java",
			"experienceRequirement": "在校/应届",
			"internshipRequirement": "4个月",
			"workDays": "每周5天",
			"workAddress": "天河区创新创业孵化基地",
		}]},
	})

	assert items[0]["experience_requirement"] == "在校/应届"
	assert items[0]["internship_requirement"] == "4个月"
	assert items[0]["work_days"] == "每周5天"
	assert items[0]["work_address"] == "天河区创新创业孵化基地"


def test_normalize_boss_job_sync_response_generates_key_only_for_chat_selector_fallback() -> None:
	"""聊天页职位筛选器是唯一允许生成本地关联键的无 ID RPA 来源。

	候选人沟通卡片也可能出现岗位名称，但它不等同于职位管理状态，因此无论
	名称如何都不能通过这条降级路径进入工作台。
	"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [
			{"jobName": "Java", "status": "online", "rpaSource": "chat_job_selector"},
			{"jobName": "沟通卡片历史岗位", "status": "online"},
		]}
	})

	assert items == [{
		"job_id": "rpa-38a0963a6364b09a",
		"name": "Java",
		"status": "online",
	}]


def test_normalize_boss_job_sync_response_generates_key_for_job_management_rpa() -> None:
	"""职位管理卡片没有平台 ID 时，也必须进入本地镜像而不是被过滤。"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [{
			"jobName": "Java",
			"city": "广州",
			"salary_range": "150-200元/天",
		}]}
	}, rpa_source="job_management")

	assert items == [{
		"job_id": "rpa-38a0963a6364b09a",
		"name": "Java",
		"city": "广州",
		"salary_range": "150-200元/天",
	}]


def test_normalize_boss_job_sync_response_keeps_closed_chat_selector_job() -> None:
	"""BOSS 筛选器明确标记关闭的岗位仍要作为历史评估归属保留。"""
	items = normalize_boss_job_sync_response({
		"zpData": {"list": [
			{"jobName": "售后技术支持", "status": "closed", "rpaSource": "chat_job_selector"},
		]}
	})

	assert items == [{
		"job_id": "rpa-0d0c56b526a6b3b6",
		"name": "售后技术支持",
		"status": "closed",
	}]
