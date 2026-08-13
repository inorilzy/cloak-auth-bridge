from pathlib import Path

import pytest

from cloak_browser_auth import mcp_server
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


class FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        del wait_until
        self.url = url

    async def title(self) -> str:
        return ""


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.closed = False
        self.browser = FakeBrowser()

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

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
async def test_detach_closes_owned_browser() -> None:
    session = ReverseSession()
    context = FakeContext()
    session._browser = context.browser
    session._context = context
    session._owned = True
    session._profile_id = "shared-main"

    result = await session.detach()

    assert result["closed_owned_browser"] is True
    assert context.closed is True
    assert session.active is False


def test_disconnected_owned_browser_is_not_active() -> None:
    session = ReverseSession()
    session._browser = FakeBrowser(connected=False)
    session._context = FakeContext()
    session._owned = True

    assert session.active is False


@pytest.mark.asyncio
async def test_open_profile_launches_owned_context(monkeypatch: pytest.MonkeyPatch) -> None:
    session = ReverseSession()
    context = FakeContext()
    launched: list[str] = []

    async def fake_launch(profile_id: str, headless: bool | None) -> None:
        del headless
        launched.append(profile_id)
        session._context = context
        session._browser = context.browser
        session._profile_id = profile_id
        session._profile_path = "profiles/shared-main"
        session._owned = True

    async def fake_domains(page: object) -> None:
        del page

    monkeypatch.setattr(session, "_launch_owned_unlocked", fake_launch)
    monkeypatch.setattr(session, "_ensure_page_domains", fake_domains)

    result = await session.open_profile("shared-main", ["https://www.bilibili.com/"])

    assert launched == ["shared-main"]
    assert result["mode"] == "owned"
    assert result["reused"] is False
    assert result["opened"] == ["https://www.bilibili.com/"]
    assert result["profile_id"] == "shared-main"


@pytest.mark.asyncio
async def test_open_profile_reuses_owned_context(monkeypatch: pytest.MonkeyPatch) -> None:
    session = ReverseSession()
    context = FakeContext()
    session._context = context
    session._browser = context.browser
    session._profile_id = "shared-main"
    session._profile_path = "profiles/shared-main"
    session._owned = True
    launched: list[str] = []

    async def fake_launch(profile_id: str, headless: bool | None) -> None:
        del headless
        launched.append(profile_id)

    async def fake_domains(page: object) -> None:
        del page

    monkeypatch.setattr(session, "_launch_owned_unlocked", fake_launch)
    monkeypatch.setattr(session, "_ensure_page_domains", fake_domains)

    result = await session.open_profile("shared-main", ["https://x.com/home"])

    assert launched == []
    assert result["reused"] is True
    assert result["opened"] == ["https://x.com/home"]


@pytest.mark.asyncio
async def test_debug_tab_uses_reverse_session() -> None:
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
async def test_close_owned_browser_for_matching_profile() -> None:
    session = ReverseSession()
    context = FakeContext()
    session._browser = context.browser
    session._context = context
    session._owned = True
    session._profile_id = "shared-main"

    result = await session.close_session("shared-main")

    assert result["closed_owned_browser"] is True
    assert result["closed_profile"] == "shared-main"
    assert context.closed is True


@pytest.mark.asyncio
async def test_close_refuses_a_different_profile() -> None:
    session = ReverseSession()
    session._owned = True
    session._profile_id = "shared-main"
    session._context = FakeContext()
    session._browser = FakeBrowser()

    with pytest.raises(RuntimeError, match="different profile"):
        await session.close_session("bilibili-main")
