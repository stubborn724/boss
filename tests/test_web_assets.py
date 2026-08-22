"""本地控制台静态页面的会话导出入口测试。"""

from boss_agent_cli.web.assets import render_console_page


def test_console_page_separates_job_management_and_candidates() -> None:
	"""招聘工作台应把岗位配置和候选人处理拆成清晰入口。"""
	page = render_console_page("test-token")

	assert 'data-workspace-view="jobs"' in page
	assert 'data-workspace-view="candidates"' in page
	assert "岗位管理" in page
	assert "候选人" in page


def test_console_page_exposes_five_module_recruiting_workbench() -> None:
	"""新版主界面应以五个模块组织操作，并把岗位需求直接交给 Agent。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-product-shell"' in page
	assert 'data-product-module="login"' in page
	assert 'data-product-module="jobs"' in page
	assert 'data-product-module="resumes"' in page
	assert 'data-product-module="candidates"' in page
	assert 'data-product-module="followups"' in page
	assert 'id="job-standard-input"' in page
	assert "/api/recruiting/jobs/rules/analyze" in page
	assert "/api/recruiting/jobs/rules" in page
	assert "AI 分析生成规则" in page
	assert 'id="job-rule-editor-dialog"' in page
	assert 'id="job-knowledge-dialog"' in page
	assert 'id="job-sync-boss-button"' in page


def test_product_workbench_exposes_visible_recruiting_automation_controls() -> None:
	"""用户实际看到的新版工作台必须提供自动化队列入口，而非只留在隐藏旧页面。"""
	page = render_console_page("test-token")

	assert 'data-product-module="automation"' in page
	assert 'data-product-view="automation"' in page
	assert 'id="product-automation-job"' in page
	assert 'id="product-automation-sync"' in page
	assert 'id="product-automation-start-conversation"' in page
	assert 'id="product-automation-start-recommendation"' in page
	assert "button.id='product-automation-start-full-flow'" in page
	assert "source:'full_flow'" in page
	assert 'id="product-automation-conversation-detail"' in page
	assert 'id="product-automation-qualified"' not in page
	assert 'id="product-automation-candidate-pool"' in page
	assert "/api/recruiting/automation/sync" in page
	assert "/api/recruiting/automation/start" in page
	assert "/api/recruiting/automation/candidate-pool" in page
	assert "/api/recruiting/automation/candidates/" in page
	assert "row.reason_codes.join('、')));" in page


def test_product_automation_control_displays_current_job_schedule_window() -> None:
	"""自动化控制区应直接显示设置页保存的当前岗位定时窗口。"""
	page = render_console_page("test-token")

	assert 'id="product-automation-schedule-summary"' in page
	assert "沟通列表：" in page
	assert "推荐牛人：" in page
	assert "全流程：" in page
	assert "当前岗位未设置定时任务" in page
	assert "renderAutomationScheduleSummary" in page


def test_product_workbench_exposes_settings_and_two_independent_schedules() -> None:
	"""新版设置页应同时承载约面试和两个按钮各自的定时任务。"""
	page = render_console_page("test-token")

	assert 'data-product-module="settings"' in page
	assert 'data-product-view="settings"' in page
	assert 'id="product-interview-settings-form"' in page
	assert 'id="conversation-schedule-form"' in page
	assert 'id="recommendation-schedule-form"' in page
	assert "/api/recruiting/automation/schedules" in page
	assert 'id="conversation-schedule-enabled"' in page
	assert 'id="recommendation-schedule-enabled"' in page
	assert 'data-schedule-source="full_flow"' in page
	assert 'id="full_flow-schedule-form"' in page
	assert 'id="full_flow-schedule-enabled"' in page
	assert "20:00" in page
	assert "09:00" in page
	assert "保存全流程定时任务" in page


def test_resume_product_view_is_online_resume_only() -> None:
	"""获取简历页只读在线简历，不能再暴露附件索要或附件分析入口。"""
	page = render_console_page("test-token")

	assert "查看在线简历" in page
	assert "/online-resume/open" in page
	assert 'id="product-online-resume-dialog"' in page
	assert "在线简历已在本平台读取" in page
	assert "刷新只同步沟通候选人，不会发送消息、索要附件或下载附件" in page
	assert "处理附件并分析" not in page


def test_online_resume_preview_uses_the_job_selected_in_the_resume_view() -> None:
	"""在线简历按钮必须使用本页岗位筛选，不能依赖自动化页的独立状态。"""
	page = render_console_page("test-token")

	assert "const jobId=selectedConversationJobId !== 'all' ? selectedConversationJobId : '';" in page
	assert "请在本页岗位筛选中选择具体岗位后，再查看在线简历。" in page
	assert "const previewRequest=await post('/api/conversations/'+encodeURIComponent(item.selection_id)+'/online-resume/open',{job_id:jobId});" in page
	assert "if(!payload.ok) throw new Error((payload.error || {}).message || '在线简历预览请求失败');" not in page


def test_online_resume_preview_reports_progress_next_to_clicked_candidate() -> None:
	"""长候选人列表中，预览进度和失败原因必须显示在当前行，不能藏在列表底部。"""
	page = render_console_page("test-token")

	assert "const onlineState=make('span','row-meta','');" in page
	assert "online.disabled=true;" in page
	assert "online.textContent='读取中…';" in page
	assert "onlineState.textContent='正在读取在线简历…';" in page
	assert "onlineState.textContent=error.message || '在线简历读取失败';" in page
	assert "finally { online.disabled=false; online.textContent='查看在线简历'; }" in page


def test_online_resume_preview_state_survives_periodic_list_rerender() -> None:
	"""自动刷新重绘候选人列表后，当前行的读取进度或失败原因仍须保留。"""
	page = render_console_page("test-token")

	assert "const onlineResumeRowStates=new Map();" in page
	assert "const rowState=onlineResumeRowStates.get(item.selection_id);" in page
	assert "onlineResumeRowStates.set(item.selection_id,{state:'running',message:'正在读取在线简历…'});" in page
	assert "onlineResumeRowStates.set(item.selection_id,{state:'failed',message:error.message || '在线简历读取失败'});" in page
	assert "onlineResumeRowStates.set(item.selection_id,{state:'succeeded',message:'在线简历已打开。'});" in page


def test_resume_product_view_filters_candidates_by_position_locally() -> None:
	"""沟通候选人页应按岗位标识刷新 BOSS 列表，不能展示伪造的未标注分组。"""
	page = render_console_page("test-token")

	assert 'id="resume-position-filter"' in page
	assert 'value="all">全部岗位' in page
	assert "selectedConversationJobId" in page
	assert "未标注岗位" not in page
	assert "$('#resume-position-filter').addEventListener('change'" in page
	assert "refreshConversationList(event.currentTarget.value)" in page
	assert "job_id=" in page
	assert "waitForConversationListRefresh = async jobId" in page
	assert "listing.job_id !== requestedJobId" in page


def test_resume_refresh_immediately_reports_submission_and_terminal_result() -> None:
	"""刷新沟通列表必须在点击、提交和完成三个阶段给出明确反馈。"""
	page = render_console_page("test-token")

	assert "正在刷新沟通列表，请稍候…" in page
	assert "刷新请求已提交，正在读取 BOSS 沟通列表。" in page


def test_resume_refresh_waits_for_slow_boss_job_switch_before_reporting_timeout() -> None:
	"""岗位切换与长列表读取超过 30 秒时，页面不能误导用户重新登录。

	BOSS 页面可能要先切换职位，再重新加载大量会话。后台任务尚在运行时，
	前端应与其等待预算保持一致，避免将正常的慢读取显示成登录失败。
	"""
	page = render_console_page("test-token")

	assert "const deadline=Date.now()+120000;" in page
	assert "BOSS 沟通列表读取超过 120 秒，后台可能仍在读取" in page
	assert "刷新沟通列表超时，请检查 BOSS 登录状态后重试" not in page
	assert "沟通列表已刷新，共读取 " in page
	assert "show($('#resume-action-notice'),result.notice);" in page
	assert "resumeRefreshButton.disabled=true" in page


def test_product_workbench_surfaces_save_and_failure_feedback() -> None:
	"""设置保存不能只改远处状态栏，必须弹出统一的成功或失败反馈。"""
	page = render_console_page("test-token")

	assert 'id="product-toast"' in page
	assert "const notify = (message, failed = false) =>" in page
	assert "约面试设置已保存" in page
	assert "沟通列表定时任务已保存" in page
	assert "推荐牛人定时任务已保存" in page


def test_product_login_retries_once_after_console_restart() -> None:
	"""登录页按钮遇到旧会话令牌时应刷新并仅重试用户刚触发的打开动作。"""
	page = render_console_page("test-token")

	assert "STALE_CONSOLE_SESSION" in page
	assert "boss-agent-retry-open-login" in page
	assert "sessionStorage.setItem('boss-agent-retry-open-login', '1')" in page
	assert "sessionStorage.removeItem('boss-agent-retry-open-login')" in page
	assert "window.location.reload()" in page


def test_console_page_exposes_recommendation_settings_and_candidate_boss_actions() -> None:
	"""招聘工作台应集中提供岗位招呼语、面试设置和三个候选人平台动作。"""
	page = render_console_page("test-token")

	assert 'data-workspace-view="settings"' in page
	assert "automation-interview-settings" in page
	assert "automation-settings-save" in page
	assert "/api/recruiting/automation/settings" in page
	assert "/api/recruiting/automation/candidates/" in page
	assert "换电话" in page
	assert "换微信" in page
	assert "约面试" in page
	assert "confirm('确认向该候选人请求电话吗？')" in page


def test_product_automation_status_clears_stale_error_after_successful_sync() -> None:
	"""自动化同步成功后应显示同步人数并清除旧错误样式，避免把成功误报为失败。"""
	page = render_console_page("test-token")

	assert "item.detail" in page
	assert "自动化已停止：" in page

	assert "automationSync.state === 'succeeded'" in page
	assert "`已同步 ${automationSync.synced || 0} 位沟通候选人。`" in page
	assert "show(status, automationMessage, automationFailed);" in page


def test_automation_request_surfaces_backend_state_errors_immediately() -> None:
	"""自动化请求必须直接展示后端 blocked/failed 结果，不能静默等待轮询。"""
	page = render_console_page("test-token")

	assert "const automationResultState = result.state || 'idle';" in page
	assert "automationResultState === 'failed' || automationResultState === 'blocked'" in page
	assert "同步请求已提交，后台正在读取 BOSS 沟通列表。" in page


def test_product_automation_shows_and_enforces_recommendation_daily_quota_state() -> None:
	"""推荐额度触顶时，页面必须禁用推荐入口并说明沟通列表仍会继续。"""
	page = render_console_page("test-token")

	assert "const recommendationQuota=(automationSnapshot || {}).recommendation_quota || {};" in page
	assert "const recommendationBlocked=recommendationQuota.blocked === true;" in page
	assert "|| recommendationBlocked" in page
	assert "当前仅处理沟通列表；次日自动恢复。" in page


def test_product_job_standard_waits_for_background_completion_before_showing_result() -> None:
	"""岗位 Agent 为后台任务时，页面不能只等一次短刷新就遗留“正在分析”。"""
	page = render_console_page("test-token")

	assert "const waitForRecruitingOperation = async operation =>" in page
	assert "await waitForRecruitingOperation('analyze-job-rules')" in page
	assert "await waitForRecruitingOperation('apply-job-rules')" in page
	assert "规则保存失败" in page
	assert "刷新只同步沟通候选人，不会发送消息、索要附件或下载附件" in page


def test_product_job_editor_shows_boss_fields_and_hides_raw_ai_input() -> None:
	"""编辑岗位弹窗展示 BOSS 信息、规则及岗位独立评分配置。"""
	page = render_console_page("test-token")

	assert 'id="job-rule-editor-dialog"' in page
	assert 'id="rule-boss-name"' in page
	assert 'id="rule-boss-city"' in page
	assert 'id="rule-boss-salary"' in page
	assert 'id="rule-boss-education"' in page
	assert 'id="rule-boss-experience"' in page
	assert 'id="rule-boss-internship"' in page
	assert 'id="rule-boss-address"' in page
	assert 'id="rule-boss-description"' in page
	assert 'id="rule-must-have"' in page
	assert 'id="rule-nice-to-have"' in page
	assert 'id="rule-reject-if"' in page
	assert 'id="rule-risk-signals"' in page
	assert 'id="rule-weight-hard-match"' in page
	assert 'id="rule-weight-professional-qa"' in page
	assert 'id="rule-screening-threshold"' in page
	assert 'id="rule-recommendation-threshold"' in page
	assert 'id="rule-professional-qa-threshold"' in page
	assert "const reviewedScoring = () =>" in page
	assert "scoring:reviewedScoring()" in page
	assert 'id="job-rule-save"' in page
	assert 'id="job-hard-condition-dialog"' not in page
	assert 'id="job-rule-requirements"' not in page


def test_product_workbench_script_preserves_javascript_newline_escape() -> None:
	"""岗位工作台脚本中的规则换行必须保持为 JS 转义，避免整段脚本语法错误。"""
	page = render_console_page("test-token")

	assert "join('\\n')" in page
	assert "split(/\\n+/)" in page


def test_product_navigation_uses_attribute_based_view_changes() -> None:
	"""内置浏览器环境下岗位导航使用稳定的属性写入切换内容面板。"""
	page = render_console_page("test-token")

	assert "view.classList.toggle('active'" not in page
	assert "view.setAttribute('class'" in page


def test_console_page_uses_compact_job_cards_before_showing_configuration() -> None:
	"""岗位管理首页应先展示岗位卡片，配置表单只在用户主动进入编辑时出现。"""
	page = render_console_page("test-token")

	assert "recruitingJobManagementDashboard.id='recruiting-job-management-dashboard'" in page
	assert 'id="recruiting-job-card-list"' in page
	assert "recruitingJobManagementMode='overview'" in page
	assert "openJobManagementEditor" in page
	assert "查看并分析简历" in page
	assert "继续配置" in page
	assert "job-management-editor-hidden" in page


def test_console_page_uses_a_compact_workbench_navigation_without_hiding_resume_flow() -> None:
	"""新工作台应收敛导航，但必须保留登录、获取简历和沟通分析入口。"""
	page = render_console_page("test-token")

	assert "recruiting-workbench-shell" in page
	assert "recruiting-workbench-nav" in page
	assert "岗位管理" in page
	assert "候选人" in page
	assert "候选人（含 AI 评分）" in page
	assert "获取简历" in page
	assert "沟通记录" in page
	assert "登录状态" in page
	assert "recruiting-resume-source-switcher" in page
	assert "conversation-panel" in page
	assert "legacy-console-hidden" in page


def test_console_page_hides_legacy_route_headers_inside_the_recruiting_shell() -> None:
	"""来源页进入新工作台壳层后不能再叠加旧控制台页头。"""
	page = render_console_page("test-token")

	assert ".recruiting-ui-shell .route-page > .page-head { display:none; }" in page


def test_candidate_view_labels_the_three_screening_layers() -> None:
	"""候选人详情应使用招聘人员可理解的三层筛选标题。"""
	page = render_console_page("test-token")

	assert "硬条件筛选" in page
	assert "语义匹配" in page
	assert "风险识别" in page


def test_console_page_integrates_score_navigation_into_candidate_view() -> None:
	"""评分入口应融入候选人模块，避免与独立看板形成重复工作流。"""
	page = render_console_page("test-token")

	assert "scoreBoardNavigationLink" in page
	assert "scoreBoardNavigationLink.remove()" in page


def test_console_page_keeps_conversation_id_export_as_advanced_fallback() -> None:
	"""内部会话 ID 导出保留为高级备用入口，默认流程不再依赖该字段。"""
	page = render_console_page("test-token")

	assert 'id="conversation-download-form"' in page
	assert 'name="friend_id"' in page
	assert "conversation-resume-download" in page
	assert "按会话 ID 导出（高级）" in page


def test_console_page_offers_current_chat_export_without_manual_internal_id() -> None:
	"""用户可直接导出官方沟通页当前选中的候选人资料。"""
	page = render_console_page("test-token")

	assert 'id="current-conversation-download-button"' in page
	assert "current-conversation-resume-download" in page


def test_console_page_offers_latest_conversation_export_without_manual_id() -> None:
	"""默认入口应以最近会话下载取代内部 ID 表单。"""
	page = render_console_page("test-token")

	assert 'id="latest-conversation-download-button"' in page
	assert "latest-conversation-resume-download" in page


def test_console_page_lists_boss_conversations_for_candidate_specific_download() -> None:
	"""控制台应提供同排序沟通列表，用户按姓名选择而非猜测最近一条。"""
	page = render_console_page("test-token")

	assert 'id="conversation-list"' in page
	assert "api/conversations" in page
	assert "resume-download" in page
	assert "选择候选人并点击“下载简历”" in page
	assert "从沟通列表取得会话 ID 后即可导出" not in page
	assert "fetch('/api/conversations?refresh=1')" in page
	assert "listing.notice.message" in page


def test_resume_product_view_shows_conversation_read_failure_instead_of_empty_state() -> None:
	"""获取简历页必须区分列表为空和 RPA 读取失败，不能让点击看似无响应。"""
	page = render_console_page("test-token")

	assert "const resumeListError = listing.error && listing.error.message" in page
	assert "listing.state === 'failed'" in page
	assert "读取失败" in page


def test_console_page_offers_single_candidate_boss_context_lookup() -> None:
	"""每条沟通卡片应支持按需读取官方卡片信息，避免用户凭姓名猜测。"""
	page = render_console_page("test-token")

	assert "查看 BOSS 卡片信息" in page
	assert "/details" in page
	assert "conversation_detail" in page


def test_console_page_shows_selected_candidate_download_progress_next_to_the_list() -> None:
	"""列表行发起导出后，进度或失败原因必须显示在可见候选人列表区域。"""
	page = render_console_page("test-token")

	assert 'id="conversation-list-action-state"' in page
	assert 'class="conversation-action-state hint"' in page
	assert "正在导出所选候选人的简历…" in page
	assert "conversation.error ? conversation.error.message" in page
	assert "rememberRequestError(conversationListActionState,payload)" in page


def test_console_page_labels_conversation_export_without_claiming_it_is_latest() -> None:
	"""会话导出结果标题应覆盖列表、当前会话和最近一位三种入口。"""
	page = render_console_page("test-token")

	assert "<h2>沟通候选人附件简历</h2>" in page
	assert "<h2>最近会话导出</h2>" not in page


def test_console_page_download_state_uses_a_complete_login_fallback_expression() -> None:
	"""下载状态的 JavaScript 条件表达式必须保留未登录时的完整回退分支。"""
	page = render_console_page("test-token")

	assert "(!ready && allowed?'请先登录':'')" in page


def test_console_page_labels_login_status_as_rpa_browser_session() -> None:
	"""页面必须说明登录态属于项目 RPA 浏览器，而非当前工作台标签。"""
	page = render_console_page("test-token")

	assert "RPA 浏览器登录状态" in page
	assert "当前项目连接的 RPA Chrome 已通过 BOSS 页面校验。" in page
	assert "loginButton.disabled=login.state==='running' || login.state==='succeeded'" in page
	assert "login.state==='failed'?'重新登录'" in page


def test_console_page_requires_explicit_conversation_refresh_after_login_recovery() -> None:
	"""连接或登录恢复不能自动驱动 BOSS 页面，列表读取必须由用户点击触发。"""
	page = render_console_page("test-token")

	assert "let lastLoginState = null;" in page
	assert "previousLoginState" in page
	assert "fetch('/api/conversations').catch" not in page
	assert "return fetch('/api/conversations');" not in page
	assert "fetch('/api/conversations?refresh=1')" in page


def test_console_page_keeps_stale_session_error_visible_after_refresh() -> None:
	"""服务重启导致页面写令牌失效时，用户应看到恢复动作而不是无响应。"""
	page = render_console_page("test-token")

	assert "本地控制台已重启，请刷新页面后重试" in page
	assert "let pendingRequestError = null;" in page


def test_console_page_explains_desktop_default_for_optional_output_directories() -> None:
	"""网页表单应明确告知用户默认文件位置，避免误以为下载没有发生。"""
	page = render_console_page("test-token")

	# 三处可选目录：按会话 ID 导出、按已知标识导出、一键批量导出。
	assert page.count('placeholder="默认保存到桌面"') == 3
	assert "默认使用本地数据目录" not in page


def test_console_page_offers_recommendation_candidate_list_and_export_action() -> None:
	"""本地控制台应提供推荐牛人列表和候选人级导出按钮。"""
	page = render_console_page("test-token")

	assert 'id="recommendation-list"' in page
	assert 'id="recommendation-refresh-button"' in page
	assert "api/recommendations" in page
	assert "recommendations/" in page
	assert "下载在线简历" in page
	# 在线简历页的安全说明可以出现“下载附件”字样；这里只约束推荐候选人
	# 不会暴露一个会触发附件下载的操作按钮。
	assert "button.textContent='下载附件'" not in page


def test_console_page_selects_boss_job_before_loading_recommendations() -> None:
	"""推荐流程应先读取并选择已发布职位，不要求用户手工复制加密职位 ID。"""
	page = render_console_page("test-token")

	assert 'id="recommendation-job-select"' in page
	assert 'id="recommendation-job-refresh-button"' in page
	assert "/api/boss-jobs" in page
	assert "职位 ID（可选" not in page
	assert "option.textContent=item.name" in page


def test_console_page_recommendation_script_keeps_internal_ids_out_of_dom() -> None:
	"""推荐列表页面只依赖不透明 selection_id，不应读取 geek/job/security 字段。"""
	page = render_console_page("test-token")

	assert "item.geek_id" not in page
	assert "item.security_id" not in page


def test_console_page_includes_recruiting_workspace_forms_and_manual_review_copy() -> None:
	"""招聘工作台应提供岗位、知识库、候选人评估和人工确认入口。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-workspace"' in page
	assert 'id="recruiting-job-form"' in page
	assert 'id="recruiting-knowledge-form"' in page
	assert 'id="recruiting-faq-form"' in page
	assert 'id="recruiting-candidate-form"' in page
	assert 'id="recruiting-assess-form"' in page
	assert "api/recruiting/workspace" in page
	assert "待人工确认" in page
	assert "不会自动发送 BOSS 消息" in page


def test_console_page_exposes_job_professional_qa_toggle() -> None:
	"""岗位表单应能明确选择是否启用平台专业问答。"""
	page = render_console_page("test-token")

	assert "recruitingProfessionalQaToggle.name='professional_qa_enabled'" in page
	assert "启用 BOSS 专业问答" in page
	assert "professional_qa_enabled" in page


def test_console_page_exposes_knowledge_file_import_and_source_citations() -> None:
	"""知识驱动问答需要文件导入入口和问题来源引用钩子。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-knowledge-import-form"' in page
	assert 'name="source_path"' in page
	assert "/api/recruiting/knowledge/import" in page
	assert "source_sha256" in page
	assert "professional_question_items" in page
	assert "question_version" in page
	assert "follow_up_questions" in page
	assert "可选追问" in page


def test_console_page_exposes_source_backed_candidate_question_answering() -> None:
	"""岗位与知识视图应提供不自动发送的本地受控试答入口。"""
	page = render_console_page("test-token")

	assert "recruiting-knowledge-answer-input" in page
	assert "生成有来源的试答" in page
	assert "/api/recruiting/answer?job_id=" in page
	assert "请核对来源后再手动回复" in page
	assert "已安全拒答" in page


def test_console_page_exposes_read_only_optimization_feedback() -> None:
	"""工作台应展示复盘指标和建议，但不出现自动修改入口。"""
	page = render_console_page("test-token")

	assert "recruiting-insights" in page
	assert "复盘建议" in page
	assert "mismatch_reason_rates" in page
	assert "不匹配原因率" in page
	assert "mutations" in page
	assert "不会自动修改岗位标准" in page


def test_console_page_exposes_self_evolution_demand_and_template_metrics() -> None:
	"""复盘视图应展示问题需求、FAQ 排行、话术结果和小样本提示。"""
	page = render_console_page("test-token")

	assert "question_demand_rates" in page
	assert "top_faq_questions" in page
	assert "template_outcome_rates" in page
	assert "sample_notice" in page
	assert "候选人问题需求" in page
	assert "话术结果" in page
	assert "hiring_learning" in page
	assert "录用结果学习" in page


def test_console_page_recruiting_script_has_no_automatic_send_action() -> None:
	"""工作台前端只能复制话术，不能出现自动发送接口或按钮。"""
	page = render_console_page("test-token")

	assert "自动发送" not in page.replace("不会自动发送 BOSS 消息", "")
	assert "/api/recruiting/send" not in page
	assert "send-message" not in page


def test_console_page_has_sidebar_navigation_for_major_workflows() -> None:
	"""主工作流应有稳定的左侧导航锚点，避免用户在长页面中迷路。"""
	page = render_console_page("test-token")

	assert 'class="side-nav"' in page
	assert 'href="#login-panel"' in page
	assert 'href="#conversation-panel"' in page
	assert 'href="#recommendation-panel"' in page
	assert 'href="#resume-download-panel"' in page
	assert 'href="#recruiting-workspace"' in page
	assert "IntersectionObserver" in page


def test_console_page_honors_initial_hash_for_deep_linked_workspaces() -> None:
	"""直接打开招聘工作台锚点时，页面应主动滚动到对应区域。"""
	page = render_console_page("test-token")

	assert "scrollToHashTarget" in page
	assert "scrollToHashTarget(location.hash.slice(1)" in page


def test_console_page_has_recruiting_workspace_views() -> None:
	"""工作台应提供按任务分组的视图，避免所有表单堆在一张长页面里。"""
	page = render_console_page("test-token")

	assert 'data-workspace-view="operations"' in page
	assert 'data-workspace-view="setup"' in page
	assert 'data-workspace-view="review"' in page
	assert 'workspace-setup-block' in page
	assert 'workspace-review-block' in page


def test_console_page_closes_faq_form_before_faq_list() -> None:
	"""FAQ 列表不能被嵌套进表单，否则后续工作台控件会被浏览器错误归属。"""
	page = render_console_page("test-token")

	faq_start = page.index('<form id="recruiting-faq-form"')
	faq_end = page.index('id="recruiting-faq-list"', faq_start)
	faq_fragment = page[faq_start:faq_end]

	assert faq_fragment.count("<form") == faq_fragment.count("</form>")


def test_console_page_keeps_app_layout_single_column_on_mobile() -> None:
	"""视觉覆盖层不能覆盖移动断点，否则固定侧栏会把主内容推出视口。"""
	page = render_console_page("test-token")

	assert '@media (max-width:900px) { .app-layout { grid-template-columns:1fr; }' in page


def test_console_page_exposes_explainable_safety_pacing_panel() -> None:
	"""页面应把额度、冷却和工作时段原因放在可见主导航中。"""
	page = render_console_page("test-token")

	assert 'id="pacing-panel"' in page
	assert 'href="#pacing-panel"' in page
	assert 'id="pacing-summary"' in page
	assert 'id="pacing-detail"' in page
	assert "renderPacing" in page
	assert "当前时段允许执行" in page
	assert "非工作时段暂不执行" in page


def test_console_page_exposes_top_level_closed_loop_summary() -> None:
	"""长页面顶部应显示真实待办和下一动作，避免用户误以为流程中断。"""
	page = render_console_page("test-token")

	assert 'id="loop-panel"' in page
	assert 'id="loop-next"' in page
	assert 'id="loop-action"' in page
	assert 'id="loop-pending"' in page
	assert "renderLoopSummary" in page
	assert "workflow.queue_summary" in page


def test_console_page_hides_empty_action_notice_without_reserving_space() -> None:
	"""工作台没有操作提示时不应留下误导性的空白色块。"""
	page = render_console_page("test-token")

	assert ".notice:empty" in page


def test_console_page_exposes_searchable_candidate_action_queue() -> None:
	"""工作台应提供按候选人和下一动作筛选的队列入口，避免流程埋在长列表里。"""
	page = render_console_page("test-token")

	assert "recruiting-candidate-queue" in page
	assert "recruiting-candidate-queue-filter" in page
	assert "renderRecruitingCandidateQueue" in page
	assert "workflow.queue" in page
	assert "recruitingSelectedCandidateId" in page


def test_console_page_shows_explainable_candidate_priority_signals() -> None:
	"""候选人队列应展示优先级理由、评分和风险，避免只给一个无依据的排序。"""
	page = render_console_page("test-token")

	assert "priority_label" in page
	assert "priority_reasons" in page
	assert "assessment_score" in page
	assert "risk_level" in page
	assert "优先级" in page
	assert "综合评分" in page
	assert "风险" in page


def test_console_page_activates_sidebar_from_initial_hash_and_hash_changes() -> None:
	"""未指定锚点时应直接进入岗位管理，锚点跳转仍保持导航状态一致。"""
	page = render_console_page("test-token")

	assert "activateNav(location.hash.slice(1) || 'recruiting-workspace')" in page
	assert "window.addEventListener('hashchange'" in page


def test_console_page_reanchors_deep_link_after_async_refresh() -> None:
	"""异步列表撑高页面后，首次深链仍应回到目标区域且不干扰后续轮询。"""
	page = render_console_page("test-token")

	assert "pendingInitialHashTarget" in page
	assert "settleInitialHashTarget" in page
	assert "await refreshRecruiting(); settleInitialHashTarget();" in page
	assert "target.scrollIntoView({behavior:'auto',block:'start'}); activateNav(sectionId);" in page


def test_console_page_preserves_selected_candidate_across_workspace_refreshes() -> None:
	"""轮询刷新不能把用户正在评估的候选人重置为列表第一人。"""
	page = render_console_page("test-token")

	assert "let recruitingSelectedCandidateId = '';" in page
	assert "recruitingSelectedCandidateId=candidate.candidate_id" in page
	assert "recruitingAssessCandidate.value=recruitingSelectedCandidateId" in page


def test_console_page_scopes_assessment_task_jobs_to_valid_published_targets() -> None:
	"""评估待办只能选择已发布岗位，避免把候选人误投到草稿或归档岗位。"""
	page = render_console_page("test-token")

	assert "job.status==='published'" in page
	assert "task.job_id ? jobs.filter(job => job.job_id===task.job_id)" in page


def test_console_page_exposes_context_switcher_for_account_isolation() -> None:
	"""页面必须明确显示当前企业/账号上下文，并提供切换入口。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-context-select"' in page
	assert "/api/recruiting/contexts" in page
	assert "/api/recruiting/context" in page
	assert "context_key" in page


def test_console_page_connects_download_results_to_assessment_and_review() -> None:
	"""下载结果应能直接进入工作台，评估后应显示人工确认动作。"""
	page = render_console_page("test-token")

	assert "导入招聘工作台" in page
	assert "/api/recruiting/candidates/import" in page
	assert "/api/recruiting/review" in page
	assert "确认继续沟通" in page
	assert "暂不推进" in page

	assert "item.friend_id" not in page


def test_console_page_explains_automatic_workspace_handoff_after_resume_export() -> None:
	"""导出结果应把自动登记和失败兜底说清楚，用户不必猜下一步。"""
	page = render_console_page("test-token")

	assert "已自动进入招聘工作台" in page
	assert "workspace_import" in page
	assert "导入失败时可手动重试" in page


def test_console_page_exposes_pipeline_stage_tracking() -> None:
	"""招聘工作台需要展示候选人阶段和人工记录入口，形成可追溯漏斗。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-pipeline-summary"' in page
	assert "记录阶段" in page
	assert "/api/recruiting/candidates/" in page
	assert "candidate_quote" in page
	assert "/api/recruiting/answers" in page
	assert "记录回答" in page
	assert "查看本地记录" in page
	assert "pipeline-terminal-summary" in page
	assert "流程已到终局" in page


def test_console_page_restores_imported_candidate_after_select_options_are_built() -> None:
	"""导入候选人后必须在重建下拉选项之后恢复选中项，避免 DOM 重置覆盖状态。"""
	page = render_console_page("test-token")

	options_index = page.index("recruitingAssessCandidate.replaceChildren();")
	pending_index = page.index("if (pendingImportedResumePath)")
	assert options_index < pending_index
	assert "pendingImportedResumePath=body.resume_path" in page


def test_console_page_exposes_actionable_recruiting_task_center() -> None:
	"""前端必须把评估后的下一步展示成可完成的本地待办。"""
	page = render_console_page("test-token")

	assert "待办中心" in page
	assert "recruiting-task-list" in page
	assert "/api/recruiting/tasks/" in page
	assert "标记完成" in page
	assert "不会代替你发消息、加私域或邀约" in page
	assert "流程已到终局" in page
	assert "恢复待办" in page
	assert "job_id:task.job_id" in page


def test_console_page_exposes_job_publish_edit_and_mismatch_feedback_controls() -> None:
	"""岗位草稿和不匹配反馈必须在页面上有明确入口，不能只存在于后端。"""
	page = render_console_page("test-token")

	assert "recruiting-publish-job-button" in page
	assert "recruitingNewJobButton" in page
	assert "/api/recruiting/jobs/" in page
	assert "/api/recruiting/mismatch-feedback" in page
	assert "appendMismatchFeedback" in page
	assert "map(item => `必须${item}`)" in page
	assert "map(item => `优先${item}`)" in page
	assert "map(item => `不接受${item}`)" in page
	assert "map(item => `风险：${item}`)" in page


def test_console_page_turns_assessment_tasks_into_inline_actions() -> None:
	"""评估待办应直接提供岗位选择和生成评估动作，不能只让用户标记完成。"""
	page = render_console_page("test-token")

	assert "选择岗位并生成评估" in page
	assert "task.kind==='assess_candidate' || task.kind==='reassess_candidate'" in page
	assert "生成本地评估" in page
	assert "recruiting-workflow-progress" in page
	assert "workflow.next_step" in page
	assert "recruiting-workflow-action" in page
	assert "去处理" in page


def test_console_page_routes_every_workflow_task_to_an_actionable_target() -> None:
	"""发布、沟通、私域、面试和终局待办不能让闭环按钮变成死路。"""
	page = render_console_page("test-token")

	assert "publish_job:'#recruiting-job-form'" in page
	assert "continue_conversation:'#recruiting-task-list'" in page
	assert "communication_round:'#recruiting-task-list'" in page
	assert "record_private_contact:'#recruiting-task-list'" in page
	assert "schedule_interview:'#recruiting-task-list'" in page
	assert "record_interview:'#recruiting-task-list'" in page
	assert "record_hiring_decision:'#recruiting-task-list'" in page
	assert "data-task-id" in page
	assert "pendingTaskId" in page
	assert "pending_candidate_name" in page
	assert "is-next" in page


def test_console_page_routes_recovery_states_to_real_candidate_or_task_actions() -> None:
	"""无待办或已跳过待办时，闭环按钮仍需落到可执行入口而不是空状态。"""
	page = render_console_page("test-token")

	assert "recover_task:'#recruiting-task-list'" in page
	assert "record_stage:'#recruiting-candidate-list'" in page
	assert "focus_task_id" in page
	assert "data-candidate-id" in page
	assert "task.status==='skipped'" in page
	assert "updateRecruitingTask(task,'pending'" in page
	assert "恢复待办" in page
	assert "nextStep==='record_stage'" in page
	assert "const recordStageAction=nextStep==='record_stage' && details ? details.querySelector('button:not(:disabled)')" in page


def test_console_page_hands_resume_export_into_workspace() -> None:
	"""简历导出完成后必须提供明确的工作台接力入口，避免流程停在结果卡片。"""
	page = render_console_page("test-token")

	assert "进入招聘工作台" in page
	assert "workspace-import-action" in page
	assert "recruiting-assess-form" in page
	assert "自动选中候选人并进入评估" in page


def test_console_page_restores_export_handoff_candidate_after_workspace_refresh() -> None:
	"""导出结果异步刷新工作区后，页面仍应把同一候选人放回下一待办。"""
	page = render_console_page("test-token")

	assert "let pendingImportedCandidateId = '';" in page
	assert "pendingImportedCandidateId=String(handoff.candidate_id || '')" in page
	assert "if (pendingImportedCandidateId)" in page
	assert "pendingImportedCandidateId=null" in page


def test_console_page_labels_loaded_workspace_state_and_can_start_next_candidate() -> None:
	"""已有数据或终局候选人时，页头不能继续显示未开始，并应允许导入下一位。"""
	page = render_console_page("test-token")

	assert "workspaceStateText(state, workflow, pipeline)" in page
	assert "已闭环" in page
	assert "导入下一位候选人" in page
	assert "closed:'#recruiting-candidate-form'" in page
	assert "const taskTarget=nextStep==='closed' ? null :" in page
	assert "const candidateTarget=nextStep==='closed' ? null :" in page
	assert "input[name=\"resume_path\"]" in page


def test_console_page_routes_intermediate_state_machine_tasks_to_real_actions() -> None:
	"""基础确认、专业问答和简历复核待办不能落入泛化完成分支。"""
	page = render_console_page("test-token")

	assert "confirm_basic:'#recruiting-task-list'" in page
	assert "complete_basic:'#recruiting-task-list'" in page
	assert "start_professional_qa:'#recruiting-task-list'" in page
	assert "prepare_resume_exchange:'#recruiting-task-list'" in page
	assert "review_resume:'#recruiting-task-list'" in page
	assert "task.kind==='start_professional_qa' || task.kind==='review_resume'" in page
	assert "task.kind!=='start_professional_qa'" in page


def test_console_page_does_not_offer_generic_completion_for_resume_review() -> None:
	"""简历复核应生成评估，不应出现绕过评估的普通标记完成路径。"""
	page = render_console_page("test-token")

	assert "review_resume" in page
	assert "task.kind!=='review_resume'" in page
	assert "生成本地评估" in page


def test_console_page_explains_the_next_review_checkpoint() -> None:
	"""同一个评估按钮在专业通过和简历复评后应显示不同的下一步。"""
	page = render_console_page("test-token")

	assert "nextReviewLabel" in page
	assert "resume_exchanged" in page
	assert "确认通过" in page


def test_console_page_does_not_reopen_an_already_reviewed_assessment() -> None:
	"""已确认的评估只展示结果状态，不能再次显示会重开流程的确认按钮。"""
	page = render_console_page("test-token")

	assert "评估已人工确认，请先重新生成评估" in page
	assert "report.review_required !== false" in page


def test_console_page_binds_manual_resume_handoff_to_selected_job() -> None:
	"""下载结果手动导入工作台时必须继承当前岗位，避免候选人串岗。"""
	page = render_console_page("test-token")

	assert "const jobId=selectedRecruitingJobId()" in page
	assert "resume_path:path,source:source,job_id:jobId" in page


def test_console_page_treats_terminal_candidates_as_read_only() -> None:
	"""终局候选人不能继续从评估表单启动旧流程。"""
	page = render_console_page("test-token")

	assert "terminalStages" in page
	assert "终局候选人不可重新评估" in page
	assert "select.disabled=terminalStages.has(selected)" in page
	assert "saveButton.disabled=terminalStages.has(candidate.stage)" in page
	assert "终局候选人请使用终局待办" in page
	assert "job_id:selectedRecruitingJobId(),stage:stageSelect.value" in page


def test_console_page_requires_an_explicit_outcome_for_review_tasks() -> None:
	"""人工确认待办不能用普通完成按钮绕过继续、补充或淘汰选择。"""
	page = render_console_page("test-token")

	assert "task.kind==='review_assessment'" in page
	assert "人工确认结果" in page
	assert "submitAssessmentReview(report" in page


def test_console_page_exposes_private_domain_interview_and_hiring_actions() -> None:
	"""待办中心应暴露闭环所需的人工记录控件和专用接口。"""
	page = render_console_page("test-token")

	assert "record_private_contact" in page
	assert "/api/recruiting/private-contacts" in page
	assert "/api/recruiting/interviews" in page
	assert "/api/recruiting/interviews/result" in page
	assert "target_stage" in page
	assert "录用" in page and "淘汰" in page and "暂缓" in page
	assert "不会自动加私域" in page


def test_console_page_exposes_weighted_evidence_and_funnel_metrics() -> None:
	"""工作台要展示带权证据和漏斗转化，而不是只有最终分数。"""
	page = render_console_page("test-token")

	assert "score_breakdown" in page
	assert "recruiting-funnel-metrics" in page
	assert "转化率" in page
	assert "阶段平均停留" in page
	assert "education_requirement" in page
	assert "source_conversion" in page
	assert "template_effectiveness" in page
	assert "communication_outcome_rates" in page
	assert "decision_outcome_rates" in page
	assert "qualified_rate" in page
	assert "professional_qa_breakdown" in page
	assert "逐题评分" in page


def test_console_page_exposes_scoped_knowledge_search_and_citations() -> None:
	"""知识库区域要能检索并显示引用来源，避免保存后仍与流程脱节。"""
	page = render_console_page("test-token")

	assert 'id="recruiting-knowledge-search-form"' in page
	assert "api/recruiting/search" in page
	assert 'id="recruiting-knowledge-search-results"' in page
	assert "来源引用" in page


def test_console_page_exposes_communication_round_timeline_and_follow_up_controls() -> None:
	"""招聘工作台应提供四轮沟通记录、回复摘要和下一次跟进入口。"""
	page = render_console_page("test-token")

	assert "/api/recruiting/communications" in page
	assert "候选人回复摘要" in page
	assert "保存本轮沟通" in page
	assert "沟通时间线" in page
	assert "communication_round" in page


def test_console_page_exposes_job_readiness_and_three_layer_screening() -> None:
	"""岗位不完整和三层初筛结果必须在工作台可见，避免流程停在黑盒总分。"""
	page = render_console_page("test-token")

	assert 'name="education_requirement"' in page
	assert 'name="min_experience_years"' in page
	assert "missing_required_fields" in page
	assert "clarification_questions" in page
	assert "岗位标准还缺" in page
	assert "三层初筛" in page
	assert "hard_filter" in page
	assert "semantic_match" in page
	assert "风险信号" in page
	assert "follow_up_questions" in page


def test_console_page_exposes_faq_draft_review_before_persisting() -> None:
	"""FAQ 生成、人工审核和入库必须是三个可见且分离的动作。"""
	page = render_console_page("test-token")

	assert "生成 FAQ 草稿" in page
	assert 'id="recruiting-faq-drafts"' in page
	assert "/api/recruiting/faq-drafts" in page
	assert "pending_review" in page
	assert "保存为 FAQ" in page
	assert "source_document_id" in page
	assert "source_version" in page


def test_console_page_exposes_manual_message_template_usage_tracking() -> None:
	"""评估话术要能标记人工使用，并关联模板版本供复盘。"""
	page = render_console_page("test-token")

	assert "/api/recruiting/message-usage" in page
	assert "标记已使用" in page
	assert "template_version" in page
	assert "不会自动发送" in page


def test_console_page_renders_imported_candidate_profile_and_missing_fields() -> None:
	"""候选人队列应直接展示结构化画像和缺失字段，帮助 HR 决定下一步。"""
	page = render_console_page("test-token")

	assert "candidate.profile" in page
	assert "候选人画像" in page
	assert "缺少字段" in page
	assert "expected_salary" in page


def test_console_page_exposes_one_click_batch_export_controls() -> None:
	"""一键导出必须有来源、数量、扫描和停止四类可见入口。"""
	page = render_console_page("test-token")

	assert 'id="batch-export-panel"' in page
	assert 'href="#batch-export-panel"' in page
	assert "一键批量导出" in page
	assert 'id="batch-export-source"' in page
	assert 'id="batch-export-limit"' in page
	assert "前 10 位" in page and "前 20 位" in page and "前 50 位" in page
	assert "只扫描附件（不下载）" in page
	assert 'id="batch-export-stop"' in page
	assert "/api/batch-export" in page
	assert "/api/batch-export/stop" in page


def test_console_page_shows_batch_progress_and_per_candidate_results() -> None:
	"""页面要展示这一批导出了什么，而不是只给一个成功提示。"""
	page = render_console_page("test-token")

	assert 'id="batch-export-summary"' in page
	assert 'id="batch-export-bar"' in page
	assert 'id="batch-export-results"' in page
	assert "renderBatchExport" in page
	assert "batch_export" in page
	assert "已处理" in page
	assert "含 PDF" in page
	assert "online_filename" in page


def test_console_page_distinguishes_pdf_available_absent_and_unchecked() -> None:
	"""未检测不能显示成无附件，否则会被误读为候选人没交简历。"""
	page = render_console_page("test-token")

	assert "attachment_badge" in page
	assert "可导 PDF" in page
	assert "无附件" in page
	assert "未检测" in page
	assert "can_export_pdf" in page
	assert "no_attachment" in page
	assert "not_checked" in page


def test_console_page_explains_batch_stop_reasons_with_recovery() -> None:
	"""停批必须解释原因，用户才知道是额度、冷却还是登录问题。"""
	page = render_console_page("test-token")

	assert "batchStopReasonLabels" in page
	assert "daily_quota" in page
	assert "cooldown" in page
	assert "login_expired" in page
	assert "repeated_failure" in page
	assert "stopped_by_user" in page


def test_console_page_offers_bulk_handoff_into_the_recruiting_workspace() -> None:
	"""批量导出结果要能一次性登记进工作台，但必须由用户显式点击。"""
	page = render_console_page("test-token")

	assert 'id="batch-export-import-all"' in page
	assert "全部导入工作台" in page
	assert "importBatchExportResults" in page
	assert "/api/recruiting/candidates/import" in page


def test_console_page_batch_export_never_requests_resumes_from_candidates() -> None:
	"""批量页面只能读取已有资料，不能出现索要简历或发送入口。"""
	page = render_console_page("test-token")

	assert "request-resume" not in page
	assert "exchange_request" not in page
	assert "不发消息、不索要附件、不交换联系方式" in page


def test_console_page_exposes_boss_job_sync_and_score_groups() -> None:
	"""职位镜像与六档评分分组必须在页面有可见入口，而非仅停留在后端接口。"""
	page = render_console_page("test-token")

	assert "同步 BOSS 职位" in page
	assert "/api/recruiting/jobs/sync-boss" in page
	assert "score_groups" in page
	assert "强烈推荐" in page
	assert "未评估" in page


def test_console_page_uses_detailed_ai_assessment_layout_and_rejection_statistics() -> None:
	"""评估页应扩大候选人区域，并把 AI 解释和不合格统计展示给 HR。"""
	page = render_console_page("test-token")

	assert "assessment-layout" in page
	assert "assessment-workbench" in page
	assert "AI 语义分析" in page
	assert "AI 已核对命中" in page
	assert "AI 待追问" in page
	assert "不合格原因统计" in page
	assert "rejection_reason_statistics" in page
	assert "score-group-totals" in page
