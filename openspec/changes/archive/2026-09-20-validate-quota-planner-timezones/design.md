## Context

The settings API trims timezone strings but stores unknown or malformed keys.
`ZoneInfo` distinguishes missing keys (`ZoneInfoNotFoundError`) from malformed
keys (`ValueError`). Only missing keys currently fall back to UTC.

## Goals / Non-Goals

**Goals:** reject invalid non-empty updates atomically and keep legacy rows usable.

**Non-Goals:** change schedule policy, migrate rows, or redesign timezone controls.

## Decisions

Validate explicit non-empty timezone updates at the API boundary with `ZoneInfo`,
returning the existing `invalid_quota_planner` dashboard error (HTTP 400). Keep
omitted, null, and blank values as no-ops, including when repairing other settings
on a legacy row. Extend the existing UTC conversion fallback to `ValueError`.
This preserves forecast and routing behavior for legacy data without rewriting it.

## Risks / Trade-offs

A previously accepted invalid key now receives a clear client error. Valid IANA
keys and aliases remain accepted by the installed timezone database. Legacy bad
keys use UTC until the operator corrects them, matching the existing missing-key
fallback rather than inventing a new routing policy.
