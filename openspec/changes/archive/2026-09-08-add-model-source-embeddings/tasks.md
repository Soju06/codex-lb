## 1. Persisted Capability

- [x] 1.1 Add the `supports_embeddings` column with a `false` server default
  and an idempotent migration that tolerates a pre-existing column.
- [x] 1.2 Surface the flag in the model-source create/read/update schemas.
- [x] 1.3 Surface the flag in the dashboard model-source form and locales.

## 2. Routing

- [x] 2.1 Add a repository lookup that selects an enabled source declaring
  the embeddings capability for the requested model.
- [x] 2.2 Add `POST /v1/embeddings` and forward the payload verbatim beyond
  the validated `model` and `input` fields.
- [x] 2.3 Return `model_not_found` when no source qualifies, with no
  subscription-account fallback.

## 3. Accounting

- [x] 3.1 Parse prompt/total token usage from the embeddings response shape.
- [x] 3.2 Fail closed with `usage_unavailable` when a limited API key needs
  usage the source did not report.
- [x] 3.3 Record success and error request logs with the upstream status.

## 4. Verification

- [x] 4.1 Add integration coverage for capability routing, the missing-source
  path, and usage accounting.
- [x] 4.2 Add frontend coverage for the capability default and the enabled
  submit path.
- [x] 4.3 Run Ruff check/format, type checks, and the migration round-trip
  test.
