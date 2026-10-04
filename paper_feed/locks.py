"""Cross-process lock for mutating Paper Feed jobs (refresh / reanalyze / summarize / fetch abstracts).

The CLI and the local server can run at the same time; both write SQLite and
the compatibility exports.  ``job_lock(kind)`` takes a lock file next to the
database (``data/.paper_feed.lock``, or the directory of ``PAPER_FEED_DB``)
created with ``O_CREAT | O_EXCL``.  It holds ``{pid, kind, started_at, host}``.

* Re-entrant per process: a flow that calls another flow in the same thread
  nests without deadlocking; another thread of the same process gets
  ``LockBusyError`` instead of blocking.
* A lock whose pid is no longer alive (same host), whose content is
  unreadable for a while, or which is older than ``STALE_AFTER_SECONDS`` is
  reclaimed.
"""
import contextlib
import datetime
import json
import os
import socket
import threading
import time
from pathlib import Path

LOCK_FILENAME = ".paper_feed.lock"
STALE_AFTER_SECONDS = 6 * 3600
# A lock file that cannot be parsed may be mid-write; reclaim it only after this.
UNREADABLE_GRACE_SECONDS = 60
PROJECT_DIR = Path(__file__).resolve().parent.parent

_THREAD_LOCK = threading.RLock()
_STATE = {"depth": 0, "path": None, "kind": None}


class LockBusyError(RuntimeError):
    """Another Paper Feed task holds the job lock.  ``holder`` is its lock info (may be {})."""

    def __init__(self, holder=None):
        self.holder = dict(holder or {})
        super().__init__(busy_message(self.holder))


def busy_message(holder):
    holder = holder or {}
    kind = holder.get("kind") or "unknown"
    pid = holder.get("pid") or "?"
    return (f"另一个任务正在运行（{kind}, pid {pid}）/ "
            f"Another Paper Feed task is running ({kind}, pid {pid}). 请稍后再试 / Try again later.")


def lock_path(database=None):
    """Lock file next to the database (``PAPER_FEED_DB`` dir, default ``data/``)."""
    database = database or os.environ.get("PAPER_FEED_DB") or None
    directory = Path(database).resolve().parent if database else PROJECT_DIR / "data"
    return directory / LOCK_FILENAME


def pid_alive(pid):
    """True when process *pid* exists on this host (unknown -> True, to stay safe)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid):
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return True
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied: the process exists but belongs to someone else.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_holder(path):
    """Parsed lock info, or None when the file is missing / unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None


def _age_seconds(path, holder):
    started = (holder or {}).get("started_at")
    if started:
        try:
            return (datetime.datetime.now() - datetime.datetime.fromisoformat(started)).total_seconds()
        except (TypeError, ValueError):
            pass
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return 0


def is_stale(path, holder):
    """True when the lock at *path* (with info *holder*) may be reclaimed."""
    if holder is None:
        return _age_seconds(path, None) > UNREADABLE_GRACE_SECONDS
    if _age_seconds(path, holder) > STALE_AFTER_SECONDS:
        return True
    same_host = (holder.get("host") or "") == socket.gethostname()
    if not same_host:
        return False
    if holder.get("pid") == os.getpid():
        # This process does not hold it (depth 0): a leftover of ours.
        return True
    return not pid_alive(holder.get("pid"))


def _create(path, kind):
    info = {"pid": os.getpid(), "kind": kind, "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "host": socket.gethostname()}
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    return info


def _acquire_file(path, kind):
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            return _create(path, kind)
        except FileExistsError:
            holder = read_holder(path)
            if not is_stale(path, holder):
                raise LockBusyError(holder or {})
            print(f"Reclaiming stale Paper Feed lock {path} ({holder}). 回收过期的任务锁。")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                raise LockBusyError(holder or {})
    raise LockBusyError(read_holder(path) or {})


def _release_file(path):
    holder = read_holder(path)
    if holder is not None and holder.get("pid") != os.getpid():
        return  # reclaimed by someone else; not ours to delete
    try:
        os.unlink(path)
    except OSError:
        pass


@contextlib.contextmanager
def job_lock(kind, database=None):
    """Hold the cross-process job lock for *kind*; raises ``LockBusyError`` when busy."""
    if not _THREAD_LOCK.acquire(blocking=False):
        raise LockBusyError({"kind": _STATE.get("kind"), "pid": os.getpid(), "host": socket.gethostname()})
    try:
        if _STATE["depth"] == 0:
            path = lock_path(database)
            _acquire_file(path, kind)
            _STATE.update(path=path, kind=kind)
        _STATE["depth"] += 1
        try:
            yield
        finally:
            _STATE["depth"] -= 1
            if _STATE["depth"] == 0:
                path = _STATE["path"]
                _STATE.update(path=None, kind=None)
                _release_file(path)
    finally:
        _THREAD_LOCK.release()
