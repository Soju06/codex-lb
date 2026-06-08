# Proposal: add-request-log-cost-breakdown

## Why
The request-log detail dialog currently shows only the aggregated request cost. Operators cannot tell how much of the bill came from non-cached input, cached input, or output tokens, even though the backend already stores the token counts needed to explain that total.

## What Changes
- Expose `inputTokens`, `outputTokens`, and `costBreakdown` on `GET /api/request-logs`.
- Fall back to `reasoning_tokens` for `outputTokens` when `output_tokens` is unavailable.
- Show a `Cost` section under the archive panel in the dashboard request-log `View Details` dialog.
- Render the section only for successful (`ok`) requests.
- Reuse existing token and currency formatting and hide unavailable segments instead of failing legacy rows.

## Capabilities
### Modified Capabilities
- `frontend-architecture`
