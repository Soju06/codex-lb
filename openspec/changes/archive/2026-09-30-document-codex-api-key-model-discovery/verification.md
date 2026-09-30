# Verification

Verified with Codex 0.159.0.

## Scoped checks

- Parsed the downloadable TOML and all five inline TOML blocks. Both provider
  catalog URLs match their base URL plus `/models`; inline and downloadable
  discovery settings agree.
- Ran the existing config-consuming tests before the change: 5 passed.
- Ran the updated tests with installed-Codex E2E enabled: 5 passed.
  The local fixture observes authenticated catalog requests, authenticated
  capability-routed WebSocket/HTTP inference attempts, and no requests when
  the required API key is missing.

```bash
CODEX_LB_RUN_CODEX_PROFILE_E2E=1 uv run --no-sync pytest -q \
  tests/e2e/test_codex_daybreak_profile.py \
  tests/integration/test_proxy_websocket_responses.py::test_codex_provider_profiles_route_before_first_account_attempt
uv run --no-sync ruff check tests/e2e/test_codex_daybreak_profile.py
uv run --no-sync ruff format --check tests/e2e/test_codex_daybreak_profile.py
uv run --no-sync mkdocs build --strict
```

Ruff and the strict documentation build passed. Python diagnostics reported
no errors or warnings.

## Real client boundary

Loaded the shipped example into an isolated app-server process, changing only
the deployment origin and supplying the API-key environment setting. After
`initialize` and in-memory authentication, `model/list` returned
`gpt-6.1-sol` with `hidden: false`. No inference request was submitted.
The temporary process exited successfully and its home was removed.

The negative controls from the investigation omitted Sol without discovery
settings and with only the discovery feature enabled. Adding the explicit
catalog URL made it visible through the same client binary and provider.

## OpenSpec

The change delta passes strict validation and is synced to the main spec.
Repository-wide `openspec validate --specs --strict` reports 50 passing and
17 failing specs. The unchanged baseline has the same 17 failures; the owning
`model-catalog-compat` spec has the same three missing-SHALL/MUST errors at
`requirements.6.text`, `requirements.22.text`, and `requirements.28.text`.
The baseline and candidate owner validation reports are identical.

Those pre-existing spec errors are outside this configuration change. Full
local CI was not run; verification was limited to the affected config
consumers, documentation, and specification delta.
