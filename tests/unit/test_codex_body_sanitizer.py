"""The structural allowlist, the residual net, and the planted mutations (#2123).

Five groups:

* **The inversion.** No string the capture authored reaches the rebuilt body.
  Asserted against the output bytes *and* by walking both JSON trees, over a
  body built to carry every leak shape a previous round missed -- a path immediately after a colon, an
  ``rsync`` target, a ``file://`` URL, a home directory inside a nested JSON
  string, the same path ``\\u``-escaped and base64-wrapped, an email address and
  an API-key-shaped token.
* **Preservation.** The shape the portability verdict is a function of survives
  byte for byte: discriminators, key sets, tool declaration fields, item ids and
  the call/output pairing. An absent field stays absent.
* **Refusals.** Everything the rules do not describe stops the rebuild instead
  of being copied or quietly dropped.
* **Ordering.** The residual scan reads the capture *and* the rebuild, and a
  kind that is present in one and absent in the other is labelled as evidence
  about the rebuild rather than as a pass. The measured bypass is the
  regression case.
* **Planted mutations.** Each one corrupts a ``tmp_path`` copy of the real
  corpus and asserts the gate rejects it. The committed fixtures are never
  mutated, and the gate's own assertions are invoked rather than
  re-implemented: a re-implemented assertion proves nothing about the gate that
  ships.
"""

from __future__ import annotations

import base64
import getpass
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.modules.proxy.replay_safety import _contains_account_scoped_tool_state
from scripts.traffic_analysis import codex_body_sanitize, fixture_privacy_scan
from scripts.traffic_analysis.codex_body_sanitize import (
    DROPPED_TOP_LEVEL_FIELDS,
    PLACEHOLDERS,
    SHAPE_PRESERVED_TOP_LEVEL_FIELDS,
    WEBSOCKET_ENVELOPE_FIELDS,
    Redaction,
    UnsanitisableBodyError,
    is_placeholder_uuid,
    is_synthetic_text,
    placeholder_uuid,
    sanitize_body,
    sanitize_headers,
    structural_vocabulary,
    surviving_captured_strings,
)
from tests.unit import test_codex_body_fixtures as gate

pytestmark = pytest.mark.unit

FIXTURES = gate.FIXTURES
CAPTURED_STANDARD = "captured_gpt55_standard_http.json"
CAPTURED_LITE = "captured_gpt56sol_lite_http.json"
SYNTHETIC_STANDARD = "gpt55_standard_first_turn.json"

_LIVE_SESSION = "0199f2c1-2b3d-4e5f-8a9b-1c2d3e4f5a6b"
_LIVE_TURN = "0199f2c1-3c4e-5f60-9bac-2d3e4f5a6b7c"
_LIVE_INSTALLATION = "0199f2c1-4d5f-6071-acbd-3e4f5a6b7c8d"

_HOME = "/home/jane/acme-ledger"
# Credential *shapes*, assembled at runtime rather than written as literals.
# The tests need the shape -- that is the whole point of an "API-key-shaped
# token in free text" case -- but a repository secret scanner reads the file,
# not the runtime value, and a fake credential that trips it costs a real
# review cycle every time. Splitting the literal keeps both honest.
_FAKE_API_KEY = "sk-" + "proj-" + "AbCdEf0123456789AbCdEf0123456789AbCdEf01"
_FAKE_BEARER = "sk-" + "live-" + "abcdefghijklmnop"
_FAKE_HEADER_KEY = "sk-" + "live-" + "xyz"
_FAKE_BASIC = "Basic " + "amFuZTpod" + "W50ZXIy"
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dBjftJeZ4CVP"
# An 84-digit integer whose big-endian bytes spell an operator path. Python ints
# have no width, so a numeric slot with no bound is a channel with no ceiling.
_PATH_AS_INTEGER = int.from_bytes(b"/home/jane/work/acme-secret-project", "big")
_SKILLS = (
    "<skills_instructions>\n### Skill roots\n- `r0` = `/srv/acme/private-skills`\n"
    "### Available skills\n- acme-payroll-export: Export the ACME payroll ledger.\n"
    "</skills_instructions>"
)
_ENVIRONMENT = (
    "<environment_context>\n  <cwd>/mnt/scratch/checkout</cwd>\n  <shell>zsh</shell>\n"
    "  <current_date>2026-09-11</current_date>\n  <timezone>Europe/Berlin</timezone>\n"
    "  <filesystem><workspace_roots><root>/mnt/scratch/checkout</root></workspace_roots></filesystem>\n"
    "</environment_context>"
)


def _realistic_body() -> dict[str, Any]:
    """One body carrying every leak shape and every preserve target.

    Modelled on the two captured bodies -- the skills block and the environment
    context arrive as developer/user content, the tool array mixes a function, a
    custom tool and the two declarations that make a real body decline -- plus
    the transcript shape a *second* turn has, which is where the measured
    bypasses lived.
    """

    return {
        "model": "gpt-5.5",
        "instructions": "You are Codex, a coding agent based on GPT-5.",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "id": f"msg_{_LIVE_SESSION}",
                "content": [{"type": "input_text", "text": _SKILLS}],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_kinds": ["model.base_instructions"],
                    "turn_id": _LIVE_TURN,
                },
            },
            {
                "type": "message",
                "role": "user",
                "id": f"msg_{_LIVE_TURN}",
                "content": [
                    {"type": "input_text", "text": f"# AGENTS.md instructions for {_HOME}"},
                    {"type": "input_text", "text": _ENVIRONMENT},
                    {"type": "input_text", "text": "Reviewed at jane@bespoke-build-box:~jane/checkout$ pytest."},
                ],
            },
            {
                "type": "function_call",
                "call_id": "call_a1b2c3",
                "name": "shell",
                "arguments": json.dumps({"command": ["bash", "-lc", f"cd {_HOME} && ./deploy.sh"]}),
            },
            {
                "type": "function_call_output",
                "call_id": "call_a1b2c3",
                "output": "bash: acme: command not found\nPATH=/usr/local/bin:/home/jane/.local/bin",
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Run a shell command.",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cmd": {"type": "string", "description": "Shell command to execute."},
                        "workdir": {"type": "string", "description": f"Defaults to {_HOME}."},
                    },
                    "required": ["cmd"],
                },
            },
            {"type": "custom", "name": "apply_patch", "description": "Patch", "format": {"type": "text"}},
            {"type": "tool_search", "execution": "client", "description": "# Tool discovery"},
            {"type": "web_search", "external_web_access": True, "search_content_types": ["text"]},
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        # The two open key namespaces. Codex sends neither, which is exactly why
        # they need to be here: without them nothing exercised ``_OpenMap`` and
        # a rule that kept the operator's key verbatim passed the whole suite.
        "metadata": {"acme_ledger_customer": "Acme Holdings plc"},
        "prompt": {"id": "pmpt_0199", "version": "7", "variables": {"jane_home_dir": _HOME}},
        "reasoning": {"effort": "medium"},
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "stream": True,
        "prompt_cache_key": _LIVE_SESSION,
        "client_metadata": {
            "session_id": _LIVE_SESSION,
            "thread_id": _LIVE_SESSION,
            "turn_id": _LIVE_TURN,
            "x-codex-installation-id": _LIVE_INSTALLATION,
            "x-codex-turn-metadata": json.dumps({"installation_id": _LIVE_INSTALLATION, "turn_id": _LIVE_TURN}),
        },
        "access_programs": ["internal-preview"],
        "stream_options": {"reasoning_summary_delivery": "sequential_cutoff", "include_obfuscation": False},
    }


# --- the inversion --------------------------------------------------------------------


def _output_item(text: str) -> dict[str, Any]:
    return {
        "model": "gpt-5.5",
        "input": [{"type": "function_call_output", "call_id": "call_1", "output": text}],
    }


def _arguments_item(text: str) -> dict[str, Any]:
    return {
        "model": "gpt-5.5",
        "input": [{"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": text}],
    }


def _message(text: str) -> dict[str, Any]:
    return {
        "model": "gpt-5.5",
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}],
    }


# Every planted case, with the substrings that must not survive. The first three
# are the measured bypasses of the denylist rounds: the path sat immediately
# after a ``:``, which the path pattern's lookbehind excluded, and the rewrite
# that *did* fire deleted the residual scan's own trigger. The last four are
# encodings no pattern over bytes recognises at all.
_PLANTED_CASES: tuple[tuple[str, dict[str, Any], tuple[str, ...], list[str]], ...] = (
    (
        "colon_adjacent_path",
        _output_item("bash: acme: command not found\nPATH=/usr/local/bin:/home/jane/.local/bin"),
        ("/home/jane/.local/bin", "/usr/local/bin"),
        ["operator_path"],
    ),
    (
        "colon_adjacent_ld_library_path",
        _output_item("LD_LIBRARY_PATH=/opt/acme/lib:/home/jane/.local/lib"),
        ("/home/jane/.local/lib", "/opt/acme/lib"),
        ["operator_path"],
    ),
    (
        "rsync_account_at_host_path",
        _arguments_item(json.dumps({"command": ["rsync", "-a", "./", "deploy@acme-prod:/srv/acme/www"]})),
        ("deploy@acme-prod", "/srv/acme/www"),
        ["operator_identity", "operator_path"],
    ),
    (
        "file_url",
        _message("see file:///home/jane/notes.md"),
        ("file:///home/jane/notes.md", "/home/jane"),
        ["operator_path"],
    ),
    (
        "home_path_in_nested_json_arguments",
        _arguments_item(json.dumps({"command": ["bash", "-lc", f"cd {_HOME} && ls"], "workdir": _HOME})),
        (_HOME,),
        ["operator_path"],
    ),
    (
        "home_path_unicode_escaped",
        _output_item("cd \\u002fhome\\u002fjane\\u002facme-ledger"),
        ("\\u002fhome", "acme-ledger"),
        [],
    ),
    (
        "home_path_base64",
        _output_item("payload=" + base64.b64encode(f"cd {_HOME} && make".encode()).decode()),
        (base64.b64encode(f"cd {_HOME} && make".encode()).decode(),),
        [],
    ),
    (
        "email_address",
        _message("ping jane.doe@acme-holdings.example for the key"),
        ("jane.doe@acme-holdings.example",),
        ["email", "operator_identity"],
    ),
    (
        "api_key_shaped_token",
        _message(f"export TOKEN={_FAKE_API_KEY}"),
        (_FAKE_API_KEY,),
        [],
    ),
)


@pytest.mark.parametrize(
    ("body", "leaks", "captured_kinds"),
    [pytest.param(body, leaks, kinds, id=name) for name, body, leaks, kinds in _PLANTED_CASES],
)
def test_a_planted_leak_cannot_reach_the_rebuilt_body(
    body: dict[str, Any], leaks: tuple[str, ...], captured_kinds: list[str]
) -> None:
    """The allowlist makes every planted case moot, including the ones no pattern sees.

    Each row records what the residual scan made of the *captured* body as well,
    which is where the two encodings earn their place: the ``\\u``-escaped path
    and the base64 blob produce no finding at all, and the API-key token
    produces one only through the credential scanner over whole files. The
    rebuild does not depend on recognising any of them.
    """

    rebuilt, _ = sanitize_body(body)

    serialized = json.dumps(rebuilt)
    for leak in leaks:
        assert leak not in serialized, leak
    assert surviving_captured_strings(body, rebuilt) == []
    assert fixture_privacy_scan.body_findings(body) == captured_kinds
    assert fixture_privacy_scan.body_findings(rebuilt) == []


def test_nothing_the_capture_authored_survives_the_realistic_body() -> None:
    """The whole-tree walk, on the body that carries every shape at once."""

    body = _realistic_body()

    rebuilt, redactions = sanitize_body(body)

    assert surviving_captured_strings(body, rebuilt) == []
    assert redactions
    # Not only the detector: the bytes, for every marker the body carries.
    serialized = json.dumps(rebuilt)
    for marker in (_HOME, "acme-payroll-export", "bespoke-build-box", "Europe/Berlin", _LIVE_SESSION, "a1b2c3"):
        assert marker not in serialized, marker


@pytest.mark.parametrize(
    "secret",
    [
        _LIVE_SESSION,
        _LIVE_TURN,
        _LIVE_INSTALLATION,
        _HOME,
        "/mnt/scratch/checkout",
        "acme-payroll-export",
        "Europe/Berlin",
        "2026-09-11",
        "bespoke-build-box",
        "jane",
    ],
    ids=[
        "session_id",
        "turn_id",
        "installation_id",
        "home_path",
        "workspace_path",
        "skill_name",
        "timezone",
        "capture_date",
        "hostname",
        "account",
    ],
)
def test_no_captured_value_survives_in_the_serialized_output(secret: str) -> None:
    """Checked against the output *bytes*: nested JSON-in-a-string hides from a key walk."""

    rebuilt, _ = sanitize_body(_realistic_body())

    assert secret not in json.dumps(rebuilt), f"{secret} survived the rebuild"


def test_every_free_text_field_is_replaced_by_its_own_path() -> None:
    """The substitute is derived from the JSON path and nothing else.

    Which is what makes the rebuild deterministic across machines and across
    operators: two people sanitising the same capture produce the same file.
    """

    rebuilt, _ = sanitize_body(_realistic_body())

    assert rebuilt["instructions"] == "[synthetic instructions]"
    assert rebuilt["input"][0]["content"][0]["text"] == "[synthetic input[0].content[0].text]"
    assert rebuilt["input"][2]["arguments"] == "[synthetic input[2].arguments]"
    assert rebuilt["input"][3]["output"] == "[synthetic input[3].output]"
    assert rebuilt["tools"][0]["description"] == "[synthetic tools[0].description]"
    assert all(is_synthetic_text(value) for value in rebuilt["tools"][0]["parameters"]["properties"])


def test_an_open_namespace_replaces_its_keys_as_well_as_its_values() -> None:
    """``metadata`` and ``prompt.variables`` are the operator's on both sides.

    Every other object in the body has a closed key namespace, so a key that is
    not in the rule table refuses. These two do not: the operator chooses the
    keys, which makes the key as much of a leak as the value. Nothing else in
    the suite reached this rule, and a version that kept the captured key
    verbatim passed every test.
    """

    rebuilt, redactions = sanitize_body(_realistic_body())

    assert list(rebuilt["metadata"]) == ["[synthetic metadata.key01]"]
    assert rebuilt["metadata"]["[synthetic metadata.key01]"] == "[synthetic metadata.key01]"
    assert list(rebuilt["prompt"]["variables"]) == ["[synthetic prompt.variables.key01]"]
    assert rebuilt["prompt"]["version"] == "[synthetic prompt.version]"
    assert rebuilt["prompt"]["id"].startswith("prompt_")
    for leaked in ("acme_ledger_customer", "Acme Holdings plc", "jane_home_dir", _HOME, "pmpt_0199"):
        assert leaked not in json.dumps(rebuilt), leaked
    assert {redaction.kind for redaction in redactions} >= {"synthetic_key"}


def test_an_open_namespace_numbers_its_keys_by_sorted_order() -> None:
    """Which is what makes a rebuild of a rebuild byte-identical.

    Numbering by insertion order gave ``key10`` before ``key1`` on the second
    pass, because a fixture is written with sorted keys and read back in that
    order. The rank an operator key leaves behind is the price.
    """

    body = _realistic_body()
    body["metadata"] = {"zulu": "z", "alpha": "a", "mike": "m"}

    once, _ = sanitize_body(body)
    twice, second = sanitize_body(once)

    assert list(once["metadata"]) == [
        "[synthetic metadata.key01]",
        "[synthetic metadata.key02]",
        "[synthetic metadata.key03]",
    ]
    assert json.dumps(twice, sort_keys=True) == json.dumps(once, sort_keys=True)
    assert second == []


def test_a_replaced_string_leaves_behind_only_its_blankness_and_its_slot() -> None:
    """``_is_nonblank_string`` is read by the replay predicate; the bytes are not.

    Blankness is not the *only* residue a replaced string leaves -- a URL keeps
    its scheme, an identifier keeps its aliasing, an open key keeps its rank --
    which is why the name says "and its slot" rather than "only".
    """

    body = _realistic_body()
    body["instructions"] = ""
    body["input"][1]["content"][0]["text"] = "   "

    rebuilt, _ = sanitize_body(body)

    assert rebuilt["instructions"] == ""
    assert rebuilt["input"][1]["content"][0]["text"] == ""
    assert rebuilt["input"][1]["content"][1]["text"].strip()


# --- preservation ---------------------------------------------------------------------


def test_the_discriminators_the_verdict_reads_are_byte_identical() -> None:
    body = _realistic_body()

    rebuilt, _ = sanitize_body(body)

    assert rebuilt["model"] == "gpt-5.5"
    assert rebuilt["store"] is False and rebuilt["stream"] is True
    assert rebuilt["include"] == ["reasoning.encrypted_content"]
    assert rebuilt["reasoning"] == {"effort": "medium"}
    assert rebuilt["text"] == {"verbosity": "low"}
    assert rebuilt["tool_choice"] == "auto"
    assert rebuilt["parallel_tool_calls"] is True
    assert [item["type"] for item in rebuilt["input"]] == [item["type"] for item in body["input"]]
    assert [item.get("role") for item in rebuilt["input"]] == [item.get("role") for item in body["input"]]
    assert [sorted(item) for item in rebuilt["input"]] == [sorted(item) for item in body["input"]]


def test_the_declaration_shapes_that_cause_the_declines_are_preserved_exactly() -> None:
    """``tool_search.execution`` and the real ``web_search`` fields are the evidence."""

    rebuilt, _ = sanitize_body(_realistic_body())

    declarations = {tool["type"]: tool for tool in rebuilt["tools"]}
    assert declarations["tool_search"]["execution"] == "client"
    assert declarations["web_search"]["external_web_access"] is True
    assert declarations["web_search"]["search_content_types"] == ["text"]
    assert sorted(declarations["function"]) == ["description", "name", "parameters", "strict", "type"]
    assert declarations["function"]["name"] == "exec_command"
    assert declarations["custom"]["format"] == {"type": "text"}


def test_a_json_schema_keeps_its_arity_and_loses_its_prose() -> None:
    """Property names are renamed together with the ``required`` list that names them."""

    rebuilt, _ = sanitize_body(_realistic_body())

    schema = rebuilt["tools"][0]["parameters"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert len(schema["properties"]) == 2
    assert [value["type"] for value in schema["properties"].values()] == ["string", "string"]
    assert schema["required"] == [next(iter(schema["properties"]))]
    assert _HOME not in json.dumps(schema)


def test_a_number_inside_a_tool_schema_is_replaced_not_preserved() -> None:
    """An arbitrary-precision integer is an arbitrary-bandwidth channel.

    Python ints have no width and a JSON double carries 53 bits of mantissa, so
    a `default` or an `examples` entry in an MCP-authored schema will hold an
    encoded filesystem path that no walk over *strings* can see. No number in a
    tool schema is read by the portability decision, so none is kept.
    """

    body = _realistic_body()
    body["tools"][0]["parameters"]["properties"]["cmd"] |= {
        "default": _PATH_AS_INTEGER,
        "examples": [1771.438476294279],
        "minLength": 12,
    }

    rebuilt, redactions = sanitize_body(body)

    schema = rebuilt["tools"][0]["parameters"]["properties"]
    first = schema[next(iter(schema))]
    assert first["default"] == 0 and first["examples"] == [0] and first["minLength"] == 0
    assert str(_PATH_AS_INTEGER) not in json.dumps(rebuilt)
    assert {redaction.kind for redaction in redactions} >= {"synthetic_number"}


def test_a_preserved_number_is_bounded_and_a_boolean_is_not_touched() -> None:
    """The knobs the body really carries survive; the channel they open does not."""

    body = _realistic_body()
    body |= {"max_output_tokens": 32000, "temperature": 0.25, "top_p": 1.0}

    rebuilt, _ = sanitize_body(body)

    assert rebuilt["max_output_tokens"] == 32000
    assert rebuilt["temperature"] == 0.25 and rebuilt["top_p"] == 1.0
    assert rebuilt["store"] is False and rebuilt["parallel_tool_calls"] is True


def test_an_account_scoped_property_name_is_kept_where_it_moves_the_verdict() -> None:
    """Measured, and narrower than it looks.

    ``_contains_account_scoped_tool_state`` walks every declaration's subtree
    *except* a ``function`` tool's own ``parameters``, which it skips at the
    root. So the same property name moves the verdict under ``tool_search`` and
    does not under ``function``. The names are kept in both places because the
    rule is one line and the asymmetry is production's, not the corpus's.
    """

    declaration = {"type": "object", "properties": {"file_id": {"type": "string"}, "acme_ticket": {"type": "string"}}}
    body = _realistic_body()
    body["tools"][0]["parameters"] = dict(declaration) | {"required": ["file_id"]}
    body["tools"][2]["parameters"] = dict(declaration)

    rebuilt, _ = sanitize_body(body)

    for index in (0, 2):
        properties = rebuilt["tools"][index]["parameters"]["properties"]
        assert "file_id" in properties, index
        assert "acme_ticket" not in properties, index
    assert rebuilt["tools"][0]["parameters"]["required"] == ["file_id"]
    # The asymmetry, measured through production rather than asserted in prose.
    assert _contains_account_scoped_tool_state(rebuilt["tools"][2]) is True
    assert _contains_account_scoped_tool_state(rebuilt["tools"][0]) is False


def test_item_ids_are_replaced_not_removed_so_the_history_evidence_survives() -> None:
    """A non-empty prefixed id is what makes a native body decline as history."""

    rebuilt, redactions = sanitize_body(_realistic_body())

    identifiers = [item["id"] for item in rebuilt["input"] if "id" in item]
    assert len(set(identifiers)) == 2
    assert all(is_placeholder_uuid(value.removeprefix("msg_")) for value in identifiers), identifiers
    assert {redaction.kind for redaction in redactions} >= {"identifier_placeholder"}
    passthrough = rebuilt["input"][0]["internal_chat_message_metadata_passthrough"]
    assert is_placeholder_uuid(passthrough["turn_id"])
    assert passthrough["content_item_kinds"] == ["model.base_instructions"]


def test_a_call_and_its_output_still_name_the_same_placeholder() -> None:
    """Referential consistency is what the fresh-replay predicate pairs on."""

    rebuilt, _ = sanitize_body(_realistic_body())

    call, output = rebuilt["input"][2], rebuilt["input"][3]
    assert call["call_id"] == output["call_id"]
    assert call["call_id"].startswith("call_") and is_placeholder_uuid(call["call_id"].removeprefix("call_"))
    assert "a1b2c3" not in json.dumps(rebuilt)


def test_strip_item_ids_removes_them_for_the_stripped_variant() -> None:
    rebuilt, redactions = sanitize_body(_realistic_body(), strip_item_ids=True)

    assert all("id" not in item for item in rebuilt["input"])
    assert {redaction.kind for redaction in redactions} >= {"item_id_stripped"}


def test_telemetry_fields_go_whole_and_the_cache_key_becomes_a_placeholder() -> None:
    rebuilt, _ = sanitize_body(_realistic_body())

    assert not DROPPED_TOP_LEVEL_FIELDS & set(rebuilt)
    assert is_placeholder_uuid(rebuilt["prompt_cache_key"])
    # The Codex key goes; a standard ``include_obfuscation`` stays, so the
    # object is not dropped -- exactly production's ``strip_source_telemetry``.
    assert rebuilt["stream_options"] == {"include_obfuscation": False}


def test_stream_options_is_dropped_once_the_codex_key_empties_it() -> None:
    body = _realistic_body()
    body["stream_options"] = {"reasoning_summary_delivery": "sequential_cutoff"}

    rebuilt, _ = sanitize_body(body)

    assert "stream_options" not in rebuilt


@pytest.mark.parametrize("absent", ["instructions", "tools", "stream_options"])
def test_an_absent_field_stays_absent(absent: str) -> None:
    """A real Lite body has no ``instructions``/``tools``; a real 5.5 body has no
    ``stream_options``. ``overflow_portability_view`` declines unknown top-level
    fields, so fabricating one would change the recorded verdict."""

    body = _realistic_body()
    del body[absent]

    rebuilt, _ = sanitize_body(body)

    assert absent not in rebuilt


def test_a_lite_additional_tools_bundle_keeps_its_shape_and_loses_its_prose() -> None:
    """Codex-generated, and the load-bearing evidence for ``not_portable_lite_namespace``.

    The bundle used to be byte-preserved, which is exactly how an MCP server's
    tool description reached a fixture unrewritten. Its structure -- the
    namespace, the nested tool names, the declaration key sets -- is what the
    verdict reads, and that is all that survives.
    """

    bundle = {
        "type": "additional_tools",
        "role": "developer",
        "id": f"at_{_LIVE_SESSION}",
        "tools": [
            {
                "type": "namespace",
                "name": "collaboration",
                "description": "Agent collaboration",
                "tools": [
                    {
                        "name": "spawn_agent",
                        "description": f"If your task is `/root/task1` the checkout is `{_HOME}`.",
                    }
                ],
            }
        ],
    }
    body = _realistic_body()
    del body["tools"]
    body["input"] = [bundle]

    rebuilt, _ = sanitize_body(body)

    namespace = rebuilt["input"][0]["tools"][0]
    assert rebuilt["input"][0]["id"] == f"at_{placeholder_uuid(1)}"
    assert namespace["type"] == "namespace" and namespace["name"] == "collaboration"
    assert namespace["tools"][0]["name"] == "spawn_agent"
    assert _HOME not in json.dumps(rebuilt)
    assert surviving_captured_strings(body, rebuilt) == []


def test_a_websocket_capture_rebuilds_into_a_fixture() -> None:
    """The frame envelope goes; ``generate`` stays, because it is Codex-emitted evidence.

    A websocket capture is persisted verbatim -- the frame *is* the request body
    on that transport -- so without this the shipped lane produced a file its
    own sanitiser refused as an unreviewed top-level field.
    """

    body = _realistic_body() | {"type": "response.create", "generate": None}

    rebuilt, redactions = sanitize_body(body)

    assert "type" not in rebuilt
    assert rebuilt["generate"] is None
    assert Redaction("type", "websocket_envelope_dropped") in redactions


def test_the_rebuild_is_idempotent() -> None:
    once, _ = sanitize_body(_realistic_body())
    twice, second_redactions = sanitize_body(once)

    assert json.dumps(twice, sort_keys=True) == json.dumps(once, sort_keys=True)
    assert second_redactions == []


@pytest.mark.parametrize(
    "name",
    [name for name, entry in gate.PROVENANCE.items() if not entry["carries_client_telemetry"]],
)
def test_rebuilding_a_committed_fixture_again_changes_nothing(name: str) -> None:
    """Idempotence on the real corpus, not only on the synthetic body.

    The pre-strip fixtures are excluded because they exist *un*rebuilt, to feed
    production's telemetry stripper.
    """

    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    rebuilt, redactions = sanitize_body(body)

    assert redactions == []
    assert json.dumps(rebuilt, sort_keys=True) == json.dumps(body, sort_keys=True), name


def test_redactions_never_carry_the_removed_value() -> None:
    _, redactions = sanitize_body(_realistic_body())

    assert len(redactions) > 10
    for redaction in redactions:
        assert _LIVE_SESSION not in redaction.path and _LIVE_SESSION not in redaction.kind
        assert _HOME not in redaction.path


# --- refusals -------------------------------------------------------------------------


def test_an_unreviewed_top_level_field_fails_closed() -> None:
    body = _realistic_body()
    body["x_future_codex_field"] = {"nested": True}

    with pytest.raises(UnsanitisableBodyError, match="outside the reviewed allowlist"):
        sanitize_body(body)


@pytest.mark.parametrize(
    ("mutate", "secret"),
    [
        pytest.param(
            lambda body, name: body["input"][0].update({name: "x"}),
            "X-note-/home/jane/.ssh/id_rsa-hunter2",
            id="unreviewed_item_field",
        ),
        pytest.param(
            lambda body, name: body["tools"][0]["parameters"].update({name: "x"}),
            "$comment-/home/jane/secret",
            id="unreviewed_schema_keyword",
        ),
    ],
)
def test_a_refusal_does_not_echo_the_unreviewed_key(mutate: Any, secret: str) -> None:
    """An unreviewed key is the node most likely to be MCP-authored.

    The refusal reaches a terminal and, from there, a pasted issue comment far
    more readily than a fixture does, so it names the parent path and the
    position and stops. The operator still has the capture in front of them.
    """

    body = _realistic_body()
    mutate(body, secret)

    with pytest.raises(UnsanitisableBodyError) as raised:
        sanitize_body(body)

    assert secret not in str(raised.value)
    assert "outside the reviewed" in str(raised.value)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda body: body["input"][0].update({"x_future_item_field": "acme"}),
            "outside the reviewed allowlist",
            id="unreviewed_item_field",
        ),
        pytest.param(
            lambda body: body["input"].append({"type": "image_generation_call", "id": "ig_1"}),
            "type outside the reviewed vocabulary",
            id="unreviewed_item_type",
        ),
        pytest.param(
            lambda body: body["input"][1].update({"role": "operator"}),
            "outside the reviewed vocabulary",
            id="unreviewed_role",
        ),
        pytest.param(
            lambda body: body["tools"][0]["parameters"].update({"$ref": "#/$defs/acme"}),
            "outside the reviewed JSON Schema set",
            id="unreviewed_schema_keyword",
        ),
        pytest.param(
            lambda body: body.update({"max_output_tokens": _PATH_AS_INTEGER}),
            "number outside the reviewed bound",
            id="integer_outside_the_bound",
        ),
        pytest.param(
            lambda body: body.update({"top_p": 0.1771438476294279}),
            "number outside the reviewed bound",
            id="float_carrying_hidden_precision",
        ),
        pytest.param(
            lambda body: body.update({"temperature": float("nan")}),
            "number outside the reviewed bound",
            id="not_a_number",
        ),
        pytest.param(
            lambda body: body["input"][1]["content"].append({"type": "input_image", "image_url": "acme://ledger/1"}),
            "URL scheme outside the reviewed vocabulary",
            id="unreviewed_url_scheme",
        ),
        pytest.param(
            lambda body: body["tools"].append({"type": "mcp", "server_label": "acme"}),
            "type outside the reviewed vocabulary",
            id="unreviewed_tool_type",
        ),
        pytest.param(
            lambda body: body.update({"model": "/home/jane/models/acme.gguf"}),
            "expected a model slug",
            id="model_outside_slug_shape",
        ),
    ],
)
def test_a_node_no_rule_describes_stops_the_rebuild(mutate: Any, expected: str) -> None:
    """Refusal is the only failure mode the inversion allows.

    A dropped key would change the key set the replay predicate validates and a
    copied value would defeat the whole design, so anything unreviewed stops the
    run and names the path. The operator extends the rule table, by hand, with a
    reviewer.
    """

    body = _realistic_body()
    mutate(body)

    with pytest.raises(UnsanitisableBodyError, match=expected):
        sanitize_body(body)


def test_the_field_sets_are_closed_and_disjoint() -> None:
    assert not DROPPED_TOP_LEVEL_FIELDS & SHAPE_PRESERVED_TOP_LEVEL_FIELDS
    assert not WEBSOCKET_ENVELOPE_FIELDS & SHAPE_PRESERVED_TOP_LEVEL_FIELDS
    # The envelope is not telemetry, so it stays out of the set that is pinned
    # against production's ``STRIPPED_TELEMETRY_FIELDS``.
    assert not WEBSOCKET_ENVELOPE_FIELDS & DROPPED_TOP_LEVEL_FIELDS
    assert PLACEHOLDERS.workspace == "/workspace/repo"
    assert is_placeholder_uuid(placeholder_uuid(0)) and not is_placeholder_uuid(_LIVE_SESSION)


def test_the_allowlist_is_not_empty_and_names_what_the_verdict_reads() -> None:
    """Guards the guard: every assertion above would pass vacuously on an empty allowlist.

    An empty rule table refuses every body, so a corpus test would fail rather
    than pass -- but ``surviving_captured_strings`` would also report nothing,
    because nothing would be emitted. The vocabulary is therefore asserted to
    contain the discriminators the portability decision actually reads.
    """

    vocabulary = structural_vocabulary()

    assert {"model", "input", "tools", "instructions", "reasoning", "include", "store", "stream"} <= vocabulary
    assert {"additional_tools", "function_call", "function_call_output", "message"} <= vocabulary
    assert {"tool_search", "web_search", "custom", "function", "namespace"} <= vocabulary
    assert {"execution", "external_web_access", "search_content_types"} <= vocabulary
    assert {"developer", "user", "assistant", "reasoning.encrypted_content"} <= vocabulary
    assert "acme" not in vocabulary


def test_the_survivor_walk_reports_a_field_dropped_from_the_allowlist() -> None:
    """The mutant: a rule that preserves ``output`` instead of synthesising it.

    Planted by hand rather than by patching the rule tree, so what is asserted
    is the *detector*: if any future edit lets a captured string through, the
    walk that runs on every rebuild and in the corpus gate reports it. Without
    this, "nothing survived" would be a claim about a walk nobody had ever seen
    fail.
    """

    body = _output_item("bash: acme: command not found\nPATH=/usr/local/bin:/home/jane/.local/bin")
    rebuilt, _ = sanitize_body(body)
    assert surviving_captured_strings(body, rebuilt) == []

    mutant = json.loads(json.dumps(rebuilt))
    mutant["input"][0]["output"] = body["input"][0]["output"]

    survivors = surviving_captured_strings(body, mutant)

    assert "/usr/local/bin:/home/jane/.local/bin" in survivors


# --- ordering: the capture is scanned too ----------------------------------------------


def test_the_residual_scan_reads_the_capture_as_well_as_the_rebuild() -> None:
    """The regression case for the measured bypass, stated as an ordering rule.

    The denylist rewrote ``PATH=/usr/local/bin:/home/jane/.local/bin`` into
    ``PATH=/workspace/repo:/home/jane/.local/bin`` -- removing the *first* path,
    which was the only one the residual pattern's lookbehind could see -- and the
    scan then reported a clean body. Scanning only the sanitised side is what
    made that a pass, so the comparison reports both sides and labels the
    difference as evidence about the rebuild.
    """

    body = _output_item("bash: acme: command not found\nPATH=/usr/local/bin:/home/jane/.local/bin")
    rebuilt, _ = sanitize_body(body)

    comparison = fixture_privacy_scan.compare_captured_and_sanitised(body, rebuilt)

    assert comparison["captured_kinds"] == ["operator_path"]
    assert comparison["residual_kinds"] == []
    assert comparison["cleared_kinds"] == ["operator_path"]


def test_the_second_net_still_recognises_an_operator_path_after_a_colon() -> None:
    """A repair to the net, not the fix.

    The lookbehind excluded ``:``, so the second half of a ``PATH`` assignment
    and an ``rsync`` target were both invisible. Repairing it costs nothing and
    the boundary does not depend on it -- which is why the case above asserts the
    rebuild, not this.
    """

    findings = fixture_privacy_scan.body_findings(
        _output_item("PATH=/usr/local/bin:/home/jane/.local/bin\nrsync ./ deploy@acme:/srv/www")
    )

    assert "operator_path" in findings


def test_a_url_and_a_relative_path_are_still_not_operator_paths() -> None:
    """Widening the net did not make every slash a path.

    Paired with a positive control in the same test, because an absence
    assertion alone also passes against a net that has stopped working.
    """

    prose = (
        "See tests/unit/test_parser.py and r0/example-skill/SKILL.md, "
        "read https://example.com/docs/guide, pick cream/sand/tan, and try ../sibling/dir."
    )

    assert fixture_privacy_scan.body_findings(_message(prose)) == []
    assert fixture_privacy_scan.body_findings(_message(f"{prose} Then cd /srv/acme/ledger.")) == ["operator_path"]


def test_the_codex_agent_namespace_is_cleared_by_value_not_by_prefix(tmp_path: Path) -> None:
    """``/root/task1`` is ``spawn_agent`` vocabulary; ``/root/<repo>`` is a checkout.

    ``/root`` is the ``docker run`` and devcontainer default, so exempting the
    prefix would blind the net to exactly the workspace a containerised capture
    leaks.
    """

    bundle = {
        "type": "additional_tools",
        "tools": [
            {
                "type": "namespace",
                "tools": [{"name": "spawn_agent", "description": "Task `/root/task1` spawns `/root/task1/task_3`."}],
            }
        ],
    }
    (tmp_path / "generated.json").write_text(json.dumps({"input": [bundle]}), encoding="utf-8")

    assert fixture_privacy_scan.scan_fixture_tree(tmp_path)["passed"] is True

    (tmp_path / "generated.json").write_text(
        json.dumps({"input": [{"type": "function_call", "arguments": f"cd {_HOME} && ls"}]}),
        encoding="utf-8",
    )

    assert fixture_privacy_scan.scan_fixture_tree(tmp_path)["passed"] is False


def test_the_second_net_judges_a_body_that_will_not_parse(tmp_path: Path) -> None:
    """The pass is over bytes, so a truncated capture is judged like any other."""

    (tmp_path / "truncated.json").write_text('{"input": [{"arguments": "cd /srv/acme/ledger', encoding="utf-8")

    report = fixture_privacy_scan.scan_fixture_tree(tmp_path)

    assert report["findings"] == [{"path": "truncated.json", "kinds": ["operator_path"]}]


# --- the commit gate --------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    ["would_be_committed.json", "sub/would_be_committed.json", "sub/../would_be_committed.json"],
)
def test_writing_into_the_corpus_requires_an_explicit_acknowledgement(
    relative: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Human diff review is the boundary, so committing a fixture asks for it by name.

    The subdirectory case is not hypothetical tidiness: the guard compared
    ``destination.parent`` against the corpus, so one extra path segment walked
    a fixture into the committed tree unacknowledged.
    """

    source = tmp_path / "body.json"
    source.write_text(json.dumps(_realistic_body()), encoding="utf-8")
    destination = FIXTURES / relative

    exit_code = codex_body_sanitize.main(["--in", str(source), "--out", str(destination)])

    assert exit_code == 2
    assert codex_body_sanitize.REVIEW_FLAG in capsys.readouterr().err
    assert not destination.exists()


@pytest.mark.parametrize("flag", ["--out", "--headers-out"])
def test_a_corpus_symlink_pointing_outside_is_still_the_corpus(
    flag: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``os.replace`` acts on the lexical path, so ``resolve()`` alone is not the guard.

    A symlink sitting in the corpus and pointing outside it resolved to
    somewhere else, so neither the acknowledgement nor the sidecar refusal
    fired -- and ``atomic_write_text`` then replaced the symlink with a real
    file *inside* the corpus. For ``--headers-out`` that is the denylisted
    header sidecar landing in the committed tree.
    """

    source = tmp_path / "body.json"
    source.write_text(json.dumps(_realistic_body()), encoding="utf-8")
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({"authorization": "Bearer x"}), encoding="utf-8")
    outside = tmp_path / "outside.json"
    link = FIXTURES / "would_be_committed.json"
    link.symlink_to(outside)
    argv = ["--in", str(source), "--out", str(tmp_path / "rebuilt.json"), "--headers-in", str(headers)]
    argv = [*argv, flag, str(link)] if flag != "--out" else ["--in", str(source), "--out", str(link)]

    try:
        assert codex_body_sanitize.writes_into_the_corpus(link) is True
        exit_code = codex_body_sanitize.main(argv)

        assert exit_code == 2
        assert capsys.readouterr().err
        assert link.is_symlink(), "the symlink was replaced by a real file inside the corpus"
        assert not outside.exists()
    finally:
        link.unlink(missing_ok=True)


@pytest.mark.parametrize("relative", ["would_be_committed.json", "sub/would_be_committed.json"])
@pytest.mark.parametrize("flag", ["--headers-out", "--emit-redactions"])
def test_no_sidecar_may_be_written_into_the_corpus(
    flag: str, relative: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the rebuilt body belongs there, and the header sidecar is a denylist.

    ``--out`` is gated by the acknowledgement; without this, ``--headers-out``
    pointed at the corpus would walk operator bytes past the same boundary
    through a file nothing rebuilds.
    """

    source = tmp_path / "body.json"
    source.write_text(json.dumps(_realistic_body()), encoding="utf-8")
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({"authorization": "Bearer x"}), encoding="utf-8")
    sidecar = FIXTURES / relative

    exit_code = codex_body_sanitize.main(
        [
            "--in",
            str(source),
            "--out",
            str(tmp_path / "rebuilt.json"),
            "--headers-in",
            str(headers),
            flag,
            str(sidecar),
        ]
    )

    assert exit_code == 2
    assert flag in capsys.readouterr().err
    assert not sidecar.exists()


def test_a_scratch_destination_needs_no_acknowledgement(tmp_path: Path) -> None:
    """The flag gates the corpus, not the tool: reviewing the body comes first."""

    source = tmp_path / "body.json"
    source.write_text(json.dumps(_realistic_body()), encoding="utf-8")
    destination = tmp_path / "rebuilt.json"

    assert codex_body_sanitize.main(["--in", str(source), "--out", str(destination)]) == 0
    assert json.loads(destination.read_text())["model"] == "gpt-5.5"


def test_the_cli_reports_both_sides_of_the_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "body.json"
    source.write_text(json.dumps(_realistic_body()), encoding="utf-8")
    log = tmp_path / "redactions.json"

    codex_body_sanitize.main(
        ["--in", str(source), "--out", str(tmp_path / "rebuilt.json"), "--emit-redactions", str(log)]
    )

    printed = capsys.readouterr().out
    assert "captured body:" in printed and "rebuilt body:" in printed
    assert "evidence about the rebuild, never a clearance" in printed
    emitted = json.loads(log.read_text())
    assert emitted["captured_scan_kinds"] and emitted["residual_scan_kinds"] == []


def test_the_cli_refuses_to_write_when_a_captured_string_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The survivor walk is a runtime refusal, not only a CI assertion.

    The body is chosen so the *residual scan* finds nothing in it: a base64
    blob is invisible to every pattern. Without that, the residual half of the
    refusal fired on its own and deleting the survivor clause entirely left the
    whole suite green -- measured.
    """

    leak = "payload=" + base64.b64encode(f"cd {_HOME} && make".encode()).decode()
    body = _output_item(leak)
    assert fixture_privacy_scan.body_findings(body) == [], "the residual scan must not be what refuses here"
    source = tmp_path / "body.json"
    source.write_text(json.dumps(body), encoding="utf-8")
    destination = tmp_path / "rebuilt.json"

    def _leaky(body: Any, **_kwargs: Any) -> tuple[dict[str, Any], list[Redaction]]:
        return dict(body), []

    monkeypatch.setattr(codex_body_sanitize, "sanitize_body", _leaky)

    exit_code = codex_body_sanitize.main(["--in", str(source), "--out", str(destination)])

    assert exit_code == 2
    # The count, not the phrase: the message used to read "0 captured string(s)
    # survived the allowlist" when the *residual scan* was what refused, so the
    # phrase alone passed against a survivor walk that had stopped working.
    error = capsys.readouterr().err
    assert re.search(r"\b([1-9]\d*) captured string\(s\) survived the allowlist", error), error
    assert "residual scan kinds" not in error, error
    assert not destination.exists()


# --- headers sidecar --------------------------------------------------------------------


def test_headers_drop_the_credential_and_the_whole_codex_identifier_family() -> None:
    headers = {
        "Authorization": f"Bearer {_FAKE_BEARER}",
        "chatgpt-account-id": "acct_0199f2c1",
        "session-id": _LIVE_SESSION,
        "thread-id": _LIVE_SESSION,
        "x-client-request-id": _LIVE_TURN,
        "x-codex-turn-metadata": json.dumps({"session_id": _LIVE_SESSION}),
        "originator": "codex_cli_rs",
        "cookie": "session=" + "abc123; user=jane",
        "proxy-authorization": _FAKE_BASIC,
        "x-api-key": _FAKE_HEADER_KEY,
        "openai-organization": "org-acme",
        "user-agent": "codex_exec/0.154.0 (Linux 6.8.0; x86_64) tmux",
        "content-type": "application/json",
    }

    sanitised, redactions = sanitize_headers(headers)

    assert set(sanitised) == {"content-type", "user-agent"}
    assert sanitised["user-agent"] == "codex_exec/0.154.0"
    assert _LIVE_SESSION not in json.dumps(sanitised)
    assert {redaction.kind for redaction in redactions} == {"header_dropped", "user_agent_tail_dropped"}


# --- the planted mutations ----------------------------------------------------------------


def _corpus(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """A writable copy of the committed corpus plus its parsed provenance."""

    root = tmp_path / "codex_bodies"
    shutil.copytree(FIXTURES, root)
    provenance = json.loads((root / gate.PROVENANCE_NAME).read_text(encoding="utf-8"))
    return root, provenance


def _run_gate(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    provenance: dict[str, Any],
) -> None:
    monkeypatch.setattr(gate, "FIXTURES", root)
    monkeypatch.setattr(gate, "PROVENANCE", provenance["fixtures"])
    monkeypatch.setattr(gate, "FIXTURE_NAMES", sorted(provenance["fixtures"]))
    (root / gate.PROVENANCE_NAME).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


def _scan(root: Path) -> dict[str, Any]:
    """The shipped default: the telemetry-key exemptions come from ``provenance.json``.

    No ``allow_telemetry_keys`` argument, so the mutations are judged by exactly
    the invocation the runbook documents.
    """

    return fixture_privacy_scan.scan_fixture_tree(root)


def test_mutation_readding_client_telemetry_fails_the_privacy_gate(tmp_path: Path) -> None:
    root, _provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    body["client_metadata"] = {"session_id": _LIVE_SESSION}
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")

    report = _scan(root)

    assert report["passed"] is False
    kinds = {finding["path"]: finding["kinds"] for finding in report["findings"]}
    assert fixture_privacy_scan.TELEMETRY_KEY_KIND in kinds[CAPTURED_STANDARD]
    assert "identifier_key" in kinds[CAPTURED_STANDARD]


def test_mutation_nested_stream_telemetry_fails_the_corpus_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one telemetry shape no other pass in the gate can see.

    The residual scan has no vocabulary for `reasoning_summary_delivery`, the
    shape gate admits `stream_options`, and the verdict is computed after
    `strip_source_telemetry` removes it -- so a fixture declaring
    `carries_client_telemetry: false` while carrying it passed everything.
    """

    root, provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    body["stream_options"] = {"reasoning_summary_delivery": "sequential_cutoff"}
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")
    _run_gate(monkeypatch, root, provenance)

    assert _scan(root)["passed"] is True, "no residual pattern sees this; the corpus gate must"
    with pytest.raises(AssertionError, match="stream_options.reasoning_summary_delivery"):
        gate.test_telemetry_presence_matches_the_declared_fixture_role(CAPTURED_STANDARD)


def test_mutation_a_live_cache_key_fails_the_privacy_gate(tmp_path: Path) -> None:
    root, _provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_LITE).read_text(encoding="utf-8"))
    body["prompt_cache_key"] = _LIVE_SESSION
    (root / CAPTURED_LITE).write_text(json.dumps(body), encoding="utf-8")

    report = _scan(root)

    assert report["passed"] is False
    assert "uuid" in dict((finding["path"], finding["kinds"]) for finding in report["findings"])[CAPTURED_LITE]


def test_mutation_an_operator_workspace_path_fails_the_privacy_gate(tmp_path: Path) -> None:
    """A hand-edited fixture is what the second net is for."""

    root, _provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    body["instructions"] = f"Working in /home/{getpass.getuser()}/work/codex-lb on the parser."
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")

    report = _scan(root)

    assert report["passed"] is False
    kinds = dict((finding["path"], finding["kinds"]) for finding in report["findings"])[CAPTURED_STANDARD]
    assert "operator_path" in kinds
    # The unmutated corpus passes the same scan, so the finding is the mutation's.
    assert _scan(_corpus(tmp_path / "pristine")[0])["passed"] is True


def test_mutation_a_skill_inventory_inside_a_tool_call_fails_the_privacy_gate(tmp_path: Path) -> None:
    """Planted on the real corpus, in the item shape a field walk used to miss."""

    root, _provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    body["input"].append({"type": "function_call_output", "call_id": "call_1", "output": f"{_SKILLS}\n{_ENVIRONMENT}"})
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")

    report = _scan(root)

    assert report["passed"] is False
    kinds = dict((finding["path"], finding["kinds"]) for finding in report["findings"])[CAPTURED_STANDARD]
    assert kinds == ["operator_path", "unsanitised_environment_tag", "unsanitised_skill_inventory"]
    # The unmutated corpus passes the same scan, so the finding is the mutation's.
    assert _scan(_corpus(tmp_path / "pristine")[0])["passed"] is True


def test_mutation_a_credential_shape_fails_through_the_reused_scanner(tmp_path: Path) -> None:
    """Reuse, not reimplementation: the finding comes from ``privacy_scan``'s own patterns."""

    root, _provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    body["instructions"] = f"Authorization: Bearer {_FAKE_JWT}"
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")

    report = _scan(root)

    assert report["passed"] is False
    kinds = dict((finding["path"], finding["kinds"]) for finding in report["findings"])[CAPTURED_STANDARD]
    assert {"bearer_token", "jwt"} <= set(kinds)


def test_mutation_removing_every_tool_surface_fails_the_shape_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, provenance = _corpus(tmp_path)
    body = json.loads((root / CAPTURED_STANDARD).read_text(encoding="utf-8"))
    del body["tools"]
    (root / CAPTURED_STANDARD).write_text(json.dumps(body), encoding="utf-8")
    _run_gate(monkeypatch, root, provenance)

    with pytest.raises(AssertionError, match=r"captured_gpt55_standard_http\.json \[captured, slug=gpt-5\.5"):
        gate.test_every_fixture_is_shaped_like_a_codex_responses_body(CAPTURED_STANDARD)


def test_mutation_flipping_an_origin_without_the_readme_fails_the_sync_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, provenance = _corpus(tmp_path)
    provenance["fixtures"][SYNTHETIC_STANDARD]["origin"] = "captured"
    _run_gate(monkeypatch, root, provenance)

    with pytest.raises(AssertionError, match="README says synthetic"):
        gate.test_readme_rows_and_provenance_rows_agree_in_both_directions()


def test_mutation_a_wrong_recorded_reason_fails_the_verdict_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the recorded verdict is really asserted, not merely stored."""

    root, provenance = _corpus(tmp_path)
    provenance["fixtures"][CAPTURED_STANDARD]["expected_portability_verdict"]["reason"] = "not_portable_history"
    _run_gate(monkeypatch, root, provenance)

    with pytest.raises(AssertionError, match="not_portable_tools"):
        gate.test_every_fixture_matches_its_recorded_view_and_verdict(CAPTURED_STANDARD)


def test_mutation_an_undeclared_extra_file_fails_the_sync_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, provenance = _corpus(tmp_path)
    shutil.copyfile(root / CAPTURED_STANDARD, root / "undeclared_capture.json")
    _run_gate(monkeypatch, root, provenance)

    with pytest.raises(AssertionError):
        gate.test_files_on_disk_and_provenance_rows_agree_in_both_directions()
