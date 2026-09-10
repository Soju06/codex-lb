# Verification — 2026-09-09

Initial validation base: `0083bc639` (origin/main when implementation started).
The results below were collected before PR preparation. No runtime settings or
production deployment were changed.

## Passing checks

- Continuation/policy/HTTP bridge/transport fallback/safe continuity/Responses
  contract unit suite: **2,587 passed**.
- HTTP bridge + Chat + Responses integration suite: **287 passed**, one existing
  idle-recovery timeout documented below.
- Chat model-source routing and dispatch: **34 passed**, 104 unrelated cases deselected.
- Final Chat, API-key accounting, cancellation/task-handoff checks: **30 passed**.
  Includes API-key socket isolation and settled reservations, per-key HTTP pin,
  image/large-payload bypass, lifecycle metrics, predispatch rejection, Cursor
  context-limit handling, and remote settlement ownership.
- Final promoted Chat tool-loop, conversation/session-anchor, outage and first-turn
  routing checks: **8 passed**, 175 unrelated cases deselected. Earlier focused
  Responses route tests cover native/ordinary requests and both Responses aliases.
- Ruff lint/format, `ty check`, proxy architecture check, and `git diff --check` passed.
- Strict OpenSpec change validation passed; all **64 main specifications** passed.

Suite counts overlap and should not be summed as unique test coverage.

## Existing baseline failure

`tests/integration/test_http_responses_bridge.py::test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement`
exceeds the 60-second pytest timeout. Reproduced individually with the same
interpreter and command in an unmodified detached worktree of `0083bc639`
(63.23 seconds) and this implementation (62.65 seconds). Both fail at the same
await with `Failed: Timeout (>60.0s) from pytest-timeout`. The test remains intact;
this change does not claim to repair that separate idle-recovery issue.

Reproduction:

```sh
python -m pytest tests/integration/test_http_responses_bridge.py::test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement -q --tb=short --timeout=60
```

## Scope of evidence

Integration tests exercise real HTTP handlers, admission, session reuse, Chat
conversion, and database reservation settlement with mocked upstream connectors.
No live provider latency benchmark or production promotion-rate measurement was
performed after these edits. Full-history locality reuses connections while
preserving full input; it does not claim automatic incremental-input savings.

## PR preparation validation

Rebased onto `e4c0164da` (v1.25.0-beta.6), including the upstream 429 backoff fix.
The rebase applied without conflicts and preserved the original change apart
from surrounding imports.

- **2,603 unit tests passed** in 109.89s:
  `test_http_continuation.py`, `test_chat_bridge_cleanup.py`,
  `test_proxy_utils.py`, `test_proxy_http_bridge.py`,
  `test_websocket_transport_fallback.py`, `test_http_bridge_safe_continuity.py`,
  and `test_proxy_api_responses_contract.py` under `tests/unit/`.
- **41 integration tests passed** in 77.99s: full
  `test_http_promotion_accounting.py` and `test_proxy_chat_completions.py`,
  plus the promoted history, Chat tool-loop, outage recovery, conversation,
  explicit-session/conversation, and first-turn routing cases in
  `test_http_responses_bridge.py`.
- `make lint typecheck` passed, including repository-wide Ruff checks and
  architecture, cancellation safety, timing seam, and settings-tier checks.
- `openspec validate --specs --strict`: **64 specifications passed**.
- `git diff origin/main...HEAD --check` passed.

Tests used `python -m pytest ... -q --tb=short --timeout=60`. The previously
reproduced idle-recovery timeout is documented above; that unrelated test was
not rerun in this focused post-rebase validation.

## Review follow-up

The recent-WS-failure counter is now emitted only if that gate disables an
admitted bridge. Configuration-disabled and image-generation bypasses no longer
produce a second outage bypass count; forced upstream HTTP behavior is unchanged.

`test_http_promotion_accounting.py`: **13 passed**, including three explicit
configuration/image-generation/outage precedence cases. `make lint typecheck`
and `git diff --check` passed after this fix.
