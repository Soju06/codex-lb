## 1. Persisted Capabilities

- [x] 1.1 Add the `supports_decisions` and `supports_systemone` columns with a
  `false` server default and an idempotent migration that tolerates
  pre-existing columns.
- [x] 1.2 Surface both flags in the model-source create/read/update schemas.
- [x] 1.3 Surface both flags in the dashboard model-source form, capability
  badges, and locales.

## 2. Routing

- [x] 2.1 Generalize the embeddings source lookup into a capability-keyed
  lookup covering embeddings, decisions, and systemone.
- [x] 2.2 Add `POST /v1/decisions` and `POST /v1/systemone`, forwarding the
  payload verbatim beyond the validated fields.
- [x] 2.3 Return `model_not_found` when no source qualifies, with no
  subscription-account fallback.
- [x] 2.4 Reject `/v1/systemone` requests without `model` before dispatch.
- [x] 2.5 Add both paths to the pre-body capability deny list.

## 3. Accounting

- [x] 3.1 Parse Responses-style token usage from decision responses.
- [x] 3.2 Fail closed with `usage_unavailable` when a limited API key needs
  usage the source did not report.
- [x] 3.3 Record success and error request logs with the upstream status.

## 4. Verification

- [x] 4.1 Add integration coverage for routing, payload preservation,
  capability independence, missing-source, upstream-error, transport-failure,
  and usage-accounting paths on both routes.
- [x] 4.2 Extend the capability route inventory and provider-routing cases.
- [x] 4.3 Add frontend coverage for the capability defaults and the enabled
  submit path.
- [x] 4.4 Run Ruff check/format, type checks, `make lint`, and the frontend
  checks.
