## Context

Direct Responses websocket traffic can replay a pre-created request when the upstream socket closes before a terminal response event. The reconnect path currently has enough request-local state to route the replay, but it needs to remember which account just failed so fresh replay attempts avoid repeating the same account-local drop. Requests that still depend on previous_response_id are constrained by owner affinity and must not be routed away from the owner account merely because the reconnect is a replay.

## Goals / Non-Goals

**Goals:**

- Penalize the account that drops a replayable websocket request.
- Carry failed-account exclusions on the replay request state.
- Apply exclusions only to fresh replay attempts that are free to choose another account.
- Preserve previous-response owner affinity for anchored follow-up requests.

**Non-Goals:**

- Changing upstream websocket connect retry limits or timeout budgets.
- Downgrading websocket replay traffic to HTTP/SSE.
- Changing public error shapes or introducing new configuration.

## Decisions

- Store replay exclusions on _WebSocketRequestState so the state travels with the replay candidate through the existing reconnect loop.
- Mark the failed account through the existing stream-error path, then add its id to replay exclusions only when the replay state does not have previous_response_id.
- Seed _connect_proxy_websocket account exclusions from the replay state before account selection, preserving all existing failover and preferred-account behavior.

## Risks / Trade-offs

- Excluding an account can reduce the candidate pool for a fresh replay. The existing account-attempt limit and final error handling remain in place.
- Not excluding previous-response owner accounts can retry a recently failed owner, but avoids breaking continuity where no other account can legally serve the anchor.
