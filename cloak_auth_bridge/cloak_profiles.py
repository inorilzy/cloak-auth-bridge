from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from cloak_auth_bridge.config import ProfileConfig, Registry, SiteConfig
from cloak_auth_bridge.converter import convert_cookie
from cloak_auth_bridge.models import AuthBundle, ClearResult, ImportResult

SET_LOCAL_STORAGE_SCRIPT = """
(items) => {
  for (const item of items) {
    localStorage.setItem(item.name, item.value);
  }
}
"""


class CloakProfileManager:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def import_auth(
        self,
        site: SiteConfig,
        profile_id: str,
        profile: ProfileConfig,
        bundle: AuthBundle,
        mode: str,
    ) -> ImportResult:
        if mode not in {"merge", "replace"}:
            raise ValueError("mode must be merge or replace")
        if mode == "replace" and not profile.dedicated:
            raise ValueError("replace mode requires a dedicated profile")

        async with self._locks[profile_id]:
            context = await self._launch(profile)
            try:
                if mode == "replace":
                    await context.clear_cookies()
                    await self._clear_origins(context, site.origins)

                cookies = [convert_cookie(cookie) for cookie in bundle.cookies]
                if cookies:
                    await context.add_cookies(cookies)
                for origin_state in bundle.origins:
                    page = await context.new_page()
                    try:
                        await page.goto(origin_state.origin, wait_until="domcontentloaded")
                        self._require_origin(page.url, origin_state.origin)
                        await page.evaluate(
                            SET_LOCAL_STORAGE_SCRIPT,
                            [item.model_dump() for item in origin_state.local_storage],
                        )
                    finally:
                        await page.close()

                verified = await self._verify_in_context(context, site)
            finally:
                await context.close()

        return ImportResult(
            site_id=site.id,
            target_profile=profile_id,
            cookies_imported=len(bundle.cookies),
            origins_imported=len(bundle.origins),
            verified=verified,
        )

    async def verify(self, site: SiteConfig, profile_id: str, profile: ProfileConfig) -> bool:
        async with self._locks[profile_id]:
            context = await self._launch(profile)
            try:
                return await self._verify_in_context(context, site)
            finally:
                await context.close()

    async def clear(
        self,
        site: SiteConfig,
        profile_id: str,
        profile: ProfileConfig,
    ) -> ClearResult:
        async with self._locks[profile_id]:
            context = await self._launch(profile)
            try:
                cookies_before = await context.cookies()
                cookies_cleared = sum(
                    self._cookie_matches_site(cookie["domain"], site) for cookie in cookies_before
                )
                for domain in site.cookie_domains:
                    await context.clear_cookies(domain=re.compile(rf"(^|\.){re.escape(domain)}$"))
                await self._clear_origins(context, site.origins)
            finally:
                await context.close()
        return ClearResult(
            site_id=site.id,
            target_profile=profile_id,
            cookies_cleared=cookies_cleared,
            origins_cleared=len(site.origins),
        )

    async def _launch(self, profile: ProfileConfig) -> Any:
        try:
            from cloakbrowser import launch_persistent_context_async  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError("cloakbrowser is not installed") from error

        profile_path = self.registry.resolve_profile_path(profile)
        profile_path.mkdir(parents=True, exist_ok=True)
        return await launch_persistent_context_async(
            str(profile_path),
            headless=profile.headless,
        )

    async def _clear_origins(self, context: Any, origins: list[str]) -> None:
        for origin in origins:
            page = await context.new_page()
            try:
                await page.goto(origin, wait_until="domcontentloaded")
                self._require_origin(page.url, origin)
                await page.evaluate("() => localStorage.clear()")
            finally:
                await page.close()

    async def _verify_in_context(self, context: Any, site: SiteConfig) -> bool:
        page = await context.new_page()
        try:
            response = await page.goto(site.verify.url, wait_until="domcontentloaded")
            if response is None or not response.ok:
                return False
            body = await page.text_content("body")
            if body is None:
                return False
            value: Any = json.loads(body)
            for part in site.verify.json_path.split("."):
                if not isinstance(value, dict) or part not in value:
                    return False
                value = value[part]
            return value == site.verify.equals
        finally:
            await page.close()

    @staticmethod
    def _require_origin(actual_url: str, expected_origin: str) -> None:
        parsed = urlsplit(actual_url)
        actual_origin = f"{parsed.scheme}://{parsed.netloc}"
        if actual_origin != expected_origin:
            raise RuntimeError(f"Cloak page redirected away from registered origin {expected_origin}")

    @staticmethod
    def _cookie_matches_site(cookie_domain: str, site: SiteConfig) -> bool:
        domain = cookie_domain.lower().lstrip(".")
        return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in site.cookie_domains)
