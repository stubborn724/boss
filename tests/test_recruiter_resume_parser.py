"""BOSS 在线简历字段漂移的归一化回归测试。"""

from boss_agent_cli.commands.recruiter.resume_parser import parse_resume


def test_parse_resume_accepts_alternate_detail_envelope_and_field_names() -> None:
	"""不同页面版本的包络和字段别名应转换成同一份结构化简历。"""
	raw = {
		"code": 0,
		"data": {
			"geekInfo": {
				"baseInfo": {
					"userName": "李四",
					"degreeName": "本科",
					"experience": "3年",
					"activeStatus": "今日活跃",
				},
				"expectation": {"position": "销售顾问", "expectedSalary": "12-18K", "city": "杭州"},
				"workExperienceList": [
					{"companyName": "甲公司", "position": "客户经理", "start": "2022-01", "end": "2024-01", "description": "开发客户"}
				],
				"educationList": [{"schoolName": "某大学", "majorName": "市场营销", "degreeName": "本科"}],
				"projectList": [{"projectName": "客户增长", "role": "负责人", "description": "提升转化"}],
				"certifications": ["普通话证书", {"name": "销售认证"}],
			}
		},
	}

	parsed = parse_resume(raw)

	assert parsed["basic"]["name"] == "李四"
	assert parsed["basic"]["degree"] == "本科"
	assert parsed["basic"]["work_years"] == "3年"
	assert parsed["expectation"] == {"position": "销售顾问", "salary": "12-18K", "city": "杭州"}
	assert parsed["work_experience"][0]["company"] == "甲公司"
	assert parsed["work_experience"][0]["responsibility"] == "开发客户"
	assert parsed["education"][0]["school"] == "某大学"
	assert parsed["project_experience"][0]["name"] == "客户增长"
	assert parsed["certifications"] == ["普通话证书", "销售认证"]


def test_parse_resume_ignores_malformed_list_items_without_failing() -> None:
	"""平台偶发返回 null/字符串列表项时，导出仍应保留可用的基础字段。"""
	raw = {
		"zpData": {
			"geekDetailInfo": {
				"geekBaseInfo": {"name": "王五"},
				"geekWorkExpList": [None, "not-a-record", {"company": "乙公司"}],
				"geekEduExpList": [None, "not-a-record"],
				"geekCertificationList": [None, "普通话证书"],
			}
		}
	}

	parsed = parse_resume(raw)

	assert parsed["basic"]["name"] == "王五"
	assert parsed["work_experience"] == [{
		"company": "乙公司",
		"position": "",
		"department": "",
		"start": "",
		"end": "",
		"duration": "",
		"responsibility": "",
		"performance": "",
		"keywords": [],
	}]
	assert parsed["education"] == []
	assert parsed["certifications"] == ["普通话证书"]
