"""招聘工作台领域能力。

本包只处理岗位标准、知识库、候选人本地资料和评估结果，不直接访问 BOSS
平台，也不执行发送消息、交换联系方式或邀约等外部动作。平台交互仍由既有
招聘适配器负责，Web 控制台通过窄接口调用这里的纯本地能力。
"""

from boss_agent_cli.recruiting.ai_review import (
	AIResumeReview,
	AIReviewError,
	build_review_messages,
	declared_criteria,
	parse_review,
	review_resume,
)
from boss_agent_cli.recruiting.auto_assignment import AutoResumeAssignmentService
from boss_agent_cli.recruiting.assessment import (
	evaluate_job_readiness,
	extract_candidate_profile,
	generate_message_templates,
	generate_professional_question_items,
	generate_professional_questions,
	parse_natural_language_criteria,
	parse_natural_language_job,
	screen_candidate,
	score_candidate,
)
from boss_agent_cli.recruiting.screening import build_review_gate
from boss_agent_cli.recruiting.models import (
	AssessmentReport,
	CandidateDecision,
	CandidateRecord,
	CandidateTask,
	CommunicationRecord,
	MessageTemplateUsage,
	FAQEntry,
	InterviewInvite,
	JobProfile,
	KnowledgeDocument,
	MismatchFeedback,
	PrivateDomainContact,
	RecruitingCriteria,
)
from boss_agent_cli.recruiting.store import RecruitingStore, RecruitingStoreError
from boss_agent_cli.recruiting.knowledge import ParsedKnowledgeFile, parse_knowledge_file
from boss_agent_cli.recruiting.context import (
	DEFAULT_RECRUITING_CONTEXT,
	RecruitingContext,
	RecruitingContextRegistry,
	context_data_dir,
)

__all__ = [
	"AIResumeReview",
	"AutoResumeAssignmentService",
	"AIReviewError",
	"AssessmentReport",
	"CandidateDecision",
	"CandidateRecord",
	"CandidateTask",
	"CommunicationRecord",
	"MessageTemplateUsage",
	"FAQEntry",
	"InterviewInvite",
	"JobProfile",
	"KnowledgeDocument",
	"MismatchFeedback",
	"PrivateDomainContact",
	"RecruitingCriteria",
	"RecruitingStore",
	"RecruitingStoreError",
	"build_review_messages",
	"declared_criteria",
	"parse_review",
	"review_resume",
	"generate_message_templates",
	"generate_professional_question_items",
	"generate_professional_questions",
	"ParsedKnowledgeFile",
	"parse_knowledge_file",
	"DEFAULT_RECRUITING_CONTEXT",
	"RecruitingContext",
	"RecruitingContextRegistry",
	"context_data_dir",
	"evaluate_job_readiness",
	"extract_candidate_profile",
	"build_review_gate",
	"parse_natural_language_criteria",
	"parse_natural_language_job",
	"screen_candidate",
	"score_candidate",
]
