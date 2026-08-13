# Cloak Browser Auth

一个本地认证与调试桥：Chrome MV3 扩展在配对后，按本地登记的站点配置采集 Cookies 和 `localStorage`，通过独立的本机认证服务注入 CloakBrowser 持久 Profile。

日常只装 MCP。IDE 启动时若 `127.0.0.1:17321` 空闲，MCP 自己带上扩展认证桥；已被占用则复用已有实例。

Cloak 窗口活在这个 MCP 进程里：关掉 IDE / 重载 MCP，窗口一起关。

`serve` 是可选项：只在希望 IDE 关掉后扩展仍保持连接时才单独常驻。

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

换电脑：加上这条 MCP 即可。第一次启动会播种 `~/.cloak-browser-auth/`，并在 `:17321` 空闲时自己打开认证桥。不必再另开终端跑 `serve`。

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

本地 Codex 可直接使用仓库内 `.mcp.json`。Cursor 用 `.cursor/mcp.json`。两者都只写 `mcp` 这一条命令。

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

第一个 MCP 进程占用 `17321` 并接待 Chrome 扩展；第二个 IDE 再开 MCP 时自动连上已有桥。默认复用本机 DPAPI secret；需要覆盖时两端设相同的 `CLOAK_BROWSER_AUTH_CLIENT_TOKEN`。

## 推荐运行方式

换机 / 日常：只启动 MCP。

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth mcp
```

成功后：

- 扩展连 `ws://127.0.0.1:17321`（由这个 MCP 或已有实例提供）
- Agent 可直接调下方工具
- 重载 MCP 会关闭 Cloak 窗口
- `:17321` 只允许一个认证桥；后启动的 MCP 自动复用

可选：希望 IDE 全关后扩展仍在线，再单独跑 `serve`。

### 连接扩展（默认免 Token）

默认 **本机 loopback trust**：扩展只要连 `ws://127.0.0.1:17321`，无需粘贴 Token。

1. 在 IDE 里启用 cloak-browser-auth MCP（它会打开 17321）
2. Chrome 加载 `extension/` 并打开弹窗
3. Token 留空，点「保存并重连」
4. 顶部显示「已连接」即可

可选加固：设置环境变量 `CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1` 后，再用 `.\scripts\pair.ps1` 复制 Token 粘贴到扩展。Token 不要发给 LLM。

### 日常流程

```text
IDE 启动 cloak-browser-auth MCP（空闲则自带 :17321）
  -> Chrome 扩展连 ws://127.0.0.1:17321
  -> auth_sync_to_cloak
  -> cloak_debug_open（本进程打开 Cloak）
  -> 调试：navigate_page / list_network_requests / ...
  -> MCP 退出，窗口一起关
```

## MCP 工具

### 认证桥

- `auth_list_sites` — 站点列表 + 扩展是否已连接
- `auth_sync_to_cloak` — 从 Chrome 扩展采集并导入 Cloak（`mode=merge|replace`）
- `auth_verify_cloak` — 验证 Profile 登录态
- `auth_clear_cloak` — 清理（必须 `confirm=true`）

### Cloak 会话

- `cloak_debug_open` — 在本 MCP 进程启动或复用 headed CloakBrowser（默认 `shared-main`）
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

这些工具操作 `cloak_debug_open` 在本进程打开的同一 Cloak 窗口。MCP 退出时窗口一起关。

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

打开 Cloak：

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
MCP stdio
    ├─ 认证桥（:17321 空闲则嵌入，否则复用）
    └─ cloak_debug_open → 本进程 launch CloakBrowser → profiles/

可选：单独 serve 常驻同一端口，给无 IDE 场景用（不管浏览器）
```

关掉 MCP 会关 Cloak 窗口。也可以 `cloak_debug_close(profile_id=..., confirm=true)` 或手动关窗。


## 安装 Chrome 扩展

1. Chrome 打开 `chrome://extensions`，启用“开发者模式”。
2. “加载已解压的扩展程序”，选择本仓库 `extension` 目录，或用户目录里播种的 `~/.cloak-browser-auth/extension`。
3. 在 IDE 里启用 MCP（它会打开 `17321`），扩展弹窗 Token 留空，点「保存并重连」。
4. 只有启用了 `CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1` 时，才需要 `.\scripts\pair.ps1` 粘贴 Token。

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
