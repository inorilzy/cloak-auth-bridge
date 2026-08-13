import asyncio

import pytest

from cloak_browser_auth import debug_session
from cloak_browser_auth.debug_session import DebugSession, _HolderState, safe_url


def test_safe_url_strips_query_and_fragment() -> None:
    assert (
        safe_url("https://x.com/home?state=secret#token")
        == "https://x.com/home"
    )


def test_safe_url_keeps_origin_and_path() -> None:
    assert safe_url("https://www.bilibili.com/video/BV1") == "https://www.bilibili.com/video/BV1"


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[object] = []


def test_session_registry_keeps_endpoint_private() -> None:
    session = DebugSession(
        profile_id="shared-main",
        profile_path="profile",
        port=19333,
        pid=123,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-12T00:00:00+00:00",
        instance_id="secret-instance",
        endpoint="pipe://secret-endpoint",
    )

    assert session.to_dict()["endpoint"] == "pipe://secret-endpoint"
    assert "endpoint" not in session.to_public_dict()
    assert "instance_id" not in session.to_public_dict()


def test_holder_only_stops_after_explicit_close() -> None:
    state = _HolderState(asyncio.new_event_loop(), FakeContext(), "shared-main", "profile", "test")

    assert state.stop_requested is False

    result = asyncio.run(state.handle({"op": "close", "instance_id": "test", "confirm": True}))

    assert result == {"ok": True, "action": "close", "closing": True}
    assert state.stop_requested is True


def test_holder_close_requires_confirmation() -> None:
    state = _HolderState(asyncio.new_event_loop(), FakeContext(), "shared-main", "profile", "test")

    with pytest.raises(ValueError, match="confirm=true"):
        asyncio.run(state.handle({"op": "close", "instance_id": "test"}))

    assert state.stop_requested is False


def test_holder_lifetime_does_not_require_session_file() -> None:
    state = _HolderState(asyncio.new_event_loop(), FakeContext(), "shared-main", "profile", "test")

    assert state.stop_requested is False
    assert state.browser_closed.is_set() is False


def test_manual_browser_close_requests_holder_stop() -> None:
    state = _HolderState(asyncio.new_event_loop(), FakeContext(), "shared-main", "profile", "test")

    state.browser_closed.set()

    assert state.browser_closed.is_set() is True


def test_load_session_rejects_a_holder_with_the_wrong_identity(monkeypatch) -> None:
    session = DebugSession(
        profile_id="shared-main",
        profile_path="profile",
        port=19333,
        pid=123,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-12T00:00:00+00:00",
        instance_id="expected",
        endpoint="pipe://cloak/shared-main",
    )
    monkeypatch.setattr(debug_session, "_read_session", lambda: session)
    monkeypatch.setattr(debug_session, "_port_open", lambda _port: True)
    monkeypatch.setattr(
        debug_session,
        "_control_request",
        lambda _session, _payload, timeout=60.0: {
            "ok": True,
            "instance_id": "other",
            "pid": session.pid,
            "profile_id": session.profile_id,
        },
    )

    assert debug_session.load_session() is None


def test_close_refuses_to_kill_an_unverified_holder(monkeypatch) -> None:
    session = DebugSession(
        profile_id="shared-main",
        profile_path="profile",
        port=19333,
        pid=123,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-12T00:00:00+00:00",
        instance_id="expected",
        endpoint="pipe://cloak/shared-main",
    )
    monkeypatch.setattr(debug_session, "_read_session", lambda: session)
    monkeypatch.setattr(debug_session, "_control_request", lambda *_args, **_kwargs: {"instance_id": "other"})
    killed: list[int] = []
    monkeypatch.setattr(debug_session, "_terminate_pid", killed.append)

    with pytest.raises(RuntimeError, match="identity"):
        debug_session.close_session()

    assert killed == []


def test_close_refuses_a_different_profile(monkeypatch) -> None:
    session = DebugSession(
        profile_id="shared-main",
        profile_path="profile",
        port=19333,
        pid=123,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-12T00:00:00+00:00",
        instance_id="expected",
        endpoint="pipe://cloak/shared-main",
    )
    monkeypatch.setattr(debug_session, "_read_session", lambda: session)
    monkeypatch.setattr(debug_session, "_control_request", lambda *_args, **_kwargs: pytest.fail("must not call holder"))

    with pytest.raises(RuntimeError, match="different profile"):
        debug_session.close_session("bilibili-main")


def test_status_does_not_delete_an_unverified_registry(monkeypatch) -> None:
    monkeypatch.setattr(debug_session, "load_session", lambda: None)
    monkeypatch.setattr(debug_session, "_session_file", lambda: type("PathStub", (), {"exists": lambda self: True})())
    cleared: list[bool] = []
    monkeypatch.setattr(debug_session, "_clear_session_files", lambda *_args: cleared.append(True))

    assert debug_session.status()["stale_registry"] is True
    assert cleared == []


def test_published_holder_pid_may_differ_from_venv_launcher_pid(monkeypatch) -> None:
    session = DebugSession(
        profile_id="bilibili-main",
        profile_path="profile",
        port=19333,
        pid=222,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-13T00:00:00+00:00",
        instance_id="expected",
        endpoint="pipe://cloak/bilibili-main",
    )
    monkeypatch.setattr(debug_session, "_read_session", lambda: session)

    published = debug_session._wait_for_published_session(
        "bilibili-main", "expected", 19333, timeout=0.1
    )

    assert published.pid == 222


@pytest.mark.parametrize(
    ("profile_id", "instance_id", "port"),
    [
        ("other", "expected", 19333),
        ("bilibili-main", "other", 19333),
        ("bilibili-main", "expected", 19334),
    ],
)
def test_published_holder_must_match_requested_identity(
    monkeypatch, profile_id: str, instance_id: str, port: int
) -> None:
    session = DebugSession(
        profile_id="bilibili-main",
        profile_path="profile",
        port=19333,
        pid=222,
        control_http="http://127.0.0.1:19333",
        started_at="2026-08-13T00:00:00+00:00",
        instance_id="expected",
        endpoint="ws://127.0.0.1:23456/secret",
    )
    monkeypatch.setattr(debug_session, "_read_session", lambda: session)

    with pytest.raises(RuntimeError, match="publishing a verified session"):
        debug_session._wait_for_published_session(profile_id, instance_id, port, timeout=0)
