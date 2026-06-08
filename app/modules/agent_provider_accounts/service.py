from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.core.crypto import TokenEncryptor
from app.db.models import AgentProviderAccount
from app.modules.agent_provider_accounts.repository import (
    AgentProviderAccountConflictError,
)

SUPPORTED_PROVIDER_IDS = {"codex", "gemini", "antigravity"}


class AgentProviderAccountValidationError(Exception):
    pass


class AgentProviderAccountDuplicateError(Exception):
    pass


class AgentProviderAccountNotFoundError(Exception):
    pass


class AgentProviderAccountsRepositoryPort(Protocol):
    async def list_by_provider(self, provider_id: str) -> list[AgentProviderAccount]: ...

    async def get_for_provider(self, provider_id: str, account_id: str) -> AgentProviderAccount | None: ...

    async def create(self, row: AgentProviderAccount) -> AgentProviderAccount: ...

    async def save(self, row: AgentProviderAccount) -> AgentProviderAccount: ...


class TokenEncryptorPort(Protocol):
    def encrypt(self, token: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class GeminiProviderAccountCreateData:
    display_name: str
    api_key: str
    external_account_id: str | None = None
    project_id: str | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class AntigravityProviderAccountCreateData:
    display_name: str
    external_account_id: str | None = None
    auth_mode: str | None = None
    api_key: str | None = None
    project_id: str | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class AgentProviderAccountUpdateData:
    display_name: str | None = None
    status: str | None = None
    api_key: str | None = None
    external_account_id: str | None = None
    project_id: str | None = None
    project_id_set: bool = False
    location: str | None = None
    location_set: bool = False


class AgentProviderAccountsService:
    def __init__(
        self,
        repository: AgentProviderAccountsRepositoryPort,
        *,
        encryptor: TokenEncryptorPort | None = None,
    ) -> None:
        self._repository = repository
        self._encryptor = encryptor or TokenEncryptor()

    async def list_accounts(self, provider_id: str) -> list[AgentProviderAccount]:
        provider = _normalize_provider_id(provider_id)
        return await self._repository.list_by_provider(provider)

    async def create_gemini_account(self, payload: GeminiProviderAccountCreateData) -> AgentProviderAccount:
        display_name = payload.display_name.strip()
        api_key = payload.api_key.strip()
        if not display_name:
            raise AgentProviderAccountValidationError("display_name is required")
        if not api_key:
            raise AgentProviderAccountValidationError("api_key is required")
        row = AgentProviderAccount(
            provider_id="gemini",
            external_account_id=_clean_optional(payload.external_account_id),
            display_name=display_name,
            status="active",
            auth_mode="api_key",
            api_key_encrypted=self._encryptor.encrypt(api_key),
            credential_fingerprint=_credential_fingerprint(api_key),
            project_id=_clean_optional(payload.project_id),
            location=_clean_optional(payload.location),
        )
        try:
            return await self._repository.create(row)
        except AgentProviderAccountConflictError as exc:
            raise AgentProviderAccountDuplicateError("Gemini provider account already exists") from exc

    async def create_antigravity_account(
        self,
        payload: AntigravityProviderAccountCreateData,
    ) -> AgentProviderAccount:
        display_name = payload.display_name.strip()
        auth_mode = _normalize_antigravity_auth_mode(payload.auth_mode, payload.api_key)
        if not display_name:
            raise AgentProviderAccountValidationError("display_name is required")
        external_account_id = _clean_optional(payload.external_account_id)
        api_key = _clean_optional(payload.api_key)
        if auth_mode == "cli_keyring" and api_key is not None:
            raise AgentProviderAccountValidationError("api_key is not allowed for cli_keyring accounts")
        if auth_mode == "cli_keyring" and external_account_id is None:
            raise AgentProviderAccountValidationError("external_account_id is required")
        if auth_mode == "api_key" and api_key is None:
            raise AgentProviderAccountValidationError("api_key is required")
        row = AgentProviderAccount(
            provider_id="antigravity",
            external_account_id=external_account_id,
            display_name=display_name,
            status="active",
            auth_mode=auth_mode,
            api_key_encrypted=None if api_key is None else self._encryptor.encrypt(api_key),
            credential_fingerprint=(
                _credential_fingerprint(api_key)
                if api_key is not None
                else _credential_fingerprint(f"antigravity:{external_account_id}")
            ),
            project_id=_clean_optional(payload.project_id),
            location=_clean_optional(payload.location),
        )
        try:
            return await self._repository.create(row)
        except AgentProviderAccountConflictError as exc:
            raise AgentProviderAccountDuplicateError("Antigravity provider account already exists") from exc

    async def update_account(
        self,
        provider_id: str,
        account_id: str,
        payload: AgentProviderAccountUpdateData,
    ) -> AgentProviderAccount:
        provider = _normalize_provider_id(provider_id)
        row = await self._repository.get_for_provider(provider, account_id)
        if row is None:
            raise AgentProviderAccountNotFoundError("provider account not found")
        if payload.display_name is not None:
            row.display_name = _required_text(payload.display_name, "display_name")
        if payload.status is not None:
            row.status = _normalize_status(payload.status)
        if payload.project_id is not None or payload.project_id_set:
            row.project_id = _clean_optional(payload.project_id)
        if payload.location is not None or payload.location_set:
            row.location = _clean_optional(payload.location)
        if payload.external_account_id is not None:
            row.external_account_id = _clean_optional(payload.external_account_id)
            if provider == "antigravity" and row.auth_mode == "cli_keyring" and row.external_account_id is None:
                raise AgentProviderAccountValidationError("external_account_id is required")
            if provider == "antigravity" and row.auth_mode == "cli_keyring" and row.external_account_id is not None:
                row.credential_fingerprint = _credential_fingerprint(f"antigravity:{row.external_account_id}")
        if payload.api_key is not None:
            if provider == "antigravity" and row.auth_mode != "api_key":
                raise AgentProviderAccountValidationError(
                    "api_key can only be updated for Antigravity API-key accounts"
                )
            if provider not in {"gemini", "antigravity"}:
                raise AgentProviderAccountValidationError("api_key can only be updated for API-key accounts")
            api_key = _required_text(payload.api_key, "api_key")
            row.api_key_encrypted = self._encryptor.encrypt(api_key)
            row.credential_fingerprint = _credential_fingerprint(api_key)
        try:
            return await self._repository.save(row)
        except AgentProviderAccountConflictError as exc:
            raise AgentProviderAccountDuplicateError("Provider account already exists") from exc


def _normalize_provider_id(provider_id: str) -> str:
    normalized = provider_id.strip().lower()
    if normalized not in SUPPORTED_PROVIDER_IDS:
        raise AgentProviderAccountValidationError(f"Unsupported provider '{provider_id}'")
    return normalized


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _required_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AgentProviderAccountValidationError(f"{field_name} is required")
    return cleaned


def _normalize_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"active", "paused"}:
        raise AgentProviderAccountValidationError("status must be active or paused")
    return normalized


def _normalize_antigravity_auth_mode(auth_mode: str | None, api_key: str | None) -> str:
    if auth_mode is None:
        return "api_key" if _clean_optional(api_key) is not None else "cli_keyring"
    normalized = auth_mode.strip().lower()
    if normalized not in {"api_key", "cli_keyring"}:
        raise AgentProviderAccountValidationError("auth_mode must be api_key or cli_keyring")
    return normalized


def _credential_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
