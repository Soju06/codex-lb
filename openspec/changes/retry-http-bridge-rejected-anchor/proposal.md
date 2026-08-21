# Retry HTTP bridge turns after clearing a rejected anchor

## Summary

When upstream rejects an HTTP bridge `previous_response_id` with
`previous_response_not_found`, codex-lb already clears that dead anchor from the
session. The same turn still fails once. Retry the turn immediately without the
anchor when the retained fresh body is already replay-safe.

## Why

The upstream error identifies a local continuity anchor that no longer exists.
After the fenced clear succeeds, a replay-safe fresh body can continue the same
user turn without asking the client to recover.

## What Changes

- Retry once on the existing fresh-upstream replay path after a rejected anchor
  is cleared.
- Keep unsafe, already-retried, eventful, or fenced-clear-missed requests on the
  existing fail-closed path.
- Do not charge the retry-circuit for this proxy-chosen recovery.

## Non-Goals

- No live deployment.
- No change to account selection, owner fences, replay-safety rules, or public
  error envelopes beyond the recovered turn no longer seeing the first error.
