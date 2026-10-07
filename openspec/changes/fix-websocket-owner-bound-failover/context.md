# Required owner and excluded owner

The connect loop already computes `require_preferred_account` from replay,
previous-response, file and turn-state ownership. The generic failover decision
omits that constraint, so a retryable failure excludes the only legal owner.
The selector correctly refuses the next selection, but the resulting owner
error hides the upstream failure that caused it.

Selection can also establish a replay owner from nonportable request text
after the loop computes that constraint. Read the current replay owner when
passing the constraint to `failover_decision`, which already surfaces
an immovable request when no same-account retry is offered. This matches the
HTTP decision and adds no new retry mechanism. For example, replay owner A
returning a retryable 403 now yields that 403 once; a movable request can still
select B. Existing quota-owner assertions must likewise expect the original
429 rather than a manufactured owner-unavailable 502.

A bodyless installed 1.24 handshake 403 maps to `forbidden`; a valid JSON
envelope may supply a retryable code. The historical production error body is
unknown, so this source regression does not establish that incident's cause.
