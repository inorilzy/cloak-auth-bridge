# CloakBrowser 长期 Holder：最佳实践与迁移建议

日期：2026-08-12
适用版本：`cloakbrowser 0.5.6`、`playwright 1.62.0`、`mcp 1.29.0`

## 结论

当前项目应收敛成一个模型：**每个 Cloak Profile 只由一个长期、独立的 Browser Holder 拥有；MCP stdio 进程只连接，不拥有浏览器。**

首选数据面不是公开 CDP，也不是继续扩展自制 HTTP RPC，而是 Playwright 1.59+ 的原生 `browser.bind()` / `browser_type.connect()`：Holder 启动 persistent context 后，将 Browser 绑定到仅 loopback 可访问的随机 WebSocket endpoint；MCP 每次启动后连接该 endpoint。Playwright 官方说明 `browser.bind()` 支持 named pipe 或 WebSocket，并支持多个客户端。[Playwright Python `Browser.bind`](https://playwright.dev/python/docs/api/class-browser#browser-bind)；[Playwright Python 1.59 release notes](https://playwright.dev/python/docs/release-notes#version-159)

管理面只保留少量、有认证的命令：`ensure/open`、`status`、`close(confirm=true)`。`detach` 只释放当前 MCP 的 Playwright client/CDP sessions，绝不能关闭 persistent context 或 Holder。用户手动关闭浏览器窗口时，Holder自然退出。

## 官方事实与设计约束

### 1. Persistent context 就是浏览器生命周期边界

`launch_persistent_context(user_data_dir)` 返回该浏览器唯一的 persistent `BrowserContext`；关闭这个 context 会自动关闭浏览器。同一个 User Data Directory 也不能同时启动多个浏览器实例。因此 profile 必须有唯一 owner，任何普通客户端都不应调用 owner context 的 `close()`。[Playwright Python `launch_persistent_context`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)

这与当前 `cloakbrowser` 包装一致：`launch_persistent_context_async()` 自己启动 Playwright，且给 `context.close()` 加了清理逻辑，关闭 context 时还会停止 owner 的 Playwright 实例（本机 `.venv/Lib/site-packages/cloakbrowser/browser.py`）。所以把这个 context 放在 MCP stdio 进程里，MCP 生命周期就天然与浏览器耦合。

### 2. MCP stdio 是短期、由客户端拥有的子进程

MCP stdio 规范规定：客户端启动 server 子进程；关停时客户端应关闭 server stdin，等待退出，之后按需发送 `SIGTERM` / `SIGKILL`。所以 stdio server 不适合拥有用户期望继续显示和操作的长期 GUI 资源。[MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#stdio)；[MCP lifecycle / shutdown](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle#shutdown)

因此，MCP 退出、任务切换、客户端重载和升级都只能导致 **detach**，不能导致浏览器关闭。长期 Holder 必须是 stdio server 之外的独立进程。

### 3. 优先 Playwright 原生协议，CDP 只作兼容出口

Playwright 官方明确称 `connect_over_cdp()` 相比 `browser_type.connect()` 是“显著更低保真”的连接，复杂能力可能有问题；它只支持 Chromium，并警告外部启动参数不一致会破坏部分 Playwright 功能。[Playwright Python `connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)

相反，Playwright 1.59+ 提供：

- `browser.bind(title)`：默认把已启动浏览器绑定到 named pipe；指定 host/port 才使用 WebSocket；
- `browser_type.connect(endpoint)`：以 Playwright 原生协议连接；
- 多客户端同时连接；
- 连接端与 owner 的 Playwright 主、次版本必须匹配（如 `1.62.x` 对 `1.62.x`）。

来源：[Browser API](https://playwright.dev/python/docs/api/class-browser#browser-bind)、[BrowserType.connect](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect)、[Playwright Python release notes](https://playwright.dev/python/docs/release-notes#version-159)。

这使 Holder 无需复制 `page`、network、console、CDP session 等几十项 Playwright 能力到自制 RPC。MCP 连接后仍可用 `context.new_cdp_session(page)` 做 Chromium 专属断点、Network/Debugger domain 操作。[Playwright Python `BrowserContext.new_cdp_session`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-new-cdp-session)

### 4. Remote debugging 是高权限控制面

Chrome 官方确认，攻击者会利用 remote debugging 提取 Cookie。Chrome 136 起，`--remote-debugging-port` / `--remote-debugging-pipe` 在默认 Chrome data directory 上不再生效，必须配合非默认 `--user-data-dir`；官方也建议调试使用隔离的自定义数据目录。[Chrome for Developers: Changes to remote debugging switches](https://developer.chrome.com/blog/remote-debugging-port)

即使只绑定 `127.0.0.1`，本机其他进程仍可能调用开放端口。故默认不要发布 CDP TCP endpoint；若为第三方工具临时开启，必须：

- 使用 Cloak 专用 profile，绝不指向日常 Chrome `User Data`；
- 只监听 loopback、使用随机端口，并把 endpoint 当作 secret；
- 用完即撤销，不把 endpoint 写到普通日志或 MCP 返回值；
- 不把“loopback”当成认证。

### 5. Windows 上 Holder 必须运行在交互用户会话

不要把有头浏览器直接做成 Windows Service。Microsoft 说明 Vista 起服务不能直接与用户交互，服务位于 Session 0；推荐把 GUI 进程放到交互用户会话并通过 IPC 通信。[Microsoft: Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)

建议顺序：

1. **默认**：按需启动、脱离 MCP 生命周期的普通 per-user Holder；用户手动关窗或显式 `close` 才结束。
2. **需要登录后常驻**：Task Scheduler 使用 `TASK_LOGON_INTERACTIVE_TOKEN`，它只在已有交互登录会话中运行；或使用用户 Startup/`HKCU Run`。[Task Scheduler `Principal.LogonType`](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype)；[Run and RunOnce keys](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys)
3. **不建议**：Windows Service 直接启动有头浏览器。若以后确实需要 service，只让 service 做无 UI supervisor，再在交互用户会话启动独立 GUI agent。

不要把 Holder 放进任何带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 MCP/client Job Object；Microsoft 说明最后一个 Job handle 关闭会终止其中全部进程。`CREATE_NEW_PROCESS_GROUP` / `DETACHED_PROCESS` 也不保证从父 Job 脱离；`CREATE_BREAKAWAY_FROM_JOB` 仅在父 Job 允许 breakaway 时有效。[Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)；[Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)

## 推荐架构

```text
Chrome 登录态扩展
  └─ ws://127.0.0.1:17321（独立 Auth Bridge daemon）

Per-user Browser Holder（每个 profile 一个，唯一 owner）
  ├─ launch_persistent_context_async(profile_dir)
  ├─ 持有 Playwright + persistent BrowserContext
  ├─ browser.bind(profile/session title, host=127.0.0.1, port=0) → loopback WebSocket
  ├─ profile lock + session registry + health
  └─ 管理 IPC：ensure/status/close(confirm)
          ↑
MCP stdio（短生命周期、可重启）
  ├─ ensure holder
  ├─ playwright.chromium.connect(native endpoint)
  ├─ 页面/网络/Console/CDP Debugger 工具
  └─ EOF/退出 → detach only
```

### 为什么分成两条 IPC

- **Playwright 数据面**：原生 endpoint 负责页面、网络、脚本、截图、CDP session 等高带宽对象协议，不重造轮子。
- **管理面**：独立、极小的控制接口负责创建 Holder、健康检查和显式关闭。不要让普通 Playwright client 的 dispose/close 成为关闭 owner 的途径。

Windows 管理 IPC 可用受限 named pipe；若先保留 loopback HTTP，也必须加随机 bearer token、限制请求体、校验 `Origin/Host`、禁止 GET 执行变更，并让 `close` 要求一次性 capability 或 `confirm=true`。Windows 官方说明 named pipe 的默认 ACL 仍给 Everyone/Anonymous 读权限；要限制到当前用户/登录 SID，并拒绝远程客户端。[Named Pipe Security and Access Rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)；[Named Pipes](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipes)

注意：Playwright 自带 named pipe endpoint 的 ACL 是否满足本项目 threat model，应在 Windows 上实测；若不能自定义 DACL，就在其外层加仅当前用户可读的 endpoint registry 和认证管理面，或改用仅 loopback 的随机 WebSocket endpoint + secret header。不能因为“pipe/loopback 是本机”就默认可信。

## 状态与所有权规则

必须把规则写成不变量，而不是散落在分支里：

1. 一个 `profile_id` 同时最多一个 Holder，锁以 profile 为键，不以固定端口为键。
2. `open/ensure` 是幂等的：同 profile 已运行则复用并按需开标签；不同 profile 不互相关闭。
3. `detach` 永远不关闭 page、persistent context、browser 或 Holder，只清理当前客户端创建的 CDP sessions/listeners/内存索引。
4. `close` 是唯一程序化关闭入口，必须显式确认并指明 profile/session id；不能“顺手关闭 legacy holder”。
5. 用户关窗口、浏览器崩溃或 owner context `close` 才使 Holder结束；Holder写入最终状态并释放 profile lock。
6. session JSON 只是发现缓存，不是活性真相；活性以 authenticated ping + owner instance nonce 为准。PID 只作诊断，不能仅凭 PID 文件杀进程（PID 会复用）。
7. 每个连接建立后重新枚举 contexts/pages 并重建客户端临时监听器；历史 network/console/paused/breakpoint 状态若要跨 MCP 重启，就应由 Holder 持久采集，否则明确标注“从 attach 时开始”。
8. 写 profile 的 auth import/verify/clear 必须经过同一 Holder 串行执行，不能另起 persistent context 抢同一 User Data Directory。

## 当前实现的具体偏差

### 阻塞级

- `cloak_browser_auth/mcp_server.py:434-440` 的 `cloak_debug_open` 调用 `SESSION.open_profile()`，仍在 MCP stdio 进程里创建浏览器。
- `cloak_browser_auth/reverse_session.py:147-232` 明确由 MCP 直接持有 persistent context；`188-193` 还会关闭已有外部 Holder，违反唯一 owner 与复用原则。
- `cloak_browser_auth/reverse_session.py:321-349` 中 `reverse_detach` 对 owned context 调用 `context.close()`；接口语义会误关浏览器。
- `cloak_browser_auth/mcp_server.py:457-464` 的 `cloak_debug_close` 同时关闭 in-process session 和 external holder，目标不精确。

### 结构性

- `cloak_browser_auth/debug_session.py:332-400` 已有独立 Holder，但 `19333` HTTP 控制面只实现 `status/list/tab/evaluate/close`；完整调试状态仍在 `ReverseSession` 内存。
- `cloak_browser_auth/main.py:44-47` 把 MCP stdio 与扩展 WebSocket `17321` 绑在同一进程；MCP 重载会让扩展断开，已有 daemon 还会造成端口占用。Auth Bridge 应独立常驻，MCP 只做客户端。
- `cloak_browser_auth/cloak_profiles.py` 的 auth import/verify/clear 每次另起 persistent context。若目标 profile 已由 Holder 打开，会与 profile 唯一实例约束冲突；这些写操作应路由给 Holder。
- `debug-session.json` + PID + 固定 `19333` 适合原型，不适合作为多 profile、多实例的真实 registry。

## 最小迁移路径

### 阶段 1：先修生命周期，不重写工具

1. 让 `cloak_debug_open` 只调用 `ensure_holder(profile_id)`，删除 MCP 内 `launch_persistent_context_async()` 路径。
2. Holder 启动 persistent context 后取得 `context.browser`，调用 `browser.bind()`，将 endpoint、随机 instance id、profile id 写入当前用户可读的 session registry。
3. `ReverseSession` 改成 `browser_type.connect(endpoint)` 客户端；保留现有页面、network、console、CDP debugger 工具代码。
4. `reverse_detach` 只 detach CDP sessions 并断开 client；owner context 不可达或至少不调用其 `close()`。
5. `cloak_debug_close(profile_id, instance_id, confirm=true)` 单独走管理面；删除“关闭所有 legacy/in-process 对象”的行为。

### 阶段 2：拆 Auth Bridge

把 `17321` 扩展桥作为独立 per-user daemon。MCP 的 auth tools 通过本地认证 IPC 调用 daemon；MCP 重启不再抢端口，也不让 Chrome 扩展反复重连。

### 阶段 3：统一 profile 写入与恢复

1. auth sync/verify/clear 路由给对应 Holder，并按 profile 串行化。
2. 明确哪些调试状态跨 MCP 保存：建议 network/console 采用 Holder 端有界 ring buffer；breakpoint 配置可保存并在新 page attach 后重放；paused call-frame 属于瞬时状态，不做持久化承诺。
3. session registry 从单文件升级为 per-profile 原子文件或小型 SQLite；写入 endpoint 前先完成 bind 与 authenticated health，退出时按 instance id 条件删除，防止旧进程删新会话。

## 验收标准

- 打开 Bilibili 后关闭/重启 Codex 或 MCP stdio，浏览器窗口和页面继续存在。
- 新 MCP 在无 CDP TCP port 的情况下，通过 Playwright 原生 endpoint 重连并可执行现有全部工具。
- 调用 `reverse_detach` 后浏览器仍存在；只有指定 profile 的 `cloak_debug_close(..., confirm=true)` 或用户手动关窗会关闭。
- 同 profile 并发 `open` 只得到一个 Holder；不同 profile 可同时运行，互不关闭。
- `17321` 已占用时 MCP 仍能启动；扩展桥重启也不影响 Browser Holder。
- profile 已打开时执行 auth sync/verify/clear 不会启动第二个相同 User Data Directory 的浏览器。
- 普通本机未授权进程不能读取 endpoint registry、调用管理 `close` 或连接调试 endpoint。
- Holder 被强杀、浏览器崩溃、session 文件损坏、PID 复用时都不会误杀无关进程；下一次 `ensure` 能明确报错或安全恢复。

## 不推荐方案

- **MCP 直接拥有浏览器**：与 stdio 生命周期冲突，任务退出会带走 GUI。
- **只给现有 HTTP Holder 逐项补齐 30+ 工具**：复制 Playwright 对象协议、事件和并发语义，维护成本高且容易丢能力。
- **默认公开 CDP TCP 端口**：保真度低、攻击面大，Chrome 官方已专门收紧此路径。
- **用 Windows Service 承载有头浏览器**：Session 0 与交互桌面模型不匹配。
- **靠 detached flags 代替真正 supervisor/owner**：无法可靠对抗父 Job Object、客户端进程树清理和崩溃恢复。

最终判断：**值得重构，而且不需要推倒调试工具。最小而正确的核心改动，是把 `ReverseSession` 从 owner 改成 Playwright-native client，把 `debug_session` 提升为唯一 owner，再把关闭权限收窄到独立管理面。**
