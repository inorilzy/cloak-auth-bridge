from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class ProfileBusyError(RuntimeError):
    """Raised when another process owns a CloakBrowser profile."""


def _acquire(handle: BinaryIO) -> None:
    try:
        if sys.platform == "win32":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno not in {errno.EACCES, errno.EAGAIN}:
            raise
        raise ProfileBusyError("CloakBrowser profile is already in use") from error


def _release(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def profile_lock(path: str | Path) -> Iterator[None]:
    profile_path = Path(path).resolve()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = profile_path.parent / f".{profile_path.name}.lock"

    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        _acquire(handle)
        try:
            yield
        finally:
            _release(handle)
