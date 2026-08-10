const SITE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/;
const HOSTNAME_PATTERN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export function normalizeSiteId(value) {
  const siteId = String(value ?? "").trim().toLowerCase();
  if (!SITE_ID_PATTERN.test(siteId)) {
    throw new Error("站点 ID 必须为 2-64 位小写字母、数字、下划线或连字符");
  }
  return siteId;
}

export function normalizeCookieDomain(value) {
  const domain = String(value ?? "").trim().toLowerCase().replace(/^\.+/, "");
  if (!HOSTNAME_PATTERN.test(domain)) {
    throw new Error(`无效 Cookie 域名：${value}`);
  }
  return domain;
}

export function normalizeOrigin(value) {
  let url;
  try {
    url = new URL(String(value ?? "").trim());
  } catch {
    throw new Error(`无效 origin：${value}`);
  }

  if (url.protocol !== "https:" || url.username || url.password || url.origin === "null") {
    throw new Error(`只允许无凭据的 HTTPS origin：${value}`);
  }
  return url.origin;
}

function unique(values) {
  return [...new Set(values)];
}

export function createSiteConfig(input) {
  const cookieDomains = unique((input.cookieDomains ?? []).map(normalizeCookieDomain));
  const origins = unique((input.origins ?? []).map(normalizeOrigin));

  if (cookieDomains.length === 0) {
    throw new Error("至少配置一个 Cookie 域名");
  }
  if (origins.length === 0) {
    throw new Error("至少配置一个 origin");
  }

  return {
    id: normalizeSiteId(input.id),
    cookieDomains,
    origins
  };
}

export function buildHostPermissions(site) {
  const cookiePermissions = site.cookieDomains.map((domain) => `https://*.${domain}/*`);
  const originPermissions = site.origins.map((origin) => {
    const url = new URL(origin);
    return `https://${url.hostname}/*`;
  });
  return unique([...cookiePermissions, ...originPermissions]);
}

