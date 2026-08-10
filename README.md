# Cloak Auth Bridge

一个本地认证桥：Chrome MV3 扩展只从用户手工授权并登记的站点采集 Cookies 和 `localStorage`，Python daemon 通过本机 WebSocket 接收后直接注入 CloakBrowser 持久 Profile。

扩展不会在 UI 中显示或导出原始认证值，也不接受任意 URL 或任意 JavaScript。MCP 工具只返回数量和验证结果，原始 Cookie/Token 不进入 LLM 上下文。

## 本地安装

在 PowerShell 中运行：

```powershell
.\scripts\setup.ps1
```

脚本会在项目内创建 `.venv`，安装 MCP、WebSocket 和官方 `cloakbrowser` Python 包。

## 启动与配对

先启动独立 daemon：

```powershell
.\scripts\start.ps1
```

另开一个 PowerShell，把 DPAPI 保护的配对 Token 直接复制到 Windows 剪贴板：

```powershell
.\scripts\pair.ps1
```

打开扩展，把剪贴板内容粘贴到“配对 Token”，点击“保存并重连”。Token 不会打印到日志，也不要发送给 LLM。

daemon 监听固定地址 `ws://127.0.0.1:17321`。daemon 每次连接生成新的随机 challenge，扩展和 daemon 使用带 `client:` / `server:` 前缀的双向 HMAC-SHA256 三步握手，Token 本身不经过 WebSocket，旧握手不能重放。

## Bilibili 配置

扩展站点白名单：

```text
站点 ID: bilibili-main
Cookie 域名: bilibili.com
Origin: https://www.bilibili.com
```

daemon 侧对应配置在 `sites/bilibili.json`，Cloak 目标在 `profiles.json`。默认持久 Profile 目录为 `profiles/bilibili-main`。

第一次真正同步时，`cloakbrowser` 可能下载约 200MB 的官方 Chromium 二进制。Profile 默认以有头模式启动，注入后通过 Bilibili `nav` API 检查 `data.isLogin`。

## MCP stdio

MCP 客户端应在本项目目录启动：

```powershell
.\.venv\Scripts\python.exe -m cloak_auth_bridge mcp
```

该进程同时提供 MCP stdio 和扩展 WebSocket，因此使用 MCP 模式前要停止独立的 `scripts\start.ps1`，避免端口冲突。

暴露的工具：

- `auth_list_sites`
- `auth_sync_to_cloak`
- `auth_verify_cloak`
- `auth_clear_cloak`（必须传 `confirm=true`）

## 自检

```powershell
.\.venv\Scripts\python.exe -m cloak_auth_bridge doctor
.\.venv\Scripts\python.exe -m pytest -q
npm test
```

## 安装 Chrome 扩展

1. Chrome 打开 `chrome://extensions`，启用“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择本仓库的 `extension` 目录。
3. 启动监听 `127.0.0.1:17321` 的本地 daemon。
4. 打开扩展弹窗，填写 daemon 生成的配对 Token，保存。
5. 打开目标站点，在弹窗中确认站点 ID、Cookie 域名和 origins，点击“授权并加入白名单”。

扩展只支持 HTTPS 站点，并且只允许连接 `ws://127.0.0.1`。

## WebSocket 协议

连接后扩展先发送自己的随机 challenge（Token 本身不经网络发送）：

```json
{
  "type": "hello",
  "extension_id": "...",
  "profile_alias": "chrome-default",
  "challenge": "base64url-random"
}
```

daemon 再生成一个随机 `server_challenge`，返回服务端证明；扩展验证后返回对应的客户端证明：

```json
{
  "type": "hello_challenge",
  "client_challenge": "base64url-random",
  "server_challenge": "base64url-random",
  "proof": "hex-hmac-sha256"
}
```

证明输入为 `client_challenge + ":" + server_challenge`，分别带 `server:` 和 `client:` HMAC 用途前缀。daemon 验证客户端证明后返回 `hello_ack`，连接才可执行采集。

完成认证后，daemon 可按已登记的 `site_id` 请求采集：

```json
{
  "id": "0192f0cb-1234",
  "type": "capture_auth",
  "site_id": "example-main",
  "nonce": "at-least-16-random-url-safe-characters"
}
```

扩展返回 `capture_auth_result`。成功响应中的 `payload` 含原始认证数据，只允许 daemon 在内存中消费；不要写入日志、MCP tool result 或 LLM 上下文。nonce 在当前 Chrome 会话内不可复用。

## 开发校验

```powershell
npm test
```

项目不需要打包或第三方运行时依赖，`extension/` 可直接被 Chrome 加载。
