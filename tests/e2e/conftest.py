from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Mapping, Sequence

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.main import create_app

DEFAULT_DASHBOARD_PASSWORD = "password123"
DEFAULT_MODEL = "gpt-5.2"


def _encode_jwt(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict[str, object]:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


async def _maybe_await(result: object) -> None:
    if inspect.isawaitable(result):
        await result


@pytest_asyncio.fixture
async def e2e_client(db_setup, monkeypatch):
    import app.main as main_module

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest_asyncio.fixture
async def client(e2e_client):
    yield e2e_client


@pytest.fixture
def dashboard_password() -> str:
    return DEFAULT_DASHBOARD_PASSWORD


@pytest.fixture
def setup_dashboard_password(dashboard_password: str):
    async def _setup(client: AsyncClient, *, password: str = dashboard_password) -> str:
        response = await client.post(
            "/api/dashboard-auth/password/setup",
            json={"password": password},
        )
        assert response.status_code == 200
        assert response.json()["passwordRequired"] is True
        return password

    return _setup


@pytest.fixture
def login_dashboard():
    async def _login(client: AsyncClient, *, password: str) -> dict[str, object]:
        response = await client.post(
            "/api/dashboard-auth/password/login",
            json={"password": password},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is True
        return payload

    return _login


@pytest.fixture
def enable_api_key_auth():
    async def _enable(client: AsyncClient) -> dict[str, object]:
        response = await client.put(
            "/api/settings",
            json={
                "stickyThreadsEnabled": False,
                "preferEarlierResetAccounts": False,
                "totpRequiredOnLogin": False,
                "apiKeyAuthEnabled": True,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiKeyAuthEnabled"] is True
        return payload

    return _enable


@pytest.fixture
def create_api_key():
    async def _create(
        client: AsyncClient,
        *,
        name: str,
        allowed_models: Sequence[str] | None = None,
        limits: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"name": name}
        if allowed_models is not None:
            payload["allowedModels"] = list(allowed_models)
        if limits is not None:
            payload["limits"] = limits

        response = await client.post("/api/api-keys/", json=payload)
        assert response.status_code == 200
        created = response.json()
        assert created["key"].startswith("sk-clb-")
        return created

    return _create


@pytest.fixture
def import_test_account():
    async def _import(client: AsyncClient, *, account_id: str, email: str) -> None:
        files = {
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(account_id, email)),
                "application/json",
            )
        }
        response = await client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    return _import


@pytest.fixture
def populate_test_registry():
    async def _populate(models: Sequence[str] | None = None) -> list[str]:
        model_ids = list(models or [DEFAULT_MODEL])
        registry = get_model_registry()
        snapshot = {
            "plus": [_make_upstream_model(slug) for slug in model_ids],
            "pro": [_make_upstream_model(slug) for slug in model_ids],
        }
        await _maybe_await(registry.update(snapshot))
        return model_ids

    return _populate
