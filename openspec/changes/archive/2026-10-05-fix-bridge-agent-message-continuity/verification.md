## Regression evidence

Before implementation, the fresh-bridge cases failed on both HTTP routes:
the upstream payload incorrectly contained `previous_response_id`. The
unavailable-owner case also exposed selection of the alternate account.

The final focused route matrix passes all 18 cases across
`/backend-api/codex/responses` and `/v1/responses`. It covers fresh sockets,
explicit stale-anchor rejection, subsequent turns using the returned turn-state
header, incomplete output, unknown manifests, malformed agent messages,
unavailable owners (including quarantined bridges), missing operation fences,
and errors after visible output.
Successful recovery preserves the entire original input and `store=false`.

Unit coverage separately checks assistant-output and tool-manifest proofs,
multiple agent messages, plaintext and encrypted content, malformed shapes,
missing parallel calls, premature interleaving, account-neutral rejection, and
non-mutation of input.

## Local validation

- `uv run pytest tests/unit/test_replay_safety.py tests/unit/test_proxy_http_bridge.py tests/unit/test_http_bridge_safe_continuity.py tests/unit/test_http_bridge_replay_rejection.py tests/unit/test_websocket_terminal_cancellation.py tests/unit/test_defer_cancellation_shield_leak.py -q --timeout=60 --tb=short --show-capture=no`: 1,350 passed.
- `uv run pytest tests/integration/test_http_responses_bridge.py -q -k preserves_agent_message_full_resend --timeout=30 --tb=short --show-capture=no`: 18 passed.
- `make lint typecheck`: passed, including architecture, cancellation, timing-seam, settings-tier, migration-topology, Ruff, and type checks.
- `npx --yes @fission-ai/openspec@1.11.0 validate fix-bridge-agent-message-continuity --strict`: passed before archive.
- `npx --yes @fission-ai/openspec@1.11.0 validate --specs --strict`: 67 specifications passed before archive; revalidated after synchronization.

The integration tests use synthetic accounts, temporary databases, and stubbed
upstream sockets. No live model generation or production rollout is included.
Frontend, Rust, Docker, PostgreSQL, and the full repository CI matrix are outside
this targeted local validation; cloud PR checks remain separate merge gates.
