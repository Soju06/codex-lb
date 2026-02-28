from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, StrictStr

EmbeddingInput: TypeAlias = StrictStr | list[StrictStr] | list[StrictInt] | list[list[StrictInt]]
EmbeddingVector: TypeAlias = list[StrictFloat] | StrictStr


class EmbeddingsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: StrictStr
    input: EmbeddingInput
    encoding_format: StrictStr | None = None
    dimensions: StrictInt | None = None
    user: StrictStr | None = None

    def to_upstream_payload(self, *, model_override: str | None = None) -> dict[str, object]:
        payload = self.model_dump(mode="json", exclude_none=True)
        if model_override:
            payload["model"] = model_override
        return payload


class EmbeddingDataItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    object: StrictStr = "embedding"
    index: StrictInt
    embedding: EmbeddingVector


class EmbeddingUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: StrictInt | None = None
    total_tokens: StrictInt | None = None


class EmbeddingsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    object: StrictStr = "list"
    data: list[EmbeddingDataItem]
    model: StrictStr
    usage: EmbeddingUsage | None = None
