from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    OpenAIAuthClaims,
    extract_id_token_claims,
    generate_unique_account_id,
)
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus


@dataclass(frozen=True, slots=True)
class PersistableTokenPayload:
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str | None = None
    last_refresh: datetime | None = None


def account_from_token_payload(
    payload: PersistableTokenPayload,
    *,
    encryptor: TokenEncryptor,
) -> Account:
    claims = extract_id_token_claims(payload.id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    raw_account_id = payload.account_id or auth_claims.chatgpt_account_id or claims.chatgpt_account_id
    email = claims.email or DEFAULT_EMAIL
    account_id = generate_unique_account_id(raw_account_id, email)
    plan_type = coerce_account_plan_type(
        auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type,
        DEFAULT_PLAN,
    )

    return Account(
        id=account_id,
        chatgpt_account_id=raw_account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt(payload.access_token),
        refresh_token_encrypted=encryptor.encrypt(payload.refresh_token),
        id_token_encrypted=encryptor.encrypt(payload.id_token),
        last_refresh=payload.last_refresh or utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
