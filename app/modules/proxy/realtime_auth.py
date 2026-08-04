from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Literal

from starlette.requests import HTTPConnection

from app.core.auth import clean_account_identity_part
from app.core.auth.dependencies import (
    validate_proxy_api_key_authorization,
    validate_required_proxy_api_key_authorization,
)
from app.core.crypto import get_or_create_key
from app.core.exceptions import ProxyAuthError
from app.db.session import get_background_session
from app.modules.api_keys.service import ApiKeyData

RealtimeCallerKind = Literal["api_key", "oauth"]
ApiKeyValidator = Callable[[str | None], Awaitable[ApiKeyData]]
KeylessOriginValidator = Callable[[HTTPConnection], Awaitable[None]]
AffinityMaterialBuilder = Callable[[str, str], str]

_OAUTH_LIVE_AFFINITY_DOMAIN = b"codex-lb/oauth-live-affinity/v1"


class OAuthLiveNotEnabledError(ProxyAuthError):
    status_code = 403
    code = "oauth_live_not_enabled"
    error_type = "permission_error"
    message = "OAuth Live Voice is not enabled"


@dataclass(frozen=True, slots=True)
class RealtimeCallerScope:
    kind: RealtimeCallerKind
    affinity_scope_material: str
    api_key: ApiKeyData | None
    allowed_account_ids: frozenset[str] | None

    @classmethod
    def for_api_key(cls, api_key: ApiKeyData) -> RealtimeCallerScope:
        return cls(
            kind="api_key",
            affinity_scope_material=api_key.id,
            api_key=api_key,
            allowed_account_ids=None,
        )

    @classmethod
    def for_oauth(
        cls,
        *,
        affinity_scope_material: str,
        allowed_account_ids: Collection[str],
    ) -> RealtimeCallerScope:
        allowed = frozenset(allowed_account_ids)
        if not allowed:
            raise OAuthLiveNotEnabledError()
        return cls(
            kind="oauth",
            affinity_scope_material=affinity_scope_material,
            api_key=None,
            allowed_account_ids=allowed,
        )

    def allows_account(self, account_id: str) -> bool:
        if self.allowed_account_ids is not None:
            return account_id in self.allowed_account_ids
        api_key = self.api_key
        return bool(
            api_key is not None
            and (not api_key.account_assignment_scope_enabled or account_id in api_key.assigned_account_ids)
        )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, value = authorization.strip().partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


async def _active_oauth_live_allowed_account_ids() -> frozenset[str]:
    # Imported lazily so the proxy contract stays acyclic while the policy
    # module owns its ORM and migration lifecycle.
    from app.modules.oauth_live.repository import OAuthLivePolicyRepository

    async with get_background_session() as session:
        return await OAuthLivePolicyRepository(session).get_active_allowed_account_ids()


async def _validate_keyless_origin(connection: HTTPConnection) -> None:
    # Reuse the ordinary proxy's zero-key admission contract. Passing no bearer
    # makes global API-key mode fail closed; disabled mode still enforces the
    # existing loopback/proxy-chain/raw-socket allowlist checks.
    await validate_proxy_api_key_authorization(None, request=connection)


def _oauth_live_affinity_scope_material(access_token: str, chatgpt_account_id: str) -> str:
    # Derive a purpose-specific HMAC key from the existing persistent encryption
    # key. Replicas that can decrypt the same account store therefore derive the
    # same affinity material without adding another secret or setting.
    root_key = get_or_create_key()
    affinity_key = hmac.digest(root_key, _OAUTH_LIVE_AFFINITY_DOMAIN, hashlib.sha256)
    credential_pair = f"{access_token}\0{chatgpt_account_id}".encode()
    digest = hmac.new(affinity_key, credential_pair, hashlib.sha256).hexdigest()
    return f"oauth-local:{digest}"


async def resolve_realtime_caller_scope(
    connection: HTTPConnection,
    authorization: str | None,
    chatgpt_account_id: str | None,
    *,
    api_key_validator: ApiKeyValidator = validate_required_proxy_api_key_authorization,
    keyless_origin_validator: KeylessOriginValidator = _validate_keyless_origin,
    affinity_material_builder: AffinityMaterialBuilder = _oauth_live_affinity_scope_material,
) -> RealtimeCallerScope:
    token = _extract_bearer_token(authorization)
    if token is not None and token.startswith("sk-clb-"):
        return RealtimeCallerScope.for_api_key(await api_key_validator(authorization))

    await keyless_origin_validator(connection)
    if token is None:
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")

    normalized_account_id = clean_account_identity_part(chatgpt_account_id)
    if normalized_account_id is None:
        raise ProxyAuthError("Missing chatgpt-account-id header")

    allowed_account_ids = await _active_oauth_live_allowed_account_ids()
    return RealtimeCallerScope.for_oauth(
        affinity_scope_material=affinity_material_builder(token, normalized_account_id),
        allowed_account_ids=allowed_account_ids,
    )
