from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from cloak_auth_bridge.cloak_profiles import CloakProfileManager
from cloak_auth_bridge.config import Registry
from cloak_auth_bridge.extension_bridge import ExtensionBridge
from cloak_auth_bridge.mcp_server import build_server, run_stdio
from cloak_auth_bridge.secret_store import copy_token_to_clipboard, load_or_create_token
from cloak_auth_bridge.service import AuthService
from cloak_auth_bridge.websocket_server import ExtensionWebSocketServer


def build_runtime() -> tuple[ExtensionWebSocketServer, AuthService]:
    registry = Registry.load()
    bridge = ExtensionBridge(load_or_create_token())
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloak Auth Bridge local daemon")
    parser.add_argument("command", choices=["mcp", "serve", "pair", "doctor"], nargs="?", default="mcp")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "pair":
        copy_token_to_clipboard(load_or_create_token())
        print("Pairing Token 已复制到剪贴板；请直接粘贴到扩展，不要发送给 LLM。")
        return
    if args.command == "doctor":
        registry = Registry.load()
        load_or_create_token()
        print(f"配置有效：{len(registry.sites)} 个站点，{len(registry.profiles)} 个 Profile。")
        return
    try:
        asyncio.run(run_daemon() if args.command == "serve" else run_mcp())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
