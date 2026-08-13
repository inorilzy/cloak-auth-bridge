from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CloakProcess:
    pid: int
    name: str
    command_line: str
    reason: str


def cloak_binary_markers() -> list[str]:
    markers = [str((Path.home() / ".cloakbrowser" / "chromium-").resolve())]
    try:
        from cloakbrowser.config import get_cache_dir  # type: ignore[import-untyped]

        markers.append(str((get_cache_dir() / "chromium-").resolve()))
    except (ImportError, OSError):
        pass
    return list(dict.fromkeys(markers))


def profile_roots() -> list[str]:
    from cloak_browser_auth import config

    config.refresh_paths()
    return [str(config.PROFILES_DIR.resolve())]


def is_google_chrome(command_line: str) -> bool:
    lowered = command_line.lower().replace("/", "\\")
    return "google\\chrome\\application\\chrome.exe" in lowered


def is_browser_child(command_line: str) -> bool:
    return "--type=" in command_line


def classify_cloak_process(
    command_line: str,
    *,
    binary_markers: list[str] | None = None,
    profile_dirs: list[str] | None = None,
) -> str | None:
    """Return a kill reason, or None if this process must be left alone."""
    if not command_line.strip():
        return None
    if is_google_chrome(command_line):
        return None
    if is_browser_child(command_line):
        return None

    normalized = command_line.replace("/", "\\").lower()
    markers = [marker.replace("/", "\\").lower() for marker in (binary_markers or cloak_binary_markers())]
    if any(marker.lower().replace("/", "\\") in normalized for marker in markers):
        return "cloak-binary"
    if ".cloakbrowser" in normalized and "chromium-" in normalized:
        return "cloak-binary"

    for root in profile_dirs or profile_roots():
        if root.replace("/", "\\").lower() in normalized and "user-data-dir" in normalized:
            return "profile-dir"

    if "cloak_browser_auth" in normalized and "debug-hold" in normalized:
        return "legacy-holder"
    return None


def list_cloak_processes() -> list[CloakProcess]:
    markers = cloak_binary_markers()
    roots = profile_roots()
    found: list[CloakProcess] = []
    for pid, name, command_line in _iter_processes():
        reason = classify_cloak_process(command_line, binary_markers=markers, profile_dirs=roots)
        if reason is None:
            continue
        found.append(CloakProcess(pid=pid, name=name, command_line=command_line, reason=reason))
    return found


def reap_stale_cloak_processes() -> dict[str, Any]:
    """Kill leftover CloakBrowser roots occupying the license seat. Never touches Google Chrome."""
    processes = list_cloak_processes()
    killed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for process in processes:
        try:
            _kill_tree(process.pid)
            killed.append({"pid": process.pid, "name": process.name, "reason": process.reason})
        except OSError as error:
            errors.append({"pid": process.pid, "error": str(error)})
    return {
        "ok": True,
        "action": "reap",
        "found": len(processes),
        "killed": killed,
        "errors": errors,
    }


def is_recoverable_launch_error(error: BaseException) -> bool:
    name = type(error).__name__
    text = str(error).lower()
    return name in {"CloakBrowserLicenseError", "ProfileBusyError"} or "session limit" in text or "already in use" in text


async def launch_persistent_with_reap(profile_path: str | Path, *, headless: bool) -> Any:
    """Launch Cloak; if the license seat is stale, reap leftovers and retry once."""
    try:
        from cloakbrowser import launch_persistent_context_async  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError("cloakbrowser is not installed") from error

    try:
        return await launch_persistent_context_async(str(profile_path), headless=headless)
    except Exception as error:
        if not is_recoverable_launch_error(error):
            raise
        reap_stale_cloak_processes()
        await asyncio.sleep(1.5)
        return await launch_persistent_context_async(str(profile_path), headless=headless)


def _iter_processes() -> list[tuple[int, str, str]]:
    if sys.platform == "win32":
        return _iter_windows_processes()
    return _iter_posix_processes()


def _iter_windows_processes() -> list[tuple[int, str, str]]:
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[tuple[int, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        processes.append((pid, str(row.get("Name") or ""), str(row.get("CommandLine") or "")))
    return processes


def _iter_posix_processes() -> list[tuple[int, str, str]]:
    result = subprocess.run(["ps", "-ax", "-o", "pid=,comm=,args="], capture_output=True, text=True, check=False)
    processes: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            processes.append((int(parts[0]), parts[1], parts[2]))
        except ValueError:
            continue
    return processes


def _kill_tree(pid: int) -> None:
    if pid <= 0:
        raise OSError("invalid pid")
    if sys.platform == "win32":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode not in {0, 128}:
            raise OSError(completed.stderr.strip() or completed.stdout.strip() or f"taskkill {pid} failed")
        return
    os.kill(pid, signal.SIGTERM)
