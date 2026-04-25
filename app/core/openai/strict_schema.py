"""Validation for OpenAI strict JSON schema mode.

When ``strict: true`` is set on a ``response_format`` (or ``text.format``)
of type ``json_schema``, the upstream Codex/Responses API enforces several
constraints on the schema (mirroring the public OpenAI Structured Outputs
policy). The most common violations are:

* every ``object`` schema must have ``additionalProperties: false``;
* every property defined under ``properties`` must be listed in
  ``required``;
* schema nodes must have a ``type`` key (no empty ``{}``).

When these violations reach the Codex backend over a websocket, the
session is terminated with ``close_code=1000`` and the proxy currently
surfaces a generic ``stream_incomplete`` 502 instead of the upstream
``invalid_json_schema`` detail. We detect these violations locally so the
client receives a 400 with the OpenAI-style error message before any
upstream request is opened.

Only the strict-mode constraints are enforced here. Schemas with
``strict`` unset or ``strict: false`` pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


@dataclass(frozen=True)
class StrictSchemaError:
    """A single strict-mode schema violation, formatted for the API."""

    code: str
    message: str
    param: str


def validate_strict_json_schema(
    schema: JsonValue,
    *,
    name: str | None,
    param: str,
) -> StrictSchemaError | None:
    """Return the first strict-mode violation in ``schema`` or ``None``.

    The returned message mirrors the upstream OpenAI Responses API format:

        Invalid schema for response_format '<name>':
        In context=(<path>), <reason>.
    """

    label = name or "response_format"
    violation = _find_violation(schema, ())
    if violation is None:
        return None
    context_path, reason = violation
    rendered_context = _render_context(context_path)
    message = f"Invalid schema for response_format '{label}': In context={rendered_context}, {reason}."
    return StrictSchemaError(
        code="invalid_json_schema",
        message=message,
        param=param,
    )


def _render_context(path: tuple[str, ...]) -> str:
    if not path:
        return "()"
    parts = ", ".join(f"'{part}'" for part in path)
    return f"({parts})"


def _find_violation(
    node: JsonValue,
    path: tuple[str, ...],
) -> tuple[tuple[str, ...], str] | None:
    if not is_json_mapping(node):
        # Non-object nodes (booleans, etc.) are not subject to strict-mode
        # property checks here.
        return None

    # Require a ``type`` key on every node we visit. The upstream API
    # rejects empty ``{}`` schemas in strict mode.
    if "type" not in node and not _has_combinator(node) and not _is_ref(node):
        return path, "schema must have a 'type' key"

    schema_type = node.get("type")

    if schema_type == "object" or "properties" in node:
        additional = node.get("additionalProperties")
        if additional is not False:
            return (
                path,
                "'additionalProperties' is required to be supplied and to be false",
            )

        properties = node.get("properties")
        if is_json_mapping(properties):
            for prop_name, prop_schema in properties.items():
                violation = _find_violation(
                    prop_schema,
                    path + ("properties", str(prop_name)),
                )
                if violation is not None:
                    return violation

    if schema_type == "array" or "items" in node:
        items = node.get("items")
        if items is not None:
            violation = _find_violation(items, path + ("items",))
            if violation is not None:
                return violation

    for combinator in ("anyOf", "oneOf", "allOf"):
        candidates = node.get(combinator)
        if is_json_list(candidates):
            for index, candidate in enumerate(candidates):
                violation = _find_violation(
                    candidate,
                    path + (combinator, str(index)),
                )
                if violation is not None:
                    return violation

    defs = node.get("$defs") or node.get("definitions")
    if is_json_mapping(defs):
        for def_name, def_schema in defs.items():
            violation = _find_violation(
                def_schema,
                path + ("$defs", str(def_name)),
            )
            if violation is not None:
                return violation

    return None


def _has_combinator(node: Mapping[str, JsonValue]) -> bool:
    return any(is_json_list(node.get(name)) for name in ("anyOf", "oneOf", "allOf"))


def _is_ref(node: Mapping[str, JsonValue]) -> bool:
    return isinstance(node.get("$ref"), str)
