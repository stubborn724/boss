# 招聘需求覆盖矩阵

这份矩阵把《智能人事 AI》需求文档映射到当前本地工作台的真实能力。状态以
代码、测试和页面行为为准，不以“按钮已经出现”作为完成依据。

| 需求域 | 当前状态 | 代码证据 | 仍需补齐 |
| --- | --- | --- | --- |
| 岗位自然语言标准 | 已实现 | `recruiting.assessment.parse_natural_language_job`、`commands/recruiter/job_standard.py` CLI 入口 | 更复杂语句仍需人工复核 |
| 岗位必答项引导 | 已实现 | `screening.evaluate_job_readiness`、岗位快照 `readiness`、`publish_job`、工作台学历/年限结构化字段 | 更复杂语句仍需人工复核 |
| 企业知识库 / FAQ | 已实现 | `recruiting.knowledge.parse_knowledge_file`、`RecruitingStore.import_knowledge`、岗位隔离检索和来源引用、`KnowledgeDocument.audience` 范围过滤、`RecruitingWorkspace.answer_question` 受控试答 | 自动生成 FAQ 草稿仍需 HR 审核；试答仍需 HR 核对后手动发送 |
| 硬条件筛选 | 已实现 | `screening._hard_filter` | 真实平台字段映射回归 |
| BOSS 沟通 / 推荐自动化 | 已实现并完成真实控制验证 | `recruiting.automation_coordinator`、`automation_queue`、`commands.recruiter.ai_dialogue.process_dialogue_once`、工作台“BOSS 自动化”模块 | 生产使用前按岗位校准单轮上限与发送策略 |
| 推荐回流与异步恢复 | 已实现 | 推荐页仅同步岗位招呼语并发起开场；候选人回流沟通列表后按真实 `friend_id` 合并到统一队列；消息指纹和等待状态避免重复问答 | 同名候选人的歧义绑定保持人工复核 |
| 附件简历终审 | 已实现 | `ConversationStateStore.resume_kind=attachment`、`AutomationQueueStore.record_final_review`、受限本地附件打开接口 | 在线简历和缺失/空附件只进人工复核，不进入合格列表 |
| 语义匹配 | 已实现 | `screening._semantic_layer` 同义短语组 + `recruiting.ai_review` 可选 AI 语义提供方；`commands/recruiter/screen_resumes.py` CLI 入口支持 `--use-ai` | 更丰富语义与行业词库 |
| 风险识别 | 已实现 | `screening._risk_layer`、`_parse_experience_timeline` | 更丰富的行业/岗位语义风险仍需人工复核 |
| 评分区间与证据 | 已实现 | `score_candidate`、`AssessmentReport.score_breakdown`、`build_optimization_projection` 的 `hiring_learning` | 样本继续积累后再扩大对照范围 |
| 专业问题生成 | 已实现 | `generate_professional_question_items`、岗位技能/风险信号追问、`AssessmentReport.professional_question_items` | 行业词库仍可按业务继续扩充 |
| 泛回答追问门槛 | 已实现 | `professional_qa.threshold=60`、逐题 `question_scores`、`failed_question_ids`、问题/回答版本元数据 | 更丰富的业务追问模板 |
| 岗位级专业问答开关 | 已实现 | `JobProfile.professional_qa_enabled`、基础意向后的待办分流、`private_professional_qa` 私域核验待办、问题/回答/来源/版本元数据、技能与风险追问、结论记录、`build_review_gate` 的私域核验门禁、工作台岗位表单 | 私域问题模板和行业词库仍可继续丰富 |
| 四轮沟通回访 | 已实现 | `CommunicationRecord`、沟通待办 | 真实平台只读联调 |
| 简历交换 / 加私域 / 面试 | 人工回填已实现 | `professional_passed → prepare_resume_exchange → resume_exchanged → review_resume → resume_passed → continue_conversation`、`private_contacts`、`interview_invites`、终局待办 | 不自动执行外部动作 |
| 招聘漏斗 / CRM | 已实现 | pipeline、候选人时间线、待办中心、workflow.queue 候选人处理队列 | 话术和结果反馈统计 |
| 不匹配原因反馈 | 已实现 | `MismatchFeedback`、`record_mismatch_feedback`、Web 反馈入口、`mismatch_reason_rates` | 平台手工反馈仍需由 HR 在官网完成后回填 |
| 每日配额 / 工作时段 | 已实现 | `automation.scheduling.PacingPolicy`、`build_pacing_status`、`SafetyGuard`、Web `安全节奏` 面板 | 生产配置仍需按账号人工校准额度 |
| 多账号 / 多企业隔离 | 已实现基线 | `RecruitingContext`、`RecruitingContextRegistry`、上下文专属工作区/认证/Profile/导出目录、Web 上下文选择器 | 外部平台实际多账号联调与权限审批 |
| 话术 / FAQ / 录用结果自我优化 | 已实现 | `recruiting.insights.build_optimization_projection`、候选人问题需求审计、FAQ 需求排行、话术回复/通过/录用率、录用组与其他终局组的脱敏画像/评估信号对照、`OptimizationDraft`、工作台 `optimization_drafts`、Web 审核接口 | 统计仍是本地人工回填事实；小样本只作趋势提示，岗位标准调整需 HR 手动确认 |
| React/Vue 前端 | 有限实现 | 当前为本地 aiohttp 原生 HTML；招聘工作台拆为“处理队列 / 岗位与知识 / 评估与复盘”三种视图，并保留桌面侧栏和移动端单列断点 | 先继续优化现有单页，再评估迁移 |

## 本轮闭环路径

1. 岗位保存后先查看 `readiness`，补齐城市、薪资、学历、年限和核心能力。
2. 在岗位知识库导入 `.md`、`.txt` 或 `.docx`，核对来源路径和哈希后再用于问题生成。
3. 导入在线简历后生成报告，先看硬条件，再看语义命中和风险信号。
4. 岗位启用 BOSS 专业问答时，低于 60 分只生成追问待办，不能把候选人当作已通过；岗位关闭时改为生成 `private_professional_qa` 待办，HR 在私域人工承接，问题、回答、来源、版本和核验结论先落本地，核验通过后才解锁交换简历。
5. HR 在官方 BOSS 页面完成沟通、加私域和面试后，再回到工作台记录事实。
6. 终局录用、淘汰或暂缓均保留本地原因，供后续反馈统计使用。
7. 先在顶部选择企业/账号上下文；切换会同时切换本地工作区和登录 Profile，导出结果会自动绑定当前岗位并创建评估待办。
8. 多候选人时优先使用“候选人处理队列”：可按姓名/下一动作搜索和按阶段筛选；队列先按待办执行状态，再按风险、硬条件和评分提示排序，并展示可解释的优先级理由、综合评分和风险等级；“去处理”会定位到对应待办，“恢复已跳过的待办”不会退回到无关的阶段下拉框。
9. 复盘区的建议可显式生成“改进草稿”，重复点击只复用同一条记录；HR 可标记“已采纳”“已忽略”或重新打开，采纳后仍需手动修改岗位、知识库或 FAQ，系统不会自动改配置。

10. 顶部“闭环总览”统一投影当前岗位的待办、活跃候选人、终局数量和下一动作；“去处理”只定位到已有本地控件。顶部“安全节奏”统一投影自动化引擎的额度、工作时段和冷却原因，不会启动自动化动作。

11. 招聘工作台默认进入“处理队列”，集中显示候选人队列、漏斗、闭环进度和待办；“岗位与知识”集中岗位标准、企业知识库和 FAQ；“评估与复盘”集中候选人导入、评估、已记录活动和复盘建议。视图切换只改变本地显示，不会改变阶段或写入外部平台。已在桌面默认视口和 390px 移动视口验证无横向溢出，FAQ 列表也保持在表单之外，避免浏览器错误嵌套。

12. “岗位与知识”视图提供候选人问题试答：优先命中已审核 FAQ，其次只返回 `candidate/shared` 范围的知识库短摘录；内部销售资料不会进入候选人回答。每条结果带来源标题和版本。没有可验证来源时只返回安全拒答，HR 核对后再手动复制到官方沟通页面，不自动发送。

13. “评估与复盘”视图会把已记录的录用、淘汰和暂缓结果按脱敏画像与评估分项做组级对照，展示综合评分、专业问答、稳定性、经验年限、行业和技能等信号。录用组或对照组少于 3 条时只标记为趋势参考；任何信号都只能生成待审核改进草稿，不会自动改岗位标准、话术或平台配置。

评估通过不会直接跳到私域：专业问答后的确认只进入“准备交换简历”，HR
完成官方页面的简历交换后才生成“简历评估”待办；简历复评通过后才生成第
1 轮沟通待办。沟通、私域和面试结果必须通过各自的记录接口落盘，不能用
通用“标记完成”伪造外部事实。

“BOSS 自动化”模块在显式 research 模式下可按岗位执行已配置的低频 RPA
沟通：先做硬筛，后进行基础与专业两阶段问答，再只对真实附件做终审。停止请求
会在每位候选人之间生效，未回复候选人保留原阶段并等待后续新消息。交换联系方式、
面试邀约和最终录用仍由 HR 决策与确认；系统不提供绕过风控或高频无边界发送的入口。

“BOSS 自动化”只承担执行过程：按岗位显示同步队列、当前阶段、跳过原因和候选人
与 AI 已实际处理的对话时间线。时间线只保存进入 AI 判断的候选人消息和 BOSS 已确认
发送的 AI 回复，不把完整历史或简历正文重复放入提示词。专业阶段的紧凑岗位卡同时
携带岗位名称、行业、技能、必须条件和加分项，要求模型从其中选择一个岗位焦点生成
单一情境题，因此 Java、销售等岗位不会使用同一套专业问题。

“候选人”模块只展示完成附件简历终审、达到分数线且未被拒绝的记录。队列身份使用
“岗位 + BOSS 会话”组合键，同一人投递不同岗位时各自的对话、附件、评分和岗位归属
独立保存；页面按岗位分组，并在每个岗位内按终审评分从高到低显示，附件路径只能经
服务端白名单验证后打开。
