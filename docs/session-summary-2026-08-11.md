# cookies2agent 会话总结（2026-08-11）

## 1. 目标

把 Chrome 登录态安全同步到 CloakBrowser 持久 Profile，供本地 Agent/MCP 使用；并解决多站点调试时 CloakBrowser Free「1 并发会话」的限制。

---

## 2. 本会话完成事项

### 2.1 端到端同步打通

| 站点 | Profile | cookies | origins | verified |
|---|---|---:|---:|---|
| `bilibili-main` | `profiles/bilibili-main` | 4 | 1 | true |
| `x-main` | `profiles/x-main` | 14 | 1 | true |
| 两者 merge | `profiles/shared-main` | 各自通过 | 各自通过 | true |

数据流：

```text
已登录 Chrome
  -> MV3 扩展按 daemon 下发的站点范围采集
  -> 本机 WebSocket（配对 + HMAC）
  -> AuthService 双重白名单校验
  -> CloakBrowser persistent profile
  -> verify（JSON 或最终 URL）
  -> MCP/CLI 只返回数量和布尔结果
```

### 2.2 取消扩展逐站授权

**原因：** 旧设计用 `optional_host_permissions`，每个站点都要在弹窗点「授权并加入白名单」。

**改动：**

- `extension/manifest.json`：改为 `host_permissions: ["https://*/*"]`
- 扩展弹窗只保留配对 Token，去掉站点白名单 UI
- 采集范围改由 daemon 在 `capture_auth` 请求里下发：

```json
{
  "type": "capture_auth",
  "site_id": "example-main",
  "cookie_domains": ["example.com"],
  "origins": ["https://www.example.com"],
  "nonce": "..."
}
```

- 扩展校验/规范化后直接采集，不再查本地 `sites` storage
- 真正边界仍在 daemon：`sites/*.json` + `profiles.json`

**注意：** 加载/更新扩展时 Chrome 可能弹一次主机权限确认；之后同步任意已登记站点都不用再点。

### 2.3 新增 x.com 站点

- 配置：`sites/x.json`
- Profile：`profiles.json` 中 `x-main`
- verify：x.com 没有 bilibili 那种简单 JSON 登录接口，因此扩展了 verify：

| 模式 | 字段 | 用途 |
|---|---|---|
| JSON | `jsonPath` + `equals` | bilibili `nav` API |
| URL | `finalUrlIncludes` / `finalUrlExcludes` | x.com `/home` 不落到 login |

示例（x.com）：

```json
{
  "id": "x-main",
  "cookieDomains": ["x.com", "twitter.com"],
  "origins": ["https://x.com"],
  "verify": {
    "url": "https://x.com/home",
    "finalUrlIncludes": ["x.com"],
    "finalUrlExcludes": ["/i/flow/login", "/login"]
  }
}
```

### 2.4 CloakBrowser 升级到 150

| 项 | 值 |
|---|---|
| Python 包 | `cloakbrowser==0.5.6` |
| 之前二进制 | `146.0.7680.177.5`（free/keyless） |
| 现在二进制 | **`150.0.7871.114.3-pro`** |
| License | Free plan（latest binary，**1 concurrent session**） |
| Key 存储 | `~/.cloakbrowser/license.key` |

要点：

- 150 不在 free/keyless 公开通道；GitHub 上是 `*-pro` 构建
- 需要 free/pro key 后走 Pro 下载
- 本机正确命令（PATH 里通常没有裸 `cloakbrowser`）：

```powershell
.\.venv\Scripts\python.exe -m cloakbrowser login <key>
.\.venv\Scripts\python.exe -m cloakbrowser update
.\.venv\Scripts\python.exe -m cloakbrowser info
```

### 2.5 Free「1 并发会话」含义与对策

**含义：** 同时只能有 **1 个 CloakBrowser browser 进程**，不是只能 1 个 tab。

| 可以 | 不行 |
|---|---|
| 1 个 browser + 多个 tab | 同时 launch 两个 browser/profile |
| 用完关掉再开另一个 | bilibili/x 两个 headed 窗口并挂 |
| shared profile 多站同窗 | 无 key 时并开多会话 |

**推荐调试方式：**  
站点都 merge 进 `shared-main`，只开 1 个 browser，多 tab 查看。

### 2.6 自建 debug session（CDP 复用）

官方 Python 库**没有**查找/复用已有 browser 的 API（无 `connect` / `get_browser` / attach）。

本项目自建了本地调试会话：

```text
debug-open
  -> 启动 1 个 headed CloakBrowser（shared profile）
  -> --remote-debugging-port=9333
  -> holder 进程保活

debug-tab
  -> Playwright connect_over_cdp(http://127.0.0.1:9333)
  -> 同一窗口 new_page()
  -> 断开 attach，不关 browser
```

新增：

- 代码：`cloak_browser_auth/debug_session.py`
- CLI：`debug-open` / `debug-tab` / `debug-list` / `debug-status` / `debug-close`
- 状态：`.auth/debug-session.json`（gitignore）
- 默认端口：`127.0.0.1:9333`

实测：

```text
debug-open --url https://www.bilibili.com  -> Chrome/150 active
debug-tab https://x.com/home               -> 同窗新 tab，pages>=2
debug-list                                 -> bilibili + x 可见
debug-close                                -> 正常关闭
```

---

## 3. 当前配置快照

### 站点

- `sites/bilibili.json` → `bilibili-main`
- `sites/x.json` → `x-main`

### Profile

```json
{
  "bilibili-main": { "dedicated": true,  "allowedSites": ["bilibili-main"] },
  "x-main":        { "dedicated": true,  "allowedSites": ["x-main"] },
  "shared-main":   { "dedicated": false, "allowedSites": ["bilibili-main", "x-main"] }
}
```

### 扩展

- ID（本机）：`fjlmifglncoiehjjclohfgpjmfidagij`
- 版本：`0.2.0`
- 权限：`cookies` / `scripting` / `storage` / `tabs` + `https://*/*`
- 弹窗：仅配对，无逐站授权

### 运行

- daemon：`ws://127.0.0.1:17321`
- MCP：`python -m cloak_browser_auth mcp`（会占同一端口，需先停 `serve`）

---

## 4. 常用命令

### 基础

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
.\scripts\pair.ps1
.\.venv\Scripts\python.exe -m cloak_browser_auth doctor
```

### MCP

```powershell
# 先停独立 serve，再：
.\.venv\Scripts\python.exe -m cloak_browser_auth mcp
```

工具：

- `auth_list_sites`
- `auth_sync_to_cloak`（`mode=merge|replace`）
- `auth_verify_cloak`
- `auth_clear_cloak`（`confirm=true`）

### 多站调试（推荐）

```powershell
# 假设已把 bilibili/x sync 到 shared-main
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-open --profile shared-main --url https://www.bilibili.com
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-tab https://x.com/home
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-list
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-status
.\.venv\Scripts\python.exe -m cloak_browser_auth debug-close
```

### CloakBrowser

```powershell
.\.venv\Scripts\python.exe -m cloakbrowser info
.\.venv\Scripts\python.exe -m cloakbrowser update
```

### 自检

```powershell
.\.venv\Scripts\python.exe -m ruff check cloak_browser_auth tests
.\.venv\Scripts\python.exe -m mypy cloak_browser_auth
.\.venv\Scripts\python.exe -m pytest -q
npm test
```

---

## 5. 安全不变量（保持）

- Token：`.auth/pairing-token.dpapi`（DPAPI），只经 `pair.ps1` 进剪贴板
- Cookie / localStorage：仅内存传输，不进日志、MCP result、LLM 上下文
- WebSocket：只绑 `127.0.0.1`
- 握手：双向 HMAC + 新 challenge，防重放
- capture：匹配 request id / nonce / 连接
- daemon 注册表是第二道白名单
- 注入前校验 Origin 未跳转
- shared profile 清理是站点级；replace 全清仅 dedicated
- debug session 输出只含安全字段（URL 会去掉 query/fragment）

---

## 6. 关键文件

| 路径 | 作用 |
|---|---|
| `extension/service-worker.js` | 连接、握手、按 daemon 配置 capture |
| `extension/protocol.js` | 校验 capture 请求中的 site 范围 |
| `extension/manifest.json` | 宽 host permissions |
| `cloak_browser_auth/extension_bridge.py` | 下发 `cookie_domains`/`origins` |
| `cloak_browser_auth/service.py` | 同步编排 |
| `cloak_browser_auth/cloak_profiles.py` | 导入 / JSON 或 URL verify / 清理 |
| `cloak_browser_auth/config.py` | 注册表与 VerifyConfig |
| `cloak_browser_auth/debug_session.py` | CDP debug session |
| `cloak_browser_auth/main.py` | CLI（含 debug-*） |
| `sites/*.json` | 站点白名单 |
| `profiles.json` | Profile 映射 |
| `README.md` | 使用说明 |

---

## 7. 验证状态

最后检查：

- pytest：通过（含 debug session 单测）
- ruff / mypy：通过
- 真实同步：bilibili + x + shared-main 均 `verified: true`
- debug session：open → tab → list → close 实测通过
- CloakBrowser：150 pro binary 已安装

---

## 8. 后续建议

1. **多站日常调试**：一律 `shared-main` + `debug-*`，不要并开多个 browser。
2. **单站隔离验证**：用 dedicated profile，用完 `debug-close` / context.close，再开下一个。
3. **新增站点**：只改 `sites/` + `profiles.json`，扩展不用再授权；shared 调试就把 site id 加进 `shared-main.allowedSites`。
4. **需要真·多 browser 并行**：升级 CloakBrowser 付费 plan。
5. **debug session 是本地调试能力**，不是 MCP 工具；不要把 CDP 端口暴露到非本机。
6. 本会话代码改动尚未提交；需要时可按主题拆 commit（免授权协议 / x.com+verify / debug session）。

---

## 9. 一句话

扩展不再逐站点授权；daemon 管站点范围；CloakBrowser 已到 150；Free 一并发用 **一个 shared profile + CDP debug session 多 tab** 解决多站调试。

---

## 10. 后续收敛：MCP 唯一入口（同日补充）

已按方案 B 收口：

- **唯一推荐常驻**：`python -m cloak_browser_auth mcp`
  - MCP stdio
  - 扩展桥 `ws://127.0.0.1:17321`
  - Cloak sync / verify / debug
- **不再推荐**长期独立 `serve` / 旧版 `start.ps1` 拉起守护进程
- `scripts/start.ps1` 改为打印 MCP 用法 + 跑 `doctor`
- 新增 `.cursor/mcp.json` 示例配置
- MCP 工具扩展：
  - 原有 `auth_*`
  - 新增 `cloak_debug_open|tab|list|status|close`

日常：

```text
IDE 加载 cloak-browser-auth MCP
  -> 扩展显示已连接
  -> auth_sync_to_cloak
  -> cloak_debug_open / cloak_debug_tab
```

应急无 MCP 客户端时才用：

```powershell
.\.venv\Scripts\python.exe -m cloak_browser_auth serve
```
