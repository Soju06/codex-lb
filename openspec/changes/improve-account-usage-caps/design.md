## Context

See `proposal.md`. Usage writes occur through several routes, reused WebSockets run turn-level routing checks in sequence, weekly pace consumes normalized account summaries, and all capped quota bars share `UsageCapMarker`.

## Goals / Non-Goals

**Goals:**
- Make every successful standard-usage writer refresh the two cap caches and cross-replica namespace.
- Preserve trusted-capability account switching before evaluating a reused account's cap.
- Keep weekly dashboard math aligned with routing's weekly-window predicate.
- Add a non-color visual distinction to every reserved quota segment through the shared marker.

**Non-Goals:**
- Changing cap values, error envelopes, or pinning semantics.
- Adding animation, image assets, dependencies, or a new dashboard control.

## Decisions

1. The usage endpoint calls the existing post-write cap refresh helper only when the updater reports that it persisted usage. Centralizing every writer inside the updater was rejected because the updater is also used by flows that already control cache settlement and because this targeted call closes the identified gap without changing those contracts.
2. The reused-socket cap gate moves after trusted-capability rerouting and applies only while an upstream socket remains reusable. A capability switch retires the old upstream and proceeds through normal account selection, where capped candidates are already excluded. Special-casing the cap predicate based only on the capability flag was rejected because it could admit a still-reused capped authorized account.
3. Weekly pace uses the shared 10,080-minute weekly-window predicate before applying `usage_cap_weekly_percent`. Duplicating the duration literal was rejected to keep dashboard and routing semantics aligned.
4. `UsageCapMarker` owns the hatch using a CSS repeating linear gradient over the existing neutral base. This covers all four quota surfaces without image assets or duplicated styles; the existing ARIA label remains the textual alternative.

## Risks / Trade-offs

- [Cache refresh adds work after a usage write] → It runs only after persisted usage and reuses the failure-isolated helper already used by other writers.
- [A dense hatch can obscure very small bars] → Use a compact but low-contrast stripe interval and retain the border separating reserved and usable regions.
- [Moving the WebSocket check could accidentally bypass caps] → Gate only when an upstream remains open and cover both switched and retained-authorized cases.
