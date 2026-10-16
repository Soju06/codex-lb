## Context

PR #1700 intentionally made reset-confirmed warm-up independent of the legacy
exhaustion threshold. The dashboard field remained visible, however, so its
value no longer affected behavior. This change restores the field's contract
while retaining the user's preferred default of warming every confirmed reset.

The warm-up service already receives before/after usage snapshots and the
settings object. Candidate construction is therefore the narrowest ownership
point for applying the threshold consistently to ordinary selected-window
resets and the paid-to-Free fallback.

## Goals / Non-Goals

**Goals:**

- Make the existing threshold effective at the candidate boundary.
- Use `0.0` as the default and accept it through every settings layer.
- Preserve confirmed-reset safety gates and durable deduplication.
- Make the paid-to-Free fallback obey the same operator setting.
- Migrate the old default without overwriting explicit non-default choices.

**Non-Goals:**

- Changing global or per-account warm-up opt-in.
- Changing reset confirmation, availability, cooldown, sender preflight, or
  attempt identity semantics.
- Adding a new public setting, dashboard control, API field, background worker,
  or retry path.

## Decisions

### Apply an inclusive threshold before availability and reset checks

`_build_candidate()` rejects a sample only when `before.used_percent` is below
the configured threshold. The existing `usage_reset_confirmed()` predicate
then remains authoritative for temporal reset evidence. Applying the gate at
this boundary keeps the setting scoped to reset-confirmed warm-up and prevents
it from affecting staggered idle warm-up.

Alternative considered: remove the field or hard-code zero. That would match
the current desired default but leave operators unable to select the previous
strict behavior and would keep the dashboard control misleading.

### Reuse the same gate for paid-to-Free fallback

The paid-to-Free path uses a monthly row that may not have a pre-refresh
sample. Zero permits the confirmed transition without inventing a comparison;
positive values require a real prior monthly sample and apply the same
inclusive comparison. This avoids treating missing evidence as satisfying a
non-zero operator threshold.

### Make zero valid in all settings layers

Backend Pydantic fields use `ge=0`, frontend Zod schemas use `nonnegative()`,
and the numeric control uses `min={0}`. Repository/model defaults and the
active storage server default all use `0.0`, so new settings rows and API/UI
defaults agree.

### Activate the staged column directly

The activation migration depends on the compatibility revision. It updates all
existing `NULL` staged values to `0.0`, then changes that already-existing
column to non-null with a `0.0` server default. The ORM exposes the public
setting only through this active column and keeps the legacy column mapped under
an internal name solely to prevent schema drift. It performs no dual write and
does not create database triggers.

Downgrade returns the active column to the nullable, no-default shape supplied by
the compatibility revision and leaves the legacy column untouched.

## Risks / Trade-offs

- [Risk] Existing configured thresholds are reset to the requested new
  default. → This is an explicit activation release; the migration contract
  initializes every existing row to `0.0`, as requested by the operator.
- [Risk] Old and current replicas may serve concurrently. → The prerequisite
  compatibility release widens both API schemas before this migration is
  deployed; this migration does not attempt to synchronize legacy writes.
- [Risk] A zero threshold could send more warm-up traffic. → Warm-up remains
  globally and per-account opt-in, post-reset availability-gated, bounded by
  the existing sender/concurrency controls, and deduplicated per reset tuple.
- [Risk] Missing paid-to-Free history could be treated too permissively. →
  Missing history is eligible only at zero; every positive threshold fails
  closed without a prior sample.

## Migration Plan

Deploy the compatibility revision first, then apply this activation revision
during the normal startup migration. Rollback downgrades only the active column
back to the compatibility revision's nullable shape; the legacy column is never
rewritten.
