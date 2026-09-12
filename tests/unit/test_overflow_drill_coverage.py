"""Anti-rot guard: every clause of the canary drill table has an assertion or an operator observation (#2123).

``docs/routing.md`` "Canary and drills" is a runbook table, and its **Expected**
cells are the promises an operator checks. The rehearsals in
``tests/integration/test_subscription_overflow_canary_drills.py`` cover most of
those promises, but not all of them can be covered -- one process cannot show
cross-replica propagation, and no stub can tell you what the *real* source
does. Twice already the prose claimed more than the suite asserted, in both
directions: a row that promised two things and named a rehearsal for one, and a
summary sentence that claimed a union no drill asserts.

So the mapping is data, not prose. ``_CLAUSES`` below maps every clause of
every **Expected** cell either to the drill that asserts it *and the assertion
text that does the asserting*, or to the runbook's "Not rehearsed" list. The
guard then holds three things together:

* **The clauses reconstruct the cell.** Walking a row's clauses in order must
  consume its whole **Expected** cell, with nothing between them but
  punctuation and whitespace. Add a promise to a row and it is covered by no
  clause, so the partition fails -- which is the case the last review round
  found by hand (an exception list that had gone stale).
* **The assertion is still there.** Each rehearsed clause names snippets that
  must appear in the body of the drill the table names for it (whitespace
  normalised, so reformatting is not a failure). Delete the assertion and the
  clause is uncovered -- and so does parking it out of the way, in a comment,
  in a string statement, or in a string that is assigned or passed somewhere:
  all three are blanked before matching (``_executable_source``). Text that
  looks like an assertion is not one.
* **And the drill still runs.** The body is only half of it: an assertion in a
  drill that never executes rehearses nothing either, and the body is where a
  switched-off drill looks exactly like a working one. So the marks that reach
  each drill are read too (``_switched_off``): a ``skip``/``skipif``/``xfail``
  on it, or on one of its parametrised cases, or on the module's
  ``pytestmark`` however that is written; a ``pytest.skip()``/``pytest.xfail()``
  anywhere in it, under whatever name the module imported ``pytest`` or the
  call itself as; a body that opens with a ``return``, a ``raise`` or a
  ``pass``. Any of those and
  every clause the drill covers is reported uncovered, by name. The drill must
  also carry the ``overflow_drill`` marker, because that -- not its path -- is
  what the runbook's one command selects.
* **The manual residue is explicit.** A clause with no rehearsal must appear
  verbatim in the "Not rehearsed" section, under its row's name, with the
  ``*Observe:*`` sentence that tells the operator what settles it -- and the
  section's own count of them must match.

What it cannot check: that an assertion *means* what the clause says, or that
the run reaches it. The requirement is that the text sits in a drill that runs,
not on a path that executes -- an assertion under a condition that is never
true, or inside a ``try`` that swallows it, reads as present here and stays a
reviewer's job. What this does is hold the mapping still, so a reviewer can
follow it in one step instead of re-deriving it. The **How** column is
procedure, not promise, and is not mapped.

The "still runs" half is read off the source, not off a live collection: this
is a unit test, and collecting ``-m overflow_drill tests/integration`` in a
subprocess costs ~13 s and imports every integration module, so an unrelated
collection error would surface here as a drill-coverage failure. What that
buys instead is the static chain -- the marker the runbook's command names, the
marker the suite declares, the marks on each drill -- checked in
``test_every_drill_runs_under_the_marker_the_runbook_names``. The gap it leaves
is the skip no source can show: a ``conftest`` that ignores the file, an
``addopts`` deselection, or a fixture raising ``Skipped`` at setup. The run is
what shows those, so the runbook tells the operator to read its summary for a
``skipped`` and for every ``test_drill_*`` the table names -- and that count is
kept the table's by
``test_the_runbook_tells_the_operator_how_many_rehearsals_to_expect``.

``_coverage_errors`` is a pure function over (doc text, suite text, clauses)
and is exercised against mutated inputs at the bottom of this file, the way
``tests/unit/test_request_log_source_parity.py`` exercises ``_parity_error``:
the guard's own failure modes are tested, not trusted. It also fails when it
has nothing to check at all -- no table, no drill, no rehearsal in the map --
so a parser that silently stops working cannot read as a clean bill of health.
"""

from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.modules.model_sources.forwarding import SOURCE_CONNECT_DEADLINE_SECONDS
from app.modules.proxy.overflow import OVERFLOW_OUTCOMES

REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_DOC = REPO_ROOT / "docs/routing.md"
_DRILL_SUITE = REPO_ROOT / "tests/integration/test_subscription_overflow_canary_drills.py"

_TABLE_HEADER = "| Drill | How | Expected | Rehearsal |"
_MANUAL_HEADING = "#### Not rehearsed -- verify manually during the canary"
# Between two clauses there may be punctuation and nothing else: a new promise
# can never be mistaken for glue, however it is punctuated.
_GLUE = re.compile(r"[\s;.,]*")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_DRILL_TEST_NAME = re.compile(r"^test_drill_[a-z0-9_]+$")
# The marker the runbook's one command selects: `-m overflow_drill tests/integration`.
_SUITE_MARKER = "overflow_drill"
_MARKER_SELECTOR = f"-m {_SUITE_MARKER} tests/integration"
# Marks that stop a drill from running, and the calls that stop one from inside.
_OFF_SWITCH_MARKS = frozenset({"skip", "skipif", "xfail"})
_ESCAPE_CALLS = frozenset({"skip", "xfail", "exit", "importorskip"})
_COUNT_WORDS = {1: "One clause", 2: "Two clauses", 3: "Three clauses", 4: "Four clauses", 5: "Five clauses"}
_OUTCOME_LABELS = re.compile(r"codex_lb_subscription_overflow_total\{([^}]*)\}")
_OUTCOME_MATCHER = re.compile(r'outcome\s*(=~|=)\s*"([^"]*)"')
# Off stops *fresh* overflow; pinned and anchored conversations go on being
# dispatched until the drain window closes, which is what the row's other
# clauses promise and what the drills assert.
_DRAIN_SURVIVING_OUTCOMES = ("dispatched_pinned", "dispatched_anchor")


@dataclass(frozen=True)
class _Clause:
    """One promise of one **Expected** cell, and what covers it.

    ``rehearsal`` names the drill that asserts it and ``assertions`` the
    fragments of that drill which do; a clause with no ``rehearsal`` must be in
    the runbook's "Not rehearsed" list instead.
    """

    row: str
    text: str
    rehearsal: str | None = None
    assertions: tuple[str, ...] = field(default_factory=tuple)


# The mapping. Order matters: a row's clauses must appear in its **Expected**
# cell in this order and cover all of it.
_CLAUSES: tuple[_Clause, ...] = (
    # -- Disconnect ------------------------------------------------------------------------------
    _Clause(
        "Disconnect",
        "One request-log row with status `cancelled`",
        "test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt",
        ('("cancelled", REQUEST_LOG_SOURCE_FRESH, key_id)', "rows[0].error_code in expected_error_codes"),
    ),
    _Clause(
        "Disconnect",
        "no pin",
        "test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt",
        ("assert await _pin_rows() == []",),
    ),
    _Clause(
        "Disconnect",
        "the source slot released",
        "test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt",
        ("assert get_source_bulkhead().in_flight(scene.source_id) == 0",),
    ),
    _Clause(
        "Disconnect",
        "the API-key reservation released",
        "test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt",
        ('assert await _reservation_states(key_id) == [("released", None, None)]',),
    ),
    _Clause(
        "Disconnect",
        "nothing reached the client",
        "test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt",
        ("assert stream.chunks == []",),
    ),
    # -- Stall -----------------------------------------------------------------------------------
    _Clause(
        "Stall",
        "A dropped SYN answers `502 model_source_unreachable` before any `200`, "
        "because the connect phase never reaches the header wait",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        (
            "assert unreachable.status_code == 502",
            'assert _error(unreachable)["code"] == "model_source_unreachable"',
            'assert b"data:" not in unreachable.content',
        ),
    ),
    _Clause(
        "Stall",
        "a source that accepts TCP and then stays silent answers `504 model_source_timeout` "
        "at the 20 s header deadline, again before any `200`",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        (
            "assert timed_out.status_code == 504",
            'assert "response headers within 20s" in header_error["message"]',
            'assert b"data:" not in timed_out.content',
        ),
    ),
    _Clause(
        "Stall",
        "the breaker opens within three attempts",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        (
            "assert breaker.failures(stalling_id) == 3",
            'assert breaker.state(stalling_id, time.monotonic()) == "open"',
        ),
    ),
    _Clause(
        "Stall",
        "a fresh request then falls back to today's `429` without reaching the source",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        ("assert fresh.json() == _todays_429(reset_at)", "assert len(state.requests) == 3"),
    ),
    _Clause(
        "Stall",
        "a pinned conversation gets `503 model_source_unavailable` with `Retry-After: 2`",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        (
            "assert pinned.status_code == 503",
            'assert _error(pinned)["code"] == MODEL_SOURCE_UNAVAILABLE_CODE',
            'assert pinned.headers["retry-after"] == str(overflow_module.RETRY_AFTER_SECONDS) == "2"',
        ),
    ),
    _Clause(
        "Stall",
        "the stalled source never touches the ChatGPT connector",
        "test_drill_stall_fails_closed_and_opens_the_breaker",
        ("assert _chatgpt_connections_acquired() == 0", "assert _model_source_connections_acquired() >= 1"),
    ),
    _Clause("Stall", "ChatGPT traffic is unaffected"),
    # -- Silent headers --------------------------------------------------------------------------
    _Clause(
        "Silent headers",
        "`504 model_source_timeout` at 30 s",
        "test_drill_silent_headers_send_nothing_and_leave_no_pin",
        (
            "assert response.status_code == 504",
            'assert "the first response frame within 30s" in error["message"]',
        ),
    ),
    _Clause(
        "Silent headers",
        "nothing was sent to the client",
        "test_drill_silent_headers_send_nothing_and_leave_no_pin",
        (
            'assert b"data:" not in response.content',
            'assert response.content == json.dumps({"error": error}, separators=(",", ":")).encode()',
        ),
    ),
    # -- Neutral release -------------------------------------------------------------------------
    _Clause(
        "Neutral release",
        "Its next turn is served by a subscription account",
        "test_drill_neutral_release_frees_a_source_free_conversation",
        (
            "assert len(relayed) == 1",
            "assert [(row.account_id is not None, row.model_source_id) for row in rows] == [(True, None)]",
        ),
    ),
    _Clause(
        "Neutral release",
        "the pin is gone",
        "test_drill_neutral_release_frees_a_source_free_conversation",
        ('assert await _pin_rows() == [], "the pin is deleted durably before the account serves the thread"',),
    ),
    _Clause(
        "Neutral release",
        "a reasoning-bearing conversation gets `400 subscription_overflow_source_unavailable` and keeps its pin",
        "test_drill_neutral_release_refuses_a_ciphertext_transcript_whatever_it_declares",
        (
            '"input": _ciphertext_input()',
            "assert response.status_code == 400",
            'assert _error(response)["code"] == SOURCE_UNAVAILABLE_CODE',
            "assert [pin.source_id for pin in await _pin_rows()] == [scene.source_id]",
        ),
    ),
    # -- Clear-then-touch ------------------------------------------------------------------------
    _Clause(
        "Clear-then-touch",
        "The source serves it until day 7",
        "test_drill_clear_then_touch_expires_at_day_seven",
        (
            "assert served.status_code == 200",
            "assert [(row.status, row.source) for row in await _all_rows()] "
            '== [("success", REQUEST_LOG_SOURCE_PINNED)]',
            'assert outcomes == [(ROUTE_CODEX_RESPONSES, "dispatched_pinned")]',
        ),
    ),
    _Clause(
        "Clear-then-touch",
        "never a subscription account",
        "test_drill_clear_then_touch_expires_at_day_seven",
        ('assert attempts == [], "a draining conversation never falls back to a subscription account"',),
    ),
    _Clause(
        "Clear-then-touch",
        "past the seventh idle day the pin is a tombstone -- a conversation the source owns is refused "
        "with `subscription_overflow_unsupported_input`",
        "test_drill_clear_then_touch_expires_at_day_seven",
        (
            "assert refused.status_code == 400",
            'assert tombstone_error["code"] == UNSUPPORTED_INPUT_CODE',
        ),
    ),
    _Clause(
        "Clear-then-touch",
        "a source-free one is released to an account instead",
        "test_drill_clear_then_touch_expires_at_day_seven",
        (
            "assert released.status_code == 200",
            'assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_released_neutral")]',
        ),
    ),
    _Clause(
        "Clear-then-touch",
        "the pin table is empty at the drain deadline",
        "test_drill_clear_then_touch_expires_at_day_seven",
        (
            'assert pruned["model_source_pins"] == len(survivors)',
            'assert await _pin_rows() == [], "the pin table is empty at the drain deadline"',
        ),
    ),
    # -- Kill switches ---------------------------------------------------------------------------
    _Clause(
        "Kill switches",
        "Fresh overflow stops -- today's `429`, byte for byte",
        "test_drill_kill_switches_restore_subscription_behaviour",
        (
            'assert real == stubbed, "a switched-off overflow must be indistinguishable from never having shipped"',
            "assert content == _golden_429_bytes(scene.reset_at)",
        ),
    ),
    _Clause("Kill switches", "on every replica within the settings-cache window (≤ 5 s)"),
    _Clause(
        "Kill switches",
        "pinned conversations drain",
        "test_drill_kill_switches_restore_subscription_behaviour",
        (
            'assert rows[2:] == [("success", REQUEST_LOG_SOURCE_PINNED, None)]',
            "assert now < _aware(row.expires_at) <= now + PIN_IDLE_TTL",
        ),
    ),
    _Clause(
        "Kill switches",
        "disabling or deleting releases a source-free conversation to a subscription account",
        "test_drill_kill_switches_restore_subscription_behaviour",
        (
            'assert outcomes[-1] == (ROUTE_CODEX_RESPONSES, "pinned_released_neutral")',
            'assert await _pin_rows() == [], "a released conversation is no longer the source\'s"',
            "assert (await _all_rows())[-1].account_id is not None",
        ),
    ),
    _Clause(
        "Kill switches",
        "a reasoning-bearing one ends with `400` and keeps its pin",
        "test_drill_kill_switches_restore_subscription_behaviour",
        (
            "assert pinned.status_code == pinned_status",
            'assert _error(pinned)["code"] == SOURCE_UNAVAILABLE_CODE',
            "assert pins[pinned_key].source_id == scene.source_id",
        ),
    ),
    # -- Anchor timing ---------------------------------------------------------------------------
    _Clause(
        "Anchor timing", "The source emits `response.created` with an `id` **before** its first content-bearing frame"
    ),
    _Clause(
        "Anchor timing",
        "given that, the anchor row exists and the turn is `200`",
        "test_drill_anchor_timing_accepts_a_source_that_mints_its_id_first",
        (
            "assert anchored.status_code == 200",
            'assert event_types.index("response.created") < content_at[0]',
            '(PIN_KIND_ANCHOR, anchor_pin_key(key_id, "resp_drill_anchor"), scene.source_id)',
        ),
    ),
    _Clause(
        "Anchor timing",
        "a source that mints its id later answers `subscription_overflow_pin_unavailable` on the SSE lifecycle",
        "test_drill_anchor_timing_refuses_an_unanchorable_sdk_turn",
        (
            'assert [event["type"] for event in events] == ["response.created", "response.failed"]',
            'assert events[1]["response"]["error"]["code"] == PIN_UNAVAILABLE_CODE',
        ),
    ),
    _Clause(
        "Anchor timing",
        "and it cannot serve SDK overflow turns at all",
        "test_drill_anchor_timing_refuses_an_unanchorable_sdk_turn",
        (
            'assert "hello from the source" not in refused.text',
            'assert await _pin_rows() == [], "an unanchorable turn writes neither the anchor nor the thread pin"',
        ),
    ),
)


# -- parsing -------------------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    """Collapse whitespace, so a wrapped assertion still matches the snippet that names it."""

    return " ".join(text.split())


def _char_column(line: str, byte_column: int) -> int:
    """``ast`` counts columns in UTF-8 bytes; ``str`` slicing counts characters, and this file has both."""

    return len(line.encode("utf-8")[:byte_column].decode("utf-8"))


def _holds_an_assertion(literal: str) -> bool:
    """Is this string literal an assertion in disguise -- a rehearsal parked where it cannot run?"""

    try:
        text = ast.literal_eval(literal)
    except (ValueError, SyntaxError):  # an f-string, or anything else not a constant
        return False
    if not isinstance(text, str) or "assert" not in text:
        return False
    try:
        parsed = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        return False
    return any(isinstance(statement, ast.Assert) for statement in parsed.body)


def _executable_source(source: str) -> str:
    """``source`` with the text that cannot run blanked out, at unchanged offsets.

    A clause is covered by an assertion the drill *runs*, but the body we match
    against is verbatim source, so text that only looks like an assertion
    covers it just as well: ``# assert await _pin_rows() == []`` still contains
    ``assert await _pin_rows() == []``, and so does a bare string statement
    holding the same line -- or, one keystroke further, ``parked = "assert
    await _pin_rows() == []"``. All of them are how an assertion gets switched
    off in practice, and all of them used to leave the build green with the
    rehearsal gone.

    So three things are blanked: comments, string-expression statements
    (docstrings included), and any string literal whose *contents* are
    themselves ``assert`` statements, wherever that literal sits. The last rule
    is deliberately narrow -- a drill's ordinary literals (``"response headers
    within 20s"``, a JSON fragment) do not parse as assertions and are left
    alone, because mapped snippets quote them. Blanking is to spaces, not
    deletion, so every surviving character keeps its position and the drill
    reads exactly as it did.
    """

    lines = source.splitlines()
    original = list(lines)

    def blank(start_row: int, start_column: int, end_row: int, end_column: int) -> None:
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            first = start_column if row == start_row else 0
            last = end_column if row == end_row else len(line)
            lines[row - 1] = line[:first] + " " * (last - first) + line[last:]

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT or (token.type == tokenize.STRING and _holds_an_assertion(token.string)):
            blank(token.start[0], token.start[1], token.end[0], token.end[1])
    for node in ast.walk(ast.parse(source)):
        # A string alone as a statement -- a docstring, or an assertion parked in one -- runs nothing.
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str) or node.end_lineno is None or node.end_col_offset is None:
            continue
        blank(
            node.lineno,
            _char_column(original[node.lineno - 1], node.col_offset),
            node.end_lineno,
            _char_column(original[node.end_lineno - 1], node.end_col_offset),
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class _Row:
    expected: str
    rehearsals: tuple[str, ...]


def _drill_table(doc: str) -> dict[str, _Row]:
    """The table under ``_TABLE_HEADER``: row label -> (**Expected** cell, rehearsals it names)."""

    lines = doc.splitlines()
    try:
        start = lines.index(_TABLE_HEADER)
    except ValueError:  # pragma: no cover - the vacuous-pass guard below covers the real file
        return {}
    rows: dict[str, _Row] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 4 or set(cells[0]) <= {"-", ":"}:
            continue
        rows[cells[0]] = _Row(
            expected=cells[2],
            rehearsals=tuple(token for token in _BACKTICKED.findall(cells[3]) if _DRILL_TEST_NAME.match(token)),
        )
    return rows


def _selected_outcomes(promql: str) -> frozenset[str]:
    """The outcomes a runbook ``codex_lb_subscription_overflow_total`` selector really matches.

    Expanded against the production enum, because the point of reading a
    selector is what an operator would see on ``/metrics``, not what the
    sentence around it meant.
    """

    labels = _OUTCOME_LABELS.findall(promql)
    assert len(labels) == 1, f"expected exactly one overflow-counter selector, got {labels}"
    matcher = _OUTCOME_MATCHER.search(labels[0])
    assert matcher, f"no 'outcome' label in the selector {labels[0]!r}"
    operator, value = matcher.groups()
    if operator == "=":
        assert value in OVERFLOW_OUTCOMES, f"{value!r} is not an outcome the proxy emits"
        return frozenset({value})
    # PromQL ``=~`` is fully anchored.
    pattern = re.compile(rf"(?:{value})\Z")
    return frozenset(outcome for outcome in OVERFLOW_OUTCOMES if pattern.match(outcome))


def _manual_section(doc: str) -> tuple[str, list[str]]:
    """The "Not rehearsed" section: its intro text and its bullets."""

    lines = doc.splitlines()
    try:
        start = lines.index(_MANUAL_HEADING)
    except ValueError:  # pragma: no cover - the vacuous-pass guard below covers the real file
        return "", []
    intro: list[str] = []
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#") or line.startswith("---"):
            break
        if line.startswith("- "):
            bullets.append(line)
        elif not bullets:
            intro.append(line)
        else:
            # A continuation paragraph ends the clause list; later bullets in
            # the section ("the rest of what the canary is for") are not it.
            break
    return "\n".join(intro), bullets


def _dotted(node: ast.expr) -> tuple[str, ...]:
    """``pytest.mark.skip`` -> ``("pytest", "mark", "skip")``; anything not a dotted name -> ``()``."""

    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ()
    parts.append(current.id)
    return tuple(reversed(parts))


def _marks(expr: ast.expr, roots: frozenset[str] = frozenset({"mark"})) -> set[str]:
    """Every ``<mark root>.<name>`` mentioned anywhere inside ``expr``.

    Anywhere, not just at the top: ``pytest.mark.skip`` and
    ``pytest.mark.skip(reason=...)`` are the same switch, and so is a mark
    smuggled into a case list as ``pytest.param(..., marks=pytest.mark.xfail)``.
    ``roots`` is what ``pytest.mark`` is called here -- ``mark`` on its own,
    plus whatever ``from pytest import mark as pm`` bound it to.
    """

    names: set[str] = set()
    for node in ast.walk(expr):
        if not isinstance(node, ast.Attribute):
            continue
        parts = _dotted(node)
        index = next((position for position, part in enumerate(parts) if part in roots), None)
        if index is not None and index + 1 < len(parts):
            names.add(parts[index + 1])
    return names


def _module_marks(module: ast.Module, roots: frozenset[str]) -> set[str]:
    """Marks from a module-level ``pytestmark``, which reach every test in the file.

    Every form pytest honours, because pytest reads the attribute and not the
    statement that made it: the plain assignment, the annotated one
    (``pytestmark: list = [...]``), an augmented one, and a mutating call on the
    list (``pytestmark.append(pytest.mark.skip)``, ``.extend``, ``.insert``).
    """

    names: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            # ``pytestmark.append(...)``: the call's own dotted name starts with
            # the list it mutates, and its arguments are the marks it adds.
            call = node.value
            if _dotted(call.func)[:1] != ("pytestmark",):
                continue
            for argument in call.args:
                names |= _marks(argument, roots)
            continue
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            continue
        if value is not None:
            names |= _marks(value, roots)
    return names


@dataclass(frozen=True)
class _Aliases:
    """What ``pytest``, ``pytest.mark`` and the calls that end a test are named in this module."""

    modules: frozenset[str]
    marks: frozenset[str]
    calls: dict[str, str]


def _pytest_aliases(module: ast.Module) -> _Aliases:
    """Resolve those names instead of assuming them.

    ``import pytest as pt``, ``from pytest import mark as pm`` and ``from
    pytest import skip as stop`` are all ordinary Python, and each hides a
    switch from a matcher that only knows the spelling ``pytest.mark.skip``.
    """

    modules = {"pytest"}
    marks = {"mark"}
    calls: dict[str, str] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "pytest"}
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            marks |= {alias.asname or alias.name for alias in node.names if alias.name == "mark"}
            calls |= {alias.asname or alias.name: alias.name for alias in node.names if alias.name in _ESCAPE_CALLS}
    return _Aliases(modules=frozenset(modules), marks=frozenset(marks), calls=calls)


def _first_statement(node: ast.AsyncFunctionDef | ast.FunctionDef) -> ast.stmt | None:
    """The drill's first statement that does something, its docstring skipped."""

    for statement in node.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str):
                continue
        return statement
    return None


def _escape_call(node: ast.AST, aliases: _Aliases) -> str | None:
    """``pytest.skip(...)`` and friends anywhere in the drill: a test may end itself at any depth."""

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        parts = _dotted(child.func)
        if not parts:
            continue
        if len(parts) == 1:
            # A bare name: whatever this module imported from pytest under it,
            # or the two spellings that mean nothing else inside a test.
            imported = aliases.calls.get(parts[0])
            if imported in _ESCAPE_CALLS:
                suffix = "" if imported == parts[0] else f" (pytest.{imported})"
                return f"{parts[0]}(){suffix}"
            if imported is None and parts[0] in ("skip", "xfail"):
                return f"{parts[0]}()"
            continue
        if parts[0] in aliases.modules and parts[-1] in _ESCAPE_CALLS:
            return f"{'.'.join(parts)}()"
    return None


def _switched_off(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
    module_marks: set[str],
    aliases: _Aliases,
) -> str | None:
    """Why this drill never reaches its assertions, or ``None`` if it does.

    ``ast.get_source_segment`` returns the ``def`` without its decorators -- by
    design, since the body is what has to contain the assertions -- so nothing
    below this line can see a drill that was switched off above it. Hence this:
    the marks that reach it (its own and the module's ``pytestmark``), and the
    two in-body ways out, a ``pytest.skip()`` and a body that opens with a
    ``return``, a ``raise`` or a ``pass``. All of them leave ``-m
    overflow_drill`` green with the rehearsal gone, which is the one thing this
    guard exists to make impossible.

    Only the *first* statement is read for the quiet exits: a ``return`` deeper
    in a drill is ordinary control flow, and reading one as a switch would make
    the guard a style checker. ``pytest.skip``/``pytest.xfail`` are read at any
    depth, because a drill that can end itself mid-way rehearses the runbook's
    promise only sometimes.
    """

    marks = module_marks | {mark for decorator in node.decorator_list for mark in _marks(decorator, aliases.marks)}
    off = sorted(marks & _OFF_SWITCH_MARKS)
    if off:
        return " and ".join(f"a pytest.mark.{mark} mark applies to it" for mark in off)
    escape = _escape_call(node, aliases)
    if escape:
        return f"it calls {escape}"
    first = _first_statement(node)
    if first is None:
        return "its body is empty"
    if isinstance(first, ast.Return):
        return "it returns before asserting anything"
    if isinstance(first, ast.Raise):
        return "it raises before asserting anything"
    if isinstance(first, ast.Pass):
        return "its body is a bare pass"
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and first.value.value is Ellipsis:
        return "its body is a bare ellipsis"
    return None


@dataclass(frozen=True)
class _Drill:
    """One ``test_drill_*``: the source that runs, and whether it runs at all."""

    body: str
    switched_off: str | None
    selected: bool


def _drills(suite: str) -> dict[str, _Drill]:
    """Every ``test_drill_*`` in the suite, by name, with its *runnable* source and its marks.

    ``body`` is the ``def`` without decorators, comments and string statements
    blanked, so switching an assertion off uncovers its clause exactly like
    deleting it does. ``switched_off`` and ``selected`` are read from the parts
    ``body`` cannot show: the decorators above it and the module's
    ``pytestmark``.
    """

    module = ast.parse(suite)
    aliases = _pytest_aliases(module)
    module_marks = _module_marks(module, aliases.marks)
    drills: dict[str, _Drill] = {}
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and _DRILL_TEST_NAME.match(node.name):
            segment = ast.get_source_segment(suite, node)
            own_marks = {mark for decorator in node.decorator_list for mark in _marks(decorator, aliases.marks)}
            drills[node.name] = _Drill(
                body=_executable_source(segment) if segment else "",
                switched_off=_switched_off(node, module_marks, aliases),
                selected=_SUITE_MARKER in module_marks | own_marks,
            )
    return drills


# -- the guard -------------------------------------------------------------------------------------


def _partition_errors(row: str, expected: str, clauses: list[_Clause]) -> list[str]:
    """The row's clauses must consume its whole **Expected** cell, in order, glued by punctuation only."""

    errors: list[str] = []
    cursor = 0
    for clause in clauses:
        index = expected.find(clause.text, cursor)
        if index < 0:
            errors.append(
                f"{row}: no clause reads {clause.text!r} in the documented outcome from offset {cursor} on -- "
                "the table and the coverage map disagree about what the row promises"
            )
            return errors
        gap = expected[cursor:index]
        if not _GLUE.fullmatch(gap):
            errors.append(
                f"{row}: {gap.strip()!r} sits between two mapped clauses and is covered by neither; "
                "map it to an assertion or list it under 'Not rehearsed'"
            )
        cursor = index + len(clause.text)
    tail = expected[cursor:]
    if not _GLUE.fullmatch(tail):
        errors.append(
            f"{row}: {tail.strip()!r} trails the last mapped clause and is covered by nothing; "
            "map it to an assertion or list it under 'Not rehearsed'"
        )
    return errors


def _coverage_errors(doc: str, suite: str, clauses: tuple[_Clause, ...]) -> list[str]:
    """Everything wrong with the documented drills, or an empty list."""

    errors: list[str] = []
    table = _drill_table(doc)
    drills = _drills(suite)
    intro, bullets = _manual_section(doc)

    # Nothing below can fail when there is nothing to check, so say so here: an
    # empty table, an empty suite or a map with no rehearsal in it would
    # otherwise walk every loop zero times and report a clean bill of health.
    if not table:
        errors.append("the runbook has no drill table, so no clause of it can be checked against the suite")
    if not drills:
        errors.append("the suite defines no test_drill_* function, so the whole table rests on nothing")
    if not any(clause.rehearsal for clause in clauses):
        errors.append("no clause in the coverage map names a rehearsal, so no assertion is being held in place")

    for name in sorted(name for name, drill in drills.items() if not drill.selected):
        errors.append(
            f"{name} carries no {_SUITE_MARKER} marker, so `{_MARKER_SELECTOR}` -- "
            "the one command the runbook gives the operator -- never runs it"
        )

    mapped_rows = {clause.row for clause in clauses}
    for row in sorted(mapped_rows - set(table)):
        errors.append(f"{row}: the coverage map claims a drill row the table does not have")
    for row in sorted(set(table) - mapped_rows):
        errors.append(
            f"{row}: a drill row with no clause in the coverage map -- every clause of its documented "
            "outcome needs either an assertion or a 'Not rehearsed' entry"
        )

    manual_count = 0
    for row, entry in table.items():
        row_clauses = [clause for clause in clauses if clause.row == row]
        if not row_clauses:
            continue
        errors.extend(_partition_errors(row, entry.expected, row_clauses))
        cited = {clause.rehearsal for clause in row_clauses if clause.rehearsal is not None}
        for name in sorted(cited - set(entry.rehearsals)):
            errors.append(f"{row}: clauses are covered by {name}, which the row's Rehearsal cell does not name")
        for name in sorted(set(entry.rehearsals) - cited):
            errors.append(f"{row}: the Rehearsal cell names {name}, which covers none of the row's clauses")

        for clause in row_clauses:
            if clause.rehearsal is None:
                manual_count += 1
                errors.extend(_manual_entry_errors(row, clause, bullets))
                continue
            drill = drills.get(clause.rehearsal)
            if drill is None:
                errors.append(f"{row}: {clause.rehearsal} is named for {clause.text!r} but the suite has no such drill")
                continue
            if drill.switched_off is not None:
                errors.append(
                    f"{row}: {clause.rehearsal} does not run -- {drill.switched_off} -- so nothing rehearses "
                    f"{clause.text!r}; a switched-off drill is not a rehearsal"
                )
                continue
            normalised = _normalized(drill.body)
            for assertion in clause.assertions:
                if _normalized(assertion) not in normalised:
                    errors.append(
                        f"{row}: {clause.rehearsal} no longer contains {assertion!r}, "
                        f"so nothing asserts {clause.text!r}"
                    )

    expected_intro = _COUNT_WORDS.get(manual_count)
    if expected_intro is None:
        errors.append(f"{manual_count} unrehearsed clauses is more than this guard is willing to call a residue")
    elif expected_intro not in intro:
        errors.append(
            f"the 'Not rehearsed' section says {intro.strip().splitlines()[0][:60]!r} "
            f"but the map has {manual_count} unrehearsed clause(s), so it should open with {expected_intro!r}"
        )

    for name in sorted(set(drills) - {rehearsal for entry in table.values() for rehearsal in entry.rehearsals}):
        errors.append(f"{name} is a drill the table names nowhere, so an operator following the runbook never runs it")
    return errors


def _manual_entry_errors(row: str, clause: _Clause, bullets: list[str]) -> list[str]:
    """An unrehearsed clause must be in the runbook, quoted, under its row, with the observation."""

    for bullet in bullets:
        if clause.text not in bullet:
            continue
        if f"**{row}**" not in bullet:
            return [f"{row}: the 'Not rehearsed' entry quoting {clause.text!r} does not name the row it belongs to"]
        if "*Observe:*" not in bullet:
            return [
                f"{row}: the 'Not rehearsed' entry for {clause.text!r} has no '*Observe:*' sentence, "
                "so it tells the operator nothing to do on the canary"
            ]
        return []
    return [
        f"{row}: {clause.text!r} has no assertion and no 'Not rehearsed' entry -- "
        "add the assertion, or list the clause with the observation that settles it during the canary"
    ]


ROUTING_DOC = _read(_ROUTING_DOC)
DRILL_SUITE = _read(_DRILL_SUITE)


# -- the parsed inputs are real (a broken parser must not pass vacuously) --------------------------


def test_the_table_and_the_suite_parse() -> None:
    table = _drill_table(ROUTING_DOC)
    drills = _drills(DRILL_SUITE)
    intro, bullets = _manual_section(ROUTING_DOC)

    assert len(table) == 7, sorted(table)
    assert all(row.expected and row.rehearsals for row in table.values()), table
    assert len(drills) >= len(table), sorted(drills)
    assert all(len(drill.body) > 500 for drill in drills.values()), {
        name: len(drill.body) for name, drill in drills.items()
    }
    assert bullets and intro.strip(), (intro, bullets)
    assert len(_CLAUSES) >= 25, len(_CLAUSES)


def test_every_drill_runs_under_the_marker_the_runbook_names() -> None:
    """The suite's end of the chain from the operator's command to the drill.

    The runbook's command selects ``-m overflow_drill`` under
    ``tests/integration``; this suite is a ``test_*.py`` file there whose
    module-level ``pytestmark`` carries that marker, and no drill in it is
    switched off. That is what "the drill the row names actually runs" means
    under pytest's default collection, checked without a subprocess -- see the
    module docstring for the gap that leaves. The other end (``make
    test-overflow-drills`` exists, the marker is registered in ``pyproject``)
    is pinned by ``test_subscription_overflow_spec_delta.py``.
    """

    assert _DRILL_SUITE.parent == REPO_ROOT / "tests/integration", _DRILL_SUITE
    assert _DRILL_SUITE.name.startswith("test_") and _DRILL_SUITE.suffix == ".py", _DRILL_SUITE
    assert _MARKER_SELECTOR in ROUTING_DOC, "the runbook's command selects something else"

    drills = _drills(DRILL_SUITE)

    assert drills, "no drills to select"
    assert {name: drill.switched_off for name, drill in drills.items() if drill.switched_off} == {}
    assert [name for name, drill in drills.items() if not drill.selected] == []


def test_the_runbook_tells_the_operator_how_many_rehearsals_to_expect() -> None:
    """The one hiding place source cannot see is a drill pytest never collects; the count is what shows it.

    A ``conftest`` ignore or an ``addopts`` deselection removes a drill without
    printing ``skipped`` anywhere, so the sentence beside the command tells the
    operator how many ``test_drill_*`` rehearsals the table names. Keep that
    number the table's, not a remembered one.
    """

    named = {rehearsal for entry in _drill_table(ROUTING_DOC).values() for rehearsal in entry.rehearsals}

    assert f"all {len(named)} `test_drill_*` rehearsals this table names ran" in ROUTING_DOC, sorted(named)


def test_the_coverage_map_is_well_formed() -> None:
    """A manual clause carries no assertions, and a rehearsed one carries at least one."""

    for clause in _CLAUSES:
        if clause.rehearsal is None:
            assert clause.assertions == (), clause
        else:
            assert clause.assertions, clause
            assert _DRILL_TEST_NAME.match(clause.rehearsal), clause
        # Short enough to be a real promise ("no pin"), long enough that a
        # degenerate clause cannot match half the cell by accident.
        assert len(clause.text) >= 6, clause


# -- the shipped tree ------------------------------------------------------------------------------


def test_every_documented_drill_clause_is_asserted_or_declared_manual() -> None:
    errors = _coverage_errors(ROUTING_DOC, DRILL_SUITE, _CLAUSES)

    assert errors == [], "\n".join(errors)


def test_the_clauses_are_the_semicolon_split_the_runbook_promises() -> None:
    """The runbook tells the reader to read each cell as ``;``-separated clauses; keep that literally true.

    ``_partition_errors`` is deliberately looser -- it would accept a clause
    split at a full stop -- so this is the check that keeps the sentence beside
    the table honest. Splitting a cell some other way means editing that
    sentence too.
    """

    for row, entry in _drill_table(ROUTING_DOC).items():
        split = [part.strip().rstrip(".").strip() for part in entry.expected.split(";")]
        mapped = [clause.text for clause in _CLAUSES if clause.row == row]

        assert split == mapped, (row, split, mapped)


def test_the_kill_switch_observation_watches_only_the_overflow_the_switch_stops() -> None:
    """An unrehearsed clause hands the operator a query; the query has to be able to succeed.

    The clause is about *fresh* overflow -- its cell reads "Fresh overflow
    stops -- today's ``429``, byte for byte; on every replica within the
    settings-cache window". But the same row promises, two clauses later, that
    pinned conversations *drain*, and a ``dispatched_.*`` selector covers
    ``dispatched_pinned`` and ``dispatched_anchor`` too. Those keep
    incrementing for up to seven idle days after the flip, so the series the
    operator was told to watch go flat cannot, and following the sentence
    literally means concluding the kill switch never propagated.
    """

    _, bullets = _manual_section(ROUTING_DOC)
    bullet = next(bullet for bullet in bullets if "settings-cache window" in bullet)

    selected = _selected_outcomes(bullet)

    still_climbing = selected & frozenset(_DRAIN_SURVIVING_OUTCOMES)
    assert not still_climbing, (
        f"the Off observation tells the operator to watch {sorted(still_climbing)} go flat, "
        "but the drain keeps feeding those outcomes for up to seven days"
    )
    assert selected == {"dispatched_fresh"}, sorted(selected)


def test_the_stall_observation_names_the_bound_a_dropped_syn_actually_waits_out() -> None:
    """The other half of the same question: an operator timing a DROP must be told the right deadline.

    A firewall ``DROP`` is what the row's first clause is about -- "the connect
    phase never reaches the header wait" -- so the bound under test is the
    connect deadline, not the header wait and not the source's total budget.
    """

    _, bullets = _manual_section(ROUTING_DOC)
    bullet = next(bullet for bullet in bullets if "firewall `DROP`" in bullet)

    assert f"the real {SOURCE_CONNECT_DEADLINE_SECONDS:.0f} s bound" in bullet, bullet


def test_the_drain_really_does_keep_feeding_the_outcomes_that_observation_excludes() -> None:
    """Keep the test above honest: the drills pin those outcomes on the very leg it describes."""

    assert frozenset(_DRAIN_SURVIVING_OUTCOMES) < OVERFLOW_OUTCOMES, _DRAIN_SURVIVING_OUTCOMES
    # The Off leg of the kill-switch drill, and day six of the clear-then-touch drain.
    assert '("off", 200, True, "declined_drain_mode", "dispatched_pinned")' in DRILL_SUITE
    assert 'assert outcomes == [(ROUTE_CODEX_RESPONSES, "dispatched_pinned")]' in DRILL_SUITE


# -- the guard bites -------------------------------------------------------------------------------


def _plant_in_doc(old: str, new: str) -> str:
    assert old in ROUTING_DOC, old
    return ROUTING_DOC.replace(old, new, 1)


def test_guard_catches_a_promise_added_to_a_row() -> None:
    """The round-2 failure mode: a row grows a clause and nothing rehearses it."""

    doc = _plant_in_doc(
        "| Silent headers | Source answers `200` and then nothing. | `504 model_source_timeout` at 30 s;",
        "| Silent headers | Source answers `200` and then nothing. | `504 model_source_timeout` at 30 s; "
        "the breaker records the failure;",
    )

    errors = _coverage_errors(doc, DRILL_SUITE, _CLAUSES)

    assert any("the breaker records the failure" in error and "covered by neither" in error for error in errors), errors


def test_guard_catches_a_new_row_with_no_rehearsal() -> None:
    doc = _plant_in_doc(
        "| Silent headers |",
        "| Cold start | Restart the replica mid-drain. | The drain window survives the restart. | none |\n"
        "| Silent headers |",
    )

    errors = _coverage_errors(doc, DRILL_SUITE, _CLAUSES)

    assert any(error.startswith("Cold start: a drill row with no clause") for error in errors), errors


def test_guard_catches_a_deleted_assertion() -> None:
    """Removing the assertion a clause maps to must fail, however green the drill stays."""

    suite = DRILL_SUITE.replace(
        '    assert stream.chunks == [], "nothing may reach a client that has already left"\n', ""
    )
    assert suite != DRILL_SUITE

    errors = _coverage_errors(ROUTING_DOC, suite, _CLAUSES)

    assert any("nothing reached the client" in error and "no longer contains" in error for error in errors), errors


_SWITCHED_OFF_DISCONNECT = (
    "    assert await _pin_rows() == []\n    assert get_source_bulkhead().in_flight(scene.source_id) == 0\n"
)


def _assert_both_disconnect_clauses_uncovered(suite: str) -> None:
    assert suite != DRILL_SUITE, "the plant did not apply"

    errors = _coverage_errors(ROUTING_DOC, suite, _CLAUSES)

    assert any("'no pin'" in error and "no longer contains" in error for error in errors), errors
    assert any("'the source slot released'" in error and "no longer contains" in error for error in errors), errors


def test_guard_catches_an_assertion_commented_out() -> None:
    """Switching an assertion off must fail exactly like deleting it.

    ``ast.get_source_segment`` keeps comments, so a substring match read
    ``# assert await _pin_rows() == []`` as still asserting the clause. These
    two lines are the only rehearsal of the Disconnect row's "no pin" and "the
    source slot released", and commenting them out left the whole build green.
    """

    _assert_both_disconnect_clauses_uncovered(
        DRILL_SUITE.replace(
            _SWITCHED_OFF_DISCONNECT,
            "    # assert await _pin_rows() == []\n"
            "    # assert get_source_bulkhead().in_flight(scene.source_id) == 0\n",
            1,
        )
    )


def test_guard_catches_an_assertion_parked_in_a_string() -> None:
    """The same hole one keystroke over: a bare string statement runs nothing either."""

    _assert_both_disconnect_clauses_uncovered(
        DRILL_SUITE.replace(
            _SWITCHED_OFF_DISCONNECT,
            '    """assert await _pin_rows() == []\n'
            '    assert get_source_bulkhead().in_flight(scene.source_id) == 0"""\n',
            1,
        )
    )


def test_guard_catches_an_assertion_parked_in_a_value() -> None:
    """And one keystroke further: a string that is assigned, or passed, runs nothing either.

    Blanking only *statement* strings left this open -- ``parked = "assert
    await _pin_rows() == []"`` kept the text in the body and covered the clause.
    A literal whose contents parse as ``assert`` statements is blanked wherever
    it sits, which is narrow enough that the drills' ordinary literals (the ones
    mapped snippets quote) survive.
    """

    _assert_both_disconnect_clauses_uncovered(
        DRILL_SUITE.replace(
            _SWITCHED_OFF_DISCONNECT,
            '    parked = "assert await _pin_rows() == []"\n'
            '    _note("assert get_source_bulkhead().in_flight(scene.source_id) == 0")\n',
            1,
        )
    )


def test_a_literal_a_mapped_assertion_quotes_survives_the_blanking() -> None:
    """The narrowness matters: several clauses map to assertions *about* a string."""

    quoting = 'assert "response headers within 20s" in header_error["message"]'
    assert quoting in DRILL_SUITE

    assert _normalized(quoting) in _normalized(_executable_source(DRILL_SUITE))


_SILENT_HEADERS_DRILL = "test_drill_silent_headers_send_nothing_and_leave_no_pin"
_SILENT_HEADERS_DEF = f"async def {_SILENT_HEADERS_DRILL}("
_STALL_DRILL = "test_drill_stall_fails_closed_and_opens_the_breaker"


def _assert_drill_does_not_rehearse(suite: str, drill: str, row: str, clause: str, reason: str) -> None:
    assert suite != DRILL_SUITE, "the plant did not apply"

    errors = _coverage_errors(ROUTING_DOC, suite, _CLAUSES)

    wanted = f"{row}: {drill} does not run -- {reason} -- so nothing rehearses {clause!r}"
    assert any(error.startswith(wanted) for error in errors), errors


def test_guard_catches_a_drill_marked_skip() -> None:
    """The round-3 failure mode: the assertions are all there, and none of them runs.

    ``ast.get_source_segment`` excludes decorators, so a substring match over
    the body cannot see ``@pytest.mark.skip`` above it: the drill reported
    ``skipped``, the marker-selected run stayed green, and both clauses of the
    Silent headers row went unrehearsed while the runbook still promised them.
    """

    _assert_drill_does_not_rehearse(
        DRILL_SUITE.replace(
            _SILENT_HEADERS_DEF, f'@pytest.mark.skip(reason="flaky, quarantined")\n{_SILENT_HEADERS_DEF}', 1
        ),
        _SILENT_HEADERS_DRILL,
        "Silent headers",
        "`504 model_source_timeout` at 30 s",
        "a pytest.mark.skip mark applies to it",
    )


def test_guard_catches_a_drill_marked_xfail() -> None:
    """An expected failure is not a rehearsal either, strict or not."""

    _assert_drill_does_not_rehearse(
        DRILL_SUITE.replace(
            f"async def {_STALL_DRILL}(", f"@pytest.mark.xfail(strict=False)\nasync def {_STALL_DRILL}(", 1
        ),
        _STALL_DRILL,
        "Stall",
        "the breaker opens within three attempts",
        "a pytest.mark.xfail mark applies to it",
    )


def test_guard_catches_a_drill_that_skips_itself() -> None:
    """The same switch one line lower -- and ``pytest.skip`` is executable, so blanking cannot reach it."""

    _assert_drill_does_not_rehearse(
        DRILL_SUITE.replace(
            "    assert response.status_code == 504",
            '    pytest.skip("quarantined")\n    assert response.status_code == 504',
            1,
        ),
        _SILENT_HEADERS_DRILL,
        "Silent headers",
        "`504 model_source_timeout` at 30 s",
        "it calls pytest.skip()",
    )


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param("    return", "it returns before asserting anything", id="return"),
        pytest.param('    raise RuntimeError("quarantined")', "it raises before asserting anything", id="raise"),
        pytest.param("    pass", "its body is a bare pass", id="pass"),
        pytest.param("    ...", "its body is a bare ellipsis", id="ellipsis"),
        pytest.param('    pytest.skip("later")\n    assert x == 1', "it calls pytest.skip()", id="skip-call"),
        pytest.param(
            '    if SLOW:\n        pytest.xfail("later")\n    assert x == 1',
            "it calls pytest.xfail()",
            id="nested-xfail",
        ),
    ],
)
def test_a_drill_that_leaves_before_it_asserts_is_switched_off(body: str, reason: str) -> None:
    """A body that runs, and gets out. Only the first statement is read for the quiet exits.

    ``return``/``raise``/``pass`` deeper in a drill are ordinary control flow,
    so they are not read as a switch; ``pytest.skip`` and ``pytest.xfail`` are
    at any depth, because a drill that can end itself mid-way is not one an
    operator can read the runbook's promise off.
    """

    suite = f"import pytest\n\npytestmark = pytest.mark.{_SUITE_MARKER}\n\n\ndef test_drill_x() -> None:\n{body}\n"

    drill = _drills(suite)["test_drill_x"]

    assert drill.switched_off == reason, drill
    assert drill.selected


@pytest.mark.parametrize(
    ("decorator", "reason"),
    [
        pytest.param("@pytest.mark.skip", "a pytest.mark.skip mark applies to it", id="bare"),
        pytest.param('@pytest.mark.skip(reason="x")', "a pytest.mark.skip mark applies to it", id="called"),
        pytest.param('@pytest.mark.skipif(True, reason="x")', "a pytest.mark.skipif mark applies to it", id="skipif"),
        pytest.param("@mark.xfail", "a pytest.mark.xfail mark applies to it", id="imported-mark"),
        pytest.param("@pm.skip", "a pytest.mark.skip mark applies to it", id="aliased-mark"),
        pytest.param("@pt.mark.skip", "a pytest.mark.skip mark applies to it", id="aliased-module"),
        pytest.param(
            '@pytest.mark.parametrize("n", [pytest.param(1, marks=pytest.mark.skip)])',
            "a pytest.mark.skip mark applies to it",
            id="param-level",
        ),
    ],
)
def test_a_mark_that_reaches_a_drill_switches_it_off(decorator: str, reason: str) -> None:
    """Bare or called, on the drill or on one of its cases, and whatever ``mark`` was imported as."""

    suite = (
        "import pytest\nimport pytest as pt\nfrom pytest import mark\nfrom pytest import mark as pm\n\n"
        f"pytestmark = pytest.mark.{_SUITE_MARKER}\n\n\n"
        f"{decorator}\ndef test_drill_x() -> None:\n    assert x == 1\n"
    )

    assert _drills(suite)["test_drill_x"].switched_off == reason


@pytest.mark.parametrize(
    "assignment",
    [
        pytest.param(f"pytestmark = [pytest.mark.{_SUITE_MARKER}, pytest.mark.skip]", id="plain"),
        pytest.param(f"pytestmark: list = [pytest.mark.{_SUITE_MARKER}, pytest.mark.skip]", id="annotated"),
        pytest.param(f"pytestmark = [pytest.mark.{_SUITE_MARKER}]\npytestmark += [pytest.mark.skip]", id="augmented"),
        pytest.param(
            f"pytestmark = [pytest.mark.{_SUITE_MARKER}]\npytestmark.append(pytest.mark.skip)", id="append-call"
        ),
        pytest.param(
            f"pytestmark = [pytest.mark.{_SUITE_MARKER}]\npytestmark.extend([pytest.mark.skip])", id="extend-call"
        ),
        pytest.param(f"from pytest import mark as pm\n\npytestmark = [pm.{_SUITE_MARKER}, pm.skip]", id="aliased-mark"),
    ],
)
def test_a_module_wide_skip_switches_every_drill_off(assignment: str) -> None:
    """``pytestmark`` is the one switch that is nowhere near the drill it disables.

    pytest reads the module *attribute*, so every way of arriving at it counts:
    the annotated assignment, the augmented one and a mutating call on the list
    were all invisible to the first cut of this check, and all of them really do
    skip the drills.
    """

    suite = f"import pytest\n\n{assignment}\n\n\ndef test_drill_x() -> None:\n    assert x == 1\n"

    drill = _drills(suite)["test_drill_x"]

    assert drill.switched_off == "a pytest.mark.skip mark applies to it"
    assert drill.selected


@pytest.mark.parametrize(
    ("imports", "call", "reason"),
    [
        pytest.param("import pytest", 'pytest.skip("x")', "it calls pytest.skip()", id="dotted"),
        pytest.param("import pytest as pt", 'pt.skip("x")', "it calls pt.skip()", id="aliased-module"),
        pytest.param("from pytest import skip", 'skip("x")', "it calls skip()", id="imported-name"),
        pytest.param(
            "from pytest import skip as stop", 'stop("x")', "it calls stop() (pytest.skip)", id="aliased-name"
        ),
        pytest.param(
            "from pytest import importorskip",
            'importorskip("nonexistent")',
            "it calls importorskip()",
            id="import-or-skip",
        ),
    ],
)
def test_a_drill_that_skips_itself_under_any_name_is_switched_off(imports: str, call: str, reason: str) -> None:
    """``pytest.skip`` is a spelling, not the mechanism; the mechanism is whatever name pytest was bound to."""

    suite = (
        f"import pytest\n{imports}\n\npytestmark = pytest.mark.{_SUITE_MARKER}\n\n\n"
        f"def test_drill_x() -> None:\n    {call}\n    assert x == 1\n"
    )

    assert _drills(suite)["test_drill_x"].switched_off == reason


def test_a_local_helper_is_not_mistaken_for_a_pytest_escape() -> None:
    """The other direction: a call this module never imported from pytest is just a call."""

    suite = (
        f"import pytest\n\npytestmark = pytest.mark.{_SUITE_MARKER}\n\n\n"
        "def test_drill_x() -> None:\n    stub.exit()\n    helper.importorskip()\n    assert x == 1\n"
    )

    assert _drills(suite)["test_drill_x"].switched_off is None


def test_a_drill_outside_the_marker_is_not_the_drill_the_runbook_runs() -> None:
    """Renaming the marker, or dropping it, makes the runbook's one command miss the drill."""

    suite = (
        "import pytest\n\npytestmark = pytest.mark.integration\n\n\ndef test_drill_x() -> None:\n    assert x == 1\n"
    )

    drill = _drills(suite)["test_drill_x"]

    assert not drill.selected
    assert drill.switched_off is None, "it runs -- just never under the command the operator was given"
    assert any(
        error.startswith(f"test_drill_x carries no {_SUITE_MARKER} marker") for error in _coverage_errors("", suite, ())
    )


def test_the_shipped_drills_are_not_read_as_switched_off() -> None:
    """The other half of the mutation tests: none of these predicates fires on the real suite."""

    drills = _drills(DRILL_SUITE)

    assert len(drills) >= 7, sorted(drills)
    assert all(drill.switched_off is None and drill.selected for drill in drills.values()), {
        name: drill.switched_off for name, drill in drills.items()
    }


@pytest.mark.parametrize(
    ("doc", "suite", "clauses", "expected"),
    [
        pytest.param("", DRILL_SUITE, _CLAUSES, "the runbook has no drill table", id="no-table"),
        pytest.param(ROUTING_DOC, "", _CLAUSES, "the suite defines no test_drill_*", id="no-drills"),
        pytest.param(
            ROUTING_DOC,
            DRILL_SUITE,
            tuple(_Clause(clause.row, clause.text) for clause in _CLAUSES),
            "no clause in the coverage map names a rehearsal",
            id="no-rehearsals",
        ),
    ],
)
def test_the_guard_fails_when_it_has_nothing_to_check(
    doc: str, suite: str, clauses: tuple[_Clause, ...], expected: str
) -> None:
    """Every input this guard reads can go empty, and an empty input must never read as a pass."""

    errors = _coverage_errors(doc, suite, clauses)

    assert any(error.startswith(expected) for error in errors), errors


def test_blanking_inert_text_leaves_the_running_drill_alone() -> None:
    """Only comments and string statements go; the code keeps its text and its offsets."""

    source = "\n".join(
        (
            "def drill():",
            '    """Docstring with assert stream.chunks == [] in it."""',
            "    assert x == 1  # assert y == 2",
            "    return x",
        )
    )

    executable = _executable_source(source)

    assert _normalized(executable) == "def drill(): assert x == 1 return x"
    assert len(executable) == len(source), (len(executable), len(source))
    assert "assert y == 2" not in executable
    assert "assert stream.chunks == []" not in executable


def test_guard_catches_a_renamed_rehearsal() -> None:
    suite = DRILL_SUITE.replace(
        "async def test_drill_silent_headers_send_nothing_and_leave_no_pin(",
        "async def test_drill_silent_headers_renamed(",
    )

    errors = _coverage_errors(ROUTING_DOC, suite, _CLAUSES)

    assert any("the suite has no such drill" in error for error in errors), errors
    assert any("test_drill_silent_headers_renamed is a drill the table names nowhere" in error for error in errors), (
        errors
    )


def test_guard_catches_a_manual_clause_that_lost_its_observation() -> None:
    doc = ROUTING_DOC.replace("*Observe:* flip Off", "flip Off", 1)
    assert doc != ROUTING_DOC

    errors = _coverage_errors(doc, DRILL_SUITE, _CLAUSES)

    assert any("has no '*Observe:*' sentence" in error for error in errors), errors


def test_guard_catches_a_manual_clause_dropped_from_the_runbook() -> None:
    """Deleting the entry must fail even though the clause is still in the table."""

    intro, bullets = _manual_section(ROUTING_DOC)
    dropped = next(bullet for bullet in bullets if "ChatGPT traffic is unaffected" in bullet)
    doc = ROUTING_DOC.replace(f"{dropped}\n", "", 1)
    assert doc != ROUTING_DOC

    errors = _coverage_errors(doc, DRILL_SUITE, _CLAUSES)

    assert any("no assertion and no 'Not rehearsed' entry" in error for error in errors), errors


def test_guard_catches_a_clause_quietly_promoted_out_of_the_manual_list() -> None:
    """The count in the runbook and the number of unrehearsed clauses are one fact, checked once."""

    promoted = tuple(
        _Clause(
            clause.row,
            clause.text,
            "test_drill_stall_fails_closed_and_opens_the_breaker",
            ("assert _chatgpt_connections_acquired() == 0",),
        )
        if clause.text == "ChatGPT traffic is unaffected"
        else clause
        for clause in _CLAUSES
    )

    errors = _coverage_errors(ROUTING_DOC, DRILL_SUITE, promoted)

    assert any("should open with 'Two clauses'" in error for error in errors), errors


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        pytest.param(
            _Clause("Stall", "no such promise in the cell"), "the table and the coverage map disagree", id="absent"
        ),
        pytest.param(_Clause("Stall", "the breaker opens within three attempts"), "covered by neither", id="reordered"),
    ],
)
def test_partition_rejects_a_clause_that_does_not_fit(clause: _Clause, expected: str) -> None:
    """A clause must be in the cell, and in the order the map lists it."""

    expected_cell = _drill_table(ROUTING_DOC)["Stall"].expected

    errors = _partition_errors("Stall", expected_cell, [clause, *[c for c in _CLAUSES if c.row == "Stall"]])

    assert any(expected in error for error in errors), errors
