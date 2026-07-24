# Relieve per-account stream-cap starvation under partitioned replicas

## Why

With partitioned account caps, each replica enforces a static share (`floor(cap / R)`) of the cluster-wide per-account stream limit. Hard bridge affinity deliberately concentrates one client's sessions on their owner replica, so the owner replica's share exhausts while peer replicas' shares sit idle. Requests that hit the exhausted share enter a recoverable capacity wait bounded only by the bridge request budget (default 7200 seconds): a live deployment showed requests parked in the 30-second wait loop for over eight minutes, thousands of `account_stream_cap` rejections per hour, and clients timing out with an empty error and retrying into the same starved loop. Unanchored parallel fork sessions — which have no continuity owner — were also pinned to the capped preferred account instead of spilling to an eligible account with headroom.

## What Changes

- Each replica publishes its per-account in-flight stream-lease counts in its bridge-ring heartbeat metadata, and reads peers' published counts on the same heartbeat tick that already refreshes the cap partition. No database reads are added to the request or admission path.
- When a stream lease would be denied by the local share, the replica MAY borrow up to its fair fraction of the observed cluster headroom (`floor((configured cap − observed cluster in-flight) / R)`), but only while every other active ring member has fresh published counts. Missing or stale peer data disables borrowing and preserves today's static-share behavior.
- The recoverable account-capacity wait for HTTP bridge session creation and recovery is bounded by a fixed 120-second ceiling in addition to the bridge request budget, so a request that cannot obtain account capacity fails with the existing HTTP 429 `account_stream_cap` / `account_response_create_cap` envelope while the client is still connected, instead of holding the request until the client abandons it with no error envelope. Per-session response-create gate waits keep the existing budget-bounded semantics. No new setting is added (simplicity gates); clients already retry 429 responses with backoff.
- An unanchored parallel fork bridge session whose payload is self-contained (no previous response, conversation, or file continuity) MAY drop its capped preferred-account hint once and re-select among eligible accounts with capacity, instead of waiting on the capped account.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Define bounded stream-share borrowing from observed idle peer capacity, a bounded account-capacity wait for bridged Responses work, and cap spillover for self-contained unanchored parallel forks.
- `bridge-ring-membership`: Ring heartbeat metadata additionally carries the replica's per-account in-flight stream-lease counts for cap-borrowing consumers.

## Impact

- `app/modules/proxy/cap_partitioning.py`, `app/modules/proxy/load_balancer.py`, `app/modules/proxy/_load_balancer/sticky_selection.py`: borrow-allowance computation and admission checks.
- `app/modules/proxy/ring_membership.py`, `app/main.py`: heartbeat metadata publication and peer-count refresh.
- `app/modules/proxy/_service/http_bridge/streaming.py`: bounded capacity wait and unanchored-fork cap spillover.
- No new setting, database migration, dashboard, or API schema change. Existing error envelopes and reason codes are reused.
