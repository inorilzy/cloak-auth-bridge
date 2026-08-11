from pathlib import Path

from cloak_auth_bridge import mcp_server
from cloak_auth_bridge.reverse_session import ReverseSession, _safe_url

REQUIRED_REVERSE_TOOLS = {
    "reverse_attach",
    "reverse_detach",
    "reverse_status",
    "select_page",
    "new_page",
    "navigate_page",
    "select_frame",
    "click_element",
    "take_screenshot",
    "list_console_messages",
    "list_network_requests",
    "clear_network_requests",
    "get_request_initiator",
    "get_websocket_messages",
    "list_scripts",
    "get_script_source",
    "save_script_source",
    "search_in_sources",
    "set_breakpoint_on_text",
    "break_on_xhr",
    "remove_breakpoint",
    "list_breakpoints",
    "get_paused_info",
    "pause_or_resume",
    "step",
    "evaluate_script",
    "clear_site_data",
}


def test_safe_url_strips_query() -> None:
    assert _safe_url("https://www.xiaohongshu.com/explore?x=1#y") == "https://www.xiaohongshu.com/explore"


def test_mcp_source_registers_js_reverse_equivalent_tools() -> None:
    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    for name in REQUIRED_REVERSE_TOOLS:
        assert f'"{name}"' in source
    assert ReverseSession is not None
    assert SESSION_STATUS_DEFAULT_INACTIVE()


def SESSION_STATUS_DEFAULT_INACTIVE() -> bool:
    from cloak_auth_bridge.reverse_session import SESSION

    status = SESSION.status()
    return status["ok"] is True and status["active"] is False
