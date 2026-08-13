from __future__ import annotations

import pytest

from cloak_browser_auth.cloak_processes import (
    classify_cloak_process,
    is_recoverable_launch_error,
    launch_persistent_with_reap,
)

MARKERS = [r"C:\Users\me\.cloakbrowser\chromium-150.0.7871.114.6-pro"]
PROFILES = [r"C:\work\cookies2agent\profiles"]


def test_classifies_cloak_binary_main_process() -> None:
    cmd = r'"C:\Users\me\.cloakbrowser\chromium-150.0.7871.114.6-pro\chrome.exe" --user-data-dir="C:\work\cookies2agent\profiles\xiaohongshu-main"'
    assert classify_cloak_process(cmd, binary_markers=MARKERS, profile_dirs=PROFILES) == "cloak-binary"


def test_ignores_google_chrome() -> None:
    cmd = r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --type=utility https://www.xiaohongshu.com'
    assert classify_cloak_process(cmd, binary_markers=MARKERS, profile_dirs=PROFILES) is None


def test_ignores_cloak_child_processes() -> None:
    cmd = r'"C:\Users\me\.cloakbrowser\chromium-150.0.7871.114.6-pro\chrome.exe" --type=renderer --user-data-dir="C:\work\cookies2agent\profiles\xiaohongshu-main"'
    assert classify_cloak_process(cmd, binary_markers=MARKERS, profile_dirs=PROFILES) is None


def test_classifies_legacy_debug_hold() -> None:
    cmd = r"C:\work\.venv\Scripts\python.exe -m cloak_browser_auth debug-hold --profile bilibili-main"
    assert classify_cloak_process(cmd, binary_markers=MARKERS, profile_dirs=PROFILES) == "legacy-holder"


def test_ignores_mcp_and_serve() -> None:
    mcp = r"C:\work\.venv\Scripts\python.exe -m cloak_browser_auth mcp"
    serve = r"C:\work\.venv\Scripts\python.exe -m cloak_browser_auth serve"
    assert classify_cloak_process(mcp, binary_markers=MARKERS, profile_dirs=PROFILES) is None
    assert classify_cloak_process(serve, binary_markers=MARKERS, profile_dirs=PROFILES) is None


def test_session_limit_is_recoverable() -> None:
    class CloakBrowserLicenseError(RuntimeError):
        pass

    error = CloakBrowserLicenseError(
        "CloakBrowser Pro: session limit reached for your plan. Close another running session or upgrade your plan."
    )
    assert is_recoverable_launch_error(error) is True
    assert is_recoverable_launch_error(ValueError("bad cookie domain")) is False

@pytest.mark.asyncio
async def test_launch_reaps_and_retries_on_session_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"launch": 0, "reap": 0}

    class CloakBrowserLicenseError(RuntimeError):
        pass

    async def fake_launch(path: str, headless: bool) -> str:
        del path, headless
        calls["launch"] += 1
        if calls["launch"] == 1:
            raise CloakBrowserLicenseError("session limit reached for your plan")
        return "context"

    monkeypatch.setattr(
        "cloak_browser_auth.cloak_processes.reap_stale_cloak_processes",
        lambda: calls.__setitem__("reap", calls["reap"] + 1),
    )

    import cloak_browser_auth.cloak_processes as module

    class FakeCloak:
        launch_persistent_context_async = staticmethod(fake_launch)

    monkeypatch.setitem(__import__("sys").modules, "cloakbrowser", FakeCloak)
    monkeypatch.setattr(module.asyncio, "sleep", _instant_sleep)

    result = await launch_persistent_with_reap("profiles/x", headless=True)
    assert result == "context"
    assert calls == {"launch": 2, "reap": 1}


async def _instant_sleep(_seconds: float) -> None:
    return None
