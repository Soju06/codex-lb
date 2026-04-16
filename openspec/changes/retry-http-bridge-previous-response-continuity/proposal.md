# retry-http-bridge-previous-response-continuity

## Why
HTTP bridge requests that carry `previous_response_id` currently fail closed whenever the active upstream websocket drops before `response.created` arrives. In production this leaves clients hanging until the 300-second stream idle timeout fires, even though the bridge still has enough continuity metadata (`x-codex-turn-state`) to attempt one fast reconnect on the same bridged session.

## What Changes
- Allow one fresh-upstream reconnect for active HTTP bridge requests that include `previous_response_id`.
- Prefer local durable continuation recovery over owner-forward handoff when the request already targets a matching durable `previous_response_id` anchor.
- Keep fail-closed `previous_response_not_found` behavior only when there is no matching live bridged session before request submission.
- Surface upstream-unavailable style failures quickly when an active bridged continuation still cannot be replayed after the reconnect attempt.
- Add regression coverage for send-failure and precreated-timeout continuation requests.

## Impact
- Multi-turn HTTP `/v1/responses` and `/backend-api/codex/responses` continuations stop hanging for 300 seconds after an upstream bridge drop or owner-forward stall.
- Existing fail-closed behavior for genuinely missing bridged continuity remains unchanged.
- Operators get faster, more accurate failures instead of delayed `stream_idle_timeout` hangs.
