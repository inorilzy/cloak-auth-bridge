import { createSiteConfig } from "./site-config.js";

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
  if (typeof message.nonce !== "string" || !NONCE_PATTERN.test(message.nonce)) {
    throw new Error("无效请求 nonce");
  }

  const site = createSiteConfig({
    id: message.site_id,
    cookieDomains: message.cookie_domains,
    origins: message.origins
  });

  return {
    id: message.id,
    siteId: site.id,
    cookieDomains: site.cookieDomains,
    origins: site.origins,
    nonce: message.nonce
  };
}

export function safeErrorMessage(error) {
  if (error instanceof Error && error.message) {
    return error.message.slice(0, 300);
  }
  return "未知错误";
}

export function isValidHelloAck(message, clientChallenge, serverChallenge = null) {
  if (message?.type !== "hello_ack" || message.ok !== true) {
    return false;
  }
  if (typeof clientChallenge !== "string" || message.client_challenge !== clientChallenge) {
    return false;
  }
  // Loopback-trust mode: server may ack immediately without a prior challenge exchange.
  if (message.mode === "loopback_trust") {
    return typeof message.server_challenge === "string" && message.server_challenge.length >= 16;
  }
  return Boolean(
    typeof serverChallenge === "string"
    && serverChallenge.length >= 16
    && message.server_challenge === serverChallenge
  );
}
