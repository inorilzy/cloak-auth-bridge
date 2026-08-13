# Cloak Browser Auth

一个本地认证与调试桥：Chrome MV3 扩展在配对后，按本地登记的站点配置采集 Cookies 和 `localStorage`，通过独立的本机认证服务注入 CloakBrowser 持久 Profile。

推荐按职责运行三个组件：

1. `python -m cloak_browser_auth serve`：唯一常驻的扩展认证服务，监听 `ws://127.0.0.1:17321`
2. `python -m cloak_browser_auth mcp`：可随 IDE 启停的 stdio 客户端
3. Cloak Holder：按需启动并独占浏览器 Profile；MCP 断开不会关闭浏览器

只有显式调用 `cloak_debug_close(profile_id=..., confirm=true)` 或手动关闭浏览器窗口才结束 Holder。
因此 `cloak_debug_open` 要求独立的 `serve` 已运行；Holder 由该常驻进程发起，不挂在 MCP 的进程树下。

扩展安装时默认具备 `https://*/*` 读取权限，不再要求逐站点“授权并加入白名单”。真正的站点范围由 `sites/` 与 `profiles.json` 双重约束。MCP 工具只返回数量和验证结果，原始 Cookie/Token 不进入 LLM 上下文。

## 安装（和 multi-search 一样走 GitHub + uvx）

仓库：

https://github.com/inorilzy/cloak-browser-auth

### Codex / CLI（推荐）

```powershell
codex mcp add cloak-browser-auth -- uvx --from git+https://github.com/inorilzy/cloak-browser-auth.git cloak-browser-auth-mcp
```

或写入 `~/.codex/config.toml`：

```toml
[mcp_servers.cloak-browser-auth]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/inorilzy/cloak-browser-auth.git",
  "cloak-browser-auth-mcp"
]
startup_timeout_sec = 60
tool_timeout_sec = 180
enabled = true
```

MCP 启动前，另开终端常驻认证服务：

```powershell
uvx --from git+https://github.com/inorilzy/cloak-browser-auth.git cloak-browser-auth serve
```

`uvx` 会从 GitHub 拉取包。首次运行会在用户目录播种配置：

```text
~/.cloak-browser-auth/
  sites/
  profiles.json
  extension/          # 给 Chrome 加载
  profiles/           # Cloak 持久登录态
  .auth/
```

也可用环境变量指定数据目录：

```powershell
$env:CLOAK_BROWSER_AUTH_HOME = "D:\cloak-auth-data"
```

### 从源码本地开发

```powershell
git clone https://github.com/inorilzy/cloak-browser-auth.git
cd cloak-browser-auth
.\scripts\setup.ps1
```

先在独立终端启动认证服务：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth serve
```

本地 Codex 可直接使用仓库内 `.mcp.json`：

```json
{
  "mcpServers": {
    "cloak-browser-auth": {
      "command": ".venv/Scripts/python.exe",
      "args": ["-m", "cloak_browser_auth", "mcp"]
    }
  }
}
```

Cursor 可用 `.cursor/mcp.json`。两者都只启动 MCP 客户端，不占用 `17321`；四个 `auth_*` 工具通过 `ws://127.0.0.1:17321/auth` 调用认证服务。

MCP 与认证服务默认复用本机 DPAPI secret；需要覆盖时，两端设置相同的 `CLOAK_BROWSER_AUTH_CLIENT_TOKEN`。

## 推荐运行方式：daemon + MCP

长期运行：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth serve
```

IDE 按需启动：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth mcp
```

成功后：

- 扩展可连 `ws://127.0.0.1:17321`
- Agent 可直接调下方工具
- MCP 重启不会断开扩展，也不会关闭已打开的 CloakBrowser
- 不要重复启动 `serve`；`17321` 只允许一个认证服务占用

### 连接扩展（默认免 Token）

默认 **本机 loopback trust**：扩展只要连 `ws://127.0.0.1:17321`，无需粘贴 Token。

1. 启动独立的 `serve`（监听 17321）
2. Chrome 加载 `extension/` 并打开弹窗
3. Token 留空，点「保存并重连」
4. 顶部显示「已连接」即可

可选加固：设置环境变量 `CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1` 后，再用 `.\scripts\pair.ps1` 复制 Token 粘贴到扩展。Token 不要发给 LLM。

### 日常流程

```text
独立 serve 常驻，扩展自动连上本机 WS（免 Token）
  -> IDE 启动 cloak-browser-auth MCP
  -> auth_sync_to_cloak
  -> cloak_debug_open（启动或复用独立 Cloak Holder）
  -> 调试：navigate_page / list_network_requests /
     search_in_sources / set_breakpoint_on_text / evaluate_script ...
  -> MCP 可退出；浏览器继续保留
  -> 仅在确实结束时 cloak_debug_close(profile_id, confirm=true)
```

## MCP 工具

### 认证桥

- `auth_list_sites` — 站点列表 + 扩展是否已连接
- `auth_sync_to_cloak` — 从 Chrome 扩展采集并导入 Cloak（`mode=merge|replace`）
- `auth_verify_cloak` — 验证 Profile 登录态
- `auth_clear_cloak` — 清理（必须 `confirm=true`）

### Cloak 会话

- `cloak_debug_open` — 启动或复用独立的 headed Cloak Holder（默认 `shared-main`）
- `cloak_debug_tab` / `cloak_debug_list` / `cloak_debug_status` / `cloak_debug_close(profile_id, confirm=true)`

### 内置逆向工具（Python 重写 js-reverse 工具面）

会话：

- `reverse_attach` / `reverse_detach` / `reverse_status`

页面与导航：

- `select_page` / `new_page` / `navigate_page` / `select_frame` / `click_element` / `take_screenshot`

脚本分析：

- `list_scripts` / `get_script_source` / `save_script_source` / `search_in_sources`

断点与执行：

- `set_breakpoint_on_text` / `break_on_xhr` / `remove_breakpoint` / `list_breakpoints`
- `get_paused_info` / `pause_or_resume` / `step`

网络与 WebSocket：

- `list_network_requests` / `clear_network_requests` / `get_request_initiator` / `get_websocket_messages`

状态与检查：

- `list_console_messages` / `evaluate_script` / `clear_site_data`

这些工具通过 Holder 控制 `cloak_debug_open` 启动的同一 Cloak 会话；MCP 只是客户端，其退出不会结束浏览器。

统一工作流：

```text
auth_sync_to_cloak(site, profile)
cloak_debug_open(profile, urls=[...])
list_network_requests / search_in_sources / evaluate_script / ...
cloak_debug_close(profile, confirm=true)
```

同步示例：

```json
{
  "name": "auth_sync_to_cloak",
  "arguments": {
    "site_id": "xiaohongshu-main",
    "target_profile": "shared-main",
    "mode": "merge"
  }
}
```

打开 Cloak 供 js-reverse 挂接：

```json
{"name": "cloak_debug_open", "arguments": {"profile_id": "shared-main", "url": ["https://www.xiaohongshu.com"]}}
{"name": "cloak_debug_status", "arguments": {}}
{"name": "cloak_debug_close", "arguments": {"profile_id": "shared-main", "confirm": true}}
```

## 站点配置

站点范围只在本地配置文件维护，扩展弹窗不再登记站点。

```text
sites/bilibili.json   -> bilibili-main
sites/x.json          -> x-main
sites/youtube.json    -> youtube-main

profiles.json
  bilibili-main / x-main / youtube-main   (dedicated)
  shared-main                            (多站调试共用)
```

新增站点：在 `sites/` 增加 JSON，并在 `profiles.json` 映射允许的 Profile；多站联调把 site id 加进 `shared-main.allowedSites`。

第一次真正同步时，`cloakbrowser` 可能下载 Chromium 二进制。Profile 默认有头可开；注入后通过站点 `verify` 检查登录状态。

## 架构

```text
Chrome 扩展
    │ WebSocket 127.0.0.1:17321
    ▼
cloak_browser_auth serve        ← 独立认证服务，唯一占用 17321
    ├─ /auth holder_open ── spawn ── Cloak Holder  ← 唯一浏览器 owner
    │                                  └─ cloakbrowser → profiles/
    ▲
    │ ws://127.0.0.1:17321/auth
IDE ── stdio ── cloak_browser_auth mcp   ← 可重启客户端
                         └─ Playwright connect → Cloak Holder
```

认证服务应独立运行：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth serve
```

MCP、Holder 与认证服务互不拥有对方的生命周期。断开 MCP 只断开客户端；关闭浏览器必须显式执行 `cloak_debug_close(profile_id=..., confirm=true)` 或手动关窗。

## 调试 CLI（可选）

与 MCP 调试工具等价，便于终端手调：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-open --profile shared-main --url https://www.youtube.com
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-tab https://x.com/home
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-list
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-close
```

默认 Holder 控制端口：`127.0.0.1:19333`；状态文件：`.auth/debug-session.json`。该端口不是公开 CDP 端点。

## 安装 Chrome 扩展

1. Chrome 打开 `chrome://extensions`，启用“开发者模式”。
2. “加载已解压的扩展程序”，选择本仓库 `extension` 目录。
3. 确保独立的 `cloak-browser-auth serve` 正在运行。
4. `.\scripts\pair.ps1`，在扩展里粘贴 Token 并保存，确认“已连接”。

扩展默认申请 `https://*/*`，只支持 HTTPS，只允许连接 `ws://127.0.0.1`。

## WebSocket 协议（扩展桥）

连接后扩展先发 challenge；服务端回 `hello_challenge`；扩展回 `hello_response`；服务端 `hello_ack` 后可采集。

完成认证后服务端按注册表请求：

```json
{
  "id": "0192f0cb-1234",
  "type": "capture_auth",
  "site_id": "example-main",
  "cookie_domains": ["example.com"],
  "origins": ["https://www.example.com"],
  "nonce": "at-least-16-random-url-safe-characters"
}
```

扩展校验范围后采集并返回 `capture_auth_result`。`payload` 仅允许服务端内存消费，禁止进日志 / MCP result / LLM。

## 自检

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth doctor
.\.venv\Scripts\python.exe -m pytest -q
npm test
```

## 安全不变量

- Token：`.auth/pairing-token.dpapi`，只经 `pair.ps1` 进剪贴板
- Cookie/localStorage：仅在本机扩展、认证服务与同步流程内存中流转，不落日志、不进工具返回
- WebSocket：只绑 `127.0.0.1`
- 站点范围：daemon 注册表为第二道白名单
- Free Cloak：同时 1 个 browser；多站用 `shared-main` + 多 tab

## 开发校验

```powershell
npm test
Get-ChildItem extension -Filter *.js | ForEach-Object { node --check $_.FullName }
```

`extension/` 可直接被 Chrome 以未打包扩展加载。
