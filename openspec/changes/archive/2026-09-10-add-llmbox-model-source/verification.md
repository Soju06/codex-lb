# Verification - 2026-09-10

## Passed
- LLMBox live catalog: auto, auto-max, glm-5.3-flash, deepseek-v4-flash-0731. Existing login read without printing or copying the token.
- Native Responses text, SSE function-call arguments/call ID, and function_call_output continuation. The easy auto-max probe reported deepseek_v4_flash; no claim of Astra availability.
- Isolated ASGI codex-lb + real LLMBox: 1 explicit opt-in integration test passed. A two-request tool roundtrip through /backend-api/codex/responses completed and recorded positive input/output token usage.
- Actual desktop-bundled Codex CLI against a separate codex-lb on 127.0.0.1:2469: exit 0, shell command read marker.txt, returned company-integration-ok-7291, turn.completed present. Flags ignored user configuration/rules for this invocation, used ephemeral mode and read-only sandbox. The real client configuration was not edited. Worker usage: 25,162 input and 69 output tokens; not a claim about quota or savings.
- 165 unit tests passed (model-source service, forwarding, catalog).
- Existing routing regression run: 139 passed with one new redirect test failure; redirect handling was fixed, then all 5 new LLMBox integration tests passed. Tests cover disabled sources, exact origin, rejected supplied keys, token rotation, missing credentials, HTTP error redaction, non-followed redirects, catalog, both Responses paths and retained usage.
- Frontend: existing 19 tests plus 2 new company-source UI tests passed; TypeScript project check and scoped ESLint passed.
- Ruff and scoped Python ty check passed.
- OpenSpec change strict validation passed.
- Playwright screenshot passed using installed Chrome in an isolated browser profile and synthetic API fixtures. docs/screenshots/llmbox-source-preview.png is a UI fixture, not a screenshot of live account quota.

## Operational status
The current production listener at 127.0.0.1:2455 was not stopped, reconfigured or changed. Its read-only drain endpoint showed in_flight=2, draining=false, no blocking bridge sessions at inspection. Installation/cutover remains separate from this implementation validation, subject to the user's active-service continuity rule. Existing accounts and credentials were not imported into the test instance.

## Limits
No authoritative upstream quota/reset endpoint was verified: display unknown. Local usage only covers retained rows within 24 hours, with incomplete usage explicitly counted. Cache presence does not prove valid upstream authentication. Full image/search/compaction/resume capability parity and every model have not been tested. The settings preset offers auto-max explicitly and disabled initially; it never relabels it Astra or configures overflow. TRAE/Coco managed tasks are unchanged.

## Reproduce
Run unit/integration suites normally with no network. Opt in to the real synthetic tool probe using:

    CODEX_LB_TEST_LLMBOX_LIVE=1 .venv/bin/pytest -q tests/integration/test_llmbox_live.py

The pytest fixtures use an isolated database. This intentionally makes two real company-model calls and requires normal LLMBox login on this machine.

## Final repository checks
After syncing and archiving this change, `openspec validate --specs` reported 47 passed / 16 failed. The affected `model-source-routing` spec passed; the 16 failed specs were not changed by this work. This is not a claim that all repository specifications pass. The original production listener remained PID 69202 on port 2455 after test cleanup; only isolated listeners 2469 and 4179 were stopped.
