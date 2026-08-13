from __future__ import annotations

import json
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


class AuthBridgeClient:
    """Small authenticated client for the auth service owned by `serve`."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"}

    async def list_sites(self) -> dict[str, Any]:
        return await self._call("list_sites")

    async def sync(self, site_id: str, target_profile: str, mode: str = "merge") -> dict[str, Any]:
        return await self._call(
            "sync",
            site_id=site_id,
            target_profile=target_profile,
            mode=mode,
        )

    async def verify(self, site_id: str, target_profile: str) -> dict[str, Any]:
        return await self._call("verify", site_id=site_id, target_profile=target_profile)

    async def clear(self, site_id: str, target_profile: str, confirm: bool) -> dict[str, Any]:
        return await self._call(
            "clear",
            site_id=site_id,
            target_profile=target_profile,
            confirm=confirm,
        )


    async def _call(self, operation: str, **arguments: Any) -> dict[str, Any]:
        try:
            async with connect(
                self._url,
                additional_headers=self._headers,
                open_timeout=3,
                proxy=None,
            ) as websocket:
                await websocket.send(json.dumps({"op": operation, "args": arguments}))
                raw = await websocket.recv()
        except ConnectionClosed as error:
            raise ConnectionError("Auth Bridge authentication failed") from error
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = (
                response.get("error", "invalid Auth Bridge response")
                if isinstance(response, dict)
                else "invalid Auth Bridge response"
            )
            if message == "authentication failed":
                raise ConnectionError("Auth Bridge authentication failed")
            raise RuntimeError(str(message))
        result = response.get("result")
        if not isinstance(result, dict):
            raise TypeError("invalid Auth Bridge result")
        return result
