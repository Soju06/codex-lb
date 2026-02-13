from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.auth.refresh import RefreshError
from app.core.clients.model_fetcher import ModelFetchError, fetch_models_for_plan
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import UpstreamModel, get_model_registry
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, _safe_close, _safe_rollback
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelRefreshScheduler:
    interval_seconds: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        session = SessionLocal()
        try:
            accounts_repo = AccountsRepository(session)
            accounts = await accounts_repo.list_accounts()
            grouped = _group_by_plan(accounts)
            if not grouped:
                logger.debug("No active accounts for model registry refresh")
                return

            encryptor = TokenEncryptor()
            per_plan_results: dict[str, list[UpstreamModel]] = {}

            for plan_type, candidates in grouped.items():
                models = await _fetch_with_failover(
                    candidates,
                    encryptor,
                    accounts_repo,
                )
                if models is not None:
                    per_plan_results[plan_type] = models

            if per_plan_results:
                registry = get_model_registry()
                registry.update(per_plan_results)
                total_models = len(registry.get_snapshot().models) if registry.get_snapshot() else 0
                logger.info(
                    "Model registry refreshed plans=%d total_models=%d",
                    len(per_plan_results),
                    total_models,
                )
            else:
                logger.warning("Model registry refresh failed for all plans")
        except Exception:
            logger.exception("Model registry refresh loop failed")
        finally:
            if session.in_transaction():
                await _safe_rollback(session)
            await _safe_close(session)


def _group_by_plan(accounts: list[Account]) -> dict[str, list[Account]]:
    grouped: dict[str, list[Account]] = {}
    for account in accounts:
        if account.status != AccountStatus.ACTIVE:
            continue
        plan_type = account.plan_type
        if not plan_type:
            continue
        grouped.setdefault(plan_type, []).append(account)
    return grouped


async def _fetch_with_failover(
    candidates: list[Account],
    encryptor: TokenEncryptor,
    accounts_repo: AccountsRepository,
) -> list[UpstreamModel] | None:
    for account in candidates:
        try:
            auth_manager = AuthManager(accounts_repo)
            account = await auth_manager.ensure_fresh(account)
            access_token = encryptor.decrypt(account.access_token_encrypted)
            account_id = account.chatgpt_account_id
            return await fetch_models_for_plan(access_token, account_id)
        except ModelFetchError as exc:
            if exc.status_code == 401:
                try:
                    account = await auth_manager.ensure_fresh(account, force=True)
                    access_token = encryptor.decrypt(account.access_token_encrypted)
                    return await fetch_models_for_plan(access_token, account.chatgpt_account_id)
                except (ModelFetchError, RefreshError):
                    logger.warning(
                        "Model fetch 401 retry failed account=%s plan=%s",
                        account.id,
                        account.plan_type,
                        exc_info=True,
                    )
                    continue
            logger.warning(
                "Model fetch failed account=%s plan=%s status=%d",
                account.id,
                account.plan_type,
                exc.status_code,
                exc_info=True,
            )
            continue
        except RefreshError:
            logger.warning(
                "Token refresh failed for model fetch account=%s plan=%s",
                account.id,
                account.plan_type,
                exc_info=True,
            )
            continue
        except Exception:
            logger.warning(
                "Unexpected error during model fetch account=%s plan=%s",
                account.id,
                account.plan_type,
                exc_info=True,
            )
            continue
    return None


def build_model_refresh_scheduler() -> ModelRefreshScheduler:
    settings = get_settings()
    return ModelRefreshScheduler(
        interval_seconds=settings.model_registry_refresh_interval_seconds,
        enabled=settings.model_registry_enabled,
    )
