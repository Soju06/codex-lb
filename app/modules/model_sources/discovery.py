"""Bounded, request-driven CPA acquisition with durable last-good catalogs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

import aiohttp
from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import ModelSource, ModelSourceModel
from app.db.session import get_background_session
from app.modules.model_sources.discovery_catalog import CpaCatalog

logger = logging.getLogger(__name__)
_REFRESH_SECONDS = 60
_TIMEOUT_SECONDS = 5
_MAX_CATALOG_BYTES = 2 * 1024 * 1024


async def refresh_cpa_catalogs() -> None:
    # Include enumeration in the budget; the subsequent catalog read owns an
    # independent session and can still succeed after refresh storage fails.
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with get_background_session() as session:
                result = await session.execute(
                    select(ModelSource.id).where(
                        ModelSource.catalog_mode == "cli_proxy_api", ModelSource.is_enabled.is_(True)
                    )
                )
                source_ids = list(result.scalars())
            pending = iter(source_ids)

            async def refresh_worker() -> None:
                for source_id in pending:
                    await _refresh_source_safely(source_id)

            async with asyncio.TaskGroup() as group:
                for _ in range(min(4, len(source_ids))):
                    group.create_task(refresh_worker())
    except TimeoutError:
        logger.warning("CPA catalog refresh budget exhausted; serving stored catalog")
    except Exception:
        logger.warning("CPA catalog refresh failed; serving stored catalog")


async def _refresh_source_safely(source_id: str) -> None:
    try:
        await _refresh_source(source_id)
    except Exception:
        # Cancellation still propagates so the request owns all acquisition work.
        logger.warning("CPA catalog refresh failed source=%s", source_id)


async def _refresh_source(source_id: str) -> None:
    token = uuid.uuid4().hex
    now = utcnow()
    async with get_background_session() as session:
        claimed = await session.execute(
            update(ModelSource)
            .where(
                ModelSource.id == source_id,
                ModelSource.catalog_mode == "cli_proxy_api",
                ModelSource.is_enabled.is_(True),
                or_(ModelSource.catalog_next_refresh_at.is_(None), ModelSource.catalog_next_refresh_at <= now),
            )
            .values(catalog_refresh_token=token, catalog_next_refresh_at=now + timedelta(seconds=_REFRESH_SECONDS))
            .returning(ModelSource.base_url, ModelSource.api_key_encrypted)
        )
        config = claimed.one_or_none()
        await session.commit()
    if config is None:
        return
    headers = {}
    try:
        if config.api_key_encrypted is not None:
            headers["Authorization"] = f"Bearer {TokenEncryptor().decrypt(config.api_key_encrypted)}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)) as client:
            async with client.get(
                f"{config.base_url}/models",
                params={"client_version": "0.144.0"},
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    logger.warning("CPA catalog refresh rejected source=%s status=%s", source_id, response.status)
                    return
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > _MAX_CATALOG_BYTES:
                        raise ValueError("CPA catalog exceeds size limit")
                catalog = CpaCatalog.model_validate_json(body)
    except (aiohttp.ClientError, TimeoutError, ValidationError, ValueError, InvalidToken):
        # Never log request credentials or upstream body text.
        logger.warning("CPA catalog refresh failed source=%s", source_id)
        return
    await _apply_catalog(source_id, token, catalog)


async def _apply_catalog(source_id: str, token: str, catalog: CpaCatalog) -> None:
    async with get_background_session() as session:
        # The conditional write takes the source lock before loading its children.
        # Config edits invalidate the token; deleted sources cannot be resurrected.
        fenced = await session.execute(
            update(ModelSource)
            .where(
                ModelSource.id == source_id,
                ModelSource.catalog_mode == "cli_proxy_api",
                ModelSource.is_enabled.is_(True),
                ModelSource.catalog_refresh_token == token,
            )
            .values(catalog_refresh_token=None)
            .returning(ModelSource.id)
        )
        if fenced.scalar_one_or_none() is None:
            await session.rollback()
            return
        source = (
            await session.execute(
                select(ModelSource).where(ModelSource.id == source_id).options(selectinload(ModelSource.models))
            )
        ).scalar_one()
        rows = {row.model: row for row in source.models}
        for row in rows.values():
            row.is_enabled = False
        for model in catalog.models:
            row = rows.get(model.slug)
            if row is None:
                row = ModelSourceModel(source_id=source_id, model=model.slug)
                session.add(row)
            model.apply(row)
        await session.commit()
