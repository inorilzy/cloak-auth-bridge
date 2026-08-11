from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cloak_auth_bridge import config
from cloak_auth_bridge.config import Registry

DEFAULT_DEBUG_PORT = 9333


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
    cdp_http: str
    started_at: str
    browser: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DebugSession:
        return cls(
            profile_id=str(data["profile_id"]),
            profile_path=str(data["profile_path"]),
            port=int(data["port"]),
            pid=int(data["pid"]),
            cdp_http=str(data["cdp_http"]),
            started_at=str(data["started_at"]),
            browser=data.get("browser"),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_path": self.profile_path,
            "port": self.port,
            "pid": self.pid,
            "cdp_http": self.cdp_http,
            "started_at": self.started_at,
            "browser": self.browser,
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


def _read_cdp_version(port: int) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("invalid CDP version payload")
    return payload


def _read_cdp_targets(port: int) -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise TypeError("invalid CDP target list")
    return payload


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


def _wait_for_cdp(port: int, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if _port_open(port):
            try:
                return _read_cdp_version(port)
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as error:
                last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"CDP port {port} did not become ready: {last_error}")


async def _open_urls(context: Any, urls: list[str]) -> list[str]:
    opened: list[str] = []
    for url in urls:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        opened.append(safe_url(page.url))
    return opened


async def run_holder(profile_id: str, port: int, urls: list[str]) -> int:
    """Long-lived process that owns the single CloakBrowser session."""
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
        raise SystemExit(f"debug port already in use: {port}")

    context = await launch_persistent_context_async(
        str(profile_path),
        headless=profile.headless,
        args=[
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
        ],
    )
    try:
        meta = _wait_for_cdp(port)
        session = DebugSession(
            profile_id=profile_id,
            profile_path=str(profile_path),
            port=port,
            pid=os.getpid(),
            cdp_http=f"http://127.0.0.1:{port}",
            started_at=datetime.now(UTC).isoformat(),
            browser=str(meta.get("Browser")) if meta.get("Browser") is not None else None,
        )
        _write_session(session)
        opened = await _open_urls(context, urls)
        print(
            json.dumps(
                {
                    "ok": True,
                    "event": "holder_ready",
                    "session": session.to_public_dict(),
                    "opened": opened,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        while not _stop_file().exists():
            await asyncio.sleep(0.5)
            if not _session_file().exists():
                break
    finally:
        try:
            await context.close()
        finally:
            _clear_session_files()
            print(json.dumps({"ok": True, "event": "holder_closed"}, ensure_ascii=False), flush=True)
    return 0


def _js_reverse_attach_hint(port: int) -> dict[str, object]:
    """How to attach js-reverse-mcp to this live Cloak session.

    js-reverse-mcp supports --browserUrl for an existing CDP endpoint and
    conflicts with --cloak. That is intentional: cloak-auth-bridge already
    launched CloakBrowser; js-reverse only attaches for DevTools debugging.
    """
    cdp = f"http://127.0.0.1:{port}"
    return {
        "cdp_http": cdp,
        "codex_mcp_name": "js-reverse-cloak-auth",
        "command": "npx",
        "args": ["-y", "js-reverse-mcp@latest", "--browserUrl", cdp],
        "workflow": [
            "1. cloak_debug_open (this server) — starts Cloak on the auth profile + CDP",
            "2. Use js-reverse-cloak-auth tools (navigate/network/breakpoint/...) against that CDP",
            "3. cloak_debug_close when finished — releases the Free-plan browser seat",
        ],
        "note": (
            "Do not pass --cloak to js-reverse when attaching: --cloak launches a second "
            "browser and conflicts with --browserUrl. Auth sync owns the Cloak process."
        ),
    }



def open_session(profile_id: str, urls: list[str] | None = None, port: int = DEFAULT_DEBUG_PORT) -> dict[str, Any]:
    existing = load_session()
    if existing is not None:
        raise RuntimeError(
            f"debug session already running: profile={existing.profile_id} pid={existing.pid} port={existing.port}"
        )
    if _port_open(port):
        raise RuntimeError(f"debug port already in use: {port}")

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
        "cloak_auth_bridge",
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
        meta = _wait_for_cdp(port, timeout=60.0)
    except Exception:
        if _pid_running(proc.pid):
            _terminate_pid(proc.pid)
        _clear_session_files()
        raise

    # Holder writes the canonical session file; if it lags, synthesize one.
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
            cdp_http=f"http://127.0.0.1:{port}",
            started_at=datetime.now(UTC).isoformat(),
            browser=str(meta.get("Browser")) if meta.get("Browser") is not None else None,
        )
        _write_session(session)

    return {
        "ok": True,
        "action": "open",
        "session": session.to_public_dict(),
        "holder_pid": proc.pid,
        "js_reverse": _js_reverse_attach_hint(session.port),
    }


async def new_tab(url: str) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"only https URLs are allowed: {safe_url(url)}")
    session = load_session()
    if session is None:
        raise RuntimeError("no active debug session; run debug-open first")

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(session.cdp_http)
        try:
            if not browser.contexts:
                raise RuntimeError("attached browser has no contexts")
            context = browser.contexts[0]
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            final = safe_url(page.url)
            pages = len(context.pages)
        finally:
            await browser.close()

    return {
        "ok": True,
        "action": "tab",
        "profile_id": session.profile_id,
        "opened": final,
        "pages": pages,
    }


def list_tabs() -> dict[str, Any]:
    session = load_session()
    if session is None:
        return {"ok": True, "action": "list", "active": False, "tabs": []}

    targets = _read_cdp_targets(session.port)
    tabs = []
    for target in targets:
        if target.get("type") != "page":
            continue
        tabs.append(
            {
                "id": target.get("id"),
                "title": target.get("title"),
                "url": safe_url(str(target.get("url") or "")),
            }
        )
    return {
        "ok": True,
        "action": "list",
        "active": True,
        "session": session.to_public_dict(),
        "tabs": tabs,
    }


def status() -> dict[str, Any]:
    session = load_session()
    if session is None:
        stale = _session_file().exists()
        if stale:
            _clear_session_files()
        return {"ok": True, "action": "status", "active": False, "cleaned_stale": stale}
    try:
        meta = _read_cdp_version(session.port)
        browser = meta.get("Browser")
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError):
        browser = session.browser
    return {
        "ok": True,
        "action": "status",
        "active": True,
        "session": session.to_public_dict(),
        "browser": browser,
        "port_open": _port_open(session.port),
        "js_reverse": _js_reverse_attach_hint(session.port),
    }


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
    }


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
