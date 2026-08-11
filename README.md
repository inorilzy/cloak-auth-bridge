# Cloak Auth Bridge

一个本地认证桥：Chrome MV3 扩展在配对后，按本地登记的站点配置采集 Cookies 和 `localStorage`，通过本机 WebSocket 交给 **同一个 MCP 进程**，再注入 CloakBrowser 持久 Profile。

**推荐形态：MCP = 唯一常驻入口。**  
`python -m cloak_auth_bridge mcp` 同时提供：

1. MCP stdio 工具（给 IDE / Agent）
2. 扩展桥 `ws://127.0.0.1:17321`
3. Cloak Profile 导入 / 校验 / 调试会话

不需要再长期单独跑 `serve` / `scripts\start.ps1` 守护进程。

扩展安装时默认具备 `https://*/*` 读取权限，不再要求逐站点“授权并加入白名单”。真正的站点范围由 `sites/` 与 `profiles.json` 双重约束。MCP 工具只返回数量和验证结果，原始 Cookie/Token 不进入 LLM 上下文。

## 安装（和 multi-search 一样走 GitHub + uvx）

仓库：

https://github.com/inorilzy/cloak-auth-bridge

### Codex / CLI（推荐）

```powershell
codex mcp add cloak-auth-bridge -- uvx --from git+https://github.com/inorilzy/cloak-auth-bridge.git@v0.2.0 cloak-auth-bridge-mcp
```

或写入 `~/.codex/config.toml`：

```toml
[mcp_servers.cloak-auth-bridge]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/inorilzy/cloak-auth-bridge.git@v0.2.0",
  "cloak-auth-bridge-mcp"
]
startup_timeout_sec = 60
tool_timeout_sec = 180
enabled = true
```

`uvx` 会从 GitHub 拉取包并启动 MCP。首次运行会在用户目录播种配置：

```text
~/.cloak-auth-bridge/
  sites/
  profiles.json
  extension/          # 给 Chrome 加载
  profiles/           # Cloak 持久登录态
  .auth/
```

也可用环境变量指定数据目录：

```powershell
$env:CLOAK_AUTH_BRIDGE_HOME = "D:\cloak-auth-data"
```

### 从源码本地开发

```powershell
git clone https://github.com/inorilzy/cloak-auth-bridge.git
cd cloak-auth-bridge
.\scripts\setup.ps1
.\.venv\Scripts\python.exe -m cloak_auth_bridge mcp
```

Cursor 本地开发可用仓库内 `.cursor/mcp.json`。

## 唯一推荐入口：MCP

`cloak-auth-bridge-mcp` / `python -m cloak_auth_bridge mcp` 同时提供：

1. MCP stdio 工具
2. 扩展桥 `ws://127.0.0.1:17321`
3. Cloak Profile 导入 / 校验 / 调试会话

请让 IDE/Codex 加载 MCP 后重启。成功后：

- 扩展可连 `ws://127.0.0.1:17321`
- Agent 可直接调下方工具
- **不要**再开独立 `serve`，否则会抢端口

### 连接扩展（默认免 Token）

默认 **本机 loopback trust**：扩展只要连 `ws://127.0.0.1:17321`，无需粘贴 Token。

1. IDE 启用本项目 MCP（会监听 17321）
2. Chrome 加载 `extension/` 并打开弹窗
3. Token 留空，点「保存并重连」
4. 顶部显示「已连接」即可

可选加固：设置环境变量 `CLOAK_AUTH_REQUIRE_TOKEN=1` 后，再用 `.\scripts\pair.ps1` 复制 Token 粘贴到扩展。Token 不要发给 LLM。

### 日常流程

```text
IDE 启动 MCP（cloak-auth-bridge + js-reverse-cloak-auth）
  -> 扩展自动连上本机 WS（免 Token）
  -> auth_list_sites 确认 source_connected=true
  -> auth_sync_to_cloak
  -> cloak_debug_open（启动 Cloak + CDP :9333）
  -> 用 js-reverse-cloak-auth 做抓包/断点/脚本逆向
  -> cloak_debug_close
```

## MCP 工具

认证桥：

- `auth_list_sites` — 站点列表 + 扩展是否已连接
- `auth_sync_to_cloak` — 从 Chrome 扩展采集并导入 Cloak（`mode=merge|replace`）
- `auth_verify_cloak` — 验证 Profile 登录态
- `auth_clear_cloak` — 清理（必须 `confirm=true`）

Cloak 会话（给 js-reverse 挂接调试）：

- `cloak_debug_open` — 打开 headed Cloak（默认 `shared-main`）并暴露 CDP `http://127.0.0.1:9333`
- `cloak_debug_tab` — 同一窗口再开 HTTPS tab
- `cloak_debug_list` / `cloak_debug_status` / `cloak_debug_close`

### 与 js-reverse-mcp 组合（推荐）

`js-reverse-mcp` 自带完整 CDP 调试工具；`--cloak` 会**自己再起一个** Cloak，和 auth profile 不是同一进程。  
正确组合是：

1. **auth 桥负责**：同步登录态 + 启动 Cloak（`cloak_debug_open`）
2. **js-reverse 负责**：挂到该 CDP 做调试（`--browserUrl`，**不要**再加 `--cloak`）

Codex 示例：

```toml
[mcp_servers.cloak-auth-bridge]
command = "uvx"
args = ["--from", "git+https://github.com/inorilzy/cloak-auth-bridge.git@v0.2.0", "cloak-auth-bridge-mcp"]

[mcp_servers.js-reverse-cloak-auth]
command = "npx"
args = ["-y", "js-reverse-mcp@latest", "--browserUrl", "http://127.0.0.1:9333"]
```

工作流：

```text
auth_sync_to_cloak(site, profile)
cloak_debug_open(profile, urls=[...])   # 返回 cdp_http / js_reverse 提示
# 然后用 js-reverse-cloak-auth：
#   navigate_page / list_network_requests / search_in_sources /
#   set_breakpoint_on_text / evaluate_script / take_screenshot ...
cloak_debug_close
```

注意：

- Free 计划同时只能 1 个 browser：先 `cloak_debug_open`，再让 js-reverse attach，不要并行再 `--cloak` 起第二个。
- `cloak_debug_open` 占用 profile 目录锁时，不要同时 CLI 再 launch 同一 profile。
- `js-reverse` 的 `--cloak` 与 `--browserUrl` 互斥；挂接模式只用 `--browserUrl`。

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
{"name": "cloak_debug_close", "arguments": {}}
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
IDE MCP client
    │ stdio
    ▼
cloak_auth_bridge mcp          ← 唯一常驻
    ├─ WebSocket 127.0.0.1:17321  ← Chrome 扩展
    ├─ AuthService sync/verify
    └─ cloakbrowser → profiles/
```

独立 `serve` 仅作应急（无 MCP 客户端时）：

```powershell
.\.venv\Scripts\python.exe -m cloak_auth_bridge serve
```

有 MCP 时不要并行 `serve`。

`scripts\start.ps1` 现在只打印 MCP 用法并跑 `doctor`，不再默认拉起长期 serve。

## 调试 CLI（可选）

与 MCP 调试工具等价，便于终端手调：

```powershell
.\.venv\Scripts\python.exe -m cloak_auth_bridge debug-open --profile shared-main --url https://www.youtube.com
.\.venv\Scripts\python.exe -m cloak_auth_bridge debug-tab https://x.com/home
.\.venv\Scripts\python.exe -m cloak_auth_bridge debug-list
.\.venv\Scripts\python.exe -m cloak_auth_bridge debug-close
```

默认 CDP：`127.0.0.1:9333`；状态文件：`.auth/debug-session.json`。

## 安装 Chrome 扩展

1. Chrome 打开 `chrome://extensions`，启用“开发者模式”。
2. “加载已解压的扩展程序”，选择本仓库 `extension` 目录。
3. 确保 IDE 已启动本项目的 `cloak-auth-bridge` MCP（或临时 `serve`）。
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
.\scripts\start.ps1
.\.venv\Scripts\python.exe -m cloak_auth_bridge doctor
.\.venv\Scripts\python.exe -m pytest -q
npm test
```

## 安全不变量

- Token：`.auth/pairing-token.dpapi`，只经 `pair.ps1` 进剪贴板
- Cookie/localStorage：仅扩展与 MCP 进程内存，不落日志、不进工具返回
- WebSocket：只绑 `127.0.0.1`
- 站点范围：daemon/MCP 注册表为第二道白名单
- Free Cloak：同时 1 个 browser；多站用 `shared-main` + 多 tab

## 开发校验

```powershell
npm test
Get-ChildItem extension -Filter *.js | ForEach-Object { node --check $_.FullName }
```

`extension/` 可直接被 Chrome 以未打包扩展加载。
