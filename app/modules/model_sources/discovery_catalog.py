"""Typed CPA compatibility-catalog claims, not provider capability attestations."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import ModelSourceModel


class CpaReasoningLevel(BaseModel):
    model_config = ConfigDict(strict=True)
    effort: str = Field(min_length=1)
    description: str | None = None

    @model_validator(mode="after")
    def default_description(self) -> CpaReasoningLevel:
        if self.description is None:
            self.description = self.effort
        return self


class CpaCatalogModel(BaseModel):
    model_config = ConfigDict(strict=True)
    slug: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    support_verbosity: bool = False
    default_verbosity: str | None = None
    context_window: int = Field(gt=0, le=2**31 - 1)
    max_tokens: int | None = Field(default=None, gt=0, le=2**31 - 1)
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    supports_parallel_tool_calls: bool = False
    supported_reasoning_levels: list[CpaReasoningLevel] = Field(default_factory=list)
    default_reasoning_level: str | None = None
    supports_reasoning_summaries: bool = False

    def apply(self, row: ModelSourceModel) -> None:
        row.display_name = self.display_name
        row.context_window = self.context_window
        row.max_output_tokens = self.max_tokens
        # Streaming Responses is the explicitly selected source contract.
        row.supports_streaming = True
        row.supports_tools = self.supports_parallel_tool_calls
        row.supports_vision = "image" in self.input_modalities
        metadata = self.model_dump(mode="json", exclude_none=True)
        metadata["supports_reasoning"] = bool(self.supported_reasoning_levels) or self.supports_reasoning_summaries
        row.raw_metadata_json = json.dumps(metadata, separators=(",", ":"))
        row.is_enabled = True


class CpaCatalog(BaseModel):
    model_config = ConfigDict(strict=True)
    models: list[CpaCatalogModel] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_models(self) -> CpaCatalog:
        slugs = [model.slug for model in self.models]
        if len(slugs) != len(set(slugs)):
            raise ValueError("Duplicate CPA model identity")
        return self
