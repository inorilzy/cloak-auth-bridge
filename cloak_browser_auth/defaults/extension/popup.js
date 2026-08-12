const elements = Object.fromEntries([
  "connection", "server-url", "pairing-token", "profile-alias", "save-settings", "notice"
].map((id) => [id, document.getElementById(id)]));

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.classList.toggle("error", isError);
}

async function loadState() {
  const [{ settings = {} }, { connection = {} }] = await Promise.all([
    chrome.storage.local.get(["settings"]),
    chrome.storage.session.get("connection")
  ]);
  elements["server-url"].value = settings.serverUrl ?? "ws://127.0.0.1:17321";
  elements["pairing-token"].value = settings.pairingToken ?? "";
  elements["profile-alias"].value = settings.profileAlias ?? "chrome-default";
  renderConnection(connection);
}

function renderConnection(connection) {
  const labels = {
    connected: "已连接",
    connecting: "连接中",
    disconnected: "已断开",
    unconfigured: "未配置",
    error: "错误"
  };
  elements.connection.textContent = labels[connection.state] ?? "未知";
  elements.connection.className = `status ${connection.state ?? ""}`;
  elements.connection.title = connection.error ?? "";
}

async function saveSettings() {
  const serverUrl = elements["server-url"].value.trim();
  const url = new URL(serverUrl);
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.hash) {
    throw new Error("WebSocket 仅允许 ws://127.0.0.1");
  }
  const pairingToken = elements["pairing-token"].value.trim();
  if (pairingToken && pairingToken.length < 16) {
    throw new Error("若填写配对 Token，至少需要 16 个字符");
  }
  const profileAlias = elements["profile-alias"].value.trim();
  if (!profileAlias) {
    throw new Error("Profile 别名不能为空");
  }
  await chrome.storage.local.set({
    settings: {
      serverUrl: url.href,
      pairingToken,
      profileAlias
    }
  });
  await chrome.runtime.sendMessage({ type: "reconnect" });
  showNotice(pairingToken ? "已启用 Token 加固并重连" : "本机免 Token 模式，正在连接");
}

elements["save-settings"].addEventListener("click", () => saveSettings().catch((error) => showNotice(error.message, true)));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.connection) {
    renderConnection(changes.connection.newValue ?? {});
  }
});

await loadState();
