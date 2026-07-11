# Change: Show request-log speed metrics

## Why

Operators can see total tokens and total elapsed time in request logs, but not how long the model took to start streaming or how quickly it produced output once streaming began.

## What Changes

- Show time to first token (TTFT) beside request-log token counts.
- Show output tokens per second (TPS), calculated from output tokens over elapsed time after TTFT.
- Add daily median TTFT and median TPS charts to Reports below the existing cost/tokens daily charts.
- Keep unavailable or impossible calculations as placeholders or zeroes rather than misleading derived values.

## Impact

- Dashboard/API display change using existing request-log latency and output-token fields.
- No database migration or proxy behavior change.
