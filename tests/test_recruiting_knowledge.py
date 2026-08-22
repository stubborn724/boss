"""招聘知识文件导入的安全边界和来源追踪测试。"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from boss_agent_cli.recruiting.knowledge import generate_faq_drafts
from boss_agent_cli.recruiting.workspace import RecruitingWorkspace


def _make_minimal_docx(path: Path, text: str) -> Path:
	"""创建只含正文 XML 的最小 docx，避免测试依赖 Office 应用。"""
	with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
		archive.writestr(
			"word/document.xml",
			"<?xml version='1.0' encoding='UTF-8'?>"
			"<document xmlns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
			f"<body><p><r><t>{text}</t></r></p></body></document>",
		)
	return path


def test_imports_supported_files_and_keeps_source_metadata(tmp_path: Path) -> None:
	"""Markdown、文本和 docx 都应导入，并能追溯到本地来源。"""
	markdown = tmp_path / "销售流程.md"
	markdown.write_text("# 销售流程\n\n先做需求诊断。", encoding="utf-8")
	text = tmp_path / "福利.txt"
	text.write_text("双休，入职缴纳社保。", encoding="utf-8")
	docx = _make_minimal_docx(tmp_path / "产品资料.docx", "产品支持试用期演示。")

	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]

	rows = [
		workspace.import_knowledge(job_id, markdown, category="sales"),
		workspace.import_knowledge(job_id, text, category="company"),
		workspace.import_knowledge(job_id, docx, category="company"),
	]

	assert {row["source_type"] for row in rows} == {"markdown", "text", "docx"}
	assert all(row["source_path"] == str(path.resolve()) for row, path in zip(rows, (markdown, text, docx)))
	assert all(len(str(row["source_sha256"])) == 64 for row in rows)
	assert rows[2]["content"] == "产品支持试用期演示。"


def test_import_rejects_unsupported_or_oversized_files(tmp_path: Path) -> None:
	"""导入边界应在写入前拒绝未知扩展名和超过限制的文件。"""
	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]
	unsupported = tmp_path / "资料.pdf"
	unsupported.write_bytes(b"not supported")
	oversized = tmp_path / "资料.txt"
	oversized.write_text("x" * (4 * 1024 * 1024 + 1), encoding="utf-8")

	with pytest.raises(ValueError, match="知识文件只支持"):
		workspace.import_knowledge(job_id, unsupported, category="company")
	with pytest.raises(ValueError, match="文件过大"):
		workspace.import_knowledge(job_id, oversized, category="company")
	assert workspace.snapshot(job_id)["knowledge"] == []


def test_knowledge_faq_drafts_are_source_traced_and_not_auto_saved(tmp_path: Path) -> None:
	"""知识事实只能生成待审核草稿，不能在生成阶段偷偷写入 FAQ。"""
	markdown = tmp_path / "销售流程.md"
	markdown.write_text("# 销售流程\n\n先做需求诊断。\n工作时间是 9:00-18:00。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]
	document = workspace.import_knowledge(job_id, markdown, category="sales")

	drafts = generate_faq_drafts([document])
	assert drafts
	assert all(item["status"] == "pending_review" for item in drafts)
	assert all(item["source_document_id"] == document["document_id"] for item in drafts)
	assert all(item["source_title"] == "销售流程" for item in drafts)
	assert all(item["source_version"] == document["source_sha256"] for item in drafts)
	assert workspace.snapshot(job_id)["faq"] == []


def test_workspace_approves_faq_draft_with_provenance(tmp_path: Path) -> None:
	"""HR 审核草稿后，FAQ 才入库并保留来源版本，便于后续复核。"""
	markdown = tmp_path / "福利.md"
	markdown.write_text("双休，入职缴纳社保。", encoding="utf-8")
	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]
	document = workspace.import_knowledge(job_id, markdown, category="company")
	draft = workspace.generate_faq_drafts(job_id)[0]

	faq = workspace.add_faq(
		job_id,
		question=draft["question"],
		answer=draft["answer"],
		allowed_variation="保持原意即可",
		source_document_id=draft["source_document_id"],
		source_title=draft["source_title"],
		source_version=draft["source_version"],
	)
	assert faq["source_document_id"] == document["document_id"]
	assert faq["source_version"] == document["source_sha256"]
	assert faq["review_status"] == "approved"
	assert workspace.snapshot(job_id)["faq"][0]["faq_id"] == faq["faq_id"]


def test_candidate_answers_exclude_internal_sales_knowledge(tmp_path: Path) -> None:
	"""候选人试答只能使用对外知识，不能把销售内部资料当成事实回答。"""
	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]
	workspace.add_knowledge(
		job_id,
		category="sales",
		title="内部异议处理手册",
		content="内部培训底价和客户分层规则不对外公开。",
		audience="internal",
	)
	workspace.add_knowledge(
		job_id,
		category="company",
		title="工作时间说明",
		content="工作时间为周一至周六。",
		audience="candidate",
	)

	internal_answer = workspace.answer_question(job_id, "底价和客户分层规则是什么？")
	public_answer = workspace.answer_question(job_id, "工作时间是什么？")

	assert internal_answer["status"] == "no_source"
	assert public_answer["status"] == "answered"
	assert public_answer["answer"] == "工作时间为周一至周六。"


def test_legacy_knowledge_audience_is_derived_from_category(tmp_path: Path) -> None:
	"""旧版知识记录缺少 audience 时，销售资料默认内部、企业资料默认可对外。"""
	workspace = RecruitingWorkspace(tmp_path)
	job_id = workspace.create_job(name="销售顾问", status="published")["job"]["job_id"]
	sales = workspace.add_knowledge(job_id, category="sales", title="销售流程", content="先做需求诊断")
	company = workspace.add_knowledge(job_id, category="company", title="休息制度", content="周日休息")

	assert sales["audience"] == "internal"
	assert company["audience"] == "candidate"
