from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnthropicErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    message: str


class AnthropicErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "error"
    error: AnthropicErrorDetail
