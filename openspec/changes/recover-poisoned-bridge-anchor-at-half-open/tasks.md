# Tasks: recover-poisoned-bridge-anchor-at-half-open

## 1. Implementation

- [x] 1.1 Quarantine the bridge key (reason `retry_circuit_poisoned_anchor`)
      when the retry circuit opens on an eventless poison-class failure, so
      the existing quarantine path plans the next full-resend request
      unanchored; leave `clean_close` alone
- [x] 1.2 Record an attempt-scoped circuit strike when an upstream terminal
      error frame fails a pending request before any response event
- [x] 1.3 Report the timer actually refusing a suppressed submission
      (`hard_key_half_open` vs `hard_key_cooldown`) in both the 503
      `retry_after_seconds` and the circuit event detail, and stop claiming
      "cooling down" when the cooldown has expired
- [x] 1.4 Keep a native terminal failure envelope (`response.failed` /
      `response.incomplete`) circuit-eligible: it marks the `response.create`
      attempt answered without counting a response event, so without an
      explicit pre-response assertion the recorder rejected it and only the
      top-level `error` shape ever consumed a strike
- [x] 1.5 Record the terminal strike before the terminal frame and its
      end-of-stream sentinel are published downstream, so a client resending
      on observed completion cannot be planned ahead of the cooldown and
      quarantine; the grouped multi-request continuity settlement returns
      before that path and MUST record its own strikes the same way
- [x] 1.6 Re-evaluate the poison quarantine after the durable conflict merge
      opens the circuit, for the multi-replica case where no worker sees the
      threshold under its own lock
- [x] 1.7 Hold a `retry_circuit_poisoned_anchor` quarantine for at least the
      remaining cooldown plus the half-open lease — the whole window in which
      the probe can be admitted. The default TTL alone cannot cover that
      window: it equals the maximum cooldown, so at that cooldown the
      quarantine lapsed in the same instant the cooldown did. The floor is
      applied per call (`minimum_seconds`); the shared default TTL is
      unchanged, so quarantines armed for other reasons keep their window

- [x] 1.8 Clear the durable continuity anchor when a terminal-frame strike opens
      the circuit on a poison-class detail. The strike alone only opened the
      circuit; the stored anchor survived every cooldown because neither poison
      clear sits on the terminal path, so quarantine masked the wedge until its
      entry expired and the same dead anchor came back
- [x] 1.9 Cap the configured anchor-poison threshold at the circuit threshold on
      every clear path, clear the anchor after grouped poison strikes too, keep
      finalization reachable when the clear is cancelled, and re-arm the
      quarantine floor against a merged cooldown

- [x] 1.10 Settle the retry circuit when a durable anchor abandonment succeeds.
      The cooldown was opened by failures against the anchor just removed, so it
      kept refusing unanchored requests for the rest of its backoff; a fenced or
      failed clear still leaves it running


- [x] 1.11 Stop the local previous-response rebind re-attaching to an anchor the
      circuit has already proven dead. An explicit rejection alone can mean the
      session was not the owner, which #1830's recovery handles by retrying the
      same anchor, so this is gated on the key being quarantined

- [x] 1.12 Record the terminal strike only when the request has no safe replay.
      A stale-anchor rejection that still holds a verified full resend is
      recovered in band, and counting it charged the key for a failure it
      recovered from and disturbed the circuit generation the replay claims

- [x] 1.13 Settle the circuit on abandonment only when every request the
      abandonment covers is stranded. Settling for every caller broke the
      stale-owner replay suite; settling for none left the production wedge
      cooling after its anchor was already gone

## 2. Regression coverage

- [x] 2.1 Two `stream_incomplete` failures quarantine the key with the
      poisoned-anchor reason; two `clean_close` failures do not
- [x] 2.2 Product path: after the circuit opens on eventless failures, the
      next full-resend request through `_stream_via_http_bridge` is prepared
      with no `previous_response_id`
- [x] 2.3 Product path: an eventless `previous_response_not_found` terminal
      frame through `_process_http_bridge_upstream_text` records one
      attempt-scoped strike; a midstream one records none; two of them open
      the circuit and quarantine the key through the real recorder
- [x] 2.4 Block reason reports the half-open lease once the cooldown has
      expired (where the legacy cooldown view reports ~0) and the cooldown
      while cooling
- [x] 2.5 Product path: a native `response.failed` envelope consumes a strike,
      and two of them open the circuit and quarantine the key
- [x] 2.6 Product path: the terminal strike is recorded while the downstream
      queue is still empty, and the terminal frame is published afterwards;
      a grouped multi-request continuity failure records one strike per
      eventless grouped request
- [x] 2.10 A confirmed abandonment clears the circuit; a fenced one does not; the
      durable row is deleted even with no version fence, while an ordinary clear
      still respects it

- [x] 2.9 Grouped poison strikes clear the anchor; a cancelled clear still
      finalizes; a merged cooldown extends an armed quarantine; the effective
      poison threshold is capped at the circuit threshold and honours lower
      configured values

- [x] 2.8 A terminal poison strike clears the durable anchor with
      `clear_continuity=True`, and does so after the terminal frame is published
      rather than in front of it

- [x] 2.7 A durable merge that raises this worker to the threshold quarantines
      the key, including when the merged cooldown has already elapsed (that key
      is at its threshold with no cooldown left, so the next request is the
      probe); a quarantine armed at the maximum cooldown stays active through
      that cooldown and the half-open lease that follows

## 3. Verification

- [x] 3.1 Run the HTTP bridge unit suite, ruff, ty, the proxy architecture
      check, and strict OpenSpec validation
- [x] 3.2 Reproduce the observed wedge state (two eventless
      `stream_incomplete` failures) against the installed build and confirm
      the key is quarantined, the next full-resend request is planned
      unanchored, and the suppressed follow-up reports a truthful
      retry-after
