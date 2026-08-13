from __future__ import annotations

from typing import Any

import pytest

from cloak_browser_auth.auth_bridge_rpc import AuthBridgeClient
from cloak_browser_auth.extension_bridge import ExtensionBridge
from cloak_browser_auth.main import build_mcp_service, build_runtime
from cloak_browser_auth.websocket_server import ExtensionWebSocketServer


class FakeAuthService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_sites(self) -> dict[str, Any]:
        self.calls.append(("list_sites", ()))
        return {"sites": [{"site_id": "bilibili-main"}]}

    async def sync(self, site_id: str, target_profile: str, mode: str = "merge") -> dict[str, Any]:
        self.calls.append(("sync", (site_id, target_profile, mode)))
        return {"ok": True, "mode": mode}

    async def verify(self, site_id: str, target_profile: str) -> dict[str, Any]:
        self.calls.append(("verify", (site_id, target_profile)))
        return {"ok": True, "verified": True}

    async def clear(self, site_id: str, target_profile: str, confirm: bool) -> dict[str, Any]:
        self.calls.append(("clear", (site_id, target_profile, confirm)))
        return {"ok": True, "cookies_cleared": 1}


@pytest.mark.asyncio
async def test_authenticated_client_calls_auth_service_owned_by_daemon() -> None:
    token = "0123456789abcdef"
    service = FakeAuthService()
    server = ExtensionWebSocketServer(
        ExtensionBridge(),
        service=service,
        client_token=token,
        port=0,
    )

    async with server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        client = AuthBridgeClient(f"ws://127.0.0.1:{port}/auth", token)

        assert await client.list_sites() == {"sites": [{"site_id": "bilibili-main"}]}
        assert await client.sync("bilibili-main", "shared-main", "replace") == {
            "ok": True,
            "mode": "replace",
        }
        assert await client.verify("bilibili-main", "shared-main") == {
            "ok": True,
            "verified": True,
        }
        assert await client.clear("bilibili-main", "shared-main", True) == {
            "ok": True,
            "cookies_cleared": 1,
        }

    assert service.calls == [
        ("list_sites", ()),
        ("sync", ("bilibili-main", "shared-main", "replace")),
        ("verify", ("bilibili-main", "shared-main")),
        ("clear", ("bilibili-main", "shared-main", True)),
    ]


@pytest.mark.asyncio
async def test_client_rejects_daemon_when_shared_token_does_not_match() -> None:
    server = ExtensionWebSocketServer(
        ExtensionBridge(),
        service=FakeAuthService(),
        client_token="correct-token-123",
        port=0,
    )

    async with server.serve() as running_server:
        port = running_server.sockets[0].getsockname()[1]
        client = AuthBridgeClient(f"ws://127.0.0.1:{port}/auth", "wrong-token-456")

        with pytest.raises(ConnectionError, match="authentication failed"):
            await client.list_sites()


def test_mcp_builds_client_while_serve_builds_websocket_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOAK_BROWSER_AUTH_CLIENT_TOKEN", "local-client-token")

    websocket_server, service = build_runtime()
    mcp_service = build_mcp_service()

    assert websocket_server.service is service
    assert websocket_server.client_token == "local-client-token"
    assert isinstance(mcp_service, AuthBridgeClient)


@pytest.mark.asyncio
async def test_mcp_embeds_auth_bridge_when_port_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    from cloak_browser_auth import main

    listening = {"up": False}
    entered: list[str] = []

    class FakeServe:
        async def __aenter__(self):
            listening["up"] = True
            entered.append("enter")
            return self

        async def __aexit__(self, *_args: object) -> bool:
            entered.append("exit")
            listening["up"] = False
            return False

    class FakeServer:
        port = 17321

        def serve(self) -> FakeServe:
            return FakeServe()

    monkeypatch.setattr(main, "auth_bridge_listening", lambda port=17321, host="127.0.0.1": listening["up"])
    monkeypatch.setattr(main, "build_runtime", lambda session=None: (FakeServer(), object()))

    async with main.maybe_embed_auth_bridge() as embedded:
        assert embedded is True
        assert entered == ["enter"]

    assert entered == ["enter", "exit"]


@pytest.mark.asyncio
async def test_mcp_attaches_when_auth_bridge_already_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    from cloak_browser_auth import main

    monkeypatch.setattr(main, "auth_bridge_listening", lambda port=17321, host="127.0.0.1": True)

    def boom() -> None:
        raise AssertionError("should not start a second auth bridge")

    monkeypatch.setattr(main, "build_runtime", boom)

    async with main.maybe_embed_auth_bridge() as embedded:
        assert embedded is False


@pytest.mark.asyncio
async def test_mcp_attaches_if_embed_loses_the_port_race(monkeypatch: pytest.MonkeyPatch) -> None:
    from cloak_browser_auth import main

    listening = {"up": False}

    class FakeServe:
        async def __aenter__(self):
            listening["up"] = True
            raise OSError("address already in use")

        async def __aexit__(self, *_args: object) -> bool:
            return False

    class FakeServer:
        port = 17321

        def serve(self) -> FakeServe:
            return FakeServe()

    monkeypatch.setattr(main, "auth_bridge_listening", lambda port=17321, host="127.0.0.1": listening["up"])
    monkeypatch.setattr(main, "build_runtime", lambda session=None: (FakeServer(), object()))

    async with main.maybe_embed_auth_bridge() as embedded:
        assert embedded is False
