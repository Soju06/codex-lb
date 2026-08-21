# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Silent HTTP bridge sessions are quarantined from re-attach and reuse

When an HTTP bridge session proves silent/wedged, the proxy MUST quarantine its session key for a bounded window so later requests stop attaching to it. A session proves silent/wedged when either (a) a pending request being failed or retired carried a proxy-injected `previous_response_id`, had sent `response.create`, observed upstream response events, and never had `response.created` assigned, or (b) the session key hits two consecutive eventless `missing_response_created_timeout` retires. This holds for every path that fails or retires the request — partial stale-holder cleanup, the reader-failure funnel, and direct all-stale session retirement alike. The quarantine MUST be evaluated only when a request is already being failed or its session retired — never against a live owned turn — so a stream whose `response.created` was observed (including deferred-reasoning streams with long event gaps) MUST NOT be quarantined, and mere event silence during an owned live turn MUST NOT trigger quarantine by itself.

While a session key is quarantined: an existing session under that key MUST NOT be selected for reuse (a new request detaches it and proceeds on a fresh session), and for durable-anchor selection a quarantined session that is still open MUST count as absent, exactly as if it were already gone. The quarantine registry verdict is authoritative for the key: any session under the key while the quarantine window is active — including a freshly created replacement whose own completion has not yet cleared the quarantine — is equally excluded from reuse and equally absent for anchor selection. A fresh reattach whose incoming payload already looks like a full conversation resend MUST NOT receive a proxy-injected durable anchor through any injection point — the fresh-reattach injection, session-state hydration of the durable anchor, or the session-level injection — so the dispatch goes upstream genuinely unanchored with the client's own untrimmed payload. A payload that does not look like a full resend (a genuine delta-only continuation) MUST still receive the durable anchor, because it has no other way to convey prior conversation state.

Quarantine state MUST be bounded and self-recovering: it is in-memory and session-scoped, expires by TTL (a live session that outlives its quarantine window MUST become reusable again), is cleared when a response completes on the same session key, and MUST NOT write account health or alter account selection. When a replacement request completes on a quarantined key and produces a usable response id, recovery MAY rebind and renew the original durable session row so the recovered response id becomes the durable continuity anchor. That durable recovery MUST be fenced by the quarantine generation captured when the replacement request was prepared, and MUST NOT perform durable mutations if the current quarantine generation differs before the mutation. The proxy MUST treat `renew_live_session` as successful only when its returned snapshot matches the expected durable session id, owner instance, owner epoch, account id, and recovered response id.

#### Scenario: Repeated eventless timeouts quarantine the key

- **GIVEN** a session key whose pending request already retired once with the eventless `missing_response_created_timeout`
- **WHEN** a subsequent attach on the same key retires with the same eventless timeout before any response completes on the key
- **THEN** the session key is quarantined with reason `repeated_eventless_timeout`
- **AND** the first timeout alone does not quarantine the key

#### Scenario: Deferred-reasoning live turn is never quarantined

- **GIVEN** an owned live turn whose `response.created` was observed and whose events flow with long gaps (deferred reasoning)
- **WHEN** its stream later fails or its session is retired
- **THEN** the session key is not quarantined
- **AND** later requests keep the existing reuse and anchor-injection behavior

#### Scenario: Delta-only payloads keep their anchor while quarantined

- **GIVEN** a quarantined session key — including one whose quarantined session is still open with other active requests
- **WHEN** a later request arrives whose payload does not look like a full conversation resend
- **THEN** the still-open quarantined session counts as absent for durable-anchor selection
- **AND** the durable anchor is still injected for that request, preserving the client's only way to convey prior context

#### Scenario: Quarantine is bounded and self-clearing

- **GIVEN** a quarantined session key
- **WHEN** a response completes on that session key, or the quarantine TTL elapses
- **THEN** the quarantine (and its eventless strike counter) is cleared
- **AND** a session that survived the quarantine window is reusable again instead of staying rejected forever
- **AND** account health and account selection are not written by this path

#### Scenario: Completed replacement response advances durable recovery

- **GIVEN** generation N of a quarantined key dispatched a replacement request
- **AND** that replacement response completes with response id `resp_recovered`
- **WHEN** the original durable session row is still owned by the expected bridge instance and owner epoch
- **THEN** the proxy may rebind the original durable row to the replacement account and renew it with `resp_recovered`
- **AND** quarantine is cleared only after the renewal snapshot confirms the expected session id, owner instance, owner epoch, account id, and response id

#### Scenario: Foreign renewal snapshot does not clear quarantine

- **GIVEN** a completed replacement response attempts durable quarantine recovery
- **WHEN** `renew_live_session` returns a snapshot for a foreign owner, epoch, account, or response id
- **THEN** the recovery is not reported as successful
- **AND** quarantine remains uncleared

#### Scenario: Newer quarantine generation blocks stale durable recovery

- **GIVEN** a generation N replacement response is still recovering
- **AND** the same key is quarantined again at generation N+1 before durable mutation
- **WHEN** generation N recovery resumes
- **THEN** it does not rebind or renew the durable row
- **AND** the generation N+1 quarantine and durable owner/anchor survive
