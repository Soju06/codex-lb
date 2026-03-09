from __future__ import annotations

from urllib.parse import urlparse


def normalize_http_proxy_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("http_proxy_url must be a valid http or https URL")

    return normalized
