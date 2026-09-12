## Why

What this instance actually transmits is unusually tame. The account count
leaves as a bucket string (`2-5`), so do the cost, database size and API-key
counts; user-agents and model names collapse to `other` unless they are on a
fixed allowlist; upstream errors are reduced to at most five allowlisted
*codes*; and the schema is `extra="forbid"`, so a stray field is a validation
error rather than a leak. No hostname, no IP, no account identity, no prompt or
response content, no keys.

The disclosure does not read that way. On first dashboard entry the operator
gets a **blocking modal with a backdrop** — mounted globally in `AppLayout`, so
it covers whatever page they landed on — containing a title, a description, a
categories paragraph, an opt-out-notification paragraph, a label, and then a
scrolling dump of the raw JSON envelope, before reaching two buttons. Dismissing
it with Escape or the backdrop persists nothing, so it **returns on the next
dashboard entry**, and every return repeats the wall.

The result is a surface that reads like "we are about to take your data" for a
payload that is mostly booleans and buckets. A downstream fork deleted telemetry
outright and advertises its absence as a feature; that is the reaction this
surface invites. The goal here is the opposite of hiding anything: say the same
true thing in one short paragraph, keep the exact envelope one click away, and
stop asking.

## What Changes

- The dialog body becomes **one short paragraph** stating what is sent and what
  is not. The separate `categories` and `optOutNotice` paragraphs leave the
  dialog; the opt-out notice stays where it is actionable, on the settings card.
- The exact envelope stays in the dialog and stays exact, but **collapsed behind
  one disclosure control** instead of rendered as a wall. The decision actions
  are reachable without scrolling past it.
- **Dismissing without deciding is remembered in the browser**, so the dialog is
  shown once rather than on every dashboard entry. Consent state is untouched by
  a dismissal — it stays `undecided` and the backend behaves exactly as before.
- Copy is rewritten to be short and flat across all three locales.

## What deliberately does not change

- **Enable and disable stay equally prominent, with equal click cost.** The
  existing requirement forbids favouring one, and making opt-out quieter would
  damage the trust this change exists to repair. Both actions keep the same
  variant and stay side by side.
- **Consent semantics are untouched.** `undecided` still resolves active,
  `CODEX_LB_TELEMETRY_ENABLED` still applies while undecided, a persisted
  decision still wins, and the settings toggle and its environment notice are
  unchanged.
- **No field is removed from the payload and none is hidden.** The envelope
  shown is still the exact one the sender would transmit, from the same shared
  constructor, and the settings card still exposes it on demand for any consent
  state.

## Impact

- Affected capability: `telemetry`. One MODIFIED requirement
  (`One-time consent dialog with exact payload preview`). No ADDED, no REMOVED.
- Dashboard-visible: before/after screenshots accompany the PR.
- Frontend only. No API change, no schema change, no settings change.
- Locale files `en`, `ko` and `zh-CN` move together.

## Known gap, not addressed here

`resolve_consent` treats `undecided` as active and the scheduler sends its first
snapshot at startup, so a snapshot can leave before the operator has seen this
dialog at all. Calmer copy makes that mismatch more visible, not less. Changing
it is a consent-semantics decision for the owner rather than a copy change, so
it stays out of this change; the dialog continues to say plainly that telemetry
is on by default.

`docs/telemetry.md` currently tells the reader to "assume transmitted snapshots
remain stored until a published retention policy or explicit deletion", which is
the most alarming sentence in the documentation. Replacing it needs the
collector's actual retention period, which is not recorded anywhere in this
repository, so it is left for whoever can state that number.
