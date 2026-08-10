from datetime import UTC, datetime

import pytest

from cloak_auth_bridge.config import Registry
from cloak_auth_bridge.converter import convert_cookie, validate_bundle
from cloak_auth_bridge.models import AuthBundle, ChromeCookie


def test_cookie_conversion_preserves_auth_fields() -> None:
    cookie = ChromeCookie.model_validate(
        {
            "name": "session",
            "value": "secret",
            "domain": ".bilibili.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "no_restriction",
            "expirationDate": 1_900_000_000,
            "partitionKey": {"topLevelSite": "https://bilibili.com"},
        }
    )

    assert convert_cookie(cookie) == {
        "name": "session",
        "value": "secret",
        "domain": ".bilibili.com",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "expires": 1_900_000_000,
        "sameSite": "None",
        "partitionKey": "https://bilibili.com",
    }


def test_session_cookie_does_not_gain_an_expiry() -> None:
    cookie = ChromeCookie.model_validate(
        {
            "name": "session",
            "value": "secret",
            "domain": ".bilibili.com",
            "session": True,
            "expirationDate": 1_900_000_000,
            "sameSite": "unspecified",
        }
    )

    converted = convert_cookie(cookie)
    assert "expires" not in converted
    assert "sameSite" not in converted


def test_bundle_rejects_domains_outside_daemon_registry() -> None:
    registry = Registry.load()
    site = registry.sites["bilibili-main"]
    bundle = AuthBundle.model_validate(
        {
            "version": 1,
            "siteId": "bilibili-main",
            "sourceProfile": "chrome-default",
            "capturedAt": datetime.now(UTC).isoformat(),
            "cookies": [{"name": "token", "value": "secret", "domain": ".evil.example"}],
            "origins": [{"origin": "https://www.bilibili.com", "localStorage": []}],
        }
    )

    with pytest.raises(ValueError, match="cookies outside"):
        validate_bundle(bundle, site)
