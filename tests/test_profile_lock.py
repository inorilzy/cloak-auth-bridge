from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cloak_browser_auth.profile_lock import profile_lock


def _try_lock(profile_path: Path) -> subprocess.CompletedProcess[str]:
    code = """
import sys
from cloak_browser_auth.profile_lock import ProfileBusyError, profile_lock

try:
    with profile_lock(sys.argv[1]):
        pass
except ProfileBusyError:
    raise SystemExit(23)
"""
    return subprocess.run(
        [sys.executable, "-c", code, str(profile_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_profile_lock_is_cross_process_nonblocking_and_released(tmp_path: Path) -> None:
    profile_path = tmp_path / "bilibili-main"

    with profile_lock(profile_path):
        busy = _try_lock(profile_path)
        assert busy.returncode == 23, busy.stderr
        assert not profile_path.exists()

    released = _try_lock(profile_path)
    assert released.returncode == 0, released.stderr
