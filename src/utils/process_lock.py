from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) is implemented through Windows process signalling.
        # Query a read-only handle instead so checking a stale lock can never
        # deliver a control event or termination signal to the owner.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ProcessLock:
    """Non-blocking, stale-aware process lock backed by an atomic lock file."""

    def __init__(self, name: str, *, lock_dir: str | Path = ".runtime/locks") -> None:
        self.path = Path(lock_dir) / f"{name}.lock"
        self.token = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    raw = self.path.read_text(encoding="ascii").strip()
                    owner_pid = int(raw.split(":", 1)[0])
                except (OSError, ValueError):
                    owner_pid = 0
                if _pid_is_running(owner_pid):
                    return False
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    return False
                continue
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(self.token)
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            if self.path.read_text(encoding="ascii").strip() == self.token:
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.acquired = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
