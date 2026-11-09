"""Platform-specific filesystem behavior for one local QEcho instance."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


def protect_path(path: str | Path, mode: int) -> None:
    """Restrict POSIX modes; Windows access remains governed by inherited ACLs."""
    if os.name != "nt":
        Path(path).chmod(mode)


class InstanceLock:
    """Hold an OS lock until release or process exit, without deleting its file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        handle = self.path.open("a+b", buffering=0)
        try:
            # Windows locks a byte range from the current offset. Keep byte zero
            # present and always seek to it, even when reusing an existing file.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise RuntimeError(
                    "This QEcho data directory is already in use; stop the existing instance first"
                ) from exc
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
