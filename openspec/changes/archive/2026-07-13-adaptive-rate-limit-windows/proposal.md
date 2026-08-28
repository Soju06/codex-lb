## Why

On 2026-07-12 OpenAI temporarily removed the 5-hour Codex usage limit for Plus, Business, and Pro plans (weekly limits remain), and upstream `openai/codex` has been duration-driven since May 2026: window kinds are derived from server-reported durations, and a primary short window is no longer guaranteed to exist. codex-lb still assumes the primary (5h) window keeps being reported. When upstream stops emitting it, the last stored primary row is never rewritten and codex-lb degrades in four verified ways:

- Selection state only zeroes stale samples at `used_percent >= 100`, so a frozen sub-100% primary row (e.g. 87%) permanently holds the soft-drain tier, budget-safe stickiness pressure, and primary-ordered strategies.
- A persisted `rate_limited` account can never recover through the background scheduler: recovery evidence for non-monthly plans is the primary row alone, which is never written again.
- The updater freshness gate is keyed on the latest primary row, whose elapsed `reset_at` permanently defeats freshness and forces an upstream fetch on every sweep visit.
- Aggregated downstream rate-limit surfaces (`x-codex-primary-*` headers and the rate-limit status payload) keep serving the frozen primary sample with a reset time in the past, and can report `limit_reached` from data that will never update.

## What Changes

- Selection-state building treats any main-window usage sample whose `reset_at` has elapsed as a reset window (0% used, reset cleared), generalizing the existing `>= 100%` rule; the rule stays after the weekly-primary remap and mutates only derived selection inputs.
- Rate-limited recovery evidence generalizes to the most recently recorded main-window row: when a post-block refresh no longer reports a short primary window, a fresh long-window row proves recovery instead of pinning the account `rate_limited` forever.
- The elapsed-reset staleness rule applies only when the elapsed row is the account's most recently recorded main-window row; a strictly newer sibling-window row's freshness governs otherwise.
- Aggregated downstream rate-limit surfaces expire elapsed window samples the same way selection does, so clients stop seeing frozen used percentages, past reset timestamps, and phantom `limit_reached`.

Explicitly out of scope (follow-up changes): quota-planner/limit-warmup window-duration generalization, dashboard account-summary display of absent windows, api-keys pooled primary-credit semantics, live rate-limit ingestion from proxied response headers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: selection state expires elapsed usage windows regardless of the sample's used percentage.
- `usage-refresh-policy`: blocked-status recovery accepts the newest main-window evidence; elapsed-reset staleness is superseded by strictly newer sibling rows; aggregated rate-limit surfaces expire elapsed samples.

## Impact

- Code: `app/modules/proxy/load_balancer.py`, `app/modules/usage/updater.py`, `app/core/usage/__init__.py`, `app/modules/proxy/_service/rate_limit.py`
- Tests: `tests/unit/test_load_balancer.py`, `tests/unit/test_usage_updater.py`, `tests/unit/test_proxy_rate_limit.py` (or the suites that currently cover these paths)
- Specs: `openspec/specs/account-routing/spec.md`, `openspec/specs/usage-refresh-policy/spec.md`
