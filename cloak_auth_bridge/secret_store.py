from __future__ import annotations

import secrets
import subprocess
from pathlib import Path
from typing import Any

from cloak_auth_bridge.config import AUTH_DIR

TOKEN_FILE = AUTH_DIR / "pairing-token.dpapi"
DESCRIPTION = "Cloak Auth Bridge pairing token"


def _win32crypt() -> Any:
    try:
        import win32crypt  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError("pywin32 is required for Windows DPAPI token storage") from error
    return win32crypt


def load_or_create_token(path: Path = TOKEN_FILE) -> str:
    win32crypt = _win32crypt()
    if path.exists():
        _description, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)
        return raw.decode("utf-8")

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = win32crypt.CryptProtectData(token.encode(), DESCRIPTION, None, None, None, 0)
    path.write_bytes(encrypted)
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
