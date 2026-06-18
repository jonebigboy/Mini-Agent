"""File locking using fcntl.flock.

POSIX flock is auto-released by the kernel when the holding process exits
(via SIGKILL, crash, or normal exit). This is the source of truth for
"session is currently active" — no mtime heuristics needed.
"""
import fcntl
import os
from pathlib import Path


class SessionLockedError(Exception):
    """Raised when a lock is held by another live process."""


class FileLock:
    """Exclusive flock wrapper. Not reentrant across instances in same process."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, blocking: bool = True) -> None:
        """Acquire exclusive lock. Raises SessionLockedError if blocking=False and held."""
        if self._fd is not None:
            return  # already held by this instance
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            os.close(fd)
            raise SessionLockedError(f"Lock held by another process: {self.path}") from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def probe(self) -> bool:
        """Non-blocking acquire + immediate release. Returns True if free."""
        try:
            self.acquire(blocking=False)
            self.release()
            return True
        except SessionLockedError:
            return False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
