# Keepalive-window incident evidence (sanitized)

This tracked summary preserves the measurements used by the formal model
without copying private session payloads or credentials.

- The saved corpus contained 788 lines labelled `HTTP bridge stream idle timeout`.
- 57 of 64 reader failures had `response_events_seen=0`.
- Healthy `gpt-5.6-luna` turns had first-upstream-event p95 of 930 ms.
- The pre-response keepalive window was 6 × 10 seconds = 60 seconds; it was
  distinct from the configured 7200-second post-start stream-idle budget.
- The affected dead-anchor examples were sessions `a0eb3b03df19` and
  `33755fa72727`; only redacted identifiers are retained here.

The full private incident corpus remains outside this repository; this file is
the reviewable, non-sensitive evidence boundary for the model controls.
