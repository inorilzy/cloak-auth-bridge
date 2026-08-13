from pathlib import Path

import pytest

from cloak_browser_auth import mcp_server
from cloak_browser_auth.debug_session import DebugSession
from cloak_browser_auth.reverse_session import ReverseSession, _safe_url

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


@pytest.mark.asyncio
async def test_mcp_close_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm=true"):
        await mcp_server._dispatch(object(), "cloak_debug_close", {"profile_id": "shared-main"})


def SESSION_STATUS_DEFAULT_INACTIVE() -> bool:
    from cloak_browser_auth.reverse_session import SESSION

    status = SESSION.status()
    return status["ok"] is True and status["active"] is False


def test_status_reports_mode_field() -> None:
    session = ReverseSession()
    status = session.status()
    assert status["ok"] is True
    assert status["active"] is False
    assert status["mode"] is None
    assert "profile_id" in status


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[object] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected

    def is_connected(self) -> bool:
        return self.connected


@pytest.mark.asyncio
async def test_detach_disconnects_client_without_closing_holder_context() -> None:
    session = ReverseSession()
    context = FakeContext()
    playwright = FakePlaywright()
    session._browser = object()
    session._context = context
    session._playwright = playwright

    result = await session.detach()

    assert result == {"ok": True, "action": "detach", "closed_owned_browser": False}
    assert context.closed is False
    assert playwright.stopped is True


def test_disconnected_holder_client_is_not_active() -> None:
    session = ReverseSession()
    session._browser = FakeBrowser(connected=False)
    session._context = FakeContext()

    assert session.active is False


@pytest.mark.asyncio
async def test_open_reuses_holder_and_connects_without_closing_it(monkeypatch) -> None:
    holder = DebugSession(
        profile_id="shared-main",
        profile_path="profile",
        port=19333,
        pid=123,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-12T00:00:00+00:00",
        instance_id="test",
        endpoint="pipe://holder",
    )
    opened: list[str] = []
    monkeypatch.setattr(
        "cloak_browser_auth.reverse_session.debug_session.open_session",
        lambda profile_id, urls: opened.extend(urls) or {"session": holder.to_public_dict()},
    )
    monkeypatch.setattr("cloak_browser_auth.reverse_session.debug_session.load_session", lambda: holder)
    session = ReverseSession()

    async def fake_connect(value: DebugSession) -> None:
        assert value is holder
        session._browser = object()
        session._context = FakeContext()
        session._profile_id = value.profile_id

    monkeypatch.setattr(session, "_connect_holder_unlocked", fake_connect)

    result = await session.open_profile("shared-main", ["https://www.bilibili.com/"])

    assert opened == ["https://www.bilibili.com/"]
    assert result["mode"] == "attached-holder"
    assert result["profile_id"] == "shared-main"


@pytest.mark.asyncio
async def test_close_uses_the_instance_connected_by_this_client(monkeypatch) -> None:
    session = ReverseSession()
    session._profile_id = "shared-main"
    session._holder_instance_id = "connected-instance"
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "cloak_browser_auth.reverse_session.debug_session.close_session",
        lambda profile_id, instance_id: calls.append((profile_id, instance_id)) or {"ok": True},
    )

    assert await session.close_session("shared-main") == {"ok": True}
    assert calls == [("shared-main", "connected-instance")]
