# Close live WebSocket previous-response leak

## Why

Codex Desktop `Continue` turns are still seeing raw upstream `previous_response_not_found` errors through the direct Responses WebSocket path. Prior continuity work masks many HTTP bridge and WebSocket cases, but live evidence shows either the deployed container is stale or a direct WebSocket edge still escapes the public masking contract.

Raw `previous_response_not_found` causes the client to treat the previous response anchor as permanently invalid. The desired public contract is retryable continuity failure or transparent recovery, never a raw upstream invalid-request payload.

## What Changes

- Add a live/parity verifier that proves the running container commit and checks recent Codex client logs for raw `previous_response_not_found`.
- Add a regression that matches the Codex Desktop direct WebSocket `Continue` shape and fails if any raw `previous_response_not_found` text can reach the downstream client.
- If current repo code already passes the regression, stop code changes and rebuild/redeploy the local container from the fixed commit.
- If current repo code fails the regression, harden the direct WebSocket public boundary so all previous-response misses are rewritten or suppressed into retryable terminal events.
- Add operator diagnostics for deployed build identity, request-log persistence failures, and continuity masking decisions.

This change intentionally extends, rather than duplicates, `harden-continuity-fail-closed-edges`: that earlier change covers many fail-closed rewrite cases, while this one adds the live raw-text-never-leaks invariant plus deploy/runtime parity proof.

## Non-goals

- Do not change model routing or account-selection strategy unless owner lookup proves unsafe.
- Do not weaken the existing fail-closed behavior for hard continuity.
- Do not push, open PRs, or run live Cubic while workspace PR/CI mode is ORANGE.

## Impact

- Affected code: `app/modules/proxy/service.py`, possibly `app/modules/proxy/api.py`, and continuity tests.
- Affected ops: local `codex-lb` Docker/Colima watchdog recovery.
- User-visible outcome: Codex `Continue` either succeeds, receives a retryable masked continuity error, or starts cleanly after a verified local redeploy. It never displays raw `previous_response_not_found`.
