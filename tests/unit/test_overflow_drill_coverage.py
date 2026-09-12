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
* **The assertion is still there, and still runs.** Each rehearsed clause
  names snippets that must appear in the body of the drill the table names for
  it (whitespace normalised, so reformatting is not a failure). Delete the
  assertion and the clause is uncovered -- and so does switching it off, since
  the body is matched with comments and string statements blanked out
  (``_executable_source``). Text that looks like an assertion is not one.
* **The manual residue is explicit.** A clause with no rehearsal must appear
  verbatim in the "Not rehearsed" section, under its row's name, with the
  ``*Observe:*`` sentence that tells the operator what settles it -- and the
  section's own count of them must match.

What it cannot check: that an assertion *means* what the clause says. It
checks that a named assertion exists and is reachable from the clause, so a
reviewer can follow the mapping in one step instead of re-deriving it. The
**How** column is procedure, not promise, and is not mapped.

``_coverage_errors`` is a pure function over (doc text, suite text, clauses)
and is exercised against mutated inputs at the bottom of this file, the way
``tests/unit/test_request_log_source_parity.py`` exercises ``_parity_error``:
the guard's own failure modes are tested, not trusted.
"""

from __future__ import annotations

import ast
import io
import re
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


def _executable_source(source: str) -> str:
    """``source`` with the text that cannot run blanked out, at unchanged offsets.

    A clause is covered by an assertion the drill *runs*, but the body we match
    against is verbatim source, so text that only looks like an assertion
    covers it just as well: ``# assert await _pin_rows() == []`` still contains
    ``assert await _pin_rows() == []``, and so does a bare string statement
    holding the same line. Both are how an assertion gets switched off in
    practice, and both used to leave the build green with the rehearsal gone.
    Comments and string-expression statements are therefore blanked -- to
    spaces, not deleted, so every surviving character keeps its position and
    the drill reads exactly as it did.
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
        if token.type == tokenize.COMMENT:
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


def _drill_bodies(suite: str) -> dict[str, str]:
    """Every ``test_drill_*`` in the suite, by name, as *runnable* source (decorators excluded).

    Comments and string statements are blanked, so switching an assertion off
    uncovers its clause exactly like deleting it does.
    """

    module = ast.parse(suite)
    bodies: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and _DRILL_TEST_NAME.match(node.name):
            segment = ast.get_source_segment(suite, node)
            bodies[node.name] = _executable_source(segment) if segment else ""
    return bodies


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
    bodies = _drill_bodies(suite)
    intro, bullets = _manual_section(doc)

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
            body = bodies.get(clause.rehearsal)
            if body is None:
                errors.append(f"{row}: {clause.rehearsal} is named for {clause.text!r} but the suite has no such drill")
                continue
            normalised = _normalized(body)
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

    for name in sorted(set(bodies) - {rehearsal for entry in table.values() for rehearsal in entry.rehearsals}):
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
    bodies = _drill_bodies(DRILL_SUITE)
    intro, bullets = _manual_section(ROUTING_DOC)

    assert len(table) == 7, sorted(table)
    assert all(row.expected and row.rehearsals for row in table.values()), table
    assert len(bodies) >= len(table), sorted(bodies)
    assert all(len(body) > 500 for body in bodies.values()), {name: len(body) for name, body in bodies.items()}
    assert bullets and intro.strip(), (intro, bullets)
    assert len(_CLAUSES) >= 25, len(_CLAUSES)


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
