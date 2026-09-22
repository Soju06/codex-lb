## Decisions

Extend the existing pinned PR labeler without changing its permissions or additive behavior. Use a pinned metadata-only github-script workflow for newly opened issues.

The issue workflow reads the current title and adds only recognized categories plus triage. It does not interpret issue bodies or manage removals. See [context](context.md) for rationale, limitations, and rollout.
