"""本地简历文本的受控读取器。

读取器只负责把用户明确选择的 PDF、Markdown 或 TXT 简历短暂转换为内存文本，
不负责保存候选人、评分或输出日志。将格式解析隔离在此处，确保导入与复评遵循
同一扩展名、大小和错误边界，也避免简历正文意外进入工作区 JSON。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from boss_agent_cli.recruiting.unicode_safety import sanitize_unicode_text


SUPPORTED_RESUME_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf"})
MAX_RESUME_BYTES = 4 * 1024 * 1024
MAX_OCR_PAGES = 8
MIN_MEANINGFUL_CHARACTERS = 30


class ResumeTextReadError(ValueError):
	"""简历无法以受支持格式安全读取时抛出的领域错误。"""


def read_resume_text(path: Path) -> str:
	"""读取受支持简历正文，返回值仅供当前导入或评估调用链使用。

	PDF 优先提取可检索文字层。对于扫描件、特殊字体编码或只有重复防复制水印的
	文件，使用本地 OCR 回退。两条路径都得不到有效正文时抛出领域错误，禁止上层
	把乱码或空内容交给评分模型。
	"""
	resolved = path.expanduser().resolve()
	if resolved.suffix.lower() not in SUPPORTED_RESUME_SUFFIXES:
		raise ResumeTextReadError("候选人简历只支持 PDF、Markdown 或文本文件")
	try:
		if resolved.stat().st_size > MAX_RESUME_BYTES:
			raise ResumeTextReadError("候选人简历文件过大，最大支持 4 MB")
		if resolved.suffix.lower() != ".pdf":
			# 外部附件可能包含非法 UTF-8 字节；先替换解码错误，再清理 Python
			# 字符串中可能存在的孤立代理字符，确保下游 AI JSON 一定可编码。
			return sanitize_unicode_text(resolved.read_text(encoding="utf-8", errors="replace"))
		reader = PdfReader(str(resolved))
		text = sanitize_unicode_text("\n".join(page.extract_text() or "" for page in reader.pages).strip())
		if _is_meaningful_resume_text(text):
			return text
		ocr_text = sanitize_unicode_text(_read_pdf_text_with_ocr(resolved))
		if _is_meaningful_resume_text(ocr_text):
			return ocr_text
		raise ResumeTextReadError("候选人简历 PDF 无法提取有效正文")
	except ResumeTextReadError:
		raise
	except (OSError, PdfReadError, PdfStreamError, ValueError) as exc:
		raise ResumeTextReadError("候选人简历文件无法读取") from exc


def _is_meaningful_resume_text(text: str) -> bool:
	"""判断提取结果是否足以进入自动评分。

	BOSS 生成的部分 PDF 会把防复制水印暴露为唯一文字层。此类内容长度看似正常，
	但每行完全相同；仅判断非空会把水印误当作候选人经历。这里同时检查有效字符
	数量和重复行占比，保留正常的中英文简历，又能稳定识别已知异常格式。
	"""
	lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
	compact = "".join(lines)
	meaningful_characters = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", compact)
	if len(meaningful_characters) < MIN_MEANINGFUL_CHARACTERS:
		return False
	if len(lines) >= 3:
		most_common_count = Counter(lines).most_common(1)[0][1]
		if most_common_count / len(lines) >= 0.6:
			return False
	return True


def _read_pdf_text_with_ocr(path: Path) -> str:
	"""在内存中渲染 PDF 页面并使用本地 RapidOCR 提取正文。

	OCR 依赖采用延迟导入，普通文本型 PDF 不承担模型加载成本。页数限制避免异常
	附件长期占用 CPU 和内存；OCR 结果只返回当前评分调用链，不额外保存文本副本。
	"""
	try:
		import pypdfium2 as pdfium
		from rapidocr_onnxruntime import RapidOCR

		document = pdfium.PdfDocument(str(path))
		ocr_engine = RapidOCR()
		chunks: list[str] = []
		for page_index in range(min(len(document), MAX_OCR_PAGES)):
			page = document[page_index]
			image = page.render(scale=1.7).to_pil()
			result, _ = ocr_engine(image)
			chunks.extend(str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip())
		return "\n".join(chunks).strip()
	except ResumeTextReadError:
		raise
	except Exception as exc:
		raise ResumeTextReadError("候选人简历 PDF 本地 OCR 失败") from exc
