# Source comparison and live controls

Baseline: minpeter `addaac05000f0544317bc7d7bf1794293b59d31b` and official
Codex `rust-v0.153.4`, the installed and latest stable release checked on
2026-09-09. The final PR ports only the routing-hint code onto main
`3987abfdf4a9cf372dde2f6694df2e4469bee602` and adds compaction propagation.
The remote-credential, retry, and benchmark-tooling changes from the fork are
outside this PR. The patch has not been deployed.

Routing-hint implementation adapted from minpeter commits
`1359906bb093c72e94c7f54d160a007277bb5656`,
`a406538eb888383f4b7b291062aa0aeddbe89c4d`,
`8cfbe662ed274ee452a935f397cefea6282f20fb`, and
`74d2c65699d89557e14fa29cc4aff1e43875378f`.

## Source evidence

- `codex-rs/core/src/client.rs:697` builds a compaction routing hint;
  `:1107` restricts synthesis to the Codex backend and uses the final model/tier;
  `:1622` and `:1769` apply it to HTTP and WebSocket requests.
- `codex-rs/protocol/src/openai_models.rs:908` removes unsupported service tiers.
- `codex-rs/core/src/client.rs:222` feeds the request tier into telemetry;
  `codex-rs/otel/src/events/session_telemetry.rs:1012` reports that metadata on
  completed events. It does not prove an upstream grant.
- Native prewarm sends `generate=false`; persistent WebSocket reuse does not
  necessarily replace its initial handshake when the tier changes. The fork's
  reuse behavior is not by itself evidence of a tier bug.

Sources:
[client.rs](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/src/client.rs),
[model tier filtering](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/protocol/src/openai_models.rs#L908),
[telemetry](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/otel/src/events/session_telemetry.rs#L1012).

## Direct OpenAI control

On 2026-09-09 09:10–09:12 UTC, official Codex ran with an isolated temporary
home, `model_provider="openai"`, ChatGPT OAuth from the same current Pro account,
no base-URL override, no proxy environment variables, and no inherited user
configuration. Model: `gpt-5.6-sol`, effort: low, one fixed sentence, no tools.

| Configured tier | Request telemetry | Generated response tier | Input | Cached | Output |
|---|---|---|---:|---:|---:|
| default | absent | default | 11118 | 9600 | 14 |
| fast | priority | default | 11121 | 9600 | 14 |
| ultrafast | absent | default | 11124 | 9600 | 14 |

The native catalog advertised priority/Fast but not ultrafast. Source filtering
and the model-only routing hint explain the Ultrafast control: it ran without
that requested tier. All three also emitted a separate prewarm completion with
tier auto and zero output. Prewarm is excluded from the generated-response rows.
These are token counts, not an account-quota or billing multiplier measurement.

## Compaction regression and live check

Before the patch, a public compact route test failed because the emitted hint
was absent. The fixed transport receives explicit subscription provenance from
the service; it never infers eligibility from an optional account header.

At 09:16 UTC the actual fork compact transport made one call with synthesis off
and one with it on, using the same account, proxy route and payload hash. Both
returned a valid compaction and actual tier default. Input tokens were 69 in
both; output tokens were 69 and 62, with no reasoning or cached tokens. The on
variant emitted `model=gpt-5.6-sol;tier=priority`; the off variant emitted no
hint. This verifies propagation without proving a speed or usage reduction.

Native successful priority/ultrafast grants were not observed in these controls.
The source gap is repaired, but upstream Fast activation remains unexplained.

## Verification

Final-branch validation: 422 focused tests passed (173 client/header/egress,
111 compact/transport-selection, 59 compact integration, 2 HTTP-bridge,
76 bridge-session/reroute, and 1 direct-WebSocket reuse). Repository-wide lint,
formatting, architecture and type checks passed. Strict OpenSpec validation
passed for all 64 specs. Coverage includes HTTP egress
with and without an account-ID header, priority/ultrafast/model-only hints,
non-subscription source exclusion, persistent WebSocket opens, model-less
preconnects, connection reuse, pre-dispatch failover, WebSocket-to-HTTP fallback,
and compact API normalization/policy enforcement.

This PR scopes compact synthesis to proxy-routed client requests. Standalone
low-level calls, including existing background warmup/automation probes, retain
the default opt-out. No new setting is introduced.

Public compact paths accept both backend and v1 requests. The existing
trailing-slash contract remains HTTP 405 with an OpenAI error envelope and no
upstream dispatch. The response tier is never rewritten to the requested tier.

Live measurements above were collected before the port on the reviewed fork;
local transport and route regressions validate the final main-based patch.
Credential snapshots and full native traces were temporary and removed.
