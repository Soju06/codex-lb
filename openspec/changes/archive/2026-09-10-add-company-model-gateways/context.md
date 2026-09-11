# Evidence and decisions

User requests direct company-model integration into the existing local codex-lb and explicitly excludes GLM. The active client uses port 2455; it must not be restarted without continuity or coordinated interruption.

TRAE raw-chat evidence: https://bytedance.larkoffice.com/docx/ZIjudHwSJooHbcxqJcjc6wCRnJg revision 285; community bridge commit 77e8bf0f9c0fe66aa33715b36b5324ccf71bef9a inspected locally. The existing Cloud-CLI-JWT login completed direct inference and tool-result continuation for all 27 current non-GLM catalog variants through isolated codex-lb services. Astra additionally passed a real Codex CLI shell-tool/read-marker task. See trae-verification.json for sanitized per-model results. This is an internal client protocol, not an official third-party compatibility guarantee.

LLMBox native Responses and tool roundtrip were verified in the preceding archived change. TTADK uses the same gateway family with a different credential/source catalog; its extra GLM model is excluded. Coco/TMates independent inference capability remains to be classified from current evidence. Do not wrap an agent final answer and advertise it as equivalent raw model/tool behavior.

Gemini compatibility: current traecli_next header profile is required for Gemini Flash; both Gemini entries emit an empty finish_reason on their terminal done event. The adapter accepts that event only for Gemini with actual output. Both Gemini models passed the same two-request tool loop after this correction. Header profile evidence was inspected in the internal byte-proxy source; no installer or client configuration change was executed.

These checks establish synthetic tool-loop compatibility, not exhaustive production parity, concurrency resilience, vision support, or long-session stability. Actual upstream model identifiers may differ from catalog labels and are preserved in response metadata; no model fallback was introduced. Production deployment remains pending.

Codebase native gateway evidence: internal pi-bytesec-provider index.ts model specs, linked from https://bytedance.sg.larkoffice.com/docx/FrXBdtjPWosmT0xaqVxli6IzgZd revision 1085. The fixed Model gateway authenticated with the normal Codebase-backed local TRAE login. GET /models returned 404. Eight of twelve source-documented native Chat model IDs passed synthetic function-call/result continuation through isolated codex-lb: seed-code-preview, doubao-seed-2.0-code, doubao-seed-1.8, deepseek-v3.1, kimi-k2.6, kimi-k2.5, qwen3.6-plus and qwen3.5-plus. See codebase-chat-verification.json. Native openrouter models returned stream errors and deepseek-v3.2 returned HTTP 404; this does not negate the separately verified TRAE raw-chat routes. Bare native GPT names collided with subscription routing; the explicit codebase/ local namespace now preserves exact upstream IDs while selecting the company source.

Correction to an earlier implementation assumption: codex-lb's existing Chat/Responses conversion supports Chat clients using Responses backends, not Responses clients using Chat backends. Coco Chat success alone does not satisfy the Codex end-to-end requirement. The new reverse conversion remains required and must be verified before enabling those models for Codex.

Native Responses result after namespace correction: codebase/gpt-5.4 and codebase/gpt-5.2 passed the full function-call/result loop. codebase/gpt-5.2-codex completed the first tool call but its second request returned upstream HTTP 400; it remains unverified and disabled. Production PID 69202 continues listening on 2455 through local.codex-lb LaunchAgent; no production restart or client routing change was made.

Direct native GPT-5.2-Codex diagnosis bypassed codex-lb with the same synthetic two-turn request: first output contained reasoning and function_call; replaying that output returned HTTP 400, invalid_encrypted_content, because upstream could not verify/decrypt its own reasoning item. The adapter does not discard reasoning to force a passing test. This is an upstream continuation limitation requiring a supported sticky-session/continuation contract or explicit exclusion.

Latest verification checkpoint: 22 targeted Python tests passed before namespace addition; both native public-route tests passed after namespace addition and asserted stripped upstream identity. Ruff and ty passed after the final safe error-message change. Frontend TypeScript, scoped ESLint and three settings tests passed. OpenSpec strict validation passed with native-gateway and namespace scenarios.

Next implementation: add an explicit per-model native Chat backend and Responses-to-Chat translation (including tool calls, custom tools, encrypted reasoning continuity, stream failure and cancellation), then verify eight known-working Chat models through Codex Responses and a real Codex CLI. Do not claim the existing opposite-direction compatibility converter solves this. Complete native model catalog/UI, inventory remaining Coco-only names and TMates boundaries, LLMBox explicit non-GLM verification, then prepare production continuity/cutover.

Historical probe checkpoint: ports 2469 and 2470 ran the two isolated matrix
servers. Both were inspected and stopped after the replacement release passed
its checks. No inference matrix remains running. Original port 2455 is
untouched. The native GPT/Chat matrix source rows exist only in isolated
database copies.

Release readiness checkpoint: a self-contained release was built at
`~/.codex-lb/releases/20260910-company-models` with its own frozen Python
environment and dashboard assets. It runs on port 2471 against a SQLite backup,
with 38 enabled model rows across TRAE (27), Codebase / Coco (10), and LLMBox
(1). Health, catalog projection, unknown-quota rendering, and real Codex CLI
shell-tool tasks passed for Astra, Kimi K2.6, and LLMBox DeepSeek; all returned
the exact marker. The original LaunchAgent plist is backed up at
`~/.codex-lb/backups/local.codex-lb.pre-company-models.plist`. Ports 2469 and
2470 were stopped after their probes; 2471 remains the ready replacement. The
production listener on 2455 still serves the original release and has not been
drained, restarted, or reconfigured.

The production listener is direct rather than fronted by a stable proxy, so a
truly seamless executable replacement is not possible. The cutover runbook is:
start the internal drain, wait for `in_flight=0`, take a fresh SQLite backup,
switch the LaunchAgent to the independently verified release, restart, verify
health, create and enable the three prepared sources, and run the three real
Codex CLI smoke tests. Any failure restores the saved plist and database backup.
This final interruption must be coordinated with the user under the local
service cutover policy.

2026-09-10 follow-up: all eight native Chat models passed the same function-call/result loop through /v1/responses using the explicit Chat backend. Kimi K2.6 additionally passed real Codex CLI shell-tool file reading with exact marker output; usage input 17357, cached 8704, output 93, reasoning 62. Chat reasoning is encrypted locally and bound to source/model; upstream errors and EOF fail explicitly. Public-route tests cover multiple calls, continuation, missing terminal events, cancellation releasing maxConcurrency=1 and disabled presets. Codebase / Coco dashboard preset contains ten disabled verified entries. UI screenshot docs/screenshots/codebase-source-preview.png was visually checked with synthetic data.

LLMBox /models still lists auto, auto-max, glm-5.3-flash, deepseek-v4-flash-0731. The explicit DeepSeek ID passed native Responses tool continuation through isolated codex-lb. The preset now uses only this fixed model and leaves it disabled; auto aliases are excluded from the planned enabled set because they cannot guarantee the no-GLM policy. No production routing changes occurred.
