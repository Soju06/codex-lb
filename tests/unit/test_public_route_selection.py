from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_service_module
from app.core.crypto import TokenEncryptor
from app.db.models import AccountStatus, OpenAIPlatformIdentity, StickySessionKind
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickyRoutingTarget
from app.modules.upstream_identities.types import (
    BACKEND_CODEX_HTTP_ROUTE_FAMILY,
    CHATGPT_PRIVATE_ROUTE_CLASS,
    OPENAI_PLATFORM_PROVIDER_KIND,
    OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
    PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
)

pytestmark = pytest.mark.unit


def _responses_request(**overrides):
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": "hi",
    }
    payload.update(overrides)
    return proxy_service_module.ResponsesRequest.model_validate(payload)


def _compact_request(**overrides):
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": "hi",
    }
    payload.update(overrides)
    return proxy_api_module.ResponsesCompactRequest.model_validate(payload)


def _adapt_compatible_candidate_checker(checker):
    async def _wrapped(model: str | None = None, *, additional_limit_name=None, account_ids=None) -> bool:
        del additional_limit_name
        return await checker(model, account_ids=account_ids)

    return _wrapped


def _adapt_should_fallback(checker):
    async def _wrapped(*, model: str | None, additional_limit_name=None, account_ids=None) -> bool:
        del additional_limit_name
        return await checker(model=model, account_ids=account_ids)

    return _wrapped


class DummyStickyRepository:
    def __init__(self, target: StickyRoutingTarget | None = None) -> None:
        self._target = target
        self.upsert_calls: list[tuple[str, StickySessionKind, str, str]] = []
        self.delete_calls: list[tuple[str, StickySessionKind, str]] = []

    async def get_target(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
        max_age_seconds: int | None = None,
    ) -> StickyRoutingTarget | None:
        del key, kind, provider_kind, max_age_seconds
        return self._target

    async def upsert_target(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: str,
        routing_subject_id: str,
        account_id: str | None = None,
    ):
        del account_id
        self.upsert_calls.append((key, kind, provider_kind, routing_subject_id))
        return SimpleNamespace()

    async def delete_scoped(self, key: str, *, kind: StickySessionKind, provider_kind: str) -> bool:
        self.delete_calls.append((key, kind, provider_kind))
        return True


class DummyPlatformIdentitiesRepository:
    def __init__(self, identities: list[OpenAIPlatformIdentity]) -> None:
        self._identities = identities

    async def list_eligible_identities(self, route_family: str) -> list[OpenAIPlatformIdentity]:
        return [
            identity
            for identity in self._identities
            if route_family in (identity.eligible_route_families or "")
            and identity.status not in (AccountStatus.PAUSED, AccountStatus.DEACTIVATED)
        ]

    async def get_by_id(self, identity_id: str) -> OpenAIPlatformIdentity | None:
        return next((identity for identity in self._identities if identity.id == identity_id), None)


def _platform_identity(identity_id: str) -> OpenAIPlatformIdentity:
    return OpenAIPlatformIdentity(
        id=identity_id,
        label=f"Platform {identity_id}",
        api_key_encrypted=TokenEncryptor().encrypt(f"sk-{identity_id}"),
        organization_id="org_test",
        project_id="proj_test",
        eligible_route_families="public_models_http,public_responses_http,backend_codex_http",
        status=AccountStatus.ACTIVE,
        last_validated_at=None,
        last_auth_failure_reason=None,
        deactivation_reason=None,
    )


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


@asynccontextmanager
async def _repo_factory(
    *,
    identities: list[OpenAIPlatformIdentity] | None = None,
    sticky_repo: DummyStickyRepository | None = None,
) -> AsyncIterator[ProxyRepositories]:
    yield cast(
        ProxyRepositories,
        SimpleNamespace(
            accounts=SimpleNamespace(),
            platform_identities=DummyPlatformIdentitiesRepository(identities or []),
            usage=SimpleNamespace(),
            request_logs=SimpleNamespace(),
            sticky_sessions=sticky_repo or DummyStickyRepository(),
            api_keys=SimpleNamespace(),
            additional_usage=SimpleNamespace(),
        ),
    )


def test_derive_request_capabilities_marks_stateless_public_http_as_platform_eligible() -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        payload=_responses_request(),
        headers={},
    )

    assert capabilities.route_family == PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY
    assert capabilities.transport == "http"
    assert capabilities.continuity_param is None


def test_derive_request_capabilities_marks_previous_response_id_as_continuity() -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        payload=_responses_request(previous_response_id="resp_prev_1"),
        headers={},
    )

    assert capabilities.continuity_param == "previous_response_id"


@pytest.mark.parametrize(
    "header_name",
    ["session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"],
)
def test_derive_request_capabilities_marks_session_headers_as_continuity(header_name: str) -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        payload=_responses_request(),
        headers={header_name: "sid_1"},
    )

    assert capabilities.continuity_param == header_name


@pytest.mark.parametrize(
    "header_name",
    ["session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"],
)
def test_derive_request_capabilities_marks_backend_codex_session_headers_as_continuity(
    header_name: str,
) -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        payload=_responses_request(),
        headers={header_name: "sid_1"},
    )

    assert capabilities.continuity_param is None
    assert capabilities.continuity_hint == header_name


@pytest.mark.parametrize(
    "header_name",
    ["session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"],
)
def test_derive_request_capabilities_marks_backend_codex_header_continuity(header_name: str) -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        payload=_responses_request(),
        headers={header_name: "sid_1"},
    )

    assert capabilities.continuity_param is None
    assert capabilities.continuity_hint == header_name


def test_derive_request_capabilities_marks_backend_codex_session_header_hint_without_payload() -> None:
    capabilities = proxy_api_module._derive_request_capabilities(
        route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
        route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
        transport="http",
        model="gpt-5.1",
        headers={"x-codex-session-id": "sid_1"},
    )

    assert capabilities.continuity_param is None
    assert capabilities.continuity_hint == "x-codex-session-id"


@pytest.mark.asyncio
async def test_selection_affinity_for_responses_reads_dashboard_settings_cache(monkeypatch) -> None:
    cache_settings = SimpleNamespace(
        sticky_threads_enabled=True,
        openai_cache_affinity_max_age_seconds=300,
    )
    monkeypatch.setattr(proxy_service_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(proxy_service_module, "get_settings_cache", lambda: _SettingsCache(cache_settings))

    affinity = await proxy_api_module._selection_affinity_for_responses_request(
        _responses_request(prompt_cache_key="thread_123"),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert affinity.kind == StickySessionKind.STICKY_THREAD
    assert affinity.key == "thread_123"


@pytest.mark.asyncio
async def test_selection_affinity_for_compact_reads_dashboard_settings_cache(monkeypatch) -> None:
    cache_settings = SimpleNamespace(
        sticky_threads_enabled=True,
        openai_cache_affinity_max_age_seconds=300,
    )
    monkeypatch.setattr(proxy_service_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(proxy_service_module, "get_settings_cache", lambda: _SettingsCache(cache_settings))

    affinity = await proxy_api_module._selection_affinity_for_compact_request(
        _compact_request(prompt_cache_key="thread_456"),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert affinity.kind == StickySessionKind.STICKY_THREAD
    assert affinity.key == "thread_456"


@pytest.mark.asyncio
async def test_select_routing_subject_keeps_chatgpt_primary_when_public_http_is_healthy(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.is_chatgpt is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedChatGPTSubject)
    assert selected.provider_kind == "chatgpt_web"


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_when_public_http_usage_is_drained(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert selected.routing_subject_id == "plat_1"


@pytest.mark.asyncio
async def test_select_routing_subject_keeps_platform_fallback_for_prompt_cache_affinity(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )
    sticky_probe_calls = 0
    platform_selection_kwargs: dict[str, object] = {}

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family
        platform_selection_kwargs.update(kwargs)
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        nonlocal sticky_probe_calls
        sticky_probe_calls += 1
        del kwargs
        return True

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        ),
        sticky_key="cache-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=300,
    )

    assert result.is_platform is True
    assert sticky_probe_calls == 0
    assert platform_selection_kwargs == {
        "sticky_key": "cache-key",
        "sticky_kind": StickySessionKind.PROMPT_CACHE,
        "reallocate_sticky": False,
        "sticky_max_age_seconds": 300,
    }


@pytest.mark.asyncio
async def test_select_routing_subject_reuses_platform_prompt_cache_affinity_when_chatgpt_recovers(
    monkeypatch,
) -> None:
    sticky_repo = DummyStickyRepository(
        StickyRoutingTarget(
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id="plat_2",
            account_id=None,
        )
    )
    service = proxy_service_module.ProxyService(
        lambda: _repo_factory(
            identities=[_platform_identity("plat_1"), _platform_identity("plat_2")],
            sticky_repo=sticky_repo,
        )
    )
    fallback_probe_calls = 0

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        nonlocal fallback_probe_calls
        fallback_probe_calls += 1
        del model, account_ids
        return False

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        ),
        sticky_key="cache-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=300,
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.routing_subject_id == "plat_2"
    assert fallback_probe_calls == 0
    assert sticky_repo.upsert_calls == [
        ("cache-key", StickySessionKind.PROMPT_CACHE, OPENAI_PLATFORM_PROVIDER_KIND, "plat_2")
    ]


@pytest.mark.asyncio
async def test_select_routing_subject_keeps_chatgpt_for_hard_session_affinity_when_sticky_target_is_healthy(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        del kwargs
        return True

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        ),
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
    )

    assert result.is_chatgpt is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedChatGPTSubject)
    assert selected.provider_kind == "chatgpt_web"


@pytest.mark.asyncio
async def test_select_routing_subject_does_not_forward_codex_session_affinity_to_platform(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )
    platform_selection_kwargs: dict[str, object] = {}

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family
        platform_selection_kwargs.update(kwargs)
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        ),
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
    )

    assert result.is_platform is True
    assert platform_selection_kwargs == {
        "sticky_key": None,
        "sticky_kind": None,
        "reallocate_sticky": False,
        "sticky_max_age_seconds": None,
    }


@pytest.mark.asyncio
async def test_select_routing_subject_keeps_backend_codex_continuity_on_chatgpt_when_sticky_target_selectable(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        del kwargs
        return False

    async def fake_sticky_chatgpt_selectable(**kwargs) -> bool:
        del kwargs
        return True

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_selectable_for_platform_fallback",
        fake_sticky_chatgpt_selectable,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_hint="x-codex-turn-state",
        ),
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
    )

    assert result.is_chatgpt is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedChatGPTSubject)
    assert selected.provider_kind == "chatgpt_web"


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_for_backend_codex_continuity_when_sticky_target_not_selectable(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )
    platform_selection_kwargs: dict[str, object] = {}

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family
        platform_selection_kwargs.update(kwargs)
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        del kwargs
        return False

    async def fake_sticky_chatgpt_selectable(**kwargs) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_selectable_for_platform_fallback",
        fake_sticky_chatgpt_selectable,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_hint="x-codex-turn-state",
        ),
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.routing_subject_id == "plat_1"
    assert platform_selection_kwargs == {
        "sticky_key": None,
        "sticky_kind": None,
        "reallocate_sticky": False,
        "sticky_max_age_seconds": None,
    }


@pytest.mark.asyncio
async def test_select_routing_subject_forwards_platform_prompt_cache_affinity_for_backend_codex_session(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )
    platform_selection_kwargs: dict[str, object] = {}

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family
        platform_selection_kwargs.update(kwargs)
        return identity

    async def fake_sticky_chatgpt_healthy(**kwargs) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(
        service,
        "sticky_chatgpt_target_is_healthy_for_platform_fallback",
        fake_sticky_chatgpt_healthy,
    )

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        ),
        sticky_key="session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        platform_sticky_key="cache-key",
        platform_sticky_kind=StickySessionKind.PROMPT_CACHE,
        platform_sticky_max_age_seconds=300,
    )

    assert result.is_platform is True
    assert platform_selection_kwargs == {
        "sticky_key": "cache-key",
        "sticky_kind": StickySessionKind.PROMPT_CACHE,
        "reallocate_sticky": False,
        "sticky_max_age_seconds": 300,
    }


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_for_models_when_public_http_usage_is_drained(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family="public_models_http",
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model=None,
        )
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert selected.routing_subject_id == "plat_1"


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_for_backend_codex_http_when_usage_is_drained(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del kwargs
        assert route_family == BACKEND_CODEX_HTTP_ROUTE_FAMILY
        return identity

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert selected.routing_subject_id == "plat_1"


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_when_only_active_chatgpt_accounts_are_model_incompatible(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())
    identity = proxy_service_module._SelectedPlatformIdentity(
        id="plat_1",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
        organization_id="org_test",
        project_id="proj_test",
    )

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_has_compatible_chatgpt_candidates(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> bool:
        del model, additional_limit_name, account_ids
        return False

    async def fail_should_fallback(**kwargs) -> bool:
        raise AssertionError(
            "provider selection must not evaluate pool drain when no compatible ChatGPT candidate exists"
        )

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return identity

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(service, "has_compatible_chatgpt_candidates", fake_has_compatible_chatgpt_candidates)
    monkeypatch.setattr(service, "should_fallback_to_platform_for_usage_drain", fail_should_fallback)
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert selected.routing_subject_id == "plat_1"


@pytest.mark.asyncio
async def test_select_routing_subject_falls_back_to_chatgpt_for_public_route_continuity(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_param="previous_response_id",
        )
    )

    assert result.is_chatgpt is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedChatGPTSubject)
    assert selected.provider_kind == "chatgpt_web"


@pytest.mark.asyncio
async def test_select_routing_subject_rejects_continuity_when_only_incompatible_chatgpt_and_platform_exists(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_has_compatible_chatgpt_candidates(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> bool:
        del model, additional_limit_name, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(service, "has_compatible_chatgpt_candidates", fake_has_compatible_chatgpt_candidates)
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_param="previous_response_id",
        )
    )

    assert result.failure is not None
    assert result.failure.error_code == "provider_continuity_unsupported"
    assert result.failure.error_param == "previous_response_id"


@pytest.mark.asyncio
async def test_select_routing_subject_rejects_websocket_when_only_incompatible_chatgpt_and_platform_exists(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_has_compatible_chatgpt_candidates(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> bool:
        del model, additional_limit_name, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(service, "has_compatible_chatgpt_candidates", fake_has_compatible_chatgpt_candidates)
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class="openai_public_ws",
            transport="websocket",
            model="gpt-5.1",
        )
    )

    assert result.failure is not None
    assert result.failure.error_code == "provider_transport_unsupported"
    assert result.failure.error_param == "transport"


@pytest.mark.asyncio
async def test_select_routing_subject_returns_continuity_compatibility_failure_without_platform(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_has_compatible_chatgpt_candidates(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> bool:
        del model, additional_limit_name, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return None

    async def fake_chatgpt_compatibility_failure(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> tuple[str | None, str | None]:
        del model, additional_limit_name, account_ids
        return "no_plan_support_for_model", "No accounts with a plan supporting model 'gpt-5.1'"

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(service, "has_compatible_chatgpt_candidates", fake_has_compatible_chatgpt_candidates)
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(service, "chatgpt_compatibility_failure", fake_chatgpt_compatibility_failure)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_param="previous_response_id",
        )
    )

    assert result.failure is not None
    assert result.failure.error_code == "no_plan_support_for_model"
    assert result.failure.error_message == "No accounts with a plan supporting model 'gpt-5.1'"


@pytest.mark.asyncio
async def test_select_routing_subject_returns_websocket_compatibility_failure_without_platform(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_has_compatible_chatgpt_candidates(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> bool:
        del model, additional_limit_name, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return None

    async def fake_chatgpt_compatibility_failure(
        model: str | None = None,
        *,
        additional_limit_name=None,
        account_ids=None,
    ) -> tuple[str | None, str | None]:
        del model, additional_limit_name, account_ids
        return "no_plan_support_for_model", "No accounts with a plan supporting model 'gpt-5.1'"

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(service, "has_compatible_chatgpt_candidates", fake_has_compatible_chatgpt_candidates)
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)
    monkeypatch.setattr(service, "chatgpt_compatibility_failure", fake_chatgpt_compatibility_failure)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class="openai_public_ws",
            transport="websocket",
            model="gpt-5.1",
        )
    )

    assert result.failure is not None
    assert result.failure.error_code == "no_plan_support_for_model"
    assert result.failure.error_message == "No accounts with a plan supporting model 'gpt-5.1'"


@pytest.mark.asyncio
async def test_select_routing_subject_uses_platform_for_backend_codex_session_headers_when_fallback_needed(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_should_fallback(*, model: str | None, account_ids=None) -> bool:
        del model, account_ids
        return True

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(
        service,
        "should_fallback_to_platform_for_usage_drain",
        _adapt_should_fallback(fake_should_fallback),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_param=None,
        )
    )

    assert result.is_platform is True
    selected = result.selected
    assert isinstance(selected, proxy_service_module.SelectedPlatformSubject)
    assert selected.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert selected.routing_subject_id == "plat_1"


@pytest.mark.asyncio
async def test_select_routing_subject_returns_provider_continuity_failure_when_only_platform(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
            continuity_param="previous_response_id",
        )
    )

    assert result.failure is not None
    assert result.failure.error_code == "provider_continuity_unsupported"
    assert result.failure.error_param == "previous_response_id"


@pytest.mark.asyncio
async def test_select_routing_subject_does_not_use_platform_when_only_platform_exists(monkeypatch) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del route_family, kwargs
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
            route_class=OPENAI_PUBLIC_HTTP_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.selected is None
    assert result.failure is not None
    assert result.failure.error_code == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_select_routing_subject_requires_chatgpt_for_backend_codex_http_when_only_platform_exists(
    monkeypatch,
) -> None:
    service = proxy_service_module.ProxyService(lambda: _repo_factory())

    async def fake_has_chatgpt_candidates(model: str | None = None, *, account_ids=None) -> bool:
        del model, account_ids
        return False

    async def fake_select_platform_identity(route_family: str, **kwargs):
        del kwargs
        assert route_family == BACKEND_CODEX_HTTP_ROUTE_FAMILY
        return proxy_service_module._SelectedPlatformIdentity(
            id="plat_1",
            api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
            organization_id=None,
            project_id=None,
        )

    monkeypatch.setattr(service, "has_chatgpt_candidates", fake_has_chatgpt_candidates)
    monkeypatch.setattr(
        service,
        "has_compatible_chatgpt_candidates",
        _adapt_compatible_candidate_checker(fake_has_chatgpt_candidates),
    )
    monkeypatch.setattr(service, "select_platform_identity", fake_select_platform_identity)

    result = await service.select_routing_subject(
        capabilities=proxy_service_module.RequestCapabilities(
            route_family=BACKEND_CODEX_HTTP_ROUTE_FAMILY,
            route_class=CHATGPT_PRIVATE_ROUTE_CLASS,
            transport="http",
            model="gpt-5.1",
        )
    )

    assert result.selected is None
    assert result.failure is not None
    assert result.failure.error_code == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_load_balancer_select_routing_subject_uses_platform_prompt_cache_sticky() -> None:
    sticky_repo = DummyStickyRepository(
        StickyRoutingTarget(
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id="plat_2",
            account_id=None,
        )
    )
    identities = [_platform_identity("plat_1"), _platform_identity("plat_2")]
    balancer = LoadBalancer(lambda: _repo_factory(identities=identities, sticky_repo=sticky_repo))

    result = await balancer.select_routing_subject(
        provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        sticky_key="cache-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=300,
    )

    assert result.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert result.routing_subject_id == "plat_2"
    assert sticky_repo.upsert_calls == [
        ("cache-key", StickySessionKind.PROMPT_CACHE, OPENAI_PLATFORM_PROVIDER_KIND, "plat_2")
    ]


@pytest.mark.asyncio
async def test_load_balancer_select_routing_subject_discards_stale_platform_sticky_target() -> None:
    sticky_repo = DummyStickyRepository(
        StickyRoutingTarget(
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id="plat_missing",
            account_id=None,
        )
    )
    balancer = LoadBalancer(lambda: _repo_factory(identities=[_platform_identity("plat_1")], sticky_repo=sticky_repo))

    result = await balancer.select_routing_subject(
        provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        sticky_key="cache-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
    )

    assert result.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
    assert result.routing_subject_id == "plat_1"
    assert sticky_repo.delete_calls == [("cache-key", StickySessionKind.PROMPT_CACHE, OPENAI_PLATFORM_PROVIDER_KIND)]


@pytest.mark.asyncio
async def test_platform_only_route_rejection_respects_api_key_account_scope() -> None:
    seen: dict[str, object] = {}

    class DummyService:
        async def select_platform_identity(self, route_family: str):
            seen["route_family"] = route_family
            return proxy_service_module._SelectedPlatformIdentity(
                id="plat_1",
                api_key_encrypted=TokenEncryptor().encrypt("sk-platform"),
                organization_id=None,
                project_id=None,
            )

        async def has_chatgpt_candidates(self, model: str | None, *, account_ids=None) -> bool:
            seen["model"] = model
            seen["account_ids"] = list(account_ids) if account_ids is not None else None
            return False

    api_key = SimpleNamespace(
        account_assignment_scope_enabled=True,
        assigned_account_ids=["acc_scoped"],
    )
    result = await proxy_api_module._should_reject_platform_only_route(
        context=cast(Any, SimpleNamespace(service=DummyService())),
        api_key=cast(Any, api_key),
        route_family=PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        model="gpt-5.1",
    )

    assert result is True
    assert seen == {
        "route_family": PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
        "model": "gpt-5.1",
        "account_ids": ["acc_scoped"],
    }
