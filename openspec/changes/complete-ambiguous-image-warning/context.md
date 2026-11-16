# Context and verification

## Decision
Reuse the existing `_http_bridge_image_send_is_ambiguous` predicate, append a shared warning to reader disconnect and sender exception messages, and leave all retry/control-flow decisions intact. Preserve the original diagnostic text. Explicit payload_too_large bypasses the warning.

Example: `Upstream websocket closed before response.completed The image request may still be executing. Automatic replay was suppressed because retrying may duplicate upstream work.`

## Evidence
- Historical run_contract.py: RED 3 failed / 1 passed; GREEN 4 passed.
- New real ASGI HTTP routes: RED 9 failed / 3 passed, all nine failures at missing warning assertions. GREEN 15 passed including three text controls.
- Final scoped run (new contract, existing peer-1009 send coverage, selected clean-close/eventless unit boundaries): 36 passed, 1053 deselected.
- ruff check on all four changed Python files: passed. git diff --check: passed.
- Tests used `env -u CODEX_LB_TEST_DATABASE_URL PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 .worktrees/image-bridge-trial/.venv/Scripts/python.exe`; pytest cache disabled. Existing conftest creates isolated temporary SQLite storage; no live upstream calls.

## Compatibility and limitations
`/v1/responses` and trailing slash retain 502 JSON errors; backend stream disconnects retain HTTP 200 with response.failed SSE, while send exceptions remain 502. Peer 1009 remains 400 payload_too_large. Images send once without replay; text generic/clean controls still retry once and finish, text send exceptions still fail after one send.

No deployment, service restart, Native changes, trial code edits, configuration edits, or git commits. Existing uncommitted image/100s/peer1009 work is preserved. OpenSpec CLI was not available on PATH, so strict validation was not run; change remains unarchived because archiving/main-spec sync are outside the authorized write scope. Full test suite and live business traffic were intentionally not run. Existing test fixtures may leave codex-lb-tests-* temporary directories on Windows.
