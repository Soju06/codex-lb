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
      same anchor, so this is gated on the key being quarantined. Superseded by
      2.20: the gate now fails fast instead of retrying unanchored

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

- [x] 2.11 The proven-dead-anchor gate tests the recorded quarantine reason, so
      an explicit rejection arriving during a wedged-reattach or
      repeated-eventless fence keeps its anchor

- [x] 2.12 The grouped-fan-out strike loop applies the same no-safe-replay
      admission test as the single-request settlement path

- [x] 2.13 The unanchored rebind kept its distinct telemetry label without
      falling out of the local previous-response recovery machinery. Obsoleted
      by 2.20: the unanchored label and its recovery-path set are removed with
      the retry they described

- [x] 2.14 An abandonment that covers no live request state settles the
      circuit, so a poison clear reached after terminal notification drained
      the pending set does not leave the cooldown running against a cause it
      just removed

- [x] 2.15 Both retirement funnels use the capped poison threshold, and one
      episode abandons its anchor once, retrying only when the clear failed

- [x] 2.16 An active poison quarantine keeps its reason when a weaker
      session-scoped fence arms the same key

- [x] 2.17 One circuit opening advances the quarantine generation once, and the
      post-persist arm is skipped when a concurrent completion already cleared
      the circuit this strike loaded

- [x] 2.18 The grouped anchor clear completes under reader cancellation and
      re-raises the original cancellation afterwards

- [x] 2.19 Every client-facing suppression 503 reports the timer actually
      refusing the request, not only the pre-created submission gate

- [x] 2.20 A proven-dead anchor fails fast instead of retrying unanchored.
      Every shape reaching the poison-quarantined rebind has already failed
      the proven full-resend checks, so stripping the anchor replayed a
      delta-only continuation context-free — the exact shape the quarantine
      requirement forbids. The gate now surfaces the explicit rejection as
      `bridge_previous_response_not_found` after exactly one upstream
      attempt, and the unanchored recovery label and its path set are removed

- [x] 2.21 A strike write that lands after its circuit was settled deletes the
      row it resurrected, fenced on that write's own update time, and leaves a
      newer failure episode's row alone. The unfenced settle escape is removed
      with the race it papered over

- [x] 2.22 A merged persisted cooldown in the future drops any leftover
      half-open lease, keeping `cooldown_until` and `half_open_until`
      mutually exclusive so a stale lease cannot suppress the next probe

- [x] 2.23 An abandonment is owed only while its failure episode is still
      registered. The clear decision reads the live episode's count and
      marker instead of the caller's captured count, so a circuit settled by
      a concurrent multiplexed success (or replaced by a fresh sub-threshold
      episode) cannot have a stale strike delete the anchor that success
      just persisted

- [x] 2.24 The waiterless retirement funnel applies the same
      no-safe-replay test as the terminal and grouped paths before recording
      its strike or reaching the poison clear; the pre-drain handoff with no
      request states keeps striking

- [x] 2.25 A configured abandonment threshold below the circuit threshold
      arms the poison quarantine on the strike that satisfies it, so the
      terminal frame is never published with the dead anchor uncovered while
      the clear is still awaiting I/O

- [x] 2.26 The same-owner stale-anchor recovery reads its suppression block
      from the source circuit key, so a generation claim refused during the
      half-open lease reports the lease's remaining time instead of a ~1s
      cooldown. The conflicting retirement-threshold sentence in the delta
      spec now states the capped rule everywhere

- [x] 2.27 The terminal and grouped clear gates consult the live registered
      episode immediately before abandoning the anchor, the same consult the
      retirement and reader funnels use, so a sibling that completes and
      settles during publication vetoes the clear instead of losing the
      fresh anchor it persisted

- [x] 2.28 The settle predicate blocks only on a request that actually holds
      a verified safe replay. An unanchored request with no replay no longer
      keeps the circuit cooling after a confirmed abandonment, matching the
      requirement that a safely replayable request is the only thing that
      may hold the circuit open

- [x] 2.29 The one-clear decision also requires the durable circuit row to
      still exist. The in-memory marker cannot cross a restart or a replica,
      but a completed response deletes the row with its reset, so a stale
      local episode that survived the reset can no longer delete the fresh
      anchor that completion persisted

- [x] 2.30 A grouped clear computes its settle decision over the grouped
      request states like the retirement funnels do, so a mixed group's safe
      member keeps the circuit generation its replay claims

- [x] 2.31 An attempt that observed a non-terminal response event (a
      deferred-reasoning prelude) is answered midstream: a terminal failure
      frame after it no longer consumes a pre-response strike, while a
      terminal frame that was itself the first observation still does

- [x] 2.32 A fenced durable delete that fails restores the popped episode
      with its version fence, so the settle is retried at the next clear
      opportunity instead of a later load resurrecting the surviving row as
      a fresh cooldown

- [x] 2.33 A completed verified stale-anchor replay deliberately keeps the
      source circuit and its durable row (four stale-owner replay variants
      pin this), so the one-clear marker stays process-local; the accepted
      residual is an abandonment advanced by one strike on a process that
      rehydrated the row, against a key already failing eventlessly

- [x] 2.34 Holding a safe replay requires the replay to still be available:
      a request whose one permitted replay already failed is stranded like
      any other and strikes and settles normally

- [x] 2.35 Strike writes and settles for one key are serialized across their
      durable awaits, and a superseded writer drops its write instead of
      merging a finished episode's strike into the replacement row; the
      compensating post-write delete this replaces is removed

- [x] 2.36 The clear decision requires the durable snapshot to still be an
      at-threshold episode: the settle resets the row to zero rather than
      deleting it, so a reset row proves the episode ended exactly as an
      absent one does

- [x] 2.37 The durable reset reports its CAS row count; a fenced reset that
      matched no row keeps the local episode instead of reporting settlement
      while the durable failures survive to be reloaded

- [x] 2.38 A successful mixed-group abandonment marks the surviving episode,
      so a later strike in the same episode does not issue a second
      continuity clear against the safe member's fresh anchor

- [x] 2.39 The admission-waiter reader-failure funnel applies the same
      no-safe-replay test as every other funnel before recording strikes or
      reaching the poison clear

- [x] 2.40 The terminal gate's episode consult runs inside the same
      cancellation-safe structure as the clear, so a cancellation escaping
      its durable lookup still finalizes the answered request

- [x] 2.41 The episode consult re-checks the live registered episode after
      its durable lookup, so a sibling settle landing mid-lookup vetoes the
      clear the stale snapshot would have authorized

- [x] 2.42 A merge that adopts a newer zero-failure durable reset also
      clears the one-clear marker: the marker belonged to the episode the
      reset ended, and the next poison episode owes its own abandonment

- [x] 2.43 The terminal and grouped strike gates exclude only a held safe
      replay, so an unanchored no-replay terminal failure consumes its
      pre-response strike like the retirement funnels already count it

- [x] 2.44 The grouped episode consult runs inside the same
      cancellation-deferred task as the grouped clear, so a reader
      cancellation landing mid-consult cannot strand the poisoned anchor
      after the grouped requests were already finalized

- [x] 2.45 The strike-attempt selection excludes requests that still hold a
      safe replay, so a mixed batch can neither charge the recoverable
      request nor make the stranded request's failure look ambiguous

- [x] 2.46 The one-clear marker is set only on the episode that performed
      the abandonment: a replacement episode installed while the rebind was
      in flight keeps its own required abandonment

- [x] 2.47 A strike serializes its registry mutation, its durable load, and
      its write with settles for the key, so a failure recorded while a
      completion was settling opens a fresh episode instead of extending the
      popped one or being dropped as superseded

- [x] 2.48 The key-lock acquire helper releases the lock when cancellation
      lands during its post-acquire validation, so a cancelled caller cannot
      wedge every later persist and settle for the key

- [x] 2.49 The reader-failure funnel's strike, episode consult, and anchor
      clear run as one cancellation-deferred task like the grouped path, so
      a reader cancellation cannot strand an at-threshold poisoned anchor
      after the failed requests were already drained and finalized

- [x] 2.50 The episode consult returns the exact episode it validated and
      the marker requires it; a separate post-consult capture reopened the
      race the consult closes and a None capture made the marker a wildcard

- [x] 2.51 The ordinary submit gate's suppression 503 uses the
      timer-specific message helper, completing the half-open/cooldown
      reporting requirement on the third and last hard-coded site

- [x] 2.52 A completion settles the circuit before it registers the fresh
      anchor, the consult captures the durable anchor with its validation
      reads, and the abandonment fences its continuity clear on that
      captured anchor, so a clear authorized against the poisoned anchor
      can never delete one a completion registered afterwards

- [x] 2.53 The strike's mutation clock is sampled after the keyed wait and
      the durable load, so a wait approaching the base backoff cannot
      persist an already-aged cooldown or make the fresh failure look older
      than the durable load for merge bookkeeping

- [x] 2.54 The quarantine clears only after the fresh anchor persisted: a
      failed alias write rewrites the completion and the clear guard skips,
      so the quarantine keeps covering the old anchor the failure left
      stored, protecting the window the settle-first ordering leaves

- [x] 2.55 An abandonment without a captured anchor fence is refused
      outright; a failed or unavailable capture returns nothing owed and a
      later strike retries the consult with a working fence

- [x] 2.56 A newer durable row with fewer failures than this worker holds
      is a restarted lineage in both merge arms: the one-clear marker
      belonged to the ended episode and resets with it, so a replacement
      episode is never denied its required abandonment

- [x] 2.57 A batch whose only attempts hold safe replays classifies as
      ineligible rather than absent, so the watchdog cannot record an
      unscoped strike against the one request that is about to recover

- [x] 2.58 A strike write whose base predates a reset row is dropped by the
      upsert instead of rebased as the first failure of the new lineage;
      fresh strikes load the reset row first and carry a matching base

- [x] 2.59 The clear consult requires the durable episode itself to be
      poison-class, so an at-threshold clean-close episode opened by another
      replica cannot authorize a stale local episode's abandonment

- [x] 2.60 The generation claim holds the per-key lock across its durable
      CAS instead of the global registry lock, so a slow claim no longer
      parks every unrelated hard key behind it

- [x] 2.61 A fenced settle that matches no row reloads the moved row and
      retries its fence once against the current version — settle-wins,
      matching the in-process rule — so a completion does not leave the
      durable row suppressing the key it just proved healthy

- [x] 2.62 The continuity clear captures and fences both continuity columns
      (response anchor and turn state) at the consult, so a turn-state alias
      written independently after the capture cannot be deleted by a clear
      that matched on the response id alone

- [x] 2.63 A strike the upsert dropped for its stale base is dropped from the
      worker's local episode too: the merge adopts the returned reset lineage
      instead of retaining the ended episode's count against the reset epoch

- [x] 2.64 A strike write lands only against the exact row version it
      loaded: every base-mismatched write drops whole, leaving the row's
      count, cooldown, detail, and version untouched, so a stale write can
      neither merge a finished episode's count into a re-struck lineage nor
      disturb the fences other writers hold on that row

- [x] 2.65 Design history, rationale, and accepted-residual dispositions
      moved from the spec deltas into the change-level design.md, leaving
      only MUST-level contract and scenarios in spec.md

- [x] 2.66 A suppressed dispatch whose claim was lost with no locally
      visible timer reports the configured half-open lease duration as its
      retry-after upper bound instead of a fabricated ~1s

- [x] 2.67 The waiterless direct retirement completes its strike, consult,
      abandonment, and detach/close work under a deferred relay cancellation
      and re-raises it afterwards, matching the reader settlement path

- [x] 2.68 A durable session whose continuity columns are all empty owes no
      abandonment: the consult refuses, so the settle-on-abandon path cannot
      reset a circuit cooling on genuinely unanchored upstream failures

- [x] 2.69 The quarantine registry's size cap evicts only expired or
      weaker-fence entries; an active poison quarantine survives overflow
      until its required cooldown-plus-lease deadline

- [x] 2.70 The exactly-one-probe requirement is scoped to the worker
      process; the multi-replica probe-admission residual is recorded in
      design.md rather than promised by the spec

- [x] 2.71 The persist merge adopts the returned row wholesale without
      comparing replica wall clocks, so a reset stamped by a lagging clock
      still replaces the local episode and re-arms a base that exists

- [x] 2.72 The partial stale-holder cleanup routes its opening strike
      through the same fenced consult and abandonment as the other funnels,
      under deferred cancellation, with a surviving safe-replay holder
      blocking the settle

- [x] 2.73 Durable loads adopt the row clock-free whenever no local strike
      is waiting on its own durable write, so a lagging-clock reset settles
      the local episode on the load path too

- [x] 2.74 The partial-cleanup settle predicate snapshots the pending owners
      under the lock when the decision is made, so a safe-replay holder that
      joined during finalization still blocks the settle

- [x] 2.75 The post-write quarantine verdict derives from the adopted row's
      detail and count; a speculative poison arm whose opening did not
      survive persistence is revoked under its own generation fence

- [x] 2.76 The terminal consult and abandonment run as a cancellation-
      deferred owned task that finalizes the request regardless and
      re-raises the cancellation afterwards

- [x] 2.77 A completion whose circuit settlement fails keeps the quarantine
      and suppresses the restored episode's owed abandonment, reporting the
      settlement result from the clear instead of swallowing it

- [x] 2.78 The streaming idle-recovery exhaustion routes its opening
      through the fenced consult and abandonment under deferred
      cancellation before the terminal event completes the stream

- [x] 2.79 A settlement that fails after a confirmed abandonment is retried
      once immediately and reported in telemetry when still owed

- [x] 2.80 A durable load whose lookup began before a same-key write
      completed is discarded instead of replacing the reconciled state

- [x] 2.81 Adopting a replacement episode invalidates the local half-open
      lease even when the adopted cooldown has already elapsed

- [x] 2.82 An at-threshold poison row adopted from a durable load arms this
      worker's process-local poison quarantine, fenced so ordinary loads do
      not churn the quarantine generation

- [x] 2.83 A durable miss from a lookup that began before a same-key write
      completed does not pop the episode that write just opened

- [x] 2.84 The partial-cleanup deferral begins before finalization, covering
      finalization and settlement as one owned task

- [x] 2.85 An unanchored delta-only payload on a key carrying poison
      evidence fails closed at planning instead of dispatching as a new
      conversation, with the durable circuit row serving as the
      replica-visible evidence

- [x] 2.86 The stale-load watermark survives the settlement popping the
      state object via a per-key record, so a racing lookup cannot
      resurrect the settled row into a fresh state

- [x] 2.87 The load-armed poison quarantine compares against the effective
      configured abandonment threshold, honoring a one-strike policy on
      rows another replica recorded

- [x] 2.88 Revoking a speculative poison arm restores a weaker quarantine
      the arm upgraded, preserving the independently justified fence

- [x] 2.89 The first anchor-planning pass loads the circuit for a hard key
      before any anchor decision, closing the first-touch window where an
      expired poison row's anchor was planned into the probe

- [x] 2.90 The load-arm verdict honors the one-clear marker, so a kept row
      from a completed verified replay does not re-fence the recovered key

- [x] 2.91 The generation claim reports confirmed losses, outages, and
      claims distinctly; only a confirmed CAS loss drives the remote-lease
      retry-after fallback

- [x] 2.92 The partial-cleanup strike is recorded before its failure frames
      are published, keeping quarantine cover ahead of an immediate resend

- [x] 2.93 The idle-exhaustion strike precedes the terminal frame and its
      consult and abandonment follow it as deferred cleanup, so a slow
      durable store cannot delay the client-visible terminal

- [x] 2.94 Poison revocation fences on the arm's own provenance and
      downgrades to a weaker fence that armed during the speculative
      window, so disproved poison evidence cannot outlive its revocation

- [x] 2.95 The idle-exhaustion consult and abandonment run as an owned
      registered task created before the terminal frame is yielded, so a
      consumer closing the generator at that yield cannot skip them

- [x] 2.96 A confirmed terminal abandonment records the episode marker
      even while its circuit settlement remains outstanding, and derives
      its settlement from the terminal request plus pending survivors

- [x] 2.97 The quarantine downgrade restores the weaker fence's own
      deadline, and the completion's generation-fenced clear applies the
      poison-provenance fence with the same downgrade

- [x] 2.98 The one-clear marker resets whenever a durable load adopts a
      foreign write, covering equal-count episode replacements

- [x] 2.99 The anchor-advance suppression fires only after the fresh
      anchor's registration succeeds

- [x] 2.100 The pre-planning circuit load repeats for the canonical key
      when continuity resolution replaces the incoming key, so aliased
      paths receive the same quarantine protection

- [x] 2.101 A lost persist whose returned row is neither the writer's own
      stamp nor its unchanged base resets the one-clear marker, covering
      equal-count replacements the load path cannot see

- [x] 2.102 The completion's clear of its own session key fences on the
      generation captured before its settlement and registration awaits,
      sparing a quarantine armed during them

- [x] 2.103 The grouped funnel writes the episode marker inside the same
      owned task as the abandonment, so a cancellation between the durable
      clear and the marker cannot skip it

- [x] 2.104 The stream finalizer awaits a still-running idle settlement
      before detaching and retiring, keeping the durable owner epoch alive
      for the abandonment's continuity fence

- [x] 2.105 Persist and settlement watermarks are stamped after their
      durable writes land, and a settlement sweeps any state a pre-delete
      load snapshot resurrected while its delete was in flight

- [x] 2.106 The settle fence carries the observed admission generation, and
      a moved row whose version held while its admission generation
      advanced leaves the settlement owed to the claimed replay

- [x] 2.107 The partial cleanup freezes its removed-holder snapshot before
      finalization empties the deque, so a removed safe-replay holder still
      blocks the settle

- [x] 2.108 The waiterless retirement receives the reader's pre-drain state
      snapshot, so a drained safe-replay holder still blocks the settle and
      an all-safe drain does not strike

- [x] 2.109 The verified replay captures its source quarantine fence with
      the clear's own provenance rule (covered at the clear seam; the
      capture call is one-line glue on the recovery path)

- [x] 2.110 The once-per-key planning load is recorded as an accepted
      residual in design.md: a remote poison opening reaches the worker at
      the next submit-time refresh, costing at most one self-healing probe

- [x] 2.111 The anchor-advance suppression persists as a fenced
      detail-only rewrite to the non-poison anchor-superseded class, never
      charging a failure or advancing the row's version, so replicas stop
      arming against the fresh anchor while concurrent strikes outrank it

- [x] 2.112 A strike whose pre-load failed re-persists once onto the
      observed row's lineage instead of losing its failure to the blind
      drop and the wholesale adoption

- [x] 2.113 A load that disproves the fenced episode — reset, supersession,
      below-threshold non-poison replacement, or missing/expired row —
      revokes the stale process-local poison quarantine under its
      provenance fence

- [x] 2.114 The planning cache honors a cached circuit view only while it
      is younger than the minimum cooldown, so a remote opening is either
      still cooling or refreshed before its expired cooldown admits a probe
      (supersedes the 2.110 residual, which is removed from design.md)

- [x] 2.115 The settle stamps a reconcile watermark only for keys that
      carried an episode or an unverified durable view, keeping healthy
      high-cardinality traffic out of the map and its prune scan

- [x] 2.116 A twice-missed settle reconciles its owed episode onto the
      surviving row — or concludes settled when the row is gone — instead
      of restoring the pre-chase snapshot with an obsolete fence

- [x] 2.117 The anchor-supersession fence carries the observed failure
      count alongside the version, so a lagging-clock strike that merged
      without moving the version outranks a supersession that follows it

- [x] 2.118 A load adopting a foreign write while the poison quarantine is
      active re-arms it against the adopted cooldown and refreshes the
      provenance; only a truly unchanged episode skips the re-arm

- [x] 2.119 Confirmed durable misses are cached for the planning window,
      sparing healthy hard keys the planning-time round trip while the
      submit-time load keeps enforcing any newly created cooldown

- [x] 2.120 Foreign writes are identified by any observed column moving —
      version, count, or detail — never the timestamp alone, so
      lagging-clock strikes reset markers and re-arm quarantines

- [x] 2.121 A proxy-injected anchor fails closed at submission when the
      key's poison quarantine is active by dispatch time, making the
      planning caches performance bounds rather than clock-dependent
      correctness assumptions

- [x] 2.122 The planning-miss cache is hard-capped and swept front-only in
      insertion order, bounding its cost under high-cardinality traffic

- [x] 2.123 A settle with neither a local episode nor a durable
      observation is refused as owed instead of issuing an unfenced reset

- [x] 2.124 The submit-time poison gate hands back the half-open probe it
      claimed when failing closed, so the corrected resend is not
      suppressed by a phantom lease

- [x] 2.125 The terminal settlement's deferral covers its publication
      awaits, so a cancellation between the queued frame and its sentinel
      cannot skip the consult, abandonment, and marker

- [x] 2.126 A request that observed a response event holds no safe replay
      and no longer blocks settlement, matching the retry path's own
      refusal

- [x] 2.127 Deferred-reasoning prelude evidence also marks the response
      started for the safe-replay predicate, since it deliberately leaves
      the counted events at zero

- [x] 2.128 The submit-time gate hands back only a half-open probe this
      request itself claimed, never a lease another request is flying

- [x] 2.129 The owed poison class survives a later non-poison strike
      overwriting the durable detail, and any next strike retries the owed
      clear, fenced on the exact reconciled lineage

- [x] 2.130 A claim CAS miss advertises a remote probe only when the
      admission generation advanced; sibling resets, purges, and lookup
      outages report the timer the fresh row actually carries

- [x] 2.131 The probe handback identifies this admission's claim by
      deadline value, covering a claim made after the load dropped a stale
      lease

- [x] 2.132 The anchor-advance suppression is a transitional fence applied
      before the fresh anchor is published and rolled back when the
      registration fails, closing the consult window without stranding the
      old anchor

- [x] 2.133 A claim CAS miss advertises a remote probe only for an
      at-threshold row whose admission generation advanced past the
      captured one, excluding probe-then-reset and recreated lineages

- [x] 2.134 A poison strike recorded over the supersession sentinel resets
      the marker, starting a new abandonment story for the fresh anchor

- [x] 2.135 The supersession rollback restores only the exact captured
      episode and lineage, never a replacement state

- [x] 2.136 The persist merge treats a returned count or detail the write
      did not submit as foreign evidence, resetting the marker a same-base
      lagging-clock merge would otherwise carry across

- [x] 2.137 The claim-miss probe inference is lineage-aware: an admission
      generation advanced in an earlier reset lineage is not a probe in
      the new one

- [x] 2.138 The supersession fence carries the expected prior detail both
      ways, so exactly one completion owns each transition and a loser's
      rollback cannot destroy the winner's supersession

- [x] 2.139 The probe handback clears only the exact lease token captured
      immediately after admission, never a lease another submission
      installed later

- [x] 2.140 The completion's quarantine clear is gated on the fresh
      anchor's durable registration confirming, so a swallowed alias
      failure cannot strip the last protection from the stored old anchor

- [x] 2.141 The planning-miss hard cap is enforced at insertion, holding
      through concurrent bursts that pass the pre-await sweep

- [x] 2.142 Handing a probe back restores the consumed transition marker,
      so the corrected resend re-claims the lease and follow-ups stay
      suppressed behind it

- [x] 2.143 A failed registration after a successful settle re-seeds the
      durable poison row on the zeroed lineage, keeping every replica
      armed against the still-stored old anchor

- [x] 2.144 The claimed-probe token is handed out by the admission's claim
      under its own lock, exact under any interleaving

- [x] 2.145 The submission finalizer releases the claimed probe on every
      pre-dispatch exit, not only the poisoned-anchor rejection

- [x] 2.146 The owed poison debt survives foreign same-lineage strikes
      (count growth) and dies with the reset/replacement signature

- [x] 2.147 A missed fenced TTL purge reconciles the surviving fresh row
      instead of popping the circuit and revoking the quarantine

- [x] 2.148 The scheduled circuit purge spares ever-claimed generations
      for one extra TTL so a claim near the boundary is not reaped
      mid-replay

- [x] 2.149 An at-threshold poison detail is sticky in the strike merge,
      making the durable row the cross-replica debt record; the local owed
      record dies with foreign writes and re-arms from the adopted row

- [x] 2.150 The completion adopts durable-only circuit rows before
      capturing its quarantine fence and pre-settle poison detail

- [x] 2.151 The probe handback keys on the send-attempt marker, keeping a
      possibly-live ambiguous dispatch's lease in force

- [x] 2.152 The stale-row TTL purge is fenced on the admission generation
      so an active replay claim survives a racing expiry

- [x] 2.153 The transitional marker applies only to episodes carrying
      poison evidence; clean episodes are never marked already abandoned

- [x] 2.154 The on-demand TTL purge applies the ever-claimed grace, since
      a claim observed in the row cannot be protected by any fence on
      observed values

- [x] 2.155 Abandonment-driven settles leave the anchor_abandoned durable
      tombstone and the unanchored-delta gate fails closed on it, covering
      restarts and other replicas after a settled abandonment

- [x] 2.156 The refreshed row after a purge miss honors the ever-claimed
      grace when its age is evaluated

- [x] 2.157 A completion replacing a poison episode settles onto the
      transitional anchor_abandoned tombstone and erases it only after the
      fresh anchor's registration commits, so a crash or takeover inside
      the settle-to-registration window fails deltas closed instead of
      reading as a disproved episode

- [x] 2.158 The failed-settlement suppression persists the transitional
      tombstone durably and promotes it to anchor_superseded only after
      the registration commits; the rollback fence follows the tombstone

- [x] 2.159 Poison debt arms only when the strike is at or over the
      threshold, so a clean_close opener cannot resurrect a
      below-threshold poison detail and clear a valid anchor uncovered

- [x] 2.160 A response.completed without a usable response id (or matched
      request) confirms no registration and keeps the quarantine

- [x] 2.161 The terminal-frame strike excludes internal warmup probes
      (prewarm or skip-request-log states), matching the completion
      settle's exclusions; the terminal-error fixtures model real client
      requests so the strike path stays exercised

- [x] 2.162 A completion whose pre-settle row already carries the
      tombstone settles onto the tombstone and erases it only after the
      registration commits

- [x] 2.163 The load-path stale purge spares tombstone rows and adopts
      them so the unanchored-delta gate can read them

- [x] 2.164 The scheduled purge preserves tombstone rows until the
      bridge-retention cutoff and reaps them past it

- [x] 2.165 A completion whose pre-settle durable load fails still
      settles locally, and the settle derives the fail-closed tombstone
      from the poison episode it actually adopts instead of resetting it
      plain off the blind capture

- [x] 2.166 A completion that cannot register a fresh anchor (no usable
      response id or matched request) leaves a poison episode unsettled
      instead of writing a zero-count tombstone the next load reads as a
      disproved episode

- [x] 2.167 A transitional tombstone fences the stored anchor: loads do
      not revoke a surviving quarantine off a tombstone row, and
      full-resend planning suppresses durable-anchor injection over one

- [x] 2.168 The abandonment tombstone is sticky in the strike merge
      against every failure-class overwrite, in both dialects, NULL-safe

- [x] 2.169 The owed-debt arm and the durable sticky-detail fence use the
      effective configured anchor-poison threshold, arming and preserving
      a one-failure debt at a configured threshold of one

- [x] 2.170 The submit-time gate fails a proxy-injected anchor closed
      over an adopted abandonment tombstone, matching the quarantine gate

- [x] 2.171 A restored episode that is itself the tombstone hands back a
      promotion token, so a committed registration promotes it instead of
      stranding the fresh anchor behind the lingering tombstone

- [x] 2.172 The on-demand stale purge fences on the observed count and
      detail, so a detail-only tombstone transition or lagging-clock
      merge makes it miss and reconcile instead of deleting the fence

- [x] 2.173 A purge-miss reconcile adopts a refreshed tombstone
      regardless of its preserved old epoch

- [x] 2.174 Owed poison debt alone makes the failed-settlement
      suppression transitional, even under a non-poison local detail

- [x] 2.175 The grouped settlement excludes internal warmup states from
      its strike loop, matching the single-request terminal branch

- [x] 2.176 The abandonment-driven settle yields to freshly registered
      continuity, downgrading its tombstone to a plain reset when the
      post-clear re-read shows a new anchor

- [x] 2.177 The freshness check compares both continuity columns, so a
      turn-state-only advance also downgrades the abandonment settle

- [x] 2.178 A post-settle continuity re-read erases a tombstone written
      over fresh continuity through the fenced detail-only rewrite

- [x] 2.179 The abandonment settle is fenced to its authorizing episode
      (epoch and count) and spares a replacement episode's cooldown; a
      continuity-informed plain reset suppresses the blind tombstone
      upgrade

- [x] 2.180 The authorizing episode is captured before the continuity
      clear's await, and the fence includes the admission generation so a
      claimed replay survives the settle

- [x] 2.181 The suppression keeps a local tombstone local until the
      registration commits, never flipping it early to the superseded
      sentinel

- [x] 2.182 The tombstone promotion and erase retry once on transient
      durable failures before deferring to the next completion's healing

- [x] 2.183 The internal precreated-retry path threads the claim token
      and a send-attempt baseline, handing an unused probe back on every
      dispatchless exit

- [x] 2.184 An episode-fenced settle never chases a moved row: the
      CAS-miss chase re-fences only for completion callers, and a
      replacement lineage keeps its cooldown

- [x] 2.185 The pre-response eventless timeout keeps upstream's
      distinguishable bridge_eventless_timeout detail while counting as
      anchor-poison evidence: the poison map and both dialects' sticky
      fences include it, and the eventless site routes through the fenced
      consult flow with the truthful suppression message

- [x] 2.186 The retry-transport failure funnel abandons through the same
      capped consult and continuity fence, never the raw configured
      threshold or an unfenced clear

- [x] 2.187 The failed-registration poison restore transitions its own
      tombstone through the fenced supersede before re-seeding

- [x] 2.188 The durable reset CAS carries the observed failure count, so
      a lagging-clock strike fences an episode reset the epoch cannot see

- [x] 2.189 A poison upgrade stashes an active weaker quarantine with its
      own deadline, and the poison revocation downgrades to it

- [x] 2.190 An unpersisted local strike cannot strand a confirmed
      abandonment's settle: the chase recognizes the same-lineage
      lower-count row and settles through its own fence

- [x] 2.191 The abandonment fences from the consulted episode when one is
      passed, so a sibling settle emptying the registry cannot unfence it

- [x] 2.192 The retry-transport funnel publishes its terminal before the
      settlement task runs, and the finalizer awaits that task

- [x] 2.193 The terminal and grouped funnels pass their consulted episode
      into the abandonment, verified through spies on the real funnels

- [x] 2.194 A completion whose pre-settle load failed recaptures its
      quarantine-clear fence after the settle, so the inner-load-armed
      quarantine clears on registration

- [x] 2.195 The scheduled purge reaps a tombstone only when no durable
      session (direct or alias-resolved) still stores continuity for its
      key, so a live session cannot outlive its fail-closed fence

- [x] 2.196 The consult adopts the authorizing durable row's epoch,
      admission generation, and higher count onto an unpersisted episode

- [x] 2.197 Promote and erase CAS misses reconcile on the row's own
      values through the shared tombstone reconcile

- [x] 2.198 The merged-opening arm uses the effective poison threshold,
      arming from an adopted one-failure row under a clean local strike

- [x] 2.199 The consult adopts a moved row's fence over a positive but
      stale local epoch, not only over an unpersisted one

- [x] 2.200 The poison classification expires on its own deadline: weaker
      arms extend only the shared session fence and cannot prolong the
      anchor-is-dead answer

## 3. Verification

- [x] 3.1 Run the HTTP bridge unit suite, ruff, ty, the proxy architecture
      check, and strict OpenSpec validation
- [x] 3.2 Reproduce the observed wedge state (two eventless
      `stream_incomplete` failures) against the installed build and confirm
      the key is quarantined, the next full-resend request is planned
      unanchored, and the suppressed follow-up reports a truthful
      retry-after
