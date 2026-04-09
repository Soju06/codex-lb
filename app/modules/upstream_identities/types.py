from __future__ import annotations

from typing import Literal, TypeAlias

CHATGPT_WEB_PROVIDER_KIND = "chatgpt_web"
OPENAI_PLATFORM_PROVIDER_KIND = "openai_platform"

PUBLIC_MODELS_HTTP_ROUTE_FAMILY = "public_models_http"
PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY = "public_responses_http"
BACKEND_CODEX_HTTP_ROUTE_FAMILY = "backend_codex_http"

OPENAI_PUBLIC_HTTP_ROUTE_CLASS = "openai_public_http"
OPENAI_PUBLIC_WS_ROUTE_CLASS = "openai_public_ws"
CHATGPT_PRIVATE_ROUTE_CLASS = "chatgpt_private"

ProviderKind: TypeAlias = Literal["chatgpt_web", "openai_platform"]
PlatformRouteFamily: TypeAlias = Literal["public_models_http", "public_responses_http", "backend_codex_http"]

PHASE1_PLATFORM_ROUTE_FAMILIES: tuple[PlatformRouteFamily, ...] = (
    PUBLIC_MODELS_HTTP_ROUTE_FAMILY,
    PUBLIC_RESPONSES_HTTP_ROUTE_FAMILY,
    BACKEND_CODEX_HTTP_ROUTE_FAMILY,
)

SUPPORTED_PROVIDER_KINDS: tuple[ProviderKind, ...] = (
    CHATGPT_WEB_PROVIDER_KIND,
    OPENAI_PLATFORM_PROVIDER_KIND,
)
