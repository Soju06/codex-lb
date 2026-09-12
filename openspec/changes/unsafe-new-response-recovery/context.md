The proxy cannot manufacture a response ID. Recovery creates a fresh upstream
`response.create` without `previous_response_id`; the upstream returns the new
ID, which is then persisted as the session anchor. Eligibility requires the
existing full-history replay proof and durable one-shot recovery fence. This
avoids silently dropping context for the normal delta-only Codex request shape.
