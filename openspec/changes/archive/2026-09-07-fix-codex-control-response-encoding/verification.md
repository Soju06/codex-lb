# Verification

## Correctness

The public-route regression failed before the fix at `response.json()` with a
gzip body beginning `1f8b08`, while HTTP status was 200. It traverses the actual
control client and loopback proxy, with native raw-byte and Python decoding
behavior, across canonical, v1, and doubled-prefix aliases. The same matrix
checks upstream 429 error normalization and exact request bytes.

Unit coverage checks singleton encoding replacement and field order for native
and SDK clients, absent/PascalCase/lowercase/mixed-case duplicate fields, direct
and routed transports, and JSON/SDP media types. The production change reuses
the existing header helper; it adds no codec or schema-processing logic.

## Live evidence before deployment

The candidate control function was loaded only into an isolated diagnostic
process using the deployed native helper and the same account route as the
reported request. No serving-process state was patched. The synthetic search
declared client compression preferences; the transport boundary asserted one
identity field and native-helper selection. The downstream adapter produced
HTTP 200 JSON with 28 results, 24,916 output characters, and 70,056 body bytes.

## Checks

- 167 upstream-client, fingerprint, and search-route unit tests passed.
- 95 focused search, control, and realtime integration tests passed in 153.55 seconds.
- Ruff lint and format checks passed.
- Strict validation passed for this OpenSpec change.
- Delta requirements are synced to the main capability, with stable context.
- Strict validation passes for `responses-api-compat`. Whole-repository strict
  validation retains 23 unrelated failures (36/59 pass); every failing spec was
  compared byte-for-byte with `HEAD` and is unchanged. Report:
  `/tmp/codex-lb-search-encoding-specs.json`.
- All implementation and scenario checks pass. Deployment follows verification.
