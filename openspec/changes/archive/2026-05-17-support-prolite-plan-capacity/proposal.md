# Change Proposal

## Problem

OpenAI can report ChatGPT Pro Lite accounts with `plan_type=prolite`. codex-lb currently preserves that value but does not recognize it as an account plan with known quota capacity or Pro-equivalent model entitlement. As a result, per-account percent-based usage can look healthy while aggregate dashboard remaining credits and capacity-weighted routing treat the account as unknown capacity, and Pro-gated models can fail account selection with `no_accounts`.

## Scope

- Recognize `prolite` as a supported account plan.
- Use Plus x5 primary and secondary capacity values for `prolite`.
- Treat `prolite` as Pro-equivalent for model plan eligibility checks while preserving the upstream plan value.
- Add regression coverage for plan normalization, usage capacity summaries, dashboard totals, and proxy account selection.

## Out of scope

- Changing stored account rows away from the upstream `prolite` value.
- Adding a database migration.
- Changing dashboard presentation copy.
