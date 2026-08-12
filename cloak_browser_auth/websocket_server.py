from __future__ import annotations

import asyncio
import logging
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cloak_browser_auth.extension_bridge import ExtensionBridge

LOGGER = logging.getLogger(__name__)


class ExtensionWebSocketServer:
    def __init__(self, bridge: ExtensionBridge, port: int = 17321) -> None:
        self.bridge = bridge
        self.port = port

    async def handler(self, connection: ServerConnection) -> None:
        remote = connection.remote_address
        if not remote or remote[0] not in {"127.0.0.1", "::1"}:
            await connection.close(code=1008, reason="loopback connections only")
            return
        try:
            await self.bridge.handle(connection)
        except ConnectionClosed:
            pass
        except (TimeoutError, StopAsyncIteration, TypeError, ValueError, asyncio.InvalidStateError) as error:
            LOGGER.warning("Rejected extension connection: error_type=%s", type(error).__name__)
            await connection.close(code=1008, reason="authentication or protocol failure")

    def serve(self) -> Any:
        return serve(
            self.handler,
            "127.0.0.1",
            self.port,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        )
