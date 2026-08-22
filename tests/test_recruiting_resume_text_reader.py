"""本地候选人简历文本读取边界的测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_read_resume_text_reads_markdown(tmp_path: Path) -> None:
	"""Markdown 简历应以 UTF-8 读取，供导入和评估共用同一文本来源。"""
	from boss_agent_cli.recruiting.resume_text_reader import read_resume_text

	path = tmp_path / "candidate.md"
	path.write_text("Java Spring Boot", encoding="utf-8")

	assert read_resume_text(path) == "Java Spring Boot"


def test_read_resume_text_replaces_unpaired_surrogate_from_external_parser(tmp_path: Path) -> None:
	"""简历提取结果必须可安全进入 UTF-8 状态文件和 AI 请求。"""
	from boss_agent_cli.recruiting.resume_text_reader import read_resume_text

	path = tmp_path / "candidate.md"
	path.write_bytes("Java 项目".encode("utf-8") + b"\xed\xa0\xbd")

	result = read_resume_text(path)

	assert result == "Java 项目\ufffd\ufffd\ufffd"
	assert result.encode("utf-8")


def test_read_resume_text_rejects_unsupported_file(tmp_path: Path) -> None:
	"""读取器拒绝非简历格式，避免把任意二进制附件送入分析链路。"""
	from boss_agent_cli.recruiting.resume_text_reader import ResumeTextReadError, read_resume_text

	path = tmp_path / "candidate.docx"
	path.write_bytes(b"binary")

	with pytest.raises(ResumeTextReadError, match="只支持"):
		read_resume_text(path)


def test_read_resume_text_converts_invalid_pdf_to_domain_error(tmp_path: Path) -> None:
	"""损坏 PDF 必须成为可隔离的读取错误，不能中断同批其他候选人评估。"""
	from boss_agent_cli.recruiting.resume_text_reader import ResumeTextReadError, read_resume_text

	path = tmp_path / "broken.pdf"
	path.write_bytes(b"not a PDF")

	with pytest.raises(ResumeTextReadError, match="无法读取"):
		read_resume_text(path)


def test_read_resume_text_uses_ocr_when_pdf_text_is_repeated_watermark(tmp_path: Path) -> None:
	"""特殊字体 PDF 只提取出重复水印时，必须改用本地 OCR 获取真实正文。"""
	from boss_agent_cli.recruiting.resume_text_reader import read_resume_text

	path = tmp_path / "encoded.pdf"
	path.write_bytes(b"%PDF-1.4 placeholder")
	watermark = "c68f9b5e36d583f61HN839u0FFBTw4q4Vv6YWOOmmfHRNBJn3Q~~"
	page = MagicMock()
	page.extract_text.return_value = "\n".join([watermark] * 8)
	ocr_text = "梁文锦 本科 软件工程 Java Spring Boot Redis MySQL 项目经历 实习经历"

	with (
		patch("boss_agent_cli.recruiting.resume_text_reader.PdfReader", return_value=MagicMock(pages=[page])),
		patch("boss_agent_cli.recruiting.resume_text_reader._read_pdf_text_with_ocr", return_value=ocr_text) as ocr,
	):
		result = read_resume_text(path)

	assert result == ocr_text
	ocr.assert_called_once_with(path.resolve())


def test_read_resume_text_rejects_pdf_when_text_layer_and_ocr_are_not_meaningful(tmp_path: Path) -> None:
	"""文字层和 OCR 都无有效正文时应转人工复核，不能把乱码送给模型评分。"""
	from boss_agent_cli.recruiting.resume_text_reader import ResumeTextReadError, read_resume_text

	path = tmp_path / "unreadable.pdf"
	path.write_bytes(b"%PDF-1.4 placeholder")
	watermark = "opaque-watermark-token"
	page = MagicMock()
	page.extract_text.return_value = "\n".join([watermark] * 8)

	with (
		patch("boss_agent_cli.recruiting.resume_text_reader.PdfReader", return_value=MagicMock(pages=[page])),
		patch(
			"boss_agent_cli.recruiting.resume_text_reader._read_pdf_text_with_ocr",
			return_value="\n".join([watermark] * 8),
		),
		pytest.raises(ResumeTextReadError, match="无法提取有效正文"),
	):
		read_resume_text(path)
