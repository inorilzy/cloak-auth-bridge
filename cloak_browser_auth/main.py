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
from cloak_browser_auth.debug_session import (
    close_session,
    list_tabs,
    new_tab,
    open_session,
    print_json,
    profile_operation,
    run_holder,
    status,
)
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


def build_runtime() -> tuple[ExtensionWebSocketServer, AuthService]:
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
    service = AuthService(registry, bridge, CloakProfileManager(registry, holder=profile_operation))
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
async def maybe_embed_auth_bridge() -> AsyncIterator[bool]:
    """Own :17321 in this process when free; otherwise attach to the existing listener."""
    if auth_bridge_listening():
        yield False
        return

    websocket_server, _service = build_runtime()
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
    async with maybe_embed_auth_bridge():
        await run_stdio(build_server(build_mcp_service(), ReverseSession()))


async def run_daemon() -> None:
    websocket_server, _service = build_runtime()
    async with websocket_server.serve():
        LOGGER.info("Listening on ws://127.0.0.1:17321")
        await asyncio.Event().wait()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloak Browser Auth local daemon")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("mcp", help="Run MCP stdio and start the auth bridge on :17321 if it is free")
    subparsers.add_parser("serve", help="Optional standalone auth bridge if you want the extension connected without an IDE")
    subparsers.add_parser("pair", help="Copy pairing token to clipboard")
    subparsers.add_parser("doctor", help="Validate local config")

    debug_open = subparsers.add_parser("debug-open", help="Start one headed debug browser session (Python holder + local control API)")
    debug_open.add_argument("--profile", default="shared-main")
    debug_open.add_argument("--port", type=int, default=19333)
    debug_open.add_argument("--url", action="append", default=[])

    debug_tab = subparsers.add_parser("debug-tab", help="Open a tab via the holder Python control API (no CDP)")
    debug_tab.add_argument("url")

    subparsers.add_parser("debug-list", help="List page tabs in the active debug session")
    subparsers.add_parser("debug-status", help="Show active debug session status")
    subparsers.add_parser("debug-close", help="Close the active debug session")

    debug_hold = subparsers.add_parser("debug-hold", help=argparse.SUPPRESS)
    debug_hold.add_argument("--profile", required=True)
    debug_hold.add_argument("--port", type=int, default=19333)
    debug_hold.add_argument("--instance-id", required=True)
    debug_hold.add_argument("--url", action="append", default=[])

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
        print("可选加固 Token 已复制到剪贴板。默认本机免 Token；仅在启用 CLOAK_BROWSER_AUTH_REQUIRE_TOKEN=1 时需要粘贴到扩展。")
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
    if args.command == "debug-open":
        print_json(open_session(args.profile, urls=args.url, port=args.port))
        return
    if args.command == "debug-tab":
        print_json(asyncio.run(new_tab(args.url)))
        return
    if args.command == "debug-list":
        print_json(list_tabs())
        return
    if args.command == "debug-status":
        print_json(status())
        return
    if args.command == "debug-close":
        print_json(close_session())
        return
    if args.command == "debug-hold":
        raise SystemExit(asyncio.run(run_holder(args.profile, args.port, args.url, args.instance_id)))
    try:
        asyncio.run(run_daemon() if args.command == "serve" else run_mcp())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
