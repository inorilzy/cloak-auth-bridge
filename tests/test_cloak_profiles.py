import re

import pytest

from cloak_browser_auth import debug_session
from cloak_browser_auth.cloak_profiles import CloakProfileManager
from cloak_browser_auth.config import ProfileConfig, Registry


def test_cookie_domain_matching_is_site_scoped() -> None:
    site = Registry.load().sites["bilibili-main"]

    assert CloakProfileManager._cookie_matches_site(".bilibili.com", site)
    assert CloakProfileManager._cookie_matches_site("api.bilibili.com", site)
    assert not CloakProfileManager._cookie_matches_site("notbilibili.com", site)


class FakeContext:
    def __init__(self) -> None:
        self.cleared_domains: list[re.Pattern[str]] = []

    async def cookies(self):
        return [
            {"domain": ".bilibili.com"},
            {"domain": ".example.com"},
        ]

    async def clear_cookies(self, *, domain):
        self.cleared_domains.append(domain)

    async def close(self):
        return None


class FakeManager(CloakProfileManager):
    def __init__(self, registry: Registry, context: FakeContext) -> None:
        super().__init__(registry)
        self.context = context

    async def _launch(self, profile):
        return self.context

    async def _clear_origins(self, context, origins):
        return None


@pytest.mark.asyncio
async def test_clear_is_site_scoped_for_shared_profiles() -> None:
    registry = Registry.load()
    site = registry.sites["bilibili-main"]
    profile = ProfileConfig(
        path="profiles/shared",
        allowedSites=["bilibili-main", "other-site"],
        dedicated=False,
    )
    context = FakeContext()

    result = await FakeManager(registry, context).clear(site, "shared", profile)

    assert result.cookies_cleared == 1
    assert len(context.cleared_domains) == 1
    assert context.cleared_domains[0].search(".bilibili.com")
    assert not context.cleared_domains[0].search(".example.com")


@pytest.mark.asyncio
async def test_clear_routes_to_active_holder_without_launching_second_context(monkeypatch) -> None:
    registry = Registry.load()
    site = registry.sites["bilibili-main"]
    profile = registry.profiles["shared-main"]
    manager = CloakProfileManager(registry)
    calls: list[tuple[str, dict]] = []

    async def holder_call(profile_id: str, payload: dict):
        calls.append((profile_id, payload))
        return {
            "ok": True,
            "site_id": site.id,
            "target_profile": profile_id,
            "cookies_cleared": 2,
            "origins_cleared": 1,
        }

    async def should_not_launch(_profile):
        raise AssertionError("active holder must own the profile context")

    monkeypatch.setattr(debug_session, "profile_operation", holder_call)
    monkeypatch.setattr(manager, "_launch", should_not_launch)

    result = await manager.clear(site, "shared-main", profile)

    assert result.cookies_cleared == 2
    assert calls == [("shared-main", {"op": "auth_clear", "site_id": site.id})]
