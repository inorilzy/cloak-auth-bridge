# Cloak Auth Bridge Chrome Extension

一个最小的 Chrome MV3 扩展：只从用户手工授权并登记的站点采集 Cookies 和 `localStorage`，通过本机 WebSocket 交给 Cloak Auth Bridge daemon。

扩展不会在 UI 中显示或导出原始认证值，也不接受任意 URL 或任意 JavaScript。LLM 应只通过 daemon 的高层 MCP 工具触发同步。

## 安装

1. Chrome 打开 `chrome://extensions`，启用“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择本仓库的 `extension` 目录。
3. 启动监听 `127.0.0.1:17321` 的本地 daemon。
4. 打开扩展弹窗，填写 daemon 生成的配对 Token，保存。
5. 打开目标站点，在弹窗中确认站点 ID、Cookie 域名和 origins，点击“授权并加入白名单”。

扩展只支持 HTTPS 站点，并且只允许连接 `ws://127.0.0.1`。

## WebSocket 协议

连接后扩展发送带用途前缀的 HMAC-SHA256 配对消息（Token 本身不经网络发送）：

```json
{
  "type": "hello",
  "extension_id": "...",
  "profile_alias": "chrome-default",
  "challenge": "base64url-random",
  "proof": "hex-hmac-sha256"
}
```

daemon 必须使用同一个 Token 验证 `proof = HMAC-SHA256(token, "client:" + challenge)`，然后返回服务端证明 `HMAC-SHA256(token, "server:" + challenge)`：

```json
{
  "type": "hello_ack",
  "ok": true,
  "challenge": "base64url-random",
  "proof": "hex-hmac-sha256"
}
```

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
