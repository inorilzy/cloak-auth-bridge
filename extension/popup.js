import { buildHostPermissions, createSiteConfig } from "./site-config.js";

const elements = Object.fromEntries([
  "connection", "server-url", "pairing-token", "profile-alias", "save-settings",
  "site-id", "cookie-domains", "origins", "save-site", "sites", "site-count", "notice"
].map((id) => [id, document.getElementById(id)]));

function lines(value) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.classList.toggle("error", isError);
}

async function loadState() {
  const [{ settings = {}, sites = {} }, { connection = {} }] = await Promise.all([
    chrome.storage.local.get(["settings", "sites"]),
    chrome.storage.session.get("connection")
  ]);
  elements["server-url"].value = settings.serverUrl ?? "ws://127.0.0.1:17321";
  elements["pairing-token"].value = settings.pairingToken ?? "";
  elements["profile-alias"].value = settings.profileAlias ?? "chrome-default";
  renderConnection(connection);
  renderSites(sites);
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

function renderSites(sites) {
  const values = Object.values(sites).sort((a, b) => a.id.localeCompare(b.id));
  elements["site-count"].textContent = String(values.length);
  elements.sites.replaceChildren();
  if (values.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "尚未登记站点";
    elements.sites.append(empty);
    return;
  }

  for (const site of values) {
    const row = document.createElement("div");
    row.className = "site";
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = site.id;
    const detail = document.createElement("small");
    detail.textContent = site.origins.join(", ");
    info.append(name, detail);
    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "移除";
    remove.addEventListener("click", () => removeSite(site.id));
    row.append(info, remove);
    elements.sites.append(row);
  }
}

async function saveSettings() {
  const serverUrl = elements["server-url"].value.trim();
  const url = new URL(serverUrl);
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.hash) {
    throw new Error("WebSocket 仅允许 ws://127.0.0.1");
  }
  const pairingToken = elements["pairing-token"].value.trim();
  if (pairingToken.length < 16) {
    throw new Error("配对 Token 至少需要 16 个字符");
  }
  const profileAlias = elements["profile-alias"].value.trim();
  if (!profileAlias) {
    throw new Error("Profile 别名不能为空");
  }
  await chrome.storage.local.set({ settings: { serverUrl: url.href, pairingToken, profileAlias } });
  await chrome.runtime.sendMessage({ type: "reconnect" });
  showNotice("设置已保存，正在连接本地服务");
}

async function saveSite() {
  const site = createSiteConfig({
    id: elements["site-id"].value,
    cookieDomains: lines(elements["cookie-domains"].value),
    origins: lines(elements.origins.value)
  });
  const permissions = buildHostPermissions(site);
  const granted = await chrome.permissions.request({ origins: permissions });
  if (!granted) {
    throw new Error("未获得站点读取权限，未加入白名单");
  }
  const { sites = {} } = await chrome.storage.local.get("sites");
  const previous = sites[site.id];
  sites[site.id] = site;
  await chrome.storage.local.set({ sites });
  if (previous) {
    await removeUnusedPermissions(buildHostPermissions(previous), sites);
  }
  renderSites(sites);
  showNotice(`已登记 ${site.id}`);
}

async function removeSite(siteId) {
  const { sites = {} } = await chrome.storage.local.get("sites");
  const removed = sites[siteId];
  if (!removed) {
    return;
  }
  delete sites[siteId];
  await chrome.storage.local.set({ sites });

  await removeUnusedPermissions(buildHostPermissions(removed), sites);
  renderSites(sites);
  showNotice(`已移除 ${siteId}`);
}

async function removeUnusedPermissions(candidates, sites) {
  const stillRequired = new Set(Object.values(sites).flatMap(buildHostPermissions));
  const removable = candidates.filter((permission) => !stillRequired.has(permission));
  if (removable.length > 0) {
    await chrome.permissions.remove({ origins: removable });
  }
}

async function prefillCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return;
  const url = new URL(tab.url);
  if (url.protocol !== "https:") return;
  elements["cookie-domains"].value = url.hostname;
  elements.origins.value = url.origin;
  elements["site-id"].value = `${url.hostname.split(".").slice(-2, -1)[0] || url.hostname}-main`
    .replace(/[^a-z0-9_-]/gi, "-")
    .toLowerCase();
}

elements["save-settings"].addEventListener("click", () => saveSettings().catch((error) => showNotice(error.message, true)));
elements["save-site"].addEventListener("click", () => saveSite().catch((error) => showNotice(error.message, true)));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.connection) {
    renderConnection(changes.connection.newValue ?? {});
  }
});

await Promise.all([loadState(), prefillCurrentTab()]);
