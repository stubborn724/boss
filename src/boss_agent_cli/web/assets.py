"""本地招聘控制台的静态页面。

页面不依赖 CDN 或第三方脚本，避免将本地任务状态、导出路径或候选人信息发送到
外部服务。动态区域只读取后端白名单元数据，不会通用渲染任意 JSON。
"""

from __future__ import annotations


def render_console_page(session_token: str) -> str:
	"""渲染带临时写请求令牌的响应式本地控制台页面。"""
	return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 招聘控制台</title>
<style>
:root {{ color-scheme: dark; --bg:#07111f; --panel:#101d2e; --line:#2a3b52; --text:#edf4fa; --muted:#a9bac9; --accent:#33d17a; --accent-dark:#0f9c52; --danger:#ff7b72; --warn:#ffc857; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-width:320px; background:var(--bg); color:var(--text); font-family:"Microsoft YaHei", "Segoe UI", sans-serif; line-height:1.5; }}
button,input {{ font:inherit; }}
button {{ cursor:pointer; border:0; border-radius:6px; min-height:42px; padding:0 16px; color:#062011; background:var(--accent); font-weight:700; transition:background-color 180ms ease, opacity 180ms ease; }}
button:hover:not(:disabled) {{ background:#55e592; }} button:disabled {{ cursor:not-allowed; opacity:.48; }}
button:focus-visible,input:focus-visible {{ outline:3px solid #8dc9ff; outline-offset:2px; }}
.shell {{ max-width:1120px; margin:0 auto; padding:28px 24px 48px; }}
.topbar {{ display:flex; align-items:flex-start; justify-content:space-between; gap:20px; border-bottom:1px solid var(--line); padding-bottom:22px; }}
h1 {{ margin:0; font-size:24px; font-weight:700; letter-spacing:0; }} h2 {{ margin:0; font-size:17px; }}
.subtle,.hint {{ color:var(--muted); }} .subtle {{ margin:7px 0 0; }} .badge {{ flex:0 0 auto; border:1px solid var(--line); border-radius:999px; padding:5px 10px; color:var(--muted); font-size:13px; }}
.grid {{ display:grid; grid-template-columns:minmax(0,1.22fr) minmax(290px,.78fr); gap:18px; margin-top:20px; }}
.panel {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:20px; }}
.status {{ display:grid; grid-template-columns:1fr auto; align-items:center; gap:16px; }} .label {{ color:var(--muted); font-size:13px; }} .state {{ margin:3px 0 0; font-size:18px; font-weight:700; }}
.status-list {{ display:grid; gap:12px; margin:18px 0 0; }} .status-item {{ border-left:3px solid var(--line); padding-left:12px; }} .status-item.good {{ border-color:var(--accent); }} .status-item.warn {{ border-color:var(--warn); }} .status-item.error {{ border-color:var(--danger); }}
.form {{ display:grid; gap:15px; margin-top:18px; }} label {{ display:grid; gap:6px; color:var(--muted); font-size:13px; }} input {{ width:100%; min-height:42px; padding:8px 10px; color:var(--text); background:#0a1626; border:1px solid var(--line); border-radius:5px; }}
.actions {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }} .secondary {{ color:var(--text); background:transparent; border:1px solid var(--line); }} .secondary:hover:not(:disabled) {{ background:#1a2c42; }}
.result {{ margin-top:18px; border-top:1px solid var(--line); padding-top:16px; }} dl {{ display:grid; grid-template-columns:110px minmax(0,1fr); gap:7px 12px; margin:10px 0 0; }} dt {{ color:var(--muted); }} dd {{ margin:0; overflow-wrap:anywhere; }}
.notice {{ margin:16px 0 0; padding:11px 12px; border-radius:5px; background:#10253a; color:var(--muted); }} .notice.error {{ color:#ffd4d0; background:#40201f; }} .notice.warn {{ color:#ffdfa1; background:#3c311b; }}
.sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }}
@media (max-width:760px) {{ .shell {{ padding:20px 15px 32px; }} .topbar,.status {{ display:grid; }} .grid {{ grid-template-columns:1fr; }} .badge {{ justify-self:start; }} }}
@media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ transition:none !important; }} }}
</style>
</head>
<body>
<main class="shell">
<header class="topbar"><div><h1>BOSS 招聘控制台</h1><p class="subtle">本地运行。登录确认始终在 BOSS 官方页面完成。</p></div><span class="badge">仅监听本机</span></header>
<div class="grid">
<section class="panel" aria-labelledby="login-heading"><div class="status"><div><div class="label" id="login-heading">登录状态</div><p class="state" id="login-state">正在读取</p></div><button id="login-button" type="button">打开 BOSS 登录页</button></div><div id="login-detail" class="notice" aria-live="polite">请先完成登录。</div></section>
<section class="panel" aria-labelledby="mode-heading"><div class="label" id="mode-heading">操作模式</div><p class="state" id="mode-state">正在读取</p><div id="mode-detail" class="notice" aria-live="polite"></div></section>
<section class="panel" aria-labelledby="download-heading"><h2 id="download-heading">下载单份候选人简历</h2><p class="hint">结果只展示文件元数据，不会在浏览器中显示简历正文。</p><form id="download-form" class="form"><label>候选人 Geek ID<input name="geek_id" required autocomplete="off"></label><label>职位 Job ID<input name="job_id" required autocomplete="off"></label><label>Security ID<input name="security_id" required autocomplete="off"></label><label>导出目录（可选）<input name="output_dir" autocomplete="off" placeholder="默认使用本地数据目录"></label><div class="actions"><button id="download-button" type="submit">下载到本地</button><span id="download-state" class="hint" aria-live="polite"></span></div></form><div id="download-result" class="result" hidden></div></section>
<aside class="panel" aria-labelledby="guide-heading"><h2 id="guide-heading">运行说明</h2><div class="status-list"><div class="status-item"><strong>1. 登录</strong><br><span class="hint">点击按钮后，在打开的官方页面扫码或确认登录。</span></div><div class="status-item"><strong>2. 启用研究模式</strong><br><span class="hint">下载候选人简历前，需在终端显式运行 <code>boss config set operating_mode research</code>。</span></div><div class="status-item"><strong>3. 主动下载</strong><br><span class="hint">填写从平台页面获得的三个定位参数后，点击下载。</span></div></div></aside>
</div>
</main>
<script>
const token = {session_token!r};
const loginButton = document.querySelector('#login-button');
const downloadButton = document.querySelector('#download-button');
const loginState = document.querySelector('#login-state');
const loginDetail = document.querySelector('#login-detail');
const modeState = document.querySelector('#mode-state');
const modeDetail = document.querySelector('#mode-detail');
const downloadState = document.querySelector('#download-state');
const resultBox = document.querySelector('#download-result');
let current = null;
function stateText(state) {{ return {{idle:'未登录',running:'进行中',succeeded:'已完成',failed:'失败',blocked:'已阻断'}}[state] || '未知'; }}
async function post(path, body) {{ const response = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json','X-Boss-Web-Token':token}}, body:JSON.stringify(body || {{}})}}); return response.json(); }}
function renderResult(result) {{ if (!result) {{ resultBox.hidden=true; return; }} resultBox.hidden=false; if (result.state === 'succeeded') {{ const r=result.result; resultBox.innerHTML='<h2>最近下载</h2><dl><dt>候选人</dt><dd></dd><dt>文件</dt><dd></dd><dt>路径</dt><dd></dd><dt>字节数</dt><dd></dd><dt>段落</dt><dd></dd></dl>'; const values=[r.candidate_name||'（无）',r.filename,r.path,String(r.bytes_written),r.sections.join('、')||'（无）']; resultBox.querySelectorAll('dd').forEach((node,index)=>node.textContent=values[index]); }} else if (result.error) {{ resultBox.textContent=result.error.message; resultBox.className='result notice error'; }} }}
function render(data) {{ current=data; const login=data.login; const download=data.download; loginState.textContent=stateText(login.state); loginDetail.textContent=login.error ? login.error.message : (login.state==='succeeded' ? '登录态已就绪。' : '点击按钮将在官方 BOSS 页面中开始登录。'); loginDetail.className='notice'+(login.error?' error':''); loginButton.disabled=login.state==='running'; loginButton.textContent=login.state==='running'?'等待官方登录确认':'打开 BOSS 登录页'; modeState.textContent=data.operating_mode; const allowed=data.operating_mode==='research'; modeDetail.textContent=allowed?'研究模式已显式启用，可由你主动下载单份简历。':'默认低风险模式会阻断下载。请先在终端显式启用 research 模式后重新打开此页面。'; modeDetail.className='notice '+(allowed?'':'warn'); const canDownload=allowed && login.state==='succeeded' && download.state!=='running'; downloadButton.disabled=!canDownload; downloadState.textContent=download.state==='running'?'正在下载，请稍候…':(!allowed?'当前模式不允许下载':(login.state!=='succeeded'?'请先登录':'')); renderResult(download); }}
async function refresh() {{ try {{ const response=await fetch('/api/state'); const payload=await response.json(); if(payload.ok) render(payload.data); }} catch (_) {{ loginDetail.textContent='无法连接本地控制台，请检查服务是否仍在运行。'; loginDetail.className='notice error'; }} }}
loginButton.addEventListener('click', async () => {{ const payload=await post('/api/login'); if(!payload.ok) {{ loginDetail.textContent=payload.error.message; loginDetail.className='notice error'; }} await refresh(); }});
document.querySelector('#download-form').addEventListener('submit', async event => {{ event.preventDefault(); const form=new FormData(event.currentTarget); const payload=await post('/api/resume-download', Object.fromEntries(form)); if(!payload.ok) {{ downloadState.textContent=payload.error.message; }} await refresh(); }});
refresh(); window.setInterval(refresh, 2000);
</script>
</body></html>"""
