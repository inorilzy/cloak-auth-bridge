from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cloak_browser_auth.extension_bridge import ExtensionBridge
from cloak_browser_auth.service import AuthOperations

LOGGER = logging.getLogger(__name__)


class ExtensionWebSocketServer:
    def __init__(
        self,
        bridge: ExtensionBridge,
        port: int = 17321,
        *,
        service: AuthOperations | None = None,
        client_token: str | None = None,
    ) -> None:
        self.bridge = bridge
        self.port = port
        self.service = service
        self.client_token = client_token

    async def handler(self, connection: ServerConnection) -> None:
        remote = connection.remote_address
        if not remote or remote[0] not in {"127.0.0.1", "::1"}:
            await connection.close(code=1008, reason="loopback connections only")
            return
        if connection.request is not None and connection.request.path == "/auth":
            await self._handle_auth_client(connection)
            return
        try:
            await self.bridge.handle(connection)
        except ConnectionClosed:
            pass
        except (TimeoutError, StopAsyncIteration, TypeError, ValueError, asyncio.InvalidStateError) as error:
            LOGGER.warning("Rejected extension connection: error_type=%s", type(error).__name__)
            await connection.close(code=1008, reason="authentication or protocol failure")

    async def _handle_auth_client(self, connection: ServerConnection) -> None:
        authorization = connection.request.headers.get("Authorization") if connection.request else None
        expected = f"Bearer {self.client_token}" if self.client_token else None
        if expected is None or authorization is None or not secrets.compare_digest(authorization, expected):
            await connection.send(json.dumps({"ok": False, "error": "authentication failed"}))
            await connection.close(code=1008, reason="authentication failed")
            return
        if self.service is None:
            await connection.close(code=1011, reason="auth service unavailable")
            return
        try:
            request = json.loads(await connection.recv())
            if not isinstance(request, dict):
                raise TypeError("request must be an object")
            operation = request.get("op")
            arguments = request.get("args", {})
            if not isinstance(arguments, dict):
                raise TypeError("args must be an object")
            if operation == "list_sites":
                result = await self.service.list_sites()
            elif operation == "sync":
                result = await self.service.sync(
                    arguments["site_id"],
                    arguments["target_profile"],
                    arguments.get("mode", "merge"),
                )
            elif operation == "verify":
                result = await self.service.verify(arguments["site_id"], arguments["target_profile"])
            elif operation == "clear":
                result = await self.service.clear(
                    arguments["site_id"],
                    arguments["target_profile"],
                    arguments["confirm"],
                )
            else:
                raise ValueError("unknown auth operation")
            response = {"ok": True, "result": result}
        except (KeyError, TypeError, ValueError, RuntimeError, TimeoutError) as error:
            LOGGER.warning("Auth Bridge request failed: error_type=%s", type(error).__name__)
            try:
                from cloak_browser_auth import config

                config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
                (config.AUTH_DIR / "last-auth-error.log").write_text(
                    f"{type(error).__name__}: {error}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
            message = str(error)
            if message.startswith(("Cloak ", "Chrome extension")):
                response = {"ok": False, "error": message}
            else:
                response = {"ok": False, "error": "Auth Bridge operation failed; inspect daemon diagnostics"}
        await connection.send(json.dumps(response, ensure_ascii=False))

    def serve(self) -> Any:
        return serve(
            self.handler,
            "127.0.0.1",
            self.port,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        )
