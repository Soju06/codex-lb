## Decision

The predicate is inclusive: a reset-confirmed candidate qualifies when the
pre-refresh usage is greater than or equal to the configured percentage. The
setting is therefore a meaningful range rather than an on/off switch:

| Setting | Eligibility |
| --- | --- |
| `0%` | every confirmed reset, including a `15% -> 0%` reset |
| `50%` | only resets whose previous sample was at least `50%` used |
| `99%` | only practically exhausted resets at or above `99%` |
| `100%` | only fully exhausted resets |

The default is intentionally `0%` because the operator's desired behavior is
to warm every confirmed reset. A default of `100%` would mean almost the
opposite: it would skip resets whose previous sample was below full exhaustion.

## Safety boundaries

The threshold is only one eligibility gate. Existing reset confirmation,
post-reset availability, global/account opt-in, account-active, model
eligibility, cooldown, and durable account/window/reset deduplication remain
unchanged. The threshold does not make an unconfirmed or exhausted post-reset
sample eligible.

For a paid-to-Free transition, upstream can replace the paid window with a
fresh monthly row and there may be no comparable pre-refresh monthly sample.
The default zero threshold treats the confirmed transition as eligible. A
positive threshold fails closed until a pre-refresh monthly sample exists and
meets the configured floor.

## Migration behavior

The original threshold migration created
`limit_warmup_exhausted_threshold_percent` with `99.0` as the server default.
That column must remain unchanged while old replicas may still serve traffic.
The activation migration depends on the compatibility PR, initializes every
`NULL` active value to `0.0`, and makes the active column non-null with a `0.0`
server default. The current application exposes only that active column through
the public setting name; it maps the legacy column internally to prevent schema
drift but never dual-writes or synchronizes it. Downgrade returns the active
column to the compatibility revision's nullable, no-default shape and leaves
the legacy column untouched.
