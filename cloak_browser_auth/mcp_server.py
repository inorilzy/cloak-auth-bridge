from __future__ import annotations

import json
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from cloak_browser_auth import __version__
from cloak_browser_auth.auth_bridge_rpc import AuthBridgeClient
from cloak_browser_auth.reverse_session import ReverseSession


def _json_content(result: dict[str, Any]) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def _tool(name: str, description: str, schema: dict[str, Any]) -> types.Tool:
    return types.Tool(name=name, description=description, inputSchema=schema)


def build_server(service: AuthBridgeClient, session: ReverseSession | None = None) -> Server:
    server = Server("cloak-browser-auth")
    reverse = session or ReverseSession()

    empty = {"type": "object", "additionalProperties": False}

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            # ---- auth bridge ----
            _tool(
                "auth_list_sites",
                "List registered Chrome authentication sources, extension connection, and allowed Cloak targets.",
                empty,
            ),
            _tool(
                "auth_sync_to_cloak",
                "Capture allowlisted login state from the connected Chrome extension into a Cloak profile. Secrets are never returned.",
                {
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
            _tool(
                "auth_verify_cloak",
                "Verify whether an allowlisted Cloak profile is logged in to a registered site.",
                {
                    "type": "object",
                    "properties": {
                        "site_id": {"type": "string"},
                        "target_profile": {"type": "string"},
                    },
                    "required": ["site_id", "target_profile"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "auth_clear_cloak",
                "Clear one registered site's authentication state from a Cloak profile. Requires confirm=true.",
                {
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
            # ---- cloak session lifecycle ----
            _tool(
                "cloak_debug_open",
                "Start or reuse one headed CloakBrowser in this MCP process. Closing MCP closes the window.",
                {
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string", "default": "shared-main"},
                        "url": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "cloak_debug_tab",
                "Open another HTTPS tab on the Playwright data plane. Requires cloak_debug_open first.",
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            _tool("cloak_debug_list", "List page tabs in the active Cloak session.", empty),
            _tool("cloak_debug_status", "Show Cloak session/CDP status.", empty),
            _tool(
                "cloak_debug_close",
                "Close the active Cloak session and release the browser seat. Requires confirm=true.",
                {
                    "type": "object",
                    "properties": {
                        "profile_id": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["profile_id", "confirm"],
                    "additionalProperties": False,
                },
            ),
            # ---- reverse session attach ----
            _tool(
                "reverse_attach",
                "Optional/legacy: attach reverse tooling to an external CDP endpoint. Prefer cloak_debug_open, which connects to the independent browser holder.",
                {
                    "type": "object",
                    "properties": {"cdp_http": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
            _tool("reverse_detach", "Detach reverse tooling without closing the Cloak browser.", empty),
            _tool("reverse_status", "Show reverse tooling attachment and collector counters.", empty),
            # ---- pages / navigation / interaction ----
            _tool(
                "select_page",
                "List open pages or select page_idx as the reverse-tooling target.",
                {
                    "type": "object",
                    "properties": {
                        "page_idx": {"type": "integer"},
                        "page_size": {"type": "integer", "default": 20},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "new_page",
                "Open a new page/tab and navigate to url.",
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "navigate_page",
                "Navigate/reload/back/forward in the selected page.",
                {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["url", "reload", "back", "forward"], "default": "url"},
                        "url": {"type": "string"},
                        "ignore_cache": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "select_frame",
                "List frames/iframes or select frame_idx for subsequent tools.",
                {
                    "type": "object",
                    "properties": {
                        "frame_idx": {"type": "integer"},
                        "page_size": {"type": "integer", "default": 20},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "click_element",
                "Click a CSS selector in the selected frame.",
                {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "index": {"type": "integer", "default": 0},
                        "timeout_ms": {"type": "integer", "default": 5000},
                    },
                    "required": ["selector"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "take_screenshot",
                "Capture a PNG screenshot of the selected page into a local file.",
                {
                    "type": "object",
                    "properties": {
                        "full_page": {"type": "boolean", "default": False},
                        "file_path": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            # ---- console / network / websocket ----
            _tool(
                "list_console_messages",
                "List captured console messages or fetch one by msgid.",
                {
                    "type": "object",
                    "properties": {
                        "msgid": {"type": "integer"},
                        "type": {"type": "string"},
                        "page_size": {"type": "integer", "default": 20},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "list_network_requests",
                "List/filter HTTP requests, inspect one reqid, or trace Set-Cookie by cookie_name.",
                {
                    "type": "object",
                    "properties": {
                        "reqid": {"type": "integer"},
                        "cookie_name": {"type": "string"},
                        "resource_type": {"type": "string"},
                        "page_size": {"type": "integer", "default": 30},
                        "include_body": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "clear_network_requests",
                "Clear captured HTTP request evidence. Requires confirm=true.",
                {
                    "type": "object",
                    "properties": {"confirm": {"type": "boolean"}},
                    "required": ["confirm"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "get_request_initiator",
                "Return initiator metadata for a captured request reqid.",
                {
                    "type": "object",
                    "properties": {"reqid": {"type": "integer"}},
                    "required": ["reqid"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "get_websocket_messages",
                "List WebSocket connections and recent frames.",
                {
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "integer"},
                        "page_size": {"type": "integer", "default": 50},
                    },
                    "additionalProperties": False,
                },
            ),
            # ---- scripts ----
            _tool(
                "list_scripts",
                "List JavaScript scripts discovered via Debugger.scriptParsed.",
                {
                    "type": "object",
                    "properties": {"page_size": {"type": "integer", "default": 50}},
                    "additionalProperties": False,
                },
            ),
            _tool(
                "get_script_source",
                "Read a script source region by script_id or url.",
                {
                    "type": "object",
                    "properties": {
                        "script_id": {"type": "string"},
                        "url": {"type": "string"},
                        "start_line": {"type": "integer", "default": 1},
                        "end_line": {"type": "integer"},
                        "max_chars": {"type": "integer", "default": 20000},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "save_script_source",
                "Save full script source to a local file.",
                {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "script_id": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["file_path"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "search_in_sources",
                "Search loaded script sources for text or regex.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "case_sensitive": {"type": "boolean", "default": False},
                        "is_regex": {"type": "boolean", "default": False},
                        "max_matches": {"type": "integer", "default": 50},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            # ---- debugger ----
            _tool(
                "set_breakpoint_on_text",
                "Find text in sources and set a code breakpoint on the Nth occurrence.",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "url_filter": {"type": "string"},
                        "occurrence": {"type": "integer", "default": 1},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "break_on_xhr",
                "Set an XHR/Fetch breakpoint by URL substring/pattern.",
                {
                    "type": "object",
                    "properties": {"url_pattern": {"type": "string"}},
                    "required": ["url_pattern"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "remove_breakpoint",
                "Remove code/XHR breakpoints. action=remove_code|remove_xhr|remove_all.",
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["remove_code", "remove_xhr", "remove_all"]},
                        "breakpoint_id": {"type": "string"},
                        "url_pattern": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ),
            _tool("list_breakpoints", "List active code and XHR breakpoints.", empty),
            _tool(
                "get_paused_info",
                "Inspect current debugger pause call stack and scopes.",
                {
                    "type": "object",
                    "properties": {"frame_index": {"type": "integer", "default": 0}},
                    "additionalProperties": False,
                },
            ),
            _tool(
                "pause_or_resume",
                "Pause or resume JavaScript execution.",
                {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["pause", "resume"]}},
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "step",
                "Step over/into/out while paused.",
                {
                    "type": "object",
                    "properties": {"type": {"type": "string", "enum": ["over", "into", "out"], "default": "over"}},
                    "additionalProperties": False,
                },
            ),
            # ---- evaluate / site data ----
            _tool(
                "evaluate_script",
                "Evaluate a JS function source in page/frame or on a paused call frame.",
                {
                    "type": "object",
                    "properties": {
                        "function": {
                            "type": "string",
                            "description": "JS function source, e.g. () => document.title",
                        },
                        "frame_index": {"type": "integer"},
                        "await_promise": {"type": "boolean", "default": True},
                    },
                    "required": ["function"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "clear_site_data",
                "Clear cookies/storage for the selected page origin. Requires confirm=true.",
                {
                    "type": "object",
                    "properties": {
                        "confirm": {"type": "boolean"},
                        "include_http_cache": {"type": "boolean", "default": False},
                    },
                    "required": ["confirm"],
                    "additionalProperties": False,
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        args = arguments or {}
        try:
            result = await _dispatch(service, reverse, name, args)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return _json_content(result)

    return server


async def _dispatch(
    service: AuthBridgeClient,
    session: ReverseSession,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    # auth
    if name == "auth_list_sites":
        return await service.list_sites()
    if name == "auth_sync_to_cloak":
        return await service.sync(args["site_id"], args["target_profile"], args.get("mode", "merge"))
    if name == "auth_verify_cloak":
        return await service.verify(args["site_id"], args["target_profile"])
    if name == "auth_clear_cloak":
        return await service.clear(args["site_id"], args["target_profile"], args["confirm"])

    # cloak lifecycle
    if name == "cloak_debug_open":
        profile_id = args.get("profile_id", "shared-main")
        return await session.open_profile(profile_id, args.get("url") or [])
    if name == "cloak_debug_tab":
        return await session.new_tab(args["url"])
    if name == "cloak_debug_list":
        return await session.list_pages()
    if name == "cloak_debug_status":
        return session.status()
    if name == "cloak_debug_close":
        if args.get("confirm") is not True:
            raise ValueError("cloak_debug_close requires confirm=true")
        return await session.close_session(args["profile_id"])

    # reverse attach
    if name == "reverse_attach":
        return await session.ensure_attached(args.get("cdp_http"))
    if name == "reverse_detach":
        return await session.detach()
    if name == "reverse_status":
        return session.status()

    # reverse tools
    if name == "select_page":
        return await session.select_page(args.get("page_idx"), int(args.get("page_size", 20)))
    if name == "new_page":
        return await session.new_page(args["url"])
    if name == "navigate_page":
        return await session.navigate_page(
            type=args.get("type", "url"),
            url=args.get("url"),
            ignore_cache=bool(args.get("ignore_cache", False)),
        )
    if name == "select_frame":
        return await session.select_frame(args.get("frame_idx"), int(args.get("page_size", 20)))
    if name == "click_element":
        return await session.click_element(
            args["selector"],
            int(args.get("index", 0)),
            int(args.get("timeout_ms", 5000)),
        )
    if name == "take_screenshot":
        return await session.take_screenshot(bool(args.get("full_page", False)), args.get("file_path"))
    if name == "list_console_messages":
        return await session.list_console_messages(args.get("msgid"), args.get("type"), int(args.get("page_size", 20)))
    if name == "list_network_requests":
        return await session.list_network_requests(
            args.get("reqid"),
            args.get("cookie_name"),
            args.get("resource_type"),
            int(args.get("page_size", 30)),
            bool(args.get("include_body", False)),
        )
    if name == "clear_network_requests":
        return await session.clear_network_requests(bool(args.get("confirm", False)))
    if name == "get_request_initiator":
        return await session.get_request_initiator(int(args["reqid"]))
    if name == "get_websocket_messages":
        return await session.get_websocket_messages(args.get("connection_id"), int(args.get("page_size", 50)))
    if name == "list_scripts":
        return await session.list_scripts(int(args.get("page_size", 50)))
    if name == "get_script_source":
        return await session.get_script_source(
            args.get("script_id"),
            args.get("url"),
            int(args.get("start_line", 1)),
            args.get("end_line"),
            int(args.get("max_chars", 20000)),
        )
    if name == "save_script_source":
        return await session.save_script_source(args["file_path"], args.get("script_id"), args.get("url"))
    if name == "search_in_sources":
        return await session.search_in_sources(
            args["query"],
            bool(args.get("case_sensitive", False)),
            bool(args.get("is_regex", False)),
            int(args.get("max_matches", 50)),
        )
    if name == "set_breakpoint_on_text":
        return await session.set_breakpoint_on_text(
            args["text"],
            args.get("url_filter"),
            int(args.get("occurrence", 1)),
        )
    if name == "break_on_xhr":
        return await session.break_on_xhr(args["url_pattern"])
    if name == "remove_breakpoint":
        return await session.remove_breakpoint(
            args["action"],
            args.get("breakpoint_id"),
            args.get("url_pattern"),
            bool(args.get("confirm", False)),
        )
    if name == "list_breakpoints":
        return await session.list_breakpoints()
    if name == "get_paused_info":
        return await session.get_paused_info(int(args.get("frame_index", 0)))
    if name == "pause_or_resume":
        return await session.pause_or_resume(args["action"])
    if name == "step":
        return await session.step(args.get("type", "over"))
    if name == "evaluate_script":
        return await session.evaluate_script(
            args["function"],
            args.get("frame_index"),
            bool(args.get("await_promise", True)),
        )
    if name == "clear_site_data":
        return await session.clear_site_data(
            bool(args.get("confirm", False)),
            bool(args.get("include_http_cache", False)),
        )

    raise ValueError(f"unknown tool: {name}")


async def run_stdio(server: Server) -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="cloak-browser-auth",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
