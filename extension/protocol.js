const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const NONCE_PATTERN = /^[A-Za-z0-9_-]{16,256}$/;

export function parseServerMessage(raw) {
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    throw new Error("服务端消息不是有效 JSON");
  }

  if (!message || typeof message !== "object" || Array.isArray(message)) {
    throw new Error("服务端消息必须是对象");
  }
  return message;
}

export function validateCaptureRequest(message) {
  if (message.type !== "capture_auth") {
    throw new Error("不支持的消息类型");
  }
  if (typeof message.id !== "string" || !REQUEST_ID_PATTERN.test(message.id)) {
    throw new Error("无效请求 ID");
  }
  if (typeof message.site_id !== "string" || !message.site_id) {
    throw new Error("缺少站点 ID");
  }
  if (typeof message.nonce !== "string" || !NONCE_PATTERN.test(message.nonce)) {
    throw new Error("无效请求 nonce");
  }

  return {
    id: message.id,
    siteId: message.site_id,
    nonce: message.nonce
  };
}

export function safeErrorMessage(error) {
  if (error instanceof Error && error.message) {
    return error.message.slice(0, 300);
  }
  return "未知错误";
}

