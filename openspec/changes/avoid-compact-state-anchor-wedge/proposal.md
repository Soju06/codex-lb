# Avoid compact state-anchor wedge

## Why

Oversized compact requests can get stuck when historical state anchors alone
exceed the local upstream wire budget. Refusing compaction leaves the user with
no safe continuation path.

## What changes

- Permit compact trimming to demote oldest historical goal/plan state anchors
  when the required set cannot fit.
- Preserve current state anchors, structural Lite/developer state, terminal
  required items, and side-effect fail-closed guards.
- Keep retained tool-call history reconciled by dropping omitted call/output
  pairs together.

## Non-goals

- Change the compact upstream budget.
- Split one compact request into multiple upstream requests.
