from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cloak_browser_auth import config
from cloak_browser_auth.config import Registry

DEFAULT_CONTROL_PORT = 19333
# Back-compat alias used by older CLI flags / docs.
DEFAULT_DEBUG_PORT = DEFAULT_CONTROL_PORT


def _session_file() -> Path:
    return config.AUTH_DIR / "debug-session.json"


def _stop_file() -> Path:
    return config.AUTH_DIR / "debug-session.stop"


@dataclass(frozen=True)
class DebugSession:
    profile_id: str
    profile_path: str
    port: int
    pid: int
    control_http: str
    started_at: str
    browser: str | None = None
    # Legacy field retained for older callers; always empty in the new model.
    cdp_http: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebugSession:
        port = int(data.get("port") or data.get("control_port") or DEFAULT_CONTROL_PORT)
        control_http = str(data.get("control_http") or f"http://127.0.0.1:{port}")
        return cls(
            profile_id=str(data["profile_id"]),
            profile_path=str(data["profile_path"]),
            port=port,
            pid=int(data["pid"]),
            control_http=control_http,
            started_at=str(data["started_at"]),
            browser=data.get("browser"),
            cdp_http=str(data.get("cdp_http") or ""),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_path": self.profile_path,
            "port": self.port,
            "pid": self.pid,
            "control_http": self.control_http,
            "control": "python-cloakbrowser-holder",
            "started_at": self.started_at,
            "browser": self.browser,
            # Explicitly omit usable CDP endpoint so agents do not re-attach.
            "cdp_http": None,
        }


def safe_url(url: str) -> str:
    """Drop query/fragment so tokens in URLs never hit stdout/logs."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except OSError:
            return False
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _write_session(session: DebugSession) -> None:
    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _session_file().write_text(
        json.dumps(session.to_public_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clear_session_files() -> None:
    for path in (_session_file(), _stop_file()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load_session() -> DebugSession | None:
    if not _session_file().exists():
        return None
    try:
        data = json.loads(_session_file().read_text(encoding="utf-8"))
        session = DebugSession.from_dict(data)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not _pid_running(session.pid) or not _port_open(session.port):
        return None
    return session


def _control_request(session: DebugSession, payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        session.control_http.rstrip("/") + "/command",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"holder control HTTP {error.code}: {detail}") from error
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"holder control request failed: {error}") from error
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("invalid holder control response")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "holder command failed"))
    return data


def _wait_for_control(port: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if _port_open(port):
            # Probe status once the port accepts TCP.
            try:
                probe = DebugSession(
                    profile_id="probe",
                    profile_path="",
                    port=port,
                    pid=os.getpid(),
                    control_http=f"http://127.0.0.1:{port}",
                    started_at=datetime.now(UTC).isoformat(),
                )
                _control_request(probe, {"op": "ping"}, timeout=2.0)
                return
            except Exception as error:
                last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"control port {port} did not become ready: {last_error}")


async def _open_urls(context: Any, urls: list[str]) -> list[str]:
    opened: list[str] = []
    for url in urls:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        opened.append(safe_url(page.url))
    return opened


class _HolderState:
    def __init__(self, loop: asyncio.AbstractEventLoop, context: Any, profile_id: str, profile_path: str) -> None:
        self.loop = loop
        self.context = context
        self.profile_id = profile_id
        self.profile_path = profile_path
        self.stop_requested = False

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op") or "")
        if op == "ping":
            return {"ok": True, "action": "ping"}
        if op == "status":
            pages = []
            for idx, page in enumerate(list(self.context.pages)):
                try:
                    url = safe_url(page.url)
                    title = await page.title()
                except Exception:
                    url, title = "about:blank", ""
                pages.append({"idx": idx, "url": url, "title": title})
            return {
                "ok": True,
                "action": "status",
                "active": True,
                "mode": "owned-holder",
                "control": "python-cloakbrowser-holder",
                "profile_id": self.profile_id,
                "profile_path": self.profile_path,
                "pages": pages,
                "cdp_http": None,
            }
        if op == "list":
            status = await self.handle({"op": "status"})
            return {
                "ok": True,
                "action": "list",
                "active": True,
                "mode": "owned-holder",
                "tabs": status.get("pages") or [],
                "profile_id": self.profile_id,
            }
        if op == "tab":
            url = str(payload.get("url") or "")
            if not url.startswith("https://"):
                raise ValueError(f"only https URLs are allowed: {safe_url(url)}")
            page = await self.context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            return {
                "ok": True,
                "action": "tab",
                "mode": "owned-holder",
                "profile_id": self.profile_id,
                "opened": safe_url(page.url),
                "pages": len(self.context.pages),
            }
        if op == "evaluate":
            expression = str(payload.get("expression") or "")
            if not expression:
                raise ValueError("expression is required")
            pages = list(self.context.pages)
            if not pages:
                raise RuntimeError("no pages open")
            idx = int(payload.get("page_idx") or 0)
            if idx < 0 or idx >= len(pages):
                raise ValueError(f"page_idx out of range: {idx}")
            page = pages[idx]
            value = await page.evaluate(expression)  # type: ignore[attr-defined]
            return {"ok": True, "action": "evaluate", "page_idx": idx, "value": value}
        if op == "close":
            self.stop_requested = True
            return {"ok": True, "action": "close", "closing": True}
        raise ValueError(f"unknown op: {op}")


def _start_control_server(state: _HolderState, port: int) -> ThreadingHTTPServer:
    holder = state

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/") in {"", "/health", "/status"}:
                future = asyncio.run_coroutine_threadsafe(holder.handle({"op": "status"}), holder.loop)
                try:
                    self._send(200, future.result(timeout=30))
                except Exception as error:
                    self._send(500, {"ok": False, "error": f"{type(error).__name__}: {error}"})
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/command":
                self._send(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("payload must be object")
                future = asyncio.run_coroutine_threadsafe(holder.handle(payload), holder.loop)
                result = future.result(timeout=120)
                self._send(200, result)
            except Exception as error:
                self._send(500, {"ok": False, "error": f"{type(error).__name__}: {error}"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="cloak-holder-control", daemon=True)
    thread.start()
    return server


async def run_holder(profile_id: str, port: int, urls: list[str]) -> int:
    """Long-lived process that owns one CloakBrowser session and a local control plane."""
    from cloakbrowser import launch_persistent_context_async  # type: ignore[import-untyped]

    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _stop_file().unlink(missing_ok=True)

    registry = Registry.load()
    if profile_id not in registry.profiles:
        raise SystemExit(f"unknown profile: {profile_id}")
    profile = registry.profiles[profile_id]
    profile_path = registry.resolve_profile_path(profile)
    profile_path.mkdir(parents=True, exist_ok=True)

    if _port_open(port):
        raise SystemExit(f"control port already in use: {port}")

    # Launch via Python cloakbrowser API. No remote-debugging-port: callers control
    # this process over the local HTTP control plane instead of CDP re-attach.
    context = await launch_persistent_context_async(
        str(profile_path),
        headless=profile.headless,
    )
    server: ThreadingHTTPServer | None = None
    try:
        opened = await _open_urls(context, urls)
        loop = asyncio.get_running_loop()
        state = _HolderState(loop, context, profile_id, str(profile_path))
        server = _start_control_server(state, port)
        session = DebugSession(
            profile_id=profile_id,
            profile_path=str(profile_path),
            port=port,
            pid=os.getpid(),
            control_http=f"http://127.0.0.1:{port}",
            started_at=datetime.now(UTC).isoformat(),
            browser="cloakbrowser",
            cdp_http="",
        )
        _write_session(session)
        print(
            json.dumps(
                {
                    "ok": True,
                    "event": "holder_ready",
                    "mode": "owned-holder",
                    "control": "python-cloakbrowser-holder",
                    "session": session.to_public_dict(),
                    "opened": opened,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        while not _stop_file().exists() and not state.stop_requested:
            await asyncio.sleep(0.5)
            if not _session_file().exists():
                break
    finally:
        if server is not None:
            try:
                server.shutdown()
            except Exception as _shutdown_error:
                _ = _shutdown_error
        try:
            await context.close()
        finally:
            _clear_session_files()
            print(json.dumps({"ok": True, "event": "holder_closed"}, ensure_ascii=False), flush=True)
    return 0


def _control_hint(port: int) -> dict[str, object]:
    return {
        "control_http": f"http://127.0.0.1:{port}",
        "control": "python-cloakbrowser-holder",
        "preferred": (
            "Use cloak_debug_open MCP tool (in-process) when possible. "
            "CLI debug-* talks to the holder control HTTP API — do not CDP re-attach."
        ),
        "commands": {
            "status": "debug-status",
            "tab": "debug-tab <https-url>",
            "list": "debug-list",
            "close": "debug-close",
        },
        "note": "cdp_http is intentionally null. Python owns the browser context.",
    }


def open_session(profile_id: str, urls: list[str] | None = None, port: int = DEFAULT_CONTROL_PORT) -> dict[str, Any]:
    existing = load_session()
    if existing is not None:
        raise RuntimeError(
            f"debug session already running: profile={existing.profile_id} pid={existing.pid} port={existing.port}"
        )
    if _port_open(port):
        raise RuntimeError(f"control port already in use: {port}")

    urls = urls or []
    for url in urls:
        if not url.startswith("https://"):
            raise ValueError(f"only https URLs are allowed: {safe_url(url)}")

    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _stop_file().unlink(missing_ok=True)
    _session_file().unlink(missing_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "cloak_browser_auth",
        "debug-hold",
        "--profile",
        profile_id,
        "--port",
        str(port),
    ]
    for url in urls:
        cmd.extend(["--url", url])

    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    log_path = config.AUTH_DIR / "debug-session.log"
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log_handle.close()

    try:
        _wait_for_control(port, timeout=90.0)
    except Exception:
        if _pid_running(proc.pid):
            _terminate_pid(proc.pid)
        _clear_session_files()
        raise

    deadline = time.time() + 10
    session = load_session()
    while session is None and time.time() < deadline:
        time.sleep(0.1)
        session = load_session()
    if session is None:
        session = DebugSession(
            profile_id=profile_id,
            profile_path=str(Registry.load().resolve_profile_path(Registry.load().profiles[profile_id])),
            port=port,
            pid=proc.pid,
            control_http=f"http://127.0.0.1:{port}",
            started_at=datetime.now(UTC).isoformat(),
            browser="cloakbrowser",
            cdp_http="",
        )
        _write_session(session)

    return {
        "ok": True,
        "action": "open",
        "mode": "owned-holder",
        "control": "python-cloakbrowser-holder",
        "session": session.to_public_dict(),
        "holder_pid": proc.pid,
        "hint": _control_hint(session.port),
    }


async def new_tab(url: str) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"only https URLs are allowed: {safe_url(url)}")
    session = load_session()
    if session is None:
        raise RuntimeError("no active debug session; run debug-open first")
    return await asyncio.to_thread(_control_request, session, {"op": "tab", "url": url})


def list_tabs() -> dict[str, Any]:
    session = load_session()
    if session is None:
        return {"ok": True, "action": "list", "active": False, "tabs": []}
    return _control_request(session, {"op": "list"})


def status() -> dict[str, Any]:
    session = load_session()
    if session is None:
        stale = _session_file().exists()
        if stale:
            _clear_session_files()
        return {"ok": True, "action": "status", "active": False, "cleaned_stale": stale}
    try:
        live = _control_request(session, {"op": "status"}, timeout=10.0)
    except Exception as error:
        return {
            "ok": True,
            "action": "status",
            "active": _pid_running(session.pid) and _port_open(session.port),
            "session": session.to_public_dict(),
            "error": f"{type(error).__name__}: {error}",
            "hint": _control_hint(session.port),
        }
    live["session"] = session.to_public_dict()
    live["port_open"] = _port_open(session.port)
    live["hint"] = _control_hint(session.port)
    return live


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def close_session() -> dict[str, Any]:
    session = load_session()
    if session is None:
        _clear_session_files()
        return {"ok": True, "action": "close", "active": False}

    # Prefer graceful close through the holder control plane.
    try:
        _control_request(session, {"op": "close"}, timeout=10.0)
    except Exception as _close_error:
        _ = _close_error

    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _stop_file().write_text("stop\n", encoding="utf-8")

    deadline = time.time() + 15
    while time.time() < deadline:
        if not _pid_running(session.pid) and not _port_open(session.port):
            break
        time.sleep(0.2)
    else:
        _terminate_pid(session.pid)
        time.sleep(0.5)

    _clear_session_files()
    return {
        "ok": True,
        "action": "close",
        "closed_profile": session.profile_id,
        "closed_pid": session.pid,
        "port": session.port,
        "control": "python-cloakbrowser-holder",
    }


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
