"""Discover TRAE models using its installed, authenticated client."""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, Field

from app.modules.model_sources.schemas import ModelSourceModelInput


class _Reasoning(BaseModel):
    effort: str
    description: str = ""


class _Variants(BaseModel):
    standard_key: str
    standard_context_window: int | None = None
    standard_default_reasoning_level: str | None = None
    standard_supported_reasoning_levels: list[_Reasoning] = Field(default_factory=list)
    max_key: str | None = None
    max_context_window: int | None = None


class _Business(BaseModel):
    variants: _Variants


class _Model(BaseModel):
    slug: str
    config_name: str
    supported_in_api: bool = False
    input_modalities: list[str] = Field(default_factory=list)
    business_metadata: _Business


class _Catalog(BaseModel):
    models: list[_Model]


def parse_catalog(raw: str) -> list[ModelSourceModelInput]:
    result = []
    for model in _Catalog.model_validate_json(raw).models:
        if not model.supported_in_api or "glm" in model.slug.lower():
            continue
        variants = model.business_metadata.variants
        for suffix, key, window in (
            ("", variants.standard_key, variants.standard_context_window),
            ("-max", variants.max_key, variants.max_context_window),
        ):
            if not key:
                continue
            metadata = {
                "trae_config_name": model.config_name,
                "trae_model_name": key,
                "supports_reasoning": bool(variants.standard_supported_reasoning_levels),
                "supported_reasoning_levels": [r.model_dump() for r in variants.standard_supported_reasoning_levels],
                "default_reasoning_level": variants.standard_default_reasoning_level,
                "verification_status": "discovered",
            }
            result.append(
                ModelSourceModelInput(
                    model="trae/" + model.slug + suffix,
                    display_name="TRAE " + model.slug + suffix,
                    context_window=window,
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision="image" in model.input_modalities,
                    raw_metadata_json=json.dumps(metadata),
                    is_enabled=False,
                )
            )
    return result


async def discover_models() -> list[ModelSourceModelInput]:
    try:
        process = await asyncio.create_subprocess_exec(
            "traecli",
            "debug",
            "models",
            "--remote",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise ValueError("TRAE CLI is unavailable on this server") from None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 45)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise ValueError("TRAE model discovery failed; check the local login")
    try:
        return parse_catalog(stdout.decode())
    except ValueError:
        raise ValueError("TRAE returned an invalid model catalog") from None
