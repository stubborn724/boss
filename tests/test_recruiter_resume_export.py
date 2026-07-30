"""招聘者候选人简历导出模块的契约测试。

覆盖四类关注点（先写测试后实现）：
1. Markdown 生成 — 结构化简历各段落都要落到文本里；
2. 文件名安全 — 路径分隔符、穿越、Windows 保留名、超长、空值；
3. 落盘行为 — 目录自动创建、UTF-8 编码、原子替换、不留临时文件；
4. 写入失败 — 统一抛 ResumeExportError，并清理临时文件。
"""

import os
from pathlib import Path

import pytest

from boss_agent_cli.commands.recruiter.resume_export import (
	ResumeExportError,
	ResumeExportResult,
	export_candidate_resume,
	render_candidate_resume_markdown,
	resolve_export_path,
	safe_filename_stem,
)


def _resume() -> dict:
	"""构造一份 parse_resume 输出形状的结构化简历。"""
	return {
		"basic": {
			"name": "张三",
			"gender": "男",
			"age": "28岁",
			"degree": "本科",
			"work_years": "5年",
			"active_status": "刚刚活跃",
			"avatar": "https://img.example.com/a.png",
		},
		"expectation": {"position": "后端工程师", "salary": "25-35K", "city": "上海"},
		"work_experience": [{
			"company": "示例科技",
			"position": "后端工程师",
			"department": "平台组",
			"start": "2020.03",
			"end": "2024.06",
			"duration": "4年3个月",
			"responsibility": "负责订单系统重构",
			"performance": "核心接口 QPS 提升 3 倍",
			"keywords": ["Python", "MySQL"],
		}],
		"project_experience": [{
			"name": "订单系统重构",
			"role": "技术负责人",
			"start": "2022.01",
			"end": "2022.09",
			"duration": "8个月",
			"description": "把单体订单模块拆成独立服务",
			"achievement": "端到端延迟下降 40%",
		}],
		"education": [{
			"school": "示例大学",
			"major": "计算机科学与技术",
			"degree": "本科",
			"start": "2015.09",
			"end": "2019.06",
		}],
		"competitive_analysis": ["近 3 天活跃", "技能匹配度高"],
		"certifications": ["PMP"],
	}


# ── Markdown 生成 ───────────────────────────────────────────────────


def test_render_markdown_includes_every_section():
	markdown = render_candidate_resume_markdown(_resume(), geek_id="geek_001", exported_at="2026-07-30T10:00:00")

	assert markdown.startswith("# 候选人简历 — 张三")
	assert "geek_001" in markdown
	assert "2026-07-30T10:00:00" in markdown
	for heading in ("## 基本信息", "## 求职期望", "## 工作经历", "## 项目经历", "## 教育经历", "## 证书", "## 平台竞争力提示"):
		assert heading in markdown
	assert "示例科技" in markdown
	assert "核心接口 QPS 提升 3 倍" in markdown
	assert "订单系统重构" in markdown
	assert "示例大学" in markdown
	assert "PMP" in markdown
	assert "近 3 天活跃" in markdown
	assert markdown.endswith("\n")


def test_render_markdown_omits_avatar_url():
	"""头像是候选人肖像地址，对下游文本分析无用，不写进导出文件。"""
	assert "img.example.com" not in render_candidate_resume_markdown(_resume(), geek_id="geek_001")


def test_render_markdown_tolerates_empty_resume():
	markdown = render_candidate_resume_markdown({}, geek_id="geek_404")

	assert "# 候选人简历" in markdown
	assert "geek_404" in markdown
	assert "## 工作经历" in markdown


def test_render_markdown_escapes_pipes_in_table_cells():
	"""姓名里的竖线会撑破 Markdown 表格，必须转义。"""
	resume = _resume()
	resume["basic"]["name"] = "张|三"

	assert "张\\|三" in render_candidate_resume_markdown(resume, geek_id="geek_001")


# ── 文件名安全 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/b\\c", "..", ".", "  ..  "])
def test_safe_filename_stem_strips_separators_and_traversal(raw: str):
	stem = safe_filename_stem(raw)

	assert "/" not in stem
	assert "\\" not in stem
	assert stem not in {"", ".", ".."}
	assert not stem.startswith(".")


@pytest.mark.parametrize("raw", ['a<b>c:d"e|f?g*h', "line\nbreak", "null\0byte"])
def test_safe_filename_stem_removes_reserved_and_control_chars(raw: str):
	stem = safe_filename_stem(raw)

	assert not set(stem) & set('<>:"/\\|?*')
	assert all(ord(ch) >= 32 for ch in stem)


@pytest.mark.parametrize("raw", ["CON", "con", "NUL", "com1", "LPT9"])
def test_safe_filename_stem_defuses_windows_device_names(raw: str):
	"""Windows 上 CON/NUL 之类是设备名，同名文件无法创建。"""
	assert safe_filename_stem(raw).lower() not in {"con", "nul", "com1", "lpt9"}


@pytest.mark.parametrize("raw", ["", "   ", "///", "***"])
def test_safe_filename_stem_falls_back_when_nothing_usable(raw: str):
	assert safe_filename_stem(raw, fallback="候选人") == "候选人"


def test_safe_filename_stem_truncates_overlong_input():
	stem = safe_filename_stem("名" * 500)

	assert 0 < len(stem) <= 80


# ── 导出路径解析 ────────────────────────────────────────────────────


def test_resolve_export_path_defaults_under_data_dir(tmp_path: Path):
	path = resolve_export_path(data_dir=tmp_path, geek_id="geek_001", candidate_name="张三")

	assert path == tmp_path / "recruiter" / "resumes" / "张三-geek_001.md"


def test_resolve_export_path_honors_explicit_output(tmp_path: Path):
	target = tmp_path / "out" / "custom.md"

	assert resolve_export_path(data_dir=tmp_path, geek_id="geek_001", candidate_name="张三", output=target) == target


def test_resolve_export_path_honors_output_dir_and_still_sanitizes(tmp_path: Path):
	path = resolve_export_path(
		data_dir=tmp_path,
		geek_id="geek_001",
		candidate_name="../evil",
		output_dir=tmp_path / "custom",
	)

	assert path.parent == tmp_path / "custom"
	assert path.suffix == ".md"
	assert ".." not in path.name


def test_resolve_export_path_uses_geek_id_when_name_missing(tmp_path: Path):
	path = resolve_export_path(data_dir=tmp_path, geek_id="geek_001", candidate_name="")

	assert path.name == "geek_001.md"


# ── 落盘行为 ────────────────────────────────────────────────────────


def test_export_creates_directory_and_writes_utf8(tmp_path: Path):
	result = export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path)

	assert isinstance(result, ResumeExportResult)
	assert result.path.exists()
	assert result.path.parent == tmp_path / "recruiter" / "resumes"
	assert result.path.read_text(encoding="utf-8").startswith("# 候选人简历 — 张三")
	assert result.bytes_written == len(result.path.read_bytes())


def test_export_returns_only_minimal_metadata(tmp_path: Path):
	"""返回值只带路径与最小元数据，简历正文不得随元数据外泄。"""
	result = export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path)

	assert result.geek_id == "geek_001"
	assert result.candidate_name == "张三"
	assert result.filename == "张三-geek_001.md"
	assert result.sections == [
		"basic",
		"expectation",
		"work_experience",
		"project_experience",
		"education",
		"competitive_analysis",
		"certifications",
	]
	assert "订单系统重构" not in repr(result)


def test_export_writes_to_explicit_output_path(tmp_path: Path):
	target = tmp_path / "nested" / "dir" / "zhangsan.md"

	result = export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path, output=target)

	assert result.path == target
	assert target.read_text(encoding="utf-8")


def test_export_overwrites_previous_snapshot_without_leftovers(tmp_path: Path):
	target = tmp_path / "zhangsan.md"
	target.write_text("旧内容", encoding="utf-8")

	export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path, output=target)

	assert "旧内容" not in target.read_text(encoding="utf-8")
	assert list(tmp_path.glob("*.tmp")) == []


# ── 写入失败 ────────────────────────────────────────────────────────


def test_export_wraps_replace_failure_and_cleans_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	target = tmp_path / "zhangsan.md"

	def _boom(src, dst):  # noqa: ANN001 - 仅用于模拟 os.replace 失败
		raise OSError(13, "permission denied")

	monkeypatch.setattr(os, "replace", _boom)

	with pytest.raises(ResumeExportError) as excinfo:
		export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path, output=target)

	assert "zhangsan.md" in str(excinfo.value)
	assert not target.exists()
	assert list(tmp_path.glob("*.tmp")) == []


def test_export_wraps_mkdir_failure(tmp_path: Path):
	"""父路径被普通文件占位时，创建目录失败也要走统一异常。"""
	blocker = tmp_path / "blocker"
	blocker.write_text("not a dir", encoding="utf-8")

	with pytest.raises(ResumeExportError):
		export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path, output=blocker / "a.md")


def test_export_error_message_never_contains_resume_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	def _boom(src, dst):  # noqa: ANN001 - 仅用于模拟 os.replace 失败
		raise OSError(28, "no space left on device")

	monkeypatch.setattr(os, "replace", _boom)

	with pytest.raises(ResumeExportError) as excinfo:
		export_candidate_resume(_resume(), geek_id="geek_001", data_dir=tmp_path)

	assert "订单系统重构" not in str(excinfo.value)
