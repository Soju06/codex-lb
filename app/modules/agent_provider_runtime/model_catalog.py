from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProviderModel:
    model_id: str
    display_name: str
    description: str
    provider_id: str
    protocol: str
    lifecycle: str
    input_token_limit: int
    output_token_limit: int
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...] = ("text",)
    supports_reasoning: bool = True
    supports_tool_use: bool = True
    supports_streaming: bool = True
    supports_vision: bool = False


GEMINI_MODELS: tuple[AgentProviderModel, ...] = (
    AgentProviderModel(
        model_id="gemini-3.5-flash",
        display_name="Gemini 3.5 Flash",
        description="Current stable Gemini flash model for high-speed agentic and coding workflows.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="stable",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro Preview",
        description="Preview Gemini Pro model optimized for software engineering and multi-step agent workflows.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="preview",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-3-flash-preview",
        display_name="Gemini 3 Flash Preview",
        description="Preview Gemini 3 flash model for frontier-class performance at lower cost.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="preview",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-3.1-flash-lite",
        display_name="Gemini 3.1 Flash-Lite",
        description="Stable lightweight Gemini 3.1 model for high-throughput workloads.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="stable",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        description="Stable Gemini 2.5 reasoning model for complex coding and long-context work.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="stable",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        description="Stable Gemini 2.5 price-performance model for low-latency tasks.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="stable",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
    AgentProviderModel(
        model_id="gemini-2.5-flash-lite",
        display_name="Gemini 2.5 Flash-Lite",
        description="Stable Gemini 2.5 fast, cost-efficient model for high-throughput tasks.",
        provider_id="gemini",
        protocol="gemini_api",
        lifecycle="stable",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
    ),
)

ANTIGRAVITY_MODELS: tuple[AgentProviderModel, ...] = (
    AgentProviderModel(
        model_id="antigravity-preview-05-2026",
        display_name="Antigravity Agent Preview",
        description=("Managed agent for autonomous multi-step workflows through the Gemini Interactions API."),
        provider_id="antigravity",
        protocol="interactions_api",
        lifecycle="preview",
        input_token_limit=1_048_576,
        output_token_limit=65_536,
        input_modalities=("text",),
        supports_tool_use=False,
        supports_streaming=False,
        supports_vision=False,
    ),
)


def list_gemini_models() -> tuple[AgentProviderModel, ...]:
    return GEMINI_MODELS


def list_agent_provider_models() -> tuple[AgentProviderModel, ...]:
    return (*GEMINI_MODELS, *ANTIGRAVITY_MODELS)
