from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from app.core.anthropic import model_registry, models, parsing
from app.core.anthropic import pricing as anthropic_pricing
from app.core.auth.refresh import TokenRefreshResult
from app.core.providers.types import AccountMetadata, ProviderOAuthConfig

OPENROUTER_PROVIDER_NAME = "openrouter"
OPENROUTER_DEFAULT_PLAN = "openrouter"
OPENROUTER_UPSTREAM_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
# Live POST /api/v1/messages/count_tokens returned 404. Do not relay it.
SUPPORTS_UPSTREAM_COUNT_TOKENS = False


@dataclass(frozen=True, slots=True)
class OpenRouterProvider:
    name: str = OPENROUTER_PROVIDER_NAME
    requires_id_token: bool = False
    supports_upstream_count_tokens: bool = SUPPORTS_UPSTREAM_COUNT_TOKENS
    model_registry: object = model_registry
    pricing: object = anthropic_pricing
    sse_parser: object = parsing
    request_normalizer: object = models
    access_token_refresh_interval_seconds: int | None = None

    def oauth_config(self) -> ProviderOAuthConfig:
        raise NotImplementedError("OpenRouter accounts are imported with API keys, not OAuth")

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenRefreshResult:
        del session
        return TokenRefreshResult(
            access_token=refresh_token,
            refresh_token=refresh_token,
            id_token=None,
            account_id=None,
            plan_type=OPENROUTER_DEFAULT_PLAN,
            email=None,
        )

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata:
        del id_token
        return AccountMetadata(account_id=None, email=None, plan_type=OPENROUTER_DEFAULT_PLAN)


def register_openrouter_provider() -> None:
    from app.core.providers import registry as provider_registry

    provider_registry._PROVIDERS[OPENROUTER_PROVIDER_NAME] = OpenRouterProvider()


register_openrouter_provider()
