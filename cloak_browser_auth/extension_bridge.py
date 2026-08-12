from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from cloak_browser_auth.crypto import create_proof, verify_proof
from cloak_browser_auth.models import AuthBundle

LOGGER = logging.getLogger(__name__)


class WebSocketConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class HelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    extension_id: str = Field(alias="extension_id")
    profile_alias: str = Field(alias="profile_alias")
    challenge: str = Field(min_length=16, max_length=256)


class HelloResponseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    client_challenge: str = Field(min_length=16, max_length=256)
    server_challenge: str = Field(min_length=16, max_length=256)
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaptureResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    nonce: str | None = None
    ok: bool
    payload: AuthBundle | None = None
    error: str | None = None


class ExtensionBridge:
    def __init__(
        self,
        pairing_token: str | None = None,
        *,
        allow_loopback_trust: bool = True,
    ) -> None:
        # Loopback trust is the default for local MCP: only 127.0.0.1 may connect,
        # so a manual pairing token is optional hardening rather than required UX.
        self._pairing_token = pairing_token
        self._allow_loopback_trust = allow_loopback_trust
        self._connection: WebSocketConnection | None = None
        self._extension_id: str | None = None
        self._pending: dict[
            str,
            tuple[str, WebSocketConnection, asyncio.Future[AuthBundle]],
        ] = {}
        self._connection_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connection is not None

    @property
    def extension_id(self) -> str | None:
        return self._extension_id

    async def handle(self, connection: WebSocketConnection) -> None:
        iterator = connection.__aiter__()
        raw_hello = await asyncio.wait_for(anext(iterator), timeout=10)
        hello = HelloMessage.model_validate_json(raw_hello)
        if hello.type != "hello":
            raise ValueError("invalid extension hello")

        server_challenge = secrets.token_urlsafe(24)
        if self._allow_loopback_trust and not self._pairing_token:
            await connection.send(
                json.dumps(
                    {
                        "type": "hello_ack",
                        "ok": True,
                        "mode": "loopback_trust",
                        "client_challenge": hello.challenge,
                        "server_challenge": server_challenge,
                    }
                )
            )
        else:
            if not self._pairing_token:
                raise ValueError("pairing token required when loopback trust is disabled")
            proof_payload = f"{hello.challenge}:{server_challenge}"
            await connection.send(
                json.dumps(
                    {
                        "type": "hello_challenge",
                        "client_challenge": hello.challenge,
                        "server_challenge": server_challenge,
                        "proof": create_proof(self._pairing_token, "server", proof_payload),
                    }
                )
            )

            raw_response = await asyncio.wait_for(anext(iterator), timeout=10)
            response = HelloResponseMessage.model_validate_json(raw_response)
            if (
                response.type != "hello_response"
                or response.client_challenge != hello.challenge
                or response.server_challenge != server_challenge
                or not verify_proof(
                    self._pairing_token,
                    "client",
                    proof_payload,
                    response.proof,
                )
            ):
                raise ValueError("extension pairing authentication failed")

            await connection.send(
                json.dumps(
                    {
                        "type": "hello_ack",
                        "ok": True,
                        "mode": "token",
                        "client_challenge": hello.challenge,
                        "server_challenge": server_challenge,
                    }
                )
            )

        async with self._connection_lock:
            previous = self._connection
            self._connection = connection
            self._extension_id = hello.extension_id
            if previous is not None and previous is not connection:
                LOGGER.warning("Replacing an existing authenticated extension connection")
                self._fail_pending(ConnectionError("Chrome extension connection was replaced"))
                await previous.close(code=1000, reason="replaced by a new authenticated connection")

        mode = "loopback_trust" if self._allow_loopback_trust and not self._pairing_token else "token"
        LOGGER.info(
            "Chrome extension connected: id=%s profile=%s mode=%s",
            hello.extension_id,
            hello.profile_alias,
            mode,
        )
        try:
            async for raw_message in iterator:
                await self._handle_message(connection, raw_message)
        finally:
            async with self._connection_lock:
                if self._connection is connection:
                    self._connection = None
                    self._extension_id = None
                    self._fail_pending(ConnectionError("Chrome extension disconnected"))
            LOGGER.info("Chrome extension disconnected")

    async def _handle_message(self, connection: WebSocketConnection, raw_message: str | bytes) -> None:
        raw = json.loads(raw_message)
        if raw.get("type") == "ping":
            await connection.send(json.dumps({"type": "pong", "at": raw.get("at")}))
            return
        if raw.get("type") != "capture_auth_result":
            raise ValueError("unsupported extension message type")

        request_id = raw.get("id")
        if not isinstance(request_id, str) or request_id not in self._pending:
            return
        result = CaptureResultMessage.model_validate(raw)
        pending = self._pending.pop(result.id, None)
        if pending is None:
            return
        expected_nonce, expected_connection, future = pending
        if future.done():
            return
        if expected_connection is not connection:
            future.set_exception(ValueError("capture response came from the wrong extension connection"))
            return
        if result.nonce != expected_nonce:
            future.set_exception(ValueError("capture response nonce mismatch"))
            return
        if not result.ok:
            future.set_exception(RuntimeError((result.error or "capture failed")[:300]))
            return
        if result.payload is None:
            future.set_exception(ValueError("capture response has no payload"))
            return
        future.set_result(result.payload)

    async def capture(
        self,
        site_id: str,
        cookie_domains: list[str],
        origins: list[str],
        timeout: float = 60,
    ) -> AuthBundle:
        connection = self._connection
        if connection is None:
            raise RuntimeError("Chrome extension is not connected")
        if not cookie_domains:
            raise ValueError("cookie_domains must not be empty")
        if not origins:
            raise ValueError("origins must not be empty")

        request_id = str(uuid.uuid4())
        nonce = secrets.token_urlsafe(24)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (nonce, connection, future)
        try:
            await connection.send(
                json.dumps(
                    {
                        "id": request_id,
                        "type": "capture_auth",
                        "site_id": site_id,
                        "cookie_domains": list(cookie_domains),
                        "origins": list(origins),
                        "nonce": nonce,
                    }
                )
            )
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    def _fail_pending(self, error: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for _nonce, _connection, future in pending:
            if not future.done():
                future.set_exception(error)
