from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from cloak_browser_auth.cloak_profiles import CloakProfileManager
from cloak_browser_auth.config import Registry
from cloak_browser_auth.debug_session import (
    close_session,
    list_tabs,
    new_tab,
    open_session,
    print_json,
    run_holder,
    status,
)
from cloak_browser_auth.extension_bridge import ExtensionBridge
from cloak_browser_auth.mcp_server import build_server, run_stdio
from cloak_browser_auth.secret_store import copy_token_to_clipboard, load_or_create_token
from cloak_browser_auth.service import AuthService
from cloak_browser_auth.websocket_server import ExtensionWebSocketServer


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
    websocket_server = ExtensionWebSocketServer(bridge)
    service = AuthService(registry, bridge, CloakProfileManager(registry))
    return websocket_server, service


async def run_mcp() -> None:
    websocket_server, service = build_runtime()
    async with websocket_server.serve():
        await run_stdio(build_server(service))


async def run_daemon() -> None:
    websocket_server, _service = build_runtime()
    async with websocket_server.serve():
        logging.getLogger(__name__).info("Listening on ws://127.0.0.1:17321")
        await asyncio.Event().wait()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloak Browser Auth local daemon")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("mcp", help="Run MCP stdio + websocket bridge")
    subparsers.add_parser("serve", help="Run websocket bridge only")
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
        raise SystemExit(asyncio.run(run_holder(args.profile, args.port, args.url)))
    try:
        asyncio.run(run_daemon() if args.command == "serve" else run_mcp())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
