from datetime import UTC, datetime

import pytest

from cloak_browser_auth.config import Registry
from cloak_browser_auth.models import AuthBundle, ClearResult, ImportResult
from cloak_browser_auth.service import AuthService


class FakeBridge:
    connected = True

    def __init__(self) -> None:
        self.capture_calls = 0

    async def capture(self, site_id: str, cookie_domains: list[str], origins: list[str]) -> AuthBundle:
        assert cookie_domains
        assert origins
        self.capture_calls += 1
        return AuthBundle.model_validate(
            {
                "version": 1,
                "siteId": site_id,
                "sourceProfile": "chrome-default",
                "capturedAt": datetime.now(UTC).isoformat(),
                "cookies": [{"name": "session", "value": "secret", "domain": ".bilibili.com"}],
                "origins": [{"origin": "https://www.bilibili.com", "localStorage": []}],
            }
        )


class FakeProfiles:
    async def import_auth(self, site, profile_id, profile, bundle, mode):
        assert bundle.cookies[0].value == "secret"
        return ImportResult(
            site_id=site.id,
            target_profile=profile_id,
            cookies_imported=1,
            origins_imported=1,
            verified=True,
        )

    async def verify(self, site, profile_id, profile):
        return True

    async def clear(self, site, profile_id, profile):
        return ClearResult(
            site_id=site.id,
            target_profile=profile_id,
            cookies_cleared=1,
            origins_cleared=1,
        )


@pytest.mark.asyncio
async def test_sync_returns_counts_but_never_raw_secrets() -> None:
    registry = Registry.load()
    service = AuthService(registry, FakeBridge(), FakeProfiles())

    result = await service.sync("bilibili-main", "bilibili-main")

    assert result["verified"] is True
    assert result["cookies_imported"] == 1
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_clear_requires_explicit_confirmation() -> None:
    registry = Registry.load()
    service = AuthService(registry, FakeBridge(), FakeProfiles())

    with pytest.raises(ValueError, match="confirm=true"):
        await service.clear("bilibili-main", "bilibili-main", False)


@pytest.mark.asyncio
async def test_invalid_mode_is_rejected_before_capture() -> None:
    registry = Registry.load()
    bridge = FakeBridge()
    service = AuthService(registry, bridge, FakeProfiles())

    with pytest.raises(ValueError, match="mode must be merge or replace"):
        await service.sync("bilibili-main", "bilibili-main", "invalid")

    assert bridge.capture_calls == 0


@pytest.mark.asyncio
async def test_authentication_operations_are_rate_limited() -> None:
    registry = Registry.load()
    service = AuthService(registry, FakeBridge(), FakeProfiles())

    await service.sync("bilibili-main", "bilibili-main")

    with pytest.raises(RuntimeError, match="rate limit"):
        await service.verify("bilibili-main", "bilibili-main")
