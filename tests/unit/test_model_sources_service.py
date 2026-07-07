from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import ModelSource
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.model_sources.schemas import ModelSourceCreateRequest, ModelSourceModelInput, ModelSourceUpdateRequest
from app.modules.model_sources.service import ModelSourcesService, ModelSourceValidationError


class _FakeEncryptor:
    def encrypt(self, token: str) -> bytes:
        return f"encrypted:{token}".encode()


class _FakeRepository:
    def __init__(self) -> None:
        self.created: ModelSource | None = None
        self.committed = False
        self.rolled_back = False

    async def create(self, row: ModelSource, *, commit: bool = True) -> ModelSource:
        _stamp_source(row)
        self.created = row
        return row

    async def get_by_id(self, source_id: str) -> ModelSource | None:
        if self.created is None or self.created.id != source_id:
            return None
        return self.created

    async def replace_models(self, source: ModelSource, models, *, commit: bool = True) -> None:
        source.models = models
        _stamp_source(source)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _stamp_source(row: ModelSource) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    row.created_at = row.created_at or now
    row.updated_at = now
    for index, model in enumerate(row.models, start=1):
        model.id = model.id or index
        model.source_id = row.id
        model.created_at = model.created_at or now
        model.updated_at = now


def _as_repository(repository: _FakeRepository) -> ModelSourcesRepository:
    return cast(ModelSourcesRepository, repository)


def _fake_encryptor() -> TokenEncryptor:
    return cast(TokenEncryptor, _FakeEncryptor())


@pytest.mark.asyncio
async def test_create_source_validates_absolute_http_url() -> None:
    service = ModelSourcesService(_as_repository(_FakeRepository()), encryptor=_fake_encryptor())

    with pytest.raises(ModelSourceValidationError, match="absolute HTTP"):
        await service.create_source(
            ModelSourceCreateRequest(
                name="Local",
                base_url="localhost:8000/v1",
                models=[ModelSourceModelInput(model="local-coder")],
            )
        )


@pytest.mark.asyncio
async def test_create_source_rejects_duplicate_models() -> None:
    service = ModelSourcesService(_as_repository(_FakeRepository()), encryptor=_fake_encryptor())

    with pytest.raises(ModelSourceValidationError, match="Duplicate"):
        await service.create_source(
            ModelSourceCreateRequest(
                name="Local",
                base_url="http://localhost:8000/v1",
                models=[
                    ModelSourceModelInput(model="local-coder"),
                    ModelSourceModelInput(model="local-coder"),
                ],
            )
        )


@pytest.mark.asyncio
async def test_create_source_encrypts_api_key_without_account_row() -> None:
    repo = _FakeRepository()
    service = ModelSourcesService(_as_repository(repo), encryptor=_fake_encryptor())

    created = await service.create_source(
        ModelSourceCreateRequest(
            name="Local",
            base_url="http://localhost:8000/v1/",
            api_key="secret",
            models=[ModelSourceModelInput(model="local-coder")],
        )
    )

    assert repo.created is not None
    assert repo.created.api_key_encrypted == b"encrypted:secret"
    assert repo.created.base_url == "http://localhost:8000/v1"
    assert repo.created.supports_audio_transcriptions is False
    assert repo.created.models[0].model == "local-coder"
    assert created.id.startswith("src_")


@pytest.mark.asyncio
async def test_create_source_persists_audio_transcription_capability() -> None:
    repo = _FakeRepository()
    service = ModelSourcesService(_as_repository(repo), encryptor=_fake_encryptor())

    created = await service.create_source(
        ModelSourceCreateRequest(
            name="ASR",
            base_url="http://localhost:8000/v1/",
            supports_audio_transcriptions=True,
            models=[ModelSourceModelInput(model="whisper-large-v3")],
        )
    )

    assert repo.created is not None
    assert repo.created.supports_audio_transcriptions is True
    assert created.supports_audio_transcriptions is True


@pytest.mark.asyncio
async def test_update_source_clears_nullable_fields_when_null_is_sent() -> None:
    repo = _FakeRepository()
    service = ModelSourcesService(_as_repository(repo), encryptor=_fake_encryptor())
    created = await service.create_source(
        ModelSourceCreateRequest(
            name="Local",
            base_url="http://localhost:8000/v1/",
            api_key="secret",
            timeout_seconds=30,
            max_concurrency=2,
            models=[ModelSourceModelInput(model="local-coder")],
        )
    )

    updated = await service.update_source(
        created.id,
        ModelSourceUpdateRequest(api_key=None, timeout_seconds=None, max_concurrency=None),
    )

    assert repo.created is not None
    assert repo.created.api_key_encrypted is None
    assert repo.created.timeout_seconds is None
    assert repo.created.max_concurrency is None
    assert updated.timeout_seconds is None
    assert updated.max_concurrency is None
