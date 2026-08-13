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
    status = ReverseSession().status()
    assert status["ok"] is True and status["active"] is False


@pytest.mark.asyncio
async def test_mcp_close_requires_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm=true"):
        await mcp_server._dispatch(object(), ReverseSession(), "cloak_debug_close", {"profile_id": "shared-main"})


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
async def test_open_profile_connects_existing_holder_without_spawning(monkeypatch) -> None:
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
    spawned: list[object] = []
    monkeypatch.setattr(
        "cloak_browser_auth.reverse_session.debug_session.open_session",
        lambda *_args, **_kwargs: spawned.append("spawned") or {},
    )
    monkeypatch.setattr("cloak_browser_auth.reverse_session.debug_session.load_session", lambda: holder)
    session = ReverseSession()

    async def fake_connect(value: DebugSession) -> None:
        assert value is holder
        session._browser = object()
        session._context = FakeContext()
        session._profile_id = value.profile_id
        session._profile_path = value.profile_path

    monkeypatch.setattr(session, "_connect_holder_unlocked", fake_connect)

    result = await session.open_profile("shared-main")

    assert spawned == []
    assert result["mode"] == "attached-holder"
    assert result["profile_id"] == "shared-main"
    assert result["opened"] == []


@pytest.mark.asyncio
async def test_open_profile_does_not_spawn_when_no_holder(monkeypatch) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(
        "cloak_browser_auth.reverse_session.debug_session.open_session",
        lambda *_args, **_kwargs: spawned.append("spawned") or {},
    )
    monkeypatch.setattr("cloak_browser_auth.reverse_session.debug_session.load_session", lambda: None)

    with pytest.raises(RuntimeError, match="cloak_debug_open"):
        await ReverseSession().open_profile("shared-main")
    assert spawned == []


@pytest.mark.asyncio
async def test_debug_tab_uses_reverse_session_not_holder_http() -> None:
    session = ReverseSession()
    calls: list[str] = []

    async def fake_new_tab(url: str) -> dict[str, object]:
        calls.append(url)
        return {"ok": True, "url": url}

    session.new_tab = fake_new_tab  # type: ignore[method-assign]

    result = await mcp_server._dispatch(
        object(),
        session,
        "cloak_debug_tab",
        {"url": "https://x.com/home"},
    )

    assert result == {"ok": True, "url": "https://x.com/home"}
    assert calls == ["https://x.com/home"]


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
