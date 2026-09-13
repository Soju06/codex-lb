"""Rebuild a captured Codex Responses request body as a committable fixture.

Pure functions plus a thin CLI. Imports nothing from ``app``: the fixture gate
(``tests/unit/test_codex_body_fixtures.py``) is where this module's field sets
are pinned against the production constants, so the tooling stays runnable on a
checkout without the application environment.

**An allowlist over structure, not a scrubber over text.** The output is
*constructed*, never edited: every node of the captured body is matched against
a rule in ``BODY``, and only what a rule names reaches the fixture. Two kinds of
rule exist and there is no third:

* **Preserved.** A closed domain the portability and parity assertions read --
  an object key, a discriminator value (``type``, ``role``, ``status``,
  ``phase``, ``effort``, ``verbosity``, a JSON Schema keyword), a boolean, a
  number, ``null``, a model slug in slug shape, and the small vocabulary of
  Codex's own tool names. A string outside the declared domain is a *refusal*,
  never a silent rewrite: ``UnsanitisableBodyError`` names the path.
* **Synthesised.** Everything else -- ``instructions``, message ``content``
  text, a ``function_call``'s ``arguments``, a ``function_call_output``'s
  ``output``, a tool ``description``, a JSON Schema property name, an ``enum``
  value, a ``metadata`` key, an ``operation.path``, a URL, every identifier --
  is replaced wholesale. What a replaced string leaves behind is small but not
  nothing, and it is worth naming: whether it was blank (the bit the replay
  predicate reads); for a URL, its scheme, because that is what decides
  account-neutrality; for an identifier, which other identifiers it equalled;
  and for a ``metadata`` key or a JSON Schema property name, its alphabetical
  rank among its siblings, because the numbering is by sorted order so that a
  rebuild of a rebuild is identical.

The predecessor was a denylist: it kept the captured text and rewrote the
substrings a set of regexes recognised. Two rounds of widening those regexes
were each bypassed, the second one measurably --
``PATH=/usr/local/bin:/home/jane/.local/bin`` inside a
``function_call_output.output`` was rewritten to
``PATH=/workspace/repo:/home/jane/.local/bin``, and the rewrite *removed the
privacy gate's trigger*, so the operator's home directory became committable
behind a green gate. Neither failure mode has anywhere to happen here: nothing
rewrites a captured string, and a captured *string* reaches the output only when
a rule names the closed domain it came from. Those domains are enumerated below
-- object keys, discriminator values, JSON Schema keywords, Codex's own tool
names, the account-scoped reference property names -- plus, outside them,
booleans, bounded numbers, a model slug in slug shape, and the residue named
above. Everything else is constructed from the JSON path.

Two things this is *not*. It is not a guarantee about information: cardinality,
array order, the permutation a ``required`` list encodes and the bounded numbers
are all shape, and shape is a channel. And it is not self-evidently true --
``surviving_captured_strings`` is the check, it runs in the CLI before a write
and in the corpus gate over what is committed, and it is a detector with known
blind spots rather than a proof. The thing that keeps operator content out of
the repository is a human reading the diff; the allowlist is what makes that
reading short enough to happen.

What that costs is stated rather than hidden: the committed fixture is no
longer a readable transcript. It records the *shape* of a real Codex body --
every key, every discriminator, every tool declaration's field set, every item
id's presence and every call/output pairing -- and none of its prose. The
portability verdict is a function of exactly that shape, which is why the gate
can assert the recorded verdict is unchanged by the rebuild.

Absence is preserved and nothing is ever fabricated. A real Responses-Lite body
carries no ``instructions``, no ``tools`` and no ``stream_options``; the
overflow portability view declines unknown top-level fields, so adding a key
would change the recorded verdict. A key the body did not carry is not written.

A websocket capture is persisted verbatim, so it arrives with the transport's
frame envelope (``type``) around the Responses body. The envelope is dropped
here -- it is not a Responses field -- while ``generate``, which the frame does
carry as a Responses field, goes through the rules like any other value.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json, read_json
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json, read_json


class UnsanitisableBodyError(ValueError):
    """A captured body carries a node no rule in ``BODY`` describes."""


@dataclass(frozen=True, slots=True)
class Redaction:
    """What changed and where. Never carries the removed value."""

    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class Placeholders:
    """Fixed substitutes. Never derived from the capture, so output is stable.

    ``workspace`` and ``skill_root`` are no longer written by this module -- the
    text that used to carry them is synthesised whole. They remain the corpus's
    declared placeholder namespace, which the residual scan in
    ``fixture_privacy_scan`` clears by value and the hand-written pre-strip
    fixtures still use.
    """

    workspace: str = "/workspace/repo"
    skill_root: str = "/workspace/skills"
    skill_name: str = "example-skill"
    date: str = "2026-01-01"
    timezone: str = "Etc/UTC"
    shell: str = "bash"
    # Namespace for every placeholder UUID: a RFC 4122 v4 layout whose node
    # field is a zero-padded ordinal, so ``is_placeholder_uuid`` recognises the
    # whole family without the scanner importing a literal list.
    uuid_prefix: str = "00000000-0000-4000-8000-"


PLACEHOLDERS = Placeholders()

_PLACEHOLDER_UUID_PATTERN = re.compile(r"\A00000000-0000-4000-8000-\d{12}\Z")

# Every synthesised string starts here, so "is this value the tooling's own?" is
# a prefix test rather than a list the gate has to keep in step.
SYNTHETIC_PREFIX = "[synthetic "
_SYNTHETIC_PATTERN = re.compile(r"\A\[synthetic .+\]\Z", re.DOTALL)

# The substitute for a number inside a tool's JSON Schema, where no value is
# read by the portability decision and every value is authored by whoever wrote
# the tool.
SYNTHETIC_NUMBER = 0

# Preserved numbers are capped, because an unbounded numeric slot is an
# unbounded channel: Python integers are arbitrary precision and a JSON double
# carries 53 bits of mantissa, so ``max_output_tokens`` or ``top_p`` can hold an
# encoded filesystem path that no string walk sees. Every number a Responses
# body plausibly carries fits well inside this.
NUMERIC_BOUND = 2**31
NUMERIC_PLACES = 4


def _is_bounded_float(value: float) -> bool:
    """Whether a float is finite, in range, and carries no hidden precision."""

    return (
        value == value
        and abs(value) != float("inf")
        and abs(value) <= NUMERIC_BOUND
        and round(value, NUMERIC_PLACES) == value
    )


def is_placeholder_uuid(value: str) -> bool:
    """Whether ``value`` belongs to the fixture's placeholder UUID family."""

    return bool(_PLACEHOLDER_UUID_PATTERN.match(value))


def placeholder_uuid(ordinal: int, placeholders: Placeholders = PLACEHOLDERS) -> str:
    return f"{placeholders.uuid_prefix}{ordinal:012d}"


def is_synthetic_text(value: str) -> bool:
    """Whether ``value`` is one of this module's own path-derived placeholders."""

    return bool(_SYNTHETIC_PATTERN.match(value))


def synthetic_text(path: str) -> str:
    """The substitute for one free-text value: its JSON path and nothing else."""

    return f"{SYNTHETIC_PREFIX}{path}]"


# --- vocabularies -------------------------------------------------------------------
#
# Every string below is part of the allowlist: a key name or a discriminator the
# portability decision, the replay predicate or the fixture gate reads. A
# captured string that is none of these never reaches the output.

MESSAGE_ROLES: frozenset[str] = frozenset({"assistant", "developer", "system", "user"})
MESSAGE_PHASES: frozenset[str] = frozenset({"commentary", "final_answer"})
ITEM_STATUSES: frozenset[str] = frozenset({"completed", "failed", "in_progress", "incomplete"})
CONTENT_PART_TYPES: frozenset[str] = frozenset(
    {"input_file", "input_image", "input_text", "output_text", "refusal", "text"}
)
REASONING_PART_TYPES: frozenset[str] = frozenset({"reasoning_text", "summary_text", "text"})
IMAGE_DETAIL_LEVELS: frozenset[str] = frozenset({"auto", "high", "low", "original"})
REASONING_EFFORTS: frozenset[str] = frozenset({"minimal", "low", "medium", "high", "xhigh", "none"})
REASONING_SUMMARIES: frozenset[str] = frozenset({"auto", "concise", "detailed", "none"})
REASONING_CONTEXTS: frozenset[str] = frozenset({"all_turns", "last_turn", "none"})
TEXT_VERBOSITIES: frozenset[str] = frozenset({"low", "medium", "high"})
TRUNCATIONS: frozenset[str] = frozenset({"auto", "disabled"})
SERVICE_TIERS: frozenset[str] = frozenset({"auto", "default", "flex", "priority", "scale"})
CACHE_RETENTIONS: frozenset[str] = frozenset({"in-memory", "24h"})
TOOL_CHOICE_STRINGS: frozenset[str] = frozenset({"auto", "none", "required"})
TOOL_CHOICE_TYPES: frozenset[str] = frozenset({"allowed_tools", "custom", "function"})
INCLUDE_VALUES: frozenset[str] = frozenset(
    {
        "code_interpreter_call.outputs",
        "computer_call_output.output.image_url",
        "file_search_call.results",
        "message.input_image.image_url",
        "message.output_text.logprobs",
        "reasoning.encrypted_content",
        "web_search_call.action.sources",
    }
)
TOOL_SEARCH_EXECUTIONS: frozenset[str] = frozenset({"client", "server"})
WEB_SEARCH_CONTENT_TYPES: frozenset[str] = frozenset({"text", "image"})
WEB_SEARCH_CONTEXT_SIZES: frozenset[str] = frozenset({"low", "medium", "high"})
WEB_SEARCH_LOCATION_TYPES: frozenset[str] = frozenset({"approximate"})
CUSTOM_TOOL_FORMAT_TYPES: frozenset[str] = frozenset({"text", "grammar"})
CUSTOM_TOOL_GRAMMAR_SYNTAXES: frozenset[str] = frozenset({"lark", "regex"})
TEXT_FORMAT_TYPES: frozenset[str] = frozenset({"text", "json_object", "json_schema"})
APPLY_PATCH_OPERATION_TYPES: frozenset[str] = frozenset({"create_file", "delete_file", "update_file"})
CALLER_TYPES: frozenset[str] = frozenset({"direct"})

# Tool names Codex 0.154.0 emits. Preserved because the corpus README cites them
# as evidence; a name outside the list -- an MCP server's, say -- is synthesised
# rather than refused, because a tool name is read only for non-blankness. The
# *list* is ours, not the operator's, which is the property that matters: a
# declaration whose name happens to collide with one of these survives as one of
# fourteen dictionary words, chosen from a set written down here.
CODEX_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "apply_patch",
        "collaboration",
        "exec",
        "exec_command",
        "followup_task",
        "functions",
        "request_user_input",
        "shell",
        "spawn_agent",
        "update_plan",
        "view_image",
        "wait",
        "web",
        "write_stdin",
    }
)

# ``internal_chat_message_metadata_passthrough.content_item_kinds`` entries Codex
# emits. Same rule as a tool name: known ones survive, anything else is
# synthesised.
CONTENT_ITEM_KINDS: frozenset[str] = frozenset(
    {
        "model.base_instructions",
        "model.developer_instructions",
        "user.message",
    }
)

# JSON Schema keywords a tool's ``parameters`` may use. A keyword outside this
# set is a refusal: ``$ref``/``$defs`` are deliberately absent, because a
# cross-document reference cannot be rewritten alongside the property names it
# points at, and refusing is the failure mode that cannot leak.
SCHEMA_KEYWORDS: frozenset[str] = frozenset(
    {
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "default",
        "description",
        "encrypted",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "not",
        "nullable",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
SCHEMA_TYPES: frozenset[str] = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})

# The only JSON Schema property names carried across, and the measured reason
# they are:
#
# ``_contains_account_scoped_tool_state`` recognises an account-scoped reference
# by *key name*, so renaming one inside a declaration it walks flips the verdict.
# It walks the whole subtree of every declaration except a ``function`` tool's
# own ``parameters``, which it skips at the root. Measured against 0.154.0's
# declarations: a property named ``file_id`` under ``tool_search.parameters``
# moves the verdict, the same property under a ``function`` tool's
# ``parameters`` does not. Keeping the names everywhere is the cheap side of
# that asymmetry -- eight OpenAI-vocabulary words, chosen from a list written
# down here rather than from the capture.
SCHEMA_PRESERVED_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        "container_id",
        "encrypted_content",
        "file_id",
        "file_ids",
        "file_url",
        "image_url",
        "vector_store_id",
        "vector_store_ids",
    }
)

# A model slug is the one operator-chosen token preserved verbatim, in slug
# shape only. A shape is not a closed domain -- 64 characters of
# ``[A-Za-z0-9._-]`` will hold anything -- so the closing argument is the fixture
# gate, not this pattern: ``test_every_captured_body_names_its_recorded_slug``
# asserts the committed body's ``model`` equals the slug provenance records, and
# ``test_the_committed_catalog_is_the_one_every_captured_fixture_records``
# asserts that slug is one the committed reference catalog serves.
MODEL_SLUG = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

# URL schemes ``replay_safety._url_is_account_neutral`` decides on. The captured
# URL is never carried: its scheme picks which synthetic URL is emitted, so the
# neutrality verdict survives and the address does not.
SYNTHETIC_URLS: dict[str, str] = {
    "data": "data:image/png;base64,iVBORw0KGgo=",
    "http": "http://fixture.invalid/asset",
    "https": "https://fixture.invalid/asset",
}


# --- the rule engine ----------------------------------------------------------------


class _Absent:
    """Sentinel: a rule that removes its own key rather than rewriting it."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"


_ABSENT = _Absent()


@dataclass(slots=True)
class _Run:
    """One sanitisation: the redaction log and the identifier map."""

    placeholders: Placeholders
    strip_item_ids: bool
    redactions: list[Redaction] = field(default_factory=list)
    identifiers: dict[tuple[str, str], str] = field(default_factory=dict)
    next_ordinal: int = 1

    def note(self, path: str, kind: str, *, changed: bool = True) -> None:
        if changed:
            self.redactions.append(Redaction(path, kind))

    def identifier(self, namespace: str, captured: str, *, fixed_ordinal: int | None = None) -> str:
        key = (namespace, captured)
        existing = self.identifiers.get(key)
        if existing is not None:
            return existing
        if fixed_ordinal is None:
            ordinal = self.next_ordinal
            self.next_ordinal += 1
        else:
            ordinal = fixed_ordinal
        minted = placeholder_uuid(ordinal, self.placeholders)
        value = f"{namespace}_{minted}" if namespace else minted
        self.identifiers[key] = value
        return value


def _refuse(path: str, detail: str) -> UnsanitisableBodyError:
    return UnsanitisableBodyError(f"{path or '<body>'}: {detail}")


def _numbered_slots(names: Sequence[str], prefix: str) -> dict[str, str]:
    """Map each captured name to a numbered path slot, stably across rebuilds.

    Ordinals follow the *sorted* order of the names and are zero-padded to a
    width the count decides, because a fixture is written with ``sort_keys`` and
    re-read in that order: numbering by insertion order gave ``field10`` before
    ``field1`` on the second pass and the rebuild stopped being idempotent.
    """

    width = max(2, len(str(len(names))))
    return {name: f"{prefix}{index:0{width}d}" for index, name in enumerate(names, start=1)}


class _Node:
    """One rule. ``apply`` rebuilds a value; ``vocabulary`` reports what it may emit."""

    __slots__ = ()

    def apply(self, value: Any, path: str, run: _Run) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def vocabulary(self) -> Iterator[str]:
        return iter(())


@dataclass(frozen=True, slots=True)
class _Enum(_Node):
    """A discriminator with a closed domain. Preserved, or refused."""

    values: frozenset[str]

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, str) or value not in self.values:
            raise _refuse(path, f"value outside the reviewed vocabulary {sorted(self.values)}")
        return value

    def vocabulary(self) -> Iterator[str]:
        return iter(sorted(self.values))


@dataclass(frozen=True, slots=True)
class _Scalar(_Node):
    """A boolean, a bounded number or ``null``. A string is never a scalar here.

    The bound is the point. Python integers are arbitrary precision and JSON
    floats carry full double precision, so an unbounded numeric slot is an
    unbounded channel: a hostile or careless ``max_output_tokens`` can hold an
    80-digit integer that decodes to a filesystem path. ``NUMERIC_BOUND`` and
    ``NUMERIC_PLACES`` cap a preserved integer at 32 bits and a preserved float
    at ``2**31 * 10**4`` distinct values, about 45 bits -- enough for every value
    a Responses body plausibly carries (a token budget, a temperature, a JSON
    Schema bound) and a refusal for the rest.
    """

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int) and -NUMERIC_BOUND <= value <= NUMERIC_BOUND:
            return value
        if isinstance(value, float) and _is_bounded_float(value):
            return value
        if isinstance(value, int | float):
            raise _refuse(path, f"number outside the reviewed bound (|v| <= {NUMERIC_BOUND}, {NUMERIC_PLACES} places)")
        raise _refuse(path, "expected a boolean, a number or null")


@dataclass(frozen=True, slots=True)
class _Token(_Node):
    """A string preserved only in a declared shape, and refused otherwise."""

    pattern: re.Pattern[str]
    description: str

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, str) or not self.pattern.match(value):
            raise _refuse(path, f"expected {self.description}")
        return value


@dataclass(frozen=True, slots=True)
class _Text(_Node):
    """Free text. Replaced wholesale; only blankness survives."""

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise _refuse(path, "expected a string")
        replacement = "" if not value.strip() else synthetic_text(path)
        run.note(path, "synthetic_text", changed=replacement != value)
        return replacement


@dataclass(frozen=True, slots=True)
class _KnownLabel(_Node):
    """A name: preserved when it is Codex's own, synthesised when it is not."""

    values: frozenset[str]

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise _refuse(path, "expected a string")
        if value in self.values:
            return value
        replacement = synthetic_text(path)
        run.note(path, "synthetic_label", changed=replacement != value)
        return replacement

    def vocabulary(self) -> Iterator[str]:
        return iter(sorted(self.values))


@dataclass(frozen=True, slots=True)
class _Ident(_Node):
    """An identifier: replaced, but referentially consistent within one body.

    Two items naming the same ``call_id`` still name the same placeholder, which
    is what ``responses_input_items_are_self_contained_fresh_replay`` pairs on.
    An empty identifier stays empty: a *non-empty* id is what makes a native
    Codex body decline as ``not_portable_history``, and both states are evidence.
    """

    namespace: str = ""
    fixed_ordinal: int | None = None
    strippable: bool = False

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None or value == "":
            return value
        if not isinstance(value, str):
            raise _refuse(path, "expected a string identifier")
        if self.strippable and run.strip_item_ids:
            run.note(path, "item_id_stripped")
            return _ABSENT
        replacement = run.identifier(self.namespace, value, fixed_ordinal=self.fixed_ordinal)
        run.note(path, "identifier_placeholder", changed=replacement != value)
        return replacement


@dataclass(frozen=True, slots=True)
class _Url(_Node):
    """A URL: the scheme decides which synthetic address is emitted."""

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise _refuse(path, "expected a URL string")
        scheme = value.split(":", 1)[0].casefold() if ":" in value else ""
        replacement = SYNTHETIC_URLS.get(scheme)
        if replacement is None:
            raise _refuse(path, f"URL scheme outside the reviewed vocabulary {sorted(SYNTHETIC_URLS)}")
        run.note(path, "synthetic_url", changed=replacement != value)
        return replacement

    def vocabulary(self) -> Iterator[str]:
        return iter(sorted(SYNTHETIC_URLS.values()))


@dataclass(frozen=True, slots=True)
class _Arr(_Node):
    item: _Node

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, list):
            raise _refuse(path, "expected an array")
        rebuilt = [self.item.apply(entry, f"{path}[{index}]", run) for index, entry in enumerate(value)]
        return [entry for entry in rebuilt if entry is not _ABSENT]

    def vocabulary(self) -> Iterator[str]:
        return self.item.vocabulary()


@dataclass(frozen=True, slots=True)
class _Obj(_Node):
    """An object with a closed key namespace.

    Keys are emitted in the order the capture presents them; a key no rule names
    is refused rather than dropped, because a silently dropped key changes the
    key set the replay predicate validates. ``dropped`` is the one exception:
    keys removed on purpose, each with a redaction entry.
    """

    fields: Mapping[str, _Node]
    dropped: Mapping[str, str] = field(default_factory=dict)

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, dict):
            raise _refuse(path, "expected an object")
        rebuilt: dict[str, Any] = {}
        for index, (key, entry) in enumerate(value.items(), start=1):
            child = f"{path}.{key}" if path else str(key)
            if key in self.dropped:
                run.note(child, self.dropped[key])
                continue
            rule = self.fields.get(key)
            if rule is None:
                # The key itself is not echoed: an unreviewed key is the node
                # most likely to be MCP-authored, and an operator pastes a
                # refusal into an issue far more readily than a fixture.
                raise _refuse(path, f"field {index} of {len(value)} is outside the reviewed allowlist")
            outcome = rule.apply(entry, child, run)
            if outcome is not _ABSENT:
                rebuilt[key] = outcome
        return rebuilt

    def vocabulary(self) -> Iterator[str]:
        for key, rule in self.fields.items():
            yield key
            yield from rule.vocabulary()


@dataclass(frozen=True, slots=True)
class _OpenMap(_Node):
    """An object whose *keys* are the operator's: both sides are synthesised."""

    value: _Node

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, dict):
            raise _refuse(path, "expected an object")
        entry = value
        rebuilt: dict[str, Any] = {}
        for key, slot in _numbered_slots(sorted(entry), f"{path}.key").items():
            # Always the slot's own placeholder: a key that already *is* that
            # placeholder is reproduced, which is where idempotence comes from,
            # and a key that merely looks synthetic is still replaced.
            minted = synthetic_text(slot)
            run.note(slot, "synthetic_key", changed=minted != key)
            rebuilt[minted] = self.value.apply(entry[key], slot, run)
        return rebuilt

    def vocabulary(self) -> Iterator[str]:
        return self.value.vocabulary()


@dataclass(frozen=True, slots=True)
class _Switch(_Node):
    """Dispatch an object on a discriminator key with a closed domain."""

    key: str
    cases: Mapping[str, _Node]
    default_case: str | None = None

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if not isinstance(value, dict):
            raise _refuse(path, "expected an object")
        discriminator = value.get(self.key)
        if discriminator is None:
            discriminator = self.default_case
        if not isinstance(discriminator, str) or discriminator not in self.cases:
            raise _refuse(path, f"{self.key} outside the reviewed vocabulary {sorted(self.cases)}")
        return self.cases[discriminator].apply(value, path, run)

    def vocabulary(self) -> Iterator[str]:
        yield self.key
        for name, rule in self.cases.items():
            yield name
            yield from rule.vocabulary()


@dataclass(frozen=True, slots=True)
class _Either(_Node):
    """One slot the Responses API lets carry more than one JSON shape."""

    string: _Node | None = None
    array: _Node | None = None
    mapping: _Node | None = None

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and self.string is not None:
            return self.string.apply(value, path, run)
        if isinstance(value, list) and self.array is not None:
            return self.array.apply(value, path, run)
        if isinstance(value, dict) and self.mapping is not None:
            return self.mapping.apply(value, path, run)
        raise _refuse(path, "value shape outside the reviewed alternatives")

    def vocabulary(self) -> Iterator[str]:
        for rule in (self.string, self.array, self.mapping):
            if rule is not None:
                yield from rule.vocabulary()


@dataclass(frozen=True, slots=True)
class _Lazy(_Node):
    """A self-referential rule (a tool namespace nests tool declarations).

    Contributes nothing to ``vocabulary``: the rule it defers to is always
    reachable from a non-deferred reference, and a cycle would not terminate.
    """

    factory: Callable[[], _Node]

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        return self.factory().apply(value, path, run)


class _SchemaNode(_Node):
    """A tool's JSON Schema.

    Keyword names survive (a closed vocabulary), the schema's *arity* survives
    (how many properties, which are required, how deep the nesting goes) and
    every string the operator could have authored -- property names, titles,
    descriptions, enum values, patterns, defaults -- is synthesised. Property
    names and ``required`` entries go through one map per object, so a schema
    that required a property still requires the property it was renamed to.
    """

    __slots__ = ()

    def apply(self, value: Any, path: str, run: _Run) -> Any:
        if isinstance(value, list):
            return [self.apply(entry, f"{path}[{index}]", run) for index, entry in enumerate(value)]
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            # No number in a tool's schema is read by the portability decision,
            # and an MCP server authors every one of them, so the value goes.
            run.note(path, "synthetic_number", changed=value != SYNTHETIC_NUMBER)
            return SYNTHETIC_NUMBER
        if isinstance(value, str):
            replacement = synthetic_text(path)
            run.note(path, "synthetic_text", changed=replacement != value)
            return replacement
        if not isinstance(value, dict):
            raise _refuse(path, "expected a JSON Schema node")
        slots = self._property_slots(value, path)
        names = {name: name if slot is None else synthetic_text(slot) for name, slot in slots.items()}
        rebuilt: dict[str, Any] = {}
        for index, (key, entry) in enumerate(value.items(), start=1):
            child = f"{path}.{key}"
            if key not in SCHEMA_KEYWORDS:
                raise _refuse(path, f"keyword {index} of {len(value)} is outside the reviewed JSON Schema set")
            if key == "type":
                rebuilt[key] = _SCHEMA_TYPE.apply(entry, child, run)
            elif key == "properties" and isinstance(entry, dict):
                rebuilt[key] = {
                    names[name]: self.apply(sub, slots[name] or f"{child}.{name}", run) for name, sub in entry.items()
                }
            elif key == "required":
                rebuilt[key] = self._required(entry, names, child, run)
            else:
                rebuilt[key] = self.apply(entry, child, run)
        return rebuilt

    @staticmethod
    def _property_slots(value: Mapping[str, Any], path: str) -> dict[str, str | None]:
        """Each property name mapped to its numbered slot, or ``None`` when kept.

        Only the account-scoped reference names are kept, and they take no
        ordinal, so adding one to a schema does not renumber its siblings.
        """

        properties = value.get("properties")
        if not isinstance(properties, dict):
            return {}
        renamed = sorted(name for name in properties if name not in SCHEMA_PRESERVED_PROPERTY_NAMES)
        numbered = _numbered_slots(renamed, f"{path}.properties.field")
        return {name: numbered.get(name) for name in properties}

    def _required(self, entry: Any, names: Mapping[str, str], path: str, run: _Run) -> Any:
        if not isinstance(entry, list):
            raise _refuse(path, "expected an array of property names")
        rebuilt: list[str] = []
        for index, name in enumerate(entry):
            if not isinstance(name, str):
                raise _refuse(f"{path}[{index}]", "expected a property name")
            minted = names.get(name, synthetic_text(f"{path}[{index}]"))
            run.note(f"{path}[{index}]", "synthetic_key", changed=minted != name)
            rebuilt.append(minted)
        return rebuilt

    def vocabulary(self) -> Iterator[str]:
        yield from sorted(SCHEMA_KEYWORDS)
        yield from sorted(SCHEMA_TYPES)
        yield from sorted(SCHEMA_PRESERVED_PROPERTY_NAMES)


_SCHEMA = _SchemaNode()
_SCHEMA_TYPE = _Either(string=_Enum(SCHEMA_TYPES), array=_Arr(_Enum(SCHEMA_TYPES)))


# --- the body ------------------------------------------------------------------------

_CALLER = _Obj({"type": _Enum(CALLER_TYPES)})
_INTERNAL_CHAT_METADATA = _Obj(
    {
        # No namespace prefix: the residual scan recognises a placeholdered
        # ``turn_id`` by the bare UUID family, and a prefixed one would red-line
        # the corpus it is meant to clear.
        "turn_id": _Ident(),
        "content_item_kinds": _Arr(_KnownLabel(CONTENT_ITEM_KINDS)),
    }
)

_TEXT_PART = _Obj({"type": _Enum(CONTENT_PART_TYPES), "text": _Text()})
_IMAGE_PART = _Obj(
    {
        "type": _Enum(CONTENT_PART_TYPES),
        "detail": _Enum(IMAGE_DETAIL_LEVELS),
        "image_url": _Url(),
        "file_id": _Ident("file"),
    }
)
_FILE_PART = _Obj(
    {
        "type": _Enum(CONTENT_PART_TYPES),
        "filename": _Text(),
        "file_data": _Text(),
        "file_id": _Ident("file"),
        "file_url": _Url(),
    }
)
_CONTENT_PART = _Switch(
    "type",
    {
        "input_text": _TEXT_PART,
        "output_text": _TEXT_PART,
        "text": _TEXT_PART,
        "refusal": _Obj({"type": _Enum(CONTENT_PART_TYPES), "refusal": _Text()}),
        "input_image": _IMAGE_PART,
        "input_file": _FILE_PART,
    },
)
_CONTENT = _Either(string=_Text(), array=_Arr(_CONTENT_PART))
_OUTPUT = _Either(string=_Text(), array=_Arr(_CONTENT_PART))

_WEB_SEARCH_TOOL = _Obj(
    {
        "type": _Enum(frozenset({"web_search", "web_search_preview"})),
        "external_web_access": _Scalar(),
        "search_content_types": _Arr(_Enum(WEB_SEARCH_CONTENT_TYPES)),
        "search_context_size": _Enum(WEB_SEARCH_CONTEXT_SIZES),
        "filters": _Obj({"allowed_domains": _Arr(_Text())}),
        "user_location": _Obj(
            {
                "type": _Enum(WEB_SEARCH_LOCATION_TYPES),
                "city": _Text(),
                "country": _Text(),
                "region": _Text(),
                "timezone": _Text(),
            }
        ),
    }
)

# A tool declaration. ``function``/``custom`` also appear *without* a ``type``
# key inside a Responses-Lite ``namespace`` bundle, which is why the switch has
# a default case; the namespace case nests this same rule through ``_Lazy``.
_TOOL: _Switch = _Switch(
    "type",
    {
        "function": _Obj(
            {
                "type": _Enum(frozenset({"function"})),
                "name": _KnownLabel(CODEX_TOOL_NAMES),
                "description": _Text(),
                "parameters": _SCHEMA,
                "strict": _Scalar(),
            }
        ),
        "custom": _Obj(
            {
                "type": _Enum(frozenset({"custom"})),
                "name": _KnownLabel(CODEX_TOOL_NAMES),
                "description": _Text(),
                "format": _Obj(
                    {
                        "type": _Enum(CUSTOM_TOOL_FORMAT_TYPES),
                        "syntax": _Enum(CUSTOM_TOOL_GRAMMAR_SYNTAXES),
                        "definition": _Text(),
                    }
                ),
            }
        ),
        "web_search": _WEB_SEARCH_TOOL,
        "web_search_preview": _WEB_SEARCH_TOOL,
        "tool_search": _Obj(
            {
                "type": _Enum(frozenset({"tool_search"})),
                "description": _Text(),
                "execution": _Enum(TOOL_SEARCH_EXECUTIONS),
                "parameters": _SCHEMA,
            }
        ),
        "apply_patch": _Obj({"type": _Enum(frozenset({"apply_patch"})), "description": _Text()}),
        "local_shell": _Obj({"type": _Enum(frozenset({"local_shell"})), "description": _Text()}),
        "shell": _Obj({"type": _Enum(frozenset({"shell"})), "description": _Text()}),
        "namespace": _Obj(
            {
                "type": _Enum(frozenset({"namespace"})),
                "name": _KnownLabel(CODEX_TOOL_NAMES),
                "description": _Text(),
                "tools": _Arr(_Lazy(lambda: _TOOL)),
            }
        ),
    },
    default_case="function",
)


def _tool_call(item_type: str, payload: Mapping[str, _Node]) -> _Obj:
    """One transcript item that is a tool call or its output."""

    return _Obj(
        {
            "type": _Enum(frozenset({item_type})),
            "id": _Ident(item_type.split("_", 1)[0], strippable=True),
            "call_id": _Ident("call"),
            "caller": _CALLER,
            "status": _Enum(ITEM_STATUSES),
            "internal_chat_message_metadata_passthrough": _INTERNAL_CHAT_METADATA,
            **payload,
        }
    )


_APPLY_PATCH_OPERATION = _Switch(
    "type",
    {
        "create_file": _Obj({"type": _Enum(APPLY_PATCH_OPERATION_TYPES), "path": _Text(), "diff": _Text()}),
        "update_file": _Obj({"type": _Enum(APPLY_PATCH_OPERATION_TYPES), "path": _Text(), "diff": _Text()}),
        "delete_file": _Obj({"type": _Enum(APPLY_PATCH_OPERATION_TYPES), "path": _Text()}),
    },
)

_INPUT_ITEM = _Switch(
    "type",
    {
        "message": _Obj(
            {
                "type": _Enum(frozenset({"message"})),
                "role": _Enum(MESSAGE_ROLES),
                "status": _Enum(ITEM_STATUSES),
                "phase": _Enum(MESSAGE_PHASES),
                "id": _Ident("msg", strippable=True),
                "content": _CONTENT,
                "internal_chat_message_metadata_passthrough": _INTERNAL_CHAT_METADATA,
            }
        ),
        "input_text": _TEXT_PART,
        "input_image": _IMAGE_PART,
        "input_file": _FILE_PART,
        "additional_tools": _Obj(
            {
                "type": _Enum(frozenset({"additional_tools"})),
                "role": _Enum(MESSAGE_ROLES),
                "id": _Ident("at", strippable=True),
                "tools": _Arr(_TOOL),
            }
        ),
        "function_call": _tool_call("function_call", {"name": _KnownLabel(CODEX_TOOL_NAMES), "arguments": _Text()}),
        "function_call_output": _tool_call("function_call_output", {"output": _OUTPUT}),
        "custom_tool_call": _tool_call("custom_tool_call", {"name": _KnownLabel(CODEX_TOOL_NAMES), "input": _Text()}),
        "custom_tool_call_output": _tool_call("custom_tool_call_output", {"output": _OUTPUT}),
        "apply_patch_call": _tool_call(
            "apply_patch_call", {"operation": _APPLY_PATCH_OPERATION, "patch": _Text(), "input": _Text()}
        ),
        "apply_patch_call_output": _tool_call("apply_patch_call_output", {"output": _OUTPUT}),
        "reasoning": _Obj(
            {
                "type": _Enum(frozenset({"reasoning"})),
                "id": _Ident("rs", strippable=True),
                "status": _Enum(ITEM_STATUSES),
                "encrypted_content": _Text(),
                "summary": _Arr(_Obj({"type": _Enum(REASONING_PART_TYPES), "text": _Text()})),
                "content": _Arr(_Obj({"type": _Enum(REASONING_PART_TYPES), "text": _Text()})),
            }
        ),
    },
    default_case="message",
)

# Whole top-level fields the fixture never keeps. A superset of production's
# ``STRIPPED_TELEMETRY_FIELDS`` (pinned in the fixture gate).
DROPPED_TOP_LEVEL_FIELDS: frozenset[str] = frozenset({"client_metadata", "access_programs"})

# The websocket lane's frame envelope, not a Responses field: production adds
# the same ``type`` in ``_build_websocket_response_create_payload``. Kept out of
# ``DROPPED_TOP_LEVEL_FIELDS`` because that set is pinned against production's
# telemetry stripper and this is not telemetry.
WEBSOCKET_ENVELOPE_FIELDS: frozenset[str] = frozenset({"type"})

# Removed from ``stream_options``; the object is dropped once emptied, exactly
# as production's ``strip_source_telemetry`` does.
SANITISED_STREAM_OPTIONS_KEYS: frozenset[str] = frozenset({"reasoning_summary_delivery"})
STREAM_OPTIONS_FIELD = "stream_options"

BODY = _Obj(
    {
        "model": _Token(MODEL_SLUG, "a model slug"),
        "instructions": _Text(),
        "input": _Either(string=_Text(), array=_Arr(_INPUT_ITEM)),
        "tools": _Arr(_TOOL),
        "tool_choice": _Either(
            string=_Enum(TOOL_CHOICE_STRINGS),
            mapping=_Obj({"type": _Enum(TOOL_CHOICE_TYPES), "name": _KnownLabel(CODEX_TOOL_NAMES)}),
        ),
        "parallel_tool_calls": _Scalar(),
        "reasoning": _Obj(
            {
                "effort": _Enum(REASONING_EFFORTS),
                "summary": _Enum(REASONING_SUMMARIES),
                "context": _Enum(REASONING_CONTEXTS),
            }
        ),
        "text": _Obj(
            {
                "verbosity": _Enum(TEXT_VERBOSITIES),
                "format": _Obj(
                    {
                        "type": _Enum(TEXT_FORMAT_TYPES),
                        "name": _Text(),
                        "description": _Text(),
                        "schema": _SCHEMA,
                        "strict": _Scalar(),
                    }
                ),
            }
        ),
        "include": _Arr(_Enum(INCLUDE_VALUES)),
        "store": _Scalar(),
        "stream": _Scalar(),
        "truncation": _Enum(TRUNCATIONS),
        "max_output_tokens": _Scalar(),
        "temperature": _Scalar(),
        "top_p": _Scalar(),
        "metadata": _OpenMap(_Text()),
        "user": _Ident("user"),
        "safety_identifier": _Ident("safety"),
        "prompt_cache_key": _Ident("", fixed_ordinal=0),
        "prompt_cache_retention": _Enum(CACHE_RETENTIONS),
        "previous_response_id": _Ident("resp"),
        "conversation": _Either(string=_Ident("conv"), mapping=_Obj({"id": _Ident("conv")})),
        "prompt": _Obj({"id": _Ident("prompt"), "version": _Text(), "variables": _OpenMap(_Text())}),
        "service_tier": _Enum(SERVICE_TIERS),
        # Codex-emitted, and the load-bearing evidence that a websocket body is
        # shaped differently: the turn frame carries ``generate: null`` while the
        # prewarm frame carries ``false``.
        "generate": _Scalar(),
        STREAM_OPTIONS_FIELD: _Obj(
            {"include_obfuscation": _Scalar()},
            dropped=dict.fromkeys(SANITISED_STREAM_OPTIONS_KEYS, "telemetry_field_dropped"),
        ),
    },
    dropped={
        **dict.fromkeys(DROPPED_TOP_LEVEL_FIELDS, "telemetry_field_dropped"),
        **dict.fromkeys(WEBSOCKET_ENVELOPE_FIELDS, "websocket_envelope_dropped"),
    },
)

ALLOWED_TOP_LEVEL_FIELDS: frozenset[str] = frozenset(BODY.fields) | frozenset(BODY.dropped)

# Never dropped and never fabricated: the field's shape reaches the fixture.
# ``stream_options`` is excluded because it is dropped once the Codex key
# empties it, exactly as production does.
SHAPE_PRESERVED_TOP_LEVEL_FIELDS: frozenset[str] = frozenset(BODY.fields) - {STREAM_OPTIONS_FIELD}

# Per-item-type key allowlists, exposed so the fixture gate can pin them as
# supersets of production's ``_ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS``.
ITEM_FIELDS: dict[str, frozenset[str]] = {
    name: frozenset(rule.fields) for name, rule in _INPUT_ITEM.cases.items() if isinstance(rule, _Obj)
}
CONTENT_PART_FIELDS: dict[str, frozenset[str]] = {
    name: frozenset(rule.fields) for name, rule in _CONTENT_PART.cases.items() if isinstance(rule, _Obj)
}


def structural_vocabulary() -> frozenset[str]:
    """Every string the rule tree may emit that was not synthesised.

    The allowlist, as a set. ``surviving_captured_strings`` and the gate test use
    it to decide whether a string in the output could have come from the
    capture; deriving it from ``BODY`` rather than retyping it is what stops the
    two drifting.
    """

    return frozenset(BODY.vocabulary())


def sanitize_body(
    body: Mapping[str, Any],
    *,
    placeholders: Placeholders = PLACEHOLDERS,
    strip_item_ids: bool = False,
) -> tuple[dict[str, Any], list[Redaction]]:
    """Rebuild ``body`` from the rule tree, and return it with the redaction log.

    Raises ``UnsanitisableBodyError`` for any node no rule describes -- an
    unreviewed field at any depth, an unknown item or tool type, a discriminator
    outside its vocabulary, a number outside the reviewed bound.

    This function does not run ``surviving_captured_strings`` on its own output.
    The CLI does, before it writes, and the corpus gate does, over the committed
    bodies; a programmatic caller that wants the check must ask for it.
    """

    run = _Run(placeholders=placeholders, strip_item_ids=strip_item_ids)
    rebuilt: dict[str, Any] = BODY.apply(dict(body), "", run)
    captured_options = body.get(STREAM_OPTIONS_FIELD)
    rebuilt_options = rebuilt.get(STREAM_OPTIONS_FIELD)
    if isinstance(captured_options, dict) and captured_options and rebuilt_options == {}:
        del rebuilt[STREAM_OPTIONS_FIELD]
    return rebuilt, run.redactions


# --- the inversion, verified --------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z0-9_@~./:\\-]{3,}")
_PLACEHOLDER_IDENTIFIER = re.compile(r"\A(?:[a-z_]+_)?00000000-0000-4000-8000-\d{12}\Z")


def is_placeholder_identifier(value: str) -> bool:
    """Whether ``value`` is a minted identifier (``msg_<placeholder uuid>``)."""

    return bool(_PLACEHOLDER_IDENTIFIER.match(value))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, entry in value.items():
            yield str(key)
            yield from _strings(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from _strings(entry)
    elif isinstance(value, str):
        yield value


def surviving_captured_strings(captured: Mapping[str, Any], sanitised: Mapping[str, Any]) -> list[str]:
    """Strings the capture authored that reach ``sanitised`` outside the allowlist.

    The inversion's own proof, and cheap enough to run on every sanitisation:
    both trees are walked, and every string the capture carried -- key or value
    -- that the allowlist does not name is looked for in the output, whole and
    token by token. An empty list means no captured string outside the allowlist
    survived. It is asserted as a test *and* enforced by the CLI, because a proof
    that only runs in CI does not stop a hand-run sanitisation.

    It walks strings only, and only tokens of three characters or more in the
    ASCII path/identifier alphabet. Two structural channels are therefore outside
    it and are capped elsewhere instead: numbers, which ``_Scalar`` bounds and
    ``_SchemaNode`` replaces outright, and cardinality plus ordering (how many
    items, how many properties, which property sorts first), which are the
    structure the verdict reads and which a human sees in the diff.

    Four things are allowed through by name: the structural vocabulary, the
    tokens of this module's own path-derived placeholders, the placeholder
    identifier namespace, and the model slug (which the corpus gate pins against
    provenance and the committed catalog, because a shape is not a domain).
    """

    allowed = set(structural_vocabulary())
    model = sanitised.get("model")
    if isinstance(model, str):
        allowed.add(model)
    emitted = list(_strings(sanitised))
    for value in emitted:
        if is_synthetic_text(value):
            allowed.add(value)
            allowed.update(_TOKEN.findall(value))
    # Tokens of the emitted *strings*, not of the serialised document: a JSON
    # literal (``true``) and a punctuation run are the encoder's, not the
    # capture's, and counting them made every body report itself.
    emitted_blob = "\n".join(emitted)
    emitted_tokens = {token for value in emitted for token in _TOKEN.findall(value)}
    survivors: list[str] = []
    for value in dict.fromkeys(_strings(captured)):
        if not value or value in allowed or is_placeholder_identifier(value):
            continue
        if any(character.isspace() for character in value) and value in emitted_blob:
            survivors.append(value)
        survivors.extend(
            token for token in {value, *_TOKEN.findall(value)} if token not in allowed and token in emitted_tokens
        )
    return sorted(dict.fromkeys(survivors))


# --- header sidecar ------------------------------------------------------------------

# Header names a sidecar never keeps. ``x-codex-turn-metadata`` is a JSON string
# embedding installation/session/thread/window/turn identifiers, so the whole
# ``x-codex-*`` family goes by prefix rather than by name.
DROPPED_HEADERS: frozenset[str] = frozenset(
    {
        "api-key",
        "authorization",
        "chatgpt-account-id",
        "cookie",
        "openai-organization",
        "openai-project",
        "originator",
        "proxy-authorization",
        "session-id",
        "set-cookie",
        "thread-id",
        "x-api-key",
        "x-client-request-id",
    }
)
DROPPED_HEADER_PREFIXES: tuple[str, ...] = ("x-codex-",)
USER_AGENT_HEADER = "user-agent"


def sanitize_headers(headers: Mapping[str, str]) -> tuple[dict[str, str], list[Redaction]]:
    """Drop credential and identifier headers; keep the ``user-agent`` version only.

    Unlike the body, this is a *denylist*, and it is deliberately not a commit
    boundary: the sidecar is an operator aid for reading a capture and is never
    committed. Even a disposable token leaves an ``authorization`` line shape in
    the raw file, and the fixture corpus reads bodies only.
    """

    sanitised: dict[str, str] = {}
    redactions: list[Redaction] = []
    for name in sorted(headers):
        lowered = name.casefold()
        if lowered in DROPPED_HEADERS or lowered.startswith(DROPPED_HEADER_PREFIXES):
            redactions.append(Redaction(lowered, "header_dropped"))
            continue
        if lowered == USER_AGENT_HEADER:
            # ``codex_cli_rs/0.154.0 (Ubuntu 24.04; x86_64) tmux`` -> the version only.
            leading = headers[name].split(" ", 1)[0]
            if leading != headers[name]:
                redactions.append(Redaction(lowered, "user_agent_tail_dropped"))
            sanitised[lowered] = leading
            continue
        sanitised[lowered] = headers[name]
    return sanitised, redactions


# --- CLI ------------------------------------------------------------------------------

CORPUS_DIRECTORY = "tests/fixtures/codex_bodies"
REVIEW_FLAG = "--i-have-read-the-sanitised-body"


def writes_into_the_corpus(destination: Path, *, repo_root: Path | None = None) -> bool:
    """Whether ``destination`` lands anywhere under the committed fixture corpus.

    The whole subtree, not the directory itself: a comparison against
    ``destination.parent`` let ``tests/fixtures/codex_bodies/sub/x.json`` past
    both the acknowledgement and the sidecar refusal.

    Judged **lexically as well as after resolution**. ``atomic_write_text``
    finishes with ``os.replace(temporary, destination)``, which acts on the
    lexical path, so a symlink sitting in the corpus and pointing outside it is
    replaced by a real file *inside* the corpus while ``resolve()`` reports the
    destination as elsewhere. A path that is in the corpus by either reading is
    in the corpus.
    """

    root = Path(__file__).resolve().parents[2] if repo_root is None else repo_root
    corpus = (root / CORPUS_DIRECTORY).resolve()
    lexical = Path(os.path.abspath(destination))
    return lexical.is_relative_to(corpus) or destination.resolve().is_relative_to(corpus)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild a captured Codex request body as a fixture.")
    parser.add_argument("--in", dest="source", type=Path, required=True, help="Captured body JSON")
    parser.add_argument("--out", dest="destination", type=Path, required=True, help="Sanitised fixture JSON")
    parser.add_argument("--headers-in", type=Path, help="Captured headers sidecar (reviewed, never committed)")
    parser.add_argument("--headers-out", type=Path, help="Sanitised headers sidecar")
    parser.add_argument("--strip-item-ids", action="store_true", help="Remove input-item ids instead of replacing them")
    parser.add_argument("--emit-redactions", type=Path, help="Write the redaction log as JSON")
    parser.add_argument(
        REVIEW_FLAG,
        dest="reviewed",
        action="store_true",
        help=(
            f"Acknowledge that you have read the sanitised body. Required to write into {CORPUS_DIRECTORY}: "
            "no tool can review a fixture for you, and human diff review is the boundary."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from scripts.traffic_analysis.fixture_privacy_scan import compare_captured_and_sanitised

    args = build_parser().parse_args(argv)
    body = read_json(args.source)
    if not isinstance(body, dict):
        print(f"Captured body must be a JSON object: {args.source}", file=sys.stderr)
        return 2
    if writes_into_the_corpus(args.destination) and not args.reviewed:
        print(
            f"Refusing to write into {CORPUS_DIRECTORY} without {REVIEW_FLAG}.\n"
            "Rebuild to a scratch path first, read the result, then re-run with the flag.",
            file=sys.stderr,
        )
        return 2
    # Only the rebuilt body may ever land in the corpus, and only with the
    # acknowledgement above. The header sidecar in particular is denylisted, not
    # rebuilt, so a ``--headers-out`` pointed at the corpus would walk operator
    # bytes straight past the boundary this file is about.
    for flag, sidecar in (("--headers-out", args.headers_out), ("--emit-redactions", args.emit_redactions)):
        if sidecar is not None and writes_into_the_corpus(sidecar):
            print(
                f"Refusing to write {flag} into {CORPUS_DIRECTORY}: only the rebuilt body belongs there.",
                file=sys.stderr,
            )
            return 2
    try:
        sanitised, redactions = sanitize_body(body, strip_item_ids=args.strip_item_ids)
    except UnsanitisableBodyError as exc:
        print(f"Refusing to sanitise {args.source}: {exc}", file=sys.stderr)
        return 2

    survivors = surviving_captured_strings(body, sanitised)
    comparison = compare_captured_and_sanitised(body, sanitised)
    print(f"captured body: {', '.join(comparison['captured_kinds']) or 'no residual-scan kinds'}")
    print(f"rebuilt body:  {', '.join(comparison['residual_kinds']) or 'no residual-scan kinds'}")
    if comparison["cleared_kinds"]:
        print(
            "  kinds the capture carried and the rebuild does not: "
            f"{', '.join(comparison['cleared_kinds'])} -- evidence about the rebuild, never a clearance."
        )
    if survivors or comparison["residual_kinds"]:
        reasons = []
        if survivors:
            reasons.append(f"{len(survivors)} captured string(s) survived the allowlist")
        if comparison["residual_kinds"]:
            reasons.append(f"residual scan kinds {comparison['residual_kinds']}")
        print(f"Refusing to write {args.destination}: {'; '.join(reasons)}", file=sys.stderr)
        return 2

    atomic_write_json(args.destination, sanitised)
    if args.headers_in and args.headers_out:
        raw_headers = read_json(args.headers_in)
        if isinstance(raw_headers, dict):
            sanitised_headers, header_redactions = sanitize_headers(raw_headers)
            atomic_write_json(args.headers_out, sanitised_headers)
            redactions.extend(header_redactions)
    kinds = sorted({redaction.kind for redaction in redactions})
    if args.emit_redactions:
        atomic_write_json(
            args.emit_redactions,
            {
                "schema_version": 1,
                "source": args.source.name,
                "strip_item_ids": args.strip_item_ids,
                "captured_scan_kinds": comparison["captured_kinds"],
                "residual_scan_kinds": comparison["residual_kinds"],
                "redactions": [{"path": item.path, "kind": item.kind} for item in redactions],
            },
        )
    print(f"Rebuilt {args.source} -> {args.destination}: {len(redactions)} redaction(s), kinds={kinds}")
    print("Next: read the written body, then fixture_privacy_scan.py --root <dir> --strict,")
    print("then add a provenance.json entry and a README row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
