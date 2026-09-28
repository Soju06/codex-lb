from __future__ import annotations


def runtime_basic_auth_url(user: str, value: str, authority: str) -> str:
    """Build a credential-bearing proxy URL at runtime for redaction tests."""
    return "http://" + user + ":" + value + "@" + authority


class WindowsOSError(OSError):
    """OSError carrying a typed ``winerror``, as CPython raises on Windows."""

    winerror: int

    def __init__(self, winerror: int, message: str = "Windows socket failure") -> None:
        super().__init__(message)
        self.winerror = winerror
