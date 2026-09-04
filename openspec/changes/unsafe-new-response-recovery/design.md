The upstream event classifier treats the exact terse invalid-anchor message as
`previous_response_not_found` only when the unsafe flag and
`server_indefinite_recovery` are both enabled. The streaming recovery handler
then consumes the existing durable recovery-attempt fence, retires the stale
upstream bridge, submits the verified anchor-free body on a fresh upstream
session, and records the newly returned response ID through normal operation
settlement. No second replay is admitted for the same fenced operation.
