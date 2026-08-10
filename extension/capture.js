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

async function captureCookies(cookieDomains) {
  const collected = new Map();
  for (const domain of cookieDomains) {
    const cookies = await chrome.cookies.getAll({ domain });
    for (const cookie of cookies) {
      const partition = cookie.partitionKey
        ? JSON.stringify(cookie.partitionKey)
        : "";
      const key = [cookie.name, cookie.domain, cookie.path, cookie.storeId, partition].join("|");
      collected.set(key, cookie);
    }
  }
  return [...collected.values()];
}

export async function captureAuthBundle(site, sourceProfile) {
  const [cookies, origins] = await Promise.all([
    captureCookies(site.cookieDomains),
    Promise.all(site.origins.map(captureOriginStorage))
  ]);

  return {
    version: 1,
    siteId: site.id,
    sourceProfile,
    capturedAt: new Date().toISOString(),
    cookies,
    origins
  };
}
