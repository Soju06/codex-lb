from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DashboardRateLimitError
from app.db.models import RateLimitAttempt


class DatabaseRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, type: str) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.type = type

    def _window_start(self) -> datetime:
        return datetime.now(UTC) - timedelta(seconds=self.window_seconds)

    async def check(self, key: str, session: AsyncSession) -> None:
        """Read-only rate-limit check. Raises if limit is exceeded; does NOT record an attempt."""
        window_start = self._window_start()

        count = await session.scalar(
            select(func.count())
            .select_from(RateLimitAttempt)
            .where(
                RateLimitAttempt.key == key,
                RateLimitAttempt.type == self.type,
                RateLimitAttempt.attempted_at >= window_start,
            )
        )

        if (count or 0) >= self.max_attempts:
            oldest_attempt = await session.scalar(
                select(RateLimitAttempt.attempted_at)
                .where(
                    RateLimitAttempt.key == key,
                    RateLimitAttempt.type == self.type,
                    RateLimitAttempt.attempted_at >= window_start,
                )
                .order_by(RateLimitAttempt.attempted_at.asc())
                .limit(1)
            )
            now = datetime.now(UTC)
            retry_after = self.window_seconds
            if oldest_attempt is not None:
                if oldest_attempt.tzinfo is None:
                    oldest_attempt = oldest_attempt.replace(tzinfo=UTC)
                reset_at = oldest_attempt + timedelta(seconds=self.window_seconds)
                retry_after = max(1, int((reset_at - now).total_seconds()))
            raise DashboardRateLimitError("Too many attempts", retry_after=retry_after)

    async def record_failure(self, key: str, session: AsyncSession) -> None:
        """Record a single failed attempt without performing a rate-limit check."""
        session.add(RateLimitAttempt(key=key, type=self.type, attempted_at=datetime.now(UTC)))
        await session.flush()
        await session.commit()

    async def clear_for_key(self, key: str, session: AsyncSession) -> None:
        """Delete all attempts for (key, type) — used to reset counter on successful auth."""
        await session.execute(
            delete(RateLimitAttempt).where(
                RateLimitAttempt.key == key,
                RateLimitAttempt.type == self.type,
            )
        )
        await session.commit()

    async def check_and_record(self, key: str, session: AsyncSession) -> None:
        """Legacy method: check + unconditionally record. Kept for TOTP backward compatibility."""
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=self.window_seconds)

        count = await session.scalar(
            select(func.count())
            .select_from(RateLimitAttempt)
            .where(
                RateLimitAttempt.key == key,
                RateLimitAttempt.type == self.type,
                RateLimitAttempt.attempted_at >= window_start,
            )
        )

        oldest_attempt = await session.scalar(
            select(RateLimitAttempt.attempted_at)
            .where(
                RateLimitAttempt.key == key,
                RateLimitAttempt.type == self.type,
                RateLimitAttempt.attempted_at >= window_start,
            )
            .order_by(RateLimitAttempt.attempted_at.asc())
            .limit(1)
        )

        session.add(RateLimitAttempt(key=key, type=self.type, attempted_at=now))
        await session.flush()
        await session.commit()

        if (count or 0) >= self.max_attempts:
            retry_after = self.window_seconds
            if oldest_attempt is not None:
                if oldest_attempt.tzinfo is None:
                    oldest_attempt = oldest_attempt.replace(tzinfo=UTC)
                reset_at = oldest_attempt + timedelta(seconds=self.window_seconds)
                retry_after = max(1, int((reset_at - now).total_seconds()))
            raise DashboardRateLimitError("Too many attempts", retry_after=retry_after)

    async def cleanup(self, session: AsyncSession, older_than_seconds: int = 3600) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        await session.execute(delete(RateLimitAttempt).where(RateLimitAttempt.attempted_at < cutoff))
        await session.commit()
