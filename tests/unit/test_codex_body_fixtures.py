"""The Codex request-body fixture corpus gate.

Provenance-agnostic: every assertion is driven by ``provenance.json`` rather
than by a hard-coded expectation per file, so adding a captured body is a data
change. Three jobs:

* **Shape.** Every fixture validates through production's own dispatch and
  looks like a Codex Responses body.
* **Sanitiser round-trip.** Rebuilding a committed fixture through
  ``codex_body_sanitize`` reproduces the same structure, host-independently,
  and nothing the capture authored survives the rebuild.
* **Sync.** Files on disk, ``provenance.json`` rows and ``README.md`` rows
  agree in both directions, and the privacy gate passes.

Assertion messages always carry the provenance, because "fixture X is the
wrong shape" is meaningless without knowing which client version produced it.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
import socket
from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest

from app.core.types import JsonValue
from app.modules.model_sources.projection import (
    STRIPPED_STREAM_OPTIONS_KEYS,
    STRIPPED_TELEMETRY_FIELDS,
    strip_source_telemetry,
)
from app.modules.proxy.api import _has_openai_responses_shape
from app.modules.proxy.replay_safety import (
    _ACCOUNT_NEUTRAL_CONTENT_FIELDS,
    _ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS,
    _ACCOUNT_NEUTRAL_MESSAGE_FIELDS,
)
from app.modules.proxy.request_policy import normalize_responses_request_payload
from scripts.traffic_analysis import codex_body_sanitize, fixture_privacy_scan
from scripts.traffic_analysis.codex_body_capture import DEFAULT_CATALOG
from scripts.traffic_analysis.codex_body_sanitize import (
    DROPPED_TOP_LEVEL_FIELDS,
    SANITISED_STREAM_OPTIONS_KEYS,
    SHAPE_PRESERVED_TOP_LEVEL_FIELDS,
    STREAM_OPTIONS_FIELD,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex_bodies"
PROVENANCE_NAME = "provenance.json"
README_NAME = "README.md"

# Top-level Responses fields a Codex request body may carry. Spelled here rather
# than imported: this is the corpus contract (nothing fabricated beyond what a
# real client sends), not a production allowlist.
_CODEX_BODY_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "include",
        "store",
        "stream",
        "truncation",
        "max_output_tokens",
        "temperature",
        "top_p",
        "metadata",
        "user",
        "safety_identifier",
        "prompt_cache_key",
        "prompt_cache_retention",
        "previous_response_id",
        "conversation",
        "prompt",
    }
)

# Fields a fixture may carry beyond a stripped body: the Codex telemetry a
# pre-strip fixture exists to feed to ``strip_source_telemetry``.
_PRE_STRIP_FIELDS = STRIPPED_TELEMETRY_FIELDS | {"stream_options", "service_tier"}

# The brief's byte-preserve list: the sanitiser must never drop or fabricate any
# of these (drift guard).
_MUST_SURVIVE_SANITISATION = frozenset(
    {
        "instructions",
        "tools",
        "input",
        "reasoning",
        "include",
        "text",
        "store",
        "stream",
        "parallel_tool_calls",
        "tool_choice",
    }
)

_README_ROW = re.compile(
    r"^\|\s*`(?P<name>[A-Za-z0-9_.-]+\.json)`\s*\|"
    r"\s*(?P<origin>captured|synthetic)\s*\|"
    r"\s*`?(?P<slug>[^|`]+?)`?\s*\|"
    r"\s*(?P<transport>[^|]+?)\s*\|"
    r"\s*(?P<captured_at>[^|]+?)\s*\|"
    r"\s*(?P<codex_version>[^|]+?)\s*\|"
)

# Production's own per-item key tables, so the structural allowlist cannot fall
# behind what the replay predicate validates.
_PRODUCTION_ITEM_FIELDS = _ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS
_PRODUCTION_CONTENT_FIELDS = _ACCOUNT_NEUTRAL_CONTENT_FIELDS
_PRODUCTION_MESSAGE_FIELDS = _ACCOUNT_NEUTRAL_MESSAGE_FIELDS


def _provenance() -> dict[str, dict[str, Any]]:
    payload = json.loads((FIXTURES / PROVENANCE_NAME).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return cast(dict[str, dict[str, Any]], payload["fixtures"])


PROVENANCE = _provenance()
FIXTURE_NAMES = sorted(PROVENANCE)


def _body_files() -> list[str]:
    return sorted(path.name for path in FIXTURES.glob("*.json") if path.name != PROVENANCE_NAME)


def _load(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _label(name: str) -> str:
    entry = PROVENANCE[name]
    return (
        f"{name} [{entry['origin']}, slug={entry['model_slug']}, "
        f"codex={entry['codex_version'] or 'n/a'}, transport={entry['transport']}]"
    )


def _stripped_body(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """The body as a model source sees it, through production's own dispatch.

    ``normalize_responses_request_payload`` with ``openai_compat`` from
    ``_has_openai_responses_shape`` is the *only* correct entry point: a real
    Responses-Lite body has no ``instructions`` key, so bare
    ``ResponsesRequest.model_validate`` fails with ``instructions Field
    required`` and the gate would red-line for the wrong reason the day a
    capture lands.
    """

    payload = normalize_responses_request_payload(dict(body), openai_compat=_has_openai_responses_shape(body))
    return strip_source_telemetry(payload.model_dump_for_forwarding())


def _stripped(name: str) -> dict[str, JsonValue]:
    return _stripped_body(_load(name))


def _structure(value: JsonValue) -> JsonValue:
    """``value`` with every leaf replaced by its type name; keys and arity kept."""

    if isinstance(value, dict):
        return {key: _structure(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_structure(item) for item in value]
    return type(value).__name__


def _node_skeleton(node: JsonValue) -> object:
    """Discriminators, id presence and nested arity of one item, tool or content part."""

    if not isinstance(node, dict):
        return type(node).__name__
    shape: dict[str, object] = {
        "type": node.get("type") if isinstance(node.get("type"), str) else None,
        "role": node.get("role") if isinstance(node.get("role"), str) else None,
        "has_id": bool(node.get("id")),
    }
    for key in ("content", "tools"):
        nested = node.get(key)
        if isinstance(nested, list):
            shape[key] = [_node_skeleton(part) for part in nested]
    return shape


def _skeleton(body: dict[str, JsonValue]) -> object:
    """The request skeleton the rebuild must preserve exactly.

    Free-form *keys* are prose too -- a tool's JSON-Schema property names are
    renamed by the rebuild, like its descriptions -- so the skeleton is the part
    that is never operator text: the top-level field set, and the discriminator,
    id presence and nested arity of every input item and tool declaration.
    """

    def branch(key: str) -> object:
        value = body.get(key)
        return [_node_skeleton(node) for node in value] if isinstance(value, list) else _structure(value)

    return {"top_level": sorted(body), "input": branch("input"), "tools": branch("tools")}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_validates_through_the_production_dispatch(name: str) -> None:
    body = _load(name)

    payload = normalize_responses_request_payload(dict(body), openai_compat=_has_openai_responses_shape(body))

    assert payload.model == body["model"], _label(name)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_is_shaped_like_a_codex_responses_body(name: str) -> None:
    body = _load(name)
    label = _label(name)

    assert isinstance(body["model"], str) and body["model"], label
    input_items = body["input"]
    assert isinstance(input_items, list) and input_items, label
    for item in input_items:
        assert isinstance(item, dict), label
        for field in ("type", "role"):
            if field in item:
                assert isinstance(item[field], str), f"{label}: {field}"
    assert body["store"] is False, label
    assert body["stream"] is True, label
    assert body["include"] == ["reasoning.encrypted_content"], label
    assert isinstance(body["reasoning"], dict), label
    # Exactly one tool surface: the standard ``tools`` array or the Lite bundle.
    has_tools = "tools" in body
    has_bundle = any(isinstance(item, dict) and item.get("type") == "additional_tools" for item in input_items)
    assert has_tools != has_bundle, f"{label}: tools={has_tools} additional_tools={has_bundle}"
    # No field production has never seen; nothing fabricated beyond the corpus contract.
    assert set(body) <= _CODEX_BODY_FIELDS | _PRE_STRIP_FIELDS, label


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_telemetry_presence_matches_the_declared_fixture_role(name: str) -> None:
    """A rebuilt fixture carries no telemetry; a pre-strip fixture must carry it.

    Nested as well as whole-field. Checking only ``DROPPED_TOP_LEVEL_FIELDS``
    let a fixture declaring ``carries_client_telemetry: false`` keep
    ``stream_options.reasoning_summary_delivery``: the residual scan has no
    vocabulary for that key, the shape gate admits ``stream_options``, and the
    verdict is computed after ``strip_source_telemetry`` removes it -- so the
    whole corpus gate accepted exactly the nested telemetry the OpenSpec
    scenario says must fail.
    """

    body = _load(name)
    entry = PROVENANCE[name]
    carries = bool(entry["carries_client_telemetry"])
    stream_options = body.get(STREAM_OPTIONS_FIELD)

    present = sorted(field for field in DROPPED_TOP_LEVEL_FIELDS if field in body)
    if isinstance(stream_options, dict):
        nested = SANITISED_STREAM_OPTIONS_KEYS & set(stream_options)
        present += sorted(f"{STREAM_OPTIONS_FIELD}.{key}" for key in nested)

    assert bool(present) == carries, f"{_label(name)}: declared {carries}, found {present}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_strips_to_a_telemetry_free_body(name: str) -> None:
    """The stripped body a source would see carries no Codex telemetry and keeps the request shape.

    The fixture's declared role decides what it starts with; what it must end
    with is the same either way, so a pre-strip fixture and a rebuilt capture
    are held to one contract.
    """

    label = _label(name)
    stripped = _stripped(name)

    assert not STRIPPED_TELEMETRY_FIELDS & set(stripped), label
    stream_options = stripped.get(STREAM_OPTIONS_FIELD)
    if isinstance(stream_options, dict):
        assert not STRIPPED_STREAM_OPTIONS_KEYS & set(stream_options), label
    assert stripped["model"] == _load(name)["model"], label
    assert isinstance(stripped["input"], list) and stripped["input"], label


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_body_names_the_slug_its_provenance_records(name: str) -> None:
    """The model slug is the one operator-chosen token the rebuild preserves verbatim.

    ``MODEL_SLUG`` is a *shape*, not a closed domain: 64 characters of
    ``[A-Za-z0-9._-]`` will hold an internal codename, a customer name or a
    private build tag, and neither the residual scan (no ``@``, no ``/``) nor
    ``surviving_captured_strings`` (which allows the emitted slug by name) can
    see it. This assertion is what closes it, together with the catalog pin
    below: the committed body must name the slug provenance declares, and for a
    captured body that slug must be one the committed reference catalog serves.
    """

    body = _load(name)

    assert body["model"] == PROVENANCE[name]["model_slug"], _label(name)


def test_the_committed_catalog_is_the_one_every_captured_fixture_records() -> None:
    """Reproducibility, verified rather than asserted in prose.

    ``--catalog`` used to be a required flag with no documented source, no
    schema and no committed sample: the file whose digest provenance records
    lived outside the repository, so no other operator could reproduce a capture
    or check the recorded digest. The capture command now defaults to this file,
    and this test is what keeps the two in step.
    """

    digest = hashlib.sha256(DEFAULT_CATALOG.read_bytes()).hexdigest()
    catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    captured = {name for name, entry in PROVENANCE.items() if entry["origin"] == "captured"}

    assert captured
    for name in sorted(captured):
        assert PROVENANCE[name]["catalog_sha256"] == digest, _label(name)
        assert PROVENANCE[name]["model_slug"] in {model["slug"] for model in catalog["models"]}, _label(name)


def test_a_captured_body_carries_its_client_version_and_source_minted_item_ids() -> None:
    """A capture is only evidence if it says which client produced it and keeps the wire's own ids.

    The item ids are the structural fact the sweep turned on: real Codex
    traffic carries a prefixed, account-scoped ``id`` on every input item, and
    the rebuild preserves it (the prose around it is synthetic).
    """

    captured = {name for name, entry in PROVENANCE.items() if entry["origin"] == "captured"}
    assert captured, "the corpus must contain at least one captured body"

    for name in sorted(captured):
        entry = PROVENANCE[name]
        assert entry["codex_version"], _label(name)
        assert entry["catalog_sha256"], _label(name)
        items = _load(name)["input"]
        assert isinstance(items, list), _label(name)
        assert any(isinstance(item, dict) and item.get("id") for item in items), _label(name)


# --- cross-pins against the production constants -------------------------------------


def test_the_sanitiser_field_sets_are_pinned_against_production() -> None:
    """Drift guard: the sanitiser must remove at least what production removes."""

    assert DROPPED_TOP_LEVEL_FIELDS >= STRIPPED_TELEMETRY_FIELDS
    assert SANITISED_STREAM_OPTIONS_KEYS == STRIPPED_STREAM_OPTIONS_KEYS
    # Fields production forwards to a source must never be dropped or fabricated.
    assert SHAPE_PRESERVED_TOP_LEVEL_FIELDS >= _MUST_SURVIVE_SANITISATION
    assert not DROPPED_TOP_LEVEL_FIELDS & SHAPE_PRESERVED_TOP_LEVEL_FIELDS


# --- corpus sync ---------------------------------------------------------------------


def test_files_on_disk_and_provenance_rows_agree_in_both_directions() -> None:
    assert _body_files() == FIXTURE_NAMES


def test_readme_rows_and_provenance_rows_agree_in_both_directions() -> None:
    """Every column the row can be checked against, not only the origin.

    The regex used to capture ``name`` and ``origin`` alone, so the slug, the
    transport, the capture date and the CLI version could say anything. The
    sanitisation column is prose and stays unpinned; it is reviewed by a human.
    """

    readme = (FIXTURES / README_NAME).read_text(encoding="utf-8")

    rows = {match.group("name"): match for line in readme.splitlines() if (match := _README_ROW.match(line))}

    assert sorted(rows) == FIXTURE_NAMES
    for name, row in sorted(rows.items()):
        entry = PROVENANCE[name]
        assert row.group("origin") == entry["origin"], f"{_label(name)}: README says {row.group('origin')}"
        assert row.group("slug") == entry["model_slug"], f"{_label(name)}: README says {row.group('slug')}"
        assert row.group("transport") == entry["transport"], f"{_label(name)}: README says {row.group('transport')}"
        expected_date = (entry["captured_at"] or "")[:10] or "—"
        assert row.group("captured_at") == expected_date, f"{_label(name)}: README says {row.group('captured_at')}"
        expected_version = entry["codex_version"] or "—"
        assert row.group("codex_version") == expected_version, (
            f"{_label(name)}: README says {row.group('codex_version')}"
        )


def test_the_fixture_privacy_gate_passes() -> None:
    """Pre-strip fixtures declare themselves *in provenance*; nothing else is exempt.

    No ``allow_telemetry_keys`` argument, because the declaration lives in
    ``provenance.json`` and the gate reads it there. ``provenance.json`` is
    exempted from the bare-telemetry-key kind for the same reason the README is
    prose: it *names* the fields the rebuild removes. Every value-shaped kind
    (a live UUID, a workspace path, an email address, an operator identity
    recognised by shape, a credential shape) still applies to it.
    """

    report = fixture_privacy_scan.scan_fixture_tree(FIXTURES)

    assert report["passed"] is True, report["findings"]
    assert report["bodies_scanned"] == len(FIXTURE_NAMES) + 1  # + provenance.json
    exempt = {name for name, entry in PROVENANCE.items() if entry["carries_client_telemetry"]}
    assert set(report["telemetry_key_exemptions"]) == exempt | {PROVENANCE_NAME}


def test_the_documented_privacy_gate_command_passes_verbatim() -> None:
    """The runbook step's argv, in process.

    It used to exit 2 on a pristine checkout (three ``telemetry_field``
    findings) and only passed with three undocumented ``--allow-telemetry-keys``
    values that existed nowhere outside the unit tests.
    """

    assert fixture_privacy_scan.main(["--root", str(FIXTURES), "--strict"]) == 0


# Plausible host and account names, none of them exotic: cloud-image and
# container defaults, hardware and OS names, project words, and the words a
# Codex payload's own English prose uses. Every one of them red-lined the
# pristine corpus while the identity pass matched a bare word against a body
# that is mostly natural-language text, and none of them could be cleared.
_PLAUSIBLE_HOST_NAMES = (
    "alpha", "apply", "assistant", "beta", "call", "claw", "cloud", "cluster", "code", "command", "content",
    "context", "core", "data", "delta", "desktop", "developer", "edit", "exec", "false", "file", "home", "image",
    "jane", "laptop", "linux", "list", "macbook", "macos", "message", "node", "office", "open", "output", "patch",
    "path", "pixel", "plan", "prime", "read", "repo", "sandbox", "server", "session", "studio", "system", "task",
    "thinkpad", "titan", "tower", "true", "turn", "vault", "wait", "write", "wsl",
)  # fmt: skip


@pytest.mark.parametrize("identity", _PLAUSIBLE_HOST_NAMES)
def test_the_documented_gate_command_passes_whatever_the_host_is_called(
    identity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine-independence, measured through the ambient sources themselves.

    Patched on `socket` and `getpass` rather than on this package, so the claim
    survives a future pass that reads the machine some other way: what is
    asserted is that the runbook step exits 0 no matter what the box is called.
    """

    monkeypatch.setattr(socket, "gethostname", lambda: identity)
    monkeypatch.setattr(getpass, "getuser", lambda: identity)

    assert fixture_privacy_scan.main(["--root", str(FIXTURES), "--strict"]) == 0, identity


@pytest.mark.parametrize("identity", _PLAUSIBLE_HOST_NAMES)
def test_the_gate_still_reports_a_planted_path_whatever_the_host_is_called(
    identity: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive control for the test above, which is an absence assertion.

    A scan that had stopped finding anything would pass all 56 identity cases
    above, so one planted path is scanned under the same 56 and must still be
    reported.
    """

    monkeypatch.setattr(socket, "gethostname", lambda: identity)
    monkeypatch.setattr(getpass, "getuser", lambda: identity)
    (tmp_path / "candidate.json").write_text(
        json.dumps({"input": [{"type": "function_call", "arguments": "cd /srv/acme/ledger && ls"}]}),
        encoding="utf-8",
    )

    report = fixture_privacy_scan.scan_fixture_tree(tmp_path)

    assert report["findings"] == [{"path": "candidate.json", "kinds": ["operator_path"]}], identity


@cache
def _expected_lite_rebuild() -> str:
    """The rebuild of one dirtied fixture, computed once under the real host name."""

    dirty = _dirtied(_load("captured_gpt56sol_lite_http.json"))
    return json.dumps(codex_body_sanitize.sanitize_body(dirty)[0], sort_keys=True)


@pytest.mark.parametrize("identity", ["exec", "repo", "content", "true"])
def test_sanitising_a_committed_fixture_does_not_depend_on_the_host_name(
    identity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: the *output* must not depend on who ran the sanitiser.

    On a host called `exec` the bare-word pass rewrote Codex's own
    `functions.exec` tool namespace to the account placeholder, so two operators
    sanitising the same capture produced different fixtures.
    """

    monkeypatch.setattr(socket, "gethostname", lambda: identity)
    monkeypatch.setattr(getpass, "getuser", lambda: identity)
    # A *dirtied* body, so the rebuild has work to do: re-running it on the
    # committed output is idempotence, which an identity sanitiser also passes.
    dirty = _dirtied(_load("captured_gpt56sol_lite_http.json"))

    sanitised, redactions = codex_body_sanitize.sanitize_body(dirty)

    assert redactions
    assert _OPERATOR_MARKER not in json.dumps(sanitised), identity
    assert json.dumps(sanitised, sort_keys=True) == _expected_lite_rebuild(), identity


def test_a_distinctive_operator_identity_is_still_reported(tmp_path: Path) -> None:
    """The pass is narrower but no longer ambient: a prompt names its own host."""

    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps({"input": [{"content": "jane@bespoke-build-box:~jane/ledger$ make"}]}),
        encoding="utf-8",
    )

    report = fixture_privacy_scan.scan_fixture_tree(tmp_path)

    assert report["findings"] == [{"path": "candidate.json", "kinds": ["operator_identity"]}]


def test_the_identity_shapes_are_recognised_and_the_payload_vocabulary_is_not() -> None:
    """What the shape buys the residual net: an account name reads as one, prose does not.

    Nothing rewrites these any more -- the text they live in is synthesised
    whole -- so the only claim left is that the second net still recognises
    them without red-lining the English a Codex payload is mostly made of.
    """

    recognises = fixture_privacy_scan.carries_operator_identity

    assert recognises("ssh jane@bespoke-build-box")
    assert recognises("cat ~jane/notes.md")
    assert recognises('HOSTNAME="bespoke-build-box"')
    for vocabulary in ("call functions.exec", "the repo is dirty", '"role": "developer"', '"@@ " | "@@"'):
        assert not recognises(vocabulary), vocabulary


def test_the_item_and_content_key_allowlists_cover_what_production_validates() -> None:
    """Drift guard for the structural allowlist itself.

    The rebuild refuses any key no rule names, so a key production's replay
    predicate validates and the sanitiser has never met would make a genuine
    capture unsanitisable. ``additional_tools`` carries an ``id`` on the wire
    that production's neutral set does not list, which is why these are
    supersets rather than equalities.
    """

    for item_type, fields in _PRODUCTION_ITEM_FIELDS.items():
        assert codex_body_sanitize.ITEM_FIELDS[item_type] >= fields, item_type
    for part_type, fields in _PRODUCTION_CONTENT_FIELDS.items():
        assert codex_body_sanitize.CONTENT_PART_FIELDS[part_type] >= fields, part_type
    assert codex_body_sanitize.ITEM_FIELDS["message"] >= _PRODUCTION_MESSAGE_FIELDS


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_rebuilding_a_fixture_preserves_its_request_skeleton(name: str) -> None:
    """The inversion's price, measured: the skeleton survives, prose does not.

    Replacing every free-text field would be worthless if it moved the shape the
    corpus exists to record, so the skeleton is computed twice -- on the
    committed body and on the body the rebuild produces from it -- and the two
    must agree discriminator for discriminator. The rebuild is idempotent on top
    of that: a second pass over its own output changes nothing at all, keys
    included, which is what makes the committed captures reproducible.
    """

    body = _load(name)

    rebuilt, _ = codex_body_sanitize.sanitize_body(body)
    twice, _ = codex_body_sanitize.sanitize_body(rebuilt)

    assert _skeleton(_stripped_body(rebuilt)) == _skeleton(_stripped_body(body)), _label(name)
    assert _structure(_stripped_body(twice)) == _structure(_stripped_body(rebuilt)), _label(name)


_OPERATOR_MARKER = "acme-holdings-/home/jane/ledger@build-box"


def _dirtied(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """A committed fixture with an operator marker written into every string.

    The corpus on disk is the rebuild's own output, so re-running the rebuild on
    it proves idempotence and nothing else: an identity sanitiser passes that
    assertion too. Putting the marker back into every free-text slot restores
    the thing the rebuild is actually for.
    """

    def _dirty(value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {key: _dirty(entry) for key, entry in value.items()}
        if isinstance(value, list):
            return [_dirty(entry) for entry in value]
        if isinstance(value, str) and not value.strip():
            return value
        if isinstance(value, str):
            return f"{value} {_OPERATOR_MARKER}" if value.startswith("[synthetic ") else value
        return value

    return cast(dict[str, JsonValue], _dirty(body))


@pytest.mark.parametrize("name", [name for name, entry in PROVENANCE.items() if entry["origin"] == "captured"])
def test_nothing_the_capture_authored_survives_the_rebuild(name: str) -> None:
    """The inversion, walked over both trees rather than asserted in prose.

    Every string the input carries -- object key and value alike -- that the
    structural allowlist does not name must be absent from the output, whole
    and token by token. Run on a *dirtied* copy, because the committed body is
    already the rebuild's output and asserting on that would hold for an
    identity sanitiser too.
    """

    dirty = _dirtied(_load(name))
    assert _OPERATOR_MARKER in json.dumps(dirty), _label(name)

    rebuilt, _ = codex_body_sanitize.sanitize_body(dirty)

    assert codex_body_sanitize.surviving_captured_strings(dirty, rebuilt) == [], _label(name)
    assert _OPERATOR_MARKER not in json.dumps(rebuilt), _label(name)
    assert "acme-holdings" not in json.dumps(rebuilt), _label(name)
