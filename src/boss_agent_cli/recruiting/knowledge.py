"""岗位知识文件的受控解析能力。

这个模块只负责把用户明确选择的本地文件转换成纯文本和来源元数据，
不负责写入工作区，也不执行任何外部动作。把文件读取与 Store 分开，
可以在 Web、CLI 和测试中复用同一套扩展名、大小和编码边界。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

_MAX_KNOWLEDGE_FILE_BYTES = 4 * 1024 * 1024
_MAX_KNOWLEDGE_CONTENT_CHARS = 20_000
_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
_DOCX_SUFFIX = ".docx"
_WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class ParsedKnowledgeFile:
	"""解析后的知识文件快照，供工作区写入岗位知识记录。"""

	title: str
	content: str
	source_type: str
	source_path: str
	source_sha256: str


def generate_faq_drafts(documents: Iterable[object], *, limit: int = 20) -> list[dict[str, str]]:
	"""从已保存的知识事实生成待审核 FAQ 草稿。

	草稿生成是确定性的本地转换：每个非标题事实句只生成一个问题/答案，
	并携带文档 ID、标题和内容哈希作为来源版本。函数刻意不接收 Store，
	因此不会写入 FAQ，也不会绕过 HR 审核或触发任何平台动作。
	"""
	if limit < 1:
		return []
	items: list[dict[str, str]] = []
	for document in documents:
		raw = document.to_dict() if hasattr(document, "to_dict") else document
		if not isinstance(raw, dict):
			continue
		document_id = str(raw.get("document_id") or "")
		title = str(raw.get("title") or "未命名知识")
		content = str(raw.get("content") or "")
		version = str(raw.get("source_sha256") or raw.get("updated_at") or "unknown")
		if not document_id or not content.strip():
			continue
		for sentence in re.split(r"[。！？!?；;\n]+", content):
			fact = sentence.strip(" -\t")
			if not fact or fact.startswith("#") or len(fact) < 2:
				continue
			items.append(
				{
					"draft_id": f"faq-draft-{hashlib.sha256(f'{document_id}|{version}|{fact}'.encode('utf-8')).hexdigest()[:16]}",
					"question": f"关于{title}，候选人可能会问：{fact}？",
					"answer": fact,
					"status": "pending_review",
					"source_document_id": document_id,
					"source_title": title,
					"source_version": version,
				}
			)
			if len(items) >= limit:
				return items
	return items


def _normalise_text(text: str) -> str:
	"""统一换行和水平空白，保留段落边界便于后续生成问题。"""
	lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
	result: list[str] = []
	previous_blank = False
	for line in lines:
		if not line:
			if not previous_blank:
				result.append("")
			previous_blank = True
		else:
			result.append(line)
			previous_blank = False
	return "\n".join(result).strip()


def _extract_docx_xml(xml_bytes: bytes) -> str:
	"""解析固定正文 XML，并把每个 Word 段落转换成一行。"""
	try:
		root = ElementTree.fromstring(xml_bytes)
	except ElementTree.ParseError as exc:
		raise ValueError("知识文件正文格式无效") from exc
	paragraphs: list[str] = []
	for paragraph in root.iter():
		if paragraph.tag.rsplit("}", 1)[-1] != "p":
			continue
		parts = [node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] == "t"]
		text = _normalise_text("".join(parts))
		if text:
			paragraphs.append(text)
	return "\n".join(paragraphs).strip()


def _read_docx_path(path: Path) -> str:
	"""读取 docx 固定正文入口，限制解压后正文长度。"""
	try:
		with ZipFile(path, "r") as archive:
			info = archive.getinfo("word/document.xml")
			if info.file_size > _MAX_KNOWLEDGE_CONTENT_CHARS * 8:
				raise ValueError("知识文件正文过长，最多支持 20000 个字符")
			xml_bytes = archive.read(info)
	except (BadZipFile, KeyError, OSError) as exc:
		raise ValueError("知识文件无法读取") from exc
	return _extract_docx_xml(xml_bytes)


def parse_knowledge_file(path: Path | str) -> ParsedKnowledgeFile:
	"""解析用户选择的知识文件并返回可追溯的纯文本快照。

	路径会在读取前解析为绝对路径，但不会自动遍历目录或跟随 URL；扩展名、
	文件大小和正文长度均在写入 Store 前验证，失败时不会产生半条记录。
	"""
	resolved = Path(path).expanduser().resolve()
	suffix = resolved.suffix.casefold()
	if suffix not in _TEXT_SUFFIXES and suffix != _DOCX_SUFFIX:
		raise ValueError("知识文件只支持 Markdown、文本或 docx")
	try:
		if resolved.stat().st_size > _MAX_KNOWLEDGE_FILE_BYTES:
			raise ValueError("知识文件过大，最大支持 4 MB")
		if suffix == _DOCX_SUFFIX:
			content = _read_docx_path(resolved)
		else:
			content = resolved.read_text(encoding="utf-8")
	except UnicodeDecodeError as exc:
		raise ValueError("知识文件必须使用 UTF-8 编码") from exc
	except OSError as exc:
		raise ValueError("知识文件无法读取") from exc
	content = _normalise_text(content)
	if not content:
		raise ValueError("知识文件正文不能为空")
	if len(content) > _MAX_KNOWLEDGE_CONTENT_CHARS:
		raise ValueError("知识文件正文过长，最多支持 20000 个字符")
	source_type = "docx" if suffix == _DOCX_SUFFIX else "markdown" if suffix in {".md", ".markdown"} else "text"
	return ParsedKnowledgeFile(
		title=resolved.stem.strip() or "未命名知识",
		content=content,
		source_type=source_type,
		source_path=str(resolved),
		source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
	)


__all__ = ["ParsedKnowledgeFile", "parse_knowledge_file"]
