function readWebStorage() {
  const read = (storage) => {
    const result = [];
    for (let index = 0; index < storage.length; index += 1) {
      const name = storage.key(index);
      if (name !== null) {
        result.push({ name, value: storage.getItem(name) ?? "" });
      }
    }
    return result;
  };

  return {
    origin: location.origin,
    localStorage: read(window.localStorage)
  };
}

function waitForTabComplete(tabId, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("等待站点页面加载超时"));
    }, timeoutMs);

    function listener(updatedTabId, changeInfo) {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") {
        return;
      }
      clearTimeout(timeoutId);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }

    chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") {
        clearTimeout(timeoutId);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }).catch((error) => {
      clearTimeout(timeoutId);
      chrome.tabs.onUpdated.removeListener(listener);
      reject(error);
    });

    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function findOrOpenOrigin(origin) {
  const tabs = await chrome.tabs.query({});
  const matchingTab = tabs.find((tab) => {
    try {
      return tab.url && new URL(tab.url).origin === origin;
    } catch {
      return false;
    }
  });
  if (matchingTab?.id !== undefined) {
    await waitForTabComplete(matchingTab.id);
    return { tabId: matchingTab.id, temporary: false };
  }

  const tab = await chrome.tabs.create({ url: `${origin}/`, active: false });
  if (tab.id === undefined) {
    throw new Error(`无法为 ${origin} 创建临时标签页`);
  }
  try {
    await waitForTabComplete(tab.id);
    return { tabId: tab.id, temporary: true };
  } catch (error) {
    await chrome.tabs.remove(tab.id).catch(() => undefined);
    throw error;
  }
}

async function captureOriginStorage(expectedOrigin) {
  const { tabId, temporary } = await findOrOpenOrigin(expectedOrigin);
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "ISOLATED",
      func: readWebStorage
    });
    const state = results[0]?.result;
    if (!state) {
      throw new Error(`无法读取 ${expectedOrigin} 的 Web Storage`);
    }
    if (state.origin !== expectedOrigin) {
      throw new Error(`${expectedOrigin} 被重定向到 ${state.origin}，已拒绝采集`);
    }
    return state;
  } finally {
    if (temporary) {
      await chrome.tabs.remove(tabId).catch(() => undefined);
    }
  }
}

function cookieKey(cookie) {
  const partition = cookie.partitionKey ? JSON.stringify(cookie.partitionKey) : "";
  return [cookie.name, cookie.domain, cookie.path, cookie.storeId ?? "", partition].join("|");
}

function domainMatchesAllowlist(cookieDomain, allowedDomains) {
  const domain = String(cookieDomain || "").toLowerCase().replace(/^\./, "");
  return allowedDomains.some((allowed) => {
    const base = String(allowed || "").toLowerCase().replace(/^\./, "");
    return domain === base || domain.endsWith(`.${base}`);
  });
}

function buildCookieQueryUrls(cookieDomains, origins) {
  const urls = new Set();
  for (const origin of origins || []) {
    urls.add(origin.endsWith("/") ? origin : `${origin}/`);
  }
  for (const domain of cookieDomains || []) {
    const host = String(domain || "").replace(/^\./, "");
    if (!host) continue;
    urls.add(`https://${host}/`);
    urls.add(`https://www.${host}/`);
    if (host === "google.com" || host.endsWith(".google.com")) {
      urls.add("https://accounts.google.com/");
      urls.add("https://www.google.com/");
    }
    if (host === "youtube.com" || host.endsWith(".youtube.com")) {
      urls.add("https://www.youtube.com/");
      urls.add("https://youtube.com/");
    }
  }
  return [...urls];
}

async function queryCookies(query) {
  try {
    return await chrome.cookies.getAll(query);
  } catch {
    return [];
  }
}

async function captureCookies(cookieDomains, origins = []) {
  const allowed = (cookieDomains || []).map((item) => String(item).toLowerCase().replace(/^\./, ""));
  const collected = new Map();

  const addCookies = (cookies) => {
    for (const cookie of cookies) {
      if (!domainMatchesAllowlist(cookie.domain, allowed)) {
        continue;
      }
      collected.set(cookieKey(cookie), cookie);
    }
  };

  const stores = await chrome.cookies.getAllCookieStores().catch(() => []);
  const storeIds = stores.length > 0 ? stores.map((store) => store.id) : [undefined];
  const criticalNames = [
    "SID", "HSID", "SSID", "APISID", "SAPISID", "SIDCC", "LOGIN_INFO",
    "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS", "__Secure-1PSIDCC", "__Secure-3PSIDCC"
  ];
  const criticalUrls = buildCookieQueryUrls(allowed, origins);

  for (const storeId of storeIds) {
    const storeQuery = storeId === undefined ? {} : { storeId };

    for (const domain of allowed) {
      addCookies(await queryCookies({ ...storeQuery, domain }));
      addCookies(await queryCookies({ ...storeQuery, domain: `.${domain}` }));
    }

    for (const url of criticalUrls) {
      addCookies(await queryCookies({ ...storeQuery, url }));
    }

    for (const origin of origins || []) {
      let topLevelSite = origin;
      try {
        topLevelSite = new URL(origin).origin;
      } catch {
        continue;
      }
      addCookies(await queryCookies({
        ...storeQuery,
        partitionKey: { topLevelSite }
      }));
      addCookies(await queryCookies({
        ...storeQuery,
        partitionKey: { topLevelSite, hasCrossSiteAncestor: true }
      }));
      addCookies(await queryCookies({
        ...storeQuery,
        partitionKey: { topLevelSite, hasCrossSiteAncestor: false }
      }));
    }

    addCookies(await queryCookies({ ...storeQuery }));

    for (const url of criticalUrls) {
      for (const name of criticalNames) {
        try {
          const cookie = await chrome.cookies.get({
            url,
            name,
            ...(storeId === undefined ? {} : { storeId })
          });
          if (cookie) {
            addCookies([cookie]);
          }
        } catch {
          // Ignore unsupported query shapes per Chrome version.
        }
      }
    }
  }

  return [...collected.values()];
}

export async function captureAuthBundle(site, sourceProfile) {
  // Open origins first so first-party context exists, then collect cookies.
  const origins = [];
  for (const origin of site.origins) {
    origins.push(await captureOriginStorage(origin));
  }
  const cookies = await captureCookies(site.cookieDomains, site.origins);

  return {
    version: 1,
    siteId: site.id,
    sourceProfile,
    capturedAt: new Date().toISOString(),
    cookies,
    origins
  };
}
