import test from "node:test";
import assert from "node:assert/strict";
import { buildHostPermissions, createSiteConfig, normalizeOrigin } from "../extension/site-config.js";

test("site config normalizes and deduplicates values", () => {
  assert.deepEqual(createSiteConfig({
    id: " Example-Main ",
    cookieDomains: [".Example.com", "example.com"],
    origins: ["https://www.example.com/path", "https://www.example.com"]
  }), {
    id: "example-main",
    cookieDomains: ["example.com"],
    origins: ["https://www.example.com"]
  });
});

test("only HTTPS origins are accepted", () => {
  assert.throws(() => normalizeOrigin("http://example.com"), /HTTPS/);
  assert.throws(() => normalizeOrigin("https://user:pass@example.com"), /HTTPS/);
});

test("host permissions cover cookie domains and configured origins", () => {
  const site = createSiteConfig({
    id: "example-main",
    cookieDomains: ["example.com"],
    origins: ["https://account.example.com"]
  });
  assert.deepEqual(buildHostPermissions(site), [
    "https://*.example.com/*",
    "https://account.example.com/*"
  ]);
});

