# Change: Show request-log speed metrics

## Why

Operators can see total tokens and total elapsed time in request logs, but not how long the model took to start streaming or how quickly it produced output once streaming began.

## What Changes

- Show time to first token (TTFT) beside request-log token counts.
- Show output tokens per second (TPS), calculated from output tokens over elapsed time after TTFT.
- Add daily median TTFT and median TPS charts to Reports below the existing cost/tokens daily charts.
- Keep unavailable or impossible calculations as placeholders or zeroes rather than misleading derived values.
- Capture first-upstream-event, response-created, and first-token latency on the websocket responses path so websocket request logs populate the same latency fields the HTTP bridge already records.
- Surface the raw persisted output-token count (`output_tokens_raw`) in the request-log API so TPS is computed from the un-derived value.

## Impact

- Dashboard/API display change built on existing request-log latency fields (`latency_first_token_ms` from TTFT observability) and the existing `output_tokens` column (from elapsed-time work); no new columns and no database migration.
- Proxy behavior change limited to observability: the websocket responses path now records latency timings into existing nullable columns, reaching parity with the HTTP bridge. No routing, failover, or client-visible response change.
