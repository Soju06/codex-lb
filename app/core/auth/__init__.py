from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


DEFAULT_EMAIL = "unknown@example.com"
DEFAULT_PLAN = "unknown"


class AuthTokens(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id_token: str = Field(alias="idToken")
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    account_id: str | None = Field(default=None, alias="accountId")


class AuthFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    tokens: AuthTokens
    last_refresh_at: datetime | None = Field(default=None, alias="lastRefreshAt")


class OpenAIAuthClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chatgpt_account_id: str | None = None
    chatgpt_plan_type: str | None = None


class IdTokenClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    email: str | None = None
    chatgpt_account_id: str | None = None
    chatgpt_plan_type: str | None = None
    exp: int | float | str | None = None
    auth: OpenAIAuthClaims | None = Field(
        default=None,
        alias="https://api.openai.com/auth",
    )


@dataclass
class AccountClaims:
    account_id: str | None
    email: str | None
    plan_type: str | None


def parse_auth_json(raw: bytes) -> AuthFile:
    data = json.loads(raw)
    model = AuthFile.model_validate(data)
    return model


def extract_id_token_claims(id_token: str) -> IdTokenClaims:
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return IdTokenClaims()
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded)
        if not isinstance(data, dict):
            return IdTokenClaims()
        return IdTokenClaims.model_validate(data)
    except Exception:
        return IdTokenClaims()


def claims_from_auth(auth: AuthFile) -> AccountClaims:
    claims = extract_id_token_claims(auth.tokens.id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    return AccountClaims(
        account_id=auth.tokens.account_id or auth_claims.chatgpt_account_id or claims.chatgpt_account_id,
        email=claims.email,
        plan_type=auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type,
    )


def fallback_account_id(email: str | None) -> str:
    if email and email != DEFAULT_EMAIL:
        digest = hashlib.sha256(email.encode()).hexdigest()[:12]
        return f"email_{digest}"
    return f"local_{uuid4().hex[:12]}"
