from __future__ import annotations


def runtime_basic_auth_url(user: str, value: str, authority: str) -> str:
    """Build a credential-bearing proxy URL at runtime for redaction tests."""
    return "http://" + user + ":" + value + "@" + authority
