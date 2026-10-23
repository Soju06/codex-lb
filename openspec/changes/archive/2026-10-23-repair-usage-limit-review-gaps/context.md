# Usage-limit review repairs

This repair completes the existing per-account policy contract; it adds no operator setting or dependency. See the change's delta specs for the requirements.

The maintainer-requested linear Alembic graph remains intact. Earlier branch snapshots reused the same scalar, override, and merge identifiers with different upstream ancestors. For example, an old scalar stamp can lack `dashboard_users`, while that stamp under the linear graph already includes the dashboard schema. Startup therefore recognizes the observed upstream checkpoints from distinctive schema markers and credential-retirement evidence before moving the ledger. Unknown marker combinations stop with an explicit error; this is not arbitrary schema-drift recovery.

The compatibility checkpoints span the observed embeddings, warmup-expiry, live-log-index, missing-cost-index, thread-cache/bridge merge, credential-retirement, and SCIM/overflow merge parents. Saved policy columns stay in place. A temporary row in the existing runtime sentinel table makes interrupted repair resumable through the ordinary Alembic cursor, and clears when canonical policy schema is reached. Credential retirement is never inferred from a revision stamp alone.

Transport repairs retain existing ownership boundaries. Websocket rejection releases provisional ownership even when settlement raises, and the registered rejection task retains a single settlement-only retry. Scope cleanup keeps its bounded wait, so a stalled settlement does not delay unrelated cleanup indefinitely. Bridge rejection defers caller cancellation across the whole cleanup and retirement sequence. Persistent settlement failures retain the existing warning behavior; this change adds no unbounded retry loop.

The dashboard fixes use the existing weekly/monthly precedence and separate provider remaining from usable credits. An unused Plus account with a 1% weekly maximum has 75.6 usable and 149.4 reserved primary credits, with no consumption. Actual weekly exhaustion still reduces provider remaining.

## Verification

Independent Claude Opus 5.5 High review found a scope-wait regression and overly
strict canonical index matching; both were corrected and re-reviewed with no
remaining actionable code findings. Final checks include 31 historical migration
cases, 9 migration state/drift cases, the head-skip regression, 28 transport cases,
179 websocket integration cases, 60 account API cases, 59 mapper/policy cases,
and 100 dashboard cases. Lint, types, strict specs, and single-head topology pass.
PostgreSQL was unavailable locally; its CI target includes the historical module.
