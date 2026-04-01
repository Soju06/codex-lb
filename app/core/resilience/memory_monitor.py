from __future__ import annotations

import logging
import resource
import sys

logger = logging.getLogger(__name__)

_memory_warning_threshold_bytes: int = 0
_memory_reject_threshold_bytes: int = 0


def configure(warning_threshold_mb: int = 0, reject_threshold_mb: int = 0) -> None:
    global _memory_warning_threshold_bytes, _memory_reject_threshold_bytes
    _memory_warning_threshold_bytes = warning_threshold_mb * 1024 * 1024
    _memory_reject_threshold_bytes = reject_threshold_mb * 1024 * 1024


def get_rss_bytes() -> int:
    try:
        psutil = __import__("psutil")
        return int(psutil.Process().memory_info().rss)
    except ImportError:
        if sys.platform == "linux":
            try:
                with open("/proc/self/statm", "rb") as f:
                    pages = int(f.read().split()[1])
                return pages * resource.getpagesize()
            except (OSError, ValueError, IndexError):
                pass
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return int(usage.ru_maxrss)
        return int(usage.ru_maxrss * 1024)


def is_memory_pressure() -> bool:
    if _memory_reject_threshold_bytes <= 0:
        return False
    return get_rss_bytes() >= _memory_reject_threshold_bytes


def is_memory_warning() -> bool:
    if _memory_warning_threshold_bytes <= 0:
        return False
    return get_rss_bytes() >= _memory_warning_threshold_bytes


__all__ = ["configure", "get_rss_bytes", "is_memory_pressure", "is_memory_warning"]
