"""Tests for ``codex_lb_claude_*`` Prometheus metrics.

Phase 13 of ``openspec/changes/add-claude-oauth-pool``. The three new metrics
defined in ``app/core/metrics/prometheus.py`` are:

- ``codex_lb_claude_requests_total`` (counter, labels: ``status``)
- ``codex_lb_claude_refresh_total`` (counter, labels: ``result``)
- ``codex_lb_claude_accounts_active`` (gauge, no labels)

These tests cover (1) metric registration when ``prometheus_client`` is
present, (2) increments emitted from the auth manager and proxy service at
the correct code paths, and (3) the gauge being set from the canonical
``ClaudeAccountRepository.count_active`` value at scrape time.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake prometheus_client mirroring the one used in tests/unit/test_metrics.py
# ---------------------------------------------------------------------------


class _MetricChild:
    def __init__(self) -> None:
        self.value = 0.0
        self.observations: list[float] = []

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        self.value -= amount

    def observe(self, amount: float) -> None:
        self.observations.append(amount)


class _MetricBase:
    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | None = None,
        registry=None,
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames or [])
        self.registry = registry
        self.samples: dict[tuple[tuple[str, str], ...], _MetricChild] = {}
        self.root = _MetricChild()

    def labels(self, **labels: str) -> _MetricChild:
        key = tuple(sorted(labels.items()))
        return self.samples.setdefault(key, _MetricChild())

    def inc(self, amount: float = 1.0) -> None:
        self.root.inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        self.root.dec(amount)

    def observe(self, amount: float) -> None:
        self.root.observe(amount)


class _Counter(_MetricBase):
    pass


class _Histogram(_MetricBase):
    pass


class _Gauge(_MetricBase):
    def set(self, value: float) -> None:
        self.root.value = value


class _CollectorRegistry:
    def __init__(self, *, auto_describe: bool) -> None:
        self.auto_describe = auto_describe


def _fake_prometheus_client_module() -> types.ModuleType:
    module = types.ModuleType("prometheus_client")
    setattr(module, "Counter", _Counter)
    setattr(module, "Histogram", _Histogram)
    setattr(module, "Gauge", _Gauge)
    setattr(module, "CollectorRegistry", _CollectorRegistry)
    return module


# ---------------------------------------------------------------------------
# Module-isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_metrics_modules() -> Iterator[None]:
    """Force re-import of the prometheus module between tests so module-level
    metric registrations don't leak across test cases. The Claude proxy
    service captures the counter symbol at import time, so we drop both
    modules here and let each test (re)load what it needs."""
    module_names = (
        "app.core.metrics.prometheus",
        "app.modules.claude.service",
        "app.modules.claude.auth_manager",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    # Drop at setup too so the test starts with a clean slate.
    for name in module_names:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        for name, module in previous.items():
            if module is not None:
                sys.modules[name] = module


def _load_prometheus_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prometheus_client_module: types.ModuleType | None,
    reload_claude_modules: bool = False,
) -> types.ModuleType:
    sys.modules.pop("app.core.metrics.prometheus", None)
    if prometheus_client_module is not None:
        monkeypatch.setitem(sys.modules, "prometheus_client", prometheus_client_module)
    else:
        monkeypatch.delitem(sys.modules, "prometheus_client", raising=False)
        real_import = builtins.__import__

        def _missing_prometheus_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "prometheus_client":
                raise ImportError("prometheus_client is not installed")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _missing_prometheus_import)
    prometheus_module = importlib.import_module("app.core.metrics.prometheus")
    if reload_claude_modules:
        # Force the Claude modules to re-bind their metric symbols to the
        # freshly-loaded prometheus module. Without this the test sees the
        # stale reference imported at first load time.
        for name in (
            "app.modules.claude.service",
            "app.modules.claude.auth_manager",
        ):
            sys.modules.pop(name, None)
    return prometheus_module


# ---------------------------------------------------------------------------
# 1. Metric registration
# ---------------------------------------------------------------------------


def test_claude_metrics_registered_when_prometheus_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prom = _load_prometheus_module(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    assert prom.PROMETHEUS_AVAILABLE is True

    requests_total = prom.codex_lb_claude_requests_total
    assert requests_total.name == "codex_lb_claude_requests_total"
    assert requests_total.labelnames == ("status",)

    refresh_total = prom.codex_lb_claude_refresh_total
    assert refresh_total.name == "codex_lb_claude_refresh_total"
    assert refresh_total.labelnames == ("result",)

    accounts_active = prom.codex_lb_claude_accounts_active
    assert accounts_active.name == "codex_lb_claude_accounts_active"
    # Gauge has no labels per the spec.
    assert accounts_active.labelnames == ()


def test_claude_metrics_absent_when_prometheus_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prom = _load_prometheus_module(monkeypatch, prometheus_client_module=None)

    assert prom.PROMETHEUS_AVAILABLE is False
    assert prom.codex_lb_claude_requests_total is None
    assert prom.codex_lb_claude_refresh_total is None
    assert prom.codex_lb_claude_accounts_active is None


def test_claude_metrics_exposed_via_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three new metrics MUST be re-exported in ``__all__`` so callers
    can ``from app.core.metrics.prometheus import codex_lb_claude_*``."""
    prom = _load_prometheus_module(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    for symbol in (
        "codex_lb_claude_requests_total",
        "codex_lb_claude_refresh_total",
        "codex_lb_claude_accounts_active",
    ):
        assert symbol in prom.__all__, f"missing {symbol} in __all__"


# ---------------------------------------------------------------------------
# 2. Refresh counter increments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_claude_access_token_success_increments_refresh_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful refresh MUST increment
    ``codex_lb_claude_refresh_total{result='success'}`` exactly once."""
    from datetime import datetime, timedelta, timezone

    from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
    from app.core.clients.anthropic.oauth import ClaudeRefreshResult
    from app.core.crypto import TokenEncryptor

    _load_prometheus_module(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())
    # Re-import auth_manager so it picks up the registered metrics module.
    sys.modules.pop("app.modules.claude.auth_manager", None)
    auth_manager_module = importlib.import_module("app.modules.claude.auth_manager")
    ClaudeAuthManager = auth_manager_module.ClaudeAuthManager
    clear_claude_refresh_singleflight_state = auth_manager_module.clear_claude_refresh_singleflight_state
    clear_claude_refresh_singleflight_state()

    class _FakeEncryptor:
        def encrypt(self, plaintext: str) -> bytes:
            return f"enc::{plaintext}".encode("utf-8")

        def decrypt(self, ciphertext: bytes) -> str:
            return ciphertext.decode("utf-8").removeprefix("enc::")

    class _FakeRepo:
        def __init__(self) -> None:
            self.persisted: dict[str, dict[str, object]] = {}

        async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
            return False

        async def insert(self, row: dict[str, object]):
            self.persisted[row["id"]] = row  # ty:ignore[invalid-assignment]
            return type("R", (), {"id": row["id"], "claude_account_uuid": row["claude_account_uuid"]})()

        async def get_by_id(self, account_id: str):  # pragma: no cover - unused here
            return None

        async def update_tokens(self, **_kwargs):
            row = self.persisted.setdefault(_kwargs["account_id"], {})
            row["claude_access_token_encrypted"] = _kwargs["access_token_encrypted"]
            row["claude_refresh_token_encrypted"] = _kwargs["refresh_token_encrypted"]
            row["claude_access_token_expires_at"] = _kwargs["access_token_expires_at"]
            return True

        async def deactivate(self, account_id: str, *, reason: str) -> bool:
            self.persisted.setdefault(account_id, {})["status"] = "DEACTIVATED"
            return True

        async def activate(self, account_id: str) -> bool:  # pragma: no cover
            return True

        async def list_accounts(self) -> list:  # pragma: no cover
            return []

        async def find_due_for_rotation(self, *, skew_seconds: int, now: datetime) -> list:
            return []

        def seed(self, account_id: str = "claude-abc-123") -> "object":
            encryptor = _FakeEncryptor()
            from app.db.models import Account, AccountStatus

            account = Account(
                id=account_id,
                provider="claude",
                status=AccountStatus.ACTIVE,
                plan_type="claude_subscription",
                routing_policy="normal",
                access_token_encrypted=encryptor.encrypt("placeholder"),
                refresh_token_encrypted=encryptor.encrypt("placeholder"),
                id_token_encrypted=encryptor.encrypt("placeholder"),
                last_refresh=datetime.now(timezone.utc),
                claude_account_uuid=account_id.removeprefix("claude-"),
                claude_access_token_encrypted=encryptor.encrypt("AT"),
                claude_refresh_token_encrypted=encryptor.encrypt("RT"),
                claude_access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            self.persisted[account.id] = {
                "id": account.id,
                "status": AccountStatus.ACTIVE.value,
                "claude_access_token_encrypted": account.claude_access_token_encrypted,
                "claude_refresh_token_encrypted": account.claude_refresh_token_encrypted,
            }
            return account

    class _FakeOAuthClient:
        def __init__(self) -> None:
            self.next_result = ClaudeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

        async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
            return self.next_result

    repo = _FakeRepo()
    account = repo.seed()
    # ``scoped_repo_factory`` routes writes through the in-memory ``_FakeRepo``
    # instead of ``SqlClaudeAccountRepository`` (which would need a real DB
    # session). The session yielded by the default ``get_background_session``
    # is irrelevant on this path because the test's write target is the
    # ``_FakeRepo``; the real write codepath is exercised in
    # ``tests/unit/test_claude_account_service.py``.
    manager = ClaudeAuthManager(
        repo=repo,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        encryptor=_FakeEncryptor(),  # ty:ignore[invalid-argument-type]
        oauth_client=_FakeOAuthClient(),
        scoped_repo_factory=lambda _session: repo,  # type: ignore[arg-type,return-value]  # ty:ignore[invalid-argument-type]
    )

    # Re-resolve the metrics symbol so we read the value from the loaded module.
    from app.core.metrics import prometheus as prom

    refresh = prom.codex_lb_claude_refresh_total
    assert refresh is not None

    result = await manager.rotate_claude_access_token(account)  # ty:ignore[invalid-argument-type]
    assert result is not None

    success_sample = refresh.samples[(("result", "success"),)]  # ty:ignore[unresolved-attribute]
    assert success_sample.value == 1.0

    clear_claude_refresh_singleflight_state()
    _ = (ClaudeAuthError, ClaudeUpstreamError, TokenEncryptor)


@pytest.mark.asyncio
async def test_rotate_claude_access_token_invalid_grant_increments_refresh_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``invalid_grant`` from the OAuth client MUST increment
    ``codex_lb_claude_refresh_total{result='invalid_grant'}``."""
    from datetime import datetime, timedelta, timezone

    from app.core.clients.anthropic.errors import ClaudeAuthError

    _load_prometheus_module(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())
    # Re-import auth_manager so it picks up the registered metrics module.
    sys.modules.pop("app.modules.claude.auth_manager", None)
    auth_manager_module = importlib.import_module("app.modules.claude.auth_manager")
    ClaudeAuthManager = auth_manager_module.ClaudeAuthManager
    clear_claude_refresh_singleflight_state = auth_manager_module.clear_claude_refresh_singleflight_state
    clear_claude_refresh_singleflight_state()

    class _FakeEncryptor:
        def encrypt(self, plaintext: str) -> bytes:
            return f"enc::{plaintext}".encode("utf-8")

        def decrypt(self, ciphertext: bytes) -> str:
            return ciphertext.decode("utf-8").removeprefix("enc::")

    class _FakeRepo:
        def __init__(self) -> None:
            self.persisted: dict[str, dict[str, object]] = {}

        async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
            return False

        async def insert(self, row):  # pragma: no cover
            self.persisted[row["id"]] = row
            return type("R", (), {"id": row["id"], "claude_account_uuid": ""})()

        async def get_by_id(self, account_id: str):  # pragma: no cover
            return None

        async def update_tokens(self, **_kwargs):
            self.persisted.setdefault(_kwargs["account_id"], {}).update(_kwargs)
            return True

        async def deactivate(self, account_id: str, *, reason: str) -> bool:
            self.persisted.setdefault(account_id, {})["status"] = "DEACTIVATED"
            return True

        async def activate(self, account_id: str) -> bool:  # pragma: no cover
            return True

        async def list_accounts(self) -> list:  # pragma: no cover
            return []

        async def find_due_for_rotation(self, *, skew_seconds: int, now: datetime) -> list:
            return []

        def seed(self, account_id: str = "claude-abc-123") -> "object":
            from app.db.models import Account, AccountStatus

            enc = _FakeEncryptor()
            account = Account(
                id=account_id,
                provider="claude",
                status=AccountStatus.ACTIVE,
                plan_type="claude_subscription",
                routing_policy="normal",
                access_token_encrypted=enc.encrypt("placeholder"),
                refresh_token_encrypted=enc.encrypt("placeholder"),
                id_token_encrypted=enc.encrypt("placeholder"),
                last_refresh=datetime.now(timezone.utc),
                claude_account_uuid=account_id.removeprefix("claude-"),
                claude_access_token_encrypted=enc.encrypt("AT"),
                claude_refresh_token_encrypted=enc.encrypt("RT"),
                claude_access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            self.persisted[account.id] = {
                "id": account.id,
                "status": AccountStatus.ACTIVE.value,
            }
            return account

    class _FakeOAuthClient:
        async def refresh(self, refresh_token: str):
            raise ClaudeAuthError("invalid_grant")

    repo = _FakeRepo()
    account = repo.seed()
    manager = ClaudeAuthManager(
        repo=repo,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        encryptor=_FakeEncryptor(),  # ty:ignore[invalid-argument-type]
        oauth_client=_FakeOAuthClient(),
        scoped_repo_factory=lambda _session: repo,  # type: ignore[arg-type,return-value]  # ty:ignore[invalid-argument-type]
    )

    result = await manager.rotate_claude_access_token(account)  # ty:ignore[invalid-argument-type]
    assert result is None  # invalid_grant → deactivated, returns None

    from app.core.metrics import prometheus as prom

    refresh = prom.codex_lb_claude_refresh_total
    assert refresh is not None
    invalid_sample = refresh.samples[(("result", "invalid_grant"),)]  # ty:ignore[unresolved-attribute]
    assert invalid_sample.value == 1.0

    clear_claude_refresh_singleflight_state()


# ---------------------------------------------------------------------------
# 3. Request counter increments via the proxy service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_consecutive_401s_increment_requests_total_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When two consecutive 401s propagate, ``codex_lb_claude_requests_total``
    MUST be incremented with ``status='auth_error'``."""
    from app.core.clients.anthropic.errors import ClaudeAuthError

    _load_prometheus_module(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
        reload_claude_modules=True,
    )

    # Re-import AFTER the prometheus module is freshly loaded so the counter
    # symbol resolves to the freshly-registered counter.
    from app.modules.claude.auth_manager import ClaudeAuthManager  # noqa: E402
    from app.modules.claude.service import ClaudeProxyService  # noqa: E402

    class _Account:
        def __init__(self) -> None:
            self.id = "claude-1"
            self.provider = "claude"

    class _Chat:
        def __init__(self) -> None:
            self.calls = 0

        async def send_messages(self, *, access_token, request_body):
            self.calls += 1
            raise ClaudeAuthError("anthropic 401")

    class _Auth(ClaudeAuthManager):
        def __init__(self) -> None:  # noqa: D401 - test stub skips parent __init__
            self.rotate_calls = 0

        async def get_access_token(self, account):
            return "AT"

        async def rotate_claude_access_token(self, account):
            self.rotate_calls += 1
            return type("R", (), {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600})()

    class _LB:
        def __init__(self) -> None:
            self.health_calls = 0

        async def select_account(self, **_):
            return type("S", (), {"account": _Account()})()

        async def record_claude_rate_limit_response(self, **_):
            return None

        async def record_error(self, _account):
            self.health_calls += 1

    class _Repo:
        async def update_rate_limit_cache(self, account_id, fields):
            return True

        async def update_last_used_at(self, account_id, *, at):
            return True

    class _Logs:
        async def add_log(self, **_):
            return None

    service = ClaudeProxyService(
        load_balancer=_LB(),  # ty:ignore[invalid-argument-type]
        chat=_Chat(),  # ty:ignore[invalid-argument-type]
        auth_manager=_Auth(),
        accounts_repository=_Repo(),
        request_log_repository=_Logs(),
    )

    with pytest.raises(ClaudeAuthError):
        await service.stream_or_complete_messages(
            request_body={"model": "claude-opus-4-8"},
            api_key=type("K", (), {"provider_scope": "claude"})(),  # ty:ignore[invalid-argument-type]
            request_id="r-double-401",
        )

    from app.core.metrics import prometheus as prom

    requests_total = prom.codex_lb_claude_requests_total
    assert requests_total is not None
    auth_error_sample = requests_total.samples[(("status", "auth_error"),)]  # ty:ignore[unresolved-attribute]
    assert auth_error_sample.value == 1.0


@pytest.mark.asyncio
async def test_429_increments_requests_total_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 from Anthropic MUST increment
    ``codex_lb_claude_requests_total{status='rate_limited'}``."""
    from app.core.clients.anthropic.errors import ClaudeRateLimited

    _load_prometheus_module(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
        reload_claude_modules=True,
    )

    from app.modules.claude.auth_manager import ClaudeAuthManager  # noqa: E402
    from app.modules.claude.service import ClaudeProxyService  # noqa: E402

    class _Account:
        id = "claude-1"
        provider = "claude"

    class _Chat:
        async def send_messages(self, *, access_token, request_body):
            raise ClaudeRateLimited("anthropic 429", headers={})

    class _Auth(ClaudeAuthManager):
        def __init__(self) -> None:  # noqa: D401 - test stub skips parent __init__
            pass

        async def get_access_token(self, account):
            return "AT"

        async def rotate_claude_access_token(self, account):
            return None

    class _LB:
        def __init__(self) -> None:
            self.record_calls = 0

        async def select_account(self, **_):
            return type("S", (), {"account": _Account()})()

        async def record_claude_rate_limit_response(self, **_):
            self.record_calls += 1

        async def record_error(self, _account):
            return None

    class _Repo:
        async def update_rate_limit_cache(self, account_id, fields):
            return True

        async def update_last_used_at(self, account_id, *, at):
            return True

    class _Logs:
        async def add_log(self, **_):
            return None

    service = ClaudeProxyService(
        load_balancer=_LB(),  # ty:ignore[invalid-argument-type]
        chat=_Chat(),  # ty:ignore[invalid-argument-type]
        auth_manager=_Auth(),
        accounts_repository=_Repo(),
        request_log_repository=_Logs(),
    )

    headers = {"anthropic-ratelimit-status": "rejected"}

    # Patch the chat side-effect to attach headers per the spec scenario.
    async def _send(*, access_token, request_body):
        raise ClaudeRateLimited("anthropic 429", headers=headers)

    service._chat.send_messages = _send  # type: ignore[attr-defined]  # ty:ignore[invalid-assignment]

    with pytest.raises(ClaudeRateLimited):
        await service.stream_or_complete_messages(
            request_body={"model": "claude-opus-4-8"},
            api_key=type("K", (), {"provider_scope": "claude"})(),  # ty:ignore[invalid-argument-type]
            request_id="r-429",
        )

    from app.core.metrics import prometheus as prom

    requests_total = prom.codex_lb_claude_requests_total
    assert requests_total is not None
    rl_sample = requests_total.samples[(("status", "rate_limited"),)]  # ty:ignore[unresolved-attribute]
    assert rl_sample.value == 1.0


@pytest.mark.asyncio
async def test_successful_request_increments_requests_total_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 from Anthropic MUST increment
    ``codex_lb_claude_requests_total{status='success'}``."""

    _load_prometheus_module(
        monkeypatch,
        prometheus_client_module=_fake_prometheus_client_module(),
        reload_claude_modules=True,
    )

    from app.modules.claude.auth_manager import ClaudeAuthManager  # noqa: E402
    from app.modules.claude.service import ClaudeProxyService  # noqa: E402

    class _Account:
        id = "claude-1"
        provider = "claude"

    class _Chat:
        async def send_messages(self, *, access_token, request_body):
            return {"id": "msg_01", "usage": {"input_tokens": 1, "output_tokens": 1}}, {}

    class _Auth(ClaudeAuthManager):
        def __init__(self) -> None:  # noqa: D401 - test stub skips parent __init__
            pass

        async def get_access_token(self, account):
            return "AT"

        async def rotate_claude_access_token(self, account):
            return None

    class _LB:
        async def select_account(self, **_):
            return type("S", (), {"account": _Account()})()

        async def record_claude_rate_limit_response(self, **_):
            return None

        async def record_error(self, _account):
            return None

    class _Repo:
        async def update_rate_limit_cache(self, account_id, fields):
            return True

        async def update_last_used_at(self, account_id, *, at):
            return True

    class _Logs:
        async def add_log(self, **_):
            return None

    service = ClaudeProxyService(
        load_balancer=_LB(),  # ty:ignore[invalid-argument-type]
        chat=_Chat(),  # ty:ignore[invalid-argument-type]
        auth_manager=_Auth(),
        accounts_repository=_Repo(),
        request_log_repository=_Logs(),
    )

    await service.stream_or_complete_messages(
        request_body={"model": "claude-opus-4-8"},
        api_key=type("K", (), {"provider_scope": "claude"})(),  # ty:ignore[invalid-argument-type]
        request_id="r-ok",
    )

    from app.core.metrics import prometheus as prom

    requests_total = prom.codex_lb_claude_requests_total
    assert requests_total is not None
    success_sample = requests_total.samples[(("status", "success"),)]  # ty:ignore[unresolved-attribute]
    assert success_sample.value == 1.0


# ---------------------------------------------------------------------------
# 4. Active accounts gauge reflects count_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_gauge_set_from_repository_count_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``codex_lb_claude_accounts_active`` MUST equal
    ``ClaudeAccountRepository.count_active()`` whenever the gauge is refreshed
    from the /metrics scrape path.

    The test reads the gauge directly via :mod:`app.core.metrics.prometheus`
    after the prometheus module has been freshly loaded with the fake
    client, and asserts the helper sets the value to whatever the repository
    returns.
    """
    _load_prometheus_module(monkeypatch, prometheus_client_module=_fake_prometheus_client_module())

    # Pop Claude service modules so re-import below binds metric symbols to
    # the freshly-loaded prometheus module (other test files in the suite
    # may have already imported them at collection time).
    for name in (
        "app.modules.claude.service",
        "app.modules.claude.auth_manager",
    ):
        sys.modules.pop(name, None)

    from app.core.metrics import prometheus as prom

    gauge = prom.codex_lb_claude_accounts_active
    assert gauge is not None

    # Re-import the service so its top-level symbol binds to the fresh gauge.
    import importlib

    service_module = importlib.import_module("app.modules.claude.service")

    # If service.py captured a stale (None) reference during a prior import
    # at collection time, the helper short-circuits and the test would fail
    # — but the binding IS fresh after the pop+reimport above.
    assert service_module.codex_lb_claude_accounts_active is gauge

    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        async def count_active(self) -> int:
            self.calls += 1
            return 7

    repo = _Repo()
    updated = await service_module.refresh_claude_accounts_active_gauge(repo)

    assert updated == 7
    assert gauge.root.value == 7.0  # ty:ignore[unresolved-attribute]
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_active_gauge_repository_protocol_exposes_count_active() -> None:
    """``ClaudeAccountRepository`` protocol MUST include ``count_active``.

    Phase 13 of the implementation plan calls out that the gauge is updated
    from ``ClaudeAccountRepository.count_active()`` at scrape time. The
    protocol that backs both the SQL and test implementations MUST declare
    this method so a stub doesn't silently miss it.
    """
    from app.modules.claude.repository import ClaudeAccountRepository

    assert hasattr(ClaudeAccountRepository, "count_active")
