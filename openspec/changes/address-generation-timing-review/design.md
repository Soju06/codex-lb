## Context

See proposal.md. The existing generation-timing change is already under review;
this follow-up repairs mechanical blockers without accepting its disputed metric
policy. Upstream main is integrated through a normal merge to retain review history.

## Goals / Non-Goals

Goals: restore SCIM, converge migrations, retain cheap canonical delta forwarding,
and preserve the routing sampler's historical terminal fallback.
Non-goals: deciding sample thresholds, report cohorts, null medians, custom-source
classification, or terminal-only TTFT; these remain a merge blocker.

## Decisions

- Restore missing locale lines exactly from upstream main, leaving timing strings.
- Rename the unmerged migration to 20260928_000000 and parent it to the merged
  SCIM/overflow head. Existing published migrations remain unchanged.
- Once the first non-reasoning output is known, increment canonical delta counts
  from the existing classifier. Inspect the first output once when reasoning came
  first; do not inspect every delta. Canonical counts represent observed events,
  not token counts or a guarantee of nonempty decoded content.
- Keep observed terminal evidence separate from the finalizer-entry fallback used
  by routing. This prevents local settlement from reducing an existing TPS sample.
- Remove model-source hardening and entrypoint relocation to independent PRs.

## Risks / Trade-offs

- The aggregate metric policy is pending: keep the PR draft and enumerate decisions
  in the GitHub response; do not rework those definitions without maintainer input.
- #2112 is unmerged and has an owner prerequisite: do not cherry-pick its timing
  object independently. The PR landing second must extend the first PR's carrier.
  See context.md for the concrete field mapping and integration checks.
- The stream mixin has a size gate: restore the method without raising that budget;
  any independent relocation belongs in its own PR.
