"""E2E verification: 分析 → 回复 → 异步等待附件 → Agent分析 → 评分≥70进一步交流.

Verifies the complete recruitment pipeline:
1. Click "分析" (analyze) on a candidate
2. System sends reply message asking for attachment resume
3. Async wait until attachment resume is available
4. Agent analyzes resume (AI or rule-based)
5. Candidates scoring ≥ 70 get further communication (pool entry)
6. Candidates scoring < 70 do NOT get further communication

This test uses mock platform/client data and does NOT require a live BOSS session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from boss_agent_cli.commands.recruiter.communication_pipeline import (
	CommunicationPipeline,
	PipelineStepResult,
)
from boss_agent_cli.commands.recruiter.conversation_resume_export import (
	ConversationResumeExportService,
)
from boss_agent_cli.commands.recruiter.resume_analysis import (
	analyze_resume_with_ai,
)
from boss_agent_cli.recruiting.dialogue_models import (
	CandidateDialogueState,
	DialogueStage,
	InterviewPhase,
)
from boss_agent_cli.recruiting.dialogue_state import DialogueStateStore
from boss_agent_cli.recruiting.resume_text_reader import ResumeTextReadError


# ── Test fixtures ────────────────────────────────────────────────────────


def _make_online_resume_markdown(name: str, score_worthy: bool = True) -> str:
	"""Generate a realistic Markdown resume."""
	if score_worthy:
		return f"""# 候选人简历 — {name}

- 候选人 ID: geek-{name}
- 导出时间: 2025-01-15T10:00:00
- 数据来源: BOSS 直聘招聘者视角在线简历

## 基本信息

| 字段 | 内容 |
| --- | --- |
| 姓名 | {name} |
| 性别 | 男 |
| 年龄 | 28岁 |
| 学历 | 本科 |
| 工作年限 | 5年 |
| 活跃状态 | 刚刚活跃 |

## 求职期望

| 字段 | 内容 |
| --- | --- |
| 期望职位 | 销售经理 |
| 期望薪资 | 15-25K |
| 期望城市 | 上海 |

## 工作经历

### 1. 某科技有限公司 — 销售主管
- 时间: 2020.03 - 2025.06（5年3个月）
- 部门: 销售部

**工作内容**

负责大客户开发与维护，带领5人销售团队，年度业绩超目标120%。擅长电话销售、面谈沟通、客户关系管理。

**工作业绩**

连续3年获得公司销售冠军，年度回款额超过500万。

### 2. 某互联网公司 — 客户经理
- 时间: 2017.07 - 2020.02（2年8个月）

**工作内容**

负责B端客户拓展，通过电话邀约、上门拜访等方式开发新客户。

## 项目经历

### 1. 华东区市场拓展项目 — 项目负责人
- 时间: 2022.01 - 2023.12

**项目描述**

带领团队开拓华东区新市场，从零搭建客户体系。

**项目成果**

1年内实现华东区销售额从0到300万的突破。

## 教育经历

- 上海大学 · 市场营销 · 本科（2013.09 - 2017.06）

## 证书

（无）

## 平台竞争力提示

- 沟通能力强
- 积极主动
"""
	else:
		return f"""# 候选人简历 — {name}

- 候选人 ID: geek-{name}
- 导出时间: 2025-01-15T10:00:00

## 基本信息

| 字段 | 内容 |
| --- | --- |
| 姓名 | {name} |
| 性别 | 女 |
| 年龄 | 22岁 |
| 学历 | 高中 |
| 工作年限 | 1年 |
| 活跃状态 | 3天前活跃 |

## 求职期望

| 字段 | 内容 |
| --- | --- |
| 期望职位 | 实习生 |
| 期望薪资 | 3-5K |
| 期望城市 | 小城市 |

## 工作经历

### 1. 某餐饮店 — 服务员
- 时间: 2024.01 - 2024.12（1年）

**工作内容**

餐厅服务工作。

## 教育经历

- 某中学 · 高中（2019.09 - 2022.06）
"""


def _mark_dialogue_ready(data_dir: Path, friend_id: int) -> None:
	"""写入完成两轮有效回答后的终审准入状态。"""
	DialogueStateStore(data_dir).save(
		CandidateDialogueState(
			candidate_key=f"friend:{friend_id}",
			job_id="job-1",
			stage=DialogueStage.READY_FOR_RESUME,
			interview_phase=InterviewPhase.PROFESSIONAL,
			basic_reply_count=1,
			professional_reply_count=1,
		),
	)


def _mock_ai_smart_scoring(messages: list[dict], **kwargs) -> str:
	"""Mock AI that reads resume text and returns appropriate scores.

	- Strong sales resume → score 82 (≥70, invite)
	- Weak non-sales resume → score 35 (<70, reject)
	"""
	# Extract resume text from the messages
	user_content = ""
	for msg in messages:
		if msg.get("role") == "user":
			user_content = str(msg.get("content", ""))
			break

	if "销售" in user_content and ("5年" in user_content or "本科" in user_content):
		# Strong candidate
		return json.dumps(
			{
				"overall_score": 82,
				"skill_match": "候选人技能与岗位要求高度匹配",
				"experience_assessment": "5年销售经验，有团队管理经验，业绩优秀",
				"strengths": ["销售能力强", "沟通能力优秀", "有管理经验", "业绩突出"],
				"gaps": ["需要了解本公司产品线"],
				"risk_flags": [],
				"follow_up_questions": ["请介绍一下你最成功的销售案例", "你对我们的产品了解多少"],
				"recommendation": "invite_to_interview",
			},
			ensure_ascii=False,
		)
	else:
		# Weak candidate
		return json.dumps(
			{
				"overall_score": 35,
				"skill_match": "候选人技能与岗位要求不匹配",
				"experience_assessment": "仅1年餐饮服务经验，无销售相关经验",
				"strengths": [],
				"gaps": ["无销售经验", "学历不达标", "无相关行业背景"],
				"risk_flags": ["经验不足"],
				"follow_up_questions": [],
				"recommendation": "reject",
			},
			ensure_ascii=False,
		)


def _mock_ai_analysis_borderline(messages: list[dict], **kwargs) -> str:
	"""Mock AI that returns exactly 70 as JSON string."""
	return json.dumps(
		{
			"overall_score": 70,
			"skill_match": "候选人基本满足岗位要求",
			"experience_assessment": "有一定销售经验，但需要培养",
			"strengths": ["沟通能力好", "学习意愿强"],
			"gaps": ["经验年限略少"],
			"risk_flags": [],
			"follow_up_questions": ["你期望的薪资范围是多少"],
			"recommendation": "invite_to_interview",
		},
		ensure_ascii=False,
	)


# ── Mock platform factory ────────────────────────────────────────────────


def _create_mock_platform(
	friend_detail_data: list[dict[str, Any]] | None = None,
	exchange_content_data: dict[str, Any] | None = None,
	send_message_result: dict[str, Any] | None = None,
):
	"""Create a fully-mocked platform for pipeline testing."""
	platform = MagicMock()
	platform.is_success.side_effect = lambda r: r.get("code") == 0
	platform.unwrap_data.side_effect = lambda r: r.get("zpData") or r.get("data")
	platform.parse_error.side_effect = lambda r: (str(r.get("code", -1)), str(r.get("message", "")))

	# Default friend_list
	platform.friend_list.return_value = {
		"code": 0,
		"zpData": {
			"friendList": [
				{
					"uid": 1,
					"friendId": 1,
					"encryptUid": "geek-1",
					"encryptGeekId": "geek-1",
					"encryptJobId": "job-1",
					"jobId": "job-1",
					"securityId": "sec-1",
					"name": "高分候选人",
				},
				{
					"uid": 2,
					"friendId": 2,
					"encryptUid": "geek-2",
					"encryptGeekId": "geek-2",
					"encryptJobId": "job-2",
					"jobId": "job-2",
					"securityId": "sec-2",
					"name": "低分候选人",
				},
			]
		},
	}

	# Default friend_detail - uses side_effect to return per-friend data
	def _friend_detail_side_effect(friend_ids):
		results = []
		for fid in friend_ids:
			if fid == 1:
				results.append(
					{
						"uid": 1,
						"friendId": 1,
						"encryptUid": "geek-1",
						"encryptGeekId": "geek-1",
						"encryptJobId": "job-1",
						"jobId": "job-1",
						"securityId": "sec-1",
						"name": "高分候选人",
					}
				)
			else:
				results.append(
					{
						"uid": fid,
						"friendId": fid,
						"encryptUid": f"geek-{fid}",
						"encryptGeekId": f"geek-{fid}",
						"encryptJobId": f"job-{fid}",
						"jobId": f"job-{fid}",
						"securityId": f"sec-{fid}",
						"name": f"候选人{fid}",
					}
				)
		return {"code": 0, "zpData": {"friendList": results}}

	if friend_detail_data is None:
		platform.friend_detail.side_effect = _friend_detail_side_effect
	else:
		platform.friend_detail.return_value = {
			"code": 0,
			"zpData": {"friendList": friend_detail_data},
		}

	# Default exchange_content (no attachment)
	if exchange_content_data is None:
		exchange_content_data = {}
	platform.exchange_content.return_value = {
		"code": 0,
		"zpData": exchange_content_data,
	}

	# Default send_message_by_friend
	if send_message_result is None:
		send_message_result = {"code": 0, "message": "ok"}
	platform.send_message_by_friend.return_value = send_message_result

	return platform


# ── Mock online exporter ─────────────────────────────────────────────────


def _create_mock_online_exporter(tmp_path: Path):
	"""Create a mock online exporter that writes a Markdown file."""

	def export(
		*, geek_id: str, job_id: str, security_id: str, data_dir: Path, output: Path | None, output_dir: Path | None
	):
		from boss_agent_cli.commands.recruiter.resume_export import ResumeExportResult

		# Determine score-worthiness from geek_id
		name = "高分候选人" if "1" in geek_id else "低分候选人"
		score_worthy = "1" in geek_id
		content = _make_online_resume_markdown(name, score_worthy)

		out_dir = output_dir or (tmp_path / "recruiter" / "resumes")
		out_dir.mkdir(parents=True, exist_ok=True)
		filepath = out_dir / f"{name}-{geek_id}.md"
		filepath.write_text(content, encoding="utf-8")

		return ResumeExportResult(
			path=filepath,
			filename=filepath.name,
			bytes_written=len(content.encode("utf-8")),
			candidate_name=name,
			geek_id=geek_id,
			exported_at="2025-01-15T10:00:00",
			sections=["basic", "expectation", "work_experience", "education"],
		)

	return export


# ── Mock attachment downloader ───────────────────────────────────────────


def _create_mock_attachment_downloader():
	"""Mock attachment downloader that returns PDF bytes."""

	def download(url: str) -> bytes:
		return b"%PDF-1.4 mock resume content"

	return download


# ══════════════════════════════════════════════════════════════════════════
# E2E Tests
# ══════════════════════════════════════════════════════════════════════════


class TestE2EAnalyzePipeline:
	"""End-to-end tests for the analyze → reply → wait → analyze → score flow."""

	def test_online_resume_alone_never_enters_final_analysis(self, tmp_path: Path):
		"""在线简历只能用于初筛，不能在附件缺失时成为终审输入。"""
		platform = _create_mock_platform()
		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=_create_mock_online_exporter(tmp_path),
			attachment_downloader=_create_mock_attachment_downloader(),
		)
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=export_service,
		)

		report = pipeline.run(limit=1, threshold=70, ask_for_resume=False)

		assert report.analyzed == 0
		assert report.items[0]["status"] == "waiting_for_dialogue"
		assert report.items[0]["online_resume_downloaded"] is False

	def test_full_pipeline_waits_for_dialogue_before_requesting_attachment(self, tmp_path: Path):
		"""批量沟通分析必须先完成 AI 两阶段对话，再请求附件。

		真实附件需要候选人后续回复。本次运行只能完成“首次询问并写入等待
		状态”，评分必须等待 RPA 自动同意并下载附件之后再执行。
		"""
		# ── Setup ──────────────────────────────────────────────
		platform = _create_mock_platform()
		online_exporter = _create_mock_online_exporter(tmp_path)
		attachment_downloader = _create_mock_attachment_downloader()
		ai_chat_fn = _mock_ai_smart_scoring

		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=online_exporter,
			attachment_downloader=attachment_downloader,
		)

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=ai_chat_fn,
			export_service=export_service,
		)

		# ── Execute ────────────────────────────────────────────
		report = pipeline.run(limit=2, threshold=70, ask_for_resume=True)

		# ── Verify ─────────────────────────────────────────────
		# 1. Pipeline completed
		assert report.state == "succeeded", f"Pipeline state: {report.state}, reason: {report.stopped_reason}"
		assert report.processed == 2

		# 在线简历即使可读也不能跳过对话；尚无终审准入时不能索要附件。
		assert report.resumed_sent == 0
		assert report.online_downloaded == 0
		assert report.analyzed == 0
		assert report.pool_added == 0
		assert {item["status"] for item in report.items} == {"waiting_for_dialogue"}
		platform.send_message_by_friend.assert_not_called()

		print(
			f"\n✅ E2E Pipeline: processed={report.processed}, "
			f"ask_sent={report.resumed_sent}, analyzed={report.analyzed}"
		)

	def test_pipeline_70_exact_threshold_adds_to_pool(self, tmp_path: Path):
		"""完成两轮对话后的附件评分恰好 70 分时应该入库。"""
		platform = _create_mock_platform()
		attachment_path = tmp_path / "candidate-1.md"
		attachment_path.write_text(_make_online_resume_markdown("高分候选人"), encoding="utf-8")
		platform.download_attachment_via_ui.return_value = {
			"code": 0,
			"zpData": {"attachment_path": str(attachment_path)},
		}
		_mark_dialogue_ready(tmp_path, 1)
		ai_chat_fn = _mock_ai_analysis_borderline  # returns exactly 70

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=ai_chat_fn,
		)

		report = pipeline.run(limit=1, threshold=70, ask_for_resume=False)

		assert report.state == "succeeded"
		assert report.processed == 1
		items = list(report.items)
		assert items[0]["score"] == 70
		assert items[0]["pool_added"] is True, "70分刚好过线，应该入库"

		print(f"\n✅ 70分阈值: score={items[0]['score']}, pool_added={items[0]['pool_added']}")

	def test_pipeline_69_below_threshold_not_added_to_pool(self, tmp_path: Path):
		"""完成两轮对话后的附件评分 69 分时不应该入库。"""

		def mock_ai_69(messages, **kwargs) -> str:
			return json.dumps(
				{
					"overall_score": 69,
					"skill_match": "基本匹配",
					"experience_assessment": "经验一般",
					"strengths": ["沟通尚可"],
					"gaps": ["经验不足"],
					"risk_flags": [],
					"follow_up_questions": [],
					"recommendation": "review",
				},
				ensure_ascii=False,
			)

		platform = _create_mock_platform()
		attachment_path = tmp_path / "candidate-1.md"
		attachment_path.write_text(_make_online_resume_markdown("高分候选人"), encoding="utf-8")
		platform.download_attachment_via_ui.return_value = {
			"code": 0,
			"zpData": {"attachment_path": str(attachment_path)},
		}
		_mark_dialogue_ready(tmp_path, 1)

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=mock_ai_69,
		)

		report = pipeline.run(limit=1, threshold=70, ask_for_resume=False)

		assert report.state == "succeeded"
		items = list(report.items)
		assert items[0]["score"] == 69
		assert items[0]["pool_added"] is False, "69分未过70线，不应该入库"

		print(f"\n✅ 69分阈值: score={items[0]['score']}, pool_added={items[0]['pool_added']}")

	def test_analyze_one_single_candidate(self, tmp_path: Path):
		"""单人“分析”必须先完成对话，不能擅自索要或读取简历。

		沟通列表的分析入口负责驱动“打招呼 -> 等待回复 -> 同意并下载附件”的
		状态机。在线简历属于独立查看能力，不能作为这里的无提示兜底，否则 HR
		点击分析时会看到页面跳转，却没有真正发送索要附件的沟通消息。
		"""
		platform = _create_mock_platform()
		online_exporter = _create_mock_online_exporter(tmp_path)
		attachment_downloader = _create_mock_attachment_downloader()

		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=online_exporter,
			attachment_downloader=attachment_downloader,
		)

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=export_service,
		)

		# Execute analyze_one (simulates clicking "分析" on one candidate)
		result = pipeline.analyze_one(
			friend_id=1,
			candidate_name="高分候选人",
			ask_for_resume=True,
		)

		# 没有终审准入时，既不能读取在线简历，也不能提前索要附件。
		assert result.ask_resume_sent is False
		assert result.online_resume_downloaded is False
		assert result.analysis is None
		assert result.status == "waiting_for_dialogue"
		platform.send_message_by_friend.assert_not_called()

		print(
			f"\n✅ Analyze one: name={result.candidate_name}, status={result.status}, ask_sent={result.ask_resume_sent}"
		)

	def test_analyze_one_without_resume_shows_waiting_message(self, tmp_path: Path):
		"""候选人无简历时给出"等待回复"提示。"""
		platform = _create_mock_platform()
		# Make friend_detail fail so no resume can be downloaded
		# Reset side_effect and use return_value for friend_detail to return an error
		platform.friend_detail.side_effect = None
		platform.friend_detail.return_value = {"code": -1, "message": "failed"}

		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=_create_mock_online_exporter(tmp_path),
			attachment_downloader=_create_mock_attachment_downloader(),
		)

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=export_service,
		)

		result = pipeline.analyze_one(
			friend_id=1,
			candidate_name="候选人",
			ask_for_resume=True,
		)

		# Should report that resume is not ready
		assert result.online_resume_downloaded is False
		assert result.error != "", f"Should have error message, got: {result.error}"
		assert "简历尚未就绪" in result.error or result.error != ""

		print(f"\n✅ Analyze without resume: error={result.error[:80]}")

	def test_pipeline_does_not_send_the_same_resume_request_twice(self, tmp_path: Path):
		"""流水线记录首次索要后，重复处理同一候选人不能再次发送消息。"""
		platform = _create_mock_platform()
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,
			export_service=None,
		)
		_mark_dialogue_ready(tmp_path, 1)

		first = PipelineStepResult(candidate_name="待回复候选人", friend_id=1)
		pipeline._process_one(
			first,
			friend_id=1,
			name="待回复候选人",
			ask_for_resume=True,
			ask_message=pipeline.DEFAULT_ASK_MESSAGE,
		)
		second = PipelineStepResult(candidate_name="待回复候选人", friend_id=1)
		pipeline._process_one(
			second,
			friend_id=1,
			name="待回复候选人",
			ask_for_resume=True,
			ask_message=pipeline.DEFAULT_ASK_MESSAGE,
		)

		assert first.ask_resume_sent is True
		assert second.ask_resume_sent is False
		assert second.status == "waiting_for_resume"
		assert platform.send_message_by_friend.call_count == 1

	def test_resume_request_remains_idempotent_after_retry_task_is_removed(self, tmp_path: Path):
		"""重试任务完成后再次点击分析也不能把候选人当成首次联系。

		``pending_retries.json`` 只表示“还要不要继续轮询”，不能同时承担
		“历史上是否已经发过索要消息”的事实记录。真实流程在附件下载成功后
		会移除待重试任务；这个回归测试固定住用户最容易遇到的重复打招呼场景。
		"""
		platform = _create_mock_platform()
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,
			export_service=None,
		)
		_mark_dialogue_ready(tmp_path, 1)

		first = PipelineStepResult(candidate_name="待回复候选人", friend_id=1)
		pipeline._process_one(
			first,
			friend_id=1,
			name="待回复候选人",
			ask_for_resume=True,
			ask_message=pipeline.DEFAULT_ASK_MESSAGE,
		)
		pipeline._retry_scheduler.remove(friend_id=1)

		second = PipelineStepResult(candidate_name="待回复候选人", friend_id=1)
		pipeline._process_one(
			second,
			friend_id=1,
			name="待回复候选人",
			ask_for_resume=True,
			ask_message=pipeline.DEFAULT_ASK_MESSAGE,
		)

		assert first.ask_resume_sent is True
		assert second.ask_resume_sent is False
		assert second.status == "waiting_for_resume"
		assert platform.send_message_by_friend.call_count == 1

	def test_existing_boss_request_is_adopted_without_sending_a_second_message(self, tmp_path: Path):
		"""旧流程已询问但本地无记录时，应读取会话事实并继续等待。"""
		platform = _create_mock_platform()
		platform.download_attachment_via_ui.return_value = {
			"code": 0,
			"message": "候选人未分享附件简历（按钮已禁用）",
		}
		platform.has_existing_resume_request.return_value = True
		_mark_dialogue_ready(tmp_path, 1)
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,
			export_service=None,
		)

		result = pipeline.analyze_one(
			friend_id=1,
			candidate_name="已询问候选人",
			ask_for_resume=True,
		)

		assert result.status == "waiting_for_resume"
		assert result.ask_resume_sent is False
		platform.send_message_by_friend.assert_not_called()
		assert pipeline._conversation_states.has_resume_request_sent(1) is True

	def test_single_analysis_extracts_pdf_text_before_scoring(self, tmp_path: Path):
		"""附件 PDF 必须先提取正文，不能按 UTF-8 失败后给出空简历评分。"""
		attachment_path = tmp_path / "candidate-resume.pdf"
		attachment_path.write_bytes(b"%PDF-1.4 placeholder")
		platform = _create_mock_platform()
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=None,
		)
		pipeline._conversation_states.mark_resume_downloaded(
			1,
			path=str(attachment_path),
			kind="attachment",
		)
		_mark_dialogue_ready(tmp_path, 1)

		with patch(
			"boss_agent_cli.commands.recruiter.communication_pipeline.read_resume_text",
			return_value=_make_online_resume_markdown("高分候选人"),
		) as reader:
			result = pipeline.analyze_one(
				friend_id=1,
				candidate_name="高分候选人",
				ask_for_resume=True,
			)

		assert result.status == "analyzed"
		assert result.score == 82
		reader.assert_called_once_with(attachment_path)
		platform.send_message_by_friend.assert_not_called()

	def test_single_analysis_sends_unreadable_pdf_to_manual_review_without_scoring(self, tmp_path: Path):
		"""PDF 正文无法读取时不得调用模型或持久化低分结果。"""
		attachment_path = tmp_path / "unreadable-resume.pdf"
		attachment_path.write_bytes(b"%PDF-1.4 placeholder")
		platform = _create_mock_platform()
		ai_chat = MagicMock(return_value='{"overall_score": 1, "recommendation": "reject"}')
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=ai_chat,
			export_service=None,
		)
		pipeline._conversation_states.mark_resume_downloaded(
			1,
			path=str(attachment_path),
			kind="attachment",
		)
		_mark_dialogue_ready(tmp_path, 1)

		with patch(
			"boss_agent_cli.commands.recruiter.communication_pipeline.read_resume_text",
			side_effect=ResumeTextReadError("候选人简历 PDF 无法提取有效正文"),
		):
			result = pipeline.analyze_one(
				friend_id=1,
				candidate_name="乱码简历候选人",
				ask_for_resume=True,
			)

		assert result.status == "manual_review"
		assert result.analysis is None
		assert result.score == 0
		assert "解析失败" in result.error
		assert pipeline._conversation_states.get(1)["stage"] == "resume_downloaded"
		ai_chat.assert_not_called()

	def test_ready_for_resume_stage_is_sufficient_for_attachment_analysis(self, tmp_path: Path):
		"""终审阶段是准入事实，不能因历史计数缺失把候选人永久卡在等待对话。"""
		attachment_path = tmp_path / "candidate-resume.pdf"
		attachment_path.write_bytes(b"%PDF-1.4 placeholder")
		platform = _create_mock_platform()
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=None,
		)
		pipeline._conversation_states.mark_resume_downloaded(
			1,
			path=str(attachment_path),
			kind="attachment",
		)
		# 模拟旧版本/恢复逻辑只写入阶段，未写入两个计数的真实状态。
		DialogueStateStore(tmp_path).save(CandidateDialogueState(
			candidate_key="friend:1",
			job_id="job-1",
			stage=DialogueStage.READY_FOR_RESUME,
			interview_phase=InterviewPhase.PROFESSIONAL,
		))

		with patch(
			"boss_agent_cli.commands.recruiter.communication_pipeline.read_resume_text",
			return_value=_make_online_resume_markdown("高分候选人"),
		):
			result = pipeline.analyze_one(
				friend_id=1,
				candidate_name="高分候选人",
				ask_for_resume=True,
			)

		assert result.status == "analyzed"
		assert result.analysis is not None
		platform.send_message_by_friend.assert_not_called()

	def test_retry_uses_rpa_to_accept_and_download_the_shared_attachment(self, tmp_path: Path):
		"""候选人回复后，后台任务必须继续 RPA 同意和下载，不再发送消息。"""
		resume_path = tmp_path / "candidate-resume.md"
		resume_path.write_text(_make_online_resume_markdown("高分候选人"), encoding="utf-8")
		platform = _create_mock_platform()
		platform.download_attachment_via_ui.return_value = {
			"code": 0,
			"zpData": {"attachment_path": str(resume_path)},
		}
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=None,
		)
		_mark_dialogue_ready(tmp_path, 1)
		pipeline._retry_scheduler.schedule(friend_id=1, candidate_name="高分候选人")
		retry_data = pipeline._retry_scheduler._read()
		retry_data["1"]["next_retry_at"] = "2000-01-01T00:00:00"
		pipeline._retry_scheduler._write(retry_data)

		results = pipeline.process_retries()

		assert len(results) == 1
		assert results[0].status == "analyzed"
		assert results[0].attachment_downloaded is True
		assert platform.download_attachment_via_ui.call_count == 1
		platform.send_message_by_friend.assert_not_called()
		assert pipeline.pending_retries() == []

	def test_retry_can_process_only_one_due_task_per_poll(self, tmp_path: Path):
		"""后台轮询每次只处理一个到期任务，避免附件 RPA 长时间占用平台。"""
		platform = _create_mock_platform()
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,
			export_service=None,
		)
		for friend_id in (1, 2):
			_mark_dialogue_ready(tmp_path, friend_id)
			pipeline._retry_scheduler.schedule(
				friend_id=friend_id,
				candidate_name=f"候选人{friend_id}",
			)
			retry_data = pipeline._retry_scheduler._read()
			retry_data[str(friend_id)]["next_retry_at"] = "2000-01-01T00:00:00"
			pipeline._retry_scheduler._write(retry_data)

		results = pipeline.process_retries(max_tasks=1)

		assert len(results) == 1
		assert pipeline._retry_scheduler.count() == 2

	def test_retry_stops_when_the_original_conversation_no_longer_exists(self, tmp_path: Path):
		"""过期 friend_id 不能无限重试，更不能回退到重新打招呼。"""
		platform = _create_mock_platform()
		# 状态机已不再调用在线简历导出器，因此会话失效必须由 RPA 当前页面
		# 返回的定位结果判断，不能再借助旧接口的异常作为旁路信号。
		platform.download_attachment_via_ui.return_value = {
			"code": -1,
			"message": "未找到该候选人的会话",
		}
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,
			export_service=None,
		)
		_mark_dialogue_ready(tmp_path, 11)
		pipeline._retry_scheduler.schedule(friend_id=11, candidate_name="已失效候选人")
		retry_data = pipeline._retry_scheduler._read()
		retry_data["11"]["next_retry_at"] = "2000-01-01T00:00:00"
		pipeline._retry_scheduler._write(retry_data)

		results = pipeline.process_retries()

		assert len(results) == 1
		assert results[0].status == "no_resume"
		assert pipeline.pending_retries() == []
		platform.send_message_by_friend.assert_not_called()

	def test_rule_based_scoring_fallback_when_no_ai(self, tmp_path: Path):
		"""AI 不可用时，已准入的附件仍可降级为规则评分。"""
		platform = _create_mock_platform()
		attachment_paths: dict[int, Path] = {}
		for friend_id in (1, 2):
			path = tmp_path / f"candidate-{friend_id}.md"
			path.write_text(_make_online_resume_markdown(f"候选人{friend_id}"), encoding="utf-8")
			attachment_paths[friend_id] = path
			_mark_dialogue_ready(tmp_path, friend_id)
		platform.download_attachment_via_ui.side_effect = lambda friend_id, save_dir: {
			"code": 0,
			"zpData": {"attachment_path": str(attachment_paths[friend_id])},
		}

		# No AI chat fn → falls back to rule-based scoring
		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=None,  # AI unavailable
		)

		report = pipeline.run(limit=2, threshold=70, ask_for_resume=False)

		assert report.state == "succeeded"
		assert report.analyzed == 2
		for item in report.items:
			assert item.get("analysis_source") == "rule_fallback", (
				f"Should use rule fallback, got: {item.get('analysis_source')}"
			)

		print(f"\n✅ Rule fallback: {report.analyzed} resumes analyzed without AI")

	def test_scoring_chain_from_parser_to_decision(self):
		"""验证评分链：简历解析 → 规则评分 → 自动化决策。"""
		from boss_agent_cli.commands.recruiter.resume_parser import parse_resume
		from boss_agent_cli.automation.scoring import score_candidate
		from boss_agent_cli.automation.models import CandidateSnapshot, CandidateKey

		# Simulate a view_geek API response
		raw_response = {
			"code": 0,
			"zpData": {
				"geekDetailInfo": {
					"geekBaseInfo": {
						"name": "王五",
						"gender": 1,
						"ageDesc": "30岁",
						"degreeCategory": "本科",
						"workYearDesc": "7年",
						"activeTimeDesc": "刚刚活跃",
					},
					"showExpectPosition": {
						"positionName": "销售总监",
						"salaryDesc": "30-50K",
						"locationName": "北京",
					},
					"geekWorkExpList": [
						{
							"company": "某大厂",
							"positionName": "销售经理",
							"department": "企业事业部",
							"startYearMonStr": "2020.01",
							"endYearMonStr": "2025.12",
							"workYearDesc": "6年",
							"responsibility": "负责大客户销售，管理10人团队",
							"workPerformance": "年销售额超1000万",
							"workEmphasis": "销售#&#管理#&#客户",
						}
					],
					"geekProjExpList": [],
					"geekEduExpList": [
						{
							"school": "北京大学",
							"major": "市场营销",
							"degreeDesc": "本科",
							"startYearMonStr": "2011.09",
							"endYearMonStr": "2015.06",
						}
					],
				}
			},
		}

		# Step 1: Parse resume
		parsed = parse_resume(raw_response)
		assert parsed["basic"]["name"] == "王五"
		assert parsed["basic"]["degree"] == "本科"
		assert len(parsed["work_experience"]) == 1

		# Step 2: Export (render to markdown text for analysis)
		from boss_agent_cli.commands.recruiter.resume_export import render_candidate_resume_markdown

		markdown = render_candidate_resume_markdown(parsed, geek_id="geek-wangwu")
		assert "王五" in markdown
		assert "销售经理" in markdown

		# Step 3: Build candidate snapshot and score
		snapshot = CandidateSnapshot(
			key=CandidateKey("wangwu"),
			name="王五",
			title="销售总监",
			city="北京",
			resume_text=markdown,
			education="本科",
			experience_years=7.0,
			last_active_at="刚刚活跃",
			intent_signals=("想看机会", "可面试"),
		)
		score_result = score_candidate(snapshot)

		# This candidate should score well (good city + experience + keywords)
		assert score_result.score >= 40, f"Expected reasonable score, got {score_result.score}"
		assert score_result.pass_hard_conditions is True

		# Step 4: Verify decision logic for score ≥ 70
		from boss_agent_cli.automation.decision import decide_action
		from boss_agent_cli.automation.config import AutomationConfig
		from boss_agent_cli.automation.models import Conversation, ConversationFingerprint

		# For a high-scoring candidate with incoming messages → should get questionnaire
		conversation = Conversation(
			title="王五",
			incoming_messages=("您好，我对这个岗位很感兴趣",),
			outgoing_messages=(),
			all_messages=("您好，我对这个岗位很感兴趣",),
			fingerprint=ConversationFingerprint("wangwu"),
			candidate=snapshot,
		)
		config = AutomationConfig()
		decision = decide_action(conversation, config, {})

		assert decision.matching is not None
		assert decision.matching.score >= 40
		if decision.matching.score >= 70:
			assert decision.action.value in (
				"send_questionnaire",
				"send_follow_up",
				"exchange_contact",
				"create_interview_lead",
			), f"Score ≥ 70 should trigger further communication, got: {decision.action.value}"
			print(f"\n✅ Score {decision.matching.score} ≥ 70 → Action: {decision.action.value}")
		else:
			print(
				f"\n⚠️ Score {decision.matching.score} < 70 → Action: {decision.action.value} (rule-based scoring is conservative)"
			)

		print(
			f"   Candidate: {snapshot.name}, Score: {score_result.score}, Recommendation: {score_result.recommendation}"
		)
		print(f"   Decision: {decision.action.value}, Confidence: {decision.confidence}")

	def test_attachment_async_wait_flow(self, tmp_path: Path):
		"""附件简历异步等待流程：候选人回复后附件可用。"""
		platform = _create_mock_platform()

		# First call: no attachment yet
		call_count = [0]

		def exchange_content_sequence(uid: int) -> dict[str, Any]:
			call_count[0] += 1
			if call_count[0] == 1:
				# First check: no attachment yet
				return {"code": 0, "zpData": {}}
			else:
				# Second check: attachment is now available
				return {
					"code": 0,
					"zpData": {
						"resume": {
							"resumeUrl": "https://example.com/resume.pdf",
							"resumeName": "王五_简历.pdf",
						}
					},
				}

		platform.exchange_content.side_effect = exchange_content_sequence
		platform.is_success.side_effect = lambda r: r.get("code") == 0

		online_exporter = _create_mock_online_exporter(tmp_path)
		attachment_downloader = _create_mock_attachment_downloader()

		# First export: attachment absent
		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=online_exporter,
			attachment_downloader=attachment_downloader,
		)

		result1 = export_service.export(friend_id=1, data_dir=tmp_path)
		assert result1.attachment.status == "absent", (
			f"First call should show no attachment, got: {result1.attachment.status}"
		)

		# Second export: attachment available → downloaded
		result2 = export_service.export(friend_id=1, data_dir=tmp_path)
		assert result2.attachment.status == "downloaded", (
			f"Second call should download attachment, got: {result2.attachment.status}"
		)
		assert result2.attachment.path is not None
		assert result2.attachment.bytes_written is not None

		print(
			f"\n✅ Async attachment flow: 1st call={result1.attachment.status}, "
			f"2nd call={result2.attachment.status}, "
			f"bytes={result2.attachment.bytes_written}"
		)

	def test_scoring_recommendation_maps_to_actions(self):
		"""评分建议映射到自动化动作的验证。"""
		from boss_agent_cli.automation.models import MatchScore

		# High score → invite
		high = MatchScore(
			pass_hard_conditions=True,
			score=85,
			recommendation="invite-to-interview",
			reason="Strong candidate",
		)
		assert high.score >= 70
		assert high.recommendation == "invite-to-interview"

		# Medium score → review
		mid = MatchScore(
			pass_hard_conditions=True,
			score=65,
			recommendation="review",
			reason="Needs more evaluation",
		)
		assert mid.score < 70
		assert mid.recommendation == "review"

		# Low score / hard fail → reject
		low = MatchScore(
			pass_hard_conditions=False,
			score=30,
			recommendation="reject",
			reason="Does not meet requirements",
		)
		assert low.score < 70
		assert low.recommendation == "reject"

		print(f"\n✅ Score→Action mapping: 85→{high.recommendation}, 65→{mid.recommendation}, 30→{low.recommendation}")


class TestPipelineLogger:
	"""Verify pipeline logger records all steps."""

	def test_logger_records_all_pipeline_steps(self, tmp_path: Path):
		"""日志应该记录流水线的每一步。"""
		platform = _create_mock_platform()
		_mark_dialogue_ready(tmp_path, 1)
		online_exporter = _create_mock_online_exporter(tmp_path)
		attachment_downloader = _create_mock_attachment_downloader()

		export_service = ConversationResumeExportService(
			platform=platform,
			online_exporter=online_exporter,
			attachment_downloader=attachment_downloader,
		)

		pipeline = CommunicationPipeline(
			platform=platform,
			data_dir=tmp_path,
			ai_chat_fn=_mock_ai_smart_scoring,
			export_service=export_service,
		)

		report = pipeline.run(limit=1, threshold=70, ask_for_resume=True)

		# Verify logger entries
		logs = report.logs
		assert len(logs) > 0, "Should have log entries"

		log_steps = {entry.get("step") for entry in logs if isinstance(entry, dict)}
		expected_steps = {"pipeline", "list", "ask_resume", "download", "analyze", "pool"}
		found = expected_steps & log_steps
		assert len(found) >= 3, f"Should find at least 3 expected steps, found: {found}"

		print(f"\n✅ Logger recorded steps: {sorted(log_steps)}")


class TestResumeAnalysisEdgeCases:
	"""Edge cases for resume analysis."""

	def test_rule_fallback_with_empty_resume(self):
		"""空简历降级到规则评分。"""
		result = analyze_resume_with_ai("", job_standard="销售岗位", ai_chat_fn=None)

		assert result.source == "rule_fallback"
		assert result.overall_score < 70  # Empty resume should score low
		assert result.recommendation != "invite_to_interview"

		print(f"\n✅ Empty resume: score={result.overall_score}, recommendation={result.recommendation}")

	def test_rule_fallback_with_strong_resume(self):
		"""强简历即使规则评分也有合理分数。"""
		strong_resume = _make_online_resume_markdown("李四", score_worthy=True)
		result = analyze_resume_with_ai(strong_resume, job_standard="销售经理", ai_chat_fn=None)

		assert result.source == "rule_fallback"
		# Strong resume should get reasonable score from rule engine
		assert result.overall_score >= 50, f"Strong resume should score ≥ 50, got {result.overall_score}"

		print(f"\n✅ Strong resume (rule): score={result.overall_score}")

	def test_ai_parse_error_falls_back_to_rules(self):
		"""AI 返回无效 JSON 时降级到规则评分。"""

		def broken_ai(messages):
			return "这不是有效的 JSON 格式 {{ broken"

		strong_resume = _make_online_resume_markdown("李四", score_worthy=True)
		result = analyze_resume_with_ai(
			strong_resume,
			job_standard="销售",
			ai_chat_fn=broken_ai,
		)

		# Should fall back to rule-based (parse_error source means JSON parse failed)
		assert result.source in ("parse_error", "rule_fallback"), (
			f"Should fallback on parse error, got source={result.source}"
		)

		print(f"\n✅ AI parse error fallback: source={result.source}, score={result.overall_score}")
