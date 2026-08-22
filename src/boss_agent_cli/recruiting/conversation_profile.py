"""沟通页和推荐卡片的候选人职业资料快照。

本模块只承载 RPA 可见的职业信息，以及对话中主动提供的粗粒度通勤事实。它不保存
精确住址、年龄、性别或完整聊天内容，使硬筛可以在调用 AI 前完成且便于审计。
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_DEGREES = ("博士", "硕士", "本科", "大专", "高中", "中专")
_DISTRICT_RE = re.compile(r"([\u4e00-\u9fff]{2,8}区)")
_STATION_RE = re.compile(r"([\u4e00-\u9fff]{2,12})(?:地铁站|站)附近")
_MINUTES_RE = re.compile(r"(\d{1,3})\s*分钟")
_SALARY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:[-~至到]\s*\d+(?:\.\d+)?)?\s*[Kk万]")


@dataclass(frozen=True)
class ConversationProfile:
	"""候选人的最小职业资料，用于硬筛和对话事实合并。"""

	work_period: str = ""
	work_company: str = ""
	work_position: str = ""
	education_period: str = ""
	education_school: str = ""
	education_major: str = ""
	education_degree: str = ""
	communication_job: str = ""
	expectation_city: str = ""
	expectation_cities: tuple[str, ...] = ()
	expectation_position: str = ""
	expected_salary: str = ""
	commute_district: str = ""
	commute_station: str = ""
	acceptable_commute_minutes: int | None = None

	@classmethod
	def from_display_fields(
		cls,
		*,
		work_text: str = "",
		education_text: str = "",
		communication_job: str = "",
		expectation_text: str = "",
	) -> "ConversationProfile":
		"""从红框的已分区展示文本构造快照，不猜测缺失字段。"""
		work_parts = _parts(work_text)
		education_parts = _parts(education_text)
		expectation_parts = _parts(expectation_text)
		expectation_cities = _cities(expectation_text)
		degree = next((item for item in _DEGREES if item in education_text), "")
		salary_match = _SALARY_RE.search(expectation_text)
		salary = salary_match.group(0).replace(" ", "") if salary_match else ""
		return cls(
			work_period=work_parts[0] if work_parts else "",
			work_company=work_parts[1] if len(work_parts) > 1 else "",
			work_position=work_parts[-1] if len(work_parts) > 2 else "",
			education_period=education_parts[0] if education_parts else "",
			education_school=education_parts[1] if len(education_parts) > 1 else "",
			education_major=education_parts[2] if len(education_parts) > 3 else "",
			education_degree=degree,
			communication_job=communication_job.strip(),
			expectation_city=expectation_cities[0] if expectation_cities else (expectation_parts[0] if expectation_parts else ""),
			expectation_cities=expectation_cities,
			expectation_position=expectation_parts[1] if len(expectation_parts) > 1 else "",
			expected_salary=salary,
		)

	@classmethod
	def from_dialogue_fact(cls, dimension: str, value: str) -> "ConversationProfile":
		"""从候选人主动陈述提取通勤粗粒度事实，忽略地址细节。"""
		if dimension.strip() != "通勤情况":
			return cls()
		district = _DISTRICT_RE.search(value)
		station = _STATION_RE.search(value)
		minutes = _MINUTES_RE.search(value)
		district_name = _clean_district(district.group(1)) if district else ""
		return cls(
			commute_district=district_name,
			commute_station=_clean_station(station.group(1), district_name) if station else "",
			acceptable_commute_minutes=int(minutes.group(1)) if minutes else None,
		)

	def to_dict(self) -> dict[str, object]:
		"""输出明确白名单，防止调用方意外扩展为个人敏感资料。"""
		return {
			"work_period": self.work_period,
			"work_company": self.work_company,
			"work_position": self.work_position,
			"education_period": self.education_period,
			"education_school": self.education_school,
			"education_major": self.education_major,
			"education_degree": self.education_degree,
			"communication_job": self.communication_job,
			"expectation_city": self.expectation_city,
			"expectation_cities": list(self.expectation_cities),
			"expectation_position": self.expectation_position,
			"expected_salary": self.expected_salary,
			"commute_district": self.commute_district,
			"commute_station": self.commute_station,
			"acceptable_commute_minutes": self.acceptable_commute_minutes,
		}


def _parts(value: str) -> list[str]:
	"""按页面空白拆分文字，保留公司和专业中的中文标点。"""
	return [item.strip() for item in re.split(r"\s+", value.strip()) if item.strip()]


def _cities(value: str) -> tuple[str, ...]:
	"""提取期望栏中以分隔符展示的多个城市，不把岗位名称误作城市。"""
	before_position = re.split(r"\s+(?=[A-Za-z]|\d+(?:\.\d+)?\s*[Kk万])", value.strip(), maxsplit=1)[0]
	parts = re.split(r"\s*(?:&|、|/|,|，|·|和)\s*", before_position)
	return tuple(dict.fromkeys(item.strip() for item in parts if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", item.strip())))


def _clean_district(value: str) -> str:
	"""去掉口语前缀，确保持久化值始终是行政区名。"""
	for prefix in ("住在", "位于", "在"):
		if value.startswith(prefix):
			return value[len(prefix):]
	return value


def _clean_station(value: str, district: str) -> str:
	"""从口语整句中裁出地铁站名称，不把地址前缀持久化。"""
	station = value.replace("地铁", "")
	if district and district in station:
		station = station.split(district, 1)[1]
	return station.removeprefix("住在").removeprefix("位于").removeprefix("在")
