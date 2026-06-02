## Why

The PR adds new request-log failure metadata fields and maps additional local
routing errors into log rows. Without OpenSpec documentation this schema and
observability behavior change is not anchored to a contract.

## What Changes

- Make local routing failure errors from the proxy load balancer explicit local-only:
  `no_plan_support_for_model`, `additional_quota_data_unavailable`,
  `no_additional_quota_eligible_accounts`.
- Keep request-log failure metadata schema migration linear with current
  `main` migration heads to avoid Alembic multiple-head check failures.
- Extend request-log observability guidance so dashboard/API consumers
  understand that local routing failures must not infer an upstream status code.

## Impact

- Preserves failure telemetry correctness for local routing decisions.
- Keeps migration state linear and safe to install on current `main`.
- Adds explicit OpenSpec requirements for request-log metadata and migration
  governance.

