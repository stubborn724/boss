"""招聘自动化外部文本的 Unicode 安全边界测试。"""

from boss_agent_cli.recruiting.unicode_safety import sanitize_json_value, sanitize_unicode_text


def test_sanitize_unicode_text_replaces_only_unpaired_surrogates() -> None:
	"""孤立代理字符必须被替换，合法中文和完整 emoji 必须保留。"""
	value = "候选人\ud83d已回复 \U0001f4cb"

	result = sanitize_unicode_text(value)

	assert result == "候选人\ufffd已回复 \U0001f4cb"
	assert result.encode("utf-8")


def test_sanitize_json_value_recursively_cleans_keys_and_values() -> None:
	"""状态写入前应递归清理容器，避免深层平台字段破坏整份 JSON。"""
	value = {"候选人\ud83d": ["回复\udc00", ("正常", "\U0001f4cb")]}

	result = sanitize_json_value(value)

	assert result == {"候选人\ufffd": ["回复\ufffd", ("正常", "\U0001f4cb")]}

