from __future__ import annotations

import pytest

from app.modules.agent_provider_accounts.service import (
    AgentProviderAccountNotFoundError,
    AgentProviderAccountsService,
    AgentProviderAccountUpdateData,
    AgentProviderAccountValidationError,
    AntigravityProviderAccountCreateData,
    GeminiProviderAccountCreateData,
)


class _RepositoryStub:
    def __init__(self) -> None:
        self.created = []
        self.rows = {}
        self.saved = []

    async def list_by_provider(self, provider_id: str):
        return []

    async def get_for_provider(self, provider_id: str, account_id: str):
        row = self.rows.get(account_id)
        if row is None or row.provider_id != provider_id:
            return None
        return row

    async def create(self, row):
        self.created.append(row)
        self.rows[row.id] = row
        return row

    async def save(self, row):
        self.saved.append(row)
        self.rows[row.id] = row
        return row


class _EncryptorStub:
    def encrypt(self, token: str) -> bytes:
        return f"encrypted:{token}".encode("utf-8")


@pytest.mark.asyncio
async def test_create_gemini_account_encrypts_api_key_and_fingerprints_secret() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())

    row = await service.create_gemini_account(
        GeminiProviderAccountCreateData(display_name=" Gemini ", api_key=" secret ", project_id=" project ")
    )

    assert row.provider_id == "gemini"
    assert row.display_name == "Gemini"
    assert row.api_key_encrypted == b"encrypted:secret"
    assert row.credential_fingerprint is not None
    assert row.project_id == "project"
    assert repository.created == [row]


@pytest.mark.asyncio
async def test_create_antigravity_account_stores_cli_profile_without_secret() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())

    row = await service.create_antigravity_account(
        AntigravityProviderAccountCreateData(
            display_name=" Antigravity ",
            external_account_id=" default ",
            location=" agy ",
        )
    )

    assert row.provider_id == "antigravity"
    assert row.display_name == "Antigravity"
    assert row.external_account_id == "default"
    assert row.auth_mode == "cli_keyring"
    assert row.status == "active"
    assert row.api_key_encrypted is None
    assert row.credential_fingerprint is not None
    assert row.location == "agy"
    assert repository.created == [row]


@pytest.mark.asyncio
async def test_create_antigravity_cli_account_rejects_api_key() -> None:
    service = AgentProviderAccountsService(_RepositoryStub(), encryptor=_EncryptorStub())

    with pytest.raises(AgentProviderAccountValidationError, match="api_key is not allowed"):
        await service.create_antigravity_account(
            AntigravityProviderAccountCreateData(
                display_name="Antigravity local",
                auth_mode="cli_keyring",
                external_account_id="default",
                api_key="should-not-store",
            )
        )


@pytest.mark.asyncio
async def test_create_antigravity_api_key_account_encrypts_secret() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())

    row = await service.create_antigravity_account(
        AntigravityProviderAccountCreateData(
            display_name=" Antigravity managed ",
            auth_mode="api_key",
            api_key=" ag-key ",
            project_id=" project ",
        )
    )

    assert row.provider_id == "antigravity"
    assert row.display_name == "Antigravity managed"
    assert row.auth_mode == "api_key"
    assert row.api_key_encrypted == b"encrypted:ag-key"
    assert row.credential_fingerprint is not None
    assert row.external_account_id is None
    assert row.project_id == "project"


@pytest.mark.asyncio
async def test_list_accounts_rejects_unknown_provider() -> None:
    service = AgentProviderAccountsService(_RepositoryStub(), encryptor=_EncryptorStub())

    with pytest.raises(AgentProviderAccountValidationError):
        await service.list_accounts("unknown")


@pytest.mark.asyncio
async def test_update_gemini_account_rotates_key_and_pauses_account() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())
    row = await service.create_gemini_account(
        GeminiProviderAccountCreateData(display_name="Old", api_key="old-key", project_id="old-project")
    )
    old_fingerprint = row.credential_fingerprint

    updated = await service.update_account(
        "gemini",
        row.id,
        AgentProviderAccountUpdateData(
            display_name=" New name ",
            status=" paused ",
            api_key=" new-key ",
            project_id=" new-project ",
            location=" global ",
        ),
    )

    assert updated is row
    assert row.display_name == "New name"
    assert row.status == "paused"
    assert row.api_key_encrypted == b"encrypted:new-key"
    assert row.credential_fingerprint != old_fingerprint
    assert row.project_id == "new-project"
    assert row.location == "global"
    assert repository.saved == [row]


@pytest.mark.asyncio
async def test_update_antigravity_cli_account_changes_external_id_fingerprint_and_rejects_blank() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())
    row = await service.create_antigravity_account(
        AntigravityProviderAccountCreateData(display_name="Agy", external_account_id="default")
    )
    old_fingerprint = row.credential_fingerprint

    updated = await service.update_account(
        "antigravity",
        row.id,
        AgentProviderAccountUpdateData(external_account_id=" workspace-b ", status="active", location=" harness "),
    )

    assert updated.external_account_id == "workspace-b"
    assert updated.location == "harness"
    assert updated.credential_fingerprint != old_fingerprint

    with pytest.raises(AgentProviderAccountValidationError):
        await service.update_account("antigravity", row.id, AgentProviderAccountUpdateData(external_account_id=" "))


@pytest.mark.asyncio
async def test_update_account_rejects_wrong_provider_or_missing_account() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())

    with pytest.raises(AgentProviderAccountValidationError):
        await service.update_account("unknown", "missing", AgentProviderAccountUpdateData(status="active"))

    with pytest.raises(AgentProviderAccountNotFoundError):
        await service.update_account("gemini", "missing", AgentProviderAccountUpdateData(status="active"))


@pytest.mark.asyncio
async def test_update_antigravity_account_rejects_api_key_rotation() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())
    row = await service.create_antigravity_account(
        AntigravityProviderAccountCreateData(display_name="Agy", external_account_id="default")
    )

    with pytest.raises(AgentProviderAccountValidationError):
        await service.update_account("antigravity", row.id, AgentProviderAccountUpdateData(api_key="secret"))


@pytest.mark.asyncio
async def test_update_antigravity_api_key_account_rotates_key() -> None:
    repository = _RepositoryStub()
    service = AgentProviderAccountsService(repository, encryptor=_EncryptorStub())
    row = await service.create_antigravity_account(
        AntigravityProviderAccountCreateData(display_name="Agy API", auth_mode="api_key", api_key="old")
    )
    old_fingerprint = row.credential_fingerprint

    updated = await service.update_account("antigravity", row.id, AgentProviderAccountUpdateData(api_key="new"))

    assert updated.api_key_encrypted == b"encrypted:new"
    assert updated.credential_fingerprint != old_fingerprint
