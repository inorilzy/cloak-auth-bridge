from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_ROOT / "defaults"
HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _looks_like_project_root(path: Path) -> bool:
    return (path / "sites").is_dir() and (path / "profiles.json").is_file()


def resolve_data_root() -> Path:
    """Resolve runtime data root for sites/profiles/auth/profile storage.

    Priority:
    1. CLOAK_AUTH_BRIDGE_HOME
    2. Current working directory when it is a checkout (has sites/ + profiles.json)
    3. Parent of package when running from a source checkout
    4. ~/.cloak-auth-bridge (seeded from packaged defaults on first use)
    """
    env_home = os.environ.get("CLOAK_AUTH_BRIDGE_HOME", "").strip()
    if env_home:
        root = Path(env_home).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        ensure_user_data(root)
        return root

    cwd = Path.cwd().resolve()
    if _looks_like_project_root(cwd):
        return cwd

    source_root = PACKAGE_ROOT.parent
    if _looks_like_project_root(source_root):
        return source_root

    root = (Path.home() / ".cloak-auth-bridge").resolve()
    root.mkdir(parents=True, exist_ok=True)
    ensure_user_data(root)
    return root


def ensure_user_data(root: Path) -> None:
    """Seed missing sites/profiles/extension into a user data root."""
    sites_dir = root / "sites"
    profiles_file = root / "profiles.json"
    extension_dir = root / "extension"
    profiles_dir = root / "profiles"
    auth_dir = root / ".auth"

    sites_dir.mkdir(parents=True, exist_ok=True)
    profiles_dir.mkdir(parents=True, exist_ok=True)
    auth_dir.mkdir(parents=True, exist_ok=True)

    default_sites = DEFAULTS_DIR / "sites"
    if default_sites.is_dir():
        for src in default_sites.glob("*.json"):
            dest = sites_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)

    default_profiles = DEFAULTS_DIR / "profiles.json"
    if default_profiles.is_file() and not profiles_file.exists():
        shutil.copy2(default_profiles, profiles_file)

    default_extension = DEFAULTS_DIR / "extension"
    if default_extension.is_dir() and not (extension_dir / "manifest.json").exists():
        if extension_dir.exists():
            shutil.rmtree(extension_dir)
        shutil.copytree(default_extension, extension_dir)


# Lazily resolved module-level paths keep tests and source checkouts working.
PROJECT_ROOT = resolve_data_root()
SITES_DIR = PROJECT_ROOT / "sites"
PROFILES_FILE = PROJECT_ROOT / "profiles.json"
PROFILES_DIR = PROJECT_ROOT / "profiles"
AUTH_DIR = PROJECT_ROOT / ".auth"


def refresh_paths() -> None:
    """Re-resolve paths (useful after env/cwd changes in long-lived processes)."""
    global PROJECT_ROOT, SITES_DIR, PROFILES_FILE, PROFILES_DIR, AUTH_DIR
    PROJECT_ROOT = resolve_data_root()
    SITES_DIR = PROJECT_ROOT / "sites"
    PROFILES_FILE = PROJECT_ROOT / "profiles.json"
    PROFILES_DIR = PROJECT_ROOT / "profiles"
    AUTH_DIR = PROJECT_ROOT / ".auth"


class VerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str
    json_path: str | None = Field(default=None, alias="jsonPath")
    equals: bool | int | str | None = None
    final_url_includes: list[str] = Field(default_factory=list, alias="finalUrlIncludes")
    final_url_excludes: list[str] = Field(default_factory=list, alias="finalUrlExcludes")

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("verify.url must use HTTPS")
        return value

    @model_validator(mode="after")
    def require_json_or_url_checks(self) -> VerifyConfig:
        has_json = self.json_path is not None
        has_url_check = bool(self.final_url_includes or self.final_url_excludes)
        if has_json and self.equals is None:
            raise ValueError("verify.equals is required when jsonPath is set")
        if not has_json and not has_url_check:
            raise ValueError("verify requires jsonPath/equals or finalUrlIncludes/finalUrlExcludes")
        if has_json and has_url_check:
            raise ValueError("verify cannot combine jsonPath with finalUrl checks")
        return self


class SiteConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    cookie_domains: list[str] = Field(alias="cookieDomains", min_length=1)
    origins: list[str] = Field(min_length=1)
    verify: VerifyConfig

    @field_validator("cookie_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized = [value.lower().lstrip(".") for value in values]
        if any(not HOSTNAME_PATTERN.fullmatch(value) for value in normalized):
            raise ValueError("cookieDomains must contain host names only")
        return list(dict.fromkeys(normalized))

    @field_validator("origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or parsed.netloc == "" or parsed.path not in {"", "/"}:
                raise ValueError("origins must be canonical HTTPS origins")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("origins must not include credentials/query/fragment")
        # Canonicalize by dropping trailing slash.
        values = [value.rstrip("/") for value in values]
        return list(dict.fromkeys(values))


class ProfileConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    path: str
    allowed_sites: list[str] = Field(alias="allowedSites", min_length=1)
    dedicated: bool = True
    headless: bool = False

    @model_validator(mode="after")
    def dedicated_profile_has_one_site(self) -> ProfileConfig:
        if self.dedicated and len(self.allowed_sites) != 1:
            raise ValueError("dedicated profiles must allow exactly one site")
        return self


class Registry:
    def __init__(self, sites: dict[str, SiteConfig], profiles: dict[str, ProfileConfig]) -> None:
        self.sites = sites
        self.profiles = profiles

    @classmethod
    def load(cls) -> Registry:
        refresh_paths()
        if not SITES_DIR.is_dir():
            raise FileNotFoundError(f"sites directory not found: {SITES_DIR}")
        if not PROFILES_FILE.is_file():
            raise FileNotFoundError(f"profiles.json not found: {PROFILES_FILE}")

        sites: dict[str, SiteConfig] = {}
        for path in sorted(SITES_DIR.glob("*.json")):
            site = SiteConfig.model_validate_json(path.read_text(encoding="utf-8"))
            if site.id in sites:
                raise ValueError(f"duplicate site id: {site.id}")
            sites[site.id] = site

        raw_profiles = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        profiles = {
            profile_id: ProfileConfig.model_validate(value) for profile_id, value in raw_profiles.items()
        }
        for profile_id, profile in profiles.items():
            unknown = set(profile.allowed_sites) - sites.keys()
            if unknown:
                raise ValueError(f"profile {profile_id} references unknown sites: {sorted(unknown)}")
            cls.resolve_profile_path(profile)
        return cls(sites, profiles)

    @staticmethod
    def resolve_profile_path(profile: ProfileConfig) -> Path:
        base = PROFILES_DIR.resolve()
        # Profile paths are stored relative to data root, e.g. profiles/foo
        candidate = Path(profile.path)
        resolved = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError("profile path must stay inside profiles/")
        return resolved

    def target(self, site_id: str, profile_id: str) -> tuple[SiteConfig, ProfileConfig]:
        site = self.sites.get(site_id)
        if site is None:
            raise ValueError(f"unknown site_id: {site_id}")
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"unknown target_profile: {profile_id}")
        if site_id not in profile.allowed_sites:
            raise ValueError(f"site {site_id} is not allowed for profile {profile_id}")
        return site, profile
