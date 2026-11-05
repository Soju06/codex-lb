from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER, ProxyResponseError
from app.core.errors import openai_error
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.capability_lineage import (
    CapabilityLineageAlias,
    normalize_capability_lineage_aliases,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory

REQUIRED_CAPABILITY_HEADER = CODEX_LB_REQUIRED_CAPABILITY_HEADER
CODEX_WINDOW_ID_HEADER = "x-codex-window-id"
CAPABILITY_SIGNAL_UNTRUSTED_CODE = "capability_signal_untrusted"
UNSUPPORTED_REQUIRED_CAPABILITY_CODE = "unsupported_required_capability"
CAPABILITY_LINEAGE_UNAVAILABLE_CODE = "capability_lineage_unavailable"
CAPABILITY_ROUTING_UNAVAILABLE_CODE = "capability_routing_unavailable"
CAPABILITY_ROUTING_UNAVAILABLE_MESSAGE = "Required capability routing is unavailable; retry later."


class RoutingCapability(StrEnum):
    TRUSTED_CYBER = "trusted_cyber"


@dataclass(frozen=True, slots=True)
class RoutingIntent:
    required_capabilities: frozenset[RoutingCapability] = frozenset()

    @classmethod
    def empty(cls) -> RoutingIntent:
        return cls()

    @classmethod
    def requiring(cls, capability: RoutingCapability) -> RoutingIntent:
        return cls(required_capabilities=frozenset({capability}))

    @property
    def requires_trusted_cyber(self) -> bool:
        return RoutingCapability.TRUSTED_CYBER in self.required_capabilities


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    require_security_work_authorized: bool
    aliases: tuple[CapabilityLineageAlias, ...]


class CapabilityRouter:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory

    async def route(
        self,
        intent: RoutingIntent,
        *,
        api_key_id: str | None,
        aliases: Collection[CapabilityLineageAlias],
    ) -> CapabilityRoute:
        normalized_aliases = normalize_capability_lineage_aliases(aliases)
        explicitly_required = intent.requires_trusted_cyber
        if api_key_id is None or not normalized_aliases:
            return CapabilityRoute(
                require_security_work_authorized=explicitly_required,
                aliases=normalized_aliases,
            )

        try:
            async with self._repo_factory() as repositories:
                repository = repositories.capability_lineage
                if repository is None:
                    raise RuntimeError("capability lineage repository is unavailable")
                inherited_requirement = False
                if not explicitly_required:
                    inherited_requirement = await repository.is_required(
                        capability=RoutingCapability.TRUSTED_CYBER.value,
                        api_key_scope=api_key_id,
                        aliases=normalized_aliases,
                    )
                required = explicitly_required or inherited_requirement
                if required:
                    marker_hashes = await repository.require(
                        capability=RoutingCapability.TRUSTED_CYBER.value,
                        api_key_scope=api_key_id,
                        aliases=normalized_aliases,
                    )
                    if not marker_hashes:
                        raise RuntimeError("capability lineage marker was not persisted")
        except Exception as exc:
            raise _capability_lineage_unavailable_error() from exc

        return CapabilityRoute(
            require_security_work_authorized=required,
            aliases=normalized_aliases,
        )


def capability_conversation_alias(
    headers: Mapping[str, str], *, client_metadata: JsonValue | None = None
) -> CapabilityLineageAlias | None:
    # Frame metadata overrides compatibility headers, matching response.create
    # projection. A thread is more specific than a shared process identity.
    for kind, values in (
        ("codex_window", _metadata_string_values(client_metadata, CODEX_WINDOW_ID_HEADER)),
        ("codex_window", _header_values(headers, CODEX_WINDOW_ID_HEADER)),
        ("codex_thread", _header_values(headers, "thread-id")),
    ):
        for value in values:
            if value.strip():
                return CapabilityLineageAlias(kind=kind, value=value.strip())
    return None


def capability_lineage_aliases(
    headers: Mapping[str, str],
    *,
    session_id: str | None = None,
    turn_state: str | None = None,
    previous_response_ids: Collection[str | None] = (),
    client_metadata: JsonValue | None = None,
) -> tuple[CapabilityLineageAlias, ...]:
    aliases: list[CapabilityLineageAlias] = []
    # Shared task/process aliases must not merge separate conversations.
    conversation = capability_conversation_alias(headers, client_metadata=client_metadata)
    if conversation is None:
        _append_alias(aliases, "session_header", session_id)
        _append_alias(aliases, "turn_state", turn_state)
    for previous_response_id in previous_response_ids:
        _append_alias(aliases, "previous_response", previous_response_id)
    if conversation is not None:
        aliases.append(conversation)
    return normalize_capability_lineage_aliases(aliases)


type _JsonPairsValue = str | int | float | bool | None | list[_JsonPairsValue] | tuple[tuple[str, _JsonPairsValue], ...]


def reject_duplicate_native_selectors(body: bytes) -> None:
    """Inspect only top-level selectors and their native cyber member."""
    pairs: _JsonPairsValue = json.loads(body, object_pairs_hook=tuple)
    if not isinstance(pairs, tuple):
        return
    programs = [value for name, value in pairs if name == "access_programs"]
    if sum(name == "model" for name, _ in pairs) > 1 or len(programs) > 1:
        raise invalid_access_program_error()
    if programs and isinstance(programs[0], tuple) and sum(name == "cyber" for name, _ in programs[0]) > 1:
        raise invalid_access_program_error()


def request_cyber_access_program(
    payload: Mapping[str, JsonValue] | None,
    *,
    api_key: ApiKeyData | None,
    effective_model: str | None = None,
) -> str | None:
    """Recognize legacy Blue models and validate the native cyber selector."""
    if payload is None or api_key is None or not api_key.automatic_daybreak_routing:
        return None
    legacy_blue = "gpt-daybreak-blue-latest" in (payload.get("model"), effective_model)
    if "access_programs" not in payload:
        return "daybreak_blue" if legacy_blue else None
    programs = payload["access_programs"]
    if not is_json_mapping(programs) or any(key != "cyber" for key in programs):
        raise invalid_access_program_error()
    if "cyber" not in programs:
        return "daybreak_blue" if legacy_blue else None
    cyber = programs["cyber"]
    if not isinstance(cyber, str) or cyber not in {"standard", "daybreak_blue"}:
        raise invalid_access_program_error()
    if legacy_blue and cyber == "standard":
        raise invalid_access_program_error()
    return cyber


def parse_routing_intent(
    headers: Mapping[str, str],
    *,
    api_key: ApiKeyData | None,
    client_metadata: JsonValue | None = None,
    header_values: Collection[str] | None = None,
    client_metadata_values: Collection[JsonValue] | None = None,
    request_payload: Mapping[str, JsonValue] | None = None,
    effective_model: str | None = None,
) -> RoutingIntent:
    values: tuple[JsonValue, ...] = (
        *(tuple(header_values) if header_values is not None else _header_values(headers, REQUIRED_CAPABILITY_HEADER)),
        *(
            tuple(client_metadata_values)
            if client_metadata_values is not None
            else _metadata_values(client_metadata, REQUIRED_CAPABILITY_HEADER)
        ),
    )
    program = request_cyber_access_program(request_payload, api_key=api_key, effective_model=effective_model)
    if program == "standard" and values:
        raise invalid_access_program_error()
    if not values and program == "daybreak_blue":
        values = (RoutingCapability.TRUSTED_CYBER.value,)
    if not values:
        return RoutingIntent.empty()
    if api_key is None:
        raise ProxyResponseError(
            403,
            openai_error(
                CAPABILITY_SIGNAL_UNTRUSTED_CODE,
                "Required capability signal requires an authenticated proxy API key.",
                error_type="permission_error",
            ),
        )
    if len(values) != 1:
        raise _unsupported_capability_error()
    raw_capability = values[0]
    if not isinstance(raw_capability, str):
        raise _unsupported_capability_error()
    try:
        capability = RoutingCapability(raw_capability)
    except ValueError as exc:
        raise _unsupported_capability_error() from exc
    return RoutingIntent.requiring(capability)


def required_capability_metadata_values(client_metadata: JsonValue | None) -> tuple[JsonValue, ...]:
    """Required-capability values carried in a request's ``client_metadata``."""

    return _metadata_values(client_metadata, REQUIRED_CAPABILITY_HEADER)


def reject_capability_signal_outside_response_create(
    *,
    api_key: ApiKeyData | None,
    client_metadata: JsonValue | None,
    client_metadata_values: Collection[JsonValue] | None = None,
    request_payload: Mapping[str, JsonValue] | None = None,
) -> None:
    intent = parse_routing_intent(
        {},
        api_key=api_key,
        client_metadata=client_metadata,
        client_metadata_values=client_metadata_values,
        request_payload=request_payload,
    )
    if intent.required_capabilities or (
        api_key is not None
        and api_key.automatic_daybreak_routing
        and request_payload is not None
        and "access_programs" in request_payload
    ):
        raise _unsupported_capability_error()


def strip_capability_metadata(client_metadata: JsonValue | None) -> JsonValue | None:
    if not is_json_mapping(client_metadata):
        return client_metadata
    normalized_name = REQUIRED_CAPABILITY_HEADER.lower()
    return {
        key: value
        for key, value in client_metadata.items()
        if not isinstance(key, str) or key.lower() != normalized_name
    }


def _header_values(headers: Mapping[str, str], name: str) -> tuple[str, ...]:
    normalized_name = name.lower()
    return tuple(value for header_name, value in headers.items() if header_name.lower() == normalized_name)


def _metadata_values(client_metadata: JsonValue | None, name: str) -> tuple[JsonValue, ...]:
    if not is_json_mapping(client_metadata):
        return ()
    normalized_name = name.lower()
    return tuple(
        value for key, value in client_metadata.items() if isinstance(key, str) and key.lower() == normalized_name
    )


def _metadata_string_values(client_metadata: JsonValue | None, name: str) -> tuple[str, ...]:
    return tuple(value for value in _metadata_values(client_metadata, name) if isinstance(value, str))


def _append_alias(aliases: list[CapabilityLineageAlias], kind: str, value: str | None) -> None:
    if value is not None:
        aliases.append(CapabilityLineageAlias(kind=kind, value=value))


def _unsupported_capability_error() -> ProxyResponseError:
    return ProxyResponseError(
        400,
        openai_error(
            UNSUPPORTED_REQUIRED_CAPABILITY_CODE,
            "Required routing capability is unsupported.",
            error_type="invalid_request_error",
        ),
    )


def _capability_lineage_unavailable_error() -> ProxyResponseError:
    return ProxyResponseError(
        503,
        openai_error(
            CAPABILITY_LINEAGE_UNAVAILABLE_CODE,
            "Required capability lineage is unavailable; retry later.",
            error_type="server_error",
        ),
    )


def invalid_access_program_error() -> ProxyResponseError:
    return ProxyResponseError(
        400,
        openai_error(
            "unsupported_access_program",
            "Supported cyber programs are standard and daybreak_blue; native and custom routing intent must agree.",
            error_type="invalid_request_error",
        ),
    )
