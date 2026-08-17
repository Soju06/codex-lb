# Usage Reporting

codex-lb records the token counts reported in the terminal Responses API event. It does not retokenize prompts or responses, and it does not estimate hidden reasoning usage.

For direct Codex traffic over HTTP or WebSocket, the reported buckets have these relationships:

- `input_tokens` includes the full input count; cached input is reported as a subset.
- `output_tokens` includes all generated output, including reasoning tokens.
- `reasoning_tokens` is the reported reasoning subset of `output_tokens`.
- Total tokens are `input_tokens + output_tokens`. Do not add cached input or reasoning tokens again.

## Dashboard

The **Request Logs** token cell shows total tokens, with reported cached-input and reasoning counts underneath. Open **Details** to see the exact reported reasoning count and its relationship to output tokens.

The **Reports** page shows the reported reasoning total for the selected date range and filters. Its coverage count states how many requests supplied a reasoning value. The daily breakdown and CSV export include the same reasoning field.

## Missing Usage

A reported zero remains `0`. A missing value remains unknown and appears as `—` in the daily report or is omitted from request details. codex-lb does not turn missing usage into zero.

Reasoning usage may be missing when the upstream terminal event does not include it, when a stream ends before that event arrives, or for older request-log rows. The dashboard does not backfill those rows. Custom OpenAI-compatible model sources do not currently feed reasoning details into this reporting path.

---

*Spec: [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture)*
