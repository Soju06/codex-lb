from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol, cast

from app.core.auth.refresh import RefreshError
from app.core.clients.http import refresh_http_client
from app.core.clients.model_fetcher import ModelFetchError, fetch_models_for_plan
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import UpstreamModel, get_model_registry
from app.core.upstream_proxy import ResolvedUpstreamRoute, resolve_upstream_route
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)
_model_refresh_auth_cooldowns: dict[str, float] = {}


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


@dataclass(slots=True)
class _TransportRecoveryState:
    attempted: bool = False


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class _ModelFetchPlanResult:
    models: list[UpstreamModel] | None
    attempted: bool
    cooled_down_only: bool = False


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
        is_leader = await _get_leader_election().try_acquire()
        if not is_leader:
            return
        try:
            async with get_background_session() as session:
                accounts_repo = AccountsRepository(session)
                accounts = await accounts_repo.list_accounts()
                grouped = _group_by_plan(accounts)
                if not grouped:
                    logger.debug("No active accounts for model registry refresh")
                    return

                encryptor = TokenEncryptor()
                per_plan_results: dict[str, list[UpstreamModel]] = {}
                cooldown_only_plans = 0

                for plan_type, candidates in grouped.items():
                    result = await _fetch_with_failover(
                        candidates,
                        encryptor,
                        accounts_repo,
                    )
                    if result.models is not None:
                        per_plan_results[plan_type] = result.models
                    elif result.cooled_down_only:
                        cooldown_only_plans += 1

                if per_plan_results:
                    registry = get_model_registry()
                    await registry.update(per_plan_results)
                    snapshot = registry.get_snapshot()
                    total_models = len(snapshot.models) if snapshot else 0
                    logger.info(
                        "Model registry refreshed plans=%d total_models=%d",
                        len(per_plan_results),
                        total_models,
                    )
                    get_account_selection_cache().invalidate()
                elif cooldown_only_plans == len(grouped):
                    logger.info("Model registry refresh skipped; all plan candidates are in auth-failure cooldown")
                else:
                    logger.warning("Model registry refresh failed for all plans")
        except Exception:
            logger.exception("Model registry refresh loop failed")


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


def _error_summary(exc: BaseException) -> str:
    if isinstance(exc, ModelFetchError):
        summary = f"status={exc.status_code} transport={exc.transport_error}"
        if exc.message:
            summary = f"{summary} message={_compact_error_message(exc.message)}"
        return summary
    if isinstance(exc, RefreshError):
        summary = f"code={exc.code} permanent={exc.is_permanent} transport={exc.transport_error}"
        if exc.message:
            summary = f"{summary} message={_compact_error_message(exc.message)}"
        return summary

    message = _compact_error_message(str(exc))
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def _compact_error_message(message: str) -> str:
    return " ".join(message.split())


async def _fetch_with_failover(
    candidates: list[Account],
    encryptor: TokenEncryptor,
    accounts_repo: AccountsRepository,
) -> _ModelFetchPlanResult:
    transport_recovery = _TransportRecoveryState()
    _prune_model_refresh_auth_cooldowns()
    attempted = False
    skipped_due_to_cooldown = 0

    for account in candidates:
        if _model_refresh_auth_cooldown_active(account.id):
            skipped_due_to_cooldown += 1
            logger.debug(
                "Skipping model fetch during auth cooldown account=%s plan=%s",
                account.id,
                account.plan_type,
            )
            continue

        auth_manager = AuthManager(accounts_repo)
        try:
            attempted = True
            account = await _ensure_fresh_with_transport_recovery(
                auth_manager,
                account,
                transport_recovery=transport_recovery,
            )
            models = await _fetch_models_with_transport_recovery(
                account,
                encryptor,
                transport_recovery=transport_recovery,
            )
            _clear_model_refresh_auth_cooldown(account.id)
            return _ModelFetchPlanResult(models=models, attempted=True)
        except ModelFetchError as exc:
            if exc.status_code == 401:
                try:
                    account = await _ensure_fresh_with_transport_recovery(
                        auth_manager,
                        account,
                        force=True,
                        transport_recovery=transport_recovery,
                    )
                    models = await _fetch_models_with_transport_recovery(
                        account,
                        encryptor,
                        transport_recovery=transport_recovery,
                    )
                    _clear_model_refresh_auth_cooldown(account.id)
                    return _ModelFetchPlanResult(models=models, attempted=True)
                except ModelFetchError as retry_exc:
                    _mark_model_refresh_auth_cooldown(account.id, retry_exc.status_code)
                    logger.warning(
                        "Model fetch retry failed account=%s plan=%s status=%d upstream_request_id=%s preview=%r",
                        account.id,
                        account.plan_type,
                        retry_exc.status_code,
                        retry_exc.upstream_request_id,
                        retry_exc.response_preview,
                        exc_info=True,
                    )
                    continue
                except RefreshError as retry_exc:
                    logger.warning(
                        "Token refresh failed for model fetch retry account=%s plan=%s error=%s",
                        account.id,
                        account.plan_type,
                        _error_summary(retry_exc),
                    )
                    continue
            _mark_model_refresh_auth_cooldown(account.id, exc.status_code)
            logger.warning(
                "Model fetch failed account=%s plan=%s status=%d upstream_request_id=%s preview=%r",
                account.id,
                account.plan_type,
                exc.status_code,
                exc.upstream_request_id,
                exc.response_preview,
                exc_info=True,
            )
            continue
        except RefreshError as exc:
            logger.warning(
                "Token refresh failed for model fetch account=%s plan=%s error=%s",
                account.id,
                account.plan_type,
                _error_summary(exc),
            )
            continue
        except Exception as exc:
            logger.warning(
                "Unexpected error during model fetch account=%s plan=%s error=%s",
                account.id,
                account.plan_type,
                _error_summary(exc),
                exc_info=True,
            )
            continue
    return _ModelFetchPlanResult(
        models=None,
        attempted=attempted,
        cooled_down_only=not attempted and skipped_due_to_cooldown == len(candidates),
    )


async def _ensure_fresh_with_transport_recovery(
    auth_manager: AuthManager,
    account: Account,
    *,
    transport_recovery: _TransportRecoveryState,
    force: bool = False,
) -> Account:
    try:
        return await auth_manager.ensure_fresh(account, force=force)
    except RefreshError as exc:
        if not exc.transport_error or transport_recovery.attempted:
            raise

        await _refresh_http_client_after_transport_error(account, exc)
        transport_recovery.attempted = True
        return await auth_manager.ensure_fresh(account, force=force)


async def _fetch_models_with_transport_recovery(
    account: Account,
    encryptor: TokenEncryptor,
    *,
    transport_recovery: _TransportRecoveryState,
) -> list[UpstreamModel]:
    access_token = encryptor.decrypt(account.access_token_encrypted)
    account_id = account.chatgpt_account_id
    route = await _resolve_upstream_route_for_account(account, operation="model_discovery")

    try:
        return await fetch_models_for_plan(
            access_token,
            account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except ModelFetchError as exc:
        if not exc.transport_error or transport_recovery.attempted:
            raise

        await _refresh_http_client_after_transport_error(account, exc)
        transport_recovery.attempted = True
        access_token = encryptor.decrypt(account.access_token_encrypted)
        account_id = account.chatgpt_account_id
        route = await _resolve_upstream_route_for_account(account, operation="model_discovery")
        return await fetch_models_for_plan(
            access_token,
            account_id,
            route=route,
            allow_direct_egress=route is None,
        )


async def _resolve_upstream_route_for_account(account: Account, *, operation: str) -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        return await resolve_upstream_route(
            session,
            account_id=account.id,
            operation=operation,
            scope="account",
        )


async def _refresh_http_client_after_transport_error(account: Account, transport_exc: BaseException) -> None:
    try:
        await refresh_http_client()
    except Exception as refresh_exc:
        logger.warning(
            "Model fetch transport recovery failed account=%s plan=%s transport_error=%s refresh_error=%s",
            account.id,
            account.plan_type,
            _error_summary(transport_exc),
            _error_summary(refresh_exc),
        )
        raise
    logger.info(
        "Refreshed shared HTTP client after model fetch transport error; retrying account=%s plan=%s error=%s",
        account.id,
        account.plan_type,
        _error_summary(transport_exc),
    )


def _mark_model_refresh_auth_cooldown(account_id: str, status_code: int) -> None:
    if status_code not in {401, 403}:
        return
    cooldown_seconds = max(0.0, float(get_settings().model_registry_refresh_auth_failure_cooldown_seconds))
    if cooldown_seconds <= 0:
        return
    _model_refresh_auth_cooldowns[account_id] = time.monotonic() + cooldown_seconds


def _model_refresh_auth_cooldown_active(account_id: str) -> bool:
    expires_at = _model_refresh_auth_cooldowns.get(account_id)
    if expires_at is None:
        return False
    if expires_at > time.monotonic():
        return True
    _model_refresh_auth_cooldowns.pop(account_id, None)
    return False


def _clear_model_refresh_auth_cooldown(account_id: str) -> None:
    _model_refresh_auth_cooldowns.pop(account_id, None)


def _prune_model_refresh_auth_cooldowns() -> None:
    now = time.monotonic()
    stale = [account_id for account_id, expires_at in _model_refresh_auth_cooldowns.items() if expires_at <= now]
    for account_id in stale:
        _model_refresh_auth_cooldowns.pop(account_id, None)


def build_model_refresh_scheduler() -> ModelRefreshScheduler:
    settings = get_settings()
    return ModelRefreshScheduler(
        interval_seconds=settings.model_registry_refresh_interval_seconds,
        enabled=settings.model_registry_enabled,
    )
