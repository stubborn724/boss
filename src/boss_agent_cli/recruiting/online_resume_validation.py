"""BOSS 在线简历正文的轻量校验。

在线简历预览页面同时包含候选人资料、操作菜单和沟通进度。这个模块只负责
判断一段文本是否足以作为“简历正文”交给本地工作台，避免 RPA 页面外壳被
误当成简历；它不负责解析、评分或保存简历。
"""

from __future__ import annotations

import re


# 这些词只表示预览页的操作区或沟通状态，不表示候选人的履历内容。
_BOSS_PREVIEW_SHELL_MARKERS = (
	"收藏",
	"转发",
	"举报",
	"继续沟通",
	"沟通中",
	"同事沟通进度",
	"我的沟通进度",
	"向您发起沟通",
)

# 在线简历字段会随 BOSS 页面版本变化，因此只使用宽松的资料词作门禁，
# 不要求每份简历都必须同时包含教育、工作和项目三个模块。
_RESUME_CONTENT_MARKERS = (
	"学历",
	"本科",
	"大专",
	"硕士",
	"博士",
	"教育经历",
	"工作经历",
	"工作经验",
	"项目经历",
	"实习经历",
	"技能",
	"专业技能",
	"期望薪资",
	"求职意向",
	"工作年限",
)

# Canvas OCR 对小字号中文字段容易出现单字偏差，但英文技术栈通常较稳定。
# 这些词只作为“长正文”的辅助证据，至少命中三个且不能同时出现多个页面外壳
# 标记，避免仅含“沟通职位 Java”的聊天侧栏被误判为简历。
_OCR_STABLE_TECHNICAL_MARKERS = (
	"java",
	"spring",
	"mysql",
	"mybatis",
	"maven",
	"rest",
	"python",
	"linux",
	"docker",
	"项目",
	"开发",
	"计算机",
)


def is_meaningful_online_resume_text(text: str) -> bool:
	"""判断文本是否不是纯 BOSS 页面外壳并具备基本简历信号。

	页面外壳可能有几十个字符，所以“非空”不能作为成功条件。若文本同时
	命中多个固定外壳标记，却没有任何履历字段标记，则视为读取失败；这样
	既能拦截沟通侧栏，也不会限制只有少量公开字段的真实简历。
	"""
	normalized = re.sub(r"\s+", "", str(text or ""))
	if not normalized:
		return False
	# OCR 可能在空白 Canvas 上返回少量数字、标点或替换字符。仅检查“非空”
	# 会把这种噪声当成成功结果，因此正文必须同时具备基本有效字符数量和
	# 至少一个稳定的履历字段。真实简历即使公开字段很少，也会包含学历、
	# 经历、技能或求职意向中的一项。
	meaningful_characters = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
	shell_hits = sum(marker in normalized for marker in _BOSS_PREVIEW_SHELL_MARKERS)
	resume_hits = sum(marker in normalized for marker in _RESUME_CONTENT_MARKERS)
	if len(meaningful_characters) < 8:
		return False
	if resume_hits == 0:
		# 中文标题被 OCR 轻微误识时，使用长正文和多个稳定技术词作为容错门禁。
		# 短文本或同时包含多个沟通页操作标记仍然失败，不能因此放宽页面壳校验。
		technical_text = normalized.casefold()
		technical_hits = sum(marker in technical_text for marker in _OCR_STABLE_TECHNICAL_MARKERS)
		return len(meaningful_characters) >= 120 and technical_hits >= 3 and shell_hits < 2
	return shell_hits < 2 or resume_hits > 0
