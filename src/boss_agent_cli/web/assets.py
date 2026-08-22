"""本地招聘控制台的静态页面。

页面不依赖 CDN 或第三方脚本，避免将本地任务状态、导出路径或候选人信息发送到
外部服务。动态区域只读取后端白名单元数据，不会通用渲染任意 JSON。
"""

from __future__ import annotations


# 样式独立成普通字符串常量：页面主体是 f-string，CSS 留在里面就必须把每一个
# 大括号写成两个，既难读也容易漏。集中维护后可以按组件分块，并且所有颜色都走
# 同一份令牌，浅色与深色只需替换令牌本身。
_CONSOLE_CSS = """
/* ---------- 设计令牌 ---------- */
:root {
  color-scheme: light;
  --bg:#f4f6fa; --bg-soft:#eaeff6; --panel:#ffffff; --panel-soft:#f7f9fc; --panel-strong:#eef3f8;
  --line:#e3e9f1; --line-strong:#cfd9e6;
  --text:#152238; --text-soft:#42546c; --muted:#71839c;
  --accent:#0f766e; --accent-dark:#0b5e58; --accent-soft:#e7f4f1; --accent-line:#9ed3ca;
  --info:#1d4ed8; --info-soft:#edf2ff; --info-line:#c4d4fd;
  --warn:#b45309; --warn-soft:#fff7ea; --warn-line:#f1d7a6;
  --danger:#b42318; --danger-soft:#fff1ef; --danger-line:#f4c8c3;
  --ok:#0b7a56; --ok-soft:#e8f7f0; --ok-line:#a7ddc6;
  --radius:12px; --radius-sm:8px; --radius-xs:6px;
  --shadow-sm:0 1px 2px rgba(21,34,56,.05);
  --shadow:0 8px 24px rgba(21,34,56,.07);
  --ring:0 0 0 3px rgba(29,78,216,.26);
  --nav-w:216px; --topbar-h:66px;
}

/* ---------- 基础排版 ---------- */
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  margin:0; min-width:320px; background:var(--bg); color:var(--text); font-size:14px; line-height:1.55;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
h1 { margin:0; font-size:17px; font-weight:700; letter-spacing:.01em; }
h2 { margin:0; font-size:15px; font-weight:700; }
h3 { margin:0; font-size:14px; font-weight:700; }
p { margin:0; }
code { padding:1px 5px; border-radius:4px; background:var(--panel-strong); font-family:ui-monospace,Consolas,monospace; font-size:12px; }
.subtle,.hint { color:var(--muted); font-size:12.5px; }
.subtle { margin:6px 0 0; }
.label { color:var(--muted); font-size:12px; font-weight:600; letter-spacing:.02em; }
.state { margin:4px 0 0; font-size:19px; font-weight:700; letter-spacing:-.01em; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
/* ---------- 控件 ---------- */
button,input,select,textarea { font:inherit; }
button {
  display:inline-flex; align-items:center; justify-content:center; gap:6px; cursor:pointer;
  min-height:36px; padding:0 14px; border:1px solid var(--accent); border-radius:var(--radius-xs);
  color:#fff; background:var(--accent); font-size:13px; font-weight:600; white-space:nowrap;
  box-shadow:var(--shadow-sm); transition:background-color 150ms ease, border-color 150ms ease, opacity 150ms ease;
}
button:hover:not(:disabled) { background:var(--accent-dark); border-color:var(--accent-dark); }
button:disabled { cursor:not-allowed; opacity:.45; box-shadow:none; }
.secondary { color:var(--text-soft); background:var(--panel); border-color:var(--line-strong); box-shadow:none; }
.secondary:hover:not(:disabled) { color:var(--info); background:var(--info-soft); border-color:var(--info-line); }
.secondary-link {
  display:inline-flex; align-items:center; min-height:34px; padding:0 12px; border:1px solid var(--line-strong);
  border-radius:var(--radius-xs); color:var(--text-soft); background:var(--panel); text-decoration:none; font-size:12.5px; font-weight:600;
}
.secondary-link:hover { color:var(--info); background:var(--info-soft); border-color:var(--info-line); }
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible,summary:focus-visible {
  outline:none; box-shadow:var(--ring);
}
input,select,textarea {
  width:100%; min-height:36px; padding:7px 10px; color:var(--text); background:var(--panel-soft);
  border:1px solid var(--line-strong); border-radius:var(--radius-xs); transition:border-color 150ms ease, background-color 150ms ease;
}
input:hover,select:hover,textarea:hover { border-color:#b7c6d8; }
input:focus,select:focus,textarea:focus { background:var(--panel); border-color:var(--accent-line); }
input::placeholder,textarea::placeholder { color:#95a5b9; }
textarea { min-height:84px; resize:vertical; line-height:1.5; }
select { appearance:auto; }
.form { display:grid; gap:13px; margin-top:16px; }
label { display:grid; gap:5px; color:var(--muted); font-size:12.5px; font-weight:600; }
.toggle-label { display:flex; align-items:center; gap:8px; font-weight:600; }
.toggle-label input { width:auto; min-height:16px; padding:0; accent-color:var(--accent); }
.actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
details summary { cursor:pointer; }
.skip-link {
  position:fixed; left:14px; top:14px; z-index:40; transform:translateY(-180%); padding:9px 14px;
  border-radius:var(--radius-xs); color:#fff; background:var(--text); text-decoration:none; font-weight:700;
  transition:transform 160ms ease;
}
.skip-link:focus { transform:translateY(0); }
/* ---------- 顶栏 ---------- */
.topbar {
  position:sticky; top:0; z-index:20; display:flex; align-items:center; justify-content:space-between; gap:20px;
  min-height:var(--topbar-h); padding:12px 26px; border-bottom:1px solid var(--line);
  background:rgba(255,255,255,.92); backdrop-filter:blur(12px);
}
.brand-line { display:flex; align-items:center; gap:11px; }
.brand-mark {
  display:grid; place-items:center; width:30px; height:30px; border-radius:9px; color:#fff;
  background:linear-gradient(140deg,#14867c,#0b5e58); font-weight:800; font-size:13px;
  box-shadow:0 3px 8px rgba(15,118,110,.28);
}
.brand-line .subtle { margin:1px 0 0; font-size:12px; }
.topbar-meta { display:flex; flex-wrap:wrap; justify-content:flex-end; align-items:center; gap:8px; }
.meta-chip {
  display:inline-flex; align-items:center; min-height:26px; padding:3px 10px; border:1px solid var(--line-strong);
  border-radius:999px; color:var(--muted); background:var(--panel); font-size:11.5px; font-weight:600;
}
.meta-chip.good { color:var(--ok); border-color:var(--ok-line); background:var(--ok-soft); }
.context-picker { display:grid; gap:3px; min-width:212px; color:var(--muted); font-size:11px; }
.context-picker select { min-height:32px; padding:4px 8px; font-size:12.5px; }
.context-picker .hint { min-height:1.1em; font-size:11px; }
.badge { flex:0 0 auto; border:1px solid var(--line-strong); border-radius:999px; padding:4px 10px; color:var(--muted); background:var(--panel); font-size:12px; }

/* ---------- 布局与侧栏 ---------- */
.shell { max-width:1320px; margin:0 auto; padding:22px 26px 56px; }
.app-layout { display:grid; grid-template-columns:var(--nav-w) minmax(0,1fr); gap:22px; align-items:start; }
.side-nav {
  position:sticky; top:calc(var(--topbar-h) + 18px); display:grid; gap:2px; padding:10px;
  border:1px solid var(--line); border-radius:var(--radius); background:var(--panel); box-shadow:var(--shadow-sm);
}
.side-nav-heading {
  padding:9px 10px 5px; color:var(--muted); font-size:10.5px; font-weight:700;
  letter-spacing:.11em; text-transform:uppercase;
}
.side-nav-heading + .side-nav-link { margin-top:0; }
.side-nav-link {
  display:flex; align-items:center; gap:9px; padding:8px 10px; border:1px solid transparent; border-radius:var(--radius-xs);
  color:var(--text-soft); text-decoration:none; font-size:13px; font-weight:600;
  transition:background-color 140ms ease, color 140ms ease;
}
.side-nav-link .nav-dot { flex:0 0 auto; width:6px; height:6px; border-radius:50%; background:var(--line-strong); transition:background-color 140ms ease; }
.side-nav-link:hover { color:var(--text); background:var(--panel-strong); }
.side-nav-link.active { color:var(--accent-dark); background:var(--accent-soft); border-color:var(--accent-line); }
.side-nav-link.active .nav-dot { background:var(--accent); }
.side-nav-foot { margin-top:4px; padding:9px 10px 3px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; line-height:1.45; }
/* ---------- 路由页面 ---------- */
.workarea { min-width:0; display:grid; gap:0; }
.route-page { display:grid; gap:16px; min-width:0; }
.route-page.route-hidden { display:none !important; }
.page-head {
  display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; gap:14px;
  padding:0 2px 2px;
}
.page-head-title { display:grid; gap:4px; min-width:0; }
.page-head-eyebrow { color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
.page-head-title .page-title { font-size:21px; font-weight:700; letter-spacing:-.01em; }
.page-head-title p { color:var(--muted); font-size:13px; }
.page-head-aside { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.grid { display:grid; grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr); gap:16px; min-width:0; }
.grid.grid-single { grid-template-columns:minmax(0,1fr); }

/* ---------- 卡片 ---------- */
.panel {
  min-width:0; padding:18px 20px; border:1px solid var(--line); border-radius:var(--radius);
  background:var(--panel); box-shadow:var(--shadow-sm);
}
.panel > .status + .form,.panel > .status + .notice { margin-top:14px; }
.status { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:16px; }
.status .hint { margin-top:5px; }
.status-list { display:grid; gap:11px; margin:16px 0 0; }
.status-item { padding:10px 12px; border:1px solid var(--line); border-left:3px solid var(--line-strong); border-radius:var(--radius-xs); background:var(--panel-soft); }
.status-item.good { border-left-color:var(--accent); }
.status-item.warn { border-left-color:var(--warn); }
.status-item.error { border-left-color:var(--danger); }
.result { margin-top:16px; border-top:1px solid var(--line); padding-top:14px; }
.result h2 { margin-bottom:4px; }
dl { display:grid; grid-template-columns:96px minmax(0,1fr); gap:6px 14px; margin:10px 0 0; }
dt { color:var(--muted); font-size:12.5px; font-weight:600; }
dd { margin:0; overflow-wrap:anywhere; font-size:13px; }

/* ---------- 提示条 ---------- */
.notice {
  margin:14px 0 0; padding:10px 12px; border:1px solid var(--info-line); border-radius:var(--radius-xs);
  color:var(--text-soft); background:var(--info-soft); font-size:12.5px; overflow-wrap:anywhere;
}
.notice:empty { display:none; }
.notice.error { color:var(--danger); border-color:var(--danger-line); background:var(--danger-soft); }
.notice.warn { color:var(--warn); border-color:var(--warn-line); background:var(--warn-soft); }
.notice.success { color:var(--ok); border-color:var(--ok-line); background:var(--ok-soft); }

/* ---------- 指标块 ---------- */
.metric-tile,.pacing-metric,.loop-metric,.pipeline-step {
  min-width:0; padding:11px 13px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft);
}
.pacing-metric-label,.loop-metric-label,.pipeline-step-label { color:var(--muted); font-size:11.5px; font-weight:600; }
.pacing-metric-value,.loop-metric-value { margin-top:3px; font-size:19px; font-weight:700; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
/* ---------- 安全节奏与闭环总览 ---------- */
.pacing-panel,.loop-panel { grid-column:1 / -1; }
.pacing-state {
  display:inline-flex; align-items:center; min-height:30px; padding:4px 12px; border:1px solid var(--ok-line);
  border-radius:999px; color:var(--ok); background:var(--ok-soft); font-size:12.5px; font-weight:700;
}
.pacing-state.paused { color:var(--warn); border-color:var(--warn-line); background:var(--warn-soft); }
.pacing-state.unavailable { color:var(--danger); border-color:var(--danger-line); background:var(--danger-soft); }
.pacing-metrics,.loop-summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:15px; }
.pacing-detail { margin-top:12px; }
.loop-panel .status { align-items:flex-start; }
.loop-next { margin-top:4px; font-size:16px; font-weight:700; letter-spacing:-.01em; overflow-wrap:anywhere; }
.loop-panel.is-closed { border-color:var(--ok-line); }
.loop-panel.is-attention { border-color:var(--warn-line); }

/* ---------- 列表行 ---------- */
.conversation-action-state { min-height:1.4em; margin-top:11px; overflow-wrap:anywhere; }
.conversation-action-state:empty { min-height:0; margin-top:0; }
.conversation-list { display:grid; gap:8px; margin:14px 0; }
.conversation-row,.recommendation-row {
  display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:12px; padding:11px 13px;
  border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft);
  transition:border-color 140ms ease, background-color 140ms ease;
}
.conversation-row:hover,.recommendation-row:hover { border-color:var(--line-strong); background:var(--panel); }
.conversation-name,.recommendation-name { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; font-weight:700; }
.conversation-time { margin-top:2px; color:var(--muted); font-size:12.5px; font-variant-numeric:tabular-nums; }
.conversation-context,.recommendation-meta { margin-top:2px; color:var(--muted); font-size:12.5px; overflow-wrap:anywhere; }
.conversation-detail { margin-top:5px; color:var(--info); font-size:12px; overflow-wrap:anywhere; }
.conversation-row button,.recommendation-row button { min-width:76px; min-height:32px; padding:0 11px; }
.conversation-toolbar { display:flex; flex-wrap:wrap; align-items:flex-end; gap:10px; margin-top:14px; }
.conversation-toolbar label { flex:1 1 250px; min-width:210px; }
.conversation-filter-count { align-self:center; color:var(--muted); font-size:12px; }

/* ---------- 招聘工作台外壳 ---------- */
.workspace-panel { grid-column:1 / -1; }
.workspace-intro {
  display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-top:14px;
  padding:11px 14px; border:1px solid var(--accent-line); border-radius:var(--radius-sm); background:var(--accent-soft);
  color:var(--text-soft); font-size:12.5px;
}
.workspace-intro strong { color:var(--text); font-size:13px; }
.workspace-view-switcher {
  display:flex; flex-wrap:wrap; gap:4px; margin-top:15px; padding:4px;
  border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-strong);
}
.workspace-view-switcher button { min-height:32px; padding:0 14px; color:var(--muted); background:transparent; border-color:transparent; box-shadow:none; }
.workspace-view-switcher button:hover:not(:disabled) { color:var(--text); background:var(--panel); border-color:transparent; }
.workspace-view-switcher button[aria-selected="true"] { color:var(--accent-dark); background:var(--panel); border-color:var(--accent-line); box-shadow:var(--shadow-sm); }
[data-workspace-view-content].workspace-view-hidden { display:none !important; }
/* ---------- 工作台内容块 ---------- */
.workspace-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin-top:16px; }
.workspace-block { min-width:0; padding-top:14px; border-top:1px solid var(--line); }
.workspace-block h3 { margin-bottom:2px; }
.workspace-list { display:grid; gap:8px; margin-top:12px; }
.assessment-workbench { grid-column:1 / -1; }
/* 候选人列表是 HR 高频浏览面，刻意占更宽的列；评分解释保持固定的可阅读宽度。 */
.assessment-layout { display:grid; grid-template-columns:minmax(0,1.45fr) minmax(340px,.95fr); gap:16px; margin-top:14px; }
.assessment-layout > .workspace-list { align-content:start; min-width:0; margin-top:0; }
.assessment-layout > .workspace-list:first-child { min-height:320px; }
.assessment-detail-row { display:grid; gap:3px; padding:8px 0; border-top:1px solid var(--line); }
.assessment-detail-row strong { font-size:12px; }
.assessment-detail-row span { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.rejection-statistics { display:grid; gap:8px; margin-top:16px; padding:13px 14px; border:1px solid var(--line); border-left:3px solid var(--warn); border-radius:var(--radius-sm); background:var(--panel-soft); }
.rejection-reason-row { display:grid; grid-template-columns:minmax(120px,1fr) minmax(80px,2fr) auto; gap:10px; align-items:center; color:var(--muted); font-size:12px; }
.rejection-reason-bar { height:8px; overflow:hidden; border-radius:999px; background:var(--line); }
.rejection-reason-bar span { display:block; height:100%; border-radius:inherit; background:var(--warn); }
.score-group-totals { display:flex; flex-wrap:wrap; gap:7px; margin-top:8px; }
.score-group-total { display:inline-flex; gap:5px; align-items:center; padding:4px 9px; border:1px solid var(--line); border-radius:999px; color:var(--muted); background:var(--panel); font-size:12px; }
.score-group-total strong { color:var(--text); font-variant-numeric:tabular-nums; }
.workspace-row { display:grid; gap:5px; padding:11px 13px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft); }
.workspace-row strong { overflow-wrap:anywhere; font-size:13px; }
.workspace-meta { color:var(--muted); font-size:12.5px; overflow-wrap:anywhere; }
.criteria-preview { display:grid; gap:6px; margin-top:12px; }
.criteria-preview div { color:var(--muted); font-size:12.5px; }
.criteria-preview strong { color:var(--text); }
/* 岗位标准是"标签 + 取值"的成对信息，压成两列比一堆等高卡片更好扫读。 */
.criteria-preview .workspace-row { grid-template-columns:98px minmax(0,1fr); align-items:baseline; gap:4px 12px; padding:8px 11px; }
.copy-button { justify-self:start; min-height:32px; padding:0 11px; }
.review-banner { border-left:3px solid var(--warn); }
.stage-pill {
  display:inline-flex; width:max-content; align-items:center; min-height:22px; padding:1px 9px; border:1px solid var(--accent-line);
  border-radius:999px; color:var(--accent-dark); background:var(--accent-soft); font-size:11.5px; font-weight:700;
}
.stage-details { margin-top:8px; border-top:1px dashed var(--line-strong); padding-top:8px; }
.stage-details summary { color:var(--info); font-size:12px; font-weight:600; }
.stage-details summary:hover { color:var(--accent-dark); }
.stage-fields { display:grid; gap:8px; margin-top:9px; }
.stage-fields input,.stage-fields select { min-height:32px; font-size:12.5px; }
.candidate-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:2px; }
.candidate-actions button { min-height:32px; padding:0 11px; }
.candidate-profile { color:var(--text-soft); }
.candidate-timeline { display:grid; gap:4px; margin-top:8px; padding-left:12px; border-left:2px solid var(--line-strong); }
.candidate-timeline span { color:var(--muted); font-size:12px; }
.communication-timeline { display:grid; gap:7px; margin-top:8px; }
.communication-row { display:grid; gap:2px; padding:8px 11px; border-left:2px solid var(--info); border-radius:0 var(--radius-xs) var(--radius-xs) 0; background:var(--info-soft); }
.communication-row strong { font-size:12.5px; }
.communication-row span { color:var(--text-soft); font-size:12px; overflow-wrap:anywhere; }

/* ---------- 漏斗与闭环进度 ---------- */
.pipeline-summary { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin-top:16px; }
/* 漏斗汇总里的整段说明不能被塞进 6 列网格的某一格，否则文字会挤成竖条。 */
.pipeline-summary > .pipeline-terminal-summary,.pipeline-summary > .pipeline-totals,.pipeline-summary > .funnel-metrics { grid-column:1 / -1; }
.pipeline-step-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pipeline-step-count { margin-top:3px; font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
.pipeline-step.is-active { border-color:var(--accent-line); background:var(--accent-soft); }
.pipeline-step.is-terminal { opacity:.75; }
.pipeline-terminal-summary { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:10px; }
.pipeline-terminal-label { color:var(--muted); font-size:11.5px; font-weight:700; }
.pipeline-terminal { display:inline-flex; gap:6px; align-items:center; padding:4px 10px; border:1px solid var(--line-strong); border-radius:999px; color:var(--muted); background:var(--panel); font-size:12px; }
.pipeline-terminal strong { color:var(--text); font-size:13px; font-variant-numeric:tabular-nums; }
.pipeline-totals { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; color:var(--muted); font-size:12px; }
.workflow-progress { margin-top:16px; padding:14px 16px; border:1px solid var(--info-line); border-radius:var(--radius-sm); background:var(--info-soft); }
.workflow-progress-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
.workflow-progress-header > div { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
.workflow-progress-header strong { font-size:13.5px; }
.workflow-progress-header span { color:var(--info); font-size:12.5px; }
.workflow-steps { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; margin-top:12px; }
.workflow-step { min-width:0; padding:9px 11px; border:1px solid var(--line); border-radius:var(--radius-xs); background:var(--panel); }
.workflow-step.current { border-color:var(--accent-line); background:var(--accent-soft); }
.workflow-step.complete { border-color:var(--ok-line); }
.workflow-step-label { font-size:12px; font-weight:700; }
.workflow-step-description { margin-top:3px; color:var(--muted); font-size:11px; overflow-wrap:anywhere; }
.workflow-step-status { margin-top:6px; color:var(--muted); font-size:11px; }
.workflow-step.current .workflow-step-status { color:var(--accent-dark); font-weight:600; }
/* ---------- 候选人队列与待办 ---------- */
.candidate-queue {
  display:grid; gap:10px; margin-top:16px; padding:14px; border:1px solid var(--line-strong);
  border-radius:var(--radius-sm); background:var(--panel-strong);
}
.candidate-queue-header { display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; gap:10px; }
.candidate-queue-header p { margin:3px 0 0; color:var(--muted); font-size:12px; }
.candidate-queue-tools { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.candidate-queue-tools input,.candidate-queue-tools select { width:auto; min-width:158px; min-height:32px; font-size:12.5px; }
.candidate-queue-list { display:grid; gap:7px; }
.candidate-queue-row {
  display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:center; padding:10px 12px;
  border:1px solid var(--line); border-left:3px solid var(--info); border-radius:var(--radius-xs); background:var(--panel);
}
.candidate-queue-row.is-next { border-color:var(--accent-line); border-left-color:var(--accent); box-shadow:0 0 0 2px rgba(15,118,110,.1); }
.candidate-queue-row.is-terminal { opacity:.7; border-left-color:var(--line-strong); }
.candidate-queue-row strong { overflow-wrap:anywhere; font-size:13.5px; }
.candidate-queue-action { margin-top:3px; color:var(--info); font-size:12px; overflow-wrap:anywhere; }
.candidate-queue-signal,.candidate-queue-reasons { margin-top:3px; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.queue-priority {
  display:inline-flex; width:max-content; margin-left:7px; padding:1px 8px; border:1px solid var(--accent-line);
  border-radius:999px; color:var(--accent-dark); background:var(--accent-soft); font-size:11px; font-weight:700; vertical-align:middle;
}
.candidate-queue-row button { min-height:32px; padding:0 11px; }
.task-summary { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; color:var(--muted); font-size:12.5px; }
.task-list { display:grid; gap:8px; margin-top:12px; }
.task-row {
  display:grid; gap:8px; padding:12px 13px; border:1px solid var(--line); border-left:3px solid var(--info);
  border-radius:var(--radius-sm); background:var(--panel-soft);
}
.task-row.is-next { border-color:var(--accent-line); border-left-color:var(--accent); background:var(--panel); box-shadow:0 0 0 2px rgba(15,118,110,.1); }
.task-row.is-done { opacity:.72; border-left-color:var(--line-strong); }
.task-title { font-size:13.5px; font-weight:700; overflow-wrap:anywhere; }
.task-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.task-actions button { min-height:32px; padding:0 11px; }
.task-actions textarea { min-width:min(100%,340px); min-height:58px; }
.task-due { color:var(--warn); font-size:12px; font-weight:600; }

/* ---------- 评估证据与复盘 ---------- */
.funnel-metrics,.score-breakdown { display:grid; gap:7px; margin-top:11px; padding:11px 13px; border:1px solid var(--line); border-radius:var(--radius-xs); background:var(--panel-soft); }
.score-breakdown { border-left:2px solid var(--accent); }
.funnel-metrics-heading { color:var(--muted); font-size:11.5px; font-weight:700; letter-spacing:.03em; }
.funnel-metrics-row { display:flex; flex-wrap:wrap; gap:6px 14px; color:var(--muted); font-size:12px; }
.funnel-metrics-row strong { color:var(--text); font-variant-numeric:tabular-nums; }
.score-breakdown-row { display:grid; grid-template-columns:minmax(104px,1fr) auto auto; gap:8px; color:var(--muted); font-size:12px; }
.score-breakdown-row strong { color:var(--text); }
.citation-list { display:grid; gap:8px; margin-top:11px; }
.citation-row { display:grid; gap:4px; padding:9px 11px; border-left:2px solid var(--info); border-radius:0 var(--radius-xs) var(--radius-xs) 0; background:var(--info-soft); }
.citation-row strong { overflow-wrap:anywhere; font-size:12.5px; }
.insight-group { display:grid; gap:7px; margin-top:12px; padding:12px 13px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft); }
.insight-group-title { color:var(--text); font-size:12.5px; font-weight:700; }
.insight-item { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 10px; align-items:center; padding:7px 0; border-top:1px solid var(--line); }
.insight-item:first-of-type { border-top:0; }
.insight-item strong { min-width:0; overflow-wrap:anywhere; font-size:12.5px; }
.insight-item-meta { color:var(--muted); font-size:11.5px; white-space:nowrap; font-variant-numeric:tabular-nums; }
.insight-bar { grid-column:1 / -1; height:5px; overflow:hidden; border-radius:999px; background:var(--bg-soft); }
.insight-bar-fill { display:block; height:100%; border-radius:999px; background:var(--accent); transition:width 220ms ease; }
.insight-sample-notice { margin-top:12px; }
/* ---------- 批量导出 ---------- */
.batch-progress { display:grid; gap:8px; margin-top:16px; padding:13px 15px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft); }
.batch-progress-summary { font-size:13.5px; font-weight:700; font-variant-numeric:tabular-nums; }
.batch-progress-meta { color:var(--muted); font-size:12px; }
.batch-result-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 12px; align-items:start; padding:10px 12px; border:1px solid var(--line); border-left:3px solid var(--ok); border-radius:var(--radius-xs); background:var(--panel-soft); }
.batch-result-row.is-failed { border-left-color:var(--danger); }
.batch-result-row.is-skipped { border-left-color:var(--line-strong); }
.batch-result-row strong { overflow-wrap:anywhere; font-size:13.5px; }
.batch-result-meta { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.batch-result-path { color:var(--muted); font-size:11.5px; overflow-wrap:anywhere; }
.pdf-badge {
  display:inline-flex; width:max-content; align-items:center; min-height:21px; padding:1px 9px;
  border:1px solid var(--line-strong); border-radius:999px; color:var(--muted); background:var(--panel);
  font-size:11px; font-weight:700; white-space:nowrap;
}
.pdf-badge.can { color:var(--ok); border-color:var(--ok-line); background:var(--ok-soft); }
.pdf-badge.none { color:var(--muted); }
.pdf-badge.unknown { color:var(--info); border-color:var(--info-line); background:var(--info-soft); }
.pdf-badge.failed { color:var(--danger); border-color:var(--danger-line); background:var(--danger-soft); }
.conversation-badges { display:flex; flex-wrap:wrap; gap:6px; margin-top:5px; }


/* ---------- 流水线日志查看器 ---------- */
.pipeline-log-viewer {
  max-height:520px; overflow-y:auto; margin-top:14px; padding:12px;
  border:1px solid var(--line-strong); border-radius:var(--radius-sm);
  background:#0d1b2a; color:#ccd6e0; font-family:ui-monospace,Consolas,monospace;
  font-size:12.5px; line-height:1.6;
}
@media (prefers-color-scheme:light) {
  .pipeline-log-viewer { background:#f0f3f8; color:#1a2740; }
}
.pipeline-log-entry {
  display:grid; grid-template-columns:56px 58px 90px 80px minmax(0,1fr); gap:6px;
  padding:4px 0; border-bottom:1px solid rgba(255,255,255,.06);
  align-items:baseline; cursor:default;
}
.pipeline-log-entry:last-child { border-bottom:none; }
.pipeline-log-ts { color:#5c7a9e; font-size:11px; white-space:nowrap; }
.pipeline-log-step { color:#7ba0c9; font-size:11px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pipeline-log-candidate { color:#9bb5d4; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pipeline-log-level {
  font-size:10.5px; font-weight:800; text-align:center; white-space:nowrap;
  padding:1px 5px; border-radius:3px; text-transform:uppercase;
}
.pipeline-log-level.ai_input { color:#1a0; background:rgba(34,170,0,.15); }
.pipeline-log-level.ai_output { color:#08f; background:rgba(0,136,255,.15); }
.pipeline-log-level.info { color:#6a8; background:rgba(102,170,136,.15); }
.pipeline-log-level.warn { color:#da0; background:rgba(221,170,0,.15); }
.pipeline-log-level.error { color:#d44; background:rgba(221,68,68,.15); }
.pipeline-log-label { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pipeline-log-detail { grid-column:1/-1; color:#667a94; font-size:11.5px; padding:4px 0 4px 8px; overflow-wrap:anywhere; max-height:4.6em; overflow-y:hidden; }
.pipeline-log-entry.expanded .pipeline-log-detail { max-height:none; }
.pipeline-log-toggle { grid-column:1/-1; color:#4477aa; font-size:11px; cursor:pointer; padding:2px 0 0 8px; }
.pipeline-log-toggle:hover { color:#88bbee; }
.pipeline-controls { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin-top:14px; }
.pipeline-controls label { flex:0 1 140px; min-width:100px; }
.pipeline-summary-text { margin-top:12px; color:var(--text-soft); font-size:13px; }

/* ---------- 锚点与响应式 ---------- */
/* 页面首个卡片留出页头高度，深链进来时标题和说明不会被顶栏与页头挤掉。 */
#login-panel,#conversation-panel,#recommendation-panel,#batch-export-panel,#pipeline-panel,#resume-download-panel,#recruiting-workspace,#guide-panel { scroll-margin-top:calc(var(--topbar-h) + 92px); }
#mode-panel,#loop-panel,#pacing-panel { scroll-margin-top:calc(var(--topbar-h) + 20px); }
@media (max-width:1180px) { .grid { grid-template-columns:minmax(0,1fr); } .pipeline-summary { grid-template-columns:repeat(3,minmax(0,1fr)); } .workflow-steps { grid-template-columns:repeat(3,minmax(0,1fr)); } }
/* 移动断点：固定侧栏必须退化成单列横向导航，否则主内容会被推出视口。 */
@media (max-width:900px) { .app-layout { grid-template-columns:1fr; } .side-nav { position:static; display:flex; align-items:center; gap:4px; overflow-x:auto; padding:7px; box-shadow:none; } .side-nav-heading { flex:0 0 auto; padding:5px 8px; } .side-nav-link { flex:0 0 auto; white-space:nowrap; } .side-nav-foot { display:none; } #login-panel,#mode-panel,#loop-panel,#pacing-panel,#conversation-panel,#recommendation-panel,#batch-export-panel,#resume-download-panel,#recruiting-workspace,#guide-panel { scroll-margin-top:16px; } }
@media (max-width:760px) {
  .topbar { position:relative; flex-wrap:wrap; gap:12px; padding:12px 16px; }
  .topbar-meta { justify-content:flex-start; }
  .context-picker { min-width:100%; }
  .shell { padding:18px 16px 36px; }
  .panel { padding:15px 16px; }
  .status,.workspace-intro { display:grid; }
  .workspace-grid,.assessment-layout { grid-template-columns:1fr; }
  .pacing-metrics,.loop-summary-grid,.pipeline-summary,.workflow-steps { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .score-breakdown-row { grid-template-columns:1fr auto; }
  .score-breakdown-row span { grid-column:1 / -1; }
  .badge { justify-self:start; }
  .page-head-title .page-title { font-size:19px; }
}
@media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } *,*::before,*::after { transition:none !important; } }

/* ---------- 仪表盘与指标 ---------- */
.dashboard-grid { display:grid; gap:18px; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); }
.dashboard-grid.wide { grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }
.metric-card {
  display:flex; flex-direction:column; gap:8px; padding:18px 20px; border-radius:var(--radius);
  background:var(--panel); border:1px solid var(--line); box-shadow:var(--shadow-sm);
}
.metric-card-header { display:flex; align-items:center; gap:10px; }
.metric-card-icon {
  display:grid; place-items:center; width:38px; height:38px; border-radius:var(--radius-sm);
  background:var(--panel-soft); font-size:18px; flex-shrink:0;
}
.metric-card-value { font-size:28px; font-weight:800; letter-spacing:-.02em; line-height:1.1; }
.metric-card-label { color:var(--muted); font-size:12.5px; font-weight:600; }
.metric-card-sub { color:var(--text-soft); font-size:12px; margin-top:2px; }
.metric-trend { display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:700; margin-top:4px; }
.metric-trend.up { color:var(--ok); }
.metric-trend.down { color:var(--danger); }
.metric-trend.neutral { color:var(--muted); }
/* 漏斗 */
.funnel { display:flex; flex-direction:column; gap:6px; padding:12px 0; }
.funnel-row { display:flex; align-items:center; gap:12px; }
.funnel-label { width:110px; text-align:right; color:var(--text-soft); font-size:12.5px; font-weight:600; flex-shrink:0; }
.funnel-bar-wrap { flex:1; position:relative; height:28px; display:flex; align-items:center; }
.funnel-bar {
  height:100%; border-radius:var(--radius-xs); background:var(--accent);
  display:flex; align-items:center; padding-left:10px; min-width:fit-content;
  transition:width 400ms ease;
}
.funnel-bar.secondary-bar { background:var(--info); }
.funnel-bar.warn-bar { background:var(--warn); }
.funnel-count { color:#fff; font-size:12px; font-weight:700; white-space:nowrap; }
.funnel-rate { font-size:11px; color:var(--muted); margin-left:8px; flex-shrink:0; }
/* 模板管理 */
.template-list { display:flex; flex-direction:column; gap:10px; }
.template-card {
  display:flex; flex-direction:column; gap:10px; padding:16px; border-radius:var(--radius-sm);
  border:1px solid var(--line); background:var(--panel-soft);
}
.template-card-header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.template-key { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:var(--accent); font-weight:700; }
.template-body { color:var(--text-soft); font-size:13px; line-height:1.6; padding:8px 12px; background:var(--panel); border-radius:var(--radius-xs); }
.template-stats { display:flex; gap:16px; flex-wrap:wrap; }
.template-stat { display:flex; flex-direction:column; gap:2px; }
.template-stat-value { font-size:15px; font-weight:700; }
.template-stat-label { font-size:11px; color:var(--muted); }
.template-actions { display:flex; gap:6px; }
/* 复盘洞察卡片 */
.insight-card {
  padding:16px 18px; border-radius:var(--radius-sm); border-left:4px solid;
  background:var(--panel-soft); display:flex; flex-direction:column; gap:8px;
}
.insight-card.high { border-left-color:var(--danger); }
.insight-card.medium { border-left-color:var(--warn); }
.insight-card.low { border-left-color:var(--info); }
.insight-card-header { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.insight-card-title { font-weight:700; font-size:14px; }
.insight-card-reason { color:var(--text-soft); font-size:13px; }
.insight-card-action { color:var(--accent); font-size:13px; font-weight:600; }
/* 进度分段条 */
.segmented-bar { display:flex; height:10px; border-radius:5px; overflow:hidden; gap:2px; }
.segmented-bar .seg { height:100%; border-radius:1px; }
.seg.ok { background:var(--ok); }
.seg.warn { background:var(--warn); }
.seg.danger { background:var(--danger); }
.seg.muted { background:var(--line-strong); }
/* 视图切换标签增强 */
.workspace-view-switcher { display:flex; gap:2px; margin-bottom:16px; border-bottom:2px solid var(--line); padding-bottom:0; }
.workspace-view-switcher button {
  padding:10px 18px; border:none; border-radius:var(--radius-sm) var(--radius-sm) 0 0;
  background:transparent; color:var(--muted); font-weight:600; font-size:13px;
  box-shadow:none; transition:color 150ms,background 150ms; cursor:pointer;
}
.workspace-view-switcher button[aria-selected="true"] { color:var(--accent); background:var(--accent-soft); }
.workspace-view-switcher button:hover:not([aria-selected="true"]) { color:var(--text); }
/* 闭环进度步骤 */
.progress-steps { display:flex; align-items:flex-start; gap:0; flex-wrap:wrap; }
.progress-step {
  display:flex; flex-direction:column; align-items:center; gap:6px; flex:1; min-width:60px;
  position:relative; padding-top:28px;
}
.progress-step::before {
  content:''; position:absolute; top:11px; left:50%; width:100%; height:2px;
  background:var(--line-strong); z-index:0;
}
.progress-step:first-child::before { left:50%; width:50%; }
.progress-step:last-child::before { width:50%; left:0; }
.progress-dot {
  width:24px; height:24px; border-radius:50%; background:var(--line-strong);
  display:grid; place-items:center; color:#fff; font-size:11px; font-weight:800;
  z-index:1; position:relative;
}
.progress-dot.done { background:var(--ok); }
.progress-dot.active { background:var(--accent); box-shadow:0 0 0 4px var(--accent-soft); }
.progress-label { font-size:11px; text-align:center; color:var(--muted); font-weight:600; }
.progress-label.done,.progress-label.active { color:var(--text); }
/* 闭环卡片特殊样式 */
.stage-card {
  padding:14px 18px; border-radius:var(--radius-sm); background:var(--panel-soft);
  border:1px solid var(--line); display:flex; flex-direction:column; gap:6px;
  cursor:pointer; transition:border-color 150ms,box-shadow 150ms;
}
.stage-card:hover { border-color:var(--accent); box-shadow:var(--shadow-sm); }
.stage-card .stage-card-count { font-size:26px; font-weight:800; }
.stage-card .stage-card-label { font-size:12px; color:var(--muted); }

/* ---------- 招聘工作台：岗位管理与候选人双模块 ---------- */
.workspace-module-note { margin:12px 0 4px; padding:12px 14px; border:1px solid var(--info-line); border-radius:var(--radius-sm); background:var(--info-soft); color:var(--text-soft); font-size:13px; line-height:1.65; }
.workspace-module-note strong { color:var(--text); }
.job-management-block { padding:16px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel); }
.job-management-dashboard { margin-top:16px; }
.job-management-dashboard-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; }
.job-management-dashboard-header h3 { margin:0; }
.job-management-dashboard-header p { margin:4px 0 0; color:var(--muted); font-size:12.5px; }
.job-management-dashboard-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.job-management-dashboard-actions button { min-height:34px; }
.job-card-list { display:grid; grid-template-columns:repeat(auto-fill,minmax(245px,1fr)); gap:12px; margin-top:14px; }
.job-card { display:grid; gap:10px; min-width:0; padding:15px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel); box-shadow:var(--shadow-sm); }
.job-card.is-ready { border-top:3px solid var(--accent); }
.job-card.is-incomplete { border-top:3px solid var(--warn); }
.job-card-header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
.job-card-title { margin:0; color:var(--text); font-size:15px; line-height:1.35; overflow-wrap:anywhere; }
.job-card-status { display:inline-flex; width:max-content; align-items:center; min-height:22px; padding:1px 8px; border-radius:999px; background:var(--accent-soft); color:var(--accent-dark); font-size:11.5px; font-weight:700; }
.job-card.is-incomplete .job-card-status { background:var(--warn-soft); color:var(--warn); }
.job-card-progress { height:4px; overflow:hidden; border-radius:999px; background:var(--line); }
.job-card-progress span { display:block; height:100%; border-radius:inherit; background:var(--accent); }
.job-card.is-incomplete .job-card-progress span { background:var(--warn); }
.job-card-meta { color:var(--muted); font-size:12.5px; line-height:1.55; overflow-wrap:anywhere; }
.job-card-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.job-card-actions button { min-height:34px; }
.job-management-editor-toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:16px 0 0; }
.job-management-editor-toolbar span { color:var(--muted); font-size:12.5px; }
.job-management-dashboard-hidden,.job-management-editor-hidden { display:none !important; }
.candidate-management-block { min-width:0; }
.candidate-assessment-detail { border-left:3px solid var(--accent-line); }
.candidate-follow-up { margin-top:10px; padding:8px 10px; border:1px solid var(--line); border-radius:var(--radius-sm); background:var(--panel-soft); }
.candidate-follow-up summary { cursor:pointer; color:var(--text-soft); font-size:12px; font-weight:700; }
.candidate-follow-up[open] summary { margin-bottom:8px; }

/* ---------- 招聘工作台独立壳层 ---------- */
/*
 * 招聘是高频、连续的操作场景，不能再沿用全量运维控制台的长导航。
 * 该壳层只重构展示和导航，原有表单节点、事件绑定与本地 API 均保持不变。
 */
.recruiting-ui-shell { background:#f0fbfa; }
.recruiting-ui-shell .topbar,
.recruiting-ui-shell .legacy-console-hidden { display:none !important; }
.recruiting-ui-shell .shell { max-width:1120px; padding:18px 20px 44px; }
.recruiting-workbench-heading { max-width:1120px; margin:0 auto; padding:18px 20px 4px; }
.recruiting-workbench-heading h1 { margin:0; color:#133a49; font-size:22px; line-height:1.35; }
.recruiting-workbench-heading p { margin:4px 0 0; color:#517182; font-size:13px; }
.recruiting-ui-shell .app-layout { grid-template-columns:132px minmax(0,1fr); gap:0; overflow:hidden; border:1px solid #d6e1e8; border-radius:6px; background:#f8fbfd; box-shadow:none; }
.recruiting-workbench-nav { display:grid; align-content:start; min-height:318px; padding:12px 0; background:#172638; }
.recruiting-workbench-nav h2 { margin:0 14px 10px; color:#fff; font-size:15px; }
.recruiting-workbench-nav button { width:100%; min-height:36px; padding:0 14px; border:0; border-radius:0; color:#e6eef4; background:transparent; box-shadow:none; text-align:left; font-size:13px; font-weight:700; }
.recruiting-workbench-nav button:hover:not(:disabled),.recruiting-workbench-nav button[aria-current="page"] { color:#fff; background:#236863; }
.recruiting-workbench-nav button:focus-visible { outline:2px solid #8ee5d9; outline-offset:-3px; }
.recruiting-resume-source-switcher { display:flex; flex-wrap:wrap; gap:7px; margin:14px 16px 0; padding:10px; border:1px solid #d6e1e8; border-radius:5px; background:#fff; }
.recruiting-resume-source-switcher[hidden] { display:none; }
.recruiting-resume-source-switcher button { min-height:30px; padding:0 10px; border-radius:4px; color:#355466; background:#f6fafc; border-color:#cbd9e2; font-size:12px; }
.recruiting-resume-source-switcher button:hover:not(:disabled),.recruiting-resume-source-switcher button[aria-current="page"] { color:#fff; background:#236863; border-color:#236863; }
.recruiting-ui-shell .workarea { min-height:318px; padding:0; background:#f8fbfd; }
.recruiting-ui-shell .route-page > .page-head { display:none; }
.recruiting-ui-shell .route-page[data-route-page="workspace"] { display:block; min-height:318px; }
.recruiting-ui-shell #recruiting-workspace > .status,
.recruiting-ui-shell #recruiting-workspace > .workspace-view-switcher { display:none; }
.recruiting-ui-shell .route-page[data-route-page="workspace"] > .grid { display:block; }
.recruiting-ui-shell #recruiting-workspace { min-height:318px; padding:18px 16px; border:0; border-radius:0; background:transparent; box-shadow:none; }
.recruiting-ui-shell #recruiting-action-state { margin:0 0 10px; }
.recruiting-ui-shell .job-management-dashboard { margin-top:0; }
.recruiting-ui-shell .job-management-dashboard-header { margin-bottom:12px; }
.recruiting-ui-shell .job-management-dashboard-header h3 { font-size:17px; }
.recruiting-ui-shell .job-management-dashboard-header p { display:none; }
.recruiting-ui-shell .job-management-dashboard-actions button { border-radius:4px; }
.recruiting-ui-shell .job-card-list { grid-template-columns:repeat(auto-fit,minmax(186px,1fr)); max-width:760px; gap:12px; margin-top:0; }
.recruiting-ui-shell .job-card { gap:9px; padding:11px; border-radius:5px; box-shadow:none; }
.recruiting-ui-shell .job-card-title { font-size:14px; }
.recruiting-ui-shell .job-card-actions button { border-radius:4px; }
.recruiting-ui-shell .job-management-editor-toolbar { margin-top:0; }
.recruiting-ui-shell .job-management-block { border-radius:5px; box-shadow:none; }
@media (max-width:700px) {
  .recruiting-ui-shell .shell { padding:12px; }
  .recruiting-workbench-heading { padding:14px 12px 4px; }
  .recruiting-workbench-heading h1 { font-size:19px; }
  .recruiting-ui-shell .app-layout { grid-template-columns:1fr; overflow:visible; }
  .recruiting-workbench-nav { display:flex; min-height:0; overflow-x:auto; padding:7px; }
  .recruiting-workbench-nav h2 { display:none; }
  .recruiting-workbench-nav button { flex:0 0 auto; width:auto; padding:0 11px; }
  .recruiting-resume-source-switcher { margin:10px 12px 0; }
  .recruiting-ui-shell #recruiting-workspace { padding:14px 12px; }
}

/* ---------- 深色模式：只替换令牌，组件规则保持同一份 ---------- */
@media (prefers-color-scheme:dark) {
  :root {
    color-scheme: dark;
    --bg:#0c1420; --bg-soft:#131f2e; --panel:#111c29; --panel-soft:#0e1825; --panel-strong:#182635;
    --line:#22344a; --line-strong:#2e4459;
    --text:#e8eff7; --text-soft:#b6c6d6; --muted:#8ba0b6;
    --accent:#2dd4a7; --accent-dark:#5ce0bc; --accent-soft:#10312c; --accent-line:#2f6f61;
    --info:#8ab4ff; --info-soft:#132339; --info-line:#2d4a72;
    --warn:#f7c469; --warn-soft:#2f2413; --warn-line:#6b5426;
    --danger:#ff9a90; --danger-soft:#2e1815; --danger-line:#733a34;
    --ok:#6fe0af; --ok-soft:#0f2c23; --ok-line:#2b6b53;
    --shadow-sm:0 1px 2px rgba(0,0,0,.4); --shadow:0 10px 28px rgba(0,0,0,.45);
    --ring:0 0 0 3px rgba(138,180,255,.4);
  }
  .topbar { background:rgba(12,20,32,.9); }
  button { color:#062018; }
  .secondary,.secondary-link { color:var(--text-soft); }
  .brand-mark { color:#062018; background:linear-gradient(140deg,#2dd4a7,#1c9c7c); }
  .skip-link { color:#0c1420; background:var(--text); }
}
"""


# 新工作台覆盖旧控制台的可见界面。旧模块保留在 DOM 中仅用于兼容仍由后端维护的
# 高级入口和既有自动化测试；用户默认只会看到下面五个按照工作流拆分的模块。
_PRODUCT_WORKBENCH_CSS = """
body > .topbar, body > .shell, body > .skip-link { display:none; }
#recruiting-product-shell { min-height:100vh; background:#f5f7fb; color:#182235; }
.product-topbar { height:64px; padding:0 30px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #e2e8f0; background:#fff; }
.product-brand { display:flex; align-items:center; gap:10px; font-weight:700; font-size:16px; }
.product-brand-mark { width:28px; height:28px; display:grid; place-items:center; color:#fff; background:#0f766e; border-radius:6px; font-size:12px; }
.product-status { color:#64748b; font-size:12px; }
.product-layout { max-width:1440px; min-height:calc(100vh - 64px); margin:0 auto; display:grid; grid-template-columns:210px minmax(0,1fr); }
.product-nav { padding:18px 12px; border-right:1px solid #e2e8f0; background:#fff; }
.product-nav button { width:100%; justify-content:flex-start; padding:0 12px; color:#475569; background:transparent; border-color:transparent; box-shadow:none; }
.product-nav button:hover, .product-nav button[aria-current="page"] { color:#0f766e; background:#e7f4f1; border-color:#cceae4; }
.product-main { min-width:0; padding:30px 34px 52px; }
.product-view { display:none; max-width:1120px; }
.product-view.active { display:block; }
.product-head { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:24px; }
.product-head h2 { font-size:22px; line-height:1.25; }
.product-head p { margin-top:6px; color:#64748b; }
.product-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
.product-card { border:1px solid #e2e8f0; border-radius:8px; padding:18px; background:#fff; box-shadow:0 1px 2px rgba(15,23,42,.03); }
.product-card h3 { margin-bottom:8px; font-size:14px; }
.product-card p { color:#64748b; font-size:13px; }
.product-card .value { margin-top:8px; font-size:24px; font-weight:700; color:#172033; }
.product-panel { margin-top:16px; border:1px solid #e2e8f0; border-radius:8px; background:#fff; }
.product-panel-head { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:16px 18px; border-bottom:1px solid #e9eef5; }
.product-panel-head h3 { font-size:15px; }
.product-panel-body { padding:18px; }
.agent-input { min-height:128px; margin:0; resize:vertical; }
.agent-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:12px; }
.product-schedule-summary { min-height:38px; margin:0 0 12px; padding:9px 12px; border:1px solid #cceae4; border-radius:6px; color:#175f59; background:#f0faf8; font-size:13px; line-height:1.5; }
.job-list, .candidate-list, .followup-list, .resume-list { display:grid; gap:10px; }
.candidate-job-group + .candidate-job-group { margin-top:22px; }
.candidate-job-group > h3 { margin:0 0 6px; font-size:15px; color:#183b47; }
.job-row, .candidate-row, .followup-row, .resume-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; padding:14px 0; border-bottom:1px solid #edf1f6; }
.job-row:last-child, .candidate-row:last-child, .followup-row:last-child, .resume-row:last-child { border-bottom:0; }
.row-title { font-weight:700; }
.row-meta { margin-top:3px; color:#64748b; font-size:12px; }
.row-actions { display:flex; gap:7px; align-items:center; }
.row-actions button { min-height:32px; padding:0 10px; font-size:12px; }
.product-empty { padding:22px 0; color:#64748b; font-size:13px; }
.product-notice { min-height:1.4em; margin-top:10px; color:#475569; font-size:13px; }
.product-notice.error { color:#b42318; }
.product-chip { display:inline-flex; min-height:24px; align-items:center; padding:2px 8px; border-radius:999px; color:#0f766e; background:#e7f4f1; font-size:12px; font-weight:600; }
.product-filter { width:260px; max-width:100%; min-height:34px; }
.product-filter-label { display:flex; align-items:center; gap:8px; color:#64748b; font-size:13px; }
.product-filter-label .product-filter { width:220px; }
.product-dialog { width:min(620px,calc(100vw - 32px)); border:0; border-radius:8px; padding:0; box-shadow:0 18px 48px rgba(15,23,42,.24); }
.product-dialog::backdrop { background:rgba(15,23,42,.36); }
.product-dialog form { padding:20px; }
.product-dialog h3 { margin:0 0 16px; font-size:16px; }
.product-dialog .form { margin:0; }
.product-dialog menu { display:flex; justify-content:flex-end; gap:8px; padding:0; margin:18px 0 0; }
.product-dialog menu button { margin:0; }
.product-dialog textarea { min-height:140px; }
.product-dialog .online-resume-text { max-height:min(62vh,680px); overflow:auto; margin:0; padding:12px; border:1px solid #e2e8f0; border-radius:6px; background:#f8fafc; color:#1e293b; font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.product-toast { position:fixed; z-index:140; right:24px; bottom:24px; max-width:min(360px,calc(100vw - 32px)); padding:11px 14px; border:1px solid #bbf7d0; border-radius:6px; color:#14532d; background:#f0fdf4; box-shadow:0 12px 28px rgba(15,23,42,.16); font-size:13px; line-height:1.5; opacity:0; pointer-events:none; transform:translateY(8px); transition:opacity .18s ease,transform .18s ease; }
.product-toast.visible { opacity:1; transform:translateY(0); }
.product-toast.error { border-color:#fecaca; color:#991b1b; background:#fef2f2; }
@media (max-width:900px) { .product-layout { grid-template-columns:1fr; } .product-nav { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:4px; border-right:0; border-bottom:1px solid #e2e8f0; padding:9px 12px; } .product-nav button { width:100%; min-width:0; padding:0 8px; } .product-main { padding:22px 16px 40px; } .product-grid { grid-template-columns:1fr; } .product-head { display:block; } .product-head .actions { margin-top:12px; } .job-row, .candidate-row, .followup-row, .resume-row { grid-template-columns:1fr; } }
"""


_PRODUCT_WORKBENCH_HTML = """
<header class="product-topbar"><div class="product-brand"><span class="product-brand-mark">B</span>智能招聘工作台</div><div id="product-login-summary" class="product-status">正在读取 RPA 浏览器登录状态</div></header>
<div class="product-layout"><nav class="product-nav" aria-label="招聘工作台模块"><button type="button" data-product-module="login" aria-current="page">登录状态</button><button type="button" data-product-module="jobs">岗位管理</button><button type="button" data-product-module="resumes">获取简历</button><button type="button" data-product-module="automation">BOSS 自动化</button><button type="button" data-product-module="candidates">候选人</button><button type="button" data-product-module="followups">后续跟进</button><button type="button" data-product-module="settings">设置</button></nav>
<main class="product-main">
<section class="product-view active" data-product-view="login"><div class="product-head"><div><h2>RPA 浏览器登录状态</h2><p>这里检查的是项目连接的 RPA Chrome，不是当前工作台浏览器标签。</p></div><button id="product-login-button" type="button">打开 BOSS 登录页</button></div><div class="product-grid"><article class="product-card"><h3>RPA 账号会话</h3><div id="product-login-state" class="value">--</div><p id="product-login-detail">正在检查 RPA 浏览器会话。</p></article><article class="product-card"><h3>运行模式</h3><div id="product-mode-state" class="value">--</div><p>简历下载与平台读取均保留在用户主动操作内。</p></article><article class="product-card"><h3>自动化状态</h3><div id="product-pacing-state" class="value">--</div><p id="product-pacing-detail">正在读取安全节奏。</p></article></div></section>
<section class="product-view" data-product-view="jobs"><div class="product-head"><div><h2>岗位管理</h2><p>岗位名称和硬条件以 BOSS 职位同步为准；自然语言只用于生成补充筛选规则。</p></div><div class="actions"><button id="job-sync-boss-button" type="button">同步 BOSS 职位</button><button id="job-knowledge-button" class="secondary" type="button">知识库</button></div></div><section class="product-panel"><div class="product-panel-head"><h3>AI 招聘标准补充</h3><span id="job-agent-source" class="product-chip">请先选择已同步岗位</span></div><div class="product-panel-body"><textarea id="job-standard-input" class="agent-input" placeholder="选择岗位后，输入补充要求。例如：电话销售经验优先，能吃苦，接受单休，招商加盟经验优先，不要频繁跳槽。"></textarea><div class="agent-actions"><button id="job-standard-submit" type="button">AI 分析生成规则</button><span id="job-standard-notice" class="product-notice" aria-live="polite"></span></div></div></section><section class="product-panel"><div class="product-panel-head"><h3>岗位列表</h3><span class="product-status">点击编辑岗位，查看 BOSS 信息并审核 AI 规则</span></div><div class="product-panel-body"><div id="product-job-list" class="job-list"></div></div></section></section>
<section class="product-view" data-product-view="resumes"><div class="product-head"><div><h2>在线简历</h2><p>刷新后读取 BOSS 沟通候选人；点击候选人右侧按钮即可在本平台查看其在线简历。</p></div><div class="actions"><button id="resume-refresh-button" class="secondary" type="button">刷新沟通列表</button><button id="resume-local-scan-button" type="button">分析本地简历</button></div></div><section class="product-panel"><div class="product-panel-head"><h3>沟通候选人</h3><div class="actions"><label class="product-filter-label">岗位<select id="resume-position-filter" class="product-filter" aria-label="筛选沟通候选人岗位"><option value="all">全部岗位</option></select></label><span id="resume-list-state" class="product-status">未读取</span></div></div><div class="product-panel-body"><p class="product-notice">刷新只同步沟通候选人，不会发送消息、索要附件或下载附件。</p><div id="product-resume-list" class="resume-list"></div><p id="resume-action-notice" class="product-notice" aria-live="polite"></p></div></section></section>
<section class="product-view" data-product-view="automation"><div class="product-head"><div><h2>BOSS 自动化</h2><p>这里负责同步、硬筛、AI 对话和附件流程。硬性不匹配的候选人直接跳过；附件终审完成后会进入“候选人”模块。</p></div><label>当前岗位<select id="product-automation-job" class="product-filter" aria-label="自动化岗位"></select></label></div><section class="product-panel"><div class="product-panel-head"><h3>自动化控制</h3><span id="product-automation-runtime" class="product-chip">未启动</span></div><div class="product-panel-body"><p id="product-automation-schedule-summary" class="product-schedule-summary" aria-live="polite">请选择岗位查看定时任务时间。</p><div class="agent-actions"><button id="product-automation-sync" type="button">同步沟通列表</button><button id="product-automation-start-conversation" type="button">开始沟通列表自动化</button><button id="product-automation-start-recommendation" type="button">开始推荐牛人自动化</button><button id="product-automation-pause" class="secondary" type="button">暂停</button><button id="product-automation-resume" class="secondary" type="button">继续</button><button id="product-automation-stop" class="secondary" type="button">停止</button></div><p id="product-automation-state" class="product-notice" aria-live="polite">请选择岗位后同步沟通列表。</p><div id="product-automation-activities" class="followup-list" aria-live="polite"></div></div></section><section class="product-panel"><div class="product-panel-head"><h3>当前岗位处理队列</h3><span class="product-status">未回复保留原阶段；点击候选人查看 AI 已处理的对话</span></div><div class="product-panel-body"><div id="product-automation-queue" class="candidate-list" aria-live="polite"></div></div></section><section class="product-panel"><div class="product-panel-head"><h3>候选人与 AI 对话</h3><span class="product-status">仅展示自动化已处理和已确认发送的消息</span></div><div class="product-panel-body"><div id="product-automation-conversation-detail" class="followup-list" aria-live="polite"></div></div></section></section>
<section class="product-view" data-product-view="settings"><div class="product-head"><div><h2>设置</h2><p>面试邀请和两个自动化入口按岗位独立保存。关闭定时任务不会影响手动按钮。</p></div></div><section class="product-panel"><div class="product-panel-head"><h3>约面试设置</h3><span id="product-interview-settings-state" class="product-status">请选择岗位</span></div><div class="product-panel-body"><form id="product-interview-settings-form" class="form"><label>岗位<select id="product-interview-job" name="job_id" required></select></label><label>推荐牛人打招呼语<textarea name="greeting_message" maxlength="100"></textarea></label><label>面试方式<select name="mode"><option value="online">线上面试</option><option value="offline">线下面试</option></select></label><label>面试日期<input name="date" type="date" required></label><label>面试时间<input name="time" type="time" required></label><label>面试地点<input name="address" maxlength="200"></label><label>联系人<input name="contact_name" maxlength="60"></label><label>联系人电话<input name="contact_phone" maxlength="32"></label><label>备注<textarea name="note" maxlength="500"></textarea></label><button type="submit">保存约面试设置</button></form></div></section><section class="product-panel"><div class="product-panel-head"><h3>沟通列表定时任务</h3><span id="conversation-schedule-state" class="product-status">未配置</span></div><div class="product-panel-body"><form id="conversation-schedule-form" class="form" data-schedule-source="conversation"><label><input id="conversation-schedule-enabled" name="enabled" type="checkbox"> 启用定时任务</label><label>岗位<select name="job_id" required></select></label><label>开始时间<input name="start_time" type="time" value="09:00" required></label><label>结束时间<input name="end_time" type="time" value="18:00" required></label><label>执行间隔（分钟）<input name="interval_minutes" type="number" min="1" max="1440" value="20" required></label><label>单次处理数量<input name="limit" type="number" min="1" max="50" value="20" required></label><label>每日配额<input name="daily_quota" type="number" min="1" max="1000" value="100" required></label><fieldset><legend>执行日</legend><label><input name="weekdays" type="checkbox" value="0">周一</label><label><input name="weekdays" type="checkbox" value="1">周二</label><label><input name="weekdays" type="checkbox" value="2">周三</label><label><input name="weekdays" type="checkbox" value="3">周四</label><label><input name="weekdays" type="checkbox" value="4">周五</label><label><input name="weekdays" type="checkbox" value="5">周六</label><label><input name="weekdays" type="checkbox" value="6">周日</label></fieldset><button type="submit">保存沟通列表定时任务</button></form></div></section><section class="product-panel"><div class="product-panel-head"><h3>推荐牛人定时任务</h3><span id="recommendation-schedule-state" class="product-status">未配置</span></div><div class="product-panel-body"><form id="recommendation-schedule-form" class="form" data-schedule-source="recommendation"><label><input id="recommendation-schedule-enabled" name="enabled" type="checkbox"> 启用定时任务</label><label>岗位<select name="job_id" required></select></label><label>开始时间<input name="start_time" type="time" value="09:00" required></label><label>结束时间<input name="end_time" type="time" value="18:00" required></label><label>执行间隔（分钟）<input name="interval_minutes" type="number" min="1" max="1440" value="60" required></label><label>单次处理数量<input name="limit" type="number" min="1" max="50" value="10" required></label><label>每日配额<input name="daily_quota" type="number" min="1" max="1000" value="30" required></label><fieldset><legend>执行日</legend><label><input name="weekdays" type="checkbox" value="0">周一</label><label><input name="weekdays" type="checkbox" value="1">周二</label><label><input name="weekdays" type="checkbox" value="2">周三</label><label><input name="weekdays" type="checkbox" value="3">周四</label><label><input name="weekdays" type="checkbox" value="4">周五</label><label><input name="weekdays" type="checkbox" value="5">周六</label><label><input name="weekdays" type="checkbox" value="6">周日</label></fieldset><button type="submit">保存推荐牛人定时任务</button></form></div></section></section>
<section class="product-view" data-product-view="candidates"><div class="product-head"><div><h2>候选人</h2><p>仅收录完成附件简历终审且达到岗位分数线的候选人，按岗位分类并按评分从高到低排序。</p></div><select id="candidate-job-filter" class="product-filter" aria-label="筛选岗位"></select></div><div class="product-grid"><article class="product-card"><h3>达标候选人</h3><div id="candidate-total" class="value">0</div></article><article class="product-card"><h3>涉及岗位</h3><div id="candidate-review-count" class="value">0</div></article><article class="product-card"><h3>附件已验证</h3><div id="candidate-rejection-count" class="value">0</div></article></div><section class="product-panel"><div class="product-panel-head"><h3>附件终审候选人</h3><span class="product-status">岗位归属 · 终审评分 · 本地附件</span></div><div class="product-panel-body"><div id="product-automation-candidate-pool" class="candidate-list"></div><div id="product-candidate-list" class="candidate-list" hidden></div></div></section></section>
<section class="product-view" data-product-view="followups"><div class="product-head"><div><h2>后续跟进</h2><p>所有动作先在 BOSS 或线下完成，再在这里记录结果和推进下一步。</p></div></div><section class="product-panel"><div class="product-panel-head"><h3>待办中心</h3><span id="followup-count" class="product-chip">0 项待办</span></div><div class="product-panel-body"><div id="product-followup-list" class="followup-list"></div></div></section></section>
</main></div>
<dialog id="job-rule-editor-dialog" class="product-dialog"><form method="dialog"><h3>编辑岗位规则</h3><section class="form"><h4>BOSS 同步信息</h4><div class="product-grid"><label>岗位名称<output id="rule-boss-name">--</output></label><label>工作城市<output id="rule-boss-city">--</output></label><label>薪资范围<output id="rule-boss-salary">--</output></label><label>学历要求<output id="rule-boss-education">--</output></label><label>经验要求<output id="rule-boss-experience">--</output></label><label>实习/工作节奏<output id="rule-boss-internship">--</output></label><label>工作地址<output id="rule-boss-address">--</output></label></div><label>岗位描述<textarea id="rule-boss-description" readonly></textarea></label><label>职位关键词<output id="rule-boss-skills">--</output></label></section><section class="form"><h4>AI 招聘规则</h4><label>必须条件<textarea id="rule-must-have" placeholder="每行一条规则"></textarea></label><label>加分条件<textarea id="rule-nice-to-have" placeholder="每行一条规则"></textarea></label><label>淘汰条件<textarea id="rule-reject-if" placeholder="每行一条规则"></textarea></label><label>风险信号<textarea id="rule-risk-signals" placeholder="每行一条规则"></textarea></label></section><section class="form"><h4>评分权重与阈值</h4><div class="product-grid"><label>硬性匹配<input id="rule-weight-hard-match" type="number" min="0" max="100"></label><label>工作经验<input id="rule-weight-experience" type="number" min="0" max="100"></label><label>专业问答<input id="rule-weight-professional-qa" type="number" min="0" max="100"></label><label>沟通表现<input id="rule-weight-communication" type="number" min="0" max="100"></label><label>稳定性<input id="rule-weight-stability" type="number" min="0" max="100"></label><label>地点薪资<input id="rule-weight-location-salary" type="number" min="0" max="100"></label><label>初筛阈值<input id="rule-screening-threshold" type="number" min="0" max="100"></label><label>推荐阈值<input id="rule-recommendation-threshold" type="number" min="0" max="100"></label><label>专业问答阈值<input id="rule-professional-qa-threshold" type="number" min="0" max="100"></label></div></section><menu><button id="job-rule-cancel" class="secondary" value="cancel">取消</button><button id="job-rule-save" value="default">确认启用</button></menu></form></dialog>
<dialog id="job-knowledge-dialog" class="product-dialog"><form method="dialog"><h3>岗位知识库</h3><div class="form"><label>类别<select id="knowledge-category"><option value="company">企业信息</option><option value="sales">销售知识</option><option value="faq">候选人常见问题</option></select></label><label>标题<input id="knowledge-title" autocomplete="off"></label><label>内容<textarea id="knowledge-content"></textarea></label></div><menu><button class="secondary" value="cancel">取消</button><button id="job-knowledge-save" value="default">保存知识</button></menu></form></dialog>
<dialog id="product-online-resume-dialog" class="product-dialog"><form method="dialog"><h3 id="product-online-resume-title">在线简历</h3><p class="hint">以下内容从当前 BOSS 在线简历只读提取，仅供本地查看。</p><pre id="product-online-resume-text" class="online-resume-text"></pre><menu><button class="secondary" value="cancel">关闭</button></menu></form></dialog>
<div id="product-toast" class="product-toast" role="status" aria-live="polite"></div>
"""


_PRODUCT_WORKBENCH_SCRIPT = """
(() => {
  const root = document.querySelector('#recruiting-product-shell');
  if (!root) return;
  const token = root.dataset.token || '';
  let snapshot = {};
  let selectedJobId = '';
	let conversationListingSnapshot = {};
	let selectedConversationJobId = 'all';
	// 在线简历读取通常比列表轮询更久。列表每次刷新都会重建 DOM，因此必须把
	// 行内反馈保存在候选人标识维度，避免“读取中”或失败原因被下一次重绘抹掉。
	const onlineResumeRowStates=new Map();
  let ruleDraft = {must_have:[],nice_to_have:[],reject_if:[],risk_signals:[]};
  const $ = selector => document.querySelector(selector);
  let toastTimer = 0;
  const notify = (message, failed = false) => {
    const toast=$('#product-toast'); if(!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent=message;
    toast.className='product-toast '+(failed?'error':'success')+' visible';
    toastTimer=window.setTimeout(()=>{ toast.className='product-toast '+(failed?'error':'success'); },3200);
  };
  const text = value => String(value || '').trim();
  const post = async (url, body = {}) => {
    // 本地写接口同时校验临时令牌和同源 Origin；Origin 缺失会让所有保存请求
    // 都被服务端拒绝。统一在这里补齐，避免每个按钮各自遗漏安全请求头。
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','Origin':window.location.origin,'X-Boss-Web-Token':token}, body:JSON.stringify(body)});
    if (response.status === 403) {
      // 服务重启会轮换临时令牌；保留状态码让调用方决定是否可以安全恢复。
      const error = new Error('本地控制台已更新，请刷新页面后重试');
      error.code = 'STALE_CONSOLE_SESSION';
      throw error;
    }
    const payload = await response.json();
    if (!payload.ok) throw new Error((payload.error && payload.error.message) || '操作失败');
    return payload.data;
  };
  const show = (target, message, failed = false) => { target.textContent = message; target.className = 'product-notice' + (failed ? ' error' : ''); };
  const openView = module => {
    // 内置浏览器环境对 classList 写操作兼容性不稳定；直接写入 class 属性可
    // 保证导航高亮和内容面板始终保持同一模块状态。
    document.querySelectorAll('[data-product-view]').forEach(view => {
      view.setAttribute('class', view.dataset.productView === module ? 'product-view active' : 'product-view');
    });
    document.querySelectorAll('[data-product-module]').forEach(button => button.setAttribute('aria-current', button.dataset.productModule === module ? 'page' : 'false'));
    if (module === 'settings') loadProductSettings();
  };
  const make = (tag, className, content = '') => { const node = document.createElement(tag); if (className) node.className = className; node.textContent = content; return node; };
  const jobById = id => (snapshot.jobs || []).find(job => job.job_id === id);
  const selectedJob = () => jobById(selectedJobId) || null;
  const ruleKeys = ['must_have','nice_to_have','reject_if','risk_signals'];
  const ruleFieldIds = {must_have:'rule-must-have',nice_to_have:'rule-nice-to-have',reject_if:'rule-reject-if',risk_signals:'rule-risk-signals'};
	// 评分配置与自然语言规则共同属于岗位版本。字段映射集中维护，避免弹窗
	// 展示值、提交值和后端模型使用不同命名而造成岗位间配置串用。
	const weightFieldIds = {
		hard_match:'rule-weight-hard-match',
		experience:'rule-weight-experience',
		professional_qa:'rule-weight-professional-qa',
		communication:'rule-weight-communication',
		stability:'rule-weight-stability',
		location_salary:'rule-weight-location-salary'
	};
  const normaliseRules = rules => Object.fromEntries(ruleKeys.map(key => [key,Array.isArray((rules || {})[key]) ? (rules || {})[key].map(text).filter(Boolean) : []]));
  // Python 模板会处理反斜杠；这里必须保留 JavaScript 的 "\\n" 转义，
  // 否则渲染后的脚本会出现跨行单引号字符串，浏览器将拒绝解析整段事件代码。
  const ruleText = items => (items || []).join('\\n');
  const fillRuleEditor = (job, rules = job.criteria || {}) => {
    const platform = job.platform_snapshot || {};
    $('#rule-boss-name').textContent = job.name || 'BOSS 未提供';
    $('#rule-boss-city').textContent = job.city || platform.city || 'BOSS 未提供';
    $('#rule-boss-salary').textContent = job.salary_range || platform.salary_range || 'BOSS 未提供';
    $('#rule-boss-education').textContent = job.education_requirement || platform.education_requirement || 'BOSS 未提供';
    $('#rule-boss-experience').textContent = platform.experience_requirement || 'BOSS 未提供';
    $('#rule-boss-internship').textContent = [platform.internship_requirement, platform.work_days].filter(Boolean).join('，') || 'BOSS 未提供';
    $('#rule-boss-address').textContent = platform.work_address || 'BOSS 未提供';
    $('#rule-boss-description').value = platform.description || platform.job_description || job.description || 'BOSS 职位描述尚未同步';
    $('#rule-boss-skills').textContent = (job.skills || []).join(' · ') || platform.keywords || 'BOSS 未提供';
    ruleDraft = normaliseRules(rules);
    ruleKeys.forEach(key => { $('#'+ruleFieldIds[key]).value = ruleText(ruleDraft[key]); });
		const weights = job.weights || {hard_match:25,experience:20,professional_qa:25,communication:15,stability:10,location_salary:5};
		Object.entries(weightFieldIds).forEach(([key,id]) => { $('#'+id).value = Number(weights[key] ?? 0); });
		$('#rule-screening-threshold').value = Number(job.screening_threshold ?? 70);
		$('#rule-recommendation-threshold').value = Number(job.recommendation_threshold ?? 80);
		$('#rule-professional-qa-threshold').value = Number(job.professional_qa_threshold ?? 60);
  };
  // 与上面的 join 保持一致，正则中的换行也必须以 JS 转义形式输出。
  const reviewedRules = () => Object.fromEntries(ruleKeys.map(key => [key,$('#'+ruleFieldIds[key]).value.split(/\\n+/).map(text).filter(Boolean)]));
	const reviewedScoring = () => ({
		weights:Object.fromEntries(Object.entries(weightFieldIds).map(([key,id]) => [key,Number($('#'+id).value)])),
		screening_threshold:Number($('#rule-screening-threshold').value),
		recommendation_threshold:Number($('#rule-recommendation-threshold').value),
		professional_qa_threshold:Number($('#rule-professional-qa-threshold').value)
	});
  const openRuleEditor = (job, rules) => { selectedJobId = job.job_id; fillRuleEditor(job, rules); $('#job-rule-editor-dialog').showModal(); };
  const renderJobs = () => {
    const host = $('#product-job-list'); host.replaceChildren(); const jobs = snapshot.jobs || [];
    if (!jobs.length) { host.append(make('p','product-empty','请先同步 BOSS 职位。')); return; }
    jobs.forEach(job => { const row = make('div','job-row'); const info = document.createElement('div'); info.append(make('div','row-title',job.name)); const ruleState=job.rules_confirmed ? '规则已确认 · '+(job.rules_version || 'v1') : '规则未确认'; info.append(make('div','row-meta',[job.source === 'boss' ? 'BOSS 同步' : '本地岗位',job.city || '城市未同步',job.salary_range || '薪资未同步',job.status_label || job.status,ruleState].join(' · '))); const actions = make('div','row-actions'); const choose = make('button','secondary',job.job_id === selectedJobId ? '当前岗位' : '选择'); choose.type='button'; choose.addEventListener('click', () => { selectedJobId = job.job_id; renderAll(); renderAutomation(automationSnapshot); }); const edit = make('button','secondary','编辑岗位'); edit.type='button'; edit.addEventListener('click', () => { openView('jobs'); openRuleEditor(job); }); actions.append(choose,edit); row.append(info,actions); host.append(row); });
    const filter = $('#candidate-job-filter'); const previous = filter.value; filter.replaceChildren(); const all = document.createElement('option'); all.value=''; all.textContent='全部岗位'; filter.append(all); jobs.forEach(job => { const option = document.createElement('option'); option.value=job.job_id; option.textContent=job.name; filter.append(option); }); filter.value = previous || selectedJobId;
    ['#product-interview-job','#conversation-schedule-form select[name="job_id"]','#recommendation-schedule-form select[name="job_id"]','#full_flow-schedule-form select[name="job_id"]'].forEach(selector => { const select=$(selector); if(!select) return; const current=select.value; select.replaceChildren(); jobs.forEach(job => { const option=document.createElement('option'); option.value=job.job_id; option.textContent=job.name; select.append(option); }); select.value=current || selectedJobId || (jobs[0] && jobs[0].job_id) || ''; });
  };
  let automationCandidatePool = [];
  const renderCandidates = () => {
    const host=$('#product-automation-candidate-pool'); host.replaceChildren();
    const filterId=$('#candidate-job-filter').value;
    const rows=automationCandidatePool.filter(row => !filterId || row.job_id === filterId);
    const jobIds=new Set(rows.map(row => row.job_id));
    $('#candidate-total').textContent=String(rows.length);
    $('#candidate-review-count').textContent=String(jobIds.size);
    $('#candidate-rejection-count').textContent=String(rows.filter(row => Boolean(row.resume_path)).length);
    if (!rows.length) { host.append(make('p','product-empty','暂无完成附件终审且达到分数线的候选人。')); return; }
    const groups=new Map();
    rows.forEach(row => { const group=groups.get(row.job_id) || []; group.push(row); groups.set(row.job_id,group); });
    [...groups.entries()].forEach(([jobId,group]) => {
      const section=document.createElement('section'); section.className='candidate-job-group';
      const heading=make('h3','', (jobById(jobId) || {}).name || jobId);
      const list=make('div','candidate-list');
      group.sort((left,right) => (right.score || 0) - (left.score || 0)).forEach(row => {
        const item=make('div','candidate-row'); const info=document.createElement('div');
        info.append(make('div','row-title',row.candidate_name || '未命名候选人'));
        info.append(make('div','row-meta',['终审评分 '+row.score,row.recommendation || '待人工确认',row.source === 'recommendation' ? '推荐牛人' : '沟通列表'].filter(Boolean).join(' · ')));
        info.append(make('div','row-meta',row.resume_path));
        const actions=make('div','row-actions'); [['phone','换电话'],['wechat','换微信'],['interview','约面试']].forEach(([action,label])=>{{ const button=make('button','secondary',label); button.type='button'; button.addEventListener('click',()=>runCandidateBossAction(row,action)); actions.append(button); }}); const open=make('button','secondary','打开本地附件'); open.type='button';
        open.addEventListener('click',async()=>{ try { await post('/api/recruiting/automation/candidates/'+encodeURIComponent(row.candidate_key)+'/resume/open',{}); } catch(error) { alert(error.message); } });
        actions.append(open); item.append(info,actions); list.append(item);
      });
      section.append(heading,list); host.append(section);
    });
  };
  const refreshAutomationCandidatePool = async () => {
    try {
      const response=await fetch('/api/recruiting/automation/candidate-pool'); const payload=await response.json();
      if (!payload.ok) throw new Error((payload.error && payload.error.message) || '候选人池读取失败');
      automationCandidatePool=payload.data.qualified || []; renderCandidates();
    } catch(error) { automationCandidatePool=[]; renderCandidates(); }
  };
  const renderResumes = state => {
    const host = $('#product-resume-list');
    host.replaceChildren();
    conversationListingSnapshot=state || conversationListingSnapshot;
    const listing = conversationListingSnapshot.conversation_list || {};
    const items = listing.items || [];
    const resumePositionFilter=$('#resume-position-filter');
    const jobs=(snapshot.jobs || []).filter(job => String(job.job_id || '').trim() && String(job.name || '').trim());
    const activeJobId=selectedConversationJobId==='all' || jobs.some(job => job.job_id===selectedConversationJobId) ? selectedConversationJobId : 'all';
    selectedConversationJobId=activeJobId;
    resumePositionFilter.replaceChildren();
    const allOption=document.createElement('option'); allOption.value='all'; allOption.textContent='全部岗位'; resumePositionFilter.append(allOption);
    jobs.forEach(job=>{ const option=document.createElement('option'); option.value=job.job_id; option.textContent=job.name; resumePositionFilter.append(option); });
    resumePositionFilter.value=activeJobId;
    const activeJob=jobs.find(job => job.job_id===activeJobId) || null;
    const resumeListError = listing.error && listing.error.message ? listing.error.message : '沟通列表读取失败，请稍后重试。';
    const listState = $('#resume-list-state');
    if (listing.state === 'failed') {
      listState.textContent = '读取失败';
      host.append(make('p','product-empty error',resumeListError));
      return;
    }
    listState.textContent = listing.state === 'succeeded' ? (activeJob ? (activeJob.name+' · '+items.length+' 位候选人') : ('已读取 '+items.length+' 位候选人')) : '未读取';
    if (!items.length) {
      host.append(make('p','product-empty',activeJob ? '当前岗位下暂无沟通候选人。' : '点击“刷新沟通列表”后，这里会显示候选人。刷新只读取名单，不会发送任何消息。'));
      return;
    }
    items.forEach(item => {
      const row=make('div','resume-row');
      const info=document.createElement('div');
      info.append(make('div','row-title',item.candidate_name || '未命名候选人'));
      info.append(make('div','row-meta',[item.position || (activeJob && activeJob.name),item.company,item.city].filter(Boolean).join(' · ') || '等待读取岗位信息'));
      const actions=make('div','row-actions');
      const online=make('button','secondary','查看在线简历');
	  const onlineState=make('span','row-meta','');
      online.type='button';
	  const rowState=onlineResumeRowStates.get(item.selection_id);
	  if(rowState) {
		onlineState.textContent=rowState.message;
		if(rowState.state==='running') {
			online.disabled=true;
			online.textContent='读取中…';
		}
	  }
      online.addEventListener('click',async()=>{
        // 在线简历页本身已按岗位刷新 BOSS 会话。必须使用这里的筛选值，
        // 不能借用“自动化”或“岗位管理”页面的独立选择状态，否则用户在
        // 本页已选 Java 时仍会被错误拦截，或读取到另一个岗位的会话。
        const jobId=selectedConversationJobId !== 'all' ? selectedConversationJobId : '';
		if(!jobId) {
			onlineResumeRowStates.set(item.selection_id,{state:'failed',message:'请先选择具体岗位。'});
			onlineState.textContent='请先选择具体岗位。';
			show($('#resume-action-notice'),'请在本页岗位筛选中选择具体岗位后，再查看在线简历。',true);
			return;
		}
        try {
		  onlineResumeRowStates.set(item.selection_id,{state:'running',message:'正在读取在线简历…'});
		  online.disabled=true;
		  online.textContent='读取中…';
		  onlineState.textContent='正在读取在线简历…';
		  notify('正在读取 '+(item.candidate_name || '候选人')+' 的在线简历…');
          show($('#resume-action-notice'),'正在打开在线简历，请稍候…');
          // ``post`` 已经解包并校验 HTTP 信封，只返回 ``data``。再次访问
          // 不存在的 ``payload.ok`` 会把已成功提交的预览任务误判为失败。
          const previewRequest=await post('/api/conversations/'+encodeURIComponent(item.selection_id)+'/online-resume/open',{job_id:jobId});
          if(!previewRequest || previewRequest.state !== 'running') throw new Error('在线简历预览请求未启动');
          show($('#resume-action-notice'),'正在刷新当前岗位沟通列表并打开在线简历预览。');
          await waitForOnlineResumePreview();
		  onlineResumeRowStates.set(item.selection_id,{state:'succeeded',message:'在线简历已打开。'});
		  onlineState.textContent='在线简历已打开。';
		} catch(error) {
		  onlineResumeRowStates.set(item.selection_id,{state:'failed',message:error.message || '在线简历读取失败'});
		  onlineState.textContent=error.message || '在线简历读取失败';
		  show($('#resume-action-notice'),error.message,true);
		  notify(error.message || '在线简历读取失败',true);
		} finally { online.disabled=false; online.textContent='查看在线简历'; }
      });
	  actions.append(online,onlineState);
      row.append(info,actions);
      host.append(row);
    });
  };
  const renderFollowups = () => { const host = $('#product-followup-list'); host.replaceChildren(); const tasks=(snapshot.tasks || []).filter(task => task.status === 'pending'); $('#followup-count').textContent=tasks.length+' 项待办'; if (!tasks.length) { host.append(make('p','product-empty','暂无待办。候选人完成评分并经人工确认后，后续动作会出现在这里。')); return; } tasks.forEach(task=>{ const row=make('div','followup-row'); const info=document.createElement('div'); info.append(make('div','row-title',task.title || '待处理事项')); info.append(make('div','row-meta',[task.candidate_name,task.next_action,task.stage_label].filter(Boolean).join(' · '))); const actions=make('div','row-actions'); const done=make('button','secondary','记录已完成'); done.type='button'; done.addEventListener('click',async()=>{ try { await post('/api/recruiting/tasks/'+encodeURIComponent(task.task_id),{status:'completed',note:'已在本地工作台记录完成'}); await waitAndRefresh(); } catch(error) { alert(error.message); } }); actions.append(done); row.append(info,actions); host.append(row); }); };
  let automationSnapshot = {};
	let selectedAutomationCandidateKey = '';
  const automationStageLabels = {synced:'已同步',hard_rejected:'硬筛不通过',basic_dialogue:'基础问答',professional_dialogue:'专业问答',waiting_candidate:'等待候选人',waiting_attachment:'等待附件',analyzed:'附件终审完成',manual_review:'待人工复核',failed:'处理失败',paused:'已暂停'};
  const renderAutomationRows = (host, rows, emptyText) => {
    host.replaceChildren();
    if (!rows.length) { host.append(make('p','product-empty',emptyText)); return; }
    rows.forEach(row => {
      const item=make('div','candidate-row');
      const info=document.createElement('div');
      info.append(make('div','row-title',row.candidate_name || '未命名候选人'));
      info.append(make('div','row-meta',[(row.source === 'recommendation' ? '推荐牛人' : '沟通列表'),automationStageLabels[row.stage] || row.stage,row.score === null || row.score === undefined ? '未终审' : ('评分 '+row.score),row.recommendation || ''].filter(Boolean).join(' · ')));
      if (row.last_action) info.append(make('div','row-meta',row.last_action));
      if (Array.isArray(row.reason_codes) && row.reason_codes.length) info.append(make('div','row-meta','原因：'+row.reason_codes.join('、')));
      const actions=make('div','row-actions');
		const detail=make('button','secondary',row.candidate_key === selectedAutomationCandidateKey ? '正在查看对话' : '查看对话');
		detail.type='button'; detail.addEventListener('click',async()=>{ selectedAutomationCandidateKey=row.candidate_key; await refreshAutomationConversation(); renderAutomationRows(host,rows,emptyText); }); actions.append(detail);
      item.append(info,actions); host.append(item);
    });
  };
  const renderAutomationConversation = detail => {
    const host=$('#product-automation-conversation-detail'); host.replaceChildren();
    if (!detail || !detail.candidate) { host.append(make('p','product-empty','从处理队列选择候选人后，这里会显示候选人与 AI 的已处理对话。')); return; }
    const candidate=detail.candidate; host.append(make('div','row-title',candidate.candidate_name || '未命名候选人'));
    host.append(make('div','row-meta',[automationStageLabels[candidate.stage] || candidate.stage,candidate.last_action || ''].filter(Boolean).join(' · ')));
    const turns=detail.timeline || [];
    if (!turns.length) { host.append(make('p','product-empty','该候选人尚无自动化已处理的对话。')); return; }
    turns.forEach(turn => { const row=make('div','candidate-row'); row.append(make('div','row-title',turn.role === 'candidate' ? '候选人' : 'AI 招聘助手')); row.append(make('div','row-meta',turn.text)); if(turn.at) row.append(make('div','row-meta',turn.at)); host.append(row); });
  };
  const refreshAutomationConversation = async () => {
    const jobId=$('#product-automation-job').value;
    if (!jobId || !selectedAutomationCandidateKey) { renderAutomationConversation(null); return; }
    try {
      const response=await fetch('/api/recruiting/automation/candidates/'+encodeURIComponent(selectedAutomationCandidateKey)+'?job_id='+encodeURIComponent(jobId));
      const payload=await response.json(); if (!payload.ok) throw new Error((payload.error && payload.error.message) || '对话读取失败');
      renderAutomationConversation(payload.data);
    } catch(error) { selectedAutomationCandidateKey=''; renderAutomationConversation(null); }
  };
  const refreshAutomationCandidates = async jobId => {
    const queue=$('#product-automation-queue');
    if (!jobId) { queue.replaceChildren(); renderAutomationConversation(null); return; }
    try {
      const response=await fetch('/api/recruiting/automation/candidates?job_id='+encodeURIComponent(jobId));
      const payload=await response.json();
      if (!payload.ok) throw new Error((payload.error && payload.error.message) || '自动化候选人读取失败');
      renderAutomationRows(queue,payload.data.candidates || [],'暂无已同步候选人。');
		if (!selectedAutomationCandidateKey || !(payload.data.candidates || []).some(row => row.candidate_key === selectedAutomationCandidateKey)) selectedAutomationCandidateKey='';
		await refreshAutomationConversation();
    } catch(error) { show($('#product-automation-state'),error.message,true); }
  };
  const renderAutomation = state => {
    automationSnapshot=state || automationSnapshot || {};
    const select=$('#product-automation-job'); const jobs=snapshot.jobs || []; const current=select.value || selectedJobId;
    select.replaceChildren(); const placeholder=document.createElement('option'); placeholder.value=''; placeholder.textContent='请选择岗位'; select.append(placeholder);
    jobs.forEach(job => { const option=document.createElement('option'); option.value=job.job_id; option.textContent=job.name; select.append(option); });
    select.value=current && jobs.some(job => job.job_id === current) ? current : '';
    if (select.value) selectedJobId=select.value;
    const runtime=(automationSnapshot || {}).state || 'idle';
    const status=$('#product-automation-state'); const runtimeLabel=$('#product-automation-runtime');
    runtimeLabel.textContent={running:'运行中',paused:'已暂停',stopping:'正在停止',stopped:'已停止',idle:'未启动'}[runtime] || runtime;
    const hasJob=Boolean(select.value); const running=runtime === 'running'; const paused=runtime === 'paused'; const stopping=runtime === 'stopping';
		renderAutomationScheduleSummary(select.value);
    $('#product-automation-sync').disabled=!hasJob || running || paused || stopping;
    $('#product-automation-start-conversation').disabled=!hasJob || running || paused || stopping;
    const recommendationQuota=(automationSnapshot || {}).recommendation_quota || {};
    const recommendationBlocked=recommendationQuota.blocked === true;
    $('#product-automation-start-recommendation').disabled=!hasJob || paused || stopping || recommendationBlocked;
    $('#product-automation-pause').disabled=!running;
    $('#product-automation-resume').disabled=!paused;
    $('#product-automation-stop').disabled=!running && !paused && !stopping;
    const automationSync=(automationSnapshot || {}).sync || {};
    let automationMessage = '可先同步沟通列表，或从推荐牛人启动招呼。';
    let automationFailed = false;
    if (!hasJob) automationMessage='请选择岗位后同步沟通列表。';
    else if (automationSync.state === 'running') automationMessage='正在同步沟通列表，请稍候。';
    else if (automationSync.state === 'succeeded') automationMessage=`已同步 ${automationSync.synced || 0} 位沟通候选人。`;
    else if (automationSync.state === 'failed') { automationMessage=(automationSync.error && automationSync.error.message) || '同步沟通列表失败，请检查登录状态后重试。'; automationFailed=true; }
    else if (recommendationBlocked) automationMessage=recommendationQuota.message || 'BOSS 推荐牛人今日沟通已达上限，当前仅处理沟通列表；次日自动恢复。';
    else if (running) automationMessage='自动化运行中，只会处理候选人的真实新回复。';
    else if (paused) automationMessage='自动化已暂停，候选人状态会保留。';
    else if (stopping) automationMessage='正在停止当前操作。';
    else if (runtime === 'stopped') {
      const latestActivity=Array.isArray((automationSnapshot || {}).activities) ? (automationSnapshot || {}).activities[0] : null;
      if (latestActivity && (latestActivity.status === 'failed' || latestActivity.status === 'blocked')) {
        automationMessage='自动化已停止：'+(latestActivity.detail || latestActivity.action || '请检查 RPA 浏览器登录状态后重试。');
        automationFailed=true;
      }
    }
    show(status, automationMessage, automationFailed);
    const activities=$('#product-automation-activities'); activities.replaceChildren(); const items=(automationSnapshot || {}).activities || [];
    if (!items.length) activities.append(make('p','product-empty','暂无自动化活动。'));
    else items.slice(0,8).forEach(item => activities.append(make('div','row-meta',[item.at,item.action,item.status,item.detail].filter(Boolean).join(' · '))));
    refreshAutomationCandidates(select.value);
  };
  const requestAutomation = async (path, body = {}) => {
    const jobId=$('#product-automation-job').value;
    if (!jobId) { show($('#product-automation-state'),'请先选择岗位。',true); return; }
    try {
      const result=await post(path,{job_id:jobId,...body});
      const automationResultState = result.state || 'idle';
      const resultError=(result.error && result.error.message) || '自动化请求失败，请检查 BOSS 页面后重试。';
      if (automationResultState === 'failed' || automationResultState === 'blocked') {
        show($('#product-automation-state'),resultError,true);
      } else if (path === '/api/recruiting/automation/sync' && automationResultState === 'running') {
        show($('#product-automation-state'),'同步请求已提交，后台正在读取 BOSS 沟通列表。');
      } else {
        show($('#product-automation-state'),'请求已提交，状态会自动刷新。');
      }
      await waitAndRefresh();
    } catch(error) { show($('#product-automation-state'),error.message,true); }
  };
  const fillScheduleForm = (source, settings) => {
    const form=$('#'+source+'-schedule-form'); if(!form) return;
    form.elements.enabled.checked=settings.enabled === true;
    ['job_id','start_time','end_time','interval_minutes','limit','daily_quota'].forEach(key => { if(form.elements[key]) form.elements[key].value=settings[key] ?? ''; });
    const weekdays=new Set((settings.weekdays || []).map(String));
    form.querySelectorAll('input[name="weekdays"]').forEach(input => { input.checked=weekdays.has(input.value); });
    const runtime=settings.runtime || {}; const labels={disabled:'未启用',outside_window:'等待执行时段',waiting_for_other_job:'等待其它岗位完成',running:'运行中',paused:'已暂停',blocked:'已阻止',failed:'启动失败'};
    $('#'+source+'-schedule-state').textContent=labels[runtime.state] || runtime.state || '已保存';
  };
  // 全流程定时任务与两个独立入口共用配置结构，但单独呈现，避免用户误以为会覆盖已有任务。
  const ensureFullFlowScheduleForm = () => {
    if ($('#full_flow-schedule-form')) return;
    const settingsView=document.querySelector('[data-product-view="settings"]');
    if (!settingsView) return;
    const panel=document.createElement('section'); panel.className='product-panel';
    panel.innerHTML='<div class="product-panel-head"><h3>全流程定时任务</h3><span id="full_flow-schedule-state" class="product-status">未配置</span></div><div class="product-panel-body"><form id="full_flow-schedule-form" class="form" data-schedule-source="full_flow"><label><input id="full_flow-schedule-enabled" name="enabled" type="checkbox"> 启用全流程定时任务</label><label>岗位<select name="job_id" required></select></label><label>开始时间<input name="start_time" type="time" value="20:00" required></label><label>结束时间<input name="end_time" type="time" value="09:00" required></label><label>执行间隔（分钟）<input name="interval_minutes" type="number" min="1" max="1440" value="20" required></label><label>单次处理数量<input name="limit" type="number" min="1" max="50" value="20" required></label><label>每日配额<input name="daily_quota" type="number" min="1" max="1000" value="20" required></label><fieldset><legend>执行日</legend><label><input name="weekdays" type="checkbox" value="0" checked>周一</label><label><input name="weekdays" type="checkbox" value="1" checked>周二</label><label><input name="weekdays" type="checkbox" value="2" checked>周三</label><label><input name="weekdays" type="checkbox" value="3" checked>周四</label><label><input name="weekdays" type="checkbox" value="4" checked>周五</label><label><input name="weekdays" type="checkbox" value="5" checked>周六</label><label><input name="weekdays" type="checkbox" value="6" checked>周日</label></fieldset><button type="submit">保存全流程定时任务</button></form></div>';
    settingsView.append(panel);
  };
  // 全流程按钮动态插入，保证旧版静态页面结构也能平滑升级。
  const ensureFullFlowAutomationButton = () => {
    if ($('#product-automation-start-full-flow')) return;
    const conversationButton=$('#product-automation-start-conversation');
    if (!conversationButton) return;
    const button=document.createElement('button');
    button.id='product-automation-start-full-flow';
    button.type='button';
    button.textContent='开始全流程自动化';
    conversationButton.before(button);
  };
  const renderAutomationScheduleSummary = async jobId => {
    const summary=$('#product-automation-schedule-summary');
    if (!jobId) { summary.textContent='请选择岗位查看定时任务时间。'; return; }
    try {
      const response=await fetch('/api/recruiting/automation/schedules'); const payload=await response.json();
      if (!payload.ok) throw new Error('定时任务读取失败');
      // 仅显示当前岗位的任务，防止不同岗位配置混在同一个自动化控制区。
      const sourceLabels={conversation:'沟通列表：',recommendation:'推荐牛人：',full_flow:'全流程：'};
      const summaries=Object.entries(sourceLabels).flatMap(([source,label]) => {
        const settings=(payload.data || {})[source] || {};
        if (settings.job_id !== jobId) return [];
        const window=[settings.start_time || '--:--',settings.end_time || '--:--'].join('-');
		return [label+(settings.enabled === true ? '已启用' : '未启用')+' '+window];
      });
      if ($('#product-automation-job').value !== jobId) return;
      summary.textContent=summaries.length ? summaries.join(' ｜ ') : '当前岗位未设置定时任务。';
    } catch (_) {
      summary.textContent='定时任务时间暂时无法读取。';
    }
  };
  const loadInterviewSettings = async () => {
    const form=$('#product-interview-settings-form'); const jobId=form.elements.job_id.value || selectedJobId; if(!jobId) return;
    const response=await fetch('/api/recruiting/automation/settings?job_id='+encodeURIComponent(jobId)); const payload=await response.json();
    if(!payload.ok) throw new Error((payload.error || {}).message || '约面试设置读取失败');
    Object.entries(payload.data || {}).forEach(([key,value]) => { if(form.elements[key]) form.elements[key].value=value || ''; });
    form.elements.job_id.value=jobId; $('#product-interview-settings-state').textContent='已读取当前岗位设置';
  };
  const ensureProductFollowupSettingsPanel = () => {
    if ($('#product-followup-settings-form')) return;
    const settingsView=document.querySelector('[data-product-view="settings"]');
    if (!settingsView) return;
    const panel=document.createElement('section'); panel.className='product-panel';
    panel.innerHTML='<div class="product-panel-head"><h3>达标候选人后续自动化</h3><span id="product-followup-settings-state" class="product-status">请选择岗位</span></div><div class="product-panel-body"><form id="product-followup-settings-form" class="form"><label>岗位<select id="product-followup-job" name="job_id" required></select></label><p class="product-notice">只有附件简历终审达标并进入当前岗位候选人池后才会执行。开启约面试时，换微信或换电话任意一个成功即可约面试。</p><fieldset><legend>自动化动作</legend><label><input id="product-followup-wechat" name="wechat_enabled" type="checkbox"> 自动换微信</label><label><input id="product-followup-phone" name="phone_enabled" type="checkbox"> 自动换电话</label><label><input id="product-followup-interview" name="interview_enabled" type="checkbox"> 自动约面试</label></fieldset><div class="actions"><button type="submit">保存后续动作设置</button><button type="button" class="secondary" id="product-followup-export-csv">导出 CSV</button><button type="button" class="secondary" id="product-followup-export-xlsx">导出 Excel</button></div></form></div>';
    settingsView.insertBefore(panel, settingsView.children[1] || null);
    const form=$('#product-followup-settings-form');
    form.addEventListener('submit',async event=>{ event.preventDefault(); const jobId=form.elements.job_id.value; if(!jobId) return; try { await post('/api/recruiting/automation/followup-settings',{job_id:jobId,phone_enabled:$('#product-followup-phone').checked,wechat_enabled:$('#product-followup-wechat').checked,interview_enabled:$('#product-followup-interview').checked}); $('#product-followup-settings-state').textContent='后续动作设置已保存'; notify('后续动作设置已保存'); } catch(error) { $('#product-followup-settings-state').textContent=error.message || '保存失败'; notify(error.message || '保存失败',true); } });
    const exportPool=async format=>{ const jobId=form.elements.job_id.value; if(!jobId) return; const response=await fetch('/api/recruiting/automation/candidate-pool/export?job_id='+encodeURIComponent(jobId)+'&format='+format); if(!response.ok){ $('#product-followup-settings-state').textContent='导出失败'; return; } const link=document.createElement('a'); link.href=URL.createObjectURL(await response.blob()); link.download=jobId+'-候选人池.'+format; link.click(); URL.revokeObjectURL(link.href); };
    $('#product-followup-export-csv').addEventListener('click',()=>exportPool('csv')); $('#product-followup-export-xlsx').addEventListener('click',()=>exportPool('xlsx'));
  };
  const loadProductFollowupSettings = async () => {
    ensureProductFollowupSettingsPanel(); const form=$('#product-followup-settings-form'); if(!form) return;
    const jobs=snapshot.jobs || []; const select=form.elements.job_id; select.replaceChildren(...jobs.map(job=>{ const option=document.createElement('option'); option.value=job.job_id; option.textContent=job.name || job.job_id; return option; }));
    const jobId=selectedJobId || (jobs[0] && jobs[0].job_id); if(!jobId) return; select.value=jobId;
    const response=await fetch('/api/recruiting/automation/followup-settings?job_id='+encodeURIComponent(jobId)); const payload=await response.json(); if(!payload.ok) return; const data=payload.data || {}; $('#product-followup-phone').checked=data.phone_enabled===true; $('#product-followup-wechat').checked=data.wechat_enabled===true; $('#product-followup-interview').checked=data.interview_enabled===true; $('#product-followup-settings-state').textContent='已读取当前岗位设置';
  };
  const loadProductSettings = async () => {
    try {
      await loadProductFollowupSettings();
      await loadInterviewSettings();
      const response=await fetch('/api/recruiting/automation/schedules'); const payload=await response.json();
      if(!payload.ok) throw new Error((payload.error || {}).message || '定时设置读取失败');
      fillScheduleForm('conversation',(payload.data || {}).conversation || {});
      fillScheduleForm('recommendation',(payload.data || {}).recommendation || {});
      fillScheduleForm('full_flow',(payload.data || {}).full_flow || {});
    } catch(error) { $('#product-interview-settings-state').textContent=error.message; }
  };
  const renderAll = state => { if (state) snapshot = state; const jobs=snapshot.jobs || []; renderJobs(); renderCandidates(); renderFollowups(); $('#job-agent-source').textContent = selectedJobId ? '已选择岗位，可随时编辑' : '新建岗位'; };
  const refresh = async () => { try { const [stateResponse, workspaceResponse] = await Promise.all([fetch('/api/state'),fetch('/api/recruiting/workspace')]); const statePayload=await stateResponse.json(); const workspacePayload=await workspaceResponse.json(); const state=statePayload.data || {}; const workspace=workspacePayload.data || {}; snapshot=workspace; $('#product-login-state').textContent=state.login && state.login.state === 'succeeded' ? 'RPA 已登录' : 'RPA 未登录'; $('#product-login-detail').textContent=state.login && state.login.error ? state.login.error.message : (state.login && state.login.notice ? state.login.notice : '当前 RPA 浏览器会话状态已读取。'); $('#product-login-summary').textContent=state.login && state.login.state === 'succeeded' ? 'RPA 浏览器已就绪' : 'RPA 浏览器需要登录'; $('#product-mode-state').textContent=state.operating_mode === 'research' ? '研究模式' : '受限模式'; const pacing=state.pacing || {}; $('#product-pacing-state').textContent=pacing.allowed === false ? '已暂停' : '可用'; $('#product-pacing-detail').textContent=pacing.reason_label || '当前无自动化发送动作。'; renderResumes(state); renderAll(); renderAutomation(state.automation || {}); await refreshAutomationCandidatePool(); } catch(error) { $('#product-login-detail').textContent='无法读取工作台状态：'+error.message; } };
  const waitAndRefresh = async () => { await new Promise(resolve => setTimeout(resolve,120)); await refresh(); };
  const waitForRecruitingOperation = async operation => {
    // 岗位标准由后台线程保存。不能只依赖一次短延时刷新，否则模型调用或磁盘
    // 写入稍慢时会把“正在分析”永久留在界面上，误导用户重复提交。
    const deadline = Date.now() + 65000;
    while (Date.now() < deadline) {
      const response = await fetch('/api/state');
      const payload = await response.json();
      const recruiting = ((payload && payload.data) || {}).recruiting || {};
      if (recruiting.operation === operation && recruiting.state !== 'running') {
        await refresh();
        return recruiting;
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('岗位标准处理超时，请检查本地服务后重试');
  };
  const waitForOnlineResumePreview = async () => {
    // 读取指定岗位时，BOSS 会先切换职位筛选，再加载会话列表。面对大列表，
    // 30 秒不足以区分“仍在读取”和“登录失效”；与后台同步预算对齐，
    // 只在 120 秒后给出受控提示，避免误导用户重复登录。
    const deadline=Date.now()+120000;
    while(Date.now()<deadline) {
      const response=await fetch('/api/state'); const payload=await response.json();
      const preview=((payload.data || {}).online_resume_preview) || {};
      if(preview.state==='succeeded') {
        const dialog=$('#product-online-resume-dialog');
        $('#product-online-resume-title').textContent=(preview.candidate_name || '候选人')+' · 在线简历';
        $('#product-online-resume-text').textContent=preview.resume_text || '未读取到可显示的简历内容。';
        if(!dialog.open) dialog.showModal();
        notify('在线简历已在本平台读取，同时已打开 BOSS 原始页面。');
        return;
      }
      if(preview.state==='failed' || preview.state==='blocked') throw new Error((preview.error || {}).message || '在线简历读取失败');
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw new Error('在线简历读取超时，请检查 BOSS 页面后重试');
  };
  const waitForConversationListRefresh = async jobId => {
    // 列表读取在后台串行任务中执行。这里仅查询本地运行时状态，不能再次调用
    // BOSS 列表接口，否则用户一次点击可能意外变成多次平台请求。
    const deadline=Date.now()+30000;
    while (Date.now()<deadline) {
      const response=await fetch('/api/state');
      const payload=await response.json();
      if (!payload.ok) throw new Error((payload.error || {}).message || '无法读取刷新状态');
      const listing=((payload.data || {}).conversation_list) || {};
      const requestedJobId=jobId && jobId!=='all' ? jobId : null;
      if (listing.job_id !== requestedJobId) {
        await new Promise(resolve=>setTimeout(resolve,250));
        continue;
      }
      if (listing.state==='failed') throw new Error(((listing.error || {}).message) || '沟通列表刷新失败');
      if (listing.refreshing===true || listing.state==='running') {
        await new Promise(resolve=>setTimeout(resolve,250));
        continue;
      }
      if (listing.state==='succeeded') {
        const notice=listing.notice || {};
        return {count:Array.isArray(listing.items) ? listing.items.length : 0,notice:String(notice.message || '')};
      }
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw new Error('BOSS 沟通列表读取超过 120 秒，后台可能仍在读取，请稍后刷新查看最终结果');
  };
	const refreshConversationList = async jobId => {
		const resumeRefreshButton=$('#resume-refresh-button');
		const requestedJobId=jobId && jobId!=='all' ? jobId : '';
		resumeRefreshButton.disabled=true;
		resumeRefreshButton.textContent='刷新中…';
		show($('#resume-action-notice'),'正在刷新沟通列表，请稍候…');
		try {
			const query='/api/conversations?refresh=1'+(requestedJobId ? '&job_id='+encodeURIComponent(requestedJobId) : '');
			const response=await fetch(query);
			const payload=await response.json();
			if (!response.ok || !payload.ok) throw new Error((payload.error || {}).message || '刷新沟通列表请求失败');
			show($('#resume-action-notice'),'刷新请求已提交，正在读取 BOSS 沟通列表。');
			const result=await waitForConversationListRefresh(jobId);
			await refresh();
			if (result.notice) show($('#resume-action-notice'),result.notice);
			else {
				show($('#resume-action-notice'),'沟通列表已刷新，共读取 '+result.count+' 位候选人。');
				notify('沟通列表已刷新，共读取 '+result.count+' 位候选人。');
			}
		} catch(error) {
			show($('#resume-action-notice'),error.message || '沟通列表刷新失败',true);
			notify(error.message || '沟通列表刷新失败',true);
		} finally {
			resumeRefreshButton.disabled=false;
			resumeRefreshButton.textContent='刷新沟通列表';
		}
	};
  document.querySelectorAll('[data-product-module]').forEach(button => button.addEventListener('click',() => openView(button.dataset.productModule)));
  const loginRetryKey='boss-agent-retry-open-login';
  const loginRetryAttempted=sessionStorage.getItem(loginRetryKey)==='1';
  if(loginRetryAttempted) sessionStorage.removeItem('boss-agent-retry-open-login');
  const startProductLogin = async () => {
    const button=$('#product-login-button'); button.disabled=true; button.textContent='正在打开…';
    $('#product-login-detail').textContent='正在打开专用 RPA Chrome 中的 BOSS 登录页。';
    try {
      await post('/api/login');
      const deadline=Date.now()+5000; let opened=false;
      while(Date.now()<deadline) {
        await waitAndRefresh();
        const state=$('#product-login-detail').textContent || '';
        if(state.includes('BOSS 登录页已打开')) { opened=true; break; }
        await new Promise(resolve=>setTimeout(resolve,180));
      }
      if(!opened) throw new Error('BOSS 登录页暂未打开，请检查专用 RPA Chrome 后重试');
      notify('BOSS 登录页已打开，请在专用 RPA Chrome 中完成登录。');
    } catch(error) {
      if(error && error.code==='STALE_CONSOLE_SESSION' && !loginRetryAttempted) {
        // 只恢复用户刚点击的登录页动作；其它写请求不会因页面重启而重放。
        sessionStorage.setItem('boss-agent-retry-open-login', '1');
        window.location.reload();
        return;
      }
      $('#product-login-detail').textContent=error.message;
      notify(error.message,true);
    } finally {
      button.disabled=false; button.textContent='打开 BOSS 登录页';
    }
  };
  $('#product-login-button').addEventListener('click', startProductLogin);
  if(loginRetryAttempted) window.setTimeout(()=>startProductLogin(),0);
  $('#job-sync-boss-button').addEventListener('click', async () => { try { const result = await post('/api/recruiting/jobs/sync-boss'); const count = (result.jobs || []).length; show($('#job-standard-notice'),`已从 BOSS 同步 ${count} 个岗位。`); await waitAndRefresh(); if (!selectedJobId && snapshot.jobs && snapshot.jobs.length) { selectedJobId = snapshot.jobs[0].job_id; renderAll(); } } catch(error) { show($('#job-standard-notice'),'BOSS 岗位同步失败：'+error.message,true); } });
  $('#job-standard-submit').addEventListener('click', async () => { const requirements=text($('#job-standard-input').value); const job=selectedJob(); if (!job) { show($('#job-standard-notice'),'请先从岗位列表选择一个 BOSS 同步岗位。',true); return; } if (!requirements) { show($('#job-standard-notice'),'请先输入补充要求。',true); return; } try { await post('/api/recruiting/jobs/rules/analyze',{requirements,job_id:job.job_id}); $('#job-agent-source').textContent='AI 正在分析'; show($('#job-standard-notice'),'正在生成四类规则。'); const completed=await waitForRecruitingOperation('analyze-job-rules'); if (completed.state !== 'succeeded') throw new Error(((completed.error || {}).message) || '规则分析失败'); const analysis=(completed.result || {}).analysis || {}; openRuleEditor(job,analysis); $('#job-agent-source').textContent=analysis.source === 'ai' ? 'AI 规则已生成' : '本地规则已生成'; show($('#job-standard-notice'),'请在弹窗中审核四类规则后保存。'); } catch(error) { show($('#job-standard-notice'),'规则分析失败：'+error.message,true); } });
  $('#job-rule-save').addEventListener('click',async(event)=>{ event.preventDefault(); const job=selectedJob(); if(!job) { show($('#job-standard-notice'),'岗位不存在，请刷新后重试。',true); return; } try { await post('/api/recruiting/jobs/rules',{job_id:job.job_id,rules:reviewedRules(),scoring:reviewedScoring()}); show($('#job-standard-notice'),'正在确认并启用审核后的规则。'); const completed=await waitForRecruitingOperation('apply-job-rules'); if (completed.state !== 'succeeded') throw new Error(((completed.error || {}).message) || '规则保存失败'); const confirmed=((completed.result || {}).job) || {}; $('#job-rule-editor-dialog').close(); $('#job-standard-input').value=''; show($('#job-standard-notice'),'规则已确认启用（'+(confirmed.rules_version || '新版本')+'），BOSS 同步信息未被修改。'); await waitAndRefresh(); } catch(error) { show($('#job-standard-notice'),'规则保存失败：'+error.message,true); } });
  $('#job-knowledge-button').addEventListener('click',()=>{ if(!selectedJob()) { show($('#job-standard-notice'),'请先新建或选择一个岗位。',true); return; } $('#job-knowledge-dialog').showModal(); });
  $('#job-knowledge-save').addEventListener('click',async()=>{ const job=selectedJob(); if(!job) return; const title=text($('#knowledge-title').value), content=text($('#knowledge-content').value); if(!title || !content) { alert('请填写知识标题和内容。'); return; } try { await post('/api/recruiting/knowledge',{job_id:job.job_id,category:$('#knowledge-category').value,title,content,audience:'candidate'}); $('#knowledge-title').value=''; $('#knowledge-content').value=''; await waitAndRefresh(); } catch(error) { alert(error.message); } });
  $('#resume-refresh-button').addEventListener('click',()=>refreshConversationList(selectedConversationJobId));
	$('#resume-position-filter').addEventListener('change',event=>{ selectedConversationJobId=event.currentTarget.value; refreshConversationList(event.currentTarget.value); });
  $('#resume-local-scan-button').addEventListener('click',async()=>{ try { await post('/api/recruiting/candidates/auto-assign',{directory:'C:\\Users\\25479\\Desktop\\简历'}); show($('#resume-action-notice'),'正在分析桌面简历并自动匹配岗位。'); await waitAndRefresh(); } catch(error) { show($('#resume-action-notice'),error.message,true); } });
  $('#product-automation-job').addEventListener('change',()=>{ selectedJobId=$('#product-automation-job').value; renderAll(); renderAutomation(automationSnapshot); });
  ensureFullFlowScheduleForm();
  ensureFullFlowAutomationButton();
  $('#product-automation-sync').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/sync'));
  $('#product-automation-start-full-flow').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/start',{source:'full_flow',limit:20}));
  $('#product-automation-start-conversation').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/start',{source:'conversation',limit:20}));
  $('#product-automation-start-recommendation').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/start',{source:'recommendation',limit:10}));
  $('#product-automation-pause').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/pause'));
  $('#product-automation-resume').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/resume'));
  $('#product-automation-stop').addEventListener('click',()=>requestAutomation('/api/recruiting/automation/stop'));
  $('#product-interview-job').addEventListener('change',()=>loadInterviewSettings().catch(error=>{$('#product-interview-settings-state').textContent=error.message;}));
  document.addEventListener('change',event=>{ if(event.target && event.target.id==='product-followup-job'){ loadProductFollowupSettings().catch(error=>{$('#product-followup-settings-state').textContent=error.message;}); } });
  $('#product-interview-settings-form').addEventListener('submit',async event=>{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); try { await post('/api/recruiting/automation/settings',body); $('#product-interview-settings-state').textContent='设置已保存'; notify('约面试设置已保存'); } catch(error) { $('#product-interview-settings-state').textContent=error.message; notify(error.message,true); } });
  document.querySelectorAll('[data-schedule-source]').forEach(form=>form.addEventListener('submit',async event=>{ event.preventDefault(); const source=form.dataset.scheduleSource; const data=new FormData(form); const body=Object.fromEntries(data); body.source=source; body.enabled=form.elements.enabled.checked; body.weekdays=data.getAll('weekdays').map(Number); ['interval_minutes','limit','daily_quota'].forEach(key=>{body[key]=Number(body[key]);}); try { const settings=await post('/api/recruiting/automation/schedules',body); fillScheduleForm(source,{...settings,runtime:{state:body.enabled?'outside_window':'disabled'}}); renderAutomationScheduleSummary($('#product-automation-job').value); notify(source==='conversation'?'沟通列表定时任务已保存':source==='recommendation'?'推荐牛人定时任务已保存':'全流程定时任务已保存'); } catch(error) { $('#'+source+'-schedule-state').textContent=error.message; notify(error.message,true); } }));
  $('#candidate-job-filter').addEventListener('change',()=>{ const jobId=$('#candidate-job-filter').value; if(jobId) selectedJobId=jobId; renderCandidates(); });
  refresh(); window.setInterval(refresh,3000);
})();
"""


def render_console_page(session_token: str) -> str:
	"""渲染带临时写请求令牌的响应式本地控制台页面。"""
	return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 招聘控制台</title>
<style>
{_CONSOLE_CSS}
{_PRODUCT_WORKBENCH_CSS}
</style>
</head>
<body>
<div id="recruiting-product-shell" data-token={session_token!r}>{_PRODUCT_WORKBENCH_HTML}</div>
<a class="skip-link" href="#recruiting-workspace">跳到招聘工作台</a>
<header class="topbar"><div><div class="brand-line"><span class="brand-mark" aria-hidden="true">B</span><div><h1>BOSS 招聘控制台</h1><p class="subtle">从候选人导入到人工决策，所有数据留在本机。</p></div></div></div><div class="topbar-meta"><label class="context-picker">当前招聘上下文<select id="recruiting-context-select" aria-label="当前招聘上下文"></select><span id="recruiting-context-state" class="hint" aria-live="polite"></span></label><span class="meta-chip good">仅监听本机</span><span class="meta-chip">人工确认优先</span></div></header>
<main class="shell">
<div class="app-layout"><nav class="side-nav" aria-label="工作台导航"><div class="side-nav-heading">状态总览</div><a class="side-nav-link active" data-nav-link href="#login-panel" aria-current="location"><span class="nav-dot" aria-hidden="true"></span>登录状态</a><a class="side-nav-link" data-nav-link href="#dashboard-panel"><span class="nav-dot" aria-hidden="true"></span>招聘仪表盘</a><a class="side-nav-link" data-nav-link href="#loop-panel"><span class="nav-dot" aria-hidden="true"></span>闭环总览</a><a class="side-nav-link" data-nav-link href="#pacing-panel"><span class="nav-dot" aria-hidden="true"></span>安全节奏</a><div class="side-nav-heading">候选人来源</div><a class="side-nav-link" data-nav-link href="#conversation-panel"><span class="nav-dot" aria-hidden="true"></span>沟通候选人</a><a class="side-nav-link" data-nav-link href="#recommendation-panel"><span class="nav-dot" aria-hidden="true"></span>推荐牛人</a><a class="side-nav-link" data-nav-link href="#batch-export-panel"><span class="nav-dot" aria-hidden="true"></span>一键导出</a><a class="side-nav-link" data-nav-link href="#pipeline-panel"><span class="nav-dot" aria-hidden="true"></span>自动流水线</a><a class="side-nav-link" data-nav-link href="#resume-download-panel"><span class="nav-dot" aria-hidden="true"></span>简历导出</a><div class="side-nav-heading">招聘执行</div><a class="side-nav-link" data-nav-link href="#recruiting-workspace"><span class="nav-dot" aria-hidden="true"></span>招聘工作台</a><a class="side-nav-link" href="/preview/score-board"><span class="nav-dot" aria-hidden="true"></span>评分看板</a><a class="side-nav-link" data-nav-link href="#guide-panel"><span class="nav-dot" aria-hidden="true"></span>运行说明</a><div class="side-nav-foot">左侧只切换本地页面，不会额外访问平台。</div></nav><div class="workarea">
<div class="route-page" data-route-page="overview"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">状态总览</span><h2 class="page-title">登录与运行状态</h2><p>先确认登录态、运行模式和自动化额度，再进入候选人处理。</p></div><div class="page-head-aside"><span class="badge">仅本机 127.0.0.1</span></div></div><div class="grid">
<section class="panel" id="login-panel" aria-labelledby="login-heading"><div class="status"><div><div class="label" id="login-heading">登录状态</div><p class="state" id="login-state">正在读取</p></div><button id="login-button" type="button">打开 BOSS 登录页</button></div><div id="login-detail" class="notice" aria-live="polite">请先完成登录。</div></section>
<section class="panel" aria-labelledby="mode-heading"><div class="label" id="mode-heading">操作模式</div><p class="state" id="mode-state">正在读取</p><div id="mode-detail" class="notice" aria-live="polite"></div></section>
<section class="panel pacing-panel" id="pacing-panel" aria-labelledby="pacing-heading"><div class="status"><div><h2 id="pacing-heading">安全节奏</h2><p class="hint">只展示自动化引擎的额度和暂停原因；本地工作台不会代替你发送消息、加私域或邀约。</p></div><span id="pacing-summary" class="pacing-state" aria-live="polite">正在读取</span></div><div class="pacing-metrics" aria-live="polite"><div class="pacing-metric"><div class="pacing-metric-label">今日已用</div><div id="pacing-used" class="pacing-metric-value">--</div></div><div class="pacing-metric"><div class="pacing-metric-label">当前额度</div><div id="pacing-quota" class="pacing-metric-value">--</div></div><div class="pacing-metric"><div class="pacing-metric-label">剩余额度</div><div id="pacing-remaining" class="pacing-metric-value">--</div></div><div class="pacing-metric"><div class="pacing-metric-label">当前时段</div><div id="pacing-window" class="pacing-metric-value">--</div></div></div><div id="pacing-detail" class="notice pacing-detail" aria-live="polite">正在读取安全节奏状态…</div></section>
<section class="panel dashboard-panel" id="dashboard-panel" aria-labelledby="dashboard-heading"><div class="status"><div><h2 id="dashboard-heading">招聘闭环仪表盘</h2><p class="hint">基于本地工作台事实的实时指标；数字只来自已记录的阶段变化，不包含平台推测。</p></div></div><div class="dashboard-grid" aria-live="polite"><div class="metric-card"><div class="metric-card-header"><div class="metric-card-icon">📋</div><div class="metric-card-label">总岗位数</div></div><div class="metric-card-value" id="dash-jobs">--</div></div><div class="metric-card"><div class="metric-card-header"><div class="metric-card-icon">👤</div><div class="metric-card-label">候选人总量</div></div><div class="metric-card-value" id="dash-candidates">--</div><div class="metric-card-sub" id="dash-active-candidates"></div></div><div class="metric-card"><div class="metric-card-header"><div class="metric-card-icon">📊</div><div class="metric-card-label">已评估</div></div><div class="metric-card-value" id="dash-assessed">--</div><div class="metric-card-sub" id="dash-avg-score"></div></div><div class="metric-card"><div class="metric-card-header"><div class="metric-card-icon">✅</div><div class="metric-card-label">已录用</div></div><div class="metric-card-value" id="dash-hired">--</div><div class="metric-trend neutral" id="dash-hire-rate"></div></div></div><h3 style="margin-top:20px;">招聘漏斗</h3><div class="funnel" id="dash-funnel" aria-live="polite"><div class="funnel-row"><span class="funnel-label">候选人总量</span><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:100%"><span class="funnel-count" id="funnel-total">--</span></div></div></div></div><h3 style="margin-top:20px;">来源转化</h3><div class="dashboard-grid wide" id="dash-sources" aria-live="polite"><div class="metric-card"><div class="metric-card-label">BOSS 沟通候选人</div><div class="metric-card-value" id="dash-source-conversation">--</div><div class="metric-card-sub" id="dash-source-conversation-rate"></div></div><div class="metric-card"><div class="metric-card-label">BOSS 推荐牛人</div><div class="metric-card-value" id="dash-source-recommendation">--</div><div class="metric-card-sub" id="dash-source-recommendation-rate"></div></div><div class="metric-card"><div class="metric-card-label">本地导入简历</div><div class="metric-card-value" id="dash-source-local">--</div></div></div><h3 style="margin-top:20px;">话术效果排行</h3><div id="dash-templates" class="workspace-list" aria-live="polite"></div><h3 style="margin-top:20px;">热门 FAQ 问题</h3><div id="dash-faq-demands" class="workspace-list" aria-live="polite"></div></section>
<section class="panel loop-panel" id="loop-panel" aria-labelledby="loop-heading"><div class="status"><div><h2 id="loop-heading">闭环总览</h2><div id="loop-next" class="loop-next" aria-live="polite">正在读取当前下一步…</div><p class="hint">下一步来自本地工作台真实待办；完成官方页面动作后，在对应待办里回写结果。</p></div><button id="loop-action" class="secondary" type="button">去处理</button></div><div class="loop-summary-grid" aria-live="polite"><div class="loop-metric"><div class="loop-metric-label">待处理</div><div id="loop-pending" class="loop-metric-value">--</div></div><div class="loop-metric"><div class="loop-metric-label">活跃候选人</div><div id="loop-active" class="loop-metric-value">--</div></div><div class="loop-metric"><div class="loop-metric-label">已进入终局</div><div id="loop-terminal" class="loop-metric-value">--</div></div><div class="loop-metric"><div class="loop-metric-label">当前岗位</div><div id="loop-job" class="loop-metric-value">--</div></div></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="conversations"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">候选人来源</span><h2 class="page-title">沟通候选人导出</h2><p>读取官方沟通列表，按候选人导出已有简历资料，再交给招聘工作台。</p></div><div class="page-head-aside"><span class="badge">只读 · 用户主动触发</span></div></div><div class="grid grid-single">
<section class="panel" id="conversation-panel" aria-labelledby="conversation-download-heading"><div class="status"><div><h2 id="conversation-download-heading">沟通候选人</h2><p class="hint">与 BOSS 沟通列表保持同一排序，选择候选人后下载资料。</p></div><div class="actions"><button id="conversation-list-refresh-button" class="secondary" type="button">刷新列表</button><button id="batch-analyze-button" class="secondary" type="button" title="按顺序分析所有未分析过的候选人（跳过已分析的）">一键分析全部</button></div></div><div class="conversation-toolbar"><label>筛选沟通候选人<input id="conversation-filter" type="search" placeholder="姓名、职位、公司或城市" autocomplete="off"></label><span id="conversation-filter-count" class="conversation-filter-count" aria-live="polite"></span><a class="secondary-link" href="https://www.zhipin.com/web/chat/index" target="_blank" rel="noopener noreferrer">打开官方沟通页</a></div><div id="conversation-list-action-state" class="conversation-action-state hint" aria-live="polite"></div><div id="single-analysis-result" class="workspace-list" aria-live="polite" style="display:none;"></div><div id="conversation-list" class="conversation-list" aria-live="polite"></div><div class="actions"><button id="latest-conversation-download-button" class="secondary" type="button">下载最近一位</button><span id="latest-conversation-download-state" class="hint" aria-live="polite"></span><button id="current-conversation-download-button" class="secondary" type="button" title="先在官方沟通页选中候选人">下载当前沟通</button><span id="current-conversation-download-state" class="hint" aria-live="polite">先在官方沟通页选中候选人</span></div><details class="form"><summary class="hint">按会话 ID 导出（高级）</summary><form id="conversation-download-form" class="form"><label>沟通会话 ID<input name="friend_id" required inputmode="numeric" pattern="[0-9]+" autocomplete="off"></label><label>导出目录（可选）<input name="output_dir" autocomplete="off" placeholder="默认保存到桌面"></label><div class="actions"><button id="conversation-download-button" type="submit">按会话 ID 导出</button><span id="conversation-download-state" class="hint" aria-live="polite"></span></div></form></details><div id="conversation-download-result" class="result" hidden></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="recommendations"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">候选人来源</span><h2 class="page-title">推荐牛人导出</h2><p>读取平台推荐列表，只导出已有在线简历，不发起任何触达动作。</p></div><div class="page-head-aside"><span class="badge">只读 · 用户主动触发</span></div></div><div class="grid grid-single">
<section class="panel" id="recommendation-panel" aria-labelledby="recommendation-heading"><div class="status"><div><h2 id="recommendation-heading">推荐牛人</h2><p class="hint">先读取已发布职位并选择一个职位，再按候选人卡片下载已有在线简历。</p></div><div class="actions"><button id="recommendation-refresh-button" class="secondary" type="button">读取推荐列表</button></div></div><form id="recommendation-filter-form" class="form"><label>BOSS 已发布职位<select id="recommendation-job-select" name="job_id" disabled><option value="">请先读取职位</option></select></label><div class="actions"><button id="recommendation-job-refresh-button" class="secondary" type="button">读取 BOSS 职位</button><span id="recommendation-job-state" class="hint" aria-live="polite">职位列表尚未读取</span></div></form><div id="recommendation-action-state" class="conversation-action-state hint" aria-live="polite"></div><div id="recommendation-list" class="conversation-list" aria-live="polite"></div><div id="recommendation-download-result" class="result" hidden></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="batch"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">候选人来源</span><h2 class="page-title">一键批量导出</h2><p>按平台排序取前 N 位，串行导出已有简历；也可以先只扫描谁已分享附件。</p></div><div class="page-head-aside"><span class="badge">只读 · 可随时停止</span></div></div><div class="grid grid-single">
<section class="panel" id="batch-export-panel" aria-labelledby="batch-export-heading"><div class="status"><div><h2 id="batch-export-heading">一键批量导出</h2><p class="hint">批量只是把单人导出重复执行：不发消息、不索要附件、不交换联系方式。每位候选人消耗一次安全节奏额度，额度耗尽或登录失效会自动停批。</p></div><span id="batch-export-state" class="pacing-state" aria-live="polite">未开始</span></div><form id="batch-export-form" class="form"><label>候选人来源<select id="batch-export-source" name="source"><option value="conversation">沟通候选人</option><option value="recommendation">推荐牛人</option></select></label><label>处理数量<select id="batch-export-limit" name="limit"><option value="10">前 10 位</option><option value="20" selected>前 20 位</option><option value="50">前 50 位</option></select></label><label>职位 ID（推荐牛人可选）<input id="batch-export-job-id" name="job_id" autocomplete="off" placeholder="不填使用平台当前职位"></label><label>导出目录（可选）<input id="batch-export-output-dir" name="output_dir" autocomplete="off" placeholder="默认保存到桌面"></label><div class="actions"><button id="batch-export-start" type="submit">一键导出这些人</button><button id="batch-export-scan" class="secondary" type="button">只扫描附件（不下载）</button><button id="batch-export-stop" class="secondary" type="button" disabled>停止</button><span id="batch-export-action-state" class="hint" aria-live="polite"></span></div></form><div class="batch-progress"><div id="batch-export-summary" class="batch-progress-summary">尚未开始</div><div class="insight-bar"><span id="batch-export-bar" class="insight-bar-fill" style="width:0%"></span></div><div id="batch-export-meta" class="batch-progress-meta">选择来源和数量后点击“一键导出这些人”。</div></div><div id="batch-export-notice" class="notice" aria-live="polite"></div><div class="actions"><button id="batch-export-import-all" class="secondary" type="button" disabled>全部导入工作台</button><span id="batch-export-import-state" class="hint" aria-live="polite"></span></div><div id="batch-export-results" class="workspace-list" aria-live="polite"></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="pipeline"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">候选人来源</span><h2 class="page-title">BossPipeline 自动流水线</h2><p>读取沟通列表，索要附件简历，下载在线和附件简历，AI 深度分析，合格候选人自动入库。</p></div><div class="page-head-aside"><span class="badge">自动化 · 可随时停止</span></div></div><div class="grid grid-single">
<section class="panel" id="pipeline-panel" aria-labelledby="pipeline-heading"><div class="status"><div><h2 id="pipeline-heading">BossPipeline 自动流水线</h2><p class="hint">每一步都有详细日志：AI 收到的 Prompt 和返回的 JSON 都会在下方日志区完整展示，方便调试。</p></div><span id="pipeline-state-chip" class="pacing-state" aria-live="polite">未开始</span></div><div class="pipeline-controls"><label>处理数量<select id="pipeline-limit"><option value="5">前 5 位</option><option value="10">前 10 位</option><option value="20" selected>前 20 位</option><option value="50">前 50 位</option></select></label><label>入库分数线<input id="pipeline-threshold" type="number" min="1" max="100" value="70" step="5"></label><label class="toggle-label"><input id="pipeline-ask-resume" type="checkbox" checked> 索要附件简历</label><div class="actions"><button id="pipeline-start" type="button">启动流水线</button><button id="pipeline-stop" class="secondary" type="button" disabled>停止</button><span id="pipeline-action-state" class="hint" aria-live="polite"></span></div></div><div id="pipeline-summary" class="pipeline-summary-text">选择参数后点击"启动流水线"。</div><div class="pipeline-summary" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;"><div class="pipeline-step"><div class="pipeline-step-label">已处理</div><div id="pipeline-processed" class="pipeline-step-count">0</div></div><div class="pipeline-step"><div class="pipeline-step-label">索要消息</div><div id="pipeline-asked" class="pipeline-step-count">0</div></div><div class="pipeline-step"><div class="pipeline-step-label">在线简历</div><div id="pipeline-online" class="pipeline-step-count">0</div></div><div class="pipeline-step"><div class="pipeline-step-label">附件简历</div><div id="pipeline-attach" class="pipeline-step-count">0</div></div><div class="pipeline-step"><div class="pipeline-step-label">已入库</div><div id="pipeline-pool" class="pipeline-step-count">0</div></div><div class="pipeline-step"><div class="pipeline-step-label">失败</div><div id="pipeline-failed-count" class="pipeline-step-count">0</div></div></div><div id="pipeline-notice" class="notice" aria-live="polite"></div><div id="pipeline-log-viewer" class="pipeline-log-viewer" aria-live="polite" role="log" aria-label="流水线实时日志"><div style="color:#5c7a9e;">等待流水线启动…</div></div><div id="pipeline-results" class="workspace-list" aria-live="polite"></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="export"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">候选人来源</span><h2 class="page-title">简历导出</h2><p>已掌握候选人、职位和安全标识时的单份在线简历导出入口。</p></div><div class="page-head-aside"><span class="badge">默认保存到桌面</span></div></div><div class="grid grid-single">
<section class="panel" id="resume-download-panel" aria-labelledby="download-heading"><h2 id="download-heading">按已知标识下载在线简历</h2><p class="hint">用于已掌握候选人、职位和安全标识的单份在线简历导出。</p><form id="download-form" class="form"><label>候选人 Geek ID<input name="geek_id" required autocomplete="off"></label><label>职位 Job ID<input name="job_id" required autocomplete="off"></label><label>Security ID<input name="security_id" required autocomplete="off"></label><label>导出目录（可选）<input name="output_dir" autocomplete="off" placeholder="默认保存到桌面"></label><div class="actions"><button id="download-button" type="submit">下载到本地</button><span id="download-state" class="hint" aria-live="polite"></span></div></form><div id="download-result" class="result" hidden></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="workspace"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">招聘执行</span><h2 class="page-title">招聘工作台</h2><p>岗位标准、知识库、候选人评估和待办闭环都在本机完成，平台动作仍由你手动执行。</p></div><div class="page-head-aside"><span class="badge">人工确认优先</span></div></div><div class="grid grid-single">
 <section class="panel workspace-panel" id="recruiting-workspace" aria-labelledby="recruiting-workspace-heading"><div class="status"><div><h2 id="recruiting-workspace-heading">招聘工作台</h2><p class="hint">岗位标准、知识库和简历评估在本机保存；最终判断需要人工确认，不会自动发送 BOSS 消息。</p></div><span id="recruiting-state" class="label" aria-live="polite">正在读取</span></div><div id="recruiting-action-state" class="notice" aria-live="polite"></div><div class="workspace-intro"><div><strong>招聘漏斗</strong><br><span>每次阶段变化都保留时间、动作和人工备注，便于复盘。</span></div><span>下载简历不会自动推进阶段</span></div><div id="recruiting-view-switcher" class="workspace-view-switcher" role="tablist" aria-label="招聘工作台视图"><button type="button" role="tab" aria-selected="true" data-workspace-view="operations">处理队列</button><button type="button" role="tab" aria-selected="false" data-workspace-view="setup">岗位与知识</button><button type="button" role="tab" aria-selected="false" data-workspace-view="review">评估与复盘</button></div><div id="recruiting-pipeline-summary" class="pipeline-summary" aria-live="polite" data-workspace-view-content="operations"></div><div class="workspace-grid"><div class="workspace-block workspace-setup-block" data-workspace-view-content="setup"><h3>岗位标准</h3><form id="recruiting-job-form" class="form"><label>岗位名称<input name="name" required maxlength="120" placeholder="例如：电话销售顾问"></label><label>城市<input name="city" maxlength="80" placeholder="例如：杭州"></label><label>薪资范围<input name="salary_range" maxlength="80" placeholder="例如：10-20K"></label><label>最低学历<input name="education_requirement" maxlength="80" placeholder="例如：大专及以上"></label><label>最低工作年限<input name="min_experience_years" type="number" min="0" max="100" step="1" inputmode="numeric" placeholder="例如：2"></label><label>自然语言招聘标准<textarea name="criteria_text" maxlength="4000" placeholder="例如：必须有电话销售经验；招商加盟经验优先；不接受频繁跳槽"></textarea></label><div class="actions"><button type="submit">保存岗位</button><span class="hint">学历和年限用于发布前完整性校验；其他标准可继续用自然语言填写。</span></div></form><div id="recruiting-job-warnings" class="notice warn" hidden></div><div class="form"><label>当前岗位<select id="recruiting-job-select"></select></label></div><div id="recruiting-criteria-preview" class="criteria-preview"></div></div><div class="workspace-block workspace-setup-block" data-workspace-view-content="setup"><h3>企业知识库</h3><form id="recruiting-knowledge-form" class="form"><label>知识类别<select name="category"><option value="sales">销售流程</option><option value="company">企业与岗位</option></select></label><label>标题<input name="title" required maxlength="160"></label><label>正文<textarea name="content" required maxlength="20000" placeholder="记录可对外说明的业务事实和流程"></textarea></label><div class="actions"><button type="submit">保存知识</button></div></form><form id="recruiting-knowledge-import-form" class="form"><label>导入本地文件（.md / .txt / .docx）<input name="source_path" required maxlength="4096" placeholder="例如：D:\\资料\\销售手册.docx"></label><div class="actions"><button type="submit" class="secondary">导入并生成问题来源</button><span class="hint">只读取你明确指定的本地文件，不上传到外部服务。</span></div></form><form id="recruiting-knowledge-search-form" class="form"><label>检索本地事实<input id="recruiting-knowledge-search-input" name="query" required maxlength="200" placeholder="例如：工作时间、销售流程"></label><div class="actions"><button type="submit" class="secondary">检索并显示来源</button><span id="recruiting-knowledge-search-state" class="hint" aria-live="polite"></span></div></form><div id="recruiting-knowledge-search-results" class="citation-list" aria-live="polite"></div><div id="recruiting-knowledge-list" class="workspace-list"></div></div><div class="workspace-block workspace-setup-block" data-workspace-view-content="setup"><h3>候选人 FAQ</h3><form id="recruiting-faq-form" class="form"><label>问题<input name="question" required maxlength="500"></label><label>标准答案<textarea name="answer" required maxlength="4000"></textarea></label><label>允许变化说明<textarea name="allowed_variation" maxlength="1000" placeholder="哪些说法可以自然变化"></textarea></label><div class="actions"><button type="submit">保存 FAQ</button></div></form><div id="recruiting-faq-list" class="workspace-list"></div></div><div class="workspace-block workspace-setup-block" data-workspace-view-content="setup"><h3>话术模板</h3><p class="hint">管理可复用的沟通话术，在实际沟通中标记使用后，仪表盘会按效果排行。</p><form id="recruiting-template-form" class="form"><input type="hidden" name="template_id"><label>模板标识<select name="category"><option value="greeting">打招呼</option><option value="follow_up">跟进</option><option value="qa_guide">问答引导</option><option value="resume_request">索要简历</option><option value="interview_invite">面试邀约</option><option value="private_domain">私域转化</option><option value="rejection">婉拒</option></select></label><label>唯一标识 key<input name="template_key" required maxlength="128" placeholder="例如：sales_first_greeting"></label><label>标题<input name="title" maxlength="160" placeholder="例如：销售岗首次打招呼"></label><label>话术正文<textarea name="body" required maxlength="5000" placeholder="输入可复用的沟通话术…"></textarea></label><div class="actions"><button type="submit">保存模板</button><button type="button" id="recruiting-template-new" class="secondary">新建空白</button><span id="recruiting-template-state" class="hint" aria-live="polite"></span></div></form><div id="recruiting-template-list" class="template-list" aria-live="polite"></div></div><div class="workspace-block workspace-review-block" data-workspace-view-content="review"><h3>评估与候选人</h3><form id="recruiting-candidate-form" class="form"><label>本地 Markdown/TXT 简历路径<input name="resume_path" required maxlength="4096" placeholder="例如：C:\\Users\\...\\候选人.md"></label><div class="actions"><button type="submit">导入候选人</button><span class="hint">导入后会保留来源并自动选中候选人。</span></div></form><div id="recruiting-candidate-list" class="workspace-list"></div><form id="recruiting-assess-form" class="form"><label>评估岗位<select name="job_id" id="recruiting-assess-job"></select></label><label>评估候选人<select name="candidate_id" id="recruiting-assess-candidate"></select></label><div class="actions"><button type="submit">生成评估</button><span class="hint">规则结果只做初筛，不能替代 HR 决策。</span></div></form><div id="recruiting-assessment-result" class="workspace-list"></div></div></div></section>
</div></div>
<div class="route-page route-hidden" data-route-page="guide"><div class="page-head"><div class="page-head-title"><span class="page-head-eyebrow">帮助</span><h2 class="page-title">本地运行说明</h2><p>三步跑通本地闭环：登录、显式启用研究模式、按候选人导出资料。</p></div></div><div class="grid grid-single">
<aside class="panel" id="guide-panel" aria-labelledby="guide-heading"><h2 id="guide-heading">运行说明</h2><div class="status-list"><div class="status-item"><strong>1. 登录</strong><br><span class="hint">点击按钮后，在打开的官方页面扫码或确认登录。</span></div><div class="status-item"><strong>2. 启用研究模式</strong><br><span class="hint">下载候选人简历前，需在终端显式运行 <code>boss config set operating_mode research</code>。</span></div><div class="status-item"><strong>3. 会话导出</strong><br><span class="hint">选择候选人并点击“下载简历”，即可导出已有在线简历和附件。</span></div></div></aside>
</div></div>
</div></div>
</main>
<script>
const token = {session_token!r};
const recruitingContextSelect = document.querySelector('#recruiting-context-select');
const recruitingContextState = document.querySelector('#recruiting-context-state');
const loginButton = document.querySelector('#login-button');
const downloadButton = document.querySelector('#download-button');
const conversationDownloadButton = document.querySelector('#conversation-download-button');
const currentConversationDownloadButton = document.querySelector('#current-conversation-download-button');
const latestConversationDownloadButton = document.querySelector('#latest-conversation-download-button');
const conversationListRefreshButton = document.querySelector('#conversation-list-refresh-button');
const conversationList = document.querySelector('#conversation-list');
const conversationListActionState = document.querySelector('#conversation-list-action-state');
const conversationFilter = document.querySelector('#conversation-filter');
const conversationFilterCount = document.querySelector('#conversation-filter-count');
const recommendationRefreshButton = document.querySelector('#recommendation-refresh-button');
const recommendationJobSelect = document.querySelector('#recommendation-job-select');
const recommendationJobRefreshButton = document.querySelector('#recommendation-job-refresh-button');
const recommendationJobState = document.querySelector('#recommendation-job-state');
const recommendationList = document.querySelector('#recommendation-list');
const recommendationActionState = document.querySelector('#recommendation-action-state');
const recommendationDownloadResultBox = document.querySelector('#recommendation-download-result');
const loginState = document.querySelector('#login-state');
const loginDetail = document.querySelector('#login-detail');
const modeState = document.querySelector('#mode-state');
const modeDetail = document.querySelector('#mode-detail');
const pacingSummary = document.querySelector('#pacing-summary');
const pacingUsed = document.querySelector('#pacing-used');
const pacingQuota = document.querySelector('#pacing-quota');
const pacingRemaining = document.querySelector('#pacing-remaining');
const pacingWindow = document.querySelector('#pacing-window');
const pacingDetail = document.querySelector('#pacing-detail');
const loopPanel = document.querySelector('#loop-panel');
const loopNext = document.querySelector('#loop-next');
const loopAction = document.querySelector('#loop-action');
const loopPending = document.querySelector('#loop-pending');
const loopActive = document.querySelector('#loop-active');
const loopTerminal = document.querySelector('#loop-terminal');
const loopJob = document.querySelector('#loop-job');
const downloadState = document.querySelector('#download-state');
const resultBox = document.querySelector('#download-result');
const conversationDownloadState = document.querySelector('#conversation-download-state');
const currentConversationDownloadState = document.querySelector('#current-conversation-download-state');
const latestConversationDownloadState = document.querySelector('#latest-conversation-download-state');
const conversationResultBox = document.querySelector('#conversation-download-result');
const recruitingState = document.querySelector('#recruiting-state');
const recruitingActionState = document.querySelector('#recruiting-action-state');
const recruitingJobSelect = document.querySelector('#recruiting-job-select');
// 岗位状态控件由脚本补到现有页面，避免破坏旧版 HTML 快照和已有导航锚点。
const recruitingPublishJobButton = document.createElement('button');
recruitingPublishJobButton.id='recruiting-publish-job-button'; recruitingPublishJobButton.type='button'; recruitingPublishJobButton.className='secondary';
recruitingPublishJobButton.textContent='\u53d1\u5e03\u5c97\u4f4d'; recruitingPublishJobButton.disabled=true;
const recruitingJobForm = document.querySelector('#recruiting-job-form');
const recruitingJobActions = recruitingJobForm && recruitingJobForm.querySelector('.actions');
if (recruitingJobActions) recruitingJobActions.append(recruitingPublishJobButton);
// 同步按钮只读取当前已登录账号的 BOSS 职位并写入本地镜像；岗位标准仍由 HR
// 在下面的表单逐个补齐并发布，不能把“同步”误解成向平台发布或修改职位。
const recruitingSyncBossJobsButton=document.createElement('button');
recruitingSyncBossJobsButton.id='recruiting-sync-boss-jobs-button'; recruitingSyncBossJobsButton.type='button'; recruitingSyncBossJobsButton.className='secondary'; recruitingSyncBossJobsButton.textContent='同步 BOSS 职位';
const recruitingSyncBossJobsState=document.createElement('span'); recruitingSyncBossJobsState.className='hint'; recruitingSyncBossJobsState.setAttribute('aria-live','polite');
if (recruitingJobActions) recruitingJobActions.append(recruitingSyncBossJobsButton,recruitingSyncBossJobsState);
recruitingSyncBossJobsButton.addEventListener('click',async () => {{
  recruitingSyncBossJobsButton.disabled=true; recruitingSyncBossJobsState.textContent='正在读取并同步职位…';
  const payload=await post('/api/recruiting/jobs/sync-boss',{{}});
  if(!payload.ok) {{ recruitingSyncBossJobsState.textContent=(payload.error && payload.error.message) || '同步失败'; recruitingSyncBossJobsButton.disabled=false; return; }}
  const result=payload.data || {{}}; recruitingSyncBossJobsState.textContent=`已同步：新增 ${{result.created || 0}}，更新 ${{result.updated || 0}}`;
  recruitingSyncBossJobsButton.disabled=false; await refreshRecruiting();
}});
// 岗位开关由脚本创建，兼容旧版页面快照，同时确保新岗位表单始终有明确的专业问答配置。
const recruitingProfessionalQaSetting=document.createElement('label'); recruitingProfessionalQaSetting.className='toggle-label';
const recruitingProfessionalQaToggle=document.createElement('input'); recruitingProfessionalQaToggle.type='checkbox'; recruitingProfessionalQaToggle.name='professional_qa_enabled'; recruitingProfessionalQaToggle.checked=true;
recruitingProfessionalQaSetting.append(recruitingProfessionalQaToggle,document.createTextNode('启用 BOSS 专业问答'));
const recruitingProfessionalQaHint=document.createElement('span'); recruitingProfessionalQaHint.className='hint'; recruitingProfessionalQaHint.textContent='关闭后不生成 BOSS 问答待办，必要的专业核验由 HR 在私域人工承接。';
const recruitingGreetingField=document.createElement('label'); recruitingGreetingField.textContent='推荐牛人打招呼语';
const recruitingGreetingInput=document.createElement('textarea'); recruitingGreetingInput.name='greeting_message'; recruitingGreetingInput.maxLength=100; recruitingGreetingInput.placeholder='例如：您好，方便确认一下您是否接受广州天河区通勤吗？'; recruitingGreetingField.append(recruitingGreetingInput);
const recruitingCriteriaField=recruitingJobForm && recruitingJobForm.elements.namedItem('criteria_text');
if(recruitingCriteriaField && recruitingCriteriaField.parentElement) {{ recruitingCriteriaField.parentElement.before(recruitingProfessionalQaSetting,recruitingProfessionalQaHint,recruitingGreetingField); }}
const recruitingJobStatus = document.createElement('div'); recruitingJobStatus.className='notice';
recruitingJobStatus.setAttribute('aria-live','polite');
if (recruitingJobForm) recruitingJobForm.insertAdjacentElement('afterend', recruitingJobStatus);
let recruitingEditingJobId = '';
let recruitingCreatingNewJob = false;
const recruitingNewJobButton = document.createElement('button'); recruitingNewJobButton.type='button'; recruitingNewJobButton.className='secondary'; recruitingNewJobButton.textContent='\u65b0\u5efa\u5c97\u4f4d';
if (recruitingJobActions) recruitingJobActions.append(recruitingNewJobButton);
if (recruitingJobForm) recruitingJobForm.addEventListener('input', () => {{ recruitingJobForm.dataset.dirty='1'; }});
function fillRecruitingJobForm(job, force=false) {{
  if (!recruitingJobForm || !job) return;
  if (!force && recruitingJobForm.dataset.loadedJob===job.job_id && recruitingJobForm.dataset.dirty==='1') return;
  ['name','city','salary_range','education_requirement','min_experience_years'].forEach(key => {{ const input=recruitingJobForm.elements.namedItem(key); if(input) input.value=job[key] === null || job[key] === undefined ? '' : job[key]; }});
  if(recruitingProfessionalQaToggle) recruitingProfessionalQaToggle.checked=job.professional_qa_enabled !== false;
  if(recruitingGreetingInput) recruitingGreetingInput.value=job.greeting_message || '';
  const criteria=recruitingJobForm.elements.namedItem('criteria_text');
  if(criteria && (force || recruitingJobForm.dataset.dirty!=='1')) {{
    // 回填时保留四类标准的前缀；否则用户只修改城市再保存，淘汰项和风险项
    // 会被解析器当成普通必选项，导致后续评估标准静默漂移。
    const criteriaParts=[...(job.criteria && job.criteria.must_have || []).map(item => `必须${{item}}`),...(job.criteria && job.criteria.nice_to_have || []).map(item => `优先${{item}}`),...(job.criteria && job.criteria.reject_if || []).map(item => `不接受${{item}}`),...(job.criteria && job.criteria.risk_signals || []).map(item => `风险：${{item}}`)];
    criteria.value=criteriaParts.join('；');
  }}
  recruitingJobForm.dataset.loadedJob=job.job_id; recruitingJobForm.dataset.dirty='0';
}}
const recruitingAssessJob = document.querySelector('#recruiting-assess-job');
const recruitingAssessCandidate = document.querySelector('#recruiting-assess-candidate');
const recruitingAssessButton = document.querySelector('#recruiting-assess-form button[type="submit"]');
const recruitingJobWarnings = document.querySelector('#recruiting-job-warnings');
const recruitingCriteriaPreview = document.querySelector('#recruiting-criteria-preview');
const recruitingKnowledgeList = document.querySelector('#recruiting-knowledge-list');
const recruitingKnowledgeForm = document.querySelector('#recruiting-knowledge-form');
const recruitingKnowledgeImportForm = document.querySelector('#recruiting-knowledge-import-form');
// 通过脚本补齐知识范围选择，兼容旧缓存页面，同时让手工录入和文件导入
// 共用同一套用途边界；范围只影响本地检索，不会触发任何平台动作。
const knowledgeAudienceOptions=[['internal','内部评估'],['candidate','候选人可见'],['shared','内部与候选人共用']];
const knowledgeAudienceLabels=Object.fromEntries(knowledgeAudienceOptions);
const ensureKnowledgeAudienceField=(form, defaultValue) => {{
  if(!form || form.querySelector('[name="audience"]')) return;
  const label=document.createElement('label'); label.textContent='知识范围';
  const select=document.createElement('select'); select.name='audience';
  knowledgeAudienceOptions.forEach(([value,text]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=text; option.selected=value===defaultValue; select.append(option); }});
  label.append(select); form.insertBefore(label, form.firstElementChild);
}};
ensureKnowledgeAudienceField(recruitingKnowledgeForm,'candidate');
ensureKnowledgeAudienceField(recruitingKnowledgeImportForm,'candidate');
const recruitingFaqList = document.querySelector('#recruiting-faq-list');
// FAQ 草稿区故意由脚本创建：它与手工 FAQ 表单分离，确保“生成”不会暗中写入 Store。
const recruitingFaqDrafts = document.createElement('div');
recruitingFaqDrafts.id='recruiting-faq-drafts';
recruitingFaqDrafts.className='workspace-list';
recruitingFaqDrafts.setAttribute('aria-live','polite');
const recruitingFaqDraftButton = document.createElement('button');
recruitingFaqDraftButton.id='recruiting-faq-draft-button';
recruitingFaqDraftButton.type='button';
recruitingFaqDraftButton.className='secondary';
recruitingFaqDraftButton.textContent='生成 FAQ 草稿';
const recruitingFaqDraftState = document.createElement('span');
recruitingFaqDraftState.id='recruiting-faq-draft-state';
recruitingFaqDraftState.className='hint';
recruitingFaqDraftState.setAttribute('aria-live','polite');
const recruitingFaqDraftBlock = document.createElement('div');
recruitingFaqDraftBlock.className='workspace-block';
recruitingFaqDraftBlock.innerHTML='<!-- id="recruiting-faq-drafts" --><p class="hint">草稿只来自当前岗位知识，保存前可编辑；状态 pending_review 表示等待人工审核。</p>';
const recruitingFaqDraftActions = document.createElement('div');
recruitingFaqDraftActions.className='actions';
recruitingFaqDraftActions.append(recruitingFaqDraftButton,recruitingFaqDraftState);
recruitingFaqDraftBlock.append(recruitingFaqDraftActions,recruitingFaqDrafts);
recruitingFaqList.insertAdjacentElement('beforebegin',recruitingFaqDraftBlock);
const recruitingKnowledgeSearchForm = document.querySelector('#recruiting-knowledge-search-form');
const recruitingKnowledgeSearchInput = document.querySelector('#recruiting-knowledge-search-input');
const recruitingKnowledgeSearchState = document.querySelector('#recruiting-knowledge-search-state');
const recruitingKnowledgeSearchResults = document.querySelector('#recruiting-knowledge-search-results');
// 受控试答紧邻检索结果展示：它只调用本地知识接口，方便 HR 核对来源后手动复制。
const recruitingKnowledgeAnswerForm = document.createElement('form');
recruitingKnowledgeAnswerForm.className='form';
recruitingKnowledgeAnswerForm.innerHTML='<label>候选人问题试答<input id="recruiting-knowledge-answer-input" name="question" required maxlength="200" placeholder="例如：工作时间和社保怎么安排？"></label><div class="actions"><button type="submit" class="secondary">生成有来源的试答</button><span id="recruiting-knowledge-answer-state" class="hint" aria-live="polite"></span></div><div id="recruiting-knowledge-answer-result" class="citation-list" aria-live="polite"></div>';
if(recruitingKnowledgeSearchForm) recruitingKnowledgeSearchForm.insertAdjacentElement('afterend', recruitingKnowledgeAnswerForm);
const recruitingKnowledgeAnswerInput = recruitingKnowledgeAnswerForm.querySelector('#recruiting-knowledge-answer-input');
const recruitingKnowledgeAnswerState = recruitingKnowledgeAnswerForm.querySelector('#recruiting-knowledge-answer-state');
const recruitingKnowledgeAnswerResult = recruitingKnowledgeAnswerForm.querySelector('#recruiting-knowledge-answer-result');
const recruitingCandidateList = document.querySelector('#recruiting-candidate-list');
const recruitingAssessmentResult = document.querySelector('#recruiting-assessment-result');
// 原始表单和列表均保留，只在浏览器端重新组织成“候选人主列 + 报告侧栏”。
// 这样不会改变既有表单标识、事件绑定或 API 契约。
const recruitingAssessmentWorkbench = recruitingCandidateList.closest('.workspace-block');
recruitingAssessmentWorkbench.classList.add('assessment-workbench');
const recruitingAssessmentForm = document.querySelector('#recruiting-assess-form');
const recruitingAssessmentLayout = document.createElement('div');
recruitingAssessmentLayout.id = 'recruiting-assessment-layout';
recruitingAssessmentLayout.className = 'assessment-layout';
recruitingAssessmentForm.insertAdjacentElement('afterend', recruitingAssessmentLayout);
recruitingAssessmentLayout.append(recruitingCandidateList, recruitingAssessmentResult);
const recruitingPipelineSummary = document.querySelector('#recruiting-pipeline-summary');
// 自动化面板独立于旧导出流水线：它只驱动硬筛、两阶段问答和附件终审。
const recruitingAutomationPanel=document.createElement('section'); recruitingAutomationPanel.className='workspace-block'; recruitingAutomationPanel.dataset.workspaceViewContent='operations'; recruitingAutomationPanel.innerHTML='<h3>BOSS 自动化</h3><div class="actions"><button type="button" id="automation-sync">同步沟通列表</button><button type="button" id="automation-start-conversation">开始沟通列表自动化</button><button type="button" id="automation-start-recommendation">开始推荐牛人自动化</button><button type="button" id="automation-pause" class="secondary">暂停</button><button type="button" id="automation-resume" class="secondary">继续</button><button type="button" id="automation-stop" class="secondary">停止</button></div><p id="automation-state" class="notice" aria-live="polite">请选择已发布岗位后同步沟通列表。</p><div id="automation-activities" class="workspace-list" aria-live="polite"></div><h3>当前岗位合格候选人</h3><div id="automation-qualified" class="workspace-list" aria-live="polite"></div><h3>当前岗位处理队列</h3><div id="automation-queue" class="workspace-list" aria-live="polite"></div>';
if(recruitingPipelineSummary && recruitingPipelineSummary.parentElement) recruitingPipelineSummary.parentElement.insertBefore(recruitingAutomationPanel,recruitingPipelineSummary.nextSibling);
// 推荐与约面试都依赖当前岗位。设置模块集中保存 BOSS 岗位招呼语和面试表单，
// 让候选人列表只负责选择候选人，不再把配置字段散落到每一行操作里。
const recruitingAutomationSettings=document.createElement('section'); recruitingAutomationSettings.className='workspace-block'; recruitingAutomationSettings.id='automation-interview-settings'; recruitingAutomationSettings.dataset.workspaceViewContent='settings'; recruitingAutomationSettings.innerHTML='<h3>设置</h3><p class="hint">后续动作只对当前岗位附件终审达标并进入候选人池的人执行。开启约面试后，换微信或换电话任一成功即可约面试。</p><form id="automation-settings-form" class="form"><fieldset><legend>达标候选人后续自动化</legend><label><input id="automation-followup-wechat" type="checkbox" name="wechat_enabled"> 自动换微信</label><label><input id="automation-followup-phone" type="checkbox" name="phone_enabled"> 自动换电话</label><label><input id="automation-followup-interview" type="checkbox" name="interview_enabled"> 自动约面试</label></fieldset><label>推荐牛人打招呼语<textarea id="automation-greeting-message" name="greeting_message" maxlength="100" placeholder="您好，感谢关注岗位。方便确认您的最高学历、相关工作年限、所在城市及到岗时间吗？"></textarea></label><label>面试方式<select id="automation-interview-mode" name="mode"><option value="online">线上面试</option><option value="offline">线下面试</option></select></label><label>面试日期<input id="automation-interview-date" name="date" type="date"></label><label>面试时间<input id="automation-interview-time" name="time" type="time"></label><label>面试地点<input id="automation-interview-address" name="address" maxlength="200" placeholder="线下面试填写"></label><label>备注<textarea id="automation-interview-note" name="note" maxlength="500"></textarea></label><label>联系人<input id="automation-interview-contact-name" name="contact_name" maxlength="60"></label><label>联系人电话<input id="automation-interview-contact-phone" name="contact_phone" maxlength="32"></label><div class="actions"><button type="submit" id="automation-settings-save">保存设置</button><button type="button" class="secondary" id="automation-export-csv">导出 CSV</button><button type="button" class="secondary" id="automation-export-xlsx">导出 Excel</button><span id="automation-settings-state" class="hint" aria-live="polite"></span></div></form>';
recruitingAutomationPanel.insertAdjacentElement('beforebegin',recruitingAutomationSettings);
const automationSyncButton=document.querySelector('#automation-sync'); const automationConversationButton=document.querySelector('#automation-start-conversation'); const automationRecommendationButton=document.querySelector('#automation-start-recommendation'); const automationPauseButton=document.querySelector('#automation-pause'); const automationResumeButton=document.querySelector('#automation-resume'); const automationStopButton=document.querySelector('#automation-stop'); const automationState=document.querySelector('#automation-state'); const automationActivities=document.querySelector('#automation-activities'); const automationQualified=document.querySelector('#automation-qualified'); const automationQueue=document.querySelector('#automation-queue');

async function runCandidateBossAction(row, action) {{ const jobId=selectedRecruitingJobId(); if(!jobId) {{ automationState.textContent='请先选择岗位。'; return; }} if((action==='phone' && !confirm('确认向该候选人请求电话吗？')) || (action==='wechat' && !confirm('确认向该候选人请求微信吗？'))) return; const payload=await post('/api/recruiting/automation/candidates/'+encodeURIComponent(row.candidate_key)+'/actions',{{job_id:jobId,action:action}}); automationState.textContent=payload.ok?'候选人操作已提交，等待 BOSS 页面回执。':((payload.error||{{}}).message||'候选人操作失败'); await refreshAutomation(); }}
function appendCandidateBossActions(item, row) {{ const actions=document.createElement('div'); actions.className='row-actions'; [['phone','换电话'],['wechat','换微信'],['interview','约面试']].forEach(([action,label])=>{{ const button=document.createElement('button'); button.type='button'; button.className='secondary'; button.textContent=label; button.addEventListener('click',()=>runCandidateBossAction(row,action)); actions.append(button); }}); item.append(actions); }}
function renderAutomationRows(host, rows, qualified) {{ host.replaceChildren(); if(!rows.length) {{ host.textContent=qualified?'暂无达到岗位分数线且已完成附件终审的候选人。':'暂无已同步候选人。'; return; }} rows.forEach(row=>{{ const item=document.createElement('div'); item.className='workspace-row'; const title=document.createElement('strong'); title.textContent=row.candidate_name || '未命名候选人'; const meta=document.createElement('div'); meta.className='hint'; meta.textContent=[row.source==='recommendation'?'推荐牛人':'沟通列表',row.stage,row.score===null||row.score===undefined?'未终审':('评分 '+row.score),row.recommendation||''].filter(Boolean).join(' · '); item.append(title,meta); if(row.phone || row.wechat || row.interview_status) {{ const contacts=document.createElement('div'); contacts.className='hint'; contacts.textContent=['手机号：'+(row.phone||'未获取'), '微信号：'+(row.wechat||'未获取'), '面试：'+(row.interview_status||'未执行')].join(' · '); item.append(contacts); }} if(row.last_action) {{ const action=document.createElement('div'); action.className='hint'; action.textContent=row.last_action; item.append(action); }} if(row.resume_path) {{ const path=document.createElement('div'); path.className='hint'; path.textContent=row.resume_path; const open=document.createElement('button'); open.type='button'; open.className='secondary'; open.textContent='打开本地附件'; open.addEventListener('click',async()=>{{ const response=await post('/api/recruiting/automation/candidates/'+encodeURIComponent(row.candidate_key)+'/resume/open',{{}}); if(!response.ok) automationState.textContent=(response.error||{{}}).message||'附件无法打开'; }}); item.append(path,open); }} appendCandidateBossActions(item,row); host.append(item); }}); }}
async function loadAutomationSettings() {{ const jobId=selectedRecruitingJobId(); if(!jobId) return; const response=await fetch('/api/recruiting/automation/settings?job_id='+encodeURIComponent(jobId)); const payload=await response.json(); if(payload.ok) {{ const data=payload.data||{{}}; ['greeting_message','mode','date','time','address','note','contact_name','contact_phone'].forEach(key=>{{ const input=document.querySelector('#automation-'+(key==='greeting_message'?'greeting-message':'interview-'+key.replace('_','-'))); if(input) input.value=data[key]||''; }}); }} const followup=await fetch('/api/recruiting/automation/followup-settings?job_id='+encodeURIComponent(jobId)); const followupPayload=await followup.json(); if(followupPayload.ok) {{ const data=followupPayload.data||{{}}; document.querySelector('#automation-followup-wechat').checked=data.wechat_enabled===true; document.querySelector('#automation-followup-phone').checked=data.phone_enabled===true; document.querySelector('#automation-followup-interview').checked=data.interview_enabled===true; }} }}
async function refreshAutomation() {{ const jobId=selectedRecruitingJobId(); if(!jobId) {{ automationState.textContent='请先创建或选择已发布岗位。'; automationQualified.replaceChildren(); automationQueue.replaceChildren(); return; }} try {{ const response=await fetch('/api/recruiting/automation/candidates?job_id='+encodeURIComponent(jobId)); const payload=await response.json(); if(payload.ok) {{ renderAutomationRows(automationQualified,payload.data.qualified||[],true); renderAutomationRows(automationQueue,payload.data.candidates||[],false); }} await loadAutomationSettings(); }} catch (_) {{ automationState.textContent='无法读取自动化候选人队列。'; }} }}
async function automationRequest(path, body) {{ const jobId=selectedRecruitingJobId(); if(!jobId) {{ automationState.textContent='请先选择岗位。'; return; }} try {{ const payload=await post(path,{{job_id:jobId,...(body||{{}})}}); const result=payload.data||{{}}; const automationResultState=result.state||'idle'; if(!payload.ok || automationResultState==='failed' || automationResultState==='blocked') {{ automationState.textContent=(payload.error||{{}}).message||((result.error||{{}}).message)||'自动化请求失败，请检查 BOSS 页面后重试。'; }} else if(path==='/api/recruiting/automation/sync' && automationResultState==='running') {{ automationState.textContent='同步请求已提交，后台正在读取 BOSS 沟通列表。'; }} else {{ automationState.textContent='请求已提交，状态会自动刷新。'; }} }} catch(error) {{ automationState.textContent=error.message||'自动化请求失败，请检查 BOSS 页面后重试。'; }} await refresh(); await refreshAutomation(); }}
function renderAutomationControl(data) {{ const state=(data||{{}}).state||'idle'; automationState.textContent=state==='running'?'自动化运行中，会持续检查沟通列表的新回复。':(state==='paused'?'自动化已暂停。':(state==='stopping'?'正在停止当前操作。':(state==='stopped'?'自动化已停止。':automationState.textContent))); automationConversationButton.disabled=state==='running'||state==='paused'||state==='stopping'; automationRecommendationButton.disabled=state==='paused'||state==='stopping'; automationPauseButton.disabled=state!=='running'; automationResumeButton.disabled=state!=='paused'; automationStopButton.disabled=!['running','paused','stopping'].includes(state); automationActivities.replaceChildren(); const activities=(data&&data.activities)||[]; activities.forEach(activity=>{{ const row=document.createElement('div'); row.className='workspace-row'; row.textContent=[activity.at,activity.action,activity.status].filter(Boolean).join(' · '); automationActivities.append(row); }}); if(!activities.length) automationActivities.textContent='暂无自动化活动。'; }}
automationSyncButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/sync'));
automationConversationButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/start',{{source:'conversation',limit:20}}));
automationRecommendationButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/start',{{source:'recommendation',limit:10}}));
automationPauseButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/pause'));
automationResumeButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/resume'));
automationStopButton.addEventListener('click',()=>automationRequest('/api/recruiting/automation/stop'));
document.querySelector('#automation-settings-form').addEventListener('submit',async event=>{{ event.preventDefault(); const jobId=selectedRecruitingJobId(); const state=document.querySelector('#automation-settings-state'); if(!jobId) {{ state.textContent='请先选择岗位。'; return; }} const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=jobId; body.phone_enabled=document.querySelector('#automation-followup-phone').checked; body.wechat_enabled=document.querySelector('#automation-followup-wechat').checked; body.interview_enabled=document.querySelector('#automation-followup-interview').checked; const followup=await post('/api/recruiting/automation/followup-settings',body); const payload=await post('/api/recruiting/automation/settings',body); state.textContent=followup.ok&&payload.ok?'设置已保存。':((followup.error||payload.error||{{}}).message||'设置保存失败'); if(followup.ok&&payload.ok) await refresh(); }});
async function exportAutomationPool(format) {{ const jobId=selectedRecruitingJobId(); if(!jobId) {{ automationState.textContent='请先选择岗位。'; return; }} const response=await fetch('/api/recruiting/automation/candidate-pool/export?job_id='+encodeURIComponent(jobId)+'&format='+format); if(!response.ok) {{ const payload=await response.json(); automationState.textContent=(payload.error||{{}}).message||'导出失败'; return; }} const blob=await response.blob(); const link=document.createElement('a'); link.href=URL.createObjectURL(blob); link.download=jobId+'-候选人池.'+format; link.click(); URL.revokeObjectURL(link.href); automationState.textContent='导出已生成。'; }}
document.querySelector('#automation-export-csv').addEventListener('click',()=>exportAutomationPool('csv')); document.querySelector('#automation-export-xlsx').addEventListener('click',()=>exportAutomationPool('xlsx'));
// 候选人队列是工作台的主入口：它只展示脱敏元数据和下一动作，点击后
// 再定位到已有的待办或候选人控件，避免把完整流程压在一张长页面里。
const recruitingCandidateQueue = document.createElement('section');
recruitingCandidateQueue.id='recruiting-candidate-queue';
recruitingCandidateQueue.className='candidate-queue';
recruitingCandidateQueue.dataset.workspaceViewContent='operations';
recruitingCandidateQueue.setAttribute('aria-labelledby','recruiting-candidate-queue-heading');
recruitingCandidateQueue.innerHTML='<div class="candidate-queue-header"><div><h3 id="recruiting-candidate-queue-heading">候选人处理队列</h3><p>优先显示有待办的候选人；终局候选人保留在队尾，便于回看。</p></div><div class="candidate-queue-tools"><label class="sr-only" for="recruiting-candidate-queue-filter">筛选候选人</label><input id="recruiting-candidate-queue-filter" type="search" placeholder="搜索姓名或下一动作" autocomplete="off"><label class="sr-only" for="recruiting-candidate-queue-stage">按阶段筛选</label><select id="recruiting-candidate-queue-stage"><option value="">全部阶段</option></select></div></div><div id="recruiting-candidate-queue-list" class="candidate-queue-list" aria-live="polite"></div>';
recruitingPipelineSummary.insertAdjacentElement('beforebegin',recruitingCandidateQueue);
const recruitingCandidateQueueFilter = recruitingCandidateQueue.querySelector('#recruiting-candidate-queue-filter');
const recruitingCandidateQueueStage = recruitingCandidateQueue.querySelector('#recruiting-candidate-queue-stage');
const recruitingCandidateQueueList = recruitingCandidateQueue.querySelector('#recruiting-candidate-queue-list');
let recruitingCandidateQueueRows = [];
// 评分分组直接消费后端的岗位维度纯投影。它不参与评分，也不自动推进候选人；
// 每组里的入口只帮助 HR 跳到已有候选人卡片完成证据核对和人工决策。
const recruitingScoreGroups=document.createElement('section');
recruitingScoreGroups.id='recruiting-score-groups'; recruitingScoreGroups.className='workspace-block'; recruitingScoreGroups.dataset.workspaceViewContent='review';
recruitingScoreGroups.innerHTML='<h3>候选人评分分组</h3><p class="hint">仅汇总当前岗位；每组候选人仍在上方主列表中处理。</p><div id="recruiting-score-groups-list" class="score-group-totals" aria-live="polite"><span class="score-group-total"><span>强烈推荐</span><strong>0</strong></span><span class="score-group-total"><span>推荐</span><strong>0</strong></span><span class="score-group-total"><span>待确认</span><strong>0</strong></span><span class="score-group-total"><span>人工复核</span><strong>0</strong></span><span class="score-group-total"><span>不推荐</span><strong>0</strong></span><span class="score-group-total"><span>未评估</span><strong>0</strong></span></div>';
const recruitingRejectionStatistics=document.createElement('section');
recruitingRejectionStatistics.id='recruiting-rejection-statistics'; recruitingRejectionStatistics.className='rejection-statistics'; recruitingRejectionStatistics.dataset.workspaceViewContent='review';
recruitingRejectionStatistics.innerHTML='<div><strong>不合格原因统计</strong><p class="hint">只汇总当前岗位已生成的评估结论，不自动淘汰或修改候选人状态。</p></div><div id="recruiting-rejection-statistics-list" aria-live="polite"></div>';
recruitingAssessmentLayout.insertAdjacentElement('afterend',recruitingRejectionStatistics);
recruitingRejectionStatistics.insertAdjacentElement('afterend',recruitingScoreGroups);
const recruitingScoreGroupsList=recruitingScoreGroups.querySelector('#recruiting-score-groups-list');
const recruitingRejectionStatisticsList=recruitingRejectionStatistics.querySelector('#recruiting-rejection-statistics-list');
function renderRecruitingScoreGroups(groups) {{
  recruitingScoreGroupsList.replaceChildren();
  (groups || []).forEach(group => {{
    const total=document.createElement('span'); total.className='score-group-total'; const label=document.createElement('span'); label.textContent=group.label || '未命名分组'; const count=document.createElement('strong'); count.textContent=String(group.count || 0); total.append(label,count); recruitingScoreGroupsList.append(total);
  }});
}}
function renderRejectionReasonStatistics(statistics) {{
  recruitingRejectionStatisticsList.replaceChildren();
  const data=statistics || {{}}; const reasons=Array.isArray(data.reasons) ? data.reasons : [];
  if(!reasons.length) {{ recruitingRejectionStatisticsList.textContent='当前岗位尚无已评为不推荐的候选人。'; return; }}
  reasons.forEach(reason => {{ const row=document.createElement('div'); row.className='rejection-reason-row'; const label=document.createElement('strong'); label.textContent=reason.label || reason.code || '未命名原因'; const bar=document.createElement('div'); bar.className='rejection-reason-bar'; const fill=document.createElement('span'); fill.style.width=Math.max(0,Math.min(100,Number(reason.rate || 0)))+'%'; bar.append(fill); const meta=document.createElement('span'); meta.textContent=`${{reason.count || 0}} 人 · ${{reason.rate || 0}}%`; row.append(label,bar,meta); recruitingRejectionStatisticsList.append(row); }});
}}
const recruitingWorkflowProgress = document.createElement('div');
recruitingWorkflowProgress.id='recruiting-workflow-progress';
recruitingWorkflowProgress.className='workflow-progress';
recruitingWorkflowProgress.dataset.workspaceViewContent='operations';
recruitingWorkflowProgress.setAttribute('aria-live','polite');
recruitingWorkflowProgress.innerHTML='<div class="workflow-progress-header"><div><strong>闭环进度</strong><span id="recruiting-workflow-next">正在计算下一步…</span></div><button id="recruiting-workflow-action" class="secondary" type="button">去处理</button></div><div id="recruiting-workflow-steps" class="workflow-steps"></div>';
recruitingPipelineSummary.insertAdjacentElement('beforebegin',recruitingWorkflowProgress);
const recruitingWorkflowNext = recruitingWorkflowProgress.querySelector('#recruiting-workflow-next');
const recruitingWorkflowSteps = recruitingWorkflowProgress.querySelector('#recruiting-workflow-steps');
const recruitingWorkflowAction = recruitingWorkflowProgress.querySelector('#recruiting-workflow-action');
const recruitingTaskBlock = document.createElement('div');
recruitingTaskBlock.className='workspace-block';
recruitingTaskBlock.dataset.workspaceViewContent='operations';
  recruitingTaskBlock.innerHTML='<h3>待办中心</h3><p class="hint">所有下一步都需要你在官方页面完成后，再手动记录；系统不会代替你发消息、加私域或邀约，也不会自动加私域。</p><div id="recruiting-task-summary" class="task-summary" aria-live="polite"></div><div id="recruiting-task-list" class="task-list" aria-live="polite"></div>';
  recruitingPipelineSummary.insertAdjacentElement('afterend',recruitingTaskBlock);
  const recruitingTaskSummary = recruitingTaskBlock.querySelector('#recruiting-task-summary');
  const recruitingTaskList = recruitingTaskBlock.querySelector('#recruiting-task-list');
  const recruitingActivityBlock = document.createElement('div');
  recruitingActivityBlock.className='workspace-block';
  recruitingActivityBlock.dataset.workspaceViewContent='review';
  recruitingActivityBlock.innerHTML='<h3>已记录的私域与面试</h3><p class="hint">这里展示本地事实记录，联系方式和简历正文不会进入页面快照。</p><div id="recruiting-activity-list" class="workspace-list" aria-live="polite"></div>';
  recruitingTaskBlock.insertAdjacentElement('afterend',recruitingActivityBlock);
  const recruitingActivityList = recruitingActivityBlock.querySelector('#recruiting-activity-list');
  const recruitingInsightsBlock = document.createElement('div');
  recruitingInsightsBlock.className='workspace-block';
  recruitingInsightsBlock.dataset.workspaceViewContent='review';
  recruitingInsightsBlock.innerHTML='<h3>复盘建议</h3><p class="hint">建议来自已记录的本地事实，只供 HR 复核；不会自动修改岗位标准、话术或平台配置。</p><div id="recruiting-insights" class="workspace-list" aria-live="polite"></div>';
  recruitingActivityBlock.insertAdjacentElement('afterend',recruitingInsightsBlock);
const recruitingInsightsList = recruitingInsightsBlock.querySelector('#recruiting-insights');
// 原页面按“队列 / 设置 / 复盘”拆分，用户需要在三个术语之间来回判断。
// 业务上只有两类高频工作：配置岗位，或处理候选人。因此仅重组展示层，
// 保留所有原始表单、接口和数据字段，避免界面改版影响既有招聘记录。
const recruitingWorkspaceSwitcher=document.querySelector('#recruiting-view-switcher');
recruitingWorkspaceSwitcher.innerHTML='<button type="button" role="tab" aria-selected="false" data-workspace-view="jobs">岗位管理</button><button type="button" role="tab" aria-selected="true" data-workspace-view="candidates">候选人</button><button type="button" role="tab" aria-selected="false" data-workspace-view="settings">设置</button>';
document.querySelectorAll('[data-workspace-view-content="setup"]').forEach(node => {{
  node.dataset.workspaceViewContent='jobs';
  node.classList.add('job-management-block');
}});
document.querySelectorAll('[data-workspace-view-content="operations"],[data-workspace-view-content="review"]').forEach(node => {{
  node.dataset.workspaceViewContent='candidates';
  node.classList.add('candidate-management-block');
}});
// 将工作台从通用控制台中拆出独立的视觉壳层。旧导航仅隐藏，不删除，
// 以便既有深链和非招聘功能仍可由原有锚点逻辑继续解析。
const recruitingWorkbenchShell=document.querySelector('.shell');
document.body.classList.add('recruiting-ui-shell');
recruitingWorkbenchShell.id='recruiting-workbench-shell';
const legacyConsoleNavigation=document.querySelector('.side-nav');
if(legacyConsoleNavigation) legacyConsoleNavigation.classList.add('legacy-console-hidden');
const recruitingWorkbenchHeading=document.createElement('header');
recruitingWorkbenchHeading.className='recruiting-workbench-heading';
recruitingWorkbenchHeading.innerHTML='<h1>岗位卡片 + 分步配置 + 分析入口</h1><p>先选择岗位，再按步骤补齐标准信息；只有配置完成的岗位才可作为正式分析标准。</p>';
recruitingWorkbenchShell.insertAdjacentElement('beforebegin',recruitingWorkbenchHeading);
const recruitingWorkbenchNav=document.createElement('nav');
recruitingWorkbenchNav.className='recruiting-workbench-nav';
recruitingWorkbenchNav.setAttribute('aria-label','招聘工作台');
recruitingWorkbenchNav.innerHTML='<h2>招聘工作台</h2><button type="button" data-recruiting-nav="jobs">岗位管理</button><button type="button" data-recruiting-nav="candidates">候选人（含 AI 评分）</button><button type="button" data-recruiting-nav="resumes">获取简历</button><button type="button" data-recruiting-nav="records">沟通记录</button><button type="button" data-recruiting-nav="login">登录状态</button>';
if(legacyConsoleNavigation) legacyConsoleNavigation.insertAdjacentElement('beforebegin',recruitingWorkbenchNav);
else document.querySelector('.app-layout').prepend(recruitingWorkbenchNav);
const recruitingWorkbenchNavButtons=Array.from(recruitingWorkbenchNav.querySelectorAll('[data-recruiting-nav]'));
// 简历来源属于同一条“获取资料后分析”的工作流。把它们收敛成二级切换，
// 保留原来的下载和批量能力，但不再让五个来源占据招聘首页的主导航。
const recruitingResumeSourceSwitcher=document.createElement('nav');
recruitingResumeSourceSwitcher.id='recruiting-resume-source-switcher';
recruitingResumeSourceSwitcher.className='recruiting-resume-source-switcher';
recruitingResumeSourceSwitcher.hidden=true;
recruitingResumeSourceSwitcher.setAttribute('aria-label','简历来源');
recruitingResumeSourceSwitcher.innerHTML='<button type="button" data-recruiting-source="conversation-panel">沟通候选人</button><button type="button" data-recruiting-source="recommendation-panel">推荐牛人</button><button type="button" data-recruiting-source="batch-export-panel">一键导出</button><button type="button" data-recruiting-source="pipeline-panel">自动流水线</button><button type="button" data-recruiting-source="resume-download-panel">简历导出</button>';
document.querySelector('.workarea').prepend(recruitingResumeSourceSwitcher);
const recruitingResumeSourceButtons=Array.from(recruitingResumeSourceSwitcher.querySelectorAll('[data-recruiting-source]'));
function setRecruitingWorkbenchNavigation(entry) {{
  recruitingWorkbenchNavButtons.forEach(button => {{
    const selected=button.dataset.recruitingNav===entry;
    if(selected) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  }});
  recruitingResumeSourceSwitcher.hidden=entry!=='resumes';
}}
function openRecruitingResumeSource(panelId) {{
  const validPanels=new Set(['conversation-panel','recommendation-panel','batch-export-panel','pipeline-panel','resume-download-panel']);
  const target=validPanels.has(panelId) ? panelId : 'conversation-panel';
  activateNav(target); setRecruitingWorkbenchNavigation('resumes');
  recruitingResumeSourceButtons.forEach(button => {{
    if(button.dataset.recruitingSource===target) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  }});
}}
recruitingResumeSourceButtons.forEach(button => button.addEventListener('click',() => openRecruitingResumeSource(button.dataset.recruitingSource || 'conversation-panel')));
recruitingWorkbenchNavButtons.forEach(button => button.addEventListener('click',() => {{
  const entry=button.dataset.recruitingNav || 'jobs';
  if(entry==='resumes') {{ openRecruitingResumeSource('conversation-panel'); return; }}
  if(entry==='login') {{ location.hash='#login-panel'; activateNav('login-panel'); setRecruitingWorkbenchNavigation('login'); return; }}
  location.hash='#recruiting-workspace'; activateNav('recruiting-workspace');
  if(entry==='jobs') {{ selectRecruitingWorkspaceView('jobs'); setRecruitingWorkbenchNavigation('jobs'); return; }}
  selectRecruitingWorkspaceView('candidates'); setRecruitingWorkbenchNavigation(entry);
  const targets={{records:'#recruiting-activity-list'}};
  const target=targets[entry] ? document.querySelector(targets[entry]) : null;
  if(target) window.setTimeout(() => {{ try {{ target.scrollIntoView({{behavior:'smooth',block:'start'}}); }} catch (_) {{}} }},0);
}}));
// 岗位管理首页只承担“看全局、选下一步”的职责。详情表单仍复用旧节点，
// 进入编辑态才显示，既避免重写既有接口，又防止信息在首次进入时堆叠。
const recruitingJobManagementDashboard=document.createElement('section');
recruitingJobManagementDashboard.id='recruiting-job-management-dashboard';
recruitingJobManagementDashboard.className='job-management-dashboard';
recruitingJobManagementDashboard.dataset.workspaceViewContent='jobs';
recruitingJobManagementDashboard.innerHTML='<div class="job-management-dashboard-header"><div><h3>岗位管理</h3><p>先选择岗位，再继续配置或查看对应候选人。</p></div><div class="job-management-dashboard-actions"></div></div><div id="recruiting-job-card-list" class="job-card-list" aria-live="polite"></div>';
recruitingWorkspaceSwitcher.insertAdjacentElement('afterend',recruitingJobManagementDashboard);
const recruitingJobCardList=recruitingJobManagementDashboard.querySelector('#recruiting-job-card-list');
const recruitingJobManagementDashboardActions=recruitingJobManagementDashboard.querySelector('.job-management-dashboard-actions');
const recruitingJobEditorBlocks=Array.from(document.querySelectorAll('.job-management-block'));
const recruitingJobEditorToolbar=document.createElement('div');
recruitingJobEditorToolbar.className='job-management-editor-toolbar';
const recruitingJobEditorBackButton=document.createElement('button');
recruitingJobEditorBackButton.type='button'; recruitingJobEditorBackButton.className='secondary'; recruitingJobEditorBackButton.textContent='返回岗位列表';
const recruitingJobEditorHint=document.createElement('span'); recruitingJobEditorHint.textContent='岗位配置保存在本机；完成后返回列表即可继续处理候选人。';
recruitingJobEditorToolbar.append(recruitingJobEditorBackButton,recruitingJobEditorHint);
recruitingJobManagementDashboard.insertAdjacentElement('afterend',recruitingJobEditorToolbar);
// “新建”和“同步”属于岗位总览动作，移动既有按钮而非复制一套事件，
// 保证原来的 RPA 同步边界和表单提交逻辑完全不变。
recruitingNewJobButton.textContent='+ 新建岗位';
recruitingJobManagementDashboardActions.append(recruitingNewJobButton,recruitingSyncBossJobsButton,recruitingSyncBossJobsState);
let recruitingJobManagementMode='overview';
const recruitingJobCandidateCounts=new Map();
function setRecruitingJobManagementMode(mode) {{
  recruitingJobManagementMode=mode==='editor' ? 'editor' : 'overview';
  const editing=recruitingJobManagementMode==='editor';
  recruitingJobManagementDashboard.classList.toggle('job-management-dashboard-hidden',editing);
  recruitingJobEditorToolbar.classList.toggle('job-management-editor-hidden',!editing);
  recruitingJobEditorBlocks.forEach(block => block.classList.toggle('job-management-editor-hidden',!editing));
}}
function openJobManagementEditor(jobId='') {{
  selectRecruitingWorkspaceView('jobs');
  const job=(recruitingWorkspace && (recruitingWorkspace.jobs || []).find(item => item.job_id===jobId)) || null;
  recruitingCreatingNewJob=!job;
  recruitingEditingJobId=job ? String(job.job_id) : '';
  if(job) {{
    if(recruitingJobSelect) recruitingJobSelect.value=String(job.job_id);
    fillRecruitingJobForm(job,true);
    recruitingJobStatus.textContent='正在编辑：'+(job.name || '未命名岗位');
  }} else if(recruitingJobForm) {{
    recruitingJobForm.reset(); recruitingJobForm.dataset.loadedJob=''; recruitingJobForm.dataset.dirty='0';
    if(recruitingProfessionalQaToggle) recruitingProfessionalQaToggle.checked=true;
    recruitingJobStatus.textContent='请填写新岗位标准，保存后再发布。';
  }}
  setRecruitingJobManagementMode('editor');
  window.setTimeout(() => {{ try {{ recruitingJobForm.querySelector('[name="name"]').focus(); }} catch (_) {{}} }},0);
}}
function renderRecruitingJobCards(data) {{
  recruitingJobCardList.replaceChildren();
  const jobs=(data && data.jobs) || [];
  if(!jobs.length) {{
    const empty=document.createElement('div'); empty.className='workspace-row'; empty.textContent='还没有岗位。新建岗位后，可在这里查看配置进度和对应候选人。'; recruitingJobCardList.append(empty); return;
  }}
  jobs.forEach(job => {{
    const readiness=job.readiness || {{}};
    const missing=Array.isArray(readiness.missing_required_fields) ? readiness.missing_required_fields : [];
    const ready=readiness.ready===true;
    const card=document.createElement('article'); card.className='job-card '+(ready ? 'is-ready' : 'is-incomplete');
    const header=document.createElement('div'); header.className='job-card-header';
    const title=document.createElement('h4'); title.className='job-card-title'; title.textContent=job.name || '未命名岗位';
    const status=document.createElement('span'); status.className='job-card-status'; status.textContent=ready ? '已可分析' : `待补 ${{missing.length || 1}} 项`;
    header.append(title,status);
    const progress=document.createElement('div'); progress.className='job-card-progress'; const progressFill=document.createElement('span');
    const progressValue=ready ? 100 : Math.max(0,Math.min(100,Math.round((5-Math.min(5,missing.length))/5*100))); progressFill.style.width=String(progressValue)+'%'; progress.append(progressFill);
    const facts=document.createElement('div'); facts.className='job-card-meta';
    const basic=[job.city,job.salary_range,job.min_experience_years===null || job.min_experience_years===undefined || job.min_experience_years==='' ? '' : String(job.min_experience_years)+' 年+',job.education_requirement].filter(Boolean);
    const count=recruitingJobCandidateCounts.get(String(job.job_id));
    facts.textContent=[basic.join(' · ') || '待补充岗位基本信息',count===undefined ? '' : String(count)+' 位候选人'].filter(Boolean).join(' · ');
    const actions=document.createElement('div'); actions.className='job-card-actions'; const action=document.createElement('button'); action.type='button';
    if(ready) {{
      action.textContent='查看并分析简历';
      action.addEventListener('click',async () => {{
        if(recruitingJobSelect) recruitingJobSelect.value=String(job.job_id);
        if(recruitingAssessJob) recruitingAssessJob.value=String(job.job_id);
        selectRecruitingWorkspaceView('candidates'); await refreshRecruiting();
      }});
    }} else {{ action.textContent='继续配置'; action.addEventListener('click',() => openJobManagementEditor(String(job.job_id))); }}
    actions.append(action); card.append(header,progress,facts,actions); recruitingJobCardList.append(card);
  }});
}}
recruitingJobEditorBackButton.addEventListener('click',() => setRecruitingJobManagementMode('overview'));
const recruitingWorkspaceIntro=document.querySelector('.workspace-intro');
// 首页卡片已明确给出下一步，旧版漏斗提示和大段模块说明只会挤占首屏，
// 因此不在岗位总览保留；具体说明仍在用户进入配置或候选人操作后呈现。
if(recruitingWorkspaceIntro) recruitingWorkspaceIntro.remove();
// 评分已融入候选人模块，移除旧侧栏入口，避免用户误以为还有第二套候选人列表。
const scoreBoardNavigationLink=document.querySelector('a[href="/preview/score-board"]');
if(scoreBoardNavigationLink) {{
  scoreBoardNavigationLink.remove();
}}
let recruitingWorkspaceView='jobs';
const recruitingWorkspaceViewButtons = Array.from(document.querySelectorAll('[data-workspace-view]'));
function selectRecruitingWorkspaceView(view, focus=false) {{
  const allowedViews=new Set(['jobs','candidates']);
  recruitingWorkspaceView=allowedViews.has(view) ? view : 'candidates';
  recruitingWorkspaceViewButtons.forEach(button => {{
    const selected=button.dataset.workspaceView===recruitingWorkspaceView;
    button.setAttribute('aria-selected',selected ? 'true' : 'false');
    button.tabIndex=selected ? 0 : -1;
  }});
  document.querySelectorAll('[data-workspace-view-content]').forEach(node => {{
    node.classList.toggle('workspace-view-hidden',node.dataset.workspaceViewContent!==recruitingWorkspaceView);
  }});
  // 从导出或待办跳回工作台时，同步精简侧栏的激活状态，避免页面内容与导航高亮不一致。
  setRecruitingWorkbenchNavigation(recruitingWorkspaceView==='jobs' ? 'jobs' : 'candidates');
  if(focus) {{
    const selectedButton=recruitingWorkspaceViewButtons.find(button => button.dataset.workspaceView===recruitingWorkspaceView);
    if(selectedButton) selectedButton.focus();
  }}
}}
recruitingWorkspaceViewButtons.forEach(button => button.addEventListener('click',() => selectRecruitingWorkspaceView(button.dataset.workspaceView)));
selectRecruitingWorkspaceView(recruitingWorkspaceView);
setRecruitingJobManagementMode(recruitingJobManagementMode);
setRecruitingWorkbenchNavigation('jobs');
const candidateStages = [['pending_screening','待筛选'],['initial_pass','初筛通过'],['greeted','已打招呼'],['basic_confirming','基础条件确认中'],['basic_passed','基础条件通过'],['professional_qa','专业问答中'],['professional_passed','专业问答通过'],['resume_exchanged','已交换简历'],['resume_passed','简历评估通过'],['private_domain_pending','待加私域'],['private_domain_added','已加私域'],['interview_pending','待邀约面试'],['interview_scheduled','已约面'],['interview_completed','面试完成'],['hired','录用'],['rejected','淘汰'],['paused','暂缓']];
const terminalStages = new Set(['hired','rejected','paused']);
function focusRecruitingQueueCandidate(candidate) {{
  if(!candidate || !candidate.candidate_id) return;
  selectRecruitingWorkspaceView('candidates');
  recruitingSelectedCandidateId=String(candidate.candidate_id);
  const focusTaskId=candidate.focus_task_id || candidate.pending_task_id;
  const taskNode=focusTaskId ? Array.from(document.querySelectorAll('[data-task-id]')).find(node => node.getAttribute('data-task-id')===String(focusTaskId)) : null;
  const candidateNode=Array.from(document.querySelectorAll('[data-candidate-id]')).find(node => node.getAttribute('data-candidate-id')===String(candidate.candidate_id));
  const target=taskNode || candidateNode;
  if(target) {{
    const detailsList=candidateNode ? Array.from(candidateNode.querySelectorAll('details')) : [];
    const details=candidateNode && detailsList.length ? (candidate.is_terminal ? detailsList[detailsList.length-1] : detailsList[0]) : null;
    if(details) details.open=true;
    try {{ target.scrollIntoView({{behavior:'smooth',block:'center'}}); }} catch (_) {{}}
    if(taskNode) {{
      const action=taskNode.querySelector('button:not(:disabled),select,input,textarea');
      if(action) action.focus();
    }} else {{
      const action=candidateNode && candidateNode.querySelector('button:not(:disabled),select:not(:disabled),input:not(:disabled),textarea:not(:disabled)');
      if(action) action.focus();
    }}
    return;
  }}
  goToRecruitingWorkspace(String(candidate.candidate_id));
}}
function renderRecruitingCandidateQueue(workflow) {{
  const rows=(workflow && workflow.queue) || [];
  recruitingCandidateQueueRows=rows;
  const selectedStage=recruitingCandidateQueueStage.value || '';
  if(!Array.from(recruitingCandidateQueueStage.options).some(option => option.value===selectedStage && option.value)) {{
    recruitingCandidateQueueStage.replaceChildren();
    const all=document.createElement('option'); all.value=''; all.textContent='全部阶段'; recruitingCandidateQueueStage.append(all);
    candidateStages.forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; recruitingCandidateQueueStage.append(option); }});
    recruitingCandidateQueueStage.value=selectedStage;
  }}
  const query=(recruitingCandidateQueueFilter.value || '').trim().toLowerCase();
  const filtered=rows.filter(row => {{
    const text=[row.name,row.next_action,row.stage_label,row.source].filter(Boolean).join(' ').toLowerCase();
    return (!query || text.includes(query)) && (!selectedStage || row.stage===selectedStage);
  }});
  recruitingCandidateQueueList.replaceChildren();
  if(!rows.length) {{ recruitingCandidateQueueList.textContent='导入候选人后，这里会按下一动作生成处理队列。'; return; }}
  if(!filtered.length) {{ recruitingCandidateQueueList.textContent='没有符合当前筛选条件的候选人。'; return; }}
  filtered.forEach(rowData => {{
    const row=document.createElement('div'); row.className='candidate-queue-row'+((rowData.focus_task_id || rowData.pending_task_id)===((workflow && (workflow.pending_task_id || workflow.focus_task_id)) || '')?' is-next':'')+(rowData.is_terminal?' is-terminal':'');
    row.setAttribute('data-queue-candidate-id',rowData.candidate_id || '');
    const content=document.createElement('div');
    const heading=document.createElement('strong'); heading.textContent=rowData.name || '未命名候选人';
    const priority=document.createElement('span'); priority.className='queue-priority'; priority.textContent='优先级：'+(rowData.priority_label || '常规处理'); heading.append(priority);
    const meta=document.createElement('div'); meta.className='workspace-meta'; meta.textContent=[rowData.stage_label,rowData.source==='boss_conversation'?'BOSS 沟通':(rowData.source==='boss_recommendation'?'推荐牛人':'本地导入'),rowData.due_at ? '计划跟进：'+rowData.due_at : ''].filter(Boolean).join(' · ');
    const signal=document.createElement('div'); signal.className='candidate-queue-signal';
    const scoreText=rowData.assessment_score===null || rowData.assessment_score===undefined ? '未生成评分' : '综合评分 '+String(rowData.assessment_score)+' 分';
    const riskLabels={{high:'高',medium:'中',low:'低',unknown:'未评估'}}; signal.textContent=scoreText+' · 风险 '+(riskLabels[rowData.risk_level] || '未评估');
    const reason=document.createElement('div'); reason.className='candidate-queue-reasons'; reason.textContent='排序理由：'+((Array.isArray(rowData.priority_reasons) && rowData.priority_reasons.length) ? rowData.priority_reasons.join('；') : '按待办顺序处理');
    const action=document.createElement('div'); action.className='candidate-queue-action'; action.textContent='下一步：'+(rowData.next_action || '记录阶段'); content.append(heading,meta,signal,reason,action);
    const button=document.createElement('button'); button.type='button'; button.className='secondary'; button.textContent=rowData.is_terminal?'查看记录':'去处理'; button.addEventListener('click',() => focusRecruitingQueueCandidate(rowData)); row.append(content,button); recruitingCandidateQueueList.append(row);
  }});
}}
recruitingCandidateQueueFilter.addEventListener('input',() => renderRecruitingCandidateQueue({{queue:recruitingCandidateQueueRows,pending_task_id:(recruitingWorkspace && recruitingWorkspace.workflow && recruitingWorkspace.workflow.pending_task_id) || ''}}));
recruitingCandidateQueueStage.addEventListener('change',() => renderRecruitingCandidateQueue({{queue:recruitingCandidateQueueRows,pending_task_id:(recruitingWorkspace && recruitingWorkspace.workflow && recruitingWorkspace.workflow.pending_task_id) || ''}}));
function updateRecruitingAssessmentAvailability() {{
  const jobId=recruitingAssessJob.value || selectedRecruitingJobId();
  const job=(recruitingWorkspace && (recruitingWorkspace.jobs || []).find(item => item.job_id===jobId)) || null;
  const candidate=(recruitingWorkspace && (recruitingWorkspace.candidates || []).find(item => item.candidate_id===recruitingAssessCandidate.value)) || null;
  const terminal=Boolean(candidate && terminalStages.has(candidate.stage));
  if(recruitingAssessButton) {{
    recruitingAssessButton.disabled=!(job && job.status==='published' && candidate && !terminal);
    recruitingAssessButton.title=terminal?'终局候选人不可重新评估':'';
  }}
}}
const pipelineSummaryStages = [['pending_screening','待筛选'],['basic_passed','基础条件通过'],['professional_qa','专业问答中'],['resume_passed','简历评估通过'],['private_domain_pending','待加私域'],['interview_pending','待邀约面试']];
const navLinks = document.querySelectorAll('[data-nav-link]');
// 侧栏是真正的页面路由：每个锚点固定属于一个页面，页面之间互斥显示。
// 保留锚点写法而不是自造 URL，是为了让深链、浏览器前进后退和既有的
// location.hash 跳转逻辑继续生效，同时避免所有区域堆在一张长页面里。
const routeSectionPages = {{'login-panel':'overview','mode-panel':'overview','dashboard-panel':'overview','loop-panel':'overview','pacing-panel':'overview','conversation-panel':'conversations','recommendation-panel':'recommendations','batch-export-panel':'batch','pipeline-panel':'pipeline','resume-download-panel':'export','recruiting-workspace':'workspace','guide-panel':'guide'}};
const routePages = Array.from(document.querySelectorAll('[data-route-page]'));
function routeForSection(sectionId) {{ return routeSectionPages[sectionId] || 'overview'; }}
function showRoutePage(pageKey) {{
  const target=routePages.some(page => page.dataset.routePage===pageKey) ? pageKey : 'overview';
  routePages.forEach(page => page.classList.toggle('route-hidden',page.dataset.routePage!==target));
  return target;
}}
function activateNav(sectionId) {{ showRoutePage(routeForSection(sectionId)); navLinks.forEach(link => {{ const active=link.getAttribute('href')==='#'+sectionId; link.classList.toggle('active',active); if(active) link.setAttribute('aria-current','location'); else link.removeAttribute('aria-current'); }}); }}
// 同一个锚点重复点击不会触发 hashchange，因此点击时先自己切一次页面。
navLinks.forEach(link => link.addEventListener('click',() => {{ const sectionId=(link.getAttribute('href') || '').slice(1); if(sectionId) activateNav(sectionId); }}));
function scrollToHashTarget(sectionId) {{
  if(!sectionId) return;
  const target=document.getElementById(sectionId);
  if(!target) return;
  // 目标所在页面必须先可见，隐藏元素上的 scrollIntoView 不会有任何效果。
  showRoutePage(routeForSection(sectionId));
  window.setTimeout(() => {{ try {{ target.scrollIntoView({{behavior:'auto',block:'start'}}); activateNav(sectionId); }} catch (_) {{}} }},0);
}}
// 沟通和推荐列表是异步填充的，它们的高度变化会把首次锚点滚动推离视口；
// 只在首轮刷新完成后补定位一次，后续轮询不再抢占用户正在阅读的位置。
let pendingInitialHashTarget=location.hash.slice(1);
function settleInitialHashTarget() {{
  const sectionId=pendingInitialHashTarget;
  if(!sectionId) return;
  pendingInitialHashTarget='';
  window.setTimeout(() => scrollToHashTarget(sectionId),40);
}}
if ('IntersectionObserver' in window) {{ const navObserver=new IntersectionObserver(entries => entries.forEach(entry => {{ if(entry.isIntersecting) activateNav(entry.target.id); }}),{{rootMargin:'-18% 0px -65% 0px',threshold:0}}); document.querySelectorAll('#login-panel,#dashboard-panel,#loop-panel,#pacing-panel,#conversation-panel,#recommendation-panel,#resume-download-panel,#recruiting-workspace,#guide-panel').forEach(section => navObserver.observe(section)); }}
// 直接打开带锚点的工作台页面时，页面可能尚未触发 IntersectionObserver；
// 先按 URL 设置一次，再监听后续导航，保证侧栏状态与实际区域一致。
activateNav(location.hash.slice(1) || 'recruiting-workspace');
scrollToHashTarget(location.hash.slice(1));
window.addEventListener('hashchange', () => {{ const sectionId=location.hash.slice(1); activateNav(sectionId || 'login-panel'); scrollToHashTarget(sectionId); }});
let current = null;
let lastLoginState = null;
let recruitingWorkspace = null;
// 轮询快照会重建评估下拉框，单独保存候选人标识才能避免用户正在操作的
// 候选人被刷新成列表第一人，尤其是多岗位、多候选人同时处理时。
let recruitingSelectedCandidateId = '';
let pendingImportedResumePath = null;
// 导出结果和工作区轮询是两个异步生命周期；单独保存候选人标识，避免
// 下拉框重建时把刚导入的人覆盖成列表第一人，确保导出后的接力仍指向同一对象。
let pendingImportedCandidateId = '';
let lastWorkspaceHandoffKey = '';
let pendingRequestError = null;
function stateText(state) {{ return {{idle:'未登录',running:'进行中',succeeded:'已完成',failed:'失败',blocked:'已阻断'}}[state] || '未知'; }}
function renderPacing(pacing) {{
  const currentPacing=pacing || {{}};
  const reasonLabels={{'':'当前时段允许执行',startup_jitter:'今日启动窗口尚未到达',daily_quota:'已达到当前时段额度',cooldown:'动作冷却中，请在提示时间后再试',off_hours:'非工作时段暂不执行'}};
  const configured=currentPacing.configured !== false;
  const unavailable=currentPacing.reason === 'unavailable';
  const allowed=currentPacing.allowed !== false;
  const used=Number.isFinite(Number(currentPacing.count)) ? Number(currentPacing.count) : 0;
  const quota=Number.isFinite(Number(currentPacing.effective_quota)) ? Number(currentPacing.effective_quota) : 0;
  const remaining=Number.isFinite(Number(currentPacing.remaining)) ? Number(currentPacing.remaining) : Math.max(0,quota-used);
  if(pacingUsed) pacingUsed.textContent=configured ? String(used) : '未启用';
  if(pacingQuota) pacingQuota.textContent=configured ? String(quota) : '未启用';
  if(pacingRemaining) pacingRemaining.textContent=configured ? String(remaining) : '未启用';
  if(pacingWindow) pacingWindow.textContent=currentPacing.window_label || '未知';
  if(pacingSummary) {{
    pacingSummary.textContent=unavailable?'状态不可用':(!configured?'仅人工操作':(allowed?'可执行':'已暂停'));
    pacingSummary.className='pacing-state'+(unavailable?' unavailable':(!allowed?' paused':''));
  }}
  if(pacingDetail) {{
    let detail=!configured ? '当前工作台没有接入自动化动作，所有候选人动作仍由 HR 在官方页面完成。' : (unavailable ? (currentPacing.reason_label || '安全节奏状态暂不可用') : (currentPacing.reason_label || reasonLabels[currentPacing.reason] || '当前时段允许执行'));
    if(currentPacing.pause_until && !allowed) detail+=` · 恢复时间：${{currentPacing.pause_until}}`;
    if(currentPacing.last_action_at) detail+=` · 最近动作：${{currentPacing.last_action_at}}`;
    pacingDetail.textContent=detail;
    pacingDetail.className='notice pacing-detail'+(unavailable?' error':(!allowed?' warn':''));
  }}
}}
function renderLoopSummary(data) {{
  const snapshot=data || {{}};
  const workflow=snapshot.workflow || {{}};
  const pipeline=snapshot.pipeline || {{}};
  const queueSummary=workflow.queue_summary || {{}};
  const jobs=snapshot.jobs || [];
  const selectedJobId=snapshot.selected_job_id || '';
  const selectedJob=jobs.find(job => job.job_id===selectedJobId) || jobs[0] || {{}};
  const closed=workflow.next_step==='closed';
  const pending=Number(queueSummary.actionable || 0);
  const terminal=Number(queueSummary.terminal || 0);
  const active=Number(pipeline.active || 0);
  const next=(workflow.pending_candidate_name && workflow.pending_task_title) ? `${{workflow.pending_candidate_name}} · ${{workflow.pending_task_title}}` : (workflow.next_action || '当前没有待处理动作');
  if(loopNext) loopNext.textContent=closed ? '当前候选人已进入终局，可导入下一位' : next;
  if(loopPending) loopPending.textContent=String(pending);
  if(loopActive) loopActive.textContent=String(active);
  if(loopTerminal) loopTerminal.textContent=String(terminal);
  if(loopJob) loopJob.textContent=selectedJob.name || (jobs.length ? '请选择岗位' : '尚未创建');
  if(loopPanel) loopPanel.className='panel loop-panel'+(closed?' is-closed':(pending?' is-attention':''));
  if(loopAction) {{
    loopAction.textContent=closed?'导入下一位':'去处理';
    loopAction.disabled=!workflow.next_step;
    loopAction.onclick=() => {{
      location.hash='#recruiting-workspace';
      activateNav('recruiting-workspace');
      window.setTimeout(() => {{ if(recruitingWorkflowAction) recruitingWorkflowAction.click(); }},0);
    }};
  }}
}}
function workspaceStateText(state, workflow, pipeline) {{
  // 操作请求状态和工作台快照状态是两条不同的生命周期：请求完成不代表
  // 候选人已经闭环，因此优先投影工作流和漏斗快照，避免页面误报“未开始”。
  const currentWorkflow=workflow || {{}}; const currentPipeline=pipeline || {{}};
  if(state==='running') return '处理中';
  if(state==='failed') return '失败';
  if(currentWorkflow.next_step==='closed') return '已闭环';
  if(currentWorkflow.next_step || Number(currentPipeline.total || 0)>0 || Number(currentPipeline.active || 0)>0) return '已加载';
  return {{idle:'未开始',succeeded:'已完成'}}[state] || '未知';
}}
async function post(path, body) {{ try {{ const response = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json','X-Boss-Web-Token':token}}, body:JSON.stringify(body || {{}})}}); if (response.status === 403) return {{ok:false,error:{{message:'本地控制台已重启，请刷新页面后重试'}}}}; return await response.json(); }} catch (_) {{ return {{ok:false,error:{{message:'请求未能发送，请检查本地控制台是否仍在运行'}}}}; }} }}
function rememberRequestError(target, payload) {{ if (!payload.ok) pendingRequestError={{target:target,message:(payload.error && payload.error.message) || '请求失败，请重试'}}; else pendingRequestError=null; }}
function selectedRecruitingJobId() {{ return recruitingJobSelect.value || (recruitingWorkspace && recruitingWorkspace.selected_job_id) || ''; }}
function renderRecruitingContexts(snapshot) {{
  if(!recruitingContextSelect) return;
  const contexts=(snapshot && snapshot.contexts) || [];
  const active=(snapshot && snapshot.active_context) || {{}};
  recruitingContextSelect.replaceChildren();
  contexts.forEach(item => {{ const option=document.createElement('option'); option.value=item.context_key || ''; option.textContent=item.label || [item.company_id,item.account_id,item.role].filter(Boolean).join(' / '); option.dataset.context=JSON.stringify(item); option.selected=option.value===active.context_key; recruitingContextSelect.append(option); }});
  if(!contexts.length) {{ const option=document.createElement('option'); option.value=active.context_key || ''; option.textContent=active.label || '默认上下文'; option.selected=true; recruitingContextSelect.append(option); }}
  recruitingContextState.textContent=`数据与登录 Profile 已隔离：${{active.company_id || 'default'}} / ${{active.account_id || 'default'}}`;
}}
function appendTextRow(parent, title, value) {{ const row=document.createElement('div'); row.className='workspace-row'; const heading=document.createElement('strong'); heading.textContent=title; const text=document.createElement('div'); text.className='workspace-meta'; text.textContent=value || '（无）'; row.append(heading,text); parent.append(row); }}
function appendMismatchFeedback(parent, candidate, jobId) {{
  const details=document.createElement('details'); details.className='stage-details';
  const summary=document.createElement('summary'); summary.textContent='\u8bb0\u5f55\u4e0d\u5339\u914d\u539f\u56e0';
  const fields=document.createElement('div'); fields.className='stage-fields';
  const reason=document.createElement('select'); reason.setAttribute('aria-label','\u4e0d\u5339\u914d\u539f\u56e0');
  [['city_mismatch','\u57ce\u5e02\u4e0d\u5339\u914d'],['salary_mismatch','\u85aa\u8d44\u4e0d\u5339\u914d'],['education_mismatch','\u5b66\u5386\u4e0d\u5339\u914d'],['experience_mismatch','\u7ecf\u9a8c\u4e0d\u8db3'],['skill_mismatch','\u6838\u5fc3\u6280\u80fd\u4e0d\u5339\u914d'],['direction_mismatch','\u804c\u4e1a\u65b9\u5411\u4e0d\u4e00\u81f4'],['stability_risk','\u7a33\u5b9a\u6027\u98ce\u9669'],['information_incomplete','\u4fe1\u606f\u4e0d\u8db3'],['other','\u5176\u4ed6\u539f\u56e0']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; reason.append(option); }});
  const stage=document.createElement('select'); stage.setAttribute('aria-label','\u7b5b\u9009\u9636\u6bb5'); [['hard_filter','\u786c\u6761\u4ef6'],['semantic_match','\u8bed\u4e49\u5339\u914d'],['risk','\u98ce\u9669\u8bc6\u522b'],['professional_qa','\u4e13\u4e1a\u95ee\u7b54']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; stage.append(option); }});
  const note=document.createElement('input'); note.placeholder='\u8865\u5145\u5907\u6ce8\uff08\u4ec5\u672c\u5730\uff09'; note.maxLength=1000;
  const button=document.createElement('button'); button.type='button'; button.className='secondary'; button.textContent='\u4fdd\u5b58\u539f\u56e0'; const state=document.createElement('span'); state.className='hint';
  button.addEventListener('click', async () => {{ button.disabled=true; state.textContent='\u6b63\u5728\u4fdd\u5b58\u2026'; const payload=await post('/api/recruiting/mismatch-feedback',{{job_id:jobId,candidate_id:candidate.candidate_id,reason_code:reason.value,stage:stage.value,note:note.value}}); if(!payload.ok) {{ state.textContent=(payload.error && payload.error.message) || '\u4fdd\u5b58\u5931\u8d25'; button.disabled=false; return; }} state.textContent='\u5df2\u8bb0\u5f55\uff0c\u672a\u63d0\u4ea4\u5e73\u53f0'; await refresh(); }});
  fields.append(reason,stage,note,button,state); details.append(summary,fields); parent.append(details);
}}
function renderKnowledgeSearch(result) {{
  recruitingKnowledgeSearchResults.replaceChildren();
  const hits=(result && result.hits) || [];
  if(!hits.length) {{ recruitingKnowledgeSearchResults.textContent='没有找到当前岗位的本地事实，请先补充知识库或 FAQ。'; return; }}
  hits.forEach(hit => {{ const row=document.createElement('div'); row.className='citation-row'; const title=document.createElement('strong'); title.textContent=`来源引用 · ${{hit.source_type==='faq'?'FAQ':'知识库'}} · ${{hit.source_title || hit.title || hit.question || '未命名'}}`; const snippet=document.createElement('div'); snippet.className='workspace-meta'; snippet.textContent=hit.snippet || '（无摘录）'; const score=document.createElement('div'); score.className='workspace-meta'; score.textContent=`相关度 ${{hit.score || 0}}`; row.append(title,snippet,score); recruitingKnowledgeSearchResults.append(row); }});
}}
function renderFaqDrafts(payload, jobId) {{
  recruitingFaqDrafts.replaceChildren();
  const drafts=(payload && payload.drafts) || [];
  if(!drafts.length) {{ recruitingFaqDrafts.textContent='当前岗位没有可生成的 FAQ 草稿，请先补充知识库。'; return; }}
  drafts.forEach(draft => {{
    const row=document.createElement('div'); row.className='workspace-row review-banner candidate-assessment-detail';
    const heading=document.createElement('strong'); heading.textContent=`FAQ 草稿 · ${{draft.status || 'pending_review'}}`;
    const source=document.createElement('div'); source.className='workspace-meta'; source.textContent=[
      `来源：${{draft.source_title || '未命名文档'}}`,
      `文档 ID：${{draft.source_document_id || '（无）'}}`,
      `版本：${{draft.source_version || '（无）'}}`
    ].join(' · ');
    const question=document.createElement('input'); question.value=draft.question || ''; question.maxLength=500; question.setAttribute('aria-label','FAQ 草稿问题');
    const answer=document.createElement('textarea'); answer.value=draft.answer || ''; answer.maxLength=4000; answer.setAttribute('aria-label','FAQ 草稿标准答案');
    const variation=document.createElement('textarea'); variation.value=''; variation.maxLength=1000; variation.placeholder='允许变化说明（可选）'; variation.setAttribute('aria-label','FAQ 草稿允许变化说明');
    const actions=document.createElement('div'); actions.className='actions';
    const save=document.createElement('button'); save.type='button'; save.textContent='保存为 FAQ';
    const ignore=document.createElement('button'); ignore.type='button'; ignore.className='secondary'; ignore.textContent='忽略草稿';
    const state=document.createElement('span'); state.className='hint';
    save.addEventListener('click',async () => {{
      if(!question.value.trim() || !answer.value.trim()) {{ state.textContent='问题和答案不能为空'; return; }}
      save.disabled=true; state.textContent='正在保存人工审核后的 FAQ…';
      const result=await post('/api/recruiting/faq',{{job_id:jobId,question:question.value,answer:answer.value,allowed_variation:variation.value,source_document_id:draft.source_document_id || '',source_title:draft.source_title || '',source_version:draft.source_version || ''}});
      if(!result.ok) {{ state.textContent=(result.error && result.error.message) || '保存 FAQ 失败'; save.disabled=false; return; }}
      state.textContent='已保存为 FAQ，来源已保留'; row.remove();
      await refresh();
    }});
    ignore.addEventListener('click',() => {{ row.remove(); recruitingFaqDraftState.textContent='已忽略草稿（未写入 FAQ）'; }});
    actions.append(save,ignore,state); row.append(heading,source,question,answer,variation,actions); recruitingFaqDrafts.append(row);
  }});
}}
async function loadFaqDrafts() {{
  const jobId=selectedRecruitingJobId();
  if(!jobId) {{ recruitingFaqDrafts.replaceChildren(); recruitingFaqDraftState.textContent='请先选择岗位'; recruitingFaqDraftButton.disabled=true; return; }}
  recruitingFaqDraftButton.disabled=true; recruitingFaqDraftState.textContent='正在根据知识库生成草稿…';
  try {{
    const response=await fetch('/api/recruiting/faq-drafts?job_id='+encodeURIComponent(jobId));
    const payload=await response.json();
    if(!payload.ok) {{ recruitingFaqDraftState.textContent=(payload.error && payload.error.message) || 'FAQ 草稿读取失败'; recruitingFaqDrafts.replaceChildren(); return; }}
    renderFaqDrafts(payload.data,jobId);
    recruitingFaqDraftState.textContent=`待审核草稿 ${{(payload.data.drafts || []).length}} 条；未点击保存不会入库`;
  }} catch (_) {{ recruitingFaqDraftState.textContent='FAQ 草稿请求失败，请检查本地控制台'; }}
  finally {{ recruitingFaqDraftButton.disabled=false; }}
}}
async function searchRecruitingKnowledge(event) {{
  event.preventDefault();
  const jobId=selectedRecruitingJobId(); const query=recruitingKnowledgeSearchInput.value.trim();
  if(!jobId) {{ recruitingKnowledgeSearchState.textContent='请先创建并选择岗位'; return; }}
  if(!query) {{ recruitingKnowledgeSearchState.textContent='请输入要核对的问题'; return; }}
  recruitingKnowledgeSearchState.textContent='正在检索本地事实…';
  try {{ const response=await fetch('/api/recruiting/search?job_id='+encodeURIComponent(jobId)+'&q='+encodeURIComponent(query)); const payload=await response.json(); if(!payload.ok) {{ recruitingKnowledgeSearchState.textContent=(payload.error && payload.error.message) || '检索失败'; renderKnowledgeSearch(null); return; }} renderKnowledgeSearch(payload.data); recruitingKnowledgeSearchState.textContent=`已找到 ${{(payload.data.hits || []).length}} 条来源`; }} catch (_) {{ recruitingKnowledgeSearchState.textContent='检索请求失败，请检查本地控制台'; renderKnowledgeSearch(null); }}
}}
async function answerRecruitingQuestion(event) {{
  event.preventDefault();
  const jobId=selectedRecruitingJobId(); const question=recruitingKnowledgeAnswerInput.value.trim();
  if(!jobId) {{ recruitingKnowledgeAnswerState.textContent='请先创建并选择岗位'; return; }}
  if(!question) {{ recruitingKnowledgeAnswerState.textContent='请输入候选人问题'; return; }}
  recruitingKnowledgeAnswerState.textContent='正在核对当前岗位本地事实…'; recruitingKnowledgeAnswerResult.replaceChildren();
  try {{
    const response=await fetch('/api/recruiting/answer?job_id='+encodeURIComponent(jobId)+'&q='+encodeURIComponent(question));
    const payload=await response.json();
    if(!payload.ok) {{ recruitingKnowledgeAnswerState.textContent=(payload.error && payload.error.message) || '试答失败'; return; }}
    const answer=payload.data || {{}}; const row=document.createElement('div'); row.className='citation-row';
    const title=document.createElement('strong'); title.textContent=answer.status==='answered' ? '可核对试答' : '没有可验证来源';
    const text=document.createElement('div'); text.className='workspace-meta'; text.textContent=answer.answer || '（无答案）';
    const source=document.createElement('div'); source.className='workspace-meta'; source.textContent=answer.source_id ? ['来源：'+(answer.source_title || '未命名'),answer.source_version ? '版本：'+answer.source_version : '',answer.confidence==='faq' ? '已审核 FAQ' : '知识库摘录'].filter(Boolean).join(' · ') : '未引用岗位知识；请人工核实或补充 FAQ。';
    row.append(title,text,source); recruitingKnowledgeAnswerResult.append(row);
    recruitingKnowledgeAnswerState.textContent=answer.status==='answered' ? '已生成；请核对来源后再手动回复' : '已安全拒答';
  }} catch (_) {{ recruitingKnowledgeAnswerState.textContent='试答请求失败，请检查本地控制台'; }}
}}
async function copyDraft(text, stateNode) {{ try {{ if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text); else {{ const input=document.createElement('textarea'); input.value=text; document.body.append(input); input.select(); document.execCommand('copy'); input.remove(); }} stateNode.textContent='已复制到剪贴板'; }} catch (_) {{ stateNode.textContent='复制失败，请手动选中话术'; }} }}
function goToRecruitingWorkspace(candidateId='') {{
  if(candidateId) recruitingSelectedCandidateId=String(candidateId);
  location.hash='#recruiting-workspace'; activateNav('recruiting-workspace'); selectRecruitingWorkspaceView('candidates');
  // 导出结果可能刚完成刷新，候选人卡片尚未挂载；延迟一帧后定位，
  // 找不到卡片时退化到导入表单，保证接力按钮永远有可见落点。
  window.setTimeout(() => {{
    const target=recruitingSelectedCandidateId ? Array.from(document.querySelectorAll('[data-candidate-id]')).find(node => node.getAttribute('data-candidate-id')===recruitingSelectedCandidateId) : null;
    // 导出结果带候选人标识时，下一步是生成评估而不是再次寻找候选人。
    // 先自动选中候选人并进入评估，避免“填入评估”按钮只改变下拉框却没有后续落点。
    const assessmentForm=candidateId ? document.querySelector('#recruiting-assess-form') : null;
    const fallback=document.querySelector('#recruiting-candidate-form'); const node=assessmentForm || target || fallback;
    if(!node) return; node.scrollIntoView({{behavior:'smooth',block:'center'}});
    if(candidateId && assessmentForm) {{
      if(recruitingAssessCandidate) recruitingAssessCandidate.value=String(candidateId);
      updateRecruitingAssessmentAvailability();
      const assessmentAction=recruitingAssessButton && !recruitingAssessButton.disabled ? recruitingAssessButton : recruitingAssessCandidate;
      if(assessmentAction) assessmentAction.focus();
      return;
    }}
    const details=target && target.querySelector('details'); if(details) details.open=true;
    const focusable=node.querySelector('button:not(:disabled),input,select,textarea'); if(focusable) focusable.focus();
  }},0);
}}
async function importResumeToWorkspace(path, source, stateNode) {{ const jobId=selectedRecruitingJobId(); stateNode.textContent='正在导入招聘工作台…'; const payload=await post('/api/recruiting/candidates/import',{{resume_path:path,source:source,job_id:jobId}}); if(!payload.ok) {{ pendingImportedResumePath=null; stateNode.textContent=(payload.error && payload.error.message) || '导入失败，请重试'; return; }} pendingImportedResumePath=path; stateNode.textContent=jobId?'已提交导入，已绑定当前岗位并进入招聘工作台…':'已提交导入，正在进入招聘工作台…'; goToRecruitingWorkspace(); await refresh(); }}
function appendResumeImportAction(parent, path, source) {{ if(!path) return; const actions=document.createElement('div'); actions.className='actions'; const button=document.createElement('button'); button.type='button'; button.className='secondary'; button.textContent='导入招聘工作台'; const state=document.createElement('span'); state.className='hint'; button.addEventListener('click',() => importResumeToWorkspace(path,source,state)); actions.append(button,state); parent.append(actions); }}
function renderWorkspaceImport(parent, result, path, source) {{
  const handoff=result && result.workspace_import;
  if(handoff && handoff.state==='imported') {{
    const handoffKey=[handoff.candidate_id || '',handoff.pending_task_id || handoff.next_action || ''].join(':');
    if(handoffKey && handoffKey!==lastWorkspaceHandoffKey) {{
      pendingImportedCandidateId=String(handoff.candidate_id || '');
      lastWorkspaceHandoffKey=handoffKey;
    }}
    const notice=document.createElement('div'); notice.className='notice';
    notice.textContent=`已自动进入招聘工作台：${{handoff.candidate_name || '候选人'}}。${{handoff.job_id ? '已绑定当前岗位。' : ''}}下一步：${{handoff.next_action || '选择岗位并生成评估'}}。`;
    const actions=document.createElement('div'); actions.className='actions';
    const button=document.createElement('button'); button.type='button'; button.className='secondary workspace-import-action'; button.textContent='进入招聘工作台';
    button.addEventListener('click',() => goToRecruitingWorkspace(handoff.candidate_id || ''));
    actions.append(button); parent.append(notice,actions); return;
  }}
  if(handoff && handoff.state==='failed') {{ const notice=document.createElement('div'); notice.className='notice warn'; notice.textContent=handoff.message || '简历已保存；导入失败时可手动重试'; parent.append(notice); }}
  appendResumeImportAction(parent,path,source);
}}
async function submitAssessmentReview(report, outcome, note, stateNode, manualOverride=false, overrideReason='') {{
  if(outcome==='proceed' && manualOverride && !overrideReason.trim()) {{ stateNode.textContent='人工强制继续必须填写理由'; return; }}
  stateNode.textContent='正在保存人工确认…';
  const payload=await post('/api/recruiting/review',{{job_id:report.job_id,candidate_id:report.candidate_id,outcome:outcome,note:note,manual_override:manualOverride,override_reason:overrideReason}});
  if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '人工确认保存失败'; return; }}
  stateNode.textContent=manualOverride?'已保存人工强制继续及审计理由':'人工确认已保存'; await refresh();
}}
// 闭环进度只投影后端给出的下一步，不在浏览器里推断或触发外部平台动作。
function renderRecruitingWorkflow(workflow) {{
  const currentWorkflow=workflow || {{}};
  const nextStep=workflow.next_step || '';
  const pendingCandidateName=String(currentWorkflow.pending_candidate_name || '');
  const pendingTaskTitle=String(currentWorkflow.pending_task_title || '');
  const nextAction=currentWorkflow.next_action || (nextStep ? `下一步：${{nextStep}}` : '当前没有待处理动作');
  recruitingWorkflowNext.textContent=pendingCandidateName && pendingTaskTitle ? `${{pendingCandidateName}} · ${{pendingTaskTitle}}` : nextAction;
  // 后端的每一种待办都必须映射到页面上的真实入口；否则用户会看到“去处理”
  // 却无法点击，流程会在发布、沟通或面试阶段断开。
  const actionTargets={{create_job:'#recruiting-job-form',publish_job:'#recruiting-job-form',import_candidate:'#recruiting-candidate-form',assess_candidate:'#recruiting-task-list',reassess_candidate:'#recruiting-task-list',start_professional_qa:'#recruiting-task-list',private_professional_qa:'#recruiting-task-list',review_resume:'#recruiting-task-list',review_assessment:'#recruiting-assessment-result',confirm_basic:'#recruiting-task-list',complete_basic:'#recruiting-task-list',prepare_resume_exchange:'#recruiting-task-list',continue_conversation:'#recruiting-task-list',communication_round:'#recruiting-task-list',communication_follow_up:'#recruiting-task-list',record_private_contact:'#recruiting-task-list',prepare_interview:'#recruiting-task-list',schedule_interview:'#recruiting-task-list',record_interview:'#recruiting-task-list',record_hiring_decision:'#recruiting-task-list',recover_task:'#recruiting-task-list',record_stage:'#recruiting-candidate-list',follow_up:'#recruiting-task-list',closed:'#recruiting-candidate-form'}};
  const targetSelector=actionTargets[nextStep];
  const pendingTaskId=String(currentWorkflow.pending_task_id || '');
  const focusTaskId=String(currentWorkflow.focus_task_id || pendingTaskId || '');
  const focusCandidateId=String(currentWorkflow.focus_candidate_id || currentWorkflow.pending_candidate_id || '');
  const actionLabels={{recover_task:'恢复待办',record_stage:'记录阶段',closed:'导入下一位候选人'}};
  recruitingWorkflowAction.textContent=actionLabels[nextStep] || '去处理';
  recruitingWorkflowAction.disabled=!targetSelector;
  recruitingWorkflowAction.onclick=() => {{
    // 当前待办优先于通用区域；没有待办时再定位到候选人卡片，确保
    // “恢复待办”和“记录阶段”都能落到真实控件而不是空白区域。
    const taskTarget=nextStep==='closed' ? null : (focusTaskId ? Array.from(document.querySelectorAll('[data-task-id]')).find(node => node.getAttribute('data-task-id')===focusTaskId) : null);
    const candidateTarget=nextStep==='closed' ? null : (focusCandidateId ? Array.from(document.querySelectorAll('[data-candidate-id]')).find(node => node.getAttribute('data-candidate-id')===focusCandidateId) : null);
    if(nextStep==='closed') {{ recruitingSelectedCandidateId=''; pendingImportedResumePath=null; selectRecruitingWorkspaceView('candidates'); }}
    else if(['create_job','publish_job'].includes(nextStep)) selectRecruitingWorkspaceView('jobs');
    else if(['assess_candidate','reassess_candidate','start_professional_qa','private_professional_qa','review_resume','review_assessment'].includes(nextStep)) selectRecruitingWorkspaceView('candidates');
    else selectRecruitingWorkspaceView('candidates');
    const target=nextStep==='closed' ? document.querySelector(targetSelector) : (taskTarget || candidateTarget || document.querySelector(targetSelector || '#recruiting-workspace'));
    if(target) {{
      target.scrollIntoView({{behavior:'smooth',block:'center'}});
      if(taskTarget) {{ const firstAction=taskTarget.querySelector('button:not(:disabled),select,input,textarea'); if(firstAction) firstAction.focus(); }}
      else if(candidateTarget) {{
        const details=candidateTarget.querySelector('details'); if(details) details.open=true;
        // 无待办时的“记录阶段”必须聚焦阶段详情里的保存控件，不能被卡片顶部
        // 的“填入评估”按钮抢走焦点，否则用户看见入口却仍不知道如何推进。
        const recordStageAction=nextStep==='record_stage' && details ? details.querySelector('button:not(:disabled)') : null;
        const firstAction=recordStageAction || candidateTarget.querySelector('button:not(:disabled),select,input,textarea'); if(firstAction) firstAction.focus();
      }}
      else if(nextStep==='publish_job' && !recruitingPublishJobButton.disabled) recruitingPublishJobButton.focus();
      else if(nextStep==='review_assessment') {{ const firstAction=target.querySelector('button:not(:disabled),input,select,textarea'); if(firstAction) firstAction.focus(); }}
      else if(nextStep==='create_job' || nextStep==='closed') {{ const firstInput=target.querySelector('input[name="resume_path"],input,textarea,select,button'); if(firstInput) firstInput.focus(); }}
    }}
  }};
  recruitingWorkflowSteps.replaceChildren();
  (currentWorkflow.steps || []).forEach(step => {{
    const item=document.createElement('div'); item.className='workflow-step '+(step.status || 'pending');
    const label=document.createElement('div'); label.className='workflow-step-label'; label.textContent=step.label || step.key || '流程步骤';
    const description=document.createElement('div'); description.className='workflow-step-description'; description.textContent=step.description || '';
    const status=document.createElement('div'); status.className='workflow-step-status'; status.textContent=step.status==='complete'?'已完成':(step.status==='current'?'当前下一步':'待处理');
    item.append(label,description,status); recruitingWorkflowSteps.append(item);
  }});
}}
function renderRecruitingPipeline(pipeline) {{
  recruitingPipelineSummary.replaceChildren();
  const counts=(pipeline && pipeline.counts) || {{}};
  pipelineSummaryStages.forEach(([stage,label]) => {{ const step=document.createElement('div'); step.className='pipeline-step'; if((counts[stage] || 0)>0) step.classList.add('is-active'); const title=document.createElement('div'); title.className='pipeline-step-label'; title.textContent=label; const count=document.createElement('div'); count.className='pipeline-step-count'; count.textContent=String(counts[stage] || 0); step.append(title,count); recruitingPipelineSummary.append(step); }});
  const terminal=document.createElement('div'); terminal.className='pipeline-terminal-summary'; const terminalHeading=document.createElement('span'); terminalHeading.className='pipeline-terminal-label'; terminalHeading.textContent='流程已到终局'; terminal.append(terminalHeading); [['hired','录用'],['rejected','淘汰'],['paused','暂缓']].forEach(([stage,label]) => {{ const item=document.createElement('div'); item.className='pipeline-terminal'; const text=document.createElement('span'); text.textContent=label; const count=document.createElement('strong'); count.textContent=String(counts[stage] || 0); item.append(text,count); terminal.append(item); }}); recruitingPipelineSummary.append(terminal);
  const totals=document.createElement('div'); totals.className='pipeline-totals'; totals.textContent=`共 ${{pipeline && pipeline.total || 0}} 位候选人 · 活跃 ${{pipeline && pipeline.active || 0}} 位 · 阶段变更只记录本地审计`; recruitingPipelineSummary.append(totals);
  const metrics=document.createElement('div'); metrics.id='recruiting-funnel-metrics'; metrics.className='funnel-metrics';
  const heading=document.createElement('div'); heading.className='funnel-metrics-heading'; heading.textContent='转化率 · 阶段平均停留'; metrics.append(heading);
  const conversion=(pipeline && pipeline.conversion) || {{}}; const conversionRow=document.createElement('div'); conversionRow.className='funnel-metrics-row'; [['初筛覆盖',conversion.screened_rate],['完成评估/复核',conversion.reviewed_rate],['进入终局',conversion.terminal_rate]].forEach(([label,value]) => {{ const item=document.createElement('span'); item.textContent=`${{label}}：`; const strong=document.createElement('strong'); strong.textContent=`${{value || 0}}%`; item.append(strong); conversionRow.append(item); }}); metrics.append(conversionRow);
  const funnel=(pipeline && pipeline.funnel) || []; funnel.filter(item => item.count > 0).slice(0,8).forEach(item => {{ const row=document.createElement('div'); row.className='funnel-metrics-row'; row.textContent=`${{item.label}}：${{item.count}} 人 · 占比 ${{item.share}}% · 平均停留 ${{item.avg_age_hours}} 小时`; metrics.append(row); }});
  const sources=(pipeline && pipeline.sources) || []; if(sources.length) {{ const sourceRow=document.createElement('div'); sourceRow.className='funnel-metrics-row'; sourceRow.textContent='来源：'+sources.map(item => `${{item.label}} ${{item.count}} 人 (${{item.share}}%)`).join(' · '); metrics.append(sourceRow); }}
  recruitingPipelineSummary.append(metrics);
}}
function taskStatusText(status) {{ return {{pending:'待处理',completed:'已完成',skipped:'已跳过'}}[status] || '未知'; }}
  async function updateRecruitingTask(task, status, note, stateNode, targetStage) {{
    stateNode.textContent=status==='completed'?'正在完成待办…':(status==='pending'?'正在恢复待办…':'正在跳过待办…');
    const payload=await post('/api/recruiting/tasks/'+encodeURIComponent(task.task_id),{{status:status,note:note || '',target_stage:targetStage || ''}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '待办更新失败'; return; }}
    stateNode.textContent=status==='completed'?'待办已完成，阶段已更新':(status==='pending'?'待办已恢复':'待办已跳过');
    await refresh();
  }}
  async function recordCommunication(task, outcome, summary, nextFollowUpAt, note, templateKey, templateVersion, stateNode) {{
    const jobId=task.job_id || selectedRecruitingJobId();
    if(!jobId) {{ stateNode.textContent='请先创建并选择岗位'; return; }}
    if((outcome==='follow_up' || outcome==='no_response') && !nextFollowUpAt.trim() && task.communication_round < 4) {{ stateNode.textContent='待跟进或未回复必须填写下一次跟进时间'; return; }}
    stateNode.textContent=`正在保存第 ${{task.communication_round || 1}} 轮沟通…`;
    const payload=await post('/api/recruiting/communications',{{job_id:jobId,candidate_id:task.candidate_id,round_number:task.communication_round || 1,outcome:outcome,candidate_reply_summary:summary || '',next_follow_up_at:nextFollowUpAt || '',note:note || '',template_key:templateKey || '',template_version:templateVersion || ''}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '沟通记录保存失败'; return; }}
    stateNode.textContent='沟通记录已保存，下一步已进入待办中心';
    await refresh();
  }}
  async function recordPrivateContact(task, channel, contactStatus, note, stateNode) {{
    stateNode.textContent='正在保存私域结果…';
    const payload=await post('/api/recruiting/private-contacts',{{job_id:task.job_id || selectedRecruitingJobId(),candidate_id:task.candidate_id,channel:channel,status:contactStatus,note:note || ''}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '私域结果保存失败'; return; }}
    stateNode.textContent=contactStatus==='added'?'已记录私域添加，进入面试准备':'已记录私域结果';
    await refresh();
  }}
  async function recordInterview(task, jobId, scheduledAt, interviewer, note, stateNode) {{
    if(!jobId || !scheduledAt) {{ stateNode.textContent='请填写面试时间'; return; }}
    stateNode.textContent='正在保存面试邀约…';
    const payload=await post('/api/recruiting/interviews',{{job_id:jobId,candidate_id:task.candidate_id,scheduled_at:scheduledAt,interviewer:interviewer || '',note:note || ''}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '面试邀约保存失败'; return; }}
    stateNode.textContent='已记录面试邀约';
    await refresh();
  }}
  async function recordInterviewResult(task, jobId, outcome, note, stateNode) {{
    if(!jobId || !outcome) {{ stateNode.textContent='请先选择面试结果'; return; }}
    stateNode.textContent='正在保存面试结果…';
    const payload=await post('/api/recruiting/interviews/result',{{job_id:jobId,candidate_id:task.candidate_id,outcome:outcome,note:note || ''}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '面试结果保存失败'; return; }}
    stateNode.textContent='已记录面试结果';
    await refresh();
  }}
  async function recordPrivateProfessionalQa(task, question, answer, questionId, questionVersion, sourceIds, outcome, note, stateNode) {{
    if(!question.trim() || !answer.trim()) {{ stateNode.textContent='请填写私域核验问题和回答'; return; }}
    stateNode.textContent=outcome==='passed'?'正在保存私域核验通过…':'正在保存私域核验并等待补充…';
    const payload=await post('/api/recruiting/private-professional-qa',{{
      job_id:task.job_id || selectedRecruitingJobId(),
      candidate_id:task.candidate_id,
      question:question,
      answer:answer,
      question_id:questionId || '',
      question_version:questionVersion || 'v1',
      source_ids:sourceIds.split(/[,，\\s]+/).map(item => item.trim()).filter(Boolean),
      outcome:outcome,
      note:note || ''
    }});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '私域专业核验保存失败'; return; }}
    stateNode.textContent=outcome==='passed'?'私域专业核验已通过，下一步进入人工确认/交换简历':'已记录追问，当前待办保持打开';
    await refresh();
  }}
function appendTaskTargetSelect(parent, task) {{
    const stages=task.allowed_target_stages || [];
    if(!stages.length) return null;
    const select=document.createElement('select'); select.setAttribute('aria-label','终局决定');
    const labels={{hired:'录用',rejected:'淘汰',paused:'暂缓'}};
    stages.forEach(stage => {{ const option=document.createElement('option'); option.value=stage; option.textContent=labels[stage] || task.allowed_target_stage_labels && task.allowed_target_stage_labels[stages.indexOf(stage)] || stage; select.append(option); }});
    parent.append(select); return select;
  }}
  // 评估待办必须走专用接口，完成后由后端生成报告和人工复核待办。
  async function runInlineAssessment(task, jobId, stateNode) {{
    if(!jobId) {{ stateNode.textContent='请先创建并选择岗位标准'; return; }}
    stateNode.textContent='正在生成本地评估…';
    const payload=await post('/api/recruiting/assess',{{job_id:jobId,candidate_id:task.candidate_id}});
    if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '评估生成失败'; return; }}
    stateNode.textContent='评估已生成，请查看证据并人工确认';
    await refresh();
  }}
function renderRecruitingTasks(data) {{
  const tasks=(data && data.tasks) || [];
  const pending=tasks.filter(task => task.status==='pending');
  const completed=tasks.filter(task => task.status==='completed').length;
  const skipped=tasks.filter(task => task.status==='skipped').length;
  recruitingTaskSummary.textContent=`待处理 ${{pending.length}} 项 · 已完成 ${{completed}} 项 · 已跳过 ${{skipped}} 项`;
  recruitingTaskList.replaceChildren();
  if(!tasks.length) {{ recruitingTaskList.textContent='导入候选人后，这里会自动生成下一步待办。'; return; }}
  // 待处理项永远排在已完成历史之前；当前下一步即使超过 30 条也必须保留。
  const orderedTasks=tasks.slice().sort((left,right) => {{
    const pendingOrder=(right.status==='pending'?1:0)-(left.status==='pending'?1:0);
    if(pendingOrder) return pendingOrder;
    return String(right.updated_at || right.created_at || '').localeCompare(String(left.updated_at || left.created_at || ''));
  }});
  const visibleTasks=orderedTasks.slice(0,30);
  const pendingTaskId=String((data && data.workflow && data.workflow.pending_task_id) || '');
  if(pendingTaskId && !visibleTasks.some(task => task.task_id===pendingTaskId)) {{
    const currentTask=orderedTasks.find(task => task.task_id===pendingTaskId);
    if(currentTask) visibleTasks.push(currentTask);
  }}
  visibleTasks.forEach(task => {{
    const row=document.createElement('div'); row.className='task-row'+(task.status!=='pending'?' is-done':'')+(task.task_id===pendingTaskId?' is-next':'');
    row.setAttribute('data-task-id',task.task_id || '');
    const heading=document.createElement('div'); heading.className='task-title'; heading.textContent=`${{task.candidate_name || '候选人'}} · ${{task.title}}`;
    const description=document.createElement('div'); description.className='workspace-meta'; description.textContent=task.description || '本地人工待办';
    const meta=document.createElement('div'); meta.className='workspace-meta'; meta.textContent=[task.communication_round ? `第 ${{task.communication_round}} 轮` : '',task.target_stage_label ? `完成后进入：${{task.target_stage_label}}` : '',task.due_at ? `计划跟进：${{task.due_at}}` : '',`状态：${{taskStatusText(task.status)}}`,task.updated_at ? `更新时间：${{task.updated_at}}` : ''].filter(Boolean).join(' · ');
    row.append(heading,description,meta);
    if(task.status==='pending') {{
      const actions=document.createElement('div'); actions.className='task-actions';
      const note=document.createElement('input'); note.placeholder='记录备注（可选）'; note.maxLength=1000; note.setAttribute('aria-label','待办完成备注');
      const state=document.createElement('span'); state.className='hint';
      if(task.kind==='assess_candidate' || task.kind==='reassess_candidate' || task.kind==='start_professional_qa' || task.kind==='review_resume') {{
        const jobSelect=document.createElement('select'); jobSelect.setAttribute('aria-label','评估岗位');
        const jobs=((data && data.jobs) || []).filter(job => job.status==='published');
        const taskJobs=task.job_id ? jobs.filter(job => job.job_id===task.job_id) : jobs;
        taskJobs.forEach(job => {{ const option=document.createElement('option'); option.value=job.job_id; option.textContent=[job.name,job.city].filter(Boolean).join(' · '); jobSelect.append(option); }});
        const suggestedJob=task.job_id || (data && data.selected_job_id) || (jobs[0] && jobs[0].job_id) || '';
        jobSelect.value=suggestedJob;
        if(!taskJobs.length) {{ const empty=document.createElement('option'); empty.value=''; empty.textContent=task.job_id?'当前岗位未发布':'请先创建已发布岗位'; jobSelect.append(empty); }}
        const assessButton=document.createElement('button'); assessButton.type='button'; assessButton.textContent=task.kind==='start_professional_qa'?'生成专业问题':'生成本地评估'; assessButton.disabled=!taskJobs.length; assessButton.addEventListener('click',() => runInlineAssessment(task,jobSelect.value,state));
        actions.append(jobSelect,assessButton);
      }} else if(task.kind==='confirm_basic' || task.kind==='complete_basic') {{
        const intentNote=document.createElement('input'); intentNote.placeholder='已确认城市、薪资、工作节奏等基础意向'; intentNote.maxLength=1000; intentNote.setAttribute('aria-label','基础意向确认备注');
        const confirm=document.createElement('button'); confirm.type='button'; confirm.textContent='记录基础意向'; confirm.addEventListener('click',async () => {{ if(!intentNote.value.trim()) {{ state.textContent='请填写基础意向确认备注'; return; }} state.textContent='正在保存基础意向…'; const payload=await post('/api/recruiting/basic-intent',{{job_id:task.job_id || selectedRecruitingJobId(),candidate_id:task.candidate_id,note:intentNote.value}}); if(!payload.ok) {{ state.textContent=(payload.error && payload.error.message) || '基础意向保存失败'; return; }} state.textContent='基础意向已记录，下一步进入专业问答'; await refresh(); }});
        actions.append(intentNote,confirm);
      }} else if(task.kind==='private_professional_qa') {{
        const question=document.createElement('textarea'); question.placeholder='私域核验问题（例如：请说明一个客户开发项目）'; question.maxLength=1000; question.setAttribute('aria-label','私域核验问题');
        const answer=document.createElement('textarea'); answer.placeholder='候选人回答（仅保存本地摘要正文，不进入页面状态快照）'; answer.maxLength=8000; answer.setAttribute('aria-label','私域核验回答');
        const questionId=document.createElement('input'); questionId.placeholder='问题标识（可选）'; questionId.maxLength=128; questionId.setAttribute('aria-label','私域核验问题标识');
        const questionVersion=document.createElement('input'); questionVersion.placeholder='问题版本（默认 v1）'; questionVersion.value='v1'; questionVersion.maxLength=64; questionVersion.setAttribute('aria-label','私域核验问题版本');
        const sourceIds=document.createElement('input'); sourceIds.placeholder='来源 ID（可选，多个用逗号分隔）'; sourceIds.maxLength=512; sourceIds.setAttribute('aria-label','私域核验来源');
        const outcome=document.createElement('select'); outcome.setAttribute('aria-label','私域核验结论'); [['follow_up','需要追问'],['passed','核验通过']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; outcome.append(option); }});
        const record=document.createElement('button'); record.type='button'; record.textContent='保存私域核验'; record.addEventListener('click',() => recordPrivateProfessionalQa(task,question.value,answer.value,questionId.value,questionVersion.value,sourceIds.value,outcome.value,note.value,state));
        actions.append(question,answer,questionId,questionVersion,sourceIds,outcome,record);
      }} else if(task.kind==='review_assessment') {{
        const report=(data.assessments || []).find(item => item.candidate_id===task.candidate_id && item.job_id===task.job_id);
        const reviewLabel=document.createElement('span'); reviewLabel.className='workspace-meta'; reviewLabel.textContent='人工确认结果'; actions.append(reviewLabel);
        if(report) {{
          const gate=report.review_gate || {{}}; const reviewCandidate=((data && data.candidates) || []).find(item => item.candidate_id===task.candidate_id) || {{}}; const nextReviewLabel=reviewCandidate.stage==='resume_exchanged'?'进入 BOSS 沟通':'进入简历交换'; const gateText=document.createElement('div'); gateText.className=gate.eligible?'notice success':'notice warn'; gateText.textContent=gate.eligible?`评估门禁已通过，可${{nextReviewLabel}}`:`默认推进已阻断：${{gate.summary || '请先补齐评估条件'}}`; actions.append(gateText);
          const override=document.createElement('label'); override.className='workspace-meta'; const overrideInput=document.createElement('input'); overrideInput.type='checkbox'; overrideInput.setAttribute('aria-label','人工强制继续'); override.append(overrideInput,document.createTextNode(' 人工强制继续（仅业务例外）'));
          const overrideReason=document.createElement('input'); overrideReason.placeholder='强制继续理由（勾选后必填）'; overrideReason.maxLength=1000; overrideReason.setAttribute('aria-label','强制继续理由');
          [['proceed',`确认通过，${{nextReviewLabel}}`],['follow_up','需要补充信息'],['reject','暂不推进']].forEach(([outcome,label]) => {{ const reviewButton=document.createElement('button'); reviewButton.type='button'; reviewButton.className='secondary'; reviewButton.textContent=label; reviewButton.addEventListener('click',() => submitAssessmentReview(report,outcome,note.value,state,overrideInput.checked,overrideReason.value)); actions.append(reviewButton); }});
          actions.append(override,overrideReason);
        }} else {{ state.textContent='评估报告尚未生成，请先完成评估'; }}
      }} else if(task.kind==='continue_conversation' || task.kind==='communication_round' || task.kind==='communication_follow_up') {{
        const round=document.createElement('div'); round.className='workspace-meta'; round.textContent=`第 ${{task.communication_round || 1}} 轮沟通`;
        const outcome=document.createElement('select'); outcome.setAttribute('aria-label','沟通结果'); [['continue','继续沟通'],['follow_up','待跟进'],['no_response','未回复'],['qualified','沟通通过'],['declined','明确拒绝']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; outcome.append(option); }});
        const reply=document.createElement('textarea'); reply.placeholder='候选人回复摘要（仅保存人工归纳）'; reply.maxLength=2000; reply.setAttribute('aria-label','候选人回复摘要');
         const nextFollowUp=document.createElement('input'); nextFollowUp.placeholder='下一次跟进时间（如 2026-08-04 10:00）'; nextFollowUp.maxLength=160; nextFollowUp.setAttribute('aria-label','下一次跟进时间'); nextFollowUp.value=task.due_at || '';
         const templateKey=document.createElement('input'); templateKey.placeholder='使用的话术标识（可选）'; templateKey.maxLength=128; templateKey.setAttribute('aria-label','使用的话术标识');
         const templateVersion=document.createElement('input'); templateVersion.placeholder='话术版本（可选，默认 v1）'; templateVersion.maxLength=64; templateVersion.setAttribute('aria-label','话术版本');
         const record=document.createElement('button'); record.type='button'; record.textContent='保存本轮沟通'; record.addEventListener('click',() => recordCommunication(task,outcome.value,reply.value,nextFollowUp.value,note.value,templateKey.value,templateVersion.value,state));
        if(task.due_at) {{ const due=document.createElement('span'); due.className='task-due'; due.textContent=`计划跟进：${{task.due_at}}`; actions.append(due); }}
         actions.append(round,outcome,reply,nextFollowUp,templateKey,templateVersion,record);
      }} else if(task.kind==='record_private_contact') {{
        const channel=document.createElement('select'); channel.setAttribute('aria-label','私域渠道'); [['wechat','微信'],['phone','电话'],['email','邮件'],['other','其他']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; channel.append(option); }});
        const contactStatus=document.createElement('select'); contactStatus.setAttribute('aria-label','私域结果'); [['added','已添加'],['pending','待确认'],['declined','未添加']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; contactStatus.append(option); }});
        const record=document.createElement('button'); record.type='button'; record.textContent='记录私域结果'; record.addEventListener('click',() => recordPrivateContact(task,channel.value,contactStatus.value,note.value,state));
        actions.append(channel,contactStatus,record);
      }} else if(task.kind==='schedule_interview') {{
        const scheduledAt=document.createElement('input'); scheduledAt.type='text'; scheduledAt.placeholder='面试时间，例如 2026-08-03 14:00'; scheduledAt.setAttribute('aria-label','面试时间');
        const interviewer=document.createElement('input'); interviewer.placeholder='面试官（可选）'; interviewer.setAttribute('aria-label','面试官');
        const record=document.createElement('button'); record.type='button'; record.textContent='记录面试邀约'; record.addEventListener('click',() => recordInterview(task,task.job_id || selectedRecruitingJobId(),scheduledAt.value,interviewer.value,note.value,state));
        actions.append(scheduledAt,interviewer,record);
      }} else if(task.kind==='record_interview') {{
        const outcome=document.createElement('select'); outcome.setAttribute('aria-label','面试结果'); [['passed','通过'],['failed','未通过'],['cancelled','取消']].forEach(([value,label]) => {{ const option=document.createElement('option'); option.value=value; option.textContent=label; outcome.append(option); }});
        const record=document.createElement('button'); record.type='button'; record.textContent='记录面试结果'; record.addEventListener('click',() => recordInterviewResult(task,task.job_id || selectedRecruitingJobId(),outcome.value,note.value,state));
        actions.append(outcome,record);
      }} else {{
        const complete=document.createElement('button'); complete.type='button'; complete.textContent='标记完成';
        let targetSelect=null;
        if(task.kind==='record_hiring_decision') targetSelect=appendTaskTargetSelect(actions,task);
        complete.addEventListener('click',() => updateRecruitingTask(task,'completed',note.value,state,targetSelect ? targetSelect.value : ''));
        actions.append(complete);
      }}
      if(task.kind!=='assess_candidate' && task.kind!=='reassess_candidate' && task.kind!=='start_professional_qa' && task.kind!=='private_professional_qa' && task.kind!=='review_resume' && task.kind!=='review_assessment' && task.kind!=='confirm_basic' && task.kind!=='complete_basic') {{
        const skip=document.createElement('button'); skip.type='button'; skip.className='secondary'; skip.textContent='跳过';
        skip.addEventListener('click',() => updateRecruitingTask(task,'skipped',note.value,state));
        actions.append(skip);
      }}
      actions.append(note,state); row.append(actions);
    }} else if(task.status==='skipped') {{
      const actions=document.createElement('div'); actions.className='task-actions';
      const restore=document.createElement('button'); restore.type='button'; restore.className='secondary'; restore.textContent='恢复待办';
      const state=document.createElement('span'); state.className='hint';
      restore.addEventListener('click',() => updateRecruitingTask(task,'pending','从历史记录恢复',state,''));
      actions.append(restore,state); row.append(actions);
    }}
    recruitingTaskList.append(row);
  }});
}}
function renderRecruitingActivities(data) {{
  recruitingActivityList.replaceChildren();
  const contacts=(data && data.private_contacts) || [];
  const communications=(data && data.communications) || [];
  const interviews=(data && data.interviews) || [];
  const decisions=(data && data.decisions) || [];
  const mismatchFeedback=(data && data.mismatch_feedback) || [];
  const candidates=(data && data.candidates) || [];
  const nameFor = candidateId => {{ const candidate=candidates.find(item => item.candidate_id===candidateId); return candidate ? candidate.name : '候选人'; }};
  const rows=[];
  communications.slice().reverse().slice(0,16).forEach(item => rows.push({{time:item.created_at,title:`${{nameFor(item.candidate_id)}} · ${{item.round_label || ('第 '+item.round_number+' 轮沟通')}} · ${{item.outcome_label || ''}}`,meta:[item.next_follow_up_at ? '下次跟进 '+item.next_follow_up_at : '',item.candidate_reply_summary,item.note].filter(Boolean).join(' · ')}}));
  contacts.slice(0,12).forEach(item => rows.push({{time:item.created_at,title:`${{nameFor(item.candidate_id)}} · 私域${{item.status_label || ''}}`,meta:[item.channel,item.note].filter(Boolean).join(' · ')}}));
  interviews.slice(0,12).forEach(item => rows.push({{time:item.updated_at || item.created_at,title:`${{nameFor(item.candidate_id)}} · 面试${{item.status_label || ''}}`,meta:[item.scheduled_at,item.interviewer,item.note].filter(Boolean).join(' · ')}}));
  decisions.slice(0,12).forEach(item => rows.push({{time:item.created_at,title:`${{nameFor(item.candidate_id)}} · ${{item.outcome_label || '终局决定'}}`,meta:item.reason || ''}}));
  mismatchFeedback.slice(0,16).forEach(item => rows.push({{time:item.created_at,title:`${{nameFor(item.candidate_id)}} · 不匹配：${{item.reason_label || item.reason_code}}`,meta:[item.stage,item.note,'仅本地记录'].filter(Boolean).join(' · ')}}));
  rows.sort((left,right) => String(right.time).localeCompare(String(left.time)));
  if(!rows.length) {{ recruitingActivityList.textContent='完成沟通、私域或面试待办后，这里会显示本地事实记录。'; return; }}
  rows.slice(0,20).forEach(item => {{ const row=document.createElement('div'); row.className='workspace-row'; const title=document.createElement('strong'); title.textContent=item.title; const meta=document.createElement('div'); meta.className='workspace-meta'; meta.textContent=[item.time,item.meta].filter(Boolean).join(' · '); row.append(title,meta); recruitingActivityList.append(row); }});
}}
function renderRecruitingInsights(data) {{
  recruitingInsightsList.replaceChildren();
  const optimization=(data && data.optimization) || {{}};
  const metrics=optimization.metrics || {{}};
  const metricText=[`候选人 ${{metrics.candidate_count || 0}}`, `评估 ${{metrics.assessment_count || 0}}`, `知识 ${{metrics.knowledge_count || 0}}`, `FAQ ${{metrics.faq_count || 0}}`, `问答低于 60 分 ${{metrics.qa_below_threshold_count || 0}}`].join(' · ');
   appendTextRow(recruitingInsightsList,'当前复盘指标',metricText);
   const sampleNotice=metrics.sample_notice || ''; if(sampleNotice) {{ const notice=document.createElement('div'); notice.className='notice warn insight-sample-notice'; notice.textContent=sampleNotice; recruitingInsightsList.append(notice); }}
   const appendInsightGroup=(title,entries,formatLabel) => {{ const values=Array.isArray(entries) ? entries : Object.entries(entries || {{}}).map(([key,value]) => ({{key:key,...value}})); if(!values.length) return; const group=document.createElement('div'); group.className='insight-group'; const heading=document.createElement('div'); heading.className='insight-group-title'; heading.textContent=title; group.append(heading); values.slice(0,5).forEach(value => {{ const item=document.createElement('div'); item.className='insight-item'; const main=document.createElement('strong'); main.textContent=formatLabel(value); const meta=document.createElement('span'); meta.className='insight-item-meta'; meta.textContent=`${{value.count || value.sample_size || 0}} 次 · ${{value.rate || value.reply_rate || 0}}%${{value.low_sample ? ' · 样本少' : ''}}`; const bar=document.createElement('div'); bar.className='insight-bar'; const fill=document.createElement('span'); fill.className='insight-bar-fill'; fill.style.width=Math.min(100,Math.max(0,Number(value.rate || value.reply_rate || 0)))+'%'; bar.append(fill); item.append(main,meta,bar); group.append(item); }}); recruitingInsightsList.append(group); }};
   const questionDemandRates=metrics.question_demand_rates || {{}}; if(Object.keys(questionDemandRates).length) appendInsightGroup('候选人问题需求',questionDemandRates,value => value.question || value.key || '未命名问题'); else appendTextRow(recruitingInsightsList,'候选人问题需求','尚未记录候选人试答问题。');
   const topFaqQuestions=metrics.top_faq_questions || []; if(topFaqQuestions.length) appendInsightGroup('FAQ 需求排行',topFaqQuestions,value => value.question || value.faq_id || '未命名 FAQ'); else appendTextRow(recruitingInsightsList,'FAQ 需求排行','尚未有 FAQ 命中记录。');
   const templateOutcomeRates=metrics.template_outcome_rates || {{}}; if(Object.keys(templateOutcomeRates).length) appendInsightGroup('话术结果',templateOutcomeRates,value => value.key || '未命名话术'); else appendTextRow(recruitingInsightsList,'话术结果','尚未记录人工使用的话术版本。');
   const sourceConversion=metrics.source_conversion || {{}}; Object.entries(sourceConversion).forEach(([source,value]) => appendTextRow(recruitingInsightsList,`来源转化 · ${{source}}`,`候选人 ${{value.candidate_count || 0}} · 终局 ${{value.terminal_count || 0}} · 终局率 ${{value.terminal_rate || 0}}%`));
   const stageConversion=metrics.stage_conversion || {{}}; if(Object.keys(stageConversion).length) appendTextRow(recruitingInsightsList,'阶段分布',Object.entries(stageConversion).map(([stage,count]) => `${{stage}} ${{count}}`).join(' · '));
   const mismatchReasonRates=metrics.mismatch_reason_rates || {{}}; const mismatchReasonLabels={{city_mismatch:'城市不匹配',salary_mismatch:'薪资不匹配',education_mismatch:'学历不匹配',experience_mismatch:'经验不足',skill_mismatch:'核心技能不匹配',direction_mismatch:'职业方向不一致',stability_risk:'稳定性风险',information_incomplete:'信息不足',other:'其他原因'}}; if(Object.keys(mismatchReasonRates).length) appendTextRow(recruitingInsightsList,'不匹配原因率',Object.entries(mismatchReasonRates).map(([reason,value]) => `${{mismatchReasonLabels[reason] || reason}} ${{value.count || 0}} 次 (${{value.rate || 0}}%)`).join(' · '));
   const communicationOutcomeRates=metrics.communication_outcome_rates || {{}}; const communicationLabels={{qualified:'沟通通过',continue:'继续沟通',follow_up:'待跟进',no_response:'未回复',declined:'明确拒绝'}}; if(Object.keys(communicationOutcomeRates).length) appendTextRow(recruitingInsightsList,'沟通结果率',Object.entries(communicationOutcomeRates).map(([outcome,value]) => `${{communicationLabels[outcome] || outcome}} ${{value.count || 0}} 次 (${{value.rate || 0}}%)`).join(' · '));
   const decisionOutcomeRates=metrics.decision_outcome_rates || {{}}; const decisionLabels={{hired:'录用',rejected:'淘汰',paused:'暂缓'}}; if(Object.keys(decisionOutcomeRates).length) appendTextRow(recruitingInsightsList,'终局结果率',Object.entries(decisionOutcomeRates).map(([outcome,value]) => `${{decisionLabels[outcome] || outcome}} ${{value.count || 0}} 次 (${{value.rate || 0}}%)`).join(' · '));
   const templateEffectiveness=metrics.template_effectiveness || {{}}; Object.entries(templateEffectiveness).forEach(([template,value]) => appendTextRow(recruitingInsightsList,`话术效果 · ${{template}}`,`人工使用 ${{value.usage_count || 0}} 次 · 关联候选人 ${{value.candidate_count || 0}} 人 · 沟通通过率 ${{value.qualified_rate || 0}}%`));
   const hiringLearning=metrics.hiring_learning || {{}};
   if(hiringLearning.status==='insufficient_data') {{
     appendTextRow(recruitingInsightsList,'录用结果学习',hiringLearning.notice || '尚未有足够的录用与其他终局记录进行比较。');
   }} else {{
     appendTextRow(recruitingInsightsList,'录用结果学习',`录用 ${{hiringLearning.hired_count || 0}} 人 · 其他终局 ${{hiringLearning.comparison_count || 0}} 人${{hiringLearning.low_sample ? ' · 样本少' : ''}}`);
     const learningNumeric=Array.isArray(hiringLearning.numeric_signals) ? hiringLearning.numeric_signals : [];
     learningNumeric.slice(0,5).forEach(signal => {{
       const direction=Number(signal.difference || 0) > 0 ? '录用组更高' : (Number(signal.difference || 0) < 0 ? '录用组更低' : '两组相同');
       appendTextRow(recruitingInsightsList,`录用信号 · ${{signal.label || signal.key}}`,`${{direction}} · ${{signal.hired_average ?? '-'}} vs ${{signal.comparison_average ?? '-'}} · 差 ${{signal.difference ?? 0}}`);
     }});
     const learningProfiles=Array.isArray(hiringLearning.profile_signals) ? hiringLearning.profile_signals : [];
     learningProfiles.slice(0,5).forEach(signal => {{
       const valueText=signal.hired_average !== undefined ? `平均年限 ${{signal.hired_average}} vs ${{signal.comparison_average}}` : `录用 ${{signal.hired_rate || 0}}% vs 其他 ${{signal.comparison_rate || 0}}%`;
       appendTextRow(recruitingInsightsList,`画像信号 · ${{signal.signal || '未命名'}}`,`${{signal.label || signal.field}} · ${{valueText}}`);
     }});
     if(hiringLearning.notice) {{ const notice=document.createElement('div'); notice.className='notice warn insight-sample-notice'; notice.textContent=hiringLearning.notice; recruitingInsightsList.append(notice); }}
   }}
   const suggestions=optimization.suggestions || [];
   const drafts=Array.isArray(data.optimization_drafts) ? data.optimization_drafts : [];
   const draftBySuggestion=new Map(drafts.map(item => [item.suggestion_id,item]));
   const jobId=data.selected_job_id || selectedRecruitingJobId();
   const reviewDraft=async (draft,status,note,stateNode) => {{
     stateNode.textContent='正在保存草稿状态…';
     const payload=await post('/api/recruiting/optimization-drafts/'+encodeURIComponent(draft.draft_id),{{status:status,note:note || ''}});
     if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '草稿状态保存失败'; return; }}
     stateNode.textContent=status==='accepted'?'已采纳；请按建议手动更新岗位或知识库':(status==='ignored'?'已忽略（未修改配置）':'已重新打开待审核');
     await refresh();
   }};
   const appendDraftControls=(row,draft,suggestion) => {{
     const actions=document.createElement('div'); actions.className='actions';
     const stateNode=document.createElement('span'); stateNode.className='hint'; stateNode.setAttribute('aria-live','polite');
     if(!draft) {{
       const create=document.createElement('button'); create.type='button'; create.className='secondary copy-button'; create.textContent='生成改进草稿';
       create.addEventListener('click',async () => {{
         create.disabled=true; stateNode.textContent='正在保存待审核草稿…';
         const payload=await post('/api/recruiting/optimization-drafts',{{job_id:jobId,suggestion_id:suggestion.suggestion_id}});
         if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '改进草稿保存失败'; create.disabled=false; return; }}
         stateNode.textContent='已生成待审核草稿'; await refresh();
       }});
       actions.append(create);
     }} else {{
       appendTextRow(row,'草稿状态',`${{draft.status_label || draft.status || '待审核'}}${{draft.review_note ? ' · '+draft.review_note : ''}}`);
       const note=document.createElement('input'); note.placeholder='审核备注（可选）'; note.maxLength=1000; note.setAttribute('aria-label','改进草稿审核备注');
       if(draft.status==='pending_review') {{
         const accept=document.createElement('button'); accept.type='button'; accept.className='secondary copy-button'; accept.textContent='标记已采纳'; accept.addEventListener('click',() => reviewDraft(draft,'accepted',note.value,stateNode));
         const ignore=document.createElement('button'); ignore.type='button'; ignore.className='secondary copy-button'; ignore.textContent='标记已忽略'; ignore.addEventListener('click',() => reviewDraft(draft,'ignored',note.value,stateNode));
         actions.append(note,accept,ignore);
       }} else {{
         const reopen=document.createElement('button'); reopen.type='button'; reopen.className='secondary copy-button'; reopen.textContent='重新打开审核'; reopen.addEventListener('click',() => reviewDraft(draft,'pending_review',note.value,stateNode));
         actions.append(note,reopen);
       }}
     }}
     actions.append(stateNode); row.append(actions);
   }};
   if(!suggestions.length && !drafts.length) {{ appendTextRow(recruitingInsightsList,'建议状态','当前没有需要特别复核的模式。'); }}
   suggestions.forEach(item => {{ const row=document.createElement('div'); row.className='workspace-row review-banner'; const title=document.createElement('strong'); title.textContent=`${{item.title || '复盘建议'}} · ${{item.severity || '提示'}}`; const reason=document.createElement('div'); reason.className='workspace-meta'; reason.textContent=item.reason || ''; const action=document.createElement('div'); action.className='workspace-meta'; action.textContent=`建议动作：${{item.action || '人工复核'}}`; row.append(title,reason,action); appendDraftControls(row,draftBySuggestion.get(item.suggestion_id),item); recruitingInsightsList.append(row); }});
   const currentSuggestionIds=new Set(suggestions.map(item => item.suggestion_id));
   drafts.filter(item => !currentSuggestionIds.has(item.suggestion_id)).forEach(draft => {{ const row=document.createElement('div'); row.className='workspace-row'; const title=document.createElement('strong'); title.textContent=`历史改进草稿 · ${{draft.title || '未命名建议'}}`; const reason=document.createElement('div'); reason.className='workspace-meta'; reason.textContent=draft.reason || ''; row.append(title,reason); appendDraftControls(row,draft,draft); recruitingInsightsList.append(row); }});
  // 明确读取该字段，防止未来前端误把建议当作自动变更指令。
  const mutations=Array.isArray(optimization.mutations) ? optimization.mutations : [];
  if(mutations.length) appendTextRow(recruitingInsightsList,'自动变更',`本地工作台禁止自动修改（${{mutations.length}}）`);
}}
function createStageSelect(selected) {{ const select=document.createElement('select'); select.setAttribute('aria-label','候选人阶段'); candidateStages.filter(([stage]) => !terminalStages.has(stage) || stage===selected).forEach(([stage,label]) => {{ const option=document.createElement('option'); option.value=stage; option.textContent=label; if(stage===selected) option.selected=true; select.append(option); }}); select.disabled=terminalStages.has(selected); return select; }}
async function submitCandidateStage(candidate, stageSelect, actionInput, noteInput, judgmentInput, quoteInput, stateNode) {{
  const action=actionInput.value.trim();
  if(!action) {{ stateNode.textContent='请填写阶段动作'; return; }}
  stateNode.textContent='正在保存阶段记录…';
  const payload=await post('/api/recruiting/candidates/'+encodeURIComponent(candidate.candidate_id)+'/stage',{{job_id:selectedRecruitingJobId(),stage:stageSelect.value,action:action,note:noteInput.value,ai_judgment:judgmentInput.value,candidate_quote:quoteInput.value}});
  if(!payload.ok) {{ stateNode.textContent=(payload.error && payload.error.message) || '阶段记录保存失败'; return; }}
  stateNode.textContent='阶段记录已保存';
  await refresh();
}}
function renderRecruitingAssessment(reports) {{
  recruitingAssessmentResult.replaceChildren();
  if (!reports || !reports.length) {{ recruitingAssessmentResult.textContent='暂无评估结果。'; return; }}
  reports.slice().reverse().forEach(report => {{
    const row=document.createElement('div'); row.className='workspace-row review-banner';
    const heading=document.createElement('strong'); heading.textContent=`${{report.candidate_name || '候选人'}} · ${{report.final_score}} 分 · ${{report.level || '待确认'}}`;
    const decision=document.createElement('div'); decision.className='workspace-meta'; decision.textContent=`结论：${{report.decision || '待人工确认'}}；下一步：${{report.next_action || '人工复核'}}；来源：${{report.engine === 'ai' ? 'AI' : '本地规则'}}；状态：${{report.review_required === false ? '已人工确认' : '待人工确认'}}`;
    row.append(heading,decision);
    const screening=report.screening || {{}}; const profile=screening.profile || {{}}; const hard=screening.hard_filter || {{}}; const semantic=screening.semantic_match || {{}}; const risk=screening.risk || {{}}; const qa=screening.professional_qa || {{}};
    appendTextRow(row,'三层初筛结论',`${{screening.decision || '待人工确认'}} · ${{screening.next_action || '人工复核'}}`);
    appendTextRow(row,'候选人画像',[profile.city ? `城市：${{profile.city}}` : '',profile.expected_salary ? `期望薪资：${{profile.expected_salary}}` : '',profile.education ? `学历：${{profile.education}}` : '',profile.experience_years === null || profile.experience_years === undefined ? '' : `经验：${{profile.experience_years}} 年`,profile.recent_role ? `最近职位：${{profile.recent_role}}` : '',profile.skills && profile.skills.length ? `技能：${{profile.skills.join('、')}}` : ''].filter(Boolean).join(' · '));
    appendTextRow(row,'硬条件筛选',`状态：${{hard.status || '待确认'}} · 不匹配：${{(hard.mismatches || []).join('；') || '无'}} · 缺失：${{(hard.unknowns || []).join('；') || '无'}}`);
    appendTextRow(row,'语义匹配',`得分：${{semantic.score === undefined ? '待确认' : semantic.score}} · 命中：${{(semantic.matched || []).join('、') || '无'}} · 缺失：${{(semantic.missing || []).join('、') || '无'}}`);
    appendTextRow(row,'风险识别（风险信号）',`${{risk.level || 'low'}} · ${{risk.summary || '未发现明显风险'}}`);
    appendTextRow(row,'匹配点', (report.matched_points || []).join('、'));
    appendTextRow(row,'风险点', (report.risk_points || []).join('、'));
    appendTextRow(row,'证据', (report.evidence || []).join('；'));
    // AI 语义层只展示已通过原文核对的内容；没有 AI 结果时明确说明降级，
    // 避免 HR 把纯规则评分误解为模型结论。
    const aiReview=(report.ai_review && typeof report.ai_review==='object') ? report.ai_review : {{}};
    const aiSection=document.createElement('section'); aiSection.className='score-breakdown'; const aiHeading=document.createElement('div'); aiHeading.className='funnel-metrics-heading'; aiHeading.textContent='AI 语义分析'; aiSection.append(aiHeading);
    const appendAiDetail=(label,value) => {{ const detail=document.createElement('div'); detail.className='assessment-detail-row'; const title=document.createElement('strong'); title.textContent=label; const text=document.createElement('span'); text.textContent=value; detail.append(title,text); aiSection.append(detail); }};
    if(aiReview.engine==='ai_semantic') {{
      appendAiDetail('AI 摘要',aiReview.summary || 'AI 未提供可采信摘要。');
      const hits=Array.isArray(aiReview.semantic_hits) ? aiReview.semantic_hits : []; appendAiDetail('AI 已核对命中',hits.length ? hits.map(item => `${{item.criterion || '岗位条件'}} ←「${{item.quote || '无引用'}}」`).join('；') : '未发现可由简历原文核对的语义命中。');
      const findings=Array.isArray(aiReview.risk_findings) ? aiReview.risk_findings : []; appendAiDetail('AI 风险引用',findings.length ? findings.map(item => `${{item.message || item.code || '风险提示'}} ←「${{item.quote || '无引用'}}」`).join('；') : '未发现需要人工核对的风险引用。');
      const followUps=Array.isArray(aiReview.follow_up_questions) ? aiReview.follow_up_questions : []; appendAiDetail('AI 待追问',followUps.length ? followUps.join('；') : '当前没有额外追问建议。');
      const rejected=Array.isArray(aiReview.rejected_claims) ? aiReview.rejected_claims : []; if(rejected.length) appendAiDetail('未采信的 AI 断言',rejected.join('；'));
    }} else {{
      appendAiDetail('AI 分析状态','当前报告使用本地规则与已记录问答；配置 AI 语义评审后，系统会在此展示经过原文核对的匹配、风险引用和追问。');
    }}
    row.append(aiSection);
    const breakdown=document.createElement('div'); breakdown.className='score-breakdown'; const breakdownHeading=document.createElement('div'); breakdownHeading.className='funnel-metrics-heading'; breakdownHeading.textContent='带权证据评分'; breakdown.append(breakdownHeading); Object.entries(report.score_breakdown || {{}}).forEach(([key,value]) => {{ const item=document.createElement('div'); item.className='score-breakdown-row'; const labels={{hard_match:'硬条件',experience:'工作年限',professional_qa:'专业问答',communication:'沟通表现',stability:'稳定性',location_salary:'地点与薪资'}}; const label=document.createElement('strong'); label.textContent=labels[key] || key; const score=document.createElement('span'); score.textContent=`得分 ${{value.score}} · 权重 ${{value.weight}}`; const weighted=document.createElement('span'); weighted.textContent=`贡献 ${{value.weighted_score}}`; item.append(label,score,weighted); breakdown.append(item); }}); if(Object.keys(report.score_breakdown || {{}}).length) row.append(breakdown);
    const qaHeading=document.createElement('div'); qaHeading.className='workspace-meta'; qaHeading.textContent=`专业问答：已记录 ${{report.answer_count || 0}} 条${{report.professional_qa_score === null || report.professional_qa_score === undefined ? '' : ' · QA '+report.professional_qa_score+' 分'}}`; row.append(qaHeading);
    const qaBreakdown=report.professional_qa_breakdown || []; if(qaBreakdown.length) appendTextRow(row,'逐题评分',qaBreakdown.map(item => `${{item.question_id || '未标识问题'}}：${{item.score}} 分 · ${{item.status==='pass' ? '通过' : '需要追问'}}`).join('；'));
    if ((qa.follow_up_questions || []).length) appendTextRow(row,'follow_up_questions',(qa.follow_up_questions || []).join('；'));
    const questionList=document.createElement('div'); questionList.className='workspace-list'; const questionItems=(report.professional_question_items || []).length ? report.professional_question_items : (report.professional_questions || []).map(question => ({{question:question,question_id:'',question_version:'v1',source_ids:[],source_titles:[],follow_up_questions:[]}})); questionItems.forEach((item,index) => {{ const question=item.question || ''; const questionRow=document.createElement('div'); questionRow.className='workspace-row'; const questionText=document.createElement('div'); questionText.className='workspace-meta'; questionText.textContent=`${{index+1}}. ${{question}}`; const citation=document.createElement('div'); citation.className='workspace-meta'; citation.textContent=[item.question_version ? `问题版本 ${{item.question_version}}` : '',(item.source_titles || []).length ? `来源：${{item.source_titles.join('、')}}` : '来源：岗位标准'].filter(Boolean).join(' · '); const followUps=document.createElement('div'); followUps.className='workspace-meta'; if((item.follow_up_questions || []).length) followUps.textContent='可选追问：'+item.follow_up_questions.join('；'); const answerInput=document.createElement('textarea'); answerInput.placeholder='记录候选人的回答（仅本地保存）'; answerInput.maxLength=8000; answerInput.setAttribute('aria-label',`第 ${{index+1}} 条专业问题回答`); const answerActions=document.createElement('div'); answerActions.className='candidate-actions'; const answerButton=document.createElement('button'); answerButton.type='button'; answerButton.className='secondary copy-button'; answerButton.textContent='记录回答'; const answerState=document.createElement('span'); answerState.className='hint'; answerButton.addEventListener('click',async () => {{ const answer=answerInput.value.trim(); if(!answer) {{ answerState.textContent='请先填写回答'; return; }} answerState.textContent='正在保存回答…'; const payload=await post('/api/recruiting/answers',{{job_id:report.job_id,candidate_id:report.candidate_id,question:question,answer:answer,question_id:item.question_id || '',question_version:item.question_version || 'v1',source_ids:item.source_ids || []}}); if(!payload.ok) {{ answerState.textContent=(payload.error && payload.error.message) || '回答保存失败'; return; }} answerState.textContent='回答已保存，重新生成评估后计入 QA 分数'; await refresh(); }}); answerActions.append(answerButton,answerState); questionRow.append(questionText,citation); if((item.follow_up_questions || []).length) questionRow.append(followUps); questionRow.append(answerInput,answerActions); questionList.append(questionRow); }}); row.append(questionList);
    if ((report.professional_qa_evidence || []).length) appendTextRow(row,'问答证据',(report.professional_qa_evidence || []).join('；'));
    if(report.review_required !== false) {{
      const reviewActions=document.createElement('div'); reviewActions.className='actions'; const note=document.createElement('input'); note.placeholder='人工备注（可选）'; note.maxLength=1000; const reviewState=document.createElement('span'); reviewState.className='hint'; reviewActions.append(note); [['proceed','确认继续沟通'],['follow_up','需要补充信息'],['reject','暂不推进']].forEach(([outcome,label]) => {{ const button=document.createElement('button'); button.type='button'; button.className='secondary copy-button'; button.textContent=label; button.addEventListener('click',() => submitAssessmentReview(report,outcome,note.value,reviewState)); reviewActions.append(button); }}); reviewActions.append(reviewState); row.append(reviewActions);
    }} else {{
      appendTextRow(row,'人工确认','评估已人工确认，请先重新生成评估');
    }}
     Object.entries(report.message_templates || {{}}).forEach(([key,value]) => {{ const action=document.createElement('div'); action.className='actions'; const text=document.createElement('div'); text.className='workspace-meta'; text.textContent=value; const button=document.createElement('button'); button.type='button'; button.className='secondary copy-button'; button.textContent='复制话术'; const used=document.createElement('button'); used.type='button'; used.className='secondary copy-button'; used.textContent='标记已使用'; const state=document.createElement('span'); state.className='hint'; button.addEventListener('click',() => copyDraft(value,state)); used.addEventListener('click',async () => {{ used.disabled=true; state.textContent='正在记录人工使用…'; const payload=await post('/api/recruiting/message-usage',{{job_id:report.job_id,candidate_id:report.candidate_id,template_key:key,template_version:'v1',note:'HR 已复制并在官方页面人工使用'}}); if(!payload.ok) {{ state.textContent=(payload.error && payload.error.message) || '记录失败'; used.disabled=false; return; }} state.textContent='已记录人工使用（需人工操作）'; }}); action.append(text,button,used,state); row.append(action); }});
    recruitingAssessmentResult.append(row);
  }});
  // 问答功能保留供后续阶段使用，但不应挤占首次判断“是否匹配”的阅读空间。
  recruitingAssessmentResult.querySelectorAll('.candidate-assessment-detail > .workspace-list').forEach(list => {{
    const details=document.createElement('details');
    details.className='candidate-follow-up';
    const summary=document.createElement('summary');
    summary.textContent='后续专业问答与追问（本轮默认收起）';
    list.replaceWith(details);
    details.append(summary,list);
  }});
}}
function renderRecruiting(data) {{
  if (!data) return;
  const previousJob=selectedRecruitingJobId();
  const jobs=data.jobs || [];
  [recruitingJobSelect,recruitingAssessJob].forEach(select => {{ select.replaceChildren(); jobs.forEach(job => {{ const option=document.createElement('option'); option.value=job.job_id; option.textContent=[job.name,job.city].filter(Boolean).join(' · '); select.append(option); }}); if (!jobs.length) {{ const empty=document.createElement('option'); empty.value=''; empty.textContent='请先创建岗位'; select.append(empty); }} }});
  const selected=data.selected_job_id || previousJob || (jobs[0] && jobs[0].job_id) || '';
  recruitingJobSelect.value=selected; recruitingAssessJob.value=selected;
  // 工作区接口按当前岗位返回候选人；将已读取过的数量保存在前端，
  // 这样回到岗位总览时不会把其他岗位误显示成“0 位候选人”。
  if(selected && Array.isArray(data.candidates)) recruitingJobCandidateCounts.set(String(selected),data.candidates.length);
  renderRecruitingJobCards(data);
  const job=jobs.find(item => item.job_id===selected) || jobs[0];
  if (!recruitingCreatingNewJob && job && !recruitingEditingJobId) recruitingEditingJobId=job.job_id;
  if (!recruitingCreatingNewJob && job && recruitingEditingJobId===job.job_id) fillRecruitingJobForm(job);
  const jobStatus=job ? (job.status || 'published') : '';
  const jobReadiness=(job && job.readiness) || {{}};
  recruitingJobStatus.className='notice '+(jobStatus==='published'?'success':(jobStatus==='archived'?'warn':''));
  recruitingJobStatus.textContent=job ? `\u5c97\u4f4d\u72b6\u6001\uff1a${{job.status_label || jobStatus}}${{jobStatus==='draft' ? ' \u00b7 \u8865\u9f50\u5fc5\u7b54\u9879\u540e\u70b9\u51fb\u53d1\u5e03' : ''}}` : '\u8bf7\u5148\u521b\u5efa\u5c97\u4f4d';
  recruitingPublishJobButton.disabled=!(job && jobStatus==='draft' && jobReadiness.ready===true);
  if(job) recruitingJobStatus.textContent += ` · 专业问答：${{job.professional_qa_enabled === false ? '关闭（私域人工承接）' : '启用（BOSS 问答）'}}`;
  if (recruitingAssessButton) recruitingAssessButton.disabled=!(job && jobStatus==='published');
  recruitingCriteriaPreview.replaceChildren();
  if (job) {{ const criteria=job.criteria || {{}}; [['硬条件',criteria.must_have],['加分项',criteria.nice_to_have],['淘汰项',criteria.reject_if],['风险项',criteria.risk_signals],['学历要求',job.education_requirement],['最低工作年限',job.min_experience_years === null || job.min_experience_years === undefined ? '' : `${{job.min_experience_years}} 年`],['行业要求',job.industry],['技能要求',job.skills]].forEach(([label,items]) => appendTextRow(recruitingCriteriaPreview,label,Array.isArray(items) ? items.join('、') : String(items || ''))); }}
  const readiness=(job && job.readiness) || {{}};
  if (job && readiness.ready === false) {{ appendTextRow(recruitingCriteriaPreview,'岗位标准还缺',(readiness.missing_required_fields || []).join('、') || '必答项'); appendTextRow(recruitingCriteriaPreview,'clarification_questions',(readiness.clarification_questions || []).join('；')); }}
  else if (job) {{ appendTextRow(recruitingCriteriaPreview,'岗位标准状态',readiness.summary || '已具备筛选所需的必答项'); }}
  const warnings=(job && job.warnings) || data.warnings || [];
  recruitingJobWarnings.hidden=!warnings.length; recruitingJobWarnings.textContent=warnings.join('；');
  recruitingKnowledgeList.replaceChildren(); (data.knowledge || []).forEach(item => {{ const row=document.createElement('div'); row.className='workspace-row'; const title=document.createElement('strong'); title.textContent=item.title; const content=document.createElement('div'); content.className='workspace-meta'; content.textContent=item.content || '（无正文）'; const source=document.createElement('div'); source.className='workspace-meta'; source.textContent=[item.audience && `知识范围：${{knowledgeAudienceLabels[item.audience] || item.audience}}`,item.source_type && `来源类型：${{item.source_type}}`,item.source_path && `文件：${{item.source_path}}`,item.source_sha256 && `哈希：${{item.source_sha256.slice(0,12)}}…`].filter(Boolean).join(' · ') || '来源：手工录入'; row.append(title,content,source); recruitingKnowledgeList.append(row); }}); if (!data.knowledge || !data.knowledge.length) recruitingKnowledgeList.textContent='当前岗位暂无知识库内容。';
  recruitingFaqList.replaceChildren(); (data.faq || []).forEach(item => appendTextRow(recruitingFaqList,item.question,item.answer)); if (!data.faq || !data.faq.length) recruitingFaqList.textContent='当前岗位暂无 FAQ。';
  renderRecruitingWorkflow(data.workflow || {{}});
  renderLoopSummary(data);
  renderRecruitingCandidateQueue(data.workflow || {{}});
	  renderRejectionReasonStatistics(data.rejection_reason_statistics || {{}});
	  renderRecruitingScoreGroups(data.score_groups || []);
  renderRecruitingPipeline(data.pipeline || {{}});
  renderRecruitingTasks(data);
  renderRecruitingActivities(data);
  renderRecruitingInsights(data);
  recruitingCandidateList.replaceChildren(); const candidates=data.candidates || []; candidates.forEach(candidate => {{
    const row=document.createElement('div'); row.className='workspace-row'; row.setAttribute('data-candidate-id',candidate.candidate_id || '');
    const header=document.createElement('div'); header.className='candidate-actions'; const heading=document.createElement('strong'); heading.textContent=candidate.name; const stage=document.createElement('span'); stage.className='stage-pill'; stage.textContent=candidate.stage_label || '待筛选'; header.append(heading,stage);
    const nextAction=candidate.next_action || (candidate.pending_task_title || (terminalStages.has(candidate.stage) ? '流程已完成' : '请检查已跳过的待办或记录下一步'));
    const meta=document.createElement('div'); meta.className='workspace-meta'; meta.textContent=[candidate.source === 'boss_conversation' ? '来源：沟通候选人' : (candidate.source === 'boss_recommendation' ? '来源：推荐牛人' : '来源：本地导入'),`最近动作：${{candidate.last_action || '导入候选人'}}`,`下一步：${{nextAction}}`,candidate.next_follow_up_at ? `下次跟进：${{candidate.next_follow_up_at}}` : '',`待办 ${{candidate.pending_task_count || 0}} 项`,`沟通 ${{candidate.communication_count || 0}} 轮`,`记录 ${{candidate.event_count || 0}} 次`].filter(Boolean).join(' · ');
    const profile=candidate.profile || {{}}; const profileRow=document.createElement('div'); profileRow.className='workspace-meta candidate-profile'; const profileValues=[profile.city ? `城市：${{profile.city}}` : '',profile.expected_salary ? `期望薪资：${{profile.expected_salary}}` : '',profile.education ? `学历：${{profile.education}}` : '',profile.experience_years === null || profile.experience_years === undefined ? '' : `经验：${{profile.experience_years}} 年`,profile.recent_role ? `最近职位：${{profile.recent_role}}` : '',profile.industry ? `行业：${{profile.industry}}` : '',Array.isArray(profile.skills) && profile.skills.length ? `技能：${{profile.skills.join('、')}}` : ''].filter(Boolean); profileRow.textContent=`候选人画像：${{profileValues.join(' · ') || '待提取'}}${{Array.isArray(profile.missing_fields) && profile.missing_fields.length ? ` · 缺少字段：${{profile.missing_fields.join('、')}}` : ''}}`;
    const selectButton=document.createElement('button'); selectButton.type='button'; selectButton.className='secondary copy-button'; selectButton.textContent=terminalStages.has(candidate.stage)?'流程已完成':'填入评估'; selectButton.disabled=terminalStages.has(candidate.stage); selectButton.title=terminalStages.has(candidate.stage)?'终局候选人不可重新评估':''; selectButton.addEventListener('click',() => {{ recruitingSelectedCandidateId=candidate.candidate_id; recruitingAssessCandidate.value=candidate.candidate_id; recruitingAssessJob.value=selected; updateRecruitingAssessmentAvailability(); }});
    const details=document.createElement('details'); details.className='stage-details'; const summary=document.createElement('summary'); summary.textContent='记录阶段'; const fields=document.createElement('div'); fields.className='stage-fields'; const stageSelect=createStageSelect(candidate.stage); const actionInput=document.createElement('input'); actionInput.value=candidate.last_action || '人工阶段记录'; actionInput.maxLength=160; actionInput.setAttribute('aria-label','阶段动作'); const noteInput=document.createElement('input'); noteInput.placeholder='人工备注（可选）'; noteInput.maxLength=1000; noteInput.setAttribute('aria-label','阶段备注'); const judgmentInput=document.createElement('input'); judgmentInput.placeholder='AI 判断或证据摘要（可选）'; judgmentInput.maxLength=1000; judgmentInput.setAttribute('aria-label','AI 判断'); const quoteInput=document.createElement('input'); quoteInput.placeholder='候选人原话（可选，仅本地审计）'; quoteInput.maxLength=2000; quoteInput.setAttribute('aria-label','候选人原话'); const saveButton=document.createElement('button'); saveButton.type='button'; saveButton.textContent='保存阶段'; saveButton.disabled=terminalStages.has(candidate.stage); const saveState=document.createElement('span'); saveState.className='hint'; saveState.textContent=saveButton.disabled?'终局候选人请使用终局待办':''; saveButton.addEventListener('click',() => submitCandidateStage(candidate,stageSelect,actionInput,noteInput,judgmentInput,quoteInput,saveState)); fields.append(stageSelect,actionInput,noteInput,judgmentInput,quoteInput,saveButton,saveState); details.append(summary,fields);
    const timeline=document.createElement('details'); timeline.className='stage-details'; const timelineSummary=document.createElement('summary'); timelineSummary.textContent='查看本地记录'; const timelineList=document.createElement('div'); timelineList.className='candidate-timeline'; (candidate.timeline || []).slice().reverse().forEach(event => {{ const item=document.createElement('span'); item.textContent=[event.created_at,event.stage_label,event.action,event.note,event.ai_judgment].filter(Boolean).join(' · '); timelineList.append(item); }}); if(!(candidate.timeline || []).length) {{ const empty=document.createElement('span'); empty.textContent='暂无阶段记录'; timelineList.append(empty); }} timeline.append(timelineSummary,timelineList);
    const communicationTimeline=document.createElement('details'); communicationTimeline.className='stage-details'; const communicationSummary=document.createElement('summary'); communicationSummary.textContent=`沟通时间线（${{candidate.communication_count || 0}} 轮）`; const communicationList=document.createElement('div'); communicationList.className='communication-timeline'; (candidate.communication_timeline || []).slice().reverse().forEach(item => {{ const entry=document.createElement('div'); entry.className='communication-row'; const title=document.createElement('strong'); title.textContent=[item.round_label,item.outcome_label].filter(Boolean).join(' · '); const detail=document.createElement('span'); detail.textContent=[item.created_at,item.candidate_reply_summary,item.next_follow_up_at ? '下次跟进 '+item.next_follow_up_at : '',item.note].filter(Boolean).join(' · '); entry.append(title,detail); communicationList.append(entry); }}); if(!(candidate.communication_timeline || []).length) {{ const empty=document.createElement('span'); empty.textContent='暂无沟通记录'; communicationList.append(empty); }} communicationTimeline.append(communicationSummary,communicationList);
    const mismatchBlock=document.createElement('div'); appendMismatchFeedback(mismatchBlock,candidate,selected); row.append(header,meta,profileRow,selectButton,details,timeline,communicationTimeline,mismatchBlock); recruitingCandidateList.append(row);
  }}); if (!candidates.length) recruitingCandidateList.textContent='先导入一份本地 Markdown/TXT 简历。';
  recruitingAssessCandidate.replaceChildren(); candidates.forEach(candidate => {{ const option=document.createElement('option'); option.value=candidate.candidate_id; option.textContent=candidate.name; recruitingAssessCandidate.append(option); }}); if (!candidates.length) {{ const empty=document.createElement('option'); empty.value=''; empty.textContent='请先导入候选人'; recruitingAssessCandidate.append(empty); }}
  // 先恢复用户正在查看的候选人；候选人已离开当前岗位时才回退到第一人。
  if (recruitingSelectedCandidateId && candidates.some(candidate => candidate.candidate_id===recruitingSelectedCandidateId)) recruitingAssessCandidate.value=recruitingSelectedCandidateId;
  if (!recruitingAssessCandidate.value && candidates.length) recruitingAssessCandidate.value=candidates[0].candidate_id;
  // 导出结果可能先于工作区快照返回；等候选人 option 真正挂载后再恢复选择，
  // 并把当前岗位一起写回评估表，避免只显示“已导入”却仍评估旧候选人。
  if (pendingImportedCandidateId) {{
    const imported=candidates.find(candidate => candidate.candidate_id===pendingImportedCandidateId);
    if(imported) {{
      recruitingSelectedCandidateId=imported.candidate_id;
      recruitingAssessCandidate.value=imported.candidate_id;
      recruitingAssessJob.value=selected;
      pendingImportedCandidateId=null;
    }}
  }}
  if (pendingImportedResumePath) {{ const imported=candidates.find(candidate => candidate.resume_path===pendingImportedResumePath); if(imported) {{ recruitingAssessCandidate.value=imported.candidate_id; recruitingSelectedCandidateId=imported.candidate_id; recruitingAssessJob.value=selected; pendingImportedResumePath=null; }} }}
  updateRecruitingAssessmentAvailability();
  renderRecruitingAssessment(data.assessments || []);
}}
async function refreshRecruiting() {{ try {{ const job=selectedRecruitingJobId(); const suffix=job ? '?job_id='+encodeURIComponent(job) : ''; const response=await fetch('/api/recruiting/workspace'+suffix); const payload=await response.json(); if(payload.ok) {{ recruitingWorkspace=payload.data; renderRecruiting(recruitingWorkspace); }} else {{ recruitingActionState.textContent=(payload.error && payload.error.message) || '招聘工作区读取失败'; recruitingActionState.className='notice error'; }} }} catch (_) {{ recruitingActionState.textContent='无法读取招聘工作区，请检查本地控制台是否仍在运行'; recruitingActionState.className='notice error'; }} }}
function renderResult(result) {{ if (!result) {{ resultBox.hidden=true; return; }} resultBox.hidden=false; resultBox.className='result'; if (result.state === 'succeeded') {{ const r=result.result; resultBox.innerHTML='<h2>最近下载</h2><dl><dt>候选人</dt><dd></dd><dt>文件</dt><dd></dd><dt>路径</dt><dd></dd><dt>字节数</dt><dd></dd><dt>段落</dt><dd></dd></dl>'; const values=[r.candidate_name||'（无）',r.filename,r.path,String(r.bytes_written),r.sections.join('、')||'（无）']; resultBox.querySelectorAll('dd').forEach((node,index)=>node.textContent=values[index]); renderWorkspaceImport(resultBox,r,r.path,'boss_conversation'); }} else if (result.error) {{ resultBox.textContent=result.error.message; resultBox.className='result notice error'; }} }}
function attachmentState(status) {{ return {{downloaded:'已下载',absent:'未发现',unavailable:'暂不可用',failed:'下载失败'}}[status] || '未知'; }}
function renderConversationResult(result) {{ if (!result) {{ conversationResultBox.hidden=true; return; }} conversationResultBox.hidden=false; conversationResultBox.className='result'; if (result.state === 'succeeded') {{ const r=result.result; const a=r.attachment || {{}}; conversationResultBox.innerHTML='<h2>沟通候选人附件简历</h2><dl><dt>候选人</dt><dd></dd><dt>附件状态</dt><dd></dd><dt>附件文件</dt><dd></dd><dt>附件路径</dt><dd></dd></dl>'; const values=[r.candidate_name||'（无）',attachmentState(a.status),a.filename||'（无）',a.path||'（无）']; conversationResultBox.querySelectorAll('dd').forEach((node,index)=>node.textContent=values[index]); if(a.status==='downloaded'&&a.path) renderWorkspaceImport(conversationResultBox,r,a.path,'boss_conversation_attachment'); }} else if (result.error) {{ conversationResultBox.textContent=result.error.message; conversationResultBox.className='result notice error'; }} }}
function renderRecommendationResult(result) {{ if (!result) {{ recommendationDownloadResultBox.hidden=true; return; }} recommendationDownloadResultBox.hidden=false; recommendationDownloadResultBox.className='result'; if (result.state === 'succeeded') {{ const r=result.result; const a=r.attachment || {{}}; recommendationDownloadResultBox.innerHTML='<h2>推荐候选人附件简历</h2><dl><dt>候选人</dt><dd></dd><dt>附件状态</dt><dd></dd><dt>附件文件</dt><dd></dd><dt>附件路径</dt><dd></dd></dl>'; const values=[r.candidate_name||'（无）',attachmentState(a.status),a.filename||'（无）',a.path||'（无）']; recommendationDownloadResultBox.querySelectorAll('dd').forEach((node,index)=>node.textContent=values[index]); if(a.status==='downloaded'&&a.path) renderWorkspaceImport(recommendationDownloadResultBox,r,a.path,'boss_recommendation_attachment'); }} else if (result.error) {{ recommendationDownloadResultBox.textContent=result.error.message; recommendationDownloadResultBox.className='result notice error'; }} }}
const batchExportForm = document.querySelector('#batch-export-form');
const batchExportSource = document.querySelector('#batch-export-source');
const batchExportLimit = document.querySelector('#batch-export-limit');
const batchExportJobId = document.querySelector('#batch-export-job-id');
const batchExportOutputDir = document.querySelector('#batch-export-output-dir');
const batchExportStart = document.querySelector('#batch-export-start');
const batchExportScan = document.querySelector('#batch-export-scan');
const batchExportStop = document.querySelector('#batch-export-stop');
const batchExportActionState = document.querySelector('#batch-export-action-state');
const batchExportStateChip = document.querySelector('#batch-export-state');
const batchExportSummary = document.querySelector('#batch-export-summary');
const batchExportBar = document.querySelector('#batch-export-bar');
const batchExportMeta = document.querySelector('#batch-export-meta');
const batchExportNotice = document.querySelector('#batch-export-notice');
const batchExportResults = document.querySelector('#batch-export-results');
const batchExportImportAll = document.querySelector('#batch-export-import-all');
const batchExportImportState = document.querySelector('#batch-export-import-state');
const pipelineStart = document.querySelector('#pipeline-start');
const pipelineStop = document.querySelector('#pipeline-stop');
const pipelineLimit = document.querySelector('#pipeline-limit');
const pipelineThreshold = document.querySelector('#pipeline-threshold');
const pipelineAskResume = document.querySelector('#pipeline-ask-resume');
const pipelineActionState = document.querySelector('#pipeline-action-state');
const pipelineStateChip = document.querySelector('#pipeline-state-chip');
const pipelineSummary = document.querySelector('#pipeline-summary');
const pipelineProcessed = document.querySelector('#pipeline-processed');
const pipelineAsked = document.querySelector('#pipeline-asked');
const pipelineOnline = document.querySelector('#pipeline-online');
const pipelineAttach = document.querySelector('#pipeline-attach');
const pipelinePool = document.querySelector('#pipeline-pool');
const pipelineFailedCount = document.querySelector('#pipeline-failed-count');
const pipelineNotice = document.querySelector('#pipeline-notice');
const pipelineLogViewer = document.querySelector('#pipeline-log-viewer');
const pipelineResults = document.querySelector('#pipeline-results');
// 附件徽标是"已核对过的事实"，因此三种状态必须能区分开：确认有、确认没有、
// 还没查过。没查过绝不能显示成"无附件"，否则用户会以为候选人没交简历。
const attachmentBadgeLabels = {{can_export_pdf:['可导 PDF','can'],downloaded:['PDF 已下载','can'],no_attachment:['无附件','none'],absent:['无附件','none'],unavailable:['附件暂不可用','none'],failed:['附件读取失败','failed'],not_checked:['未检测','unknown']}};
const batchStopReasonLabels = {{stopped_by_user:'已按你的请求停止，剩余候选人未处理',daily_quota:'已达到今日安全额度上限，请明天继续或调高 automation.daily_action_quota',cooldown:'动作冷却中，请稍后继续剩余候选人',startup_jitter:'今日启动窗口尚未到达，请稍后重试',off_hours:'当前处于非工作时段，已暂停剩余候选人',login_expired:'BOSS 登录已失效，请重新登录后继续剩余候选人',repeated_failure:'连续失败已触发熔断，请检查登录态、网络和导出目录后重试',no_target:'平台没有返回可处理的候选人。如果官方页面能看到人，通常是登录态失效——请先在“登录状态”页重新登录，再回来重试'}};
let batchExportImportItems = [];
function appendAttachmentBadge(parent, status) {{
  const [label,variant]=attachmentBadgeLabels[status] || attachmentBadgeLabels.not_checked;
  const badge=document.createElement('span'); badge.className='pdf-badge '+variant; badge.textContent=label; parent.append(badge); return badge;
}}
function renderPipeline(data) {{
  if (!pipelineStateChip) return;
  const state = (data && data.state) || 'idle';
  const running = state === 'running';
  const stateLabels = {{ idle: '未开始', running: '运行中', succeeded: '已完成', stopped: '已停止', failed: '失败', blocked: '被阻止' }};
  pipelineStateChip.textContent = stateLabels[state] || state;
  pipelineStateChip.className = 'pacing-state' + (state === 'failed' || state === 'blocked' ? ' unavailable' : (running ? ' paused' : ''));
  if (pipelineStart) pipelineStart.disabled = running;
  if (pipelineStop) pipelineStop.disabled = !running;
  // Update counts
  if (pipelineProcessed) pipelineProcessed.textContent = data.processed || 0;
  if (pipelineAsked) pipelineAsked.textContent = data.resumed_sent || 0;
  if (pipelineOnline) pipelineOnline.textContent = data.online_downloaded || 0;
  if (pipelineAttach) pipelineAttach.textContent = data.attachment_downloaded || 0;
  if (pipelinePool) pipelinePool.textContent = data.pool_added || 0;
  if (pipelineFailedCount) pipelineFailedCount.textContent = data.failed || 0;
  // Summary
  if (pipelineSummary) {{
    if (state === 'idle') pipelineSummary.textContent = '选择参数后点击"启动流水线"。';
    else if (running) pipelineSummary.textContent = '流水线运行中… 日志实时更新在下方。';
    else if (state === 'succeeded') pipelineSummary.textContent = '流水线完成！处理 ' + (data.processed||0) + ' 人，入库 ' + (data.pool_added||0) + ' 人。';
    else if (state === 'stopped') pipelineSummary.textContent = '流水线已停止：' + (data.stopped_reason || '用户手动停止');
    else pipelineSummary.textContent = stateLabels[state] || state;
  }}
  // Notice
  if (pipelineNotice) {{
    const err = data.error;
    pipelineNotice.textContent = err ? ((err.message || '') + (data.stopped_reason ? ' (' + data.stopped_reason + ')' : '')) : '';
    pipelineNotice.className = 'notice' + (err ? ' error' : '');
  }}
  // Log viewer
  if (pipelineLogViewer && data.logs) {{
    const logs = data.logs.slice(-300);
    if (!logs.length && state === 'idle') {{
      pipelineLogViewer.innerHTML = '<div style="color:#5c7a9e;">等待流水线启动…</div>';
    }} else if (!logs.length) {{
      pipelineLogViewer.innerHTML = '<div style="color:#5c7a9e;">暂无日志</div>';
    }} else {{
      let html = '';
      let prevTs = '';
      logs.forEach(function(log) {{
        const lvl = log.level || 'info';
        const shortTs = (log.ts || '').substring(0, 8);
        html += '<div class="pipeline-log-entry" data-level="' + lvl + '">';
        html += '<span class="pipeline-log-ts">' + escHtml(shortTs) + '</span>';
        html += '<span class="pipeline-log-step">' + escHtml((log.step||'').substring(0,10)) + '</span>';
        html += '<span class="pipeline-log-candidate">' + escHtml((log.candidate||'').substring(0,8)) + '</span>';
        html += '<span class="pipeline-log-level ' + lvl + '">' + lvl.replace('_',' ') + '</span>';
        html += '<span class="pipeline-log-label">' + escHtml((log.label||'').substring(0,30)) + '</span>';
        // Detail content
        const detail = log.detail || '';
        const prompt = log.prompt_text || '';
        const raw = log.raw_response || '';
        const parsed = log.parsed_result || '';
        const body = detail || prompt || raw || parsed || '';
        if (body) {{
          const isLong = body.length > 200 || lvl === 'ai_input' || lvl === 'ai_output';
          const display = isLong ? body.substring(0, 200) + '…' : body;
          html += '<span class="pipeline-log-detail">' + escHtml(display) + '</span>';
          if (isLong) {{
                                    html += '<span class="pipeline-log-toggle" data-full="' + escHtml(body).replace(/"/g, '&quot;').replace(/'/g, '&#39;') + '" data-display="' + escHtml(display).replace(/"/g, '&quot;').replace(/'/g, '&#39;') + '">展开全文(' + body.length + '字符)</span>';
          }}
        }}
        html += '</div>';
      }});
      pipelineLogViewer.innerHTML = html;
      // Auto-scroll to bottom
      pipelineLogViewer.scrollTop = pipelineLogViewer.scrollHeight;
    }}
  }}
  // Results
  if (pipelineResults && data.items) {{
    let html = '';
    data.items.forEach(function(item, idx) {{
      const ok = !item.error;
      html += '<div class="workspace-row" style="border-left-color:' + (ok ? 'var(--ok)' : 'var(--danger)') + '">';
      html += '<strong>' + escHtml(item.candidate_name || ('候选人' + (idx+1))) + '</strong>';
      html += '<span class="workspace-meta">评分: ' + (item.score || '-') + ' | ';
      html += '在线: ' + (item.online_resume_downloaded ? '已下载' : '未获取') + ' | ';
      html += '附件: ' + (item.attachment_downloaded ? '已下载' : (item.attachment_available ? '可用' : '无')) + ' | ';
      html += '入库: ' + (item.pool_added ? '是' : '否');
      if (item.error) html += ' | 错误: ' + escHtml(item.error);
      html += '</span>';
      if (item.follow_up_questions && item.follow_up_questions.length) {{
        html += '<span class="workspace-meta">追问: ' + escHtml(item.follow_up_questions.join(' | ')) + '</span>';
      }}
      html += '</div>';
    }});
    pipelineResults.innerHTML = html;
  }}
}}
function escHtml(s) {{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}

async function startPipeline() {{
  if (pipelineStart) pipelineStart.disabled = true;
  if (pipelineActionState) pipelineActionState.textContent = '正在启动流水线…';
  const body = {{
    limit: Number(pipelineLimit.value || 20),
    threshold: Number(pipelineThreshold.value || 70),
    ask_for_resume: pipelineAskResume ? pipelineAskResume.checked : true
  }};
  const payload = await post('/api/pipeline/start', body);
  if (!payload.ok) {{
    if (pipelineActionState) pipelineActionState.textContent = (payload.error && payload.error.message) || '流水线启动失败';
    if (pipelineStart) pipelineStart.disabled = false;
    await refresh();
    return;
  }}
  if (pipelineActionState) pipelineActionState.textContent = '流水线已启动，日志实时更新中…';
  await refresh();
}}

async function stopPipeline() {{
  if (pipelineStop) pipelineStop.disabled = true;
  if (pipelineActionState) pipelineActionState.textContent = '正在请求停止…';
  const payload = await post('/api/pipeline/stop', {{}});
  if (!payload.ok) {{
    if (pipelineActionState) pipelineActionState.textContent = (payload.error && payload.error.message) || '停止失败';
  }} else {{
    if (pipelineActionState) pipelineActionState.textContent = '已请求停止，当前候选人处理完即停';
  }}
  await refresh();
}}

function renderBatchExport(batch) {{
  const data=batch || {{state:'idle'}};
  const state=data.state || 'idle';
  const running=state==='running';
  const items=Array.isArray(data.items) ? data.items : [];
  const requested=Number(data.requested || 0);
  const processed=Number(data.processed || items.length);
  const scanning=data.mode==='scan';
  const stateLabels={{idle:'未开始',running:scanning?'正在扫描':'正在导出',succeeded:'已完成',failed:'失败',blocked:'已阻断'}};
  if(batchExportStateChip) {{
    batchExportStateChip.textContent=stateLabels[state] || '未知';
    batchExportStateChip.className='pacing-state'+(state==='failed'||state==='blocked'?' unavailable':(running?' paused':''));
  }}
  const ready=Boolean(current && current.operating_mode==='research' && current.login && current.login.state==='succeeded');
  if(batchExportStart) batchExportStart.disabled=running || !ready;
  if(batchExportScan) batchExportScan.disabled=running || !ready || batchExportSource.value!=='conversation';
  if(batchExportStop) batchExportStop.disabled=!running;
  if(batchExportSummary) {{
    batchExportSummary.textContent=state==='idle'
      ? '尚未开始'
      : `已处理 ${{processed}} / ${{requested || processed}} · 成功 ${{Number(data.succeeded || 0)}} · 失败 ${{Number(data.failed || 0)}} · 含 PDF ${{Number(data.with_attachment || 0)}}`;
  }}
  if(batchExportBar) {{
    const total=requested || processed || 1;
    batchExportBar.style.width=Math.min(100,Math.round((processed/total)*100))+'%';
  }}
  if(batchExportMeta) {{
    const sourceLabel=data.source==='recommendation'?'推荐牛人':'沟通候选人';
    batchExportMeta.textContent=state==='idle'
      ? '选择来源和数量后点击“一键导出这些人”；扫描附件只判定谁已分享，不下载文件。'
      : `${{sourceLabel}} · ${{scanning?'只扫描附件':'导出在线简历与已分享附件'}}${{running?' · 每位候选人之间保留安全间隔':''}}`;
  }}
  if(batchExportNotice) {{
    const stopLabel=batchStopReasonLabels[data.stopped_reason];
    const errorMessage=data.error && data.error.message;
    batchExportNotice.textContent=errorMessage || stopLabel || '';
    batchExportNotice.className='notice'+(errorMessage?' error':(stopLabel?' warn':''));
  }}
  batchExportImportItems=items.filter(item => item.online_status==='exported' && item.online_path && !item.error_code);
  if(batchExportImportAll) batchExportImportAll.disabled=running || !batchExportImportItems.length;
  if(!batchExportResults) return;
  batchExportResults.replaceChildren();
  if(!items.length) {{
    if(state!=='idle') batchExportResults.textContent=running?'正在读取第一位候选人…':'本批没有产生任何结果。';
    return;
  }}
  items.forEach(item => {{
    const row=document.createElement('div');
    row.className='batch-result-row'+(item.error_code?' is-failed':(item.online_status==='skipped'?' is-skipped':''));
    const info=document.createElement('div');
    const name=document.createElement('strong'); name.textContent=item.name || '（未命名候选人）';
    const meta=document.createElement('div'); meta.className='batch-result-meta';
    meta.textContent=item.error_code
      ? `失败：${{item.error_message || '未知原因'}}`
      : (item.online_filename ? `在线简历：${{item.online_filename}}` : '未导出在线简历（仅扫描附件）');
    info.append(name,meta);
    if(item.online_path) {{ const path=document.createElement('div'); path.className='batch-result-path'; path.textContent=item.online_path; info.append(path); }}
    if(item.attachment_filename) {{ const attachment=document.createElement('div'); attachment.className='batch-result-path'; attachment.textContent='附件：'+item.attachment_filename; info.append(attachment); }}
    const badges=document.createElement('div'); badges.className='conversation-badges';
    appendAttachmentBadge(badges,item.attachment_status);
    row.append(info,badges); batchExportResults.append(row);
  }});
}}
async function startBatchExport(mode) {{
  if(!batchExportForm) return;
  const body={{
    source:batchExportSource.value,
    limit:Number(batchExportLimit.value || 20),
    mode:mode,
    job_id:batchExportJobId.value.trim(),
    output_dir:batchExportOutputDir.value.trim(),
  }};
  batchExportActionState.textContent=mode==='scan'?'正在提交附件扫描…':'正在提交批量导出…';
  const payload=await post('/api/batch-export',body);
  if(!payload.ok) {{ batchExportActionState.textContent=(payload.error && payload.error.message) || '批量任务启动失败'; await refresh(); return; }}
  batchExportActionState.textContent=mode==='scan'?'扫描已开始，可随时停止':'导出已开始，可随时停止';
  await refresh();
}}
async function importBatchExportResults() {{
  if(!batchExportImportItems.length) return;
  const jobId=selectedRecruitingJobId();
  const source=batchExportSource.value==='recommendation'?'boss_recommendation':'boss_conversation';
  batchExportImportAll.disabled=true;
  let imported=0; let failed=0;
  for(const item of batchExportImportItems) {{
    batchExportImportState.textContent=`正在导入 ${{imported+failed+1}} / ${{batchExportImportItems.length}}…`;
    const payload=await post('/api/recruiting/candidates/import',{{resume_path:item.online_path,source:source,job_id:jobId}});
    if(payload.ok) imported+=1; else failed+=1;
  }}
  batchExportImportState.textContent=`已导入 ${{imported}} 位${{failed?`，${{failed}} 位失败`:''}}${{jobId?'（已绑定当前岗位）':'（未选择岗位，可在工作台补绑）'}}`;
  await refresh();
}}
function renderConversationList(listing) {{

  conversationList.replaceChildren();
  conversationList.className='conversation-list';
  if (listing.state === 'running') {{ conversationFilterCount.textContent='正在读取'; conversationList.setAttribute('aria-busy','true'); conversationList.textContent='正在读取沟通列表…'; return; }}
  if (listing.refreshing === true) {{ conversationFilterCount.textContent='正在刷新'; conversationList.setAttribute('aria-busy','true'); }} else conversationList.removeAttribute('aria-busy');
  if (listing.state === 'failed') {{ conversationFilterCount.textContent=''; conversationList.removeAttribute('aria-busy'); conversationList.textContent=listing.error ? listing.error.message : '沟通列表读取失败'; conversationList.className='conversation-list notice error'; return; }}
  if (listing.state !== 'succeeded') {{ conversationFilterCount.textContent=''; conversationList.removeAttribute('aria-busy'); conversationList.textContent='点击刷新列表读取 BOSS 沟通候选人。'; return; }}
  if (listing.notice) {{ const notice=document.createElement('div'); notice.className='notice warn'; notice.textContent=listing.notice.message; conversationList.append(notice); }}
	const items=Array.isArray(listing.items) ? listing.items : [];
	const listBusy=listing.state==='running' || listing.refreshing===true;
  const query=(conversationFilter.value || '').trim().toLowerCase();
  const filtered=items.filter(item => [item.candidate_name,item.position,item.company,item.city,item.updated_at].filter(Boolean).join(' ').toLowerCase().includes(query));
  conversationFilterCount.textContent=query ? `显示 ${{filtered.length}} / ${{items.length}} 位` : `共 ${{items.length}} 位`;
  if (!items.length) {{ const empty=document.createElement('div'); empty.textContent='暂无可显示的沟通候选人。'; conversationList.append(empty); return; }}
  if (!filtered.length) {{ const empty=document.createElement('div'); empty.textContent='没有符合筛选条件的沟通候选人。'; conversationList.append(empty); return; }}
	filtered.forEach(item => {{
    const row=document.createElement('div'); row.className='conversation-row';
    const info=document.createElement('div');
    const name=document.createElement('div'); name.className='conversation-name'; name.textContent=item.candidate_name;
    const context=document.createElement('div'); context.className='conversation-context'; context.textContent=[item.position,item.company,item.city].filter(Boolean).join(' · ') || '职位、公司和城市未提供';
    const time=document.createElement('div'); time.className='conversation-time'; time.textContent=item.updated_at;
    const unread=Number(item.unread_count || 0);
		const unreadText=Number.isFinite(unread) && unread > 0 ? ` · 未读 ${{Math.min(unread,999)}}` : '';
		time.textContent=(item.updated_at || '-')+unreadText;
		info.append(name,context,time);
		// 附件徽标来自本地扫描索引：确认有、确认没有和还没查过必须区分，
		// 未检测绝不能显示成“无附件”。扫描动作在“一键导出”页发起。
		const badges=document.createElement('div'); badges.className='conversation-badges';
		appendAttachmentBadge(badges,item.attachment_badge || 'not_checked');
		info.append(badges);
			// 分析状态徽标
			const analyzedInfo = current && current.analysis_statuses && current.analysis_statuses[item.selection_id];
			const analysisBadge = document.createElement('span');
			analysisBadge.className = analyzedInfo ? 'pdf-badge can' : 'pdf-badge unknown';
			analysisBadge.textContent = analyzedInfo ? '已分析(' + (analyzedInfo.score || '?') + '分)' : '待分析';
			analysisBadge.title = analyzedInfo ? ('评分: ' + (analyzedInfo.score || '?') + ' | ' + (analyzedInfo.recommendation || '') + ' | ' + (analyzedInfo.analyzed_at || '')) : '尚未进行AI分析';
			badges.appendChild(analysisBadge);
		const detailBusy=Boolean(current && current.conversation_detail && current.conversation_detail.state==='running');
		const detailState=current && current.conversation_detail && current.conversation_detail.selection_id===item.selection_id ? current.conversation_detail : null;
		if (detailState && detailState.state==='succeeded' && detailState.data) {{
			const detail=document.createElement('div'); detail.className='conversation-detail'; detail.textContent=[detailState.data.position,detailState.data.company,detailState.data.city].filter(Boolean).join(' · ') || 'BOSS 卡片未提供职位、公司或城市'; info.append(detail);
		}} else if (detailState && detailState.state==='failed' && detailState.error) {{
			const detail=document.createElement('div'); detail.className='conversation-detail'; detail.textContent=detailState.error.message; info.append(detail);
		}}
		const button=document.createElement('button'); button.type='button'; button.textContent='下载简历';
	button.disabled=listBusy || !(current && current.operating_mode==='research' && current.login.state==='succeeded' && current.conversation_download.state!=='running');
		button.addEventListener('click', async () => {{
      button.disabled=true;
      conversationListActionState.textContent='正在提交导出请求…';
      const payload=await post('/api/conversations/'+encodeURIComponent(item.selection_id)+'/resume-download',{{workspace_job_id:selectedRecruitingJobId()}});
      rememberRequestError(conversationListActionState,payload);
      await refresh();
		}});
		// 分析按钮
			const analyzeBtn=document.createElement('button'); analyzeBtn.type='button'; analyzeBtn.className='secondary'; analyzeBtn.textContent='分析';
			analyzeBtn.disabled=listBusy || !(current && current.operating_mode==='research' && current.login.state==='succeeded'); const saForBtn=current && current.single_analysis; if(saForBtn && saForBtn.candidate_name===item.candidate_name && saForBtn.state==='running'){{analyzeBtn.disabled=true; analyzeBtn.textContent='分析中…';}} else if(saForBtn && saForBtn.candidate_name===item.candidate_name && saForBtn.state==='succeeded'){{analyzeBtn.textContent='已完成('+(saForBtn.item&&saForBtn.item.score||'?')+'分)';}}
			analyzeBtn.addEventListener('click', async () => {{
				analyzeBtn.disabled=true; analyzeBtn.textContent='分析中…';
				const payload=await post('/api/pipeline/analyze-one',{{selection_id:item.selection_id}});
				const failed=!payload.ok || (payload.data && payload.data.state==='failed'); if(failed) {{ alert('分析失败: '+((payload.error&&payload.error.message)||(payload.data&&payload.data.error&&payload.data.error.message)||'未知错误')); analyzeBtn.disabled=false; analyzeBtn.textContent='分析'; }}
				else {{ analyzeBtn.textContent='已提交'; }}
				await refresh();
			}});
					const canReadDetail=Boolean(current && current.operating_mode==='research' && current.login && current.login.state==='succeeded');
		const detailButton=document.createElement('button'); detailButton.type='button'; detailButton.className='secondary'; detailButton.textContent=detailState && detailState.state==='running' ? '读取中…' : (detailBusy ? '等待当前读取完成' : '查看 BOSS 卡片信息'); detailButton.title='只读取这一位候选人的官方卡片上下文'; detailButton.disabled=listBusy || !canReadDetail || detailBusy;
		detailButton.addEventListener('click', async () => {{ detailButton.disabled=true; conversationListActionState.textContent='正在读取候选人卡片信息…'; const payload=await post('/api/conversations/'+encodeURIComponent(item.selection_id)+'/details',{{}}); rememberRequestError(conversationListActionState,payload); await refresh(); }});
		const actions=document.createElement('div'); actions.className='actions'; actions.append(detailButton,analyzeBtn,button);
		row.append(info,actions); conversationList.append(row);
	}});
}}

// -- 单人分析结果展示 --
function renderSingleAnalysisResult(sa) {{
	const container = document.querySelector('#single-analysis-result');
	if (!container) return;
	if (!sa || sa.state === 'idle') {{ container.style.display = 'none'; container.replaceChildren(); return; }}
	container.style.display = 'block';
	container.replaceChildren();
	if (sa.state === 'running') {{
		container.innerHTML = '<div class="notice info">正在分析 ' + (sa.candidate_name || '候选人') + ' 的简历…</div>';
		return;
	}}
	if (sa.state === 'failed') {{
		container.innerHTML = '<div class="notice error">分析失败: ' + ((sa.error && sa.error.message) || '未知错误') + '</div>';
		return;
	}}
	if (sa.state === 'succeeded' && sa.item) {{
		const item = sa.item;
		const wrapper = document.createElement('div');
		wrapper.innerHTML = '<h3>分析结果: ' + (item.candidate_name || '') + '</h3>' +
			'<div class="funnel-metrics">' +(item.error ? '<div class="funnel-metrics-row" style="color:var(--danger)"><strong>错误</strong> ' + (item.error || '') + '</div>' : '') +(item.online_resume_downloaded ? '<div class="funnel-metrics-row" style="color:var(--ok)">在线简历已下载</div>' : '<div class="funnel-metrics-row" style="color:var(--warn)">在线简历未下载</div>') +
			'<div class="funnel-metrics-row"><strong>综合评分</strong> ' + (item.score || 0) + ' 分</div>' +
			'<div class="funnel-metrics-row"><strong>推荐</strong> ' + (item.analysis_recommendation || 'review') + '</div>' +
			'<div class="funnel-metrics-row"><strong>分析来源</strong> ' + (item.analysis_source || '未知') + '</div>' +
			(item.online_resume_downloaded ? '<div class="funnel-metrics-row">在线简历已下载</div>' : '') +
			'</div>';
		container.append(wrapper);

		// 追问
		if (item.follow_up_questions && item.follow_up_questions.length) {{
			const fq = document.createElement('div');
			fq.className = 'funnel-metrics';
			fq.innerHTML = '<div class="funnel-metrics-heading">建议追问</div>';
			item.follow_up_questions.forEach(function(q) {{
				const d = document.createElement('div');
				d.className = 'funnel-metrics-row';
				d.textContent = q;
				fq.append(d);
			}});
			container.append(fq);
		}}

		// AI 输入输出日志
		const logs = sa.logs || [];
		const aiLogs = logs.filter(function(l) {{ return true; }});  // 显示所有日志
		if (aiLogs.length) {{
			const logSection = document.createElement('div');
			logSection.className = 'pipeline-log-viewer';
			logSection.setAttribute('role', 'log');
			logSection.setAttribute('aria-label', 'AI 分析日志');
			logSection.style.maxHeight = '400px';
			logSection.style.marginTop = '10px';
			aiLogs.forEach(function(log) {{
				const entry = document.createElement('div');
				entry.className = 'pipeline-log-entry';
				const level = (log.level === 'ai_input' || log.level === 'ai_output' || log.level === 'info' || log.level === 'warn' || log.level === 'error') ? log.level : 'info';
				const label = log.level === 'ai_input' ? (log.label || 'AI Prompt') : (log.label || 'AI 响应');
				entry.innerHTML = '<span class="pipeline-log-ts">' + (log.ts || '') + '</span>' +
					'<span class="pipeline-log-step">' + (log.step || '') + '</span>' +
					'<span class="pipeline-log-candidate">' + (log.candidate || '') + '</span>' +
					'<span class="pipeline-log-level ' + level + '">' + level + '</span>' +
					'<span class="pipeline-log-label">' + label + '</span>';
				const detail = document.createElement('div');
				detail.className = 'pipeline-log-detail';
				const display = (log.prompt_text || log.raw_response || '').substring(0, 300);
				const full = log.prompt_text || log.raw_response || '';
				detail.textContent = display;
				entry.append(detail);
				if (full.length > 300) {{
					const toggle = document.createElement('div');
					toggle.className = 'pipeline-log-toggle';
					toggle.textContent = '展开全文(' + full.length + '字符)';
					toggle.setAttribute('data-display', display);
					toggle.setAttribute('data-full', full);
					entry.append(toggle);
				}}
				logSection.append(entry);
			}});
			container.append(logSection);
		}}
	}}
}}

function renderRecommendationList(listing) {{
  recommendationList.replaceChildren();
  recommendationList.className='conversation-list';
  if (listing.state === 'running') {{ recommendationList.setAttribute('aria-busy','true'); recommendationList.textContent='正在读取推荐牛人…'; return; }}
  if (listing.refreshing === true) {{ recommendationList.setAttribute('aria-busy','true'); }} else recommendationList.removeAttribute('aria-busy');
  if (listing.state === 'failed') {{ recommendationList.removeAttribute('aria-busy'); recommendationList.textContent=listing.error ? listing.error.message : '推荐牛人列表读取失败'; recommendationList.className='conversation-list notice error'; return; }}
  if (listing.state !== 'succeeded') {{ recommendationList.removeAttribute('aria-busy'); recommendationList.textContent='点击“读取推荐列表”开始读取 BOSS 推荐牛人。'; return; }}
  if (listing.notice) {{ const notice=document.createElement('div'); notice.className='notice warn'; notice.textContent=listing.notice.message; recommendationList.append(notice); }}
  if (!listing.items.length) {{ const empty=document.createElement('div'); empty.textContent='暂无可显示的推荐候选人。'; recommendationList.append(empty); return; }}
  listing.items.forEach(item => {{
    const row=document.createElement('div'); row.className='recommendation-row';
    const info=document.createElement('div');
    const name=document.createElement('div'); name.className='recommendation-name'; name.textContent=item.candidate_name;
    const meta=document.createElement('div'); meta.className='recommendation-meta'; meta.textContent=[item.title,item.city,item.experience,item.degree].filter(Boolean).join(' · ');
    const extra=document.createElement('div'); extra.className='recommendation-meta'; extra.textContent=[item.salary,item.active_time,item.company].filter(Boolean).join(' · ');
    info.append(name,meta,extra);
    const button=document.createElement('button'); button.type='button'; button.textContent=item.can_download?'下载在线简历':'暂不可下载'; button.title=item.download_hint || '下载已有在线简历';
    button.disabled=listing.refreshing===true || !(item.can_download && current && current.operating_mode==='research' && current.login.state==='succeeded' && current.recommendation_download.state!=='running');
    button.addEventListener('click', async () => {{
      button.disabled=true;
      recommendationActionState.textContent='正在提交推荐候选人导出请求…';
      const payload=await post('/api/recommendations/'+encodeURIComponent(item.selection_id)+'/resume-download',{{workspace_job_id:selectedRecruitingJobId()}});
      rememberRequestError(recommendationActionState,payload);
      await refresh();
    }});
    row.append(info,button); recommendationList.append(row);
  }});
}}
function renderBossJobs(listing) {{
  if (!listing || listing.state !== 'succeeded') return;
  const previous=String(recommendationJobSelect.value || '');
  recommendationJobSelect.replaceChildren();
  const placeholder=document.createElement('option'); placeholder.value=''; placeholder.textContent='选择一个已发布职位'; recommendationJobSelect.append(placeholder);
  (Array.isArray(listing.items) ? listing.items : []).forEach(item => {{
    if (!item || typeof item.job_id !== 'string' || !item.job_id) return;
    const option=document.createElement('option'); option.value=item.job_id; option.textContent=item.name || '未命名职位'; recommendationJobSelect.append(option);
  }});
  recommendationJobSelect.disabled=recommendationJobSelect.options.length <= 1;
  if (previous && Array.from(recommendationJobSelect.options).some(option => option.value===previous)) recommendationJobSelect.value=previous;
  recommendationJobState.textContent=recommendationJobSelect.options.length > 1 ? '已读取 '+String(recommendationJobSelect.options.length-1)+' 个可用职位' : '未找到可用于推荐读取的在线职位';
}}
async function refreshBossJobs() {{
  recommendationJobRefreshButton.disabled=true; recommendationJobState.textContent='正在读取 BOSS 已发布职位…';
  try {{
    const response=await fetch('/api/boss-jobs'); const payload=await response.json();
    const listing=payload && payload.data;
    if (!payload.ok || !listing || listing.state !== 'succeeded') {{ recommendationJobState.textContent=(listing && listing.error && listing.error.message) || (payload.error && payload.error.message) || '职位列表读取失败，请确认登录后重试'; return; }}
    renderBossJobs(listing);
  }} catch (_) {{ recommendationJobState.textContent='职位列表读取失败，请检查本地服务和登录状态后重试'; }}
  finally {{ recommendationJobRefreshButton.disabled=false; }}
}}
function renderDashboard(data) {{
	  if (!data) return;
	  const jobs=(data.jobs || []).length;
	  const candidates=(data.candidates || []).length;
	  const activeCount=(data.candidates || []).filter(c => !terminalStages.has(c.stage)).length;
	  const assessed=(data.assessments || []).length;
	  const hired=(data.candidates || []).filter(c => c.stage==='hired').length;
	  const avgScore=data.assessments && data.assessments.length ? Math.round(data.assessments.reduce((sum,a) => sum+(Number(a.final_score)||0),0)/data.assessments.length) : null;
	  const rate=hired && candidates ? Math.round(hired/candidates*100) : 0;
	  // 趋势计算
	  const hist=(data.historical_snapshots||[]);
	  let trendCandidates='neutral',trendAssessed='neutral',trendHired='neutral';
	  if(hist.length>=2) {{
	    const prev=hist[hist.length-2].metrics||{{}};
	    const curr=hist[hist.length-1].metrics||{{}};
	    if(curr.candidate_count>prev.candidate_count) trendCandidates='up'; else if(curr.candidate_count<prev.candidate_count) trendCandidates='down';
	    if(curr.assessed_count>prev.assessed_count) trendAssessed='up'; else if(curr.assessed_count<prev.assessed_count) trendAssessed='down';
	    if(curr.hired_count>prev.hired_count) trendHired='up'; else if(curr.hired_count<prev.hired_count) trendHired='down';
	  }}
	  const dashJobs=document.querySelector('#dash-jobs'); if(dashJobs) dashJobs.textContent=jobs;
	  const dashCandidates=document.querySelector('#dash-candidates'); if(dashCandidates) dashCandidates.innerHTML=candidates+'<span class="metric-trend '+trendCandidates+'">'+(trendCandidates==='up'?' ↑':(trendCandidates==='down'?' ↓':''))+'</span>';
	  const dashActive=document.querySelector('#dash-active-candidates'); if(dashActive) dashActive.textContent=`活跃 ${{activeCount}} 位 · 终局 ${{candidates-activeCount}} 位`;
	  const dashAssessed=document.querySelector('#dash-assessed'); if(dashAssessed) dashAssessed.innerHTML=assessed+'<span class="metric-trend '+trendAssessed+'">'+(trendAssessed==='up'?' ↑':(trendAssessed==='down'?' ↓':''))+'</span>';
	  const dashAvgScore=document.querySelector('#dash-avg-score'); if(dashAvgScore && avgScore!==null) dashAvgScore.textContent=`平均分 ${{avgScore}}`;
	  const dashHired=document.querySelector('#dash-hired'); if(dashHired) dashHired.innerHTML=hired+'<span class="metric-trend '+trendHired+'">'+(trendHired==='up'?' ↑':(trendHired==='down'?' ↓':''))+'</span>';
	  const dashHireRate=document.querySelector('#dash-hire-rate'); if(dashHireRate) {{ dashHireRate.textContent=`转化率 ${{rate}}%`; dashHireRate.className='metric-trend '+(rate>=30?'up':(rate>=15?'neutral':'down')); }}
	  // 漏斗：优先用 pipeline.funnel，回退用 pipeline.counts
	  const funnel=document.querySelector('#dash-funnel');
	  if(funnel) {{
	    const pipeStages=(data.pipeline || {{}}).counts || {{}};
	    const funnelData=(data.pipeline || {{}}).funnel || [];
	    const stages=funnelData.length ? funnelData : [{{stage:'pending_screening',label:'待筛选',count:pipeStages.pending_screening||0}},{{stage:'basic_passed',label:'基础通过',count:pipeStages.basic_passed||0}},{{stage:'resume_passed',label:'简历通过',count:pipeStages.resume_passed||0}},{{stage:'interview_scheduled',label:'已约面',count:pipeStages.interview_scheduled||0}},{{stage:'hired',label:'录用',count:pipeStages.hired||0}}];
	    const maxCount=Math.max(1,...stages.map(s => s.count||0));
	    funnel.innerHTML=stages.map((stage,i) => {{
	      const count=stage.count||0;
	      const ratio=maxCount?Math.max(3,Math.round(count/maxCount*100)):0;
	      const convRate=i>0&&stages[i-1].count?Math.round(count/stages[i-1].count*100):(count?100:0);
	      const barClass=stage.stage==='hired'?'secondary-bar':(stage.stage==='resume_passed'?'warn-bar':'');
	      return '<div class="funnel-row"><span class="funnel-label">'+(stage.label||stage.stage)+'</span><div class="funnel-bar-wrap"><div class="funnel-bar'+(barClass?' '+barClass:'')+'" style="width:'+ratio+'%"><span class="funnel-count">'+count+'</span></div><span class="funnel-rate">'+convRate+'%</span></div></div>';
	    }}).join('');
	  }}
	  // 来源转化
	  const pipelineSources=(data.pipeline || {{}}).sources || {{}};
	  const sources={{boss_conversation:document.querySelector('#dash-source-conversation'),boss_recommendation:document.querySelector('#dash-source-recommendation'),local_markdown:document.querySelector('#dash-source-local')}};
	  Object.entries(sources).forEach(([source,el]) => {{ if(!el) return; el.textContent=pipelineSources[source]||0; }});
	  const sourceConvRate=document.querySelector('#dash-source-conversation-rate');
	  if(sourceConvRate) {{ const srcCount=pipelineSources.boss_conversation||0; sourceConvRate.textContent=srcCount?`沟通来源 ${{srcCount}} 人`:'暂无数据'; }}
	  const sourceRecRate=document.querySelector('#dash-source-recommendation-rate');
	  if(sourceRecRate) {{ const srcCount=pipelineSources.boss_recommendation||0; sourceRecRate.textContent=srcCount?`推荐来源 ${{srcCount}} 人`:'暂无数据'; }}
	  // 话术效果
	  const dashTemplates=document.querySelector('#dash-templates');
	  if(dashTemplates) {{
	    const tmplEffectiveness=(data.optimization && data.optimization.template_effectiveness) || [];
	    if(!tmplEffectiveness.length) {{ dashTemplates.textContent='暂无语术使用数据。在实际沟通中标记已使用的话术，这里会按效果排行。'; }}
	    else {{ dashTemplates.innerHTML=tmplEffectiveness.slice(0,5).map(t => '<div class="workspace-row"><strong>'+t.template_key+'</strong><div class="workspace-meta">使用 '+String(t.usage_count||0)+' 次 · 响应率 '+String(t.reply_rate||0)+'% · 通过率 '+String(t.qualified_rate||0)+'% · 录用 '+String(t.hire_rate||0)+'%</div></div>').join(''); }}
	  }}
	  // FAQ 热门
	  const dashFaq=document.querySelector('#dash-faq-demands');
	  if(dashFaq) {{
	    const demands=(data.question_demands || []).slice(0,5);
	    if(!demands.length) {{ dashFaq.textContent='暂无 FAQ 问题统计。候选人在沟通中提问后，这里会显示热门问题排行。'; }}
	    else {{ dashFaq.innerHTML=demands.map(d => '<div class="workspace-row"><strong>'+d.normalized_question+'</strong><div class="workspace-meta">被问 '+String(d.count||0)+' 次</div></div>').join(''); }}
	  }}
	}}
function render(data) {{
  current=data;
  const login=data.login;
  const previousLoginState=lastLoginState;
  lastLoginState=login.state;
  const download=data.download;
  const conversation=data.conversation_download;
  const recommendation=data.recommendation_download;
  const recruiting=data.recruiting || {{state:'idle'}};
	 renderAutomationControl(data.automation || {{}});
  renderRecruitingContexts(data.recruiting_context);
  loginState.textContent=stateText(login.state);
  loginDetail.textContent=login.error ? login.error.message : (login.notice || (login.state==='succeeded' ? '当前项目连接的 RPA Chrome 已通过 BOSS 页面校验。' : '点击按钮将在项目连接的 RPA Chrome 中打开官方 BOSS 登录页。'));
  loginDetail.className='notice'+(login.error?' error':'');
  loginButton.disabled=login.state==='running' || login.state==='succeeded';
  loginButton.textContent=login.state==='running'?'等待官方登录确认':(login.state==='succeeded'?'RPA 登录已确认':(login.state==='failed'?'重新登录':'打开 BOSS 登录页'));
  modeState.textContent=data.operating_mode;
  const allowed=data.operating_mode==='research';
  modeDetail.textContent=allowed?'研究模式已显式启用，可由你主动下载单份简历。':'默认低风险模式会阻断下载。请先在终端显式启用 research 模式后重新打开此页面。';
  modeDetail.className='notice '+(allowed?'':'warn');
  renderPacing(data.pacing);
  if(recruitingWorkspace) renderDashboard(recruitingWorkspace);
  const ready=allowed && login.state==='succeeded';
  const canDownload=ready && download.state!=='running';
	const canConversationDownload=ready && conversation.state!=='running';
  const canRecommendationDownload=ready && recommendation.state!=='running';
  const conversationListing=data.conversation_list || {{}};
  downloadButton.disabled=!canDownload;
  conversationDownloadButton.disabled=!canConversationDownload;
  currentConversationDownloadButton.disabled=!canConversationDownload;
  latestConversationDownloadButton.disabled=!canConversationDownload;
  const conversationRefreshing=conversationListing.state==='running' || conversationListing.refreshing===true;
  conversationListRefreshButton.disabled=!ready || conversationRefreshing;
  conversationListRefreshButton.textContent=conversationRefreshing?'刷新中…':'刷新列表';
  conversationListRefreshButton.setAttribute('aria-busy',conversationRefreshing?'true':'false');
  const recommendationRefreshing=data.recommendations.state==='running' || data.recommendations.refreshing===true;
  recommendationRefreshButton.disabled=!ready || recommendationRefreshing;
  recommendationRefreshButton.textContent=recommendationRefreshing?'读取中…':'读取推荐列表';
  recommendationRefreshButton.setAttribute('aria-busy',recommendationRefreshing?'true':'false');
  downloadState.textContent=download.state==='running'?'正在下载，请稍候…':(!ready && allowed?'请先登录':'');
  conversationDownloadState.textContent=conversation.state==='running'?'正在导出，请稍候…':(!ready && allowed?'请先登录':'');
  conversationListActionState.textContent=conversationListing.refreshing===true?'正在刷新沟通列表，请稍候…':(conversationListing.state==='running'?'正在读取沟通列表…':(conversation.error ? conversation.error.message:(conversation.state==='running'?'正在导出所选候选人的简历…':'')));
  currentConversationDownloadState.textContent=conversation.state==='running'?'正在导出当前会话，请稍候…':(!ready && allowed?'请先登录':'');
  latestConversationDownloadState.textContent=conversation.state==='running'?'正在导出最近会话，请稍候…':(!ready && allowed?'请先登录':'');
  recommendationActionState.textContent=recommendation.state==='running'?'正在导出推荐候选人简历，请稍候…':(recommendation.error ? recommendation.error.message:'');
  if (data.recommendations.state==='running') recommendationActionState.textContent='正在读取推荐牛人…';
  const workflow=(recruitingWorkspace && recruitingWorkspace.workflow) || {{}};
  const pipeline=(recruitingWorkspace && recruitingWorkspace.pipeline) || {{}};
  recruitingState.textContent=workspaceStateText(recruiting.state, workflow, pipeline);
  recruitingActionState.className='notice'+(recruiting.error?' error':'');
  recruitingActionState.textContent=recruiting.state==='running'?'正在处理招聘工作台操作…':(recruiting.error ? recruiting.error.message:(recruiting.state==='succeeded'?'操作已完成，请刷新候选人或评估结果。':''));
  if (recruiting.state==='succeeded' && recruiting.result && recruiting.result.warnings && recruiting.result.warnings.length) {{ recruitingJobWarnings.hidden=false; recruitingJobWarnings.textContent=recruiting.result.warnings.join('；'); }}
  renderResult(download);
  renderConversationResult(conversation);
	renderConversationList(data.conversation_list); renderSingleAnalysisResult(data.single_analysis);
  renderRecommendationResult(recommendation);
  renderRecommendationList(data.recommendations);
  renderBatchExport(data.batch_export);
	renderPipeline(data.pipeline);
  if (pendingRequestError) pendingRequestError.target.textContent=pendingRequestError.message;
	// 登录状态恢复只更新本地工作台。沟通列表属于会改变 BOSS 页面上下文的
	// RPA 操作，必须由用户点击“刷新沟通列表”显式触发，避免启动控制台时
	// 自动把专用 Chrome 切到沟通页。
}}
async function refresh() {{ try {{ const response=await fetch('/api/state'); const payload=await response.json(); if(payload.ok) render(payload.data); await refreshRecruiting(); settleInitialHashTarget(); await refreshAutomation(); }} catch (err) {{ loginDetail.textContent='错误: ' + (err.message || String(err)); loginDetail.className='notice error'; }} }}
async function refreshRecruitingContexts() {{ try {{ const response=await fetch('/api/recruiting/contexts'); const payload=await response.json(); if(payload.ok) renderRecruitingContexts(payload.data); }} catch (_) {{ if(recruitingContextState) recruitingContextState.textContent='无法读取招聘上下文'; }} }}
loginButton.addEventListener('click', async () => {{ const payload=await post('/api/login'); if(!payload.ok) {{ loginDetail.textContent=payload.error.message; loginDetail.className='notice error'; }} await refresh(); }});
document.querySelector('#download-form').addEventListener('submit', async event => {{ event.preventDefault(); const form=new FormData(event.currentTarget); const body=Object.fromEntries(form); body.workspace_job_id=selectedRecruitingJobId(); const payload=await post('/api/resume-download', body); rememberRequestError(downloadState,payload); await refresh(); }});
document.querySelector('#conversation-download-form').addEventListener('submit', async event => {{ event.preventDefault(); const form=new FormData(event.currentTarget); const body=Object.fromEntries(form); body.workspace_job_id=selectedRecruitingJobId(); const payload=await post('/api/conversation-resume-download', body); rememberRequestError(conversationDownloadState,payload); await refresh(); }});
currentConversationDownloadButton.addEventListener('click', async () => {{ const payload=await post('/api/current-conversation-resume-download',{{workspace_job_id:selectedRecruitingJobId()}}); rememberRequestError(currentConversationDownloadState,payload); await refresh(); }});
latestConversationDownloadButton.addEventListener('click', async () => {{ const payload=await post('/api/latest-conversation-resume-download',{{workspace_job_id:selectedRecruitingJobId()}}); rememberRequestError(latestConversationDownloadState,payload); await refresh(); }});
conversationListRefreshButton.addEventListener('click', async () => {{ const response=await fetch('/api/conversations?refresh=1'); const payload=await response.json(); if(!payload.ok) {{ conversationList.textContent=payload.error.message; }} await refresh(); }});
conversationFilter.addEventListener('input', () => {{ if(current && current.conversation_list) renderConversationList(current.conversation_list); }});
recommendationJobRefreshButton.addEventListener('click', refreshBossJobs);
recommendationRefreshButton.addEventListener('click', async () => {{ const jobId=String(recommendationJobSelect.value || '').trim(); if(!jobId) {{ recommendationActionState.textContent='请先读取并选择一个 BOSS 已发布职位'; return; }} const response=await fetch('/api/recommendations?refresh=1&job_id='+encodeURIComponent(jobId)); const payload=await response.json(); if(!payload.ok) {{ recommendationActionState.textContent=payload.error.message; }} await refresh(); }});
batchExportForm.addEventListener('submit', async event => {{ event.preventDefault(); await startBatchExport('export'); }});
batchExportScan.addEventListener('click', () => startBatchExport('scan'));
batchExportSource.addEventListener('change', () => {{ if(current) renderBatchExport(current.batch_export); }});
batchExportStop.addEventListener('click', async () => {{ batchExportStop.disabled=true; batchExportActionState.textContent='正在请求停止…'; const payload=await post('/api/batch-export/stop',{{}}); if(!payload.ok) batchExportActionState.textContent=(payload.error && payload.error.message) || '停止请求失败'; else batchExportActionState.textContent='已请求停止，当前候选人处理完即停'; await refresh(); }});
batchExportImportAll.addEventListener('click', importBatchExportResults);
if (pipelineStart) pipelineStart.addEventListener('click', startPipeline);
if (pipelineStop) pipelineStop.addEventListener('click', stopPipeline);


	// Single analysis log viewer event delegation
	const singleAnalysisResult = document.querySelector('#single-analysis-result');
	if (singleAnalysisResult) {{
		singleAnalysisResult.addEventListener('click', function(e) {{
			const toggle = e.target.closest('.pipeline-log-toggle');
			if (!toggle) return;
			const row = toggle.parentElement;
			const detail = row.querySelector('.pipeline-log-detail');
			if (!detail) return;
			row.classList.toggle('expanded');
			if (row.classList.contains('expanded')) {{
				detail.textContent = toggle.getAttribute('data-full') || '';
				toggle.textContent = '收起';
			}} else {{
				detail.textContent = toggle.getAttribute('data-display') || '';
				toggle.textContent = '展开全文(' + (toggle.getAttribute('data-full') || '').length + '字符)';
			}}
		}});
	}}

// -- 全局批量分析 --
const batchAnalyzeBtn = document.querySelector('#batch-analyze-button');
// Log viewer event delegation for expand/collapse
if (pipelineLogViewer) {{
	pipelineLogViewer.addEventListener('click', function(e) {{
		const toggle = e.target.closest('.pipeline-log-toggle');
		if (!toggle) return;
		const row = toggle.parentElement;
		const detail = row.querySelector('.pipeline-log-detail');
		if (!detail) return;
		row.classList.toggle('expanded');
		if (row.classList.contains('expanded')) {{
			detail.textContent = toggle.getAttribute('data-full') || '';
			toggle.textContent = '收起';
		}} else {{
			detail.textContent = toggle.getAttribute('data-display') || '';
			toggle.textContent = '展开全文(' + (toggle.getAttribute('data-full') || '').length + '字符)';
		}}
	}});
}}

if (batchAnalyzeBtn) {{
	batchAnalyzeBtn.addEventListener('click', async function() {{
		if (!confirm('将按沟通列表顺序分析所有【待分析】候选人（最多20人），已分析过的自动跳过。确定启动？')) return;
		batchAnalyzeBtn.disabled = true;
		batchAnalyzeBtn.textContent = '启动中…';
		const payload = await post('/api/pipeline/analyze-all', {{limit: 20}});
		if (!payload.ok) {{
			alert('启动失败: ' + ((payload.error && payload.error.message) || '未知错误'));
			batchAnalyzeBtn.disabled = false;
			batchAnalyzeBtn.textContent = '一键分析全部';
		}} else {{
			batchAnalyzeBtn.textContent = '分析中…（刷新页面查看进度）';
		}}
		await refresh();
	}});
}}
if (pipelineStart) pipelineStart.addEventListener('click', startPipeline);
if (pipelineStop) pipelineStop.addEventListener('click', stopPipeline);
recruitingContextSelect.addEventListener('change', async () => {{ const selected=recruitingContextSelect.options[recruitingContextSelect.selectedIndex]; if(!selected || !selected.dataset.context) return; const context=JSON.parse(selected.dataset.context); recruitingContextState.textContent='正在切换上下文…'; recruitingContextSelect.disabled=true; const payload=await post('/api/recruiting/context',context); recruitingContextSelect.disabled=false; if(!payload.ok) {{ recruitingContextState.textContent=(payload.error && payload.error.message) || '上下文切换失败'; await refresh(); return; }} recruitingContextState.textContent='上下文已切换，正在刷新工作台…'; recruitingEditingJobId=''; recruitingCreatingNewJob=false; await refresh(); }});
recruitingJobSelect.addEventListener('change', async () => {{ recruitingCreatingNewJob=false; recruitingEditingJobId=recruitingJobSelect.value; recruitingAssessJob.value=recruitingJobSelect.value; await refreshRecruiting(); }});
recruitingAssessCandidate.addEventListener('change', () => {{ recruitingSelectedCandidateId=recruitingAssessCandidate.value; updateRecruitingAssessmentAvailability(); }});
recruitingAssessJob.addEventListener('change', updateRecruitingAssessmentAvailability);
recruitingNewJobButton.addEventListener('click', () => openJobManagementEditor());
document.querySelector('#recruiting-job-form').addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.professional_qa_enabled=recruitingProfessionalQaToggle ? recruitingProfessionalQaToggle.checked : true; body.status='draft'; const path=recruitingEditingJobId && !recruitingCreatingNewJob ? '/api/recruiting/jobs/'+encodeURIComponent(recruitingEditingJobId) : '/api/recruiting/jobs'; const payload=await post(path,body); if(payload.ok) {{ recruitingCreatingNewJob=false; recruitingEditingJobId=(payload.data && payload.data.job && payload.data.job.job_id) || recruitingEditingJobId; if(recruitingJobForm) recruitingJobForm.dataset.dirty='0'; }} rememberRequestError(recruitingActionState,payload); await refresh(); }});
recruitingPublishJobButton.addEventListener('click', async () => {{ const jobId=selectedRecruitingJobId(); if(!jobId) return; recruitingPublishJobButton.disabled=true; const payload=await post('/api/recruiting/jobs/'+encodeURIComponent(jobId)+'/status',{{status:'published'}}); rememberRequestError(recruitingActionState,payload); await refresh(); }});
document.querySelector('#recruiting-knowledge-form').addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=selectedRecruitingJobId(); const payload=await post('/api/recruiting/knowledge',body); rememberRequestError(recruitingActionState,payload); await refresh(); }});
recruitingKnowledgeImportForm.addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=selectedRecruitingJobId(); recruitingActionState.textContent='正在导入知识文件…'; const payload=await post('/api/recruiting/knowledge/import',body); rememberRequestError(recruitingActionState,payload); if(payload.ok) recruitingKnowledgeImportForm.reset(); await refresh(); }});
// ------ 话术模板管理 ------
const recruitingTemplateForm=document.querySelector('#recruiting-template-form');const recruitingTemplateNew=document.querySelector('#recruiting-template-new');const recruitingTemplateState=document.querySelector('#recruiting-template-state');const recruitingTemplateList=document.querySelector('#recruiting-template-list');
function resetTemplateForm(){{ if(!recruitingTemplateForm) return; recruitingTemplateForm.reset(); recruitingTemplateForm.elements.template_id.value=''; recruitingTemplateForm.elements.category.value='greeting'; recruitingTemplateState.textContent=''; }}
function fillTemplateForm(tmpl){{ if(!recruitingTemplateForm||!tmpl) return; recruitingTemplateForm.elements.template_id.value=tmpl.template_id||''; recruitingTemplateForm.elements.template_key.value=tmpl.template_key||''; recruitingTemplateForm.elements.category.value=tmpl.category||'greeting'; recruitingTemplateForm.elements.title.value=tmpl.title||''; recruitingTemplateForm.elements.body.value=tmpl.body||''; recruitingTemplateState.textContent='编辑：'+tmpl.template_key; }}
function renderTemplates(templates){{ if(!recruitingTemplateList) return; recruitingTemplateList.replaceChildren(); const items=templates||[]; if(!items.length){{ recruitingTemplateList.textContent='暂无语术模板。使用上方表单创建第一条。'; return; }} const cats={{greeting:'打招呼',follow_up:'跟进',qa_guide:'问答引导',resume_request:'索要简历',interview_invite:'面试邀约',private_domain:'私域转化',rejection:'婉拒'}}; items.forEach(tmpl => {{ const card=document.createElement('div');card.className='template-card'; const header=document.createElement('div');header.className='template-card-header'; const key=document.createElement('span');key.className='template-key';key.textContent=tmpl.template_key; const cat=document.createElement('span');cat.className='hint';cat.textContent=cats[tmpl.category]||tmpl.category; header.append(key,cat); const body=document.createElement('div');body.className='template-body';body.textContent=tmpl.body||''; const actions=document.createElement('div');actions.className='template-actions'; const editBtn=document.createElement('button');editBtn.type='button';editBtn.className='secondary';editBtn.textContent='编辑';editBtn.addEventListener('click',() => fillTemplateForm(tmpl)); const delBtn=document.createElement('button');delBtn.type='button';delBtn.className='secondary';delBtn.textContent='删除';delBtn.addEventListener('click',async () => {{ delBtn.disabled=true; const payload=await post('/api/recruiting/templates/'+encodeURIComponent(tmpl.template_id)+'/delete',{{}}); if(payload.ok){{ recruitingTemplateState.textContent='已删除';resetTemplateForm();await refresh(); }}else{{ recruitingTemplateState.textContent=(payload.error&&payload.error.message)||'删除失败';delBtn.disabled=false; }} }}); actions.append(editBtn,delBtn); card.append(header,body,actions); recruitingTemplateList.append(card); }}); }}
if(recruitingTemplateForm) recruitingTemplateForm.addEventListener('submit',async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=selectedRecruitingJobId(); recruitingTemplateState.textContent='保存中…'; const payload=await post('/api/recruiting/templates',body); if(payload.ok){{ recruitingTemplateState.textContent='已保存';resetTemplateForm();await refresh(); }}else{{ recruitingTemplateState.textContent=(payload.error&&payload.error.message)||'保存失败'; }} }});
if(recruitingTemplateNew) recruitingTemplateNew.addEventListener('click',resetTemplateForm);
const _origRenderRecruiting_tmpl=renderRecruiting; renderRecruiting=function(data){{ _origRenderRecruiting_tmpl(data); if(data&&data.templates) renderTemplates(data.templates); }};
// ------ 话术模板管理结束 ------
document.querySelector('#recruiting-faq-form').addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=selectedRecruitingJobId(); const payload=await post('/api/recruiting/faq',body); rememberRequestError(recruitingActionState,payload); await refresh(); }});
recruitingFaqDraftButton.addEventListener('click',loadFaqDrafts);
recruitingKnowledgeSearchForm.addEventListener('submit', searchRecruitingKnowledge);
recruitingKnowledgeAnswerForm.addEventListener('submit', answerRecruitingQuestion);
document.querySelector('#recruiting-candidate-form').addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); body.job_id=selectedRecruitingJobId(); const payload=await post('/api/recruiting/candidates/import',body); if(payload.ok) pendingImportedResumePath=body.resume_path; rememberRequestError(recruitingActionState,payload); await refresh(); }});
document.querySelector('#recruiting-assess-form').addEventListener('submit', async event => {{ event.preventDefault(); const body=Object.fromEntries(new FormData(event.currentTarget)); const payload=await post('/api/recruiting/assess',body); rememberRequestError(recruitingActionState,payload); await refresh(); }});
fetch('/api/recruiting/contexts').then(async response => {{ const payload=await response.json(); if(payload.ok) renderRecruitingContexts(payload.data); }}).then(() => refresh()).catch(() => {{}});
refresh(); window.setInterval(refresh, 2000);
</script>
<script>
{_PRODUCT_WORKBENCH_SCRIPT}
</script>
</body></html>"""
