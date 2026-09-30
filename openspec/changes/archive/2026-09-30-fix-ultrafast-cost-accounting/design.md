## Decisions

Extend the existing ModelPrice tier fields and selection path, not routing or
usage normalization. Add six optional Ultrafast rates: short/long input, cached
input and output. The existing catalog validation and snapshot field discovery
own their lifecycle.

Actual response tier is already propagated to request logs and settlement.
Regression tests exercise that wiring through both Responses streaming modes,
including downgraded default responses, component costs and idempotent limits.

Keep metadata source adapters and compatible field retention as the existing
mechanism. Ultrafast mode context tiers must share the standard threshold.

No database changes are needed. Cache-write support is deliberately excluded
until its independent accounting contract lands.
