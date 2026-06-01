from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime

import aiohttp

from app import __version__
from app.core.utils.time import utcnow
from app.modules.runtime.schemas import RuntimeVersionResponse

logger = logging.getLogger(__name__)

_GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/Soju06/codex-lb/releases/latest"
_LATEST_RELEASE_PAGE_URL = "https://github.com/Soju06/codex-lb/releases/latest"
_VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


@dataclass(frozen=True, slots=True)
class _RuntimeVersionSnapshot:
    current_version: str
    latest_version: str | None
    update_available: bool
    checked_at: datetime
    source: str | None


class RuntimeVersionService:
    def __init__(
        self,
        *,
        current_version: str = __version__,
        ttl_seconds: float = 6 * 60 * 60,
        failure_ttl_seconds: float = 15 * 60,
        github_token_env_var: str = "GITHUB_TOKEN",
    ) -> None:
        self._current_version = current_version
        self._ttl_seconds = ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._github_token_env_var = github_token_env_var
        self._cached_snapshot: _RuntimeVersionSnapshot | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def get_version_status(self) -> RuntimeVersionResponse:
        now = time.monotonic()
        if self._cached_snapshot is not None and now - self._cached_at < self._cache_ttl_for(self._cached_snapshot):
            return _to_response(self._cached_snapshot)

        async with self._lock:
            now = time.monotonic()
            if self._cached_snapshot is not None and now - self._cached_at < self._cache_ttl_for(self._cached_snapshot):
                return _to_response(self._cached_snapshot)

            snapshot = await self._fetch_snapshot()
            self._cached_snapshot = snapshot
            self._cached_at = time.monotonic()
            return _to_response(snapshot)

    async def invalidate(self) -> None:
        async with self._lock:
            self._cached_snapshot = None
            self._cached_at = 0.0

    async def _fetch_snapshot(self) -> _RuntimeVersionSnapshot:
        checked_at = utcnow()
        try:
            latest_version = await self._fetch_latest_release_version()
        except Exception:
            logger.warning("Failed to fetch latest codex-lb release from GitHub", exc_info=True)
            return _RuntimeVersionSnapshot(
                current_version=self._current_version,
                latest_version=None,
                update_available=False,
                checked_at=checked_at,
                source=None,
            )

        update_available = _is_newer_version(latest_version, self._current_version)
        return _RuntimeVersionSnapshot(
            current_version=self._current_version,
            latest_version=latest_version,
            update_available=update_available,
            checked_at=checked_at,
            source="github",
        )

    async def _fetch_latest_release_version(self) -> str:
        timeout = aiohttp.ClientTimeout(total=10, sock_connect=5, sock_read=5)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"codex-lb/{self._current_version}",
        }
        github_token = os.getenv(self._github_token_env_var, "").strip()
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(_GITHUB_LATEST_RELEASE_URL, headers=headers) as response:
                if response.status != 200:
                    raise RuntimeError(f"GitHub releases API returned HTTP {response.status}")
                data = await response.json()

        raw_version = data.get("tag_name") if isinstance(data, dict) else None
        if not isinstance(raw_version, str):
            raise RuntimeError(f"GitHub release tag_name is not a string: {raw_version!r}")
        version = _normalize_version(raw_version)
        if version is None:
            raise RuntimeError(f"GitHub release tag_name is not a stable semver: {raw_version!r}")
        return version

    def _cache_ttl_for(self, snapshot: _RuntimeVersionSnapshot) -> float:
        if snapshot.source is None:
            return self._failure_ttl_seconds
        return self._ttl_seconds


def _to_response(snapshot: _RuntimeVersionSnapshot) -> RuntimeVersionResponse:
    return RuntimeVersionResponse(
        current_version=snapshot.current_version,
        latest_version=snapshot.latest_version,
        update_available=snapshot.update_available,
        checked_at=snapshot.checked_at,
        source=snapshot.source,
        release_url=_LATEST_RELEASE_PAGE_URL,
    )


def _normalize_version(value: str) -> str | None:
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    core = ".".join(match.group(part) for part in ("major", "minor", "patch"))
    prerelease = match.group("prerelease")
    return f"{core}-{prerelease}" if prerelease else core


@dataclass(frozen=True, slots=True)
class _ParsedVersion:
    major: int
    minor: int
    patch: int
    prerelease: str | None


def _parse_version(value: str) -> _ParsedVersion | None:
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return _ParsedVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=match.group("prerelease"),
    )


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_version = _parse_version(candidate)
    current_version = _parse_version(current)
    if candidate_version is None or current_version is None:
        return False
    candidate_core = (candidate_version.major, candidate_version.minor, candidate_version.patch)
    current_core = (current_version.major, current_version.minor, current_version.patch)
    if candidate_core != current_core:
        return candidate_core > current_core
    if candidate_version.prerelease is None and current_version.prerelease is not None:
        return True
    if candidate_version.prerelease is not None and current_version.prerelease is None:
        return False
    if candidate_version.prerelease is None and current_version.prerelease is None:
        return False
    return _compare_prerelease(candidate_version.prerelease, current_version.prerelease) > 0


def _compare_prerelease(candidate: str | None, current: str | None) -> int:
    if candidate is None and current is None:
        return 0
    if candidate is None:
        return 1
    if current is None:
        return -1

    candidate_parts = candidate.split(".")
    current_parts = current.split(".")
    for candidate_part, current_part in zip(candidate_parts, current_parts, strict=False):
        if candidate_part == current_part:
            continue
        candidate_numeric = candidate_part.isdigit()
        current_numeric = current_part.isdigit()
        if candidate_numeric and current_numeric:
            candidate_number = int(candidate_part)
            current_number = int(current_part)
            if candidate_number != current_number:
                return 1 if candidate_number > current_number else -1
            continue
        if candidate_numeric:
            return -1
        if current_numeric:
            return 1
        return 1 if candidate_part > current_part else -1
    if len(candidate_parts) == len(current_parts):
        return 0
    return 1 if len(candidate_parts) > len(current_parts) else -1


_runtime_version_service = RuntimeVersionService()


def get_runtime_version_service() -> RuntimeVersionService:
    return _runtime_version_service
