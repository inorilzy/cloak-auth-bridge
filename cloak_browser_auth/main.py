from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cloak_browser_auth.auth_bridge_rpc import AuthBridgeClient
from cloak_browser_auth.cloak_profiles import CloakProfileManager
from cloak_browser_auth.config import Registry
from cloak_browser_auth.extension_bridge import ExtensionBridge
from cloak_browser_auth.mcp_server import build_server, run_stdio
from cloak_browser_auth.reverse_session import ReverseSession
from cloak_browser_auth.secret_store import copy_token_to_clipboard, load_or_create_token
from cloak_browser_auth.service import AuthService
from cloak_browser_auth.websocket_server import ExtensionWebSocketServer

AUTH_BRIDGE_PORT = 17321
AUTH_BRIDGE_URL = f"ws://127.0.0.1:{AUTH_BRIDGE_PORT}/auth"
LOGGER = logging.getLogger(__name__)


def _client_token() -> str:
    token = os.environ.get("CLOAK_BROWSER_AUTH_CLIENT_TOKEN", "").strip()
    return token or load_or_create_token()


def build_runtime(session: ReverseSession | None = None) -> tuple[ExtensionWebSocketServer, AuthService]:
    registry = Registry.load()
    # Default: loopback trust, no manual token paste.
    # Set CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1 to force HMAC pairing token mode.
    require_token = os.environ.get("CLOAK_BROWSER_AUTH_REQUIRE_TOKEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    token = load_or_create_token() if require_token else None
    bridge = ExtensionBridge(token, allow_loopback_trust=not require_token)
    holder = session.profile_operation if session is not None else None
    service = AuthService(registry, bridge, CloakProfileManager(registry, holder=holder))
    websocket_server = ExtensionWebSocketServer(
        bridge,
        service=service,
        client_token=_client_token(),
    )
    return websocket_server, service


def build_mcp_service() -> AuthBridgeClient:
    return AuthBridgeClient(AUTH_BRIDGE_URL, _client_token())


def auth_bridge_listening(port: int = AUTH_BRIDGE_PORT, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


async def _wait_for_auth_bridge(port: int = AUTH_BRIDGE_PORT, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if auth_bridge_listening(port):
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"auth bridge on 127.0.0.1:{port} did not become ready")


@asynccontextmanager
async def maybe_embed_auth_bridge(session: ReverseSession | None = None) -> AsyncIterator[bool]:
    """Own :17321 in this process when free; otherwise attach to the existing listener."""
    if auth_bridge_listening():
        yield False
        return

    websocket_server, _service = build_runtime(session)
    try:
        async with websocket_server.serve():
            await _wait_for_auth_bridge(websocket_server.port)
            LOGGER.info("Embedded auth bridge on ws://127.0.0.1:%s", websocket_server.port)
            yield True
    except OSError:
        if auth_bridge_listening():
            LOGGER.info("Auth bridge already listening; attaching as client")
            yield False
            return
        raise


async def run_mcp() -> None:
    session = ReverseSession()
    try:
        async with maybe_embed_auth_bridge(session):
            await run_stdio(build_server(build_mcp_service(), session))
    finally:
        await session.close_owned()


async def run_daemon() -> None:
    websocket_server, _service = build_runtime()
    async with websocket_server.serve():
        LOGGER.info("Listening on ws://127.0.0.1:17321")
        await asyncio.Event().wait()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloak Browser Auth local daemon")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("mcp", help="Run MCP stdio, auth bridge, and in-process CloakBrowser")
    subparsers.add_parser(
        "serve",
        help="Optional standalone auth bridge if you want the extension connected without an IDE",
    )
    subparsers.add_parser("pair", help="Copy pairing token to clipboard")
    subparsers.add_parser("doctor", help="Validate local config")

    parser.set_defaults(command="mcp")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "pair":
        copy_token_to_clipboard(load_or_create_token())
        print(
            "可选加固 Token 已复制到剪贴板。默认本机免 Token；"
            "仅在启用 CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1 时需要粘贴到扩展。"
        )
        return
    if args.command == "doctor":
        from cloak_browser_auth.config import PROJECT_ROOT, refresh_paths

        refresh_paths()
        registry = Registry.load()
        print(
            f"配置有效：{len(registry.sites)} 个站点，{len(registry.profiles)} 个 Profile。"
            f" data_root={PROJECT_ROOT}"
            " 默认本机免 Token（loopback trust）。"
        )
        return
    try:
        asyncio.run(run_daemon() if args.command == "serve" else run_mcp())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
