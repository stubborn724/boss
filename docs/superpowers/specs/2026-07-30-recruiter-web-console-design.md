# 招聘简历本地控制台设计

## 目标

为现有的 BOSS 招聘者简历下载能力提供一个仅绑定本机回环地址的 Web 控制台。用户能从页面启动登录、在 BOSS 官方页面完成扫码或确认、观察登录状态，并在显式研究模式下主动下载一份候选人简历到本地 Markdown 文件。

## 边界

- 登录认证复用 `AuthManager.login`，官方 BOSS 页面是唯一的凭据、二维码和验证码交互面。
- 控制台不读取、显示、记录或传输 Cookie、密码、验证码和简历正文。
- 简历下载仍只允许单候选人、单次用户点击触发；不加入 MCP、批量处理、自动分析或自动沟通。
- `operating_mode=research` 仍须由用户显式通过既有 CLI 配置设置。Web 控制台展示状态和阻断原因，但不能隐式或一键改变模式。
- 服务仅监听 `127.0.0.1`，以每次启动生成的会话令牌校验写请求的 `Origin` 与同源页面，降低本机跨站请求风险。

## 架构

新增 `boss_agent_cli.web` 包，职责按边界拆分：

1. `runtime.py` 持有配置、认证状态和任务串行锁。它启动后台登录任务，调用认证服务，并将异常转换为不含凭据的任务状态。
2. `app.py` 只定义 aiohttp 路由、请求验证和 JSON 响应。它不调用平台客户端、不渲染简历、不保存业务数据。
3. `assets.py` 返回单个静态 HTML/CSS/JavaScript 控制台。页面轮询状态，提交登录或下载请求，结果区域只使用元数据字段。
4. `commands/web.py` 是 CLI 入口。它读取既有 `data_dir`、平台、CDP 地址和配置，创建应用并启动本地服务器。
5. `commands/recruiter/resume_download_service.py` 从 Click 命令抽出取数、解析和导出的核心流程。CLI 和 Web 都调用它，以保持字段处理、原始响应隔离和原子写入的一致性。

## 流程

```text
页面点击登录
  -> POST /api/login
  -> Runtime 在后台调用 AuthManager.login
  -> 现有认证链路打开 BOSS 官方页面
  -> 页面 GET /api/state 轮询任务状态
  -> 用户在官方页面完成扫码/确认
  -> Runtime 保存既有 TokenStore 登录态并报告成功

页面点击下载
  -> POST /api/resume-download
  -> App 验证参数与 research 模式
  -> Runtime 串行调用 ResumeDownloadService
  -> recruiter adapter 获取、解析并原子写入 Markdown
  -> 页面仅得到路径、文件名、字节数、候选人名、段落和时间
```

## 接口

- `GET /`：控制台页面。
- `GET /api/state`：不含隐私数据的 `login`、`download`、`operating_mode` 状态。
- `POST /api/login`：启动一个登录任务；已有运行中任务时返回其状态，不重复打开浏览器。
- `POST /api/resume-download`：接收 `geek_id`、`job_id`、`security_id` 与可选 `output` / `output_dir`。非法或互斥参数返回 `INVALID_PARAM`；低风险模式返回 `COMPLIANCE_BLOCKED`；其他异常转换为既有错误码与脱敏消息。

所有 JSON 写请求必须携带服务器初始页注入的会话令牌和同源 `Origin`。服务器不会接受任意跨域来源，也不会开放监听到局域网。

## 异常与并发

登录和下载分别单飞：同类任务运行中复用状态而不重启任务。下载任务以单一运行锁保护平台访问和同名快照写入。认证失败、超时、平台错误和导出错误映射成可展示的错误对象；日志与响应中不含令牌、原始平台响应或简历正文。

## 用户界面

页面是紧凑的双栏运营工具：顶部展示登录与模式状态，左栏放下载表单，右栏放当前任务和最近一次结果。未登录或非研究模式时下载控件禁用并显示下一步。使用原生语义表单、可见焦点状态、错误文本、状态文本和 `prefers-reduced-motion`，适配窄屏单栏布局。

## 测试与验证

- 单元测试：登录任务单飞、状态脱敏、研究模式拦截、下载参数与服务结果映射。
- HTTP 测试：本地令牌/Origin 拦截、页面不泄露简历正文、状态与成功元数据响应。
- 回归测试：CLI `download-resume` 继续验证与服务共享同一导出结果。
- 手工冒烟：启动 `boss web`，检查本地页面在桌面与移动宽度不溢出；点击登录后确认官方 BOSS 页面被打开。实际扫码由用户完成，随后才可执行真实下载。
