# Context: dashboard first-run empty states

Normative requirements live in the change delta spec. This note records the
first-run review that produced the change.

## Purpose

A brand-new dashboard (no accounts, no API keys, no request logs) should tell
the operator what to do next. Filter-empty copy and zero-line charts imply
data exists and was hidden.

## Decisions

- Copy is the product fix; Accounts/APIs already expose add/create above the
  list, so they do not get a second CTA.
- Dashboard empty-account cards/list are the one place that needs a link,
  because those surfaces have no add control.
- Reports no-data is keyed off an empty `daily` array. Gap-filling zeros stay
  for sparse-but-present series.
- `/firewall` stays as a compatibility route. The redirect target is richer;
  `/settings` without query/hash stays collapsed so first-paint still skips
  Advanced self-fetches.

## Example

Empty fleet, operator opens `/accounts`: "No accounts yet" / "Add an account
to start routing." After importing one account and searching for a missing
email: "No matching accounts" / "Adjust filters."

Opening `/firewall` lands on Settings with Advanced open and the Firewall
heading in view.

## Non-goals

Guest write access, skeleton loading, and session-fail-as-admin stay out of
this change.
