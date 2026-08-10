from __future__ import annotations

import json
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from cloak_auth_bridge import __version__
from cloak_auth_bridge.service import AuthService


def build_server(service: AuthService) -> Server:
    server = Server("cloak-auth-bridge")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="auth_list_sites",
                description="List registered Chrome authentication sources and their allowed Cloak targets.",
                inputSchema={"type": "object", "additionalProperties": False},
            ),
            types.Tool(
                name="auth_sync_to_cloak",
                description=(
                    "Capture an allowlisted login state from Chrome and import it directly into an "
                    "allowlisted Cloak profile. Raw authentication secrets are never returned."
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
                description="Clear one registered site's authentication state from a Cloak profile. Requires confirm=true.",
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
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name == "auth_list_sites":
            result = await service.list_sites()
        elif name == "auth_sync_to_cloak":
            result = await service.sync(
                arguments["site_id"],
                arguments["target_profile"],
                arguments.get("mode", "merge"),
            )
        elif name == "auth_verify_cloak":
            result = await service.verify(arguments["site_id"], arguments["target_profile"])
        elif name == "auth_clear_cloak":
            result = await service.clear(
                arguments["site_id"],
                arguments["target_profile"],
                arguments["confirm"],
            )
        else:
            raise ValueError(f"unknown tool: {name}")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

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
