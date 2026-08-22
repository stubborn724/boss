"""招聘自动化外部文本的 Unicode 安全边界。

BOSS 页面、浏览器桥接和 PDF 提取器都属于外部数据源。它们偶尔会返回孤立的
UTF-16 代理字符；该字符可存在于 Python 字符串，却不能编码为 UTF-8，会在 AI
请求或状态落盘时中断整轮自动化。本模块只替换这种非法码位，不改动正常中文、
完整 emoji 或其他合法 Unicode 字符。
"""

from __future__ import annotations

from typing import Any


_REPLACEMENT_CHARACTER = "\ufffd"


def sanitize_unicode_text(value: str) -> str:
	"""替换字符串中的孤立代理码位，使结果始终可安全编码为 UTF-8。

	Python 中完整 emoji 已经是单个 Unicode 码点，不落在代理区间，因此不会被
	误伤。这里只处理 ``U+D800`` 到 ``U+DFFF``，保留尽可能多的候选人原始信息。
	"""
	if not any(0xD800 <= ord(character) <= 0xDFFF for character in value):
		return value
	return "".join(
		_REPLACEMENT_CHARACTER if 0xD800 <= ord(character) <= 0xDFFF else character
		for character in value
	)


def sanitize_json_value(value: Any) -> Any:
	"""递归清理准备进入 JSON 的字符串键和值。

	状态对象可能在多层字典或列表中携带平台字段。持久化层调用此函数作为最后
	一道保险，避免未来新增字段时遗漏清洗。非 JSON 标量保持原值，由原序列化器
	继续负责类型校验。
	"""
	if isinstance(value, str):
		return sanitize_unicode_text(value)
	if isinstance(value, dict):
		return {
			sanitize_unicode_text(key) if isinstance(key, str) else key: sanitize_json_value(item)
			for key, item in value.items()
		}
	if isinstance(value, list):
		return [sanitize_json_value(item) for item in value]
	if isinstance(value, tuple):
		return tuple(sanitize_json_value(item) for item in value)
	return value
