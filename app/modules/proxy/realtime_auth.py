from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Literal

from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
from app.core.exceptions import ProxyAuthError
from app.db.session import get_background_session
from app.modules.api_keys.service import ApiKeyData

RealtimeCallerKind = Literal["api_key", "oauth"]
ApiKeyValidator = Callable[[str | None], Awaitable[ApiKeyData]]


class OAuthLiveNotEnabledError(ProxyAuthError):
    status_code = 403
    code = "oauth_live_not_enabled"
    error_type = "permission_error"
    message = "OAuth Live Voice is not enabled for this account"


@dataclass(frozen=True, slots=True)
class RealtimeCallerScope:
    kind: RealtimeCallerKind
    affinity_scope_material: str
    api_key: ApiKeyData | None
    caller_account_id: str | None
    allowed_account_ids: frozenset[str] | None

    @classmethod
    def for_api_key(cls, api_key: ApiKeyData) -> RealtimeCallerScope:
        return cls(
            kind="api_key",
            affinity_scope_material=api_key.id,
            api_key=api_key,
            caller_account_id=None,
            allowed_account_ids=None,
        )

    @classmethod
    def for_oauth(
        cls,
        *,
        caller_account_id: str,
        allowed_account_ids: Collection[str],
    ) -> RealtimeCallerScope:
        allowed = frozenset(allowed_account_ids)
        if not allowed:
            raise OAuthLiveNotEnabledError()
        return cls(
            kind="oauth",
            affinity_scope_material=f"oauth:{caller_account_id}",
            api_key=None,
            caller_account_id=caller_account_id,
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


async def _active_oauth_live_allowed_account_ids(caller_account_id: str) -> frozenset[str]:
    # Imported lazily so the proxy contract stays acyclic while the policy
    # module owns its ORM and migration lifecycle.
    from app.modules.oauth_live.repository import OAuthLivePolicyRepository

    async with get_background_session() as session:
        return await OAuthLivePolicyRepository(session).get_active_allowed_account_ids(caller_account_id)


async def resolve_realtime_caller_scope(
    authorization: str | None,
    chatgpt_account_id: str | None,
    *,
    api_key_validator: ApiKeyValidator = validate_required_proxy_api_key_authorization,
) -> RealtimeCallerScope:
    token = _extract_bearer_token(authorization)
    if token is not None and token.startswith("sk-clb-"):
        return RealtimeCallerScope.for_api_key(await api_key_validator(authorization))

    from app.core.auth.codex_oauth_identity import resolve_verified_codex_oauth_identity

    identity = await resolve_verified_codex_oauth_identity(authorization, chatgpt_account_id)
    allowed_account_ids = await _active_oauth_live_allowed_account_ids(identity.caller_account_id)
    return RealtimeCallerScope.for_oauth(
        caller_account_id=identity.caller_account_id,
        allowed_account_ids=allowed_account_ids,
    )
