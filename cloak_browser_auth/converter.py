from __future__ import annotations

from typing import Any

from cloak_browser_auth.config import SiteConfig
from cloak_browser_auth.models import AuthBundle, ChromeCookie

SAME_SITE_MAP = {
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
}


def convert_cookie(cookie: ChromeCookie) -> dict[str, Any]:
    converted: dict[str, Any] = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path,
        "httpOnly": cookie.http_only,
        "secure": cookie.secure,
    }
    if not cookie.session and cookie.expiration_date is not None:
        converted["expires"] = cookie.expiration_date
    same_site = SAME_SITE_MAP.get(cookie.same_site or "")
    if same_site is not None:
        converted["sameSite"] = same_site
    top_level_site = (cookie.partition_key or {}).get("topLevelSite")
    if top_level_site:
        converted["partitionKey"] = top_level_site
    return converted


def validate_bundle(bundle: AuthBundle, site: SiteConfig) -> None:
    if bundle.site_id != site.id:
        raise ValueError("extension returned a mismatched site id")

    expected_origins = set(site.origins)
    actual_origins = {state.origin for state in bundle.origins}
    if actual_origins != expected_origins:
        raise ValueError("extension returned origins outside the site registry")

    for cookie in bundle.cookies:
        domain = cookie.domain.lower().lstrip(".")
        if not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in site.cookie_domains):
            raise ValueError("extension returned cookies outside the site registry")
