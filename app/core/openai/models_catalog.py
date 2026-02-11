from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ModelLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: int
    output: int


class ModelModalities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: list[str]
    output: list[str]


class ModelVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoningEffort: str
    reasoningSummary: str
    textVerbosity: str


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    limit: ModelLimits
    modalities: ModelModalities
    variants: dict[str, ModelVariant]


MODEL_CATALOG: dict[str, ModelEntry] = {
    "gpt-5.3": ModelEntry(
        name="GPT 5.3",
        limit=ModelLimits(context=272000, output=128000),
        modalities=ModelModalities(input=["text", "image"], output=["text"]),
        variants={
            "none": ModelVariant(reasoningEffort="none", reasoningSummary="auto", textVerbosity="medium"),
            "low": ModelVariant(reasoningEffort="low", reasoningSummary="auto", textVerbosity="medium"),
            "medium": ModelVariant(reasoningEffort="medium", reasoningSummary="auto", textVerbosity="medium"),
            "high": ModelVariant(reasoningEffort="high", reasoningSummary="detailed", textVerbosity="medium"),
            "xhigh": ModelVariant(reasoningEffort="xhigh", reasoningSummary="detailed", textVerbosity="medium"),
        },
    ),
    "gpt-5.3-codex": ModelEntry(
        name="GPT 5.3 Codex",
        limit=ModelLimits(context=272000, output=128000),
        modalities=ModelModalities(input=["text", "image"], output=["text"]),
        variants={
            "low": ModelVariant(reasoningEffort="low", reasoningSummary="auto", textVerbosity="medium"),
            "medium": ModelVariant(reasoningEffort="medium", reasoningSummary="auto", textVerbosity="medium"),
            "high": ModelVariant(reasoningEffort="high", reasoningSummary="detailed", textVerbosity="medium"),
            "xhigh": ModelVariant(reasoningEffort="xhigh", reasoningSummary="detailed", textVerbosity="medium"),
        },
    ),
    "gpt-5.2": ModelEntry(
        name="GPT 5.2",
        limit=ModelLimits(context=272000, output=128000),
        modalities=ModelModalities(input=["text", "image"], output=["text"]),
        variants={
            "none": ModelVariant(reasoningEffort="none", reasoningSummary="auto", textVerbosity="medium"),
            "low": ModelVariant(reasoningEffort="low", reasoningSummary="auto", textVerbosity="medium"),
            "medium": ModelVariant(reasoningEffort="medium", reasoningSummary="auto", textVerbosity="medium"),
            "high": ModelVariant(reasoningEffort="high", reasoningSummary="detailed", textVerbosity="medium"),
            "xhigh": ModelVariant(reasoningEffort="xhigh", reasoningSummary="detailed", textVerbosity="medium"),
        },
    ),
    "gpt-5.2-codex": ModelEntry(
        name="GPT 5.2 Codex",
        limit=ModelLimits(context=272000, output=128000),
        modalities=ModelModalities(input=["text", "image"], output=["text"]),
        variants={
            "low": ModelVariant(reasoningEffort="low", reasoningSummary="auto", textVerbosity="medium"),
            "medium": ModelVariant(reasoningEffort="medium", reasoningSummary="auto", textVerbosity="medium"),
            "high": ModelVariant(reasoningEffort="high", reasoningSummary="detailed", textVerbosity="medium"),
            "xhigh": ModelVariant(reasoningEffort="xhigh", reasoningSummary="detailed", textVerbosity="medium"),
        },
    ),
    "gpt-5.1-codex-max": ModelEntry(
        name="GPT 5.1 Codex Max",
        limit=ModelLimits(context=272000, output=128000),
        modalities=ModelModalities(input=["text", "image"], output=["text"]),
        variants={
            "low": ModelVariant(reasoningEffort="low", reasoningSummary="detailed", textVerbosity="medium"),
            "medium": ModelVariant(reasoningEffort="medium", reasoningSummary="detailed", textVerbosity="medium"),
            "high": ModelVariant(reasoningEffort="high", reasoningSummary="detailed", textVerbosity="medium"),
            "xhigh": ModelVariant(reasoningEffort="xhigh", reasoningSummary="detailed", textVerbosity="medium"),
        },
    ),
}
