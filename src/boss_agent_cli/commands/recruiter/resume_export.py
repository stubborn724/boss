"""招聘者 — 候选人在线简历导出为本地 Markdown 文件。

职责边界（MVP）
---------------
本模块只做「结构化简历 -> 本地文件」这一段，刻意不碰其它环节：

* 输入必须是 :func:`boss_agent_cli.commands.recruiter.resume_parser.parse_resume`
  已经解析好的结构化字典 —— 本模块不发起任何网络请求、不接触原始 API 响应；
* 输出是一个 UTF-8 Markdown 文件，外加一份「最小元数据」（路径、字节数、
  候选人姓名、包含哪些段落），供命令层写进 JSON 信封；
* 不做批量、不做分析、不做自动沟通 —— 这些能力由上层显式命令或另一个
  Agent 承担，避免「读 API 数据」和「文件落盘」耦合在一起。

安全约束
--------
1. 文件名一律经过 :func:`safe_filename_stem` 清洗：候选人姓名来自平台，
   属于不可信输入，可能含路径分隔符、`..`、控制字符或 Windows 设备名；
2. 写盘走「临时文件 + :func:`os.replace`」的原子替换，进程中断不会留下
   半截简历；失败时清理临时文件并抛 :class:`ResumeExportError`；
3. 异常信息只包含路径和系统错误原因，绝不回带简历正文。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 默认导出目录：<data_dir>/recruiter/resumes/
# 单独开一层 recruiter/，和求职者自己的简历目录 <data_dir>/resumes/ 物理隔开，
# 避免「我的简历」和「候选人简历」两类完全不同的个人数据混在一个目录里。
_EXPORT_SUBDIRS = ("recruiter", "resumes")

# 文件名主干长度上限。Windows 单个路径段上限 255，这里留足余量给
# `-<geek_id>.md` 后缀和上层目录，同时避免中文姓名异常长时撑爆路径。
_FILENAME_MAX_LEN = 80

# 需要从文件名里剔除的字符：Windows 保留字符 + 路径分隔符 + 全部控制字符。
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

# Windows 设备名：同名文件（无论有无扩展名）都创建不出来，必须改写。
_WINDOWS_DEVICE_NAMES = frozenset(
	{"con", "prn", "aux", "nul"}
	| {f"com{index}" for index in range(1, 10)}
	| {f"lpt{index}" for index in range(1, 10)}
)

# 段落顺序即 Markdown 呈现顺序，也是元数据 `sections` 的稳定顺序。
_SECTION_ORDER = (
	"basic",
	"expectation",
	"work_experience",
	"project_experience",
	"education",
	"competitive_analysis",
	"certifications",
)


class ResumeExportError(RuntimeError):
	"""简历导出失败（目录创建失败、写入失败、原子替换失败）。

	命令层捕获它并映射为 `EXPORT_FAILED` 错误信封。异常消息只允许出现
	路径和操作系统给出的错误原因，不得携带简历正文。
	"""


@dataclass(frozen=True)
class ResumeExportResult:
	"""一次导出的最小元数据。

	刻意不包含简历正文或原始响应：正文只存在于磁盘文件里，元数据才是
	可以安全进入 stdout 信封的部分。
	"""

	path: Path
	filename: str
	bytes_written: int
	candidate_name: str
	geek_id: str
	exported_at: str
	sections: list[str] = field(default_factory=list)


# ── 文件名安全 ──────────────────────────────────────────────────────


def safe_filename_stem(raw: str, *, fallback: str = "candidate") -> str:
	"""把任意不可信文本清洗成安全的文件名主干（不含扩展名）。

	处理顺序是有讲究的：先删危险字符，再压缩空白，最后剥首尾的点和空格 ——
	这样 ``../../etc/passwd`` 会先掉分隔符变成 ``....etcpasswd``，再被剥成
	``etcpasswd``，不会残留任何可用于路径穿越的片段。

	Parameters
	----------
	raw:
		原始文本，通常是平台返回的候选人姓名。
	fallback:
		清洗后为空时的兜底值。传空字符串表示「允许返回空」，由调用方决定
		如何拼接（例如退化成只用 geek_id 命名）。
	"""
	text = _UNSAFE_FILENAME_CHARS.sub("", str(raw or ""))
	# 换行、制表已在上一步作为控制字符删除，这里合并残余的多个空格。
	text = re.sub(r"\s+", " ", text).strip()
	# 首尾的点会造成隐藏文件（.name）或 Windows 上无法创建（name.）。
	text = text.strip(". ")
	if len(text) > _FILENAME_MAX_LEN:
		# 截断后可能又暴露出首尾的点，再剥一次。
		text = text[:_FILENAME_MAX_LEN].strip(". ")
	if text.lower() in _WINDOWS_DEVICE_NAMES:
		# 加下划线即可脱离设备名空间，同时保留可读性。
		text = f"{text}_"
	return text or fallback


def default_export_dir(data_dir: Path) -> Path:
	"""返回默认导出目录 ``<data_dir>/recruiter/resumes``（不创建）。"""
	return Path(data_dir).joinpath(*_EXPORT_SUBDIRS)


def resolve_export_path(
	*,
	data_dir: Path,
	geek_id: str,
	candidate_name: str,
	output: Path | None = None,
	output_dir: Path | None = None,
) -> Path:
	"""决定简历文件的落盘路径。

	优先级：``output``（用户完全指定）> ``output_dir``（用户指定目录，文件名
	自动生成）> ``data_dir`` 下的专属目录。文件名固定为
	``<姓名>-<geek_id>.md``，同一候选人重复下载会覆盖同一份快照，便于下游
	Agent 用确定路径找到文件。
	"""
	if output is not None:
		# 用户显式给出完整路径，尊重原样（含扩展名），只做 ~ 展开。
		return Path(output).expanduser()

	safe_geek_id = safe_filename_stem(geek_id, fallback="candidate")
	# fallback="" 表示姓名不可用时不硬造名字，退化成只用 geek_id 命名。
	safe_name = safe_filename_stem(candidate_name, fallback="")
	stem = f"{safe_name}-{safe_geek_id}" if safe_name else safe_geek_id

	directory = Path(output_dir).expanduser() if output_dir is not None else default_export_dir(data_dir)
	return directory / f"{stem}.md"


# ── Markdown 渲染 ───────────────────────────────────────────────────


def _cell(value: Any) -> str:
	"""把任意值渲染成安全的 Markdown 表格单元格内容。

	竖线会被解析成列分隔符，换行会直接截断表格行，两者都必须处理掉。
	"""
	text = "" if value is None else str(value)
	text = re.sub(r"\s*\n+\s*", " ", text).strip()
	return text.replace("|", "\\|") or "—"


def _block(value: Any) -> str:
	"""渲染多行正文（工作内容、项目描述等），保留段落但去掉首尾空白。"""
	text = "" if value is None else str(value)
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	return "\n".join(lines)


def _table(rows: list[tuple[str, Any]]) -> list[str]:
	"""渲染「字段 / 内容」两列表格。"""
	lines = ["| 字段 | 内容 |", "| --- | --- |"]
	lines.extend(f"| {label} | {_cell(value)} |" for label, value in rows)
	return lines


def _section(title: str, body: list[str]) -> list[str]:
	"""统一的段落包装：标题恒定出现，空段落写明「（无）」。

	段落标题不随数据缺失而消失，下游 Agent 才能用固定结构解析文件。
	"""
	return [f"## {title}", "", *(body or ["（无）"]), ""]


def _period(start: Any, end: Any, duration: Any) -> str:
	"""把起止时间和时长拼成一行可读文本。"""
	span = " - ".join(part for part in (_raw(start), _raw(end)) if part)
	length = _raw(duration)
	if span and length:
		return f"{span}（{length}）"
	return span or length


def _raw(value: Any) -> str:
	"""取纯文本值（不做表格转义），空值返回空串。"""
	return "" if value is None else str(value).strip()


def _bullets(pairs: list[tuple[str, Any]]) -> list[str]:
	"""渲染「- 标签: 值」列表，跳过空值。"""
	return [f"- {label}: {_raw(value)}" for label, value in pairs if _raw(value)]


def _paragraph(title: str, value: Any) -> list[str]:
	"""渲染带小标题的正文块，值为空时整块省略。"""
	body = _block(value)
	if not body:
		return []
	return ["", f"**{title}**", "", body]


def _render_work_experience(items: list[dict[str, Any]]) -> list[str]:
	lines: list[str] = []
	for index, item in enumerate(items, start=1):
		heading = " — ".join(part for part in (_raw(item.get("company")), _raw(item.get("position"))) if part)
		lines.append(f"### {index}. {heading or '未填写'}")
		lines.append("")
		keywords = item.get("keywords") or []
		lines.extend(_bullets([
			("时间", _period(item.get("start"), item.get("end"), item.get("duration"))),
			("部门", item.get("department")),
			("关键词", " / ".join(_raw(word) for word in keywords if _raw(word))),
		]))
		lines.extend(_paragraph("工作内容", item.get("responsibility")))
		lines.extend(_paragraph("工作业绩", item.get("performance")))
		lines.append("")
	return lines


def _render_project_experience(items: list[dict[str, Any]]) -> list[str]:
	lines: list[str] = []
	for index, item in enumerate(items, start=1):
		heading = " — ".join(part for part in (_raw(item.get("name")), _raw(item.get("role"))) if part)
		lines.append(f"### {index}. {heading or '未填写'}")
		lines.append("")
		lines.extend(_bullets([
			("时间", _period(item.get("start"), item.get("end"), item.get("duration"))),
		]))
		lines.extend(_paragraph("项目描述", item.get("description")))
		lines.extend(_paragraph("项目成果", item.get("achievement")))
		lines.append("")
	return lines


def _render_education(items: list[dict[str, Any]]) -> list[str]:
	lines: list[str] = []
	for item in items:
		parts = [_raw(item.get("school")), _raw(item.get("major")), _raw(item.get("degree"))]
		label = " · ".join(part for part in parts if part) or "未填写"
		period = _period(item.get("start"), item.get("end"), None)
		lines.append(f"- {label}（{period}）" if period else f"- {label}")
	return lines


def _as_dict(value: Any) -> dict[str, Any]:
	"""容错取字典；平台字段缺失或类型异常时退化成空字典。"""
	return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
	"""容错取列表。"""
	return value if isinstance(value, list) else []


def render_candidate_resume_markdown(
	resume: dict[str, Any],
	*,
	geek_id: str = "",
	exported_at: str | None = None,
) -> str:
	"""把结构化候选人简历渲染成 Markdown 文本。

	渲染是纯函数：不读环境、不写磁盘、不抛异常。任何字段缺失都退化成
	「（无）」而不是报错 —— 平台字段本身随版本漂移，导出不该因此失败。

	注意：`basic.avatar`（候选人头像地址）被有意排除 —— 它对下游文本分析
	没有价值，却是一份额外的肖像数据副本。
	"""
	data = _as_dict(resume)
	basic = _as_dict(data.get("basic"))
	expectation = _as_dict(data.get("expectation"))

	lines: list[str] = [
		f"# 候选人简历 — {_raw(basic.get('name')) or '未知候选人'}",
		"",
		*_bullets([
			("候选人 ID", geek_id),
			("导出时间", exported_at),
			("数据来源", "BOSS 直聘招聘者视角在线简历（boss hr download-resume）"),
		]),
		"",
	]

	lines.extend(_section("基本信息", _table([
		("姓名", basic.get("name")),
		("性别", basic.get("gender")),
		("年龄", basic.get("age")),
		("学历", basic.get("degree")),
		("工作年限", basic.get("work_years")),
		("活跃状态", basic.get("active_status")),
	]) if basic else []))

	lines.extend(_section("求职期望", _table([
		("期望职位", expectation.get("position")),
		("期望薪资", expectation.get("salary")),
		("期望城市", expectation.get("city")),
	]) if expectation else []))

	lines.extend(_section("工作经历", _render_work_experience(_as_list(data.get("work_experience")))))
	lines.extend(_section("项目经历", _render_project_experience(_as_list(data.get("project_experience")))))
	lines.extend(_section("教育经历", _render_education(_as_list(data.get("education")))))
	lines.extend(_section("证书", [f"- {_raw(item)}" for item in _as_list(data.get("certifications")) if _raw(item)]))
	lines.extend(_section(
		"平台竞争力提示",
		[f"- {_raw(item)}" for item in _as_list(data.get("competitive_analysis")) if _raw(item)],
	))

	# 合并连续空行，避免各段落拼接后出现大段留白。
	rendered = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
	return rendered.rstrip("\n") + "\n"


# ── 落盘 ────────────────────────────────────────────────────────────


def _discard(path: Path) -> None:
	"""尽力删除临时文件；清理本身失败不应掩盖原始错误。"""
	try:
		path.unlink(missing_ok=True)
	except OSError:
		pass


def _reason(exc: OSError) -> str:
	"""提取操作系统给出的失败原因，不含任何简历内容。"""
	return exc.strerror or str(exc)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
	"""原子写入：先写同目录临时文件，再 :func:`os.replace` 覆盖目标。

	同目录是必要条件 —— 跨盘/跨文件系统的 replace 不是原子操作。写入后
	fsync 落盘，避免掉电后留下空文件。
	"""
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		raise ResumeExportError(f"创建导出目录失败: {path.parent} ({_reason(exc)})") from exc

	# 带 pid 后缀，避免并发导出同一候选人时两个进程互相覆盖临时文件。
	tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
	try:
		with open(tmp_path, "wb") as handle:
			handle.write(payload)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(tmp_path, path)
	except OSError as exc:
		_discard(tmp_path)
		raise ResumeExportError(f"写入简历文件失败: {path} ({_reason(exc)})") from exc


def _present_sections(resume: dict[str, Any]) -> list[str]:
	"""列出实际有内容的段落，作为下游分析的「这份文件里有什么」提示。"""
	data = _as_dict(resume)
	return [name for name in _SECTION_ORDER if data.get(name)]


def export_candidate_resume(
	resume: dict[str, Any],
	*,
	geek_id: str,
	data_dir: Path,
	output: Path | None = None,
	output_dir: Path | None = None,
	exported_at: str | None = None,
) -> ResumeExportResult:
	"""把一份结构化候选人简历导出为本地 Markdown 文件。

	Parameters
	----------
	resume:
		:func:`parse_resume` 的输出（结构化字典）。
	geek_id:
		候选人 ID，参与文件命名，并写进返回的元数据。
	data_dir:
		CLI 的数据目录；未显式指定输出位置时用它推导默认导出目录。
	output:
		完整文件路径。与 ``output_dir`` 互斥（由命令层校验）。
	output_dir:
		导出目录，文件名自动生成。
	exported_at:
		导出时间戳（ISO 8601）。留空取当前时间；测试可注入固定值。

	Returns
	-------
	ResumeExportResult
		导出路径与最小元数据。

	Raises
	------
	ResumeExportError
		目录创建、写入或原子替换失败。
	"""
	basic = _as_dict(_as_dict(resume).get("basic"))
	candidate_name = _raw(basic.get("name"))
	timestamp = exported_at or datetime.now().isoformat(timespec="seconds")

	markdown = render_candidate_resume_markdown(resume, geek_id=geek_id, exported_at=timestamp)
	# 显式编码后按二进制写入：跨平台字节数一致，也不会被换行符转换改写内容。
	payload = markdown.encode("utf-8")

	path = resolve_export_path(
		data_dir=data_dir,
		geek_id=geek_id,
		candidate_name=candidate_name,
		output=output,
		output_dir=output_dir,
	)
	_atomic_write_bytes(path, payload)

	return ResumeExportResult(
		path=path,
		filename=path.name,
		bytes_written=len(payload),
		candidate_name=candidate_name,
		geek_id=geek_id,
		exported_at=timestamp,
		sections=_present_sections(resume),
	)
