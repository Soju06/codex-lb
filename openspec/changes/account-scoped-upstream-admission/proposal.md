# Change: account-scoped upstream admission

## Problem

codex-lb can currently turn local usage snapshots and synthetic quota estimates into account unavailability before a request reaches upstream. That makes local dashboards or planner data act as an upstream availability oracle, causing `no_accounts` / retry-hint failures even when the upstream account might still accept real traffic.

The proxy should only fail closed before upstream for explicit local policy or local capacity guards. Upstream rate/quota failures should remain account-scoped because Codex/OpenAI exposes account credentials as the authoritative blast boundary here; local model/transport/request-kind dimensions are diagnostic metadata, not upstream policy dimensions.

## Solution

- Keep account status gates authoritative only for persisted upstream/account states and explicit local capacity guards
- Treat usage snapshots as routing pressure, ordering, and operator visibility data, not a hard pre-upstream block for foreground traffic
- Keep local account response-create/stream caps as explicit local overload reasons
- Preserve opportunistic burn safeguards as explicitly local policy, separate from normal foreground proxy routing

## Changes

- Add account-routing requirements that foreground selection MUST NOT reject solely because local standard usage is at or above 100 percent
- Add account-routing requirements that upstream 429/quota penalties are account-scoped by default unless upstream evidence proves a narrower scope
- Add regression coverage for active accounts with exhausted local usage snapshots remaining selectable

## Out of scope

- Changing explicit API-key spend/concurrency limits
- Removing account-local response-create/stream caps
- Introducing model- or transport-scoped upstream cooldowns
- Production rollout
