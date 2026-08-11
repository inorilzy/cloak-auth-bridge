from __future__ import annotations

import asyncio
import json
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from cloak_auth_bridge import __version__, debug_session
from cloak_auth_bridge.service import AuthService


def _json_content(result: dict[str, Any]) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def build_server(service: AuthService) -> Server:
    server = Server("cloak-auth-bridge")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="auth_list_sites",
                description=(
                    "List registered Chrome authentication sources, whether the extension bridge is "
                    "connected, and each site's allowed Cloak targets."
                ),
                inputSchema={"type": "object", "additionalProperties": False},
            ),
            types.Tool(
                name="auth_sync_to_cloak",
                description=(
                    "Capture an allowlisted login state from the connected Chrome extension and import "
                    "it into an allowlisted Cloak profile. Raw authentication secrets are never returned."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "site_id": {"type": "string"},
                        "target_profile": {"type": "string"},
                        "mode": {"type": "string", "enum": ["merge", "replace"], "default": "merge"},
                    },
                    "required": ["site_id", "target_profile"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="auth_verify_cloak",
                description="Verify whether an allowlisted Cloak profile is logged in to a registered site.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "site_id": {"type": "string"},
                        "target_profile": {"type": "string"},
                    },
                    "required": ["site_id", "target_profile"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="auth_clear_cloak",
                description=(
                    "Clear one registered site's authentication state from a Cloak profile. "
                    "Requires confirm=true."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "site_id": {"type": "string"},
                        "target_profile": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["site_id", "target_profile", "confirm"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="cloak_debug_open",
                description=(
                    "Open one headed CloakBrowser session on an allowlisted auth profile and expose "
                    "local CDP (default http://127.0.0.1:9333). Attach js-reverse-mcp with "
                    "--browserUrl (NOT --cloak) so reverse/debug tools share this Cloak process. "
                    "Returns js_reverse attach hints. Free plan allows only one browser session."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string", "default": "shared-main"},
                        "url": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional HTTPS URLs to open as initial tabs.",
                        },
                        "port": {"type": "integer", "default": 9333},
                    },
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="cloak_debug_tab",
                description=(
                    "Open another HTTPS tab in the active Cloak debug session via local CDP attach. "
                    "Does not start a second browser process."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            types.Tool(
                name="cloak_debug_list",
                description="List page tabs in the active Cloak debug session.",
                inputSchema={"type": "object", "additionalProperties": False},
            ),
            types.Tool(
                name="cloak_debug_status",
                description=(
                    "Show whether a Cloak debug session is active, including CDP endpoint and "
                    "js-reverse --browserUrl attach instructions when running."
                ),
                inputSchema={"type": "object", "additionalProperties": False},
            ),
            types.Tool(
                name="cloak_debug_close",
                description="Close the active Cloak debug session and release the Free-plan browser seat.",
                inputSchema={"type": "object", "additionalProperties": False},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        args = arguments or {}
        if name == "auth_list_sites":
            result = await service.list_sites()
        elif name == "auth_sync_to_cloak":
            result = await service.sync(
                args["site_id"],
                args["target_profile"],
                args.get("mode", "merge"),
            )
        elif name == "auth_verify_cloak":
            result = await service.verify(args["site_id"], args["target_profile"])
        elif name == "auth_clear_cloak":
            result = await service.clear(
                args["site_id"],
                args["target_profile"],
                args["confirm"],
            )
        elif name == "cloak_debug_open":
            result = await asyncio.to_thread(
                debug_session.open_session,
                args.get("profile_id", "shared-main"),
                args.get("url") or [],
                int(args.get("port", 9333)),
            )
        elif name == "cloak_debug_tab":
            result = await debug_session.new_tab(args["url"])
        elif name == "cloak_debug_list":
            result = await asyncio.to_thread(debug_session.list_tabs)
        elif name == "cloak_debug_status":
            result = await asyncio.to_thread(debug_session.status)
        elif name == "cloak_debug_close":
            result = await asyncio.to_thread(debug_session.close_session)
        else:
            raise ValueError(f"unknown tool: {name}")
        return _json_content(result)

    return server


async def run_stdio(server: Server) -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="cloak-auth-bridge",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
