## Context

The retention card already receives both the effective request-log retention
value and the nullable dashboard override. It edits overrides in local React
state and persists them only through the card's existing save button.

## Goals / Non-Goals

**Goals:**

- Make effective disabled request-log pruning unmistakable to operators.
- Offer the existing 30-day safety floor and a conservative 90-day option as
  quick form presets.
- Preserve every stored policy until the operator explicitly saves.

**Non-Goals:**

- Changing retention defaults, validation floors, API contracts, scheduler
  behavior, usage-history retention, or any unrelated Settings surface.

## Decisions

- Drive the warning from `settings.requestLogRetentionDays`, the API-provided
  effective value, rather than inferring policy from the nullable override.
- Render the presets only with the disabled-state warning. A preset updates
  only the request-log input's local state; the existing save path remains the
  sole persistence path.
- Keep validation and payload construction unchanged so a preset follows the
  same focused override semantics as a manually entered value.
- Localize all new copy in every supported dashboard locale.

## Risks / Trade-offs

- [Risk] An operator may mistake a selected preset for an applied policy.
  → Keep the disabled warning visible until refreshed settings report a
  non-zero effective value, and require the existing explicit save action.
- [Risk] UI shortcuts could drift from backend validation.
  → Use constants already matching the 30-day floor and values covered by
  component tests.
