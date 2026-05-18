from __future__ import annotations

import ctypes
import importlib
import logging
import os
import sys
from ctypes import wintypes
from types import ModuleType


def _load_resource_module() -> ModuleType | None:
    try:
        return importlib.import_module("resource")
    except ImportError:
        return None


_resource = _load_resource_module()

logger = logging.getLogger(__name__)

_memory_warning_threshold_bytes: int = 0
_memory_reject_threshold_bytes: int = 0
_rss_provider_warning_logged = False


def configure(warning_threshold_mb: int = 0, reject_threshold_mb: int = 0) -> None:
    global _memory_warning_threshold_bytes, _memory_reject_threshold_bytes
    _memory_warning_threshold_bytes = warning_threshold_mb * 1024 * 1024
    _memory_reject_threshold_bytes = reject_threshold_mb * 1024 * 1024


def _get_windows_rss_bytes() -> int | None:
    if sys.platform != "win32":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except (AttributeError, OSError):
        return None

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    process = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    if ok == 0:
        return None
    return int(counters.WorkingSetSize)


def _get_resource_rss_bytes() -> int | None:
    if _resource is None:
        return None
    usage = _resource.getrusage(_resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return int(usage.ru_maxrss)
    return int(usage.ru_maxrss * 1024)


def _log_rss_provider_unavailable() -> None:
    global _rss_provider_warning_logged
    if _rss_provider_warning_logged:
        return
    _rss_provider_warning_logged = True
    logger.warning("Memory RSS provider unavailable on platform %s; disabling memory-pressure checks", sys.platform)


def get_rss_bytes() -> int:
    try:
        psutil = __import__("psutil")
        return int(psutil.Process().memory_info().rss)
    except ImportError:
        pass

    if sys.platform == "linux":
        try:
            with open("/proc/self/statm", "rb") as f:
                pages = int(f.read().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass

    rss = _get_windows_rss_bytes()
    if rss is not None:
        return rss

    rss = _get_resource_rss_bytes()
    if rss is not None:
        return rss

    _log_rss_provider_unavailable()
    return 0


def is_memory_pressure() -> bool:
    if _memory_reject_threshold_bytes <= 0:
        return False
    return get_rss_bytes() >= _memory_reject_threshold_bytes


def is_memory_warning() -> bool:
    if _memory_warning_threshold_bytes <= 0:
        return False
    return get_rss_bytes() >= _memory_warning_threshold_bytes


__all__ = ["configure", "get_rss_bytes", "is_memory_pressure", "is_memory_warning"]
