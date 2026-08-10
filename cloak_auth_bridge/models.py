from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StorageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class OriginState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    local_storage: list[StorageItem] = Field(alias="localStorage")

    @field_validator("origin")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://") or value.endswith("/"):
            raise ValueError("origin must be canonical HTTPS origin")
        return value


class ChromeCookie(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")
    expiration_date: float | None = Field(default=None, alias="expirationDate")
    same_site: Literal["no_restriction", "lax", "strict", "unspecified"] | None = Field(
        default=None,
        alias="sameSite",
    )
    host_only: bool = Field(default=False, alias="hostOnly")
    session: bool = False
    store_id: str | None = Field(default=None, alias="storeId")
    partition_key: dict[str, Any] | None = Field(default=None, alias="partitionKey")


class AuthBundle(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    version: Literal[1]
    site_id: str = Field(alias="siteId")
    source_profile: str = Field(alias="sourceProfile")
    captured_at: datetime = Field(alias="capturedAt")
    cookies: list[ChromeCookie]
    origins: list[OriginState]


class ImportResult(BaseModel):
    ok: bool = True
    site_id: str
    target_profile: str
    cookies_imported: int
    origins_imported: int
    verified: bool


class ClearResult(BaseModel):
    ok: bool = True
    site_id: str
    target_profile: str
    cookies_cleared: int
    origins_cleared: int
