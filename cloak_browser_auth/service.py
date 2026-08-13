from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from cloak_browser_auth.cloak_profiles import CloakProfileManager
from cloak_browser_auth.config import Registry
from cloak_browser_auth.converter import validate_bundle
from cloak_browser_auth.extension_bridge import ExtensionBridge

T = TypeVar("T")
MIN_OPERATION_INTERVAL_SECONDS = 1.0


class AuthOperations(Protocol):
    async def list_sites(self) -> dict[str, Any]: ...

    async def sync(self, site_id: str, target_profile: str, mode: str = "merge") -> dict[str, Any]: ...

    async def verify(self, site_id: str, target_profile: str) -> dict[str, Any]: ...

    async def clear(self, site_id: str, target_profile: str, confirm: bool) -> dict[str, Any]: ...


class AuthService:
    def __init__(
        self,
        registry: Registry,
        bridge: ExtensionBridge,
        profiles: CloakProfileManager,
    ) -> None:
        self.registry = registry
        self.bridge = bridge
        self.profiles = profiles
        self._last_sync: dict[str, str] = {}
        self._operation_lock = asyncio.Lock()
        self._last_operation_at = 0.0

    async def list_sites(self) -> dict[str, Any]:
        sites = []
        for site_id in sorted(self.registry.sites):
            allowed_targets = sorted(
                profile_id
                for profile_id, profile in self.registry.profiles.items()
                if site_id in profile.allowed_sites
            )
            sites.append(
                {
                    "site_id": site_id,
                    "source_connected": self.bridge.connected,
                    "last_sync_at": self._last_sync.get(site_id),
                    "allowed_targets": allowed_targets,
                }
            )
        return {"sites": sites}

    async def sync(self, site_id: str, target_profile: str, mode: str = "merge") -> dict[str, Any]:
        if mode not in {"merge", "replace"}:
            raise ValueError("mode must be merge or replace")
        site, profile = self.registry.target(site_id, target_profile)
        async with self._limited_operation():
            bundle = await self.bridge.capture(site.id, site.cookie_domains, site.origins)
            validate_bundle(bundle, site)
            result = await self._safe_profile_call(
                self.profiles.import_auth(site, target_profile, profile, bundle, mode),
                "import",
            )
        self._last_sync[site_id] = datetime.now(UTC).isoformat()
        return result.model_dump()

    async def verify(self, site_id: str, target_profile: str) -> dict[str, Any]:
        site, profile = self.registry.target(site_id, target_profile)
        async with self._limited_operation():
            verified = await self._safe_profile_call(
                self.profiles.verify(site, target_profile, profile),
                "verification",
            )
        return {
            "ok": True,
            "site_id": site_id,
            "target_profile": target_profile,
            "verified": verified,
        }

    async def clear(self, site_id: str, target_profile: str, confirm: bool) -> dict[str, Any]:
        if confirm is not True:
            raise ValueError("auth_clear_cloak requires confirm=true")
        site, profile = self.registry.target(site_id, target_profile)
        async with self._limited_operation():
            result = await self._safe_profile_call(
                self.profiles.clear(site, target_profile, profile),
                "clear",
            )
        return result.model_dump()

    @asynccontextmanager
    async def _limited_operation(self) -> AsyncIterator[None]:
        async with self._operation_lock:
            now = asyncio.get_running_loop().time()
            if now - self._last_operation_at < MIN_OPERATION_INTERVAL_SECONDS:
                raise RuntimeError("authentication operation rate limit exceeded; retry later")
            self._last_operation_at = now
            yield

    async def _safe_profile_call(self, operation: Awaitable[T], action: str) -> T:
        try:
            return await operation
        except Exception:  # noqa: BLE001 - credential boundary must sanitize third-party errors
            raise RuntimeError(f"Cloak {action} failed; inspect local daemon diagnostics") from None
