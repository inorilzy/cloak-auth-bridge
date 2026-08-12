from __future__ import annotations

import secrets
import subprocess
from pathlib import Path
from typing import Any

from cloak_browser_auth import config

DESCRIPTION = "Cloak Browser Auth pairing token"


def _token_file() -> Path:
    return config.AUTH_DIR / "pairing-token.dpapi"


def _win32crypt() -> Any:
    try:
        import win32crypt  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError("pywin32 is required for Windows DPAPI token storage") from error
    return win32crypt


def load_or_create_token(path: Path | None = None) -> str:
    win32crypt = _win32crypt()
    token_path = path or _token_file()
    if token_path.exists():
        _description, raw = win32crypt.CryptUnprotectData(token_path.read_bytes(), None, None, None, 0)
        return raw.decode("utf-8")

    token = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = win32crypt.CryptProtectData(token.encode(), DESCRIPTION, None, None, None, 0)
    token_path.write_bytes(encrypted)
    return token


def copy_token_to_clipboard(token: str) -> None:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "$input | Set-Clipboard",
    ]
    subprocess.run(command, input=token, text=True, check=True, capture_output=True)
