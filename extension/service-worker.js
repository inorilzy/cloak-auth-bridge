import { captureAuthBundle } from "./capture.js";
import { createHmacProof } from "./auth.js";
import {
  isValidHelloAck,
  parseServerMessage,
  safeErrorMessage,
  validateCaptureRequest
} from "./protocol.js";

const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:17321",
  pairingToken: "",
  profileAlias: "chrome-default"
};
let socket = null;
let authenticated = false;
let reconnectTimer = null;
let keepAliveTimer = null;
let currentChallenge = null;
let currentServerChallenge = null;
let currentPairingToken = null;
let connectionGeneration = 0;
const seenNonces = new Set();
let noncesLoaded = false;

function randomToken(byteLength = 24) {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(["settings", "sites"]);
  return {
    settings: { ...DEFAULT_SETTINGS, ...(stored.settings ?? {}) },
    sites: stored.sites ?? {}
  };
}

function setConnectionState(state, error = "") {
  chrome.storage.session.set({ connection: { state, error, updatedAt: Date.now() } });
}

function closeSocket() {
  clearTimeout(reconnectTimer);
  clearInterval(keepAliveTimer);
  reconnectTimer = null;
  keepAliveTimer = null;
  authenticated = false;
  currentChallenge = null;
  currentServerChallenge = null;
  currentPairingToken = null;
  if (socket) {
    const closingSocket = socket;
    socket = null;
    closingSocket.onopen = null;
    closingSocket.onmessage = null;
    closingSocket.onerror = null;
    closingSocket.onclose = null;
    closingSocket.close();
  }
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 5_000);
}

async function rememberNonce(nonce) {
  if (!noncesLoaded) {
    const stored = await chrome.storage.session.get("seenNonces");
    for (const storedNonce of stored.seenNonces ?? []) {
      seenNonces.add(storedNonce);
    }
    noncesLoaded = true;
  }
  if (seenNonces.has(nonce)) {
    throw new Error("检测到重复 nonce，已拒绝请求");
  }
  seenNonces.add(nonce);
  await chrome.storage.session.set({ seenNonces: [...seenNonces] });
}

function send(message, targetSocket = socket) {
  if (targetSocket?.readyState !== WebSocket.OPEN) {
    throw new Error("本地服务连接已断开");
  }
  targetSocket.send(JSON.stringify(message));
}

async function handleCapture(message, responseSocket) {
  const request = validateCaptureRequest(message);
  await rememberNonce(request.nonce);

  const { settings, sites } = await loadSettings();
  const site = sites[request.siteId];
  if (!site) {
    throw new Error(`站点 ${request.siteId} 不在扩展白名单中`);
  }

  const payload = await captureAuthBundle(site, settings.profileAlias);
  send({
    id: request.id,
    type: "capture_auth_result",
    nonce: request.nonce,
    ok: true,
    payload
  }, responseSocket);
}

async function handleMessage(event, responseSocket) {
  let message;
  try {
    message = parseServerMessage(event.data);
    if (message.type === "hello_challenge") {
      if (
        typeof message.server_challenge !== "string"
        || message.server_challenge.length < 16
        || message.client_challenge !== currentChallenge
        || !currentPairingToken
      ) {
        throw new Error("本地服务 challenge 无效");
      }
      const proofPayload = `${currentChallenge}:${message.server_challenge}`;
      const expectedProof = await createHmacProof(currentPairingToken, "server", proofPayload);
      if (message.proof !== expectedProof) {
        throw new Error("本地服务配对验证失败");
      }
      currentServerChallenge = message.server_challenge;
      send({
        type: "hello_response",
        client_challenge: currentChallenge,
        server_challenge: currentServerChallenge,
        proof: await createHmacProof(currentPairingToken, "client", proofPayload)
      }, responseSocket);
      return;
    }
    if (message.type === "hello_ack") {
      if (!isValidHelloAck(message, currentChallenge, currentServerChallenge)) {
        throw new Error("本地服务配对验证失败");
      }
      authenticated = true;
      setConnectionState("connected");
      return;
    }
    if (!authenticated) {
      throw new Error("本地服务尚未完成配对验证");
    }
    if (message.type === "pong") {
      return;
    }
    await handleCapture(message, responseSocket);
  } catch (error) {
    const id = typeof message?.id === "string" ? message.id : null;
    if (id && responseSocket?.readyState === WebSocket.OPEN) {
      send({
        id,
        type: "capture_auth_result",
        nonce: typeof message?.nonce === "string" ? message.nonce : undefined,
        ok: false,
        error: safeErrorMessage(error)
      }, responseSocket);
    }
    if (socket === responseSocket) {
      setConnectionState("error", safeErrorMessage(error));
      if (!authenticated) {
        responseSocket.close();
      }
    }
  }
}

async function connect() {
  const generation = ++connectionGeneration;
  closeSocket();
  const { settings } = await loadSettings();
  if (generation !== connectionGeneration) {
    return;
  }
  if (settings.pairingToken.length < 16) {
    setConnectionState("unconfigured", "请先在扩展弹窗中设置配对 Token");
    return;
  }

  let url;
  try {
    url = new URL(settings.serverUrl);
  } catch {
    setConnectionState("error", "WebSocket 地址无效");
    return;
  }
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.hash) {
    setConnectionState("error", "只允许连接 ws://127.0.0.1");
    return;
  }

  setConnectionState("connecting");
  const connectingSocket = new WebSocket(url.href);
  socket = connectingSocket;
  connectingSocket.onopen = async () => {
    try {
      if (generation !== connectionGeneration || socket !== connectingSocket) {
        connectingSocket.close();
        return;
      }
      currentChallenge = randomToken();
      currentPairingToken = settings.pairingToken;
      if (generation !== connectionGeneration || socket !== connectingSocket) {
        connectingSocket.close();
        return;
      }
      send({
        type: "hello",
        extension_id: chrome.runtime.id,
        profile_alias: settings.profileAlias,
        challenge: currentChallenge
      }, connectingSocket);
      keepAliveTimer = setInterval(() => {
        if (authenticated && socket?.readyState === WebSocket.OPEN) {
          send({ type: "ping", at: Date.now() }, connectingSocket);
        }
      }, 20_000);
    } catch (error) {
      setConnectionState("error", safeErrorMessage(error));
      connectingSocket.close();
    }
  };
  connectingSocket.onmessage = (event) => {
    if (socket === connectingSocket && generation === connectionGeneration) {
      handleMessage(event, connectingSocket);
    }
  };
  connectingSocket.onerror = () => {
    if (socket === connectingSocket) {
      setConnectionState("error", "无法连接本地服务");
    }
  };
  connectingSocket.onclose = () => {
    if (socket !== connectingSocket) {
      return;
    }
    socket = null;
    authenticated = false;
    clearInterval(keepAliveTimer);
    setConnectionState("disconnected", "本地服务连接已断开");
    scheduleReconnect();
  };
}

chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.settings) {
    connect();
  }
});
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "reconnect") {
    return false;
  }
  connect().then(() => sendResponse({ ok: true }));
  return true;
});

connect();
