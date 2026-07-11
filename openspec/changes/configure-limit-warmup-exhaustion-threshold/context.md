# Context

## Purpose

This change keeps reset-confirmed limit warm-up useful when upstream quota
reporting never reaches a literal `100` value. The intent is not to warm active
accounts at arbitrary usage levels; it is to let operators define what counts as
an exhausted pre-reset sample.

## Decision

Add a separate `limit_warmup_exhausted_threshold_percent` setting instead of
reusing `limit_warmup_min_available_percent`. The existing minimum-available
setting describes the post-reset remaining quota gate. The new threshold
describes the pre-reset exhaustion gate.

## Default

The default is `99.0` because upstream payloads can plateau at 99 percent for
windows that are effectively exhausted. Operators who need strict historical
behavior can set the value back to `100.0`.

## Constraints

Warm-up still requires the selected window's `reset_at` to move forward and the
post-refresh usage sample to report `used_percent < 100`. The threshold does
not create a manual warm-up button, bypass per-account opt-in, or alter request
deduplication by account/window/reset tuple.

## Example

If the previous 5h sample is `used_percent = 99`, the new 5h sample is
`used_percent = 0`, and `reset_at` advanced, the default threshold allows one
warm-up attempt for that account/window/reset tuple.
