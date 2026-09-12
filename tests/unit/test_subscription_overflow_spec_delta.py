"""Drift guards between the overflow spec deltas / operator docs and the shared interface contract (#2123 WP-C2, WP-D).

``openspec validate --strict`` checks structure, not names. The normative
deltas quote the closed enums, error codes, hint texts and request-log labels
that ``app/modules/proxy/overflow.py`` defines; these tests fail when either
side drifts, the way ``tests/unit/test_metrics.py`` does for the
``codex_lb_model_source_*`` metric names.

The closed enums -- the request-log ``source`` values, the metric ``route``
labels and the dispatch kinds -- are discovered from ``overflow.py`` by import
(``tests/unit/_overflow_constants.py``) rather than spelled here, so a value
added to the authority grows the expected set instead of slipping past guards
that only knew the values which existed when they were written. The removal
direction is covered by comparing the delta's own prose enumerations of the
closed ``route`` and dispatch-``kind`` sets against the discovered enum, so a
retired label cannot stay advertised by the normative spec either.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from app.modules.proxy import overflow
from tests.unit._overflow_constants import (
    BACKEND_MODULE,
    DISPATCH_KIND_PREFIX,
    ROUTE_PREFIX,
    SOURCE_PREFIX,
    discovery_error,
    overflow_constants,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGE = REPO_ROOT / "openspec/changes/add-subscription-overflow-model-source"
_ROUTING_DELTA = _CHANGE / "specs/model-source-routing/spec.md"
_COMPAT_DELTA = _CHANGE / "specs/responses-api-compat/spec.md"
_OBSERVABILITY_DELTA = _CHANGE / "specs/proxy-runtime-observability/spec.md"
_ACCOUNT_ROUTING_DELTA = _CHANGE / "specs/account-routing/spec.md"
_TASKS = _CHANGE / "tasks.md"
_ROUTING_DOC = REPO_ROOT / "docs/routing.md"
_FRONTEND_SRC = REPO_ROOT / "frontend/src"
_LOCALES = _FRONTEND_SRC / "i18n/locales"
_SETTINGS_COMPONENT = _FRONTEND_SRC / "features/settings/components/subscription-overflow-settings.tsx"

_BACKTICKED = re.compile(r"`([^`\n]+)`")
# Every value of the closed ``outcome`` enum has one of these shapes; the
# observability delta may not quote any such token that the enum lacks.
_OUTCOME_SHAPE = re.compile(r"^(dispatched|bounced|declined|pinned|pin_commit|decision)_[a-z_]+$")
# The canary drill table itself -- rows, clauses and the rehearsals that cover
# them -- is guarded by ``tests/unit/test_overflow_drill_coverage.py``, which
# owns the only parser for it. Here it is only the row labels that matter, in
# ``test_routing_doc_states_shipped_status_canary_and_client_floor``.

# ``route`` labels and dispatch kinds are ordinary words, so they have no shape
# the outcome regex above could match on. The delta enumerates them in prose
# instead -- "closed `route` set `a`, `b`, `c`", "`kind` `a`, `b` or `c`" -- and
# each such run is compared to the discovered enum in both directions.
# The connectors are comma-anchored on purpose. A bare ``and`` before a backticked
# token starts a new clause in this delta ("... `compact` and `outcome` in the closed
# set"), so absorbing it would misread the live spec; a run cut short by an
# unrecognised connector still fails the comparison whenever the dropped token is a
# live label.
_ENUMERATED_RUN = re.compile(r"`[a-z0-9_]+`(?:(?:, |, and |, or | or )`[a-z0-9_]+`)*")
_ROUTE_ENUMERATION = re.compile(r"`route`(?: set| in) (?=`)")
_KIND_ENUMERATION = re.compile(r"(?:for direct source routing and |`kind` (?=`))")

# The closed enums the deltas and the docs must keep naming, read off the authority.
SOURCE_LITERALS = overflow_constants(SOURCE_PREFIX)
ROUTE_LABELS = overflow_constants(ROUTE_PREFIX)
DISPATCH_KINDS = overflow_constants(DISPATCH_KIND_PREFIX)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _backticked(path: Path) -> set[str]:
    return set(_BACKTICKED.findall(_read(path)))


def _enumerations(text: str, anchor: re.Pattern[str]) -> list[set[str]]:
    """Every backticked run the delta enumerates right after ``anchor``."""
    runs: list[set[str]] = []
    for match in anchor.finditer(text):
        run = _ENUMERATED_RUN.match(text, match.end())
        assert run is not None, text[match.start() : match.end() + 80]
        runs.append(set(_BACKTICKED.findall(run.group(0))))
    return runs


@pytest.mark.parametrize("prefix", [SOURCE_PREFIX, ROUTE_PREFIX, DISPATCH_KIND_PREFIX])
def test_discovered_enums_are_trustworthy(prefix: str) -> None:
    """Sanity-check the authority before comparing the deltas against it.

    Every enum expectation below is discovered by name prefix, so an empty,
    duplicated or unexported set would make those comparisons pass vacuously
    instead of failing.
    """
    error = discovery_error(prefix, overflow_constants(prefix))

    assert error is None, error


def test_observability_delta_names_exactly_the_closed_outcome_enum() -> None:
    quoted = {token for token in _backticked(_OBSERVABILITY_DELTA) if _OUTCOME_SHAPE.match(token)}

    assert quoted == set(overflow.OVERFLOW_OUTCOMES), {
        "in_spec_only": sorted(quoted - set(overflow.OVERFLOW_OUTCOMES)),
        "in_code_only": sorted(set(overflow.OVERFLOW_OUTCOMES) - quoted),
    }


def test_declined_outcomes_cover_every_decline_reason() -> None:
    reasons = set(get_args(overflow.DeclineReason))

    assert {f"declined_{reason}" for reason in reasons} <= set(overflow.OVERFLOW_OUTCOMES)
    # The routing delta spells every decline reason by name, bare or as its counter outcome.
    routing_tokens = _backticked(_ROUTING_DELTA)
    missing = sorted(
        reason for reason in reasons if reason not in routing_tokens and f"declined_{reason}" not in routing_tokens
    )
    assert missing == [], missing


def test_observability_delta_names_the_routes_and_metrics() -> None:
    quoted = _backticked(_OBSERVABILITY_DELTA)
    text = _read(_OBSERVABILITY_DELTA)

    for route in sorted(ROUTE_LABELS.values()):
        assert route in quoted, route
    assert overflow.OVERFLOW_TOTAL_METRIC in text
    assert overflow.BREAKER_STATE_METRIC in text
    for kind in sorted(DISPATCH_KINDS.values()):
        assert kind in quoted, kind
    # The design's separate overflow result counter was folded into the dispatch
    # counter; naming it would also trip the model-source metric drift guard's
    # successor regex once it covers the overflow prefix.
    assert "codex_lb_subscription_overflow_source_result_total" not in text


def test_observability_delta_enumerates_exactly_the_closed_route_and_kind_sets() -> None:
    """The other direction: the delta may not advertise a label the runtime cannot emit.

    Discovery grows the expected set when a label is *added*. A retired
    ``ROUTE_*`` / ``DISPATCH_KIND_*`` constant would just shrink the loops above,
    leaving the normative delta enumerating a value the metric can no longer
    carry -- so each prose enumeration is compared to the enum as a set.
    """
    text = _read(_OBSERVABILITY_DELTA)

    for family, anchor, expected in (
        ("`route`", _ROUTE_ENUMERATION, set(ROUTE_LABELS.values())),
        ("dispatch `kind`", _KIND_ENUMERATION, set(DISPATCH_KINDS.values())),
    ):
        runs = _enumerations(text, anchor)

        assert runs, (
            f"{_rel(_OBSERVABILITY_DELTA)} no longer enumerates the closed {family} set in a shape "
            f"this guard can read; the reverse check would pass vacuously"
        )
        for run in runs:
            assert run == expected, {
                "family": family,
                "in_spec_only": sorted(run - expected),
                "in_code_only": sorted(expected - run),
            }


def test_enumeration_parser_reads_the_connectors_the_delta_uses() -> None:
    """The run must not stop early on a comma connector, nor swallow the next clause."""
    oxford = _enumerations(
        "the closed `route` set `a`, `b`, and `retired` and the closed `outcome` set", _ROUTE_ENUMERATION
    )

    assert oxford == [{"a", "b", "retired"}], oxford
    # The delta's second enumeration really ends this way; a bare ``and`` before a
    # backticked token starts a new clause and must not join the run.
    clause = _enumerations("with `route` in `a`, `b` and `outcome` in the closed set", _ROUTE_ENUMERATION)

    assert clause == [{"a", "b"}], clause


def test_request_log_source_values_are_quoted_in_every_normative_surface() -> None:
    """Both normative deltas and the operator docs name every attributable ``source`` value."""
    for path in (_ROUTING_DELTA, _OBSERVABILITY_DELTA, _ROUTING_DOC):
        quoted = _backticked(path)
        missing = sorted(value for value in SOURCE_LITERALS.values() if value not in quoted)

        assert missing == [], (
            f"{_rel(path)} does not quote the request-log `source` value(s) {missing} that "
            f"{BACKEND_MODULE} defines; every attributable source value must be named in the "
            f"normative deltas and the operator docs"
        )


@pytest.mark.parametrize(
    "code",
    [
        overflow.PIN_UNAVAILABLE_CODE,
        overflow.SOURCE_UNAVAILABLE_CODE,
        overflow.UNSUPPORTED_INPUT_CODE,
        overflow.HANDSHAKE_DENIAL_CODE,
        overflow.WS_BOUNCE_CODE,
        overflow.MODEL_SOURCE_UNAVAILABLE_CODE,
        overflow.MODEL_SOURCE_BUSY_CODE,
    ],
)
def test_compat_delta_quotes_every_overflow_error_code(code: str) -> None:
    assert code in _backticked(_COMPAT_DELTA), code


def test_routing_delta_quotes_the_pin_failure_codes_and_the_job_headers() -> None:
    quoted = _backticked(_ROUTING_DELTA)

    for token in (
        overflow.PIN_UNAVAILABLE_CODE,
        overflow.PIN_UNVERIFIED_CODE,
        overflow.SOURCE_UNAVAILABLE_CODE,
        overflow.UNSUPPORTED_INPUT_CODE,
        overflow.MODEL_SOURCE_UNAVAILABLE_CODE,
        overflow.MODEL_SOURCE_BUSY_CODE,
        overflow.SUBAGENT_HEADER,
        overflow.MEMGEN_HEADER,
        *sorted(overflow.BACKGROUND_ALLOWLIST),
    ):
        assert token in quoted, token
    assert f"Retry-After: {overflow.RETRY_AFTER_SECONDS}" in quoted


def test_hint_texts_are_quoted_verbatim_in_spec_and_docs() -> None:
    compat = _read(_COMPAT_DELTA)
    docs = _read(_ROUTING_DOC)

    assert f"`{overflow.HINT_HEADER}: {overflow.HINT_NATIVE_TEXT}`" in compat
    assert overflow.HINT_SDK_SENTENCE in compat
    assert overflow.HINT_HEADER in docs
    assert overflow.HINT_NATIVE_TEXT in docs


def test_forbidden_bounce_codes_appear_only_as_prohibitions() -> None:
    compat = _read(_COMPAT_DELTA)

    for forbidden in ("server_is_overloaded", "slow_down"):
        assert f"`{forbidden}`" in compat, forbidden
        for match in re.finditer(re.escape(f"`{forbidden}`"), compat):
            window = compat[max(0, match.start() - 200) : match.start()].lower()
            assert "never" in window or "not" in window, compat[max(0, match.start() - 120) : match.end()]


def test_account_routing_delta_states_the_trigger_and_the_drain_reversal() -> None:
    text = _read(_ACCOUNT_ROUTING_DELTA)

    assert "The overflow trigger is the probe's answer" in text
    assert "never" in text and "single_account" in text
    assert "no second settings read" in text


def test_tasks_mark_the_routing_extension_done_and_list_the_wiring_packages() -> None:
    text = _read(_TASKS)

    assert "- [x] 3.1 " in text
    for task in ("3.22", "3.23", "3.24", "3.25", "3.26", "3.27", "3.28", "3.36"):
        assert f" {task} " in text, task


def test_routing_doc_states_shipped_status_canary_and_client_floor() -> None:
    docs = _read(_ROUTING_DOC)

    assert "Shipping in stages" not in docs
    assert "lands in a later release" not in docs
    assert "### Canary and drills" in docs
    for drill in ("Disconnect", "Stall", "Silent headers", "Neutral release", "Clear-then-touch", "Kill switches"):
        assert f"| {drill} |" in docs, drill
    assert "0.99.0" in docs
    assert "own database" in docs
    assert "no per-replica flag" in docs
    assert overflow.OVERFLOW_TOTAL_METRIC in docs
    assert overflow.BREAKER_STATE_METRIC in docs


def test_the_drill_entry_point_is_documented_and_exists() -> None:
    docs = _read(_ROUTING_DOC)
    makefile = _read(REPO_ROOT / "Makefile")
    pyproject = _read(REPO_ROOT / "pyproject.toml")

    assert "make test-overflow-drills" in docs
    assert "-m overflow_drill" in docs
    assert "test-overflow-drills:" in makefile
    assert "-m overflow_drill tests/integration" in makefile
    # An unregistered marker selects nothing under ``--strict-markers`` and
    # warns otherwise, so the runbook's one command must not depend on it.
    assert '"overflow_drill: ' in pyproject


def test_the_stall_drill_no_longer_promises_a_timeout_for_a_dropped_connection() -> None:
    """A dropped SYN answers ``502 model_source_unreachable``; only a connected-but-silent source times out.

    Both error codes appearing in a document proves nothing -- they would appear
    just as well with the two shapes swapped, which is the mistake this test
    exists to catch. So each mention of the shape is read together with the code
    that follows it, up to the next one.
    """

    shapes = {
        "drops the SYN": "model_source_unreachable",
        "drops the connection attempt": "model_source_unreachable",
        "accepts TCP and then stays silent": "model_source_timeout",
        "accepts the connection and then sends nothing": "model_source_timeout",
    }
    codes = re.compile(r"model_source_(?:unreachable|timeout)")

    for path in (_ROUTING_DOC, _ROUTING_DELTA):
        text = _read(path)
        mentions = sorted(
            (match.start(), match.end(), shape, expected)
            for shape, expected in shapes.items()
            for match in re.finditer(re.escape(shape), text)
        )
        assert mentions, path

        answered: set[str] = set()
        for index, (_start, end, shape, expected) in enumerate(mentions):
            # Read only as far as the next shape: a code belonging to the shape
            # after this one must not be able to answer for this one.
            limit = mentions[index + 1][0] if index + 1 < len(mentions) else len(text)
            answer = codes.search(text, end, limit)
            assert answer is not None, (path, shape)
            assert answer.group() == expected, (path, shape, answer.group())
            answered.add(expected)

        # Both shapes, not one of them twice: each document has to state the
        # pair, which is what makes a swap visible.
        assert answered == {"model_source_unreachable", "model_source_timeout"}, (path, sorted(answered))


def test_dashboard_staged_notice_is_gone_from_the_component_and_every_locale() -> None:
    assert "stagedNotice" not in _read(_SETTINGS_COMPONENT)

    locales = {path.name: json.loads(_read(path)) for path in sorted(_LOCALES.glob("*.json"))}
    assert set(locales) == {"en.json", "ko.json", "zh-CN.json"}
    for name, resource in locales.items():
        assert "settings.routing.subscriptionOverflow.stagedNotice" not in resource, name
    overflow_keys = {
        name: {key for key in resource if key.startswith("settings.routing.subscriptionOverflow.")}
        for name, resource in locales.items()
    }
    assert overflow_keys["ko.json"] == overflow_keys["en.json"]
    assert overflow_keys["zh-CN.json"] == overflow_keys["en.json"]
