# responses-api-compat Specification

## Purpose

Define Responses API compatibility contracts so Codex, OpenCode, and OpenAI-style clients preserve expected behavior.
## Requirements
### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

### Requirement: Responses requests reject uploaded input_image references

The system SHALL accept `{"type":"input_file","file_id":"file_*"}` attached-file items in `/v1/responses`, `/backend-api/codex/responses`, and `/responses/compact` request payloads and forward them verbatim.

When an `input_image` part contains a `file_id` field or an `image_url` starting with `sediment://`, the proxy MUST return HTTP 400 with `error.code = "unsupported_input_image_format"` and an explanation that the upstream Responses API only accepts inline `data:` URLs for `input_image`. The proxy MUST NOT fetch the upload, MUST NOT inline-convert the image, and MUST NOT trim, slim, or rewrite any conversation content.

`app/core/openai/requests.py::extract_input_image_file_references` MAY be used to detect the unsupported shape. This request path MUST NOT fetch uploads, inline-convert images, or otherwise reshape inbound conversation payloads.

#### Scenario: input_image file_id is rejected before forwarding

- **WHEN** a `/v1/responses` request contains `{"type":"input_image","file_id":"file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** the response explains that inline `data:` URLs are the supported `input_image` contract

#### Scenario: sediment upload URL is rejected before forwarding

- **WHEN** a `/responses/compact` request contains `{"type":"input_image","image_url":"sediment://file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** does not fetch or inline-convert the upload

#### Scenario: large request payload routes via HTTP transport on auto

- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge

- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Oversized responses request payloads fall back to HTTP
When `upstream_stream_transport` is `"auto"` and the serialized request payload size exceeds the WebSocket frame budget, the proxy MUST use upstream HTTP `POST` instead of WebSocket. If the HTTP responses bridge is enabled and the same oversized request would otherwise route through the bridge, the proxy MUST bypass the bridge for that request only and send it over raw HTTP. Explicit `upstream_stream_transport` overrides MUST still take precedence.

#### Scenario: large request payload routes via HTTP transport on auto
- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge
- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Clean upstream close before any response event fails fast

When the HTTP Responses bridge observes an upstream WebSocket close with
`close_code = 1000` before any `response.*` event has been surfaced for the
pending request, the proxy MUST preserve its existing pre-visible replay
guards. If the request has already used exactly one eligible pre-visible
replay and the replacement upstream WebSocket also closes cleanly before any
response event, the proxy MAY perform exactly one additional replay. The
additional replay MUST be hard-capped at one per request, and the configured
maximum MUST NOT raise that cap.

The proxy MUST NOT replay after downstream-visible output, after a terminal
response event, or when continuity-sensitive request state makes replay unsafe.
Before the additional replay, the proxy MAY sleep for bounded configured
jitter. The proxy MUST emit a dedicated low-cardinality diagnostic event for
the additional replay.

When a downstream HTTP stream task initiates pre-response recovery while the
upstream reader is blocked on the superseded socket, the proxy MUST cancel and
await that reader before locally closing the socket. It MUST then start exactly
one reader for the replacement socket. A close caused by replacing the socket
MUST NOT be recorded as an upstream clean-close failure, MUST NOT increment the
retry circuit, and MUST NOT retire pending work moved to the replacement. The
cancelled reader's socket-generation finalizer MUST NOT leave the shared session
marked closed while the replacement socket is being selected or opened, so idle
pruning MUST NOT evict the handoff in progress.

The default pre-response idle-recovery window MUST leave bounded headroom
before the downstream client's request timeout. With the default ten-second
keepalive interval, the proxy MUST initiate eligible recovery after no more
than six silent intervals so replacement connection and first output can occur
before a 120-second client deadline.

The stuck pre-response watchdog MUST judge staleness using elapsed time since
the last upstream activity and the absence of a response identifier or
`response.created` latency, not admission flags alone. A request with a prior
continuity anchor MUST receive at most two retire-thresholds of grace before
being considered stale. When the watchdog skips a candidate, it MUST emit a
low-cardinality diagnostic containing the session-closed state, candidate
count, and pending-state verdicts.

#### Scenario: clean close before response.created is not retried

- **WHEN** the initial upstream HTTP responses bridge closes with `close_code = 1000` before any `response.*` event for the pending request
- **THEN** the proxy returns HTTP 502 with `error.code = "upstream_rejected_input"`
- **AND** does not transparently replay the pre-created request

#### Scenario: clean close before response output receives one bounded additional replay

- **GIVEN** an HTTP bridge request has no surfaced `response.*` events
- **AND** its first pre-visible replay has already been used
- **WHEN** the replacement upstream WebSocket closes with code `1000`
- **THEN** the proxy performs one additional pre-visible replay
- **AND** the request replay count increases by one
- **AND** the proxy emits a `retry_precreated_clean_close` diagnostic event

#### Scenario: repeated clean closes do not create an unbounded replay loop

- **GIVEN** the additional clean-close replay has already been used
- **WHEN** another upstream WebSocket closes cleanly before response output
- **THEN** the proxy does not replay the request again
- **AND** the existing terminal or circuit handling is used

#### Scenario: visible output still prevents clean-close replay

- **GIVEN** the pending request has surfaced any response event downstream
- **WHEN** the upstream WebSocket closes with code `1000`
- **THEN** the proxy does not replay the request

#### Scenario: clean-close retry jitter is bounded

- **GIVEN** clean-close retry jitter is configured
- **WHEN** the additional clean-close replay is scheduled
- **THEN** the delay is no greater than the configured jitter maximum
- **AND** the hard replay cap remains one regardless of the configured value

#### Scenario: downstream idle recovery transfers reader ownership

- **GIVEN** the upstream reader is blocked on the current bridge socket
- **AND** the downstream HTTP stream task initiates eligible pre-response recovery
- **WHEN** the bridge replaces the upstream socket
- **THEN** the old reader is cancelled and awaited before its socket is closed
- **AND** the shared session remains live while the replacement socket opens
- **AND** idle pruning retains the registered session while the handoff is in progress
- **AND** exactly one reader owns the replacement socket
- **AND** the local close does not open or increment the retry circuit
- **AND** pending work remains attached to the replacement session

#### Scenario: silent pre-response recovery precedes the client timeout

- **GIVEN** the upstream has produced no response event
- **AND** the default ten-second keepalive interval is active
- **WHEN** six silent intervals elapse
- **THEN** the proxy initiates eligible pre-response recovery
- **AND** at least sixty seconds remain before a 120-second client request timeout

#### Scenario: anchored stuck-gate grace is bounded

- **GIVEN** a pending HTTP bridge request has a prior continuity anchor
- **AND** no response identifier or `response.created` latency has been recorded
- **WHEN** less than two retire thresholds have elapsed since the gate began waiting
- **THEN** the watchdog does not classify the request as stale
- **WHEN** two retire thresholds elapse without upstream activity
- **THEN** the watchdog may classify the request as stale

#### Scenario: upstream activity resolves admission-flag ambiguity

- **GIVEN** a pending request has not acquired the response-created gate
- **AND** upstream activity has not produced a response identifier or `response.created`
- **WHEN** the staleness threshold elapses
- **THEN** the watchdog classifies the request as stale
- **AND** emits pending-state verdict inputs when it skips a watchdog pass

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`, plus the distinguishable pre-response
`bridge_eventless_timeout`, which counts as anchor-poison evidence like
the stream classes while keeping its own durable detail). Every funnel
that abandons on repeated eventless failures — the idle-recovery
exhaustion and the retry-transport failure path alike — MUST route
through the shared poison consult and the captured continuity fence;
none may apply a threshold of its own or clear continuity unfenced. The failed-registration poison restore MUST
transition its own settle's tombstone through the fenced detail-only
supersede before re-seeding — the strike merge's sticky tombstone would
otherwise silently refuse the poison class and leave a threshold
tombstone no replica arms a quarantine from. The durable reset CAS MUST
carry the observed failure count alongside the epoch and admission
generation, because a lagging-clock strike merges a higher count without
moving the epoch; a completion settle defeated by that fence still wins
through its chase. The episode fence's count comparison is strictly
greater-than in the chase: merges only increment, so a LOWER durable
count at the same epoch and admission generation is this worker's own
lineage observed before local strikes whose durable writes failed, and a
confirmed abandonment MUST still settle it rather than leave the removed
anchor's cooldown standing. A poison arm upgrading over an active weaker
quarantine MUST stash the weaker fence's reason and its OWN deadline —
captured before the arm extends the entry — so a later load disproving
the poison episode downgrades to the weaker fence instead of evicting
the entry and freeing a still-wedged session before its original TTL,
mirroring the existing weaker-over-poison stash. The abandonment's
episode fence MUST derive from the consulted episode itself when the
caller holds one — a sibling settle can remove the registry entry
between the consult and the capture, and a None capture would run the
settle unfenced against a replacement episode. The retry-transport
funnel's consult and abandonment MUST run as an owned settlement task
after its terminal frame is published, under the same finalizer await as
the idle-recovery exhaustion, so a slow durable store never delays the
client-visible failure and a cancellation cannot skip the cleanup.
Every consult-backed funnel — terminal, grouped, idle, and transport
alike — MUST pass its consulted episode into the abandonment. A
completion whose pre-settle load failed MUST recapture its
quarantine-clear fence after the settle: the settle's own successful
inner load can arm the quarantine after the blind capture, and the
unrecaptured fence would strand a healthy key for the poison window;
the recapture still precedes the registration awaits, so concurrent
strikes during those stay outside the fence.

A bridge retirement MUST record one of those failures only when the retiring
session still owns at least one pending request and no response event has been
observed for that request lifecycle. Retiring an idle upstream bridge with no
pending request MUST NOT advance the circuit or cause a later request to be
treated as a repeated failure. A pending request that has already emitted a
response event MUST remain excluded from this pre-response circuit. An
upstream terminal error frame that fails a pending request before any
response event was observed, and that leaves the request with no safe replay,
MUST record one failure for that request
lifecycle through the same attempt-scoped recorder, because that failure
settles through the terminal path rather than a retirement and would
otherwise never advance the circuit; a later retirement of the same lifecycle
MUST NOT count it again. An internal warmup probe — a prewarm request state,
or one marked to skip request logging — MUST be excluded from that terminal
recording: it carries no anchor and proves nothing about the key's
continuity, and charging it would open and quarantine the hard key before
any real turn. An attempt that observed any non-terminal response
event — a deferred-reasoning prelude whose ordinary event accounting was
deliberately skipped included — was answered midstream, and a terminal frame
that follows it MUST NOT be charged as a pre-response strike. A failure the proxy can still replay safely MUST NOT
advance the circuit: the request is not stranded, the verified stale-anchor
replay that follows depends on the circuit generation it captured, and counting
there both disturbs that fence and charges the key for a failure it recovered
from in band. This exclusion MUST apply identically when one terminal frame
settles a grouped fan-out of requests, so a group whose members can each still
replay safely cannot advance the circuit between them. A native terminal failure envelope
(`response.failed` or `response.incomplete`) MUST remain eligible for that
recording even though it marks the `response.create` attempt as answered
without counting a response event. The recording MUST complete before the
terminal frame and its end-of-stream sentinel are published downstream, so a
client that resends the moment it observes completion cannot have that resend
planned while the resulting cooldown and quarantine are still being written.
The grouped multi-request continuity settlement, which fails several pending
requests with synthetic terminal events and returns before that path, MUST
record one failure for each grouped request that observed no response event,
under the same ordering rule.

When hard-key retry-circuit cooldown suppresses a request after its bridge
session has been created but before `response.create` is dispatched, and no
other turn owns that session (no visible pending or queued request, no
registered admission waiter, no unanchored handoff held by another request),
the proxy MUST mark the session `reconnect_requested` and `retire_after_drain`
and MUST invoke the bounded drain-retirement path before returning the
suppression error, so the never-dispatched socket is closed and detached rather
than left reusable. If another turn owns the session — in particular the
half-open probe the cooldown admitted, which may still sit between its
admission decision and its dispatch registration — the suppressed request MUST
leave the session unmarked and MUST NOT close it; that owner's own lifecycle
governs retirement, and the admitted turn MUST proceed. If the suppressed
request itself holds the session's unanchored handoff, the session counts as
unowned and its submit finalizer completes the retirement after releasing the
handoff. This applies to both late submit suppression and startup pre-submit
cooldown terminal handling.

So that a concurrent suppression can see it, a submit MUST count as a
registered admission waiter on its session from submit entry, before the
retry-circuit admission decision, until the dispatch path takes that
registration over; every pre-dispatch exit MUST release it, and the release
MUST re-run any retirement the registration was deferring.

Proof-gated full-resend replay and operation-fenced continuity replay remain
eligible bypasses and MUST NOT be retired by this suppression requirement.

When such a strike opens the circuit on a poison-class detail, the proxy MUST
also clear the stored durable continuity anchor for that key. The quarantine
armed with the strike only suppresses injection in this process and expires,
so without the durable clear the same dead anchor is restored on the next
reattach and re-poisons the key after every cooldown. On every settlement path — terminal,
grouped, retirement, and close alike — the anchor-poison threshold IS the
circuit's own failure threshold, a fixed application constant rather than a
runtime setting. Above that threshold the key is refused for 60-600s per
strike, so a higher value could never be reached at any useful rate, and the
poison quarantine MUST be armed with the strike that opens the circuit, so a
clear is never published without quarantine cover.

A grouped settlement whose strikes carry the circuit through that threshold
MUST clear the anchor as well, after its grouped terminal frames are published.

Unlike the strike, the durable clear MUST NOT precede the terminal frame; a
resend arriving in that window is already covered by the quarantine. Because
the frame has already been published, a cancellation escaping the clear MUST
NOT skip finalization of the settled request. Every funnel that runs after
its failed requests are drained and finalized — the reader settlement, the
waiterless direct retirement, the partial stale-holder cleanup, the terminal
settlement, and the streaming idle-recovery exhaustion alike — MUST complete
its strike, episode consult, abandonment, episode marker, and retirement
under a deferred cancellation and re-raise the cancellation afterwards,
because no request lifecycle remains to retry the abandonment it would
otherwise skip; the marker in particular MUST be written inside the same
owned task as the abandonment it records, because a cancellation landing
between the durable clear and a post-task marker write would leave the
cleared episode unmarked and a later load would re-arm quarantine from the
unchanged surviving row. An
opening recorded by the streaming idle-recovery exhaustion MUST record its
strike before the terminal event is published — so the cooldown and
quarantine cover an immediate resend — and MUST run its consult and
abandonment as an owned cleanup task created before the terminal event is
yielded — a consumer closing the generator after receiving that frame
injects GeneratorExit at the yield, and cleanup only started afterwards
would never run — with the task registered so it survives the generator,
never delaying the terminal frame behind the durable store. The stream
finalizer MUST await a still-running idle settlement task before detaching
the request and releasing the session, because retirement releases the
durable owner epoch the abandonment's continuity clear is fenced on, and a
task that merely survives the generator loses that fence to a concurrent
retirement. The terminal settlement's
deferral MUST cover its publication awaits as well — the operation
persistence, the queued frame, and its end-of-stream sentinel — because a
cancellation landing inside any of them otherwise escapes before the owned
settlement task exists, with the request already finalized by the abort
path and nothing left to retry the abandonment. The partial
stale-holder cleanup MUST order itself the same way: strike before its
failure frames are published, abandonment after. The partial stale-holder cleanup's
deferral MUST begin before its holders are finalized, covering finalization
and settlement as one owned task, because a cancellation landing inside
finalization otherwise re-raises before the settlement exists.

A quarantine armed from a local opening MUST be re-armed against the merged
cooldown when durable persistence returns a longer deadline, so its floor
covers the cooldown actually in force rather than the local backoff it was
first computed from. The load path MUST re-arm the same way: a load that
adopts a foreign write while the poison quarantine is active extends the
deadline against the adopted cooldown and refreshes the poison provenance
to the lineage that now owns the row — otherwise a later durable strike
could extend the cooldown past the old deadline and the quarantine would
lapse mid-cooldown with the planning cache still fresh. Only a truly
unchanged episode skips re-arming, which is what keeps ordinary loads from
bumping the generation recovery fences observe.

A confirmed durable anchor abandonment MUST settle the retry circuit for that
key; a settlement that fails after the abandonment confirmed MUST be retried
once immediately, and a settlement still owed after that retry MUST be
reported in telemetry rather than silently reported as settled. The circuit was opened by failures against the anchor the abandonment
removed, so its cooldown would otherwise back off a cause that no longer
exists and refuse requests that carry no anchor at all. The abandonment is the
same proof of recovery a completed response carries. An abandonment that was
fenced or failed proves nothing and MUST leave the cooldown running, and so
does one whose requests can still be replayed safely: such a request is about
to be retried and claims the circuit's generation at dispatch, so the circuit
must survive for it. A safely replayable request is the only thing that may
hold the circuit open, and a consumed replay is no replay: the one permitted
replay's failure leaves the request stranded like any other, striking and
settling normally. An abandonment covering no live request MUST settle:
terminal notification drains the pending set before retirement, so the
funnels routinely abandon a poisoned anchor with a pre-drain count and no
request states at all, and nothing is holding the generation there.

A settle only removes rows it holds evidence for: the version fence protects a
row another writer created, and a worker that observed no durable row deletes
nothing. A worker holding neither a local episode nor a durable
observation — a completion whose lookup raised on a stateless worker —
holds no fence at all and MUST leave the settlement owed rather than issue
an unfenced reset that could clear a poison episode or claimed admission
generation another replica created during the outage; the best-effort
unfenced clear is reserved for a worker that at least carries a local
episode. The settle fence MUST also carry the admission generation observed
with its version: a replica's replay claim advances only that generation, so
a reset fenced on the version alone would clear the circuit beneath the
claimed replay. The on-demand TTL purge MUST apply the same ever-claimed grace as the
scheduled purge — a claim that landed before this worker's lookup is
carried in the observed row, and no fence on observed values can protect
it. A stale row's fenced TTL purge MUST carry the observed admission
generation alongside its version — a replay claim advances only that
generation, and purging the claimed row would let a later recovery
dispatch a second replay beside the first. A stale row's fenced TTL purge that matches no row MUST NOT be treated
as a deletion: another replica re-struck the key after this worker's
lookup, and the load MUST reconcile against the surviving row instead of
popping the local circuit and revoking its quarantine while the fresh
cooldown stands. The scheduled cleanup MUST give ever-claimed rows one
extra TTL of grace, because a replay claim advances only the admission
generation and deliberately leaves the timestamp unchanged — reaping a
claimed generation mid-replay would let a later recovery mint a fresh
fence and dispatch a second stale-anchor replay beside the first. A fenced settle that matches no row MUST reload the moved row and
retry its fence once against the current version before giving up; only a
second miss leaves the episode owed to the next opportunity. An episode
kept owed after a twice-missed settle MUST be reconciled onto the row that
actually survived — or concluded settled when that row is gone or reset —
rather than restored from the pre-chase snapshot, whose obsolete fence
would misdirect the anchor supersession and let the next load misread the
surviving row as a foreign episode, reset the one-clear marker, and
authorize abandoning the fresh anchor the completion just registered. A moved row
whose version is unchanged while its admission generation advanced is a
claim, not a concurrent strike, and the settlement MUST stay owed rather
than chase the claimed generation. A circuit opened and remediated in the same instant cannot defeat
this, because strike writes and settles for one key are serialized: the
settle waits for the in-flight write to land and then deletes the row it
produced under its version fence, and a writer that finds its episode settled
or replaced when its turn comes drops the write instead of merging a finished
episode's strike into the row's current owner.

Once the key is quarantined for a poisoned anchor, the local previous-response
rebind MUST NOT re-attach to the rejected anchor. The quarantine registry is
shared with the wedged-reattach and repeated-eventless fences, which fence the
session without evidence about its anchor, so this rebind MUST test the
recorded quarantine reason rather than the presence of an active quarantine
window; an explicit rejection arriving during either of the other two fences
MUST keep the anchor. An explicit rejection on its
own does not prove the anchor dead, since it can mean the session was not its
owner, so the rebind's existing same-anchor retry MUST be preserved until the
circuit has opened on repeated eventless poison-class failures. After that the
request MUST fail fast: every shape that reaches this rebind has already
failed the proven full-resend and operation-fence checks, so its payload does
not retain the anchor's context, and retrying it unanchored would replay a
delta-only continuation as a context-free request. The proxy MUST surface the
explicit rejection to the client as `bridge_previous_response_not_found`
after exactly one upstream attempt, leaving recovery to the client, which is
the only party holding the conversation history. An abandonment-driven
settle MUST leave a durable anchor-abandoned tombstone on the zeroed
circuit row — a completion's settle writes none, erasing it on real
recovery — and the unanchored-delta gate MUST fail closed on that
tombstone too, because after a settled abandonment a restarted worker or
another replica holds neither a quarantine nor a poison row and would
otherwise dispatch a delta as a brand-new conversation. The claimed-row
grace applies wherever a stale row's age is evaluated, including the
refreshed row after a purge miss.

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown; a concurrent replica's write MUST
NOT disturb the row's stored version. Strike
writes and settles for one key MUST be serialized across their durable
awaits. A strike write MUST land only against the exact row version it
loaded: every base-mismatched write — one whose episode was settled,
replaced, or outrun while it waited, including a write whose base predates a
reset row that another replica has already re-struck — MUST be dropped whole,
leaving the row's count, cooldown, detail, and update time unchanged, and the
writer MUST reconcile from the returned row. The drop applies to the writer's
own replica of the episode as well: the writer MUST reconcile from the
returned row by adopting it wholesale — count, cooldown, detail, and version
— without comparing replica wall clocks, so a reset stamped by a lagging
clock still replaces the local episode and the next strike carries a base
that actually exists on the row. A lost write whose returned row is neither
the writer's own stamp nor its unchanged base MUST reset the one-clear
marker, even at an equal or higher failure count, because adopting the
foreign version makes every later load see the replacement episode as
unchanged and the persist reconciliation is the only point that can observe
it. A write that carried no valid base because the pre-strike load failed
MUST NOT lose its failure to the drop: its strike was never merged
anywhere, so it re-strikes once on top of the returned row's lineage, and
only a second drop accepts the ordinary undercount. A durable load MUST adopt the row the same
clock-free way whenever no local strike is waiting on its own durable write;
only a strike between its record and its write keeps its local count
dominant, and that write's own merge then reconciles it. A foreign write
MUST be identified by any observed column moving — version, count, or
detail — never by the timestamp alone, because a lagging-clock strike
merges through the timestamp maximum without moving it while incrementing
the count, and an anchor supersession rewrites the detail in place by
design. The persist reconciliation applies the same rule to its own landed
write: a returned count or detail the write did not submit is a foreign
contribution folded into the merge, its evidence targets whatever anchor
is current, and the one-clear marker cannot survive it. A load whose lookup
began before a same-key strike or settlement completed its durable write
MUST be discarded rather than adopted, because its snapshot can predate that
write; a durable miss from such a lookup MUST NOT pop the local episode the
completed write just opened, and the watermark this guard reads MUST survive
the settlement popping the state object, so a lookup racing a settlement
cannot adopt the pre-settlement row into a fresh state and resurrect the
settled cooldown. That watermark MUST be retained only for keys that
carried an episode or an unverified durable view: a healthy key's settle —
no local state and a confirmed durable miss — has nothing a racing load
could resurrect, and fencing every conversation key ever served would grow
the map and its shared-lock prune scan without bound. The fences this guard compares against MUST be stamped
after the durable write lands, not at the writer's entry — a load can start
during the write's await and still carry a later start stamp than the entry
time while its snapshot predates the write — and a settlement MUST sweep
any state that a pre-delete snapshot re-created while its delete was in
flight. Adopting a replacement episode MUST invalidate the local half-open
lease even when the adopted cooldown has already elapsed, and a poison row at
the circuit threshold adopted from a durable load
MUST arm this worker's process-local poison quarantine, since the replica
that recorded the strikes cannot arm it here — unless the local episode's
one-clear marker records that its anchor was already abandoned, in which
case re-arming would re-fence a recovered key. The reverse holds too: a
load that disproves the fenced episode — a zero-failure reset, a
deliberate anchor supersession, a below-threshold replacement whose detail
is not poison-class, or a missing or expired row for a previously
reconciled key — MUST revoke or downgrade the process-local poison
quarantine under its provenance fence, so a remotely recovered key does
not stay excluded from reuse and anchor injection for the stale deadline;
a replaced lineage at or past the threshold keeps the fence, since its
earlier strikes may still be the poison evidence. The first anchor-planning
pass for a hard key MUST perform this load before any anchor decision, so
the worker's first touch of an expired at-threshold poison key cannot plan
the poisoned anchor into the admitted probe. The cached planning view MUST
be honored only while it is younger than the minimum cooldown: a circuit
another replica opens after this worker's last load is then either still
cooling — and the submit-time gate suppresses the request before dispatch —
or refreshed at planning before its expired cooldown can admit a probe, so
a cached below-threshold or reset row cannot hide a remote opening from
the anchor decision. A confirmed durable miss MUST be cached for the same
planning window, so healthy hard keys do not pay a planning-time round
trip on top of the submit-time load; a row another replica creates after a
cached miss is still enforced at submission while its cooldown runs. The
miss cache MUST be hard-capped — enforced where entries are inserted,
since the sweep runs before the durable await and a concurrent burst can
land past it — and swept in insertion order rather than scanned in full
under the shared lock, so high-cardinality healthy traffic cannot make
every load pay for the map. Both planning caches are
performance bounds, not correctness assumptions: a replica's cooldown is
stamped by its own wall clock and can look already expired anywhere else,
so a payload carrying a proxy-injected anchor MUST fail closed at
submission — with the same `bridge_previous_response_not_found` rejection
the planning gate surfaces — when the key's poison quarantine is active by
dispatch time. That submission gate is what holds under arbitrary replica
clock skew; client-supplied anchors are never refused by it. When it fails
closed after the admission gate has already claimed the half-open probe,
it MUST hand the probe back, or the phantom lease suppresses the client's
corrected full-history resend — the very request the rejection asks for —
for up to the whole lease. Only a probe this request itself claimed may be
handed back, identified by an exact lease token captured immediately after
admission: a lease unchanged since before admission belongs to a request
already in flight (a proof-gated replay bypasses it), a changed deadline
at capture is this admission's claim — including one made after the load
dropped a stale lease through a fresh adoption — and the handback clears
only that exact token, so a lease another submission installed later is
never mistaken for this request's own; releasing the probe another request
is flying would let a second dispatch run beside it. Handing a probe back
MUST restore the transition marker the admission consumed — an expired but
positive cooldown — because a lease is claimed only while an expired
cooldown transitions to half-open, and leaving both timers at zero would
admit every follow-up unleased instead of leasing exactly one corrected
resend. Every pre-dispatch exit hands the probe back the same way: a
claimed lease whose request never reached the upstream send — a rejected
anchor, a recovery-journal or ledger refusal, a reconnect failure, a
completed-operation spool return — MUST be released by the submission's
finalizer, or traffic is suppressed for the whole lease behind a probe
that never flew. The finalizer decides by the send-attempt marker, never
the sent timestamp: an ambiguous send failure clears the timestamp while
the frame may already be running upstream, and releasing that probe would
let a second dispatch run beside it. When continuity resolution
replaces the incoming key with a different canonical key, the load MUST be
repeated for that canonical key before the suppression checks consult it,
so a request arriving through a turn-state, previous-response, or session
alias receives the same quarantine protection as one arriving on the
canonical key directly.

A replay-dispatch claim that misses its CAS proves a probe holds the
lease only when the row's admission generation advanced past the captured
one on a row still at the effective threshold and still carrying the
captured lineage's version — a reset preserves the admission generation
while starting a new lineage, so an advance from an earlier lineage is not
a probe in the new one: a sibling completion resets
the row and changes its version without touching that generation, a
probe-then-reset sequence keeps the advanced generation on a zero-count
row with no timer left, and a purged-and-recreated lineage restarts
generations below the captured one. In every such case the suppression
MUST report the timer the fresh row actually carries rather than a
half-open wait no probe owns; a purge or lookup outage proves nothing and
reports the same way.

When the cooldown expires, each worker process MUST admit exactly one probe
request and MUST keep suppressing its other non-bypassed requests for that
key while that probe may still be running. Probe admission is process-local:
the half-open lease is not persisted, and replicas do not coordinate probe
admission (an accepted residual recorded in this change's `design.md`). When the circuit opens on an eventless
poison-class failure (`stream_incomplete` or `stream_idle_timeout` with no
observed response event), the proxy MUST quarantine the session key as
specified under the silent-session quarantine requirement, so the probe
admitted after the cooldown is planned without the anchor the circuit opened
on. This MUST hold however the circuit reached its threshold: when a replica's
local count is below the threshold and merging the returned durable row is
what raises its view to the threshold and opens the circuit, the recording
replica MUST re-evaluate the quarantine against the merged state, because it
never observed the threshold under its own lock. The post-write quarantine
verdict MUST derive from the adopted row's detail and count, not the local
strike's class: a `clean_close` losing to a poison opening still quarantines
the key, and a poison quarantine armed speculatively by a strike whose
opening did not survive persistence MUST be revoked, fenced on the exact arm
so any concurrent re-arm is preserved; when that arm upgraded a weaker
quarantine that was active on its own evidence, revocation MUST restore the
prior reason and deadline rather than evicting the weaker fence with the
upgrade. Revocation MUST be fenced on the poison arm's own provenance, not
the raw entry generation, because a weaker fence arming during the
speculative window bumps the generation while the no-downgrade guard keeps
the poison reason; that concurrent weaker fence is what revocation
downgrades to, restored at its own deadline rather than the disproved
arm's longer floor. A completion's generation-fenced quarantine clear MUST
apply the same provenance fence and the same downgrade to poison entries,
so a successful replay is not left classified as poisoned by a concurrent
weaker arm's generation bump. The completion's clear of its own session key
MUST fence the same way, on the generation captured before its settlement
and registration awaits: a strike arming a new quarantine during those
awaits is evidence the completion does not disprove, and that quarantine
MUST survive the clear, or the next half-open probe is planned with the
newly poisoned anchor on a key already marked loaded. The clear MUST also
be gated on the fresh anchor's durable registration actually confirming: a
swallowed durable alias failure leaves the old poisoned anchor as the
stored one, and with the circuit already settled the quarantine is the
only protection a replica change or restart has left. A completed event
that never attempts the registration — no usable response id, or no
matched request — confirms nothing, and the quarantine MUST survive it;
such a completion MUST also leave a poison episode unsettled, because
settling it would replace the poison row with a zero-count tombstone
while the old anchor stays stored, and the next planning load would read
the zero count as a disproved episode, revoke the quarantine, and inject
the dead anchor into a full resend. When the settle
succeeded and the registration then failed while poison evidence existed
before the settle, that evidence MUST be re-seeded durably — the row
re-opened at the circuit threshold with the prior poison class — because
the kept local quarantine alone is revoked by the next load reading the
zeroed row as a disproved episode, and other replicas never arm; a CAS
drop against a row that moved concurrently defers to the newer evidence.
The settle-to-registration window itself MUST stay durably suppressing: a
completion replacing a poison episode settles onto the transitional
anchor_abandoned tombstone and erases it, fenced on the exact reset row,
only after the fresh anchor's registration commits, and the
failed-settlement suppression persists the same transitional tombstone —
promoted to the superseded sentinel only after the registration commits,
rolled back under a fence expecting the tombstone when it fails — so a
crash or ownership takeover anywhere inside the window leaves a row that
fails deltas closed rather than one read as a disproved episode handing
replicas the old poisoned anchor. A row already carrying the tombstone
settles onto the tombstone again — never onto a plain reset — since its
registration can equally fail or never run. The tombstone MUST outlive the
circuit-state TTL: neither the load-path stale purge nor the on-demand
fenced purge may take it, and the scheduled purge reaps it only past the
bridge-retention cutoff its caller supplies AND only when no durable
session — resolved by the key directly or through an alias — still
stores continuity for it: a crash between a poison settle and its
registration leaves a live session whose lease delta-only requests keep
refreshing while the tombstone's own epoch stays fixed, and an age-only
reap would hand the next request the poisoned anchor the tombstone
fences. The continuity it guards lives for the session, not the circuit
TTL. A consult that authorizes a local episode from a durable poison row
MUST adopt that row's epoch, admission generation, and higher count onto
the episode unconditionally — an unpersisted local write and a
cross-replica strike that moved the row alike leave a stale local fence
that both settlement attempts would reject, standing the removed
anchor's cooldown. A promote or erase whose fenced rewrite misses MUST
reconcile on the row's own values — strike merges keep the tombstone
sticky, so a miss means the count moved, not that the tombstone was
replaced: a zeroed row erases plain, a positive count promotes to the
superseded sentinel, and a second miss defers to the next completion.
The merged-opening quarantine arm MUST use the circuit threshold, so an
adopted poison row that reaches it arms the quarantine even when the local
strike was clean. The poison
classification carries its OWN deadline: only a poison arm may extend
it, a weaker arm extends only the shared session fence, and the
anchor-is-dead answer expires on the poison deadline even while weaker
evidence keeps the session fenced — an expired classification also stops
outranking a weaker arm's reason. A
pre-settle capture can be blind — the completion's durable read failed —
while the settle's own load adopts an at-threshold poison row the capture
never saw. The settle MUST derive its reset detail from the state it
actually adopts: an adopted poison episode or existing tombstone settles
onto the fail-closed tombstone even when the caller captured nothing,
while local settlement and the fenced best-effort durable clear are
preserved so a successful terminal response still clears circuit state
during a transient outage. Fences captured blind stay conservative: a
quarantine armed by the settle's own load survives its miss-fenced clear,
and the lingering tombstone is healed by a later completion's fence-aware
settle-and-erase. Until that happens the tombstone itself MUST keep
fencing the stored anchor: a load adopting a tombstone row MUST NOT
revoke a surviving poison quarantine as a disproved episode, full-resend
planning MUST suppress durable-anchor injection over a tombstone exactly
as it does under quarantine, the submit-time gate MUST fail a
proxy-injected anchor closed over an adopted tombstone exactly as it does
under quarantine — the tombstone arms no quarantine by design, and
planning may have served a cached view that predates it — and the strike merge MUST keep the tombstone
detail sticky against every failure-class overwrite — only the fenced
settle and supersede paths, a completion establishing fresh continuity,
may rewrite it. An episode whose restored state IS the tombstone — a
sticky tombstone can carry later strikes onto a positive count — is
itself transitional, never clean: the failed-settlement suppression MUST
hand back a promotion token for it so a committed registration promotes
the tombstone to the superseded sentinel, or every later submission
carrying the freshly registered anchor is rejected against it. The
on-demand stale purge MUST fence on the observed failure count and
detail as well as the epoch and admission generation, because the
detail-only tombstone supersede and lagging-clock merges move neither of
the latter, and an unfenced purge would delete the crash-safety fence
another replica just installed. A refreshed row read after such a purge
miss MUST be adopted regardless of circuit age when it carries the
tombstone — the detail-only rewrite preserved the old epoch, and
rejecting it hands planning the dead anchor the tombstone guards. The
failed-settlement suppression MUST treat outstanding owed poison debt as
poison evidence even when a later non-poison strike overwrote the local
detail, so the debt cannot survive a fresh registration and abandon the
anchor just registered. The grouped multi-request settlement MUST apply
the same internal-warmup exclusions as the single-request terminal
branch. The abandonment-driven settle MUST yield to freshly registered
continuity: a sibling completion can register a NEW anchor and erase its
transitional tombstone between the abandonment's continuity clear and
its settle, and re-writing the tombstone then would durably fail every
valid follow-up riding the fresh anchor with no registration left to
erase it — a post-clear continuity re-read showing fresh evidence in EITHER
continuity column — a response anchor or a turn state present and
different from the abandoned capture, since a delta can resolve through
the turn state alone — downgrades the settle to a plain reset, while an
unknown or unchanged re-read keeps the tombstone. The check-to-settle
window itself MUST be reconciled after the write: when the settle wrote
a tombstone, one more continuity re-read showing fresh evidence erases
it through the fenced detail-only rewrite on the exact settled row —
both sides reconciling after their own writes is what makes every
interleaving converge, and the fence defers to any newer write. The
abandonment settle MUST also be fenced to the episode that authorized it
— the epoch, failure count, and admission generation captured BEFORE the
continuity clear's await, the closest snapshot to the episode the poison
consult validated — leaving a nonmatching newer row untouched, a claimed
replay generation included: a replacement episode opened against the freshly registered
anchor carries its own valid cooldown, and resetting it would let the
newly poisoned anchor retry immediately. The episode fence binds the
CAS-miss chase as well: when the fenced reset misses because the row
moved, an episode-fenced settle MUST NOT re-fence on the moved row's own
version — a nonmatching row is a replacement lineage whose valid cooldown
the chase would durably zero — and the settlement stays owed; the
settle-wins chase belongs to completion callers whose own evidence
outranks concurrent strikes. A continuity-informed plain
reset is authoritative: the state-derived tombstone upgrade applies only
to a blind caller, never to one that saw fresh continuity replace the
poisoned anchor. The suppression's local marker MUST NOT flip a local
tombstone to the superseded sentinel before the registration commits —
the local cache is what a proof-gated resend that cannot reload consults,
and an early sentinel would bypass the fail-closed gates while the
poisoned anchor is still the stored one. The post-registration promotion
and erase writes MUST retry once on a transient durable failure before
deferring to the next completion's healing, since a skipped rewrite
leaves every replica rejecting the newly valid anchor. Every admission
that can claim the half-open probe MUST hand it back when its caller
exits without advancing a send attempt past the captured baseline — the
internal precreated-retry path included, whose requests already carry
prior attempts and therefore key the release on advancement, not on a
zero count. The owed-debt arm and the sticky-detail
fence MUST both use the circuit threshold, so the strike that opens the
circuit arms and preserves the debt whose poison evidence already authorized
the abandonment.
The claimed-probe token MUST be handed out by the admission's claim under
its own lock, never inferred from before/after reads. Every fence captured
for a later clear MUST be captured under the same provenance rule the clear
applies — a poison entry's poison provenance rather than its raw generation
— or a weaker fence arming before the capture blocks the very clear the
capture was meant to authorize. That re-evaluation MUST turn on the merged opening itself and not on
the cooldown it leaves: a merge can adopt a cooldown that has already elapsed,
and such a key is at its threshold with no cooldown left, so the next request
is the half-open probe the quarantine exists to protect. A `clean_close` opening MUST NOT quarantine the key.

Every client-facing suppression MUST report this, not only the pre-created
submission gate: the stream-idle fail-closed paths and the stale-anchor
generation-claim suppression return the same `upstream_request_timeout` 503 and
MUST describe the same timer.

When the proxy suppresses a submission, the `retry_after_seconds` it returns
and the detail it logs MUST reflect the timer that is actually refusing the
request: the cooldown while the cooldown is active (`hard_key_cooldown`), and
the half-open lease once the cooldown has expired (`hard_key_half_open`). The
suppression message MUST NOT describe the bridge as cooling down when the
cooldown has expired.

The clean-close retry jitter MUST be drawn uniformly from 0 up to the fixed
2 second maximum (`_HTTP_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS`); the
maximum is not a runtime setting.

The proxy MUST evict process-local circuit entries and their loaded/persisted
markers after one hour without use, independently of durable-row cleanup, so
one-shot hard-affinity keys cannot grow the worker's memory without bound.

Before every hard-affinity retry decision, the proxy MUST refresh the durable
row so a cooldown opened by another replica is observed even when this process
has already loaded the key. A durable lookup or persistence failure MUST NOT
crash the request; the proxy MUST continue using available local state and
record the failure for observability. Rows older than one hour MUST be treated
as expired and removed. A successful terminal response MUST clear the local
and durable circuit state.

#### Scenario: idle bridge retirement does not consume a circuit strike

- **GIVEN** a hard-affinity HTTP bridge has no pending requests
- **WHEN** its upstream WebSocket closes and the idle bridge is retired
- **THEN** the retry-circuit failure count for that key remains unchanged
- **AND** a later request is not placed in cooldown because of the idle close

#### Scenario: eventless pending retirement consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** the bridge retires because the upstream fails before acknowledging the request
- **THEN** the retry circuit records exactly one failure for that request lifecycle

#### Scenario: eventless terminal error frame consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** upstream fails that request with a terminal error frame (for example a rewritten `previous_response_not_found`) before any response event
- **THEN** the retry circuit records exactly one failure for that request lifecycle
- **AND** a subsequent retirement of the same lifecycle does not record a second failure

#### Scenario: native terminal failure envelope consumes a strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no counted response event
- **WHEN** upstream fails it with a native `response.failed` envelope that never sent `response.created`
- **THEN** the envelope still consumes one attempt-scoped retry-circuit strike
- **AND** two such envelopes on the same key open the circuit and quarantine it with reason `retry_circuit_poisoned_anchor`

#### Scenario: the terminal strike lands before the client observes completion

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no counted response event
- **WHEN** upstream fails it with an eventless terminal error frame
- **THEN** the retry-circuit failure is recorded before the terminal frame or its end-of-stream sentinel reaches the downstream queue
- **AND** the terminal frame is still published to the client afterwards

#### Scenario: grouped continuity failure records one strike per eventless request

- **GIVEN** a hard-affinity HTTP bridge with several pending requests sharing one anchor
- **WHEN** upstream reports `previous_response_not_found` and the grouped settlement fails them all with synthetic terminal events
- **THEN** each grouped request that observed no response event records one attempt-scoped strike
- **AND** the strikes are recorded before the grouped terminal events are persisted or delivered

#### Scenario: a terminal poison strike clears the durable anchor

- **GIVEN** a hard-affinity bridge key whose stored anchor upstream has rejected
- **WHEN** a terminal failure frame opens the retry circuit on a poison-class detail
- **THEN** the durable continuity anchor for that key is cleared with its alias rows
- **AND** the clear runs after the terminal frame reaches the client, not before it

#### Scenario: grouped poison strikes clear the durable anchor

- **GIVEN** a grouped continuity failure carrying two eventless requests on one hard key
- **WHEN** the grouped strikes carry the circuit through its threshold
- **THEN** the durable anchor is cleared after the grouped terminal frames are published

#### Scenario: a cancelled anchor clear still finalizes the settled request

- **GIVEN** a terminal poison strike whose durable clear is cancelled mid-write
- **WHEN** the terminal frame has already been published to the client
- **THEN** the request is still finalized and its session lease still released

#### Scenario: a merged cooldown extends an already-armed quarantine

- **GIVEN** a local opening that armed the poison quarantine from its own backoff
- **WHEN** durable persistence merges in a longer cooldown deadline
- **THEN** the quarantine floor is recomputed against the merged cooldown

#### Scenario: abandoning the anchor settles the circuit it invalidated

- **GIVEN** a hard-affinity key whose retry circuit is cooling from poison-class failures
- **WHEN** the durable anchor those failures hit is successfully abandoned
- **THEN** the retry circuit for that key is cleared rather than left cooling
- **AND** a fenced or failed abandonment leaves the cooldown running
- **AND** a strike write that lands after the settle deletes the row it resurrected, fenced on its own update time

#### Scenario: a proven-dead anchor fails fast instead of retrying unanchored

- **GIVEN** a key quarantined for a poisoned anchor after repeated eventless poison-class failures
- **WHEN** an anchored request fails with an explicit previous-response rejection and enters local rebind
- **THEN** the client receives the explicit rejection as `bridge_previous_response_not_found` after exactly one upstream attempt
- **AND** the anchor is not stripped for an unanchored retry, because the reaching payload does not retain the anchor's context
- **AND** an explicit rejection on a key that is not quarantined still retries the same anchor

#### Scenario: midstream retirement does not consume a pre-response strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with an observed response event
- **WHEN** the bridge retires before completion
- **THEN** the pre-response retry-circuit failure count remains unchanged

#### Scenario: the second hard-key failure opens a durable circuit

- **GIVEN** a hard-affinity key has one recorded pre-response failure
- **WHEN** a second eligible failure is recorded
- **THEN** the proxy opens the retry circuit
- **AND** persists at least two consecutive failures and a cooldown deadline
- **AND** subsequent pre-created replay is suppressed until that deadline

#### Scenario: circuit opened by eventless failures quarantines the key

- **GIVEN** a hard-affinity key has one recorded eventless `stream_incomplete` failure
- **WHEN** a second eventless `stream_incomplete` failure opens the circuit
- **THEN** the session key is quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** the next full-resend request on that key is planned without the durable anchor

#### Scenario: a circuit opened by the durable merge still quarantines the key

- **GIVEN** a replica that records an eventless `stream_incomplete` failure below the threshold under its own lock while the durable row already holds another replica's failures
- **WHEN** merging the returned durable row raises the recording replica's view to the threshold and opens the cooldown
- **THEN** that replica re-evaluates the quarantine against the merged state
- **AND** the session key is quarantined with reason `retry_circuit_poisoned_anchor`

#### Scenario: a merged opening whose cooldown already elapsed still quarantines

- **GIVEN** another replica opened the circuit long enough ago that its cooldown deadline is already in the past
- **WHEN** this worker's durable write merges that state and raises it to the threshold with no cooldown remaining
- **THEN** the key is still quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** the quarantine covers the half-open lease, because the next request on that key is the probe

#### Scenario: circuit opened by clean closes does not quarantine the key

- **GIVEN** a hard-affinity key has one recorded `clean_close` failure
- **WHEN** a second `clean_close` failure opens the circuit
- **THEN** the session key is not quarantined

#### Scenario: suppression reports the half-open lease after the cooldown expires

- **GIVEN** a hard-affinity key's cooldown has expired and a probe holds the half-open lease
- **WHEN** another request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining half-open lease
- **AND** the circuit event detail is `hard_key_half_open`
- **AND** the message does not describe the bridge as cooling down

#### Scenario: suppression reports the cooldown while cooling

- **GIVEN** a hard-affinity key's cooldown is active
- **WHEN** a request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining cooldown
- **AND** the circuit event detail is `hard_key_cooldown`

#### Scenario: retry decisions observe a cooldown opened by another replica

- **GIVEN** this replica previously looked up a hard-affinity key with no row
- **AND** another replica persists an open cooldown for that same key and API-key scope
- **WHEN** this replica evaluates the next pre-created retry
- **THEN** it refreshes durable state before deciding
- **AND** suppresses the retry for the persisted cooldown

#### Scenario: circuit state remains isolated by key and API-key scope

- **GIVEN** one hard-affinity key has an open circuit
- **WHEN** a different affinity key or API-key scope evaluates a retry
- **THEN** that request is not suppressed by the first key's circuit

#### Scenario: durable circuit lookup failure does not fail the request

- **GIVEN** durable retry-circuit lookup or persistence is unavailable
- **WHEN** the proxy evaluates or records a retry-circuit event
- **THEN** the request continues using any available local circuit state
- **AND** the failure is logged and exposed through retry-circuit observability

#### Scenario: late cooldown suppression retires the newly created session

- **GIVEN** a hard-key request has created or selected an HTTP bridge session
- **AND** the retry circuit is still in cooldown when late pre-created
  admission runs
- **WHEN** the request is suppressed before `response.create` is dispatched
- **THEN** the proxy returns the existing HTTP 503 cooldown error
- **AND** marks the session for reconnect and retirement after drain
- **AND** invokes bounded retirement
- **AND** does not send `response.create` upstream
- **AND** the session is not reusable for a later request

#### Scenario: startup cooldown terminal handling retires its session

- **GIVEN** a hard continuity-bound request has an already-created bridge
  session but no safe replay bypass
- **AND** the retry circuit is in cooldown before the startup submit attempt
- **WHEN** startup terminal handling returns the cooldown failure
- **THEN** the existing 503 or synthetic `stream_idle_timeout` envelope is
  preserved
- **AND** the session is marked for reconnect and retirement after drain
- **AND** bounded retirement is invoked
- **AND** submit is not attempted

#### Scenario: cooldown replay bypass does not retire the session

- **GIVEN** a hard-key request is in cooldown
- **AND** proof-gated or operation-fenced continuity replay is allowed
- **WHEN** the retry decision runs
- **THEN** the request remains eligible for that authorized replay
- **AND** the generic cooldown suppression retirement is not triggered

#### Scenario: a concurrently admitted probe keeps its session

- **GIVEN** a hard-key retry circuit at cooldown expiry
- **AND** request A passes admission as the half-open probe and has not yet
  reached its dispatch registration
- **WHEN** request B on the same session is suppressed by A's probe lease
- **THEN** B returns the existing HTTP 503 cooldown error
- **AND** the session is not marked for reconnect or retirement and is not
  closed
- **AND** A proceeds past the pre-dispatch retiring fence to dispatch

#### Scenario: a session owned by other work is left to its owner

- **GIVEN** cooldown suppression rejects a request on a session that owns
  visible pending work, a registered admission waiter, or another request's
  unanchored handoff
- **WHEN** the suppression returns
- **THEN** the session is not marked for retirement and is not closed
- **AND** the owning work's own settlement path governs the session

#### Scenario: a suppressed request releases only its own admission registration

- **GIVEN** a submit counted itself as an admission waiter at entry
- **WHEN** the retry circuit suppresses it or any other pre-dispatch exit fails it
- **THEN** its registration is released without disturbing other waiters
- **AND** a retirement that registration was deferring runs once the session
  is unowned

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely. Responses streams over both HTTP and WebSocket transports MUST use `http_responses_stream_request_budget_seconds` when it is configured; they MUST fall back to `proxy_request_budget_seconds` only when no stream-specific budget is available.

`compact_request_budget_seconds`, `stream_idle_timeout_seconds`, `proxy_request_budget_seconds` and `http_responses_stream_request_budget_seconds` are dashboard-managed (`configuration-tiers`): each has a nullable `dashboard_settings` column of the same name whose non-NULL value MUST override the process environment value, which in turn overrides the code default. Consumers MUST read the effective value from the `SettingsCache` snapshot bound at the request or WebSocket entry point and MUST NOT query the database per request or per event. The environment variables remain as deprecated fallbacks and MUST NOT be copied into the column by the server; a client that echoes the effective values of a `GET /api/settings` response back through `PUT` stores them as explicit dashboard values (clients MUST send only the fields they intend to change). `GET /api/settings` MUST report any environment value the `Settings` model accepts, including one outside the bounds `PUT` enforces.

Background consumers derived from the stream budget (the quota warm-up claim lease, which floors at the stream budget) MUST resolve the effective value from a dashboard snapshot the scheduler tick already holds, not from the environment alone. The background warm-up probe itself MUST stream under that same snapshot, so a healthy probe still provably outlives its claim lease when the dashboard budget is below the environment value. `upstream_connect_timeout_seconds` MUST NOT exceed the effective `http_responses_stream_request_budget_seconds` (`upstream-connect-within-stream-budget`); `PUT /api/settings` MUST reject a change that introduces that violation with `400 timeout_invariant_violation`, alongside the existing `admission-wait-within-stream-budget` rule.

#### Scenario: compact and stream watchdog defaults leave room for long turns
- **WHEN** the service starts with default configuration
- **THEN** `compact_request_budget_seconds` is at least 180 seconds
- **AND** `stream_idle_timeout_seconds` is at least 600 seconds

#### Scenario: WebSocket Responses stream uses the stream-specific request budget
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **WHEN** a native WebSocket Responses stream computes its request deadline
- **THEN** the stream budget is 7200 seconds
- **AND** the generic 600 second proxy request budget does not terminate the turn

#### Scenario: WebSocket reconnect keeps the stream-specific deadline
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **AND** a native WebSocket Responses request needs to reconnect after more than 600 seconds but less than 7200 seconds
- **WHEN** the reconnect performs account selection and opens its replacement upstream WebSocket
- **THEN** both operations remain bounded by the original 7200-second stream deadline
- **AND** the reconnect does not fail solely because the generic 600-second budget elapsed

#### Scenario: Dashboard value overrides startup environment
- **GIVEN** `CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS=600` in the process environment and an operator has stored `900` for `proxy_request_budget_seconds` through `PUT /api/settings`
- **WHEN** a new request computes its request deadline on any replica
- **THEN** the deadline uses the 900 second dashboard value
- **AND** `GET /api/settings` reports `proxyRequestBudgetSeconds: 900` with `provenance.proxy_request_budget_seconds.source = "dashboard"`

#### Scenario: Clearing the dashboard value returns to the environment
- **GIVEN** the same deployment
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: null`
- **THEN** the column becomes NULL, new requests use the 600 second environment value, and the provenance source becomes `"env"` (or `"default"` when the environment matches the code default)

#### Scenario: Timeout invariants are enforced on the effective values
- **GIVEN** the environment-only admission wait is 10 seconds
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: 5`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `admission-wait-within-proxy-budget`
- **AND** a request that raises every violated budget in the same `PUT` is accepted

#### Scenario: Dashboard stream budget overrides startup environment
- **GIVEN** `CODEX_LB_HTTP_RESPONSES_STREAM_REQUEST_BUDGET_SECONDS=7200` in the process environment and an operator has stored `3600` for `http_responses_stream_request_budget_seconds` through `PUT /api/settings`
- **WHEN** a new HTTP or WebSocket Responses stream computes its request deadline on any replica
- **THEN** the stream budget is 3600 seconds
- **AND** `GET /api/settings` reports `httpResponsesStreamRequestBudgetSeconds: 3600` with `provenance.http_responses_stream_request_budget_seconds.source = "dashboard"`
- **AND** the next quota warm-up claim lease is 3600 seconds long rather than 7200
- **AND** the background warm-up probe streams under the same 3600 second budget

#### Scenario: Connect timeout is bounded by the effective stream budget
- **GIVEN** default configuration
- **WHEN** the operator sends `PUT /api/settings` with `upstreamConnectTimeoutSeconds: 100` and `httpResponsesStreamRequestBudgetSeconds: 60`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `upstream-connect-within-stream-budget`
- **AND** a `PUT` with `httpResponsesStreamRequestBudgetSeconds: 5` is rejected naming `admission-wait-within-stream-budget`
- **AND** nothing is stored in either case

### Requirement: Responses upstream websocket liveness is bounded

The proxy MUST configure direct and routed upstream Responses WebSocket transports with finite ping/pong liveness detection derived from `proxy_downstream_websocket_idle_timeout_seconds`, read as the effective dashboard-managed value (a non-NULL `dashboard_settings.proxy_downstream_websocket_idle_timeout_seconds` overrides the environment value) from the snapshot bound to the connection. A direct connection MUST use the native helper watchdog when native egress is selected and the Python `websockets` watchdog only on the pre-dispatch missing-helper fallback. When an established Responses WebSocket is terminated because its transport did not receive the required pong, the adapter MUST classify the failure as `upstream_websocket_liveness_timeout`. Direct WebSocket and HTTP bridge relay owners MUST treat that failure as account neutral, MUST NOT transparently replay a pending request whose delivery is ambiguous, MUST finalize its pending request ownership exactly once, and MUST retire the affected upstream socket so a later client retry opens a fresh connection. An HTTP bridge reader MUST suppress its own pending-deque settlement only when a concurrent submitter explicitly claimed liveness-settlement ownership under the session lifecycle lock; `session.closed` alone MUST NOT suppress settlement.

#### Scenario: Direct Responses websocket loses pong liveness

- **GIVEN** a direct upstream Responses WebSocket has been established
- **WHEN** the selected native-helper or Python fallback keepalive watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Routed Responses websocket loses pong liveness

- **GIVEN** a routed upstream Responses WebSocket has been established for an HTTP bridge or direct WebSocket client
- **WHEN** the aiohttp heartbeat watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Long turn remains healthy through control frames

- **GIVEN** a Responses turn emits no application event within the liveness interval
- **WHEN** the upstream WebSocket continues replying to transport pings
- **THEN** the proxy keeps the upstream socket open
- **AND** the existing Responses request budget remains authoritative for the turn

#### Scenario: Closed bridge without a sender claim later loses pong liveness

- **GIVEN** an HTTP bridge session has multiple pending requests
- **AND** a separate submit failure marks the session closed without claiming liveness-settlement ownership
- **WHEN** the still-running upstream transport later expires its heartbeat
- **THEN** the reader settles every pending request with `upstream_websocket_liveness_timeout`
- **AND** the selected account receives no failure-health signal

#### Scenario: Claimed bridge settlement survives submitter cancellation

- **GIVEN** an HTTP bridge submitter claims liveness-settlement ownership after its send fails
- **WHEN** the submitter is cancelled before whole-deque settlement completes
- **THEN** settlement continues until every pending sibling is finalized exactly once
- **AND** the submitter cancellation is preserved after settlement completes

#### Scenario: Dashboard idle timeout controls new connections

- **GIVEN** `CODEX_LB_PROXY_DOWNSTREAM_WEBSOCKET_IDLE_TIMEOUT_SECONDS=120` and an operator stores `45` through `PUT /api/settings`
- **WHEN** a new downstream WebSocket connection is accepted on any replica
- **THEN** its idle timeout and the derived upstream ping/pong liveness window use 45 seconds
- **AND** connections accepted before the change keep the value they were bound with

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests
are pending and have not reached a terminal event, the proxy MUST record a
transient upstream error for the account before signaling failure for those
pending requests, except when the close carries a classified process-wide
network failure or upstream WebSocket liveness timeout, is a clean close
(`close_code = 1000`) before any `response.*` event, or carries the classified
per-socket `upstream_keepalive_timeout` transport error. Clean pre-response
closes, keepalive timeouts, process-wide network failures, and liveness
timeouts MUST remain account-neutral and use their classified error and bounded
retry or retry-circuit handling. For other closes, the proxy MUST surface
`stream_incomplete` to affected pending requests except when a direct Responses
WebSocket request has already successfully emitted a finite integer
`sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST
record the request outcome as `stream_incomplete` without emitting a synthetic
terminal frame under the active response id, then MUST close the downstream
WebSocket with code 1011, unless the request satisfies the verified
no-generation prewarm recovery contract defined in "Direct WebSocket replay
never mixes numeric response sequences" and its one-shot replay succeeds.

#### Scenario: websocket closes before pending responses complete
- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: sequenced direct websocket closes before completion
- **GIVEN** a direct Responses WebSocket request has successfully emitted a finite integer `sequence_number`
- **AND** the request does not satisfy the verified no-generation prewarm recovery contract
- **WHEN** the upstream websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: websocket liveness timeout remains account neutral
- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** its transport reports `upstream_websocket_liveness_timeout`
- **THEN** the pending request fails with that classified error code
- **AND** the account receives no failure-health signal
- **AND** the request is not transparently replayed

#### Scenario: clean pre-response close does not penalize the account
- **GIVEN** a hard-affinity HTTP bridge request is pending with no surfaced response event
- **WHEN** the upstream websocket closes cleanly before response output
- **THEN** the proxy records the clean-close retry-circuit outcome
- **AND** the selected account is not penalized

#### Scenario: created-only generate-false Codex prewarm recovers
- **GIVEN** a direct Responses WebSocket request is classified by Codex turn
  metadata as `request_kind = "prewarm"`
- **AND** its normalized request body contains `generate = false`
- **AND** only `response.created` at numeric sequence `0` has been sent
  downstream, with no other response progress or visible output
- **WHEN** the upstream websocket closes before the terminal event
- **THEN** the proxy MAY perform the existing bounded one-shot replay
- **AND** it suppresses the replayed `response.created`
- **AND** it forwards only replay numeric sequences that advance beyond `0`
- **AND** the recovered request is finalized and logged exactly once

### Requirement: HTTP SSE stream idle timeouts remain account-neutral

When an HTTP SSE Responses stream's first upstream event is `response.failed` with `code=stream_idle_timeout`, the proxy MUST exclude that account from the remainder of the same request and MAY fail over to another account. It MUST NOT write account error-health (`record_error`, rate-limit, quota, or permanent failure) for that idle timeout. Request logs MUST still record `stream_idle_timeout` on the idle attempt.

#### Scenario: First-event stream idle timeout failovers without health penalty

- **GIVEN** an HTTP SSE Responses stream whose first upstream event is `response.failed` with `code=stream_idle_timeout`
- **AND** another healthy account is available
- **WHEN** the proxy retries the request
- **THEN** the idle account is excluded from the remainder of this request
- **AND** the idle account receives no error-health write
- **AND** the client receives the later account's successful stream
- **AND** the idle attempt's request log still uses `error_code=stream_idle_timeout`

### Requirement: Single HTTP bridge previous-response misses recover or fail closed
When an HTTP bridge session receives an anonymous upstream `previous_response_not_found` error for a single pending follow-up request, the service MUST treat the error as an internal continuity-loss signal. It MUST either recover through the existing previous-response rebind path or rewrite the error to a retryable continuity failure instead of forwarding the raw upstream invalid-request error.

#### Scenario: single pending HTTP bridge follow-up loses previous-response continuity
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has exactly one pending request with `previous_response_id`
- **AND** upstream emits `previous_response_not_found` without a `response.id`
- **THEN** the service attempts the existing previous-response recovery path
- **AND** if recovery is unavailable, it emits a retryable continuity failure for that request
- **AND** the downstream error code is not `previous_response_not_found`

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error. A payload that only carries incremental tool outputs for tool calls that are not also present in the same request is not self-contained and MUST NOT be replayed as a fresh turn without `previous_response_id`.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items without their matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without `previous_response_id`
- **AND** the downstream client receives a retryable continuity failure rather than a fabricated fresh turn

### Requirement: Parameterless invalid previous-response errors use continuity recovery

When an upstream Responses WebSocket rejects an anchored request with `type = "invalid_request_error"`, no `code` or `param`, and the normalized message ``Invalid `previous_response_id``` with or without one trailing period, the service MUST classify the frame as a previous-response continuity miss. It MUST apply the same replay, masking, ownership, and account-health rules as the canonical `previous_response_not_found` error and MUST NOT relay the raw invalid-request frame downstream. A different named parameter or any other trailing punctuation MUST NOT match this error shape.

#### Scenario: Codex-native delta continuation receives the canonical recovery signal

- **GIVEN** a Codex-native `/backend-api/codex/responses` request carries `previous_response_id` and delta-only tool output that cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the downstream client receives a sanitized error with `code = "previous_response_not_found"`
- **AND** the raw upstream envelope and previous response id are not exposed

#### Scenario: Self-contained full resend is replayed without the rejected anchor

- **GIVEN** an anchored direct WebSocket request retains a self-contained full-resend body that is safe to replay without `previous_response_id`
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the service reconnects and replays the retained body without `previous_response_id`
- **AND** the raw upstream error is not sent downstream

#### Scenario: Public WebSocket retains generic continuity masking

- **GIVEN** a public `/v1/responses` WebSocket request carries `previous_response_id` but cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error
- **THEN** the downstream client receives the existing sanitized `stream_incomplete` continuity failure
- **AND** neither `previous_response_not_found` nor the raw upstream envelope is exposed

#### Scenario: Unrelated invalid requests retain their original classification

- **WHEN** upstream returns `invalid_request_error` with a different message or names a parameter other than `previous_response_id`
- **THEN** the service MUST NOT classify that error as a previous-response continuity miss

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped `previous_response_not_found` error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events

When serving streaming `POST /v1/responses`, the service MUST forward a
string-valued event type only when it is exactly `error` or begins with
`response.`. Other string-valued event types MUST be dropped before they reach
the public stream. OpenAI-shaped backend requests with public contract
enforcement enabled MUST follow the same filtering rule. Native Codex requests
with public contract enforcement disabled MUST retain upstream vendor events.

#### Scenario: Codex-internal rate-limit event is dropped before response.created

- **WHEN** upstream emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain `codex.rate_limits`
- **AND** its first event MUST be `response.created`

#### Scenario: Timing diagnostics are filtered without losing text or completion

- **WHEN** upstream emits `responsesapi.websocket_timing` before, between, or after standard response events
- **THEN** a public-contract stream MUST NOT contain that diagnostic
- **AND** standard text deltas and completion events MUST remain in order

#### Scenario: OpenAI-shaped backend request filters vendor events

- **WHEN** an OpenAI-shaped `/backend-api/codex/responses` request enables public contract enforcement
- **THEN** its response stream MUST apply the public event-family filtering rule

#### Scenario: Codex-internal events on the Codex CLI route are preserved

- **WHEN** a native `/backend-api/codex/responses` request disables public contract enforcement
- **THEN** the response stream MUST retain `codex.rate_limits` and `responsesapi.websocket_timing` in upstream order

### Requirement: Streamed /v1 responses terminal output is backfilled from item events
When serving streaming `POST /v1/responses`, if the upstream's terminal `response.completed` or `response.incomplete` event carries `output` as missing or as an empty list, the service MUST reconstruct `output` from the `response.output_item.done` events emitted earlier in the same stream before yielding the terminal SSE event. The reconstructed `output` MUST preserve the `output_index` ordering and the raw item payloads. When the terminal `response.completed` / `response.incomplete` already carries a non-empty `output`, the service MUST forward it unchanged.

#### Scenario: Terminal response.completed with empty output is backfilled from streamed items
- **GIVEN** the upstream emits `response.output_item.done` events with valid message or function-call items
- **WHEN** the upstream's terminal `response.completed` event carries `output: []`
- **THEN** the public stream's terminal `response.completed` event MUST carry the reconstructed `output` array, populated from the streamed `output_item.done` items in `output_index` order
- **AND** an OpenAI Python SDK consumer calling `stream.get_final_response().output` MUST receive the same populated list

#### Scenario: Terminal response.completed already carries output
- **WHEN** the upstream's terminal `response.completed` event already includes a non-empty `output` array
- **THEN** the public stream's terminal event MUST carry that `output` array unchanged

### Requirement: Public /v1 responses SSE stream starts with response.created
When serving streaming `POST /v1/responses`, the first OpenAI-contract event the public stream emits MUST be `response.created`. When the upstream's first standard `response.*` event is not `response.created` (for example when the Codex backend jumps directly to `response.failed` on upstream rejection mid-stream), the service MUST synthesize a `response.created` SSE event from the source event's `response` envelope and emit it before forwarding the source event, so that consumers using the OpenAI Python SDK's `responses.stream(...)` parser do not raise `RuntimeError`.

#### Scenario: Upstream error stream that skips response.created is repaired
- **WHEN** the upstream's first standard event is `response.failed` (no preceding `response.created`)
- **THEN** the public stream MUST emit a synthesized `response.created` event derived from the failed event's `response` envelope before forwarding the `response.failed` event
- **AND** an OpenAI Python SDK consumer iterating the stream MUST NOT raise `RuntimeError` from the parser's initial-response check

#### Scenario: Normal stream is not double-emitted
- **WHEN** the upstream's first standard event is already `response.created`
- **THEN** the public stream MUST emit exactly one `response.created` event (no synthesized duplicate)

### Requirement: Upstream overload envelopes are classified as retryable transient failures

When `classify_upstream_failure` observes an upstream error envelope whose `code` is `overloaded_error` or `server_is_overloaded`, the system MUST treat it as `retryable_transient` regardless of the accompanying HTTP status. Streamed Responses API traffic can deliver the overload envelope on a connection that has already returned HTTP 200, so a 5xx-only heuristic is insufficient to drive account fail-over and bounded retry.

#### Scenario: `overloaded_error` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the failover layer is eligible to retry the request or fail over to another account instead of returning a non-retryable error to the client

#### Scenario: `overloaded_error` with a 5xx status remains retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` is 500, 502, 503, or 504
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the result is the same as the no-status path, so the 5xx fallback heuristic is not the only signal driving the decision

#### Scenario: `server_is_overloaded` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="server_is_overloaded"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the streaming retry layer is eligible to retry the request before surfacing the terminal overload event

#### Scenario: HTTP bridge retries a pre-created overload event

- **GIVEN** the HTTP responses session bridge is enabled
- **WHEN** the first upstream `response.failed` or `error` event has `code="overloaded_error"` or `code="server_is_overloaded"`
- **THEN** the bridge MUST retry the pre-created request before forwarding that terminal event
- **AND** the bridge MUST preserve its existing no-replay behavior after downstream-visible output or for other fail-fast error codes

### Requirement: Strict function tool parameter schemas are pre-validated

The service MUST pre-validate the JSON schema attached to a function tool when that tool sets `strict: true`, before opening any upstream connection. The validation rules mirror OpenAI's Structured Outputs strict-mode policy (https://platform.openai.com/docs/guides/structured-outputs) and the existing `enforce_strict_text_format` policy for `text.format.json_schema`:

- Every `object` schema node MUST set `additionalProperties: false`.
- Every property under `properties` MUST appear in `required`.
- Every schema node MUST carry a `type` key (no empty `{}` schemas).
- The same rules apply recursively to nested object / array / combinator (`anyOf` / `oneOf` / `allOf`) schemas.

When any of those rules is violated, the service MUST reject the request with `HTTP 400 invalid_request_error` carrying:

- `error.code = "invalid_function_parameters"`
- `error.message = "Invalid schema for function '<name>': In context=<path>, <reason>."`
- `error.param = "tools[<index>].parameters"` for native Responses-API requests; `error.param = "tools[<index>].function.parameters"` for chat-completions requests routed through the coercion pipeline.

This brings strict function tool schema handling into parity with `text.format.json_schema`. Without it, an invalid strict tool schema reaches the upstream Codex backend, which closes the WebSocket with `close_code=1000` and surfaces as a generic `502 server_error / upstream_rejected_input`. Real OpenAI returns `400 invalid_function_parameters` for the identical payload. A 5xx on a deterministically-broken request also triggers retry / failover loops in well-behaved clients.

#### Scenario: Strict tool missing `additionalProperties` is rejected with 400

- **WHEN** a client sends `tools: [{"type": "function", "name": "f", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, "strict": true}]`
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"`, `error.message` matching `/Invalid schema for function 'f': In context=\(\), 'additionalProperties' is required to be supplied and to be false\./`, and `error.param = "tools[0].parameters"`

#### Scenario: Strict tool with `additionalProperties: true` is rejected

- **WHEN** a client sends a function tool with `strict: true` and `parameters.additionalProperties = true`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the same `'additionalProperties' is required to be supplied and to be false` message

#### Scenario: Strict tool with property missing from `required` is rejected

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, but `required` omits one of the listed `properties`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the `'required' is required to be supplied and to be an array including every key in properties` message

#### Scenario: Compliant strict tool is accepted

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, and every property listed in `required`
- **THEN** the proxy forwards the request to the upstream unchanged and the response is `200`

#### Scenario: `strict: false` or omitted strict skips pre-validation

- **WHEN** a client sends a function tool with `strict: false` or without a `strict` key, and the schema would have violated strict mode (e.g. missing `additionalProperties`)
- **THEN** the proxy does not run the strict pre-validation and forwards the request unchanged, matching pre-fix behavior for non-strict tools

### Requirement: Same-response side-effect tool-call replays are suppressed

When the proxy receives multiple downstream `response.output_item.done` events for the same response that describe the same side-effecting local tool operation, the proxy SHALL forward only the first event to the client.

The proxy SHALL treat `exec_command`, `write_stdin`, `multi_tool_use.parallel`, and `apply_patch_call` events as side-effecting. For these tools, a changed `call_id` alone MUST NOT make a same-response replay distinct.

When a `multi_tool_use.parallel` event contains duplicate nested side-effect operations, the proxy SHALL remove the duplicate nested operations before forwarding the event. Duplicate nested `exec_command` operations MUST ignore volatile output/wait fields such as `yield_time_ms` and `max_output_tokens`. Duplicate nested `write_stdin` operations MUST be scoped by `session_id` and `chars`. Duplicate nested `wait_agent` operations MUST be scoped by the target set.

Read-only function calls and matching operations under different response ids MUST continue to pass through.

#### Scenario: side-effect call replay uses a new call id

- **WHEN** a streamed response emits two `exec_command` output items with the same response id and arguments but different call ids
- **THEN** the proxy forwards the first event
- **AND** suppresses the second event

#### Scenario: read-only call ids stay distinct

- **WHEN** a streamed response emits two read-only function calls with the same arguments and different call ids
- **THEN** the proxy forwards both events

#### Scenario: later response ids stay distinct

- **WHEN** two responses emit the same side-effecting operation under different response ids
- **THEN** the proxy forwards both events

#### Scenario: parallel batch contains duplicate shell operations

- **WHEN** a `multi_tool_use.parallel` event contains two nested `functions.exec_command` operations with the same command and only different wait/output fields
- **THEN** the proxy forwards one nested operation inside the parallel batch
- **AND** does not forward the duplicate nested operation to the client

### Requirement: Continuity-dependent Responses follow-ups fail closed with retryable errors
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps. When forwarding a turn-state-anchored follow-up to its bridge owner fails with `bridge_owner_unreachable` and a fresh durable lookup shows the owner no longer holds an active lease (released, expired, or the row is missing or CLOSED), the service MUST recover the follow-up locally through durable takeover instead of returning the retryable error. The fresh durable lookup MUST use the same resolution semantics as request routing, including the latest-turn-state fallback, so a row originally resolved without a registered alias remains takeover-eligible. When the durable lease is still actively held by another instance — including DRAINING rows whose lease has not been released or expired — the service MUST keep failing closed with the retryable error.

#### Scenario: HTTP bridge loses local continuity metadata for a follow-up request
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` follow-up request depends on `previous_response_id` or a hard continuity turn-state
- **AND** the bridge cannot reconstruct the matching live continuity state from local or durable metadata
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: in-flight bridge follower loses continuity while waiting on the same canonical session
- **WHEN** a follow-up request waits on an in-flight HTTP bridge session for the same hard continuity key
- **AND** the bridge still cannot reconstruct safe continuity state once the leader finishes
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: multiplexed follow-ups fail closed only for the matching continuity anchor
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests with different `previous_response_id` anchors
- **AND** continuity loss is detected for exactly one of those anchors
- **THEN** the service applies the retryable fail-closed continuity error only to the matching follow-up request
- **AND** it does not expose raw `previous_response_not_found`
- **AND** unrelated pending requests continue on their own response lifecycle

#### Scenario: multiplexed follow-ups sharing one anchor fail closed together without leaking raw continuity errors
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests that share the same `previous_response_id` anchor
- **AND** upstream emits an anonymous continuity loss event such as `previous_response_not_found` for that shared anchor
- **THEN** the service rewrites each affected follow-up into a retryable continuity error
- **AND** no affected follow-up exposes raw `previous_response_not_found`
- **AND** the run remains usable for subsequent requests after the rewritten failures

#### Scenario: single pre-created follow-up still fails closed when continuity loss omits explicit response id in message
- **WHEN** a websocket follow-up request is pending with `previous_response_id` and has not received a stable upstream `response.id` yet
- **AND** upstream emits `previous_response_not_found` with `param=previous_response_id`
- **AND** the upstream error message omits the literal previous response identifier
- **THEN** the service still maps that continuity loss to the pending follow-up
- **AND** it rewrites the downstream terminal event to a retryable continuity error
- **AND** it does not surface raw `previous_response_not_found` to the client

#### Scenario: turn-state follow-up recovers locally after the owner released its lease
- **WHEN** a turn-state-anchored follow-up without `previous_response_id` is forwarded to its bridge owner during the post-shutdown ring grace window
- **AND** the forward fails with `bridge_owner_unreachable`
- **AND** a fresh durable lookup using the request-routing resolution semantics (registered alias or latest-turn-state fallback) shows the lease is released or expired
- **THEN** the service retries the follow-up locally through durable takeover instead of returning the retryable 503
- **AND** the takeover retry carries the fresh durable lookup as its continuity anchor even when the turn-state alias registration was lost
- **AND** a fresh durable lookup showing a live lease held by another instance — even for a DRAINING row — still fails closed with the retryable `bridge_owner_unreachable` error

#### Scenario: previous-response reuse does not register an abandoned creation future
- **WHEN** an HTTP bridge request resolves a live compatible session through `previous_response_id`
- **THEN** the bridge returns that existing session without registering an unresolved inflight session-creation future for its canonical key
- **AND** a subsequent request on the same previous-response anchor can reuse the session successfully

### Requirement: Live DRAINING durable leases reject foreign claims

When a durable HTTP-bridge session is `DRAINING` and another instance still holds an unexpired lease, a foreign `claim_live_session` MUST leave the current owner and lease unchanged even when `allow_takeover` is true. Local session create MUST use the same live-owner predicate as turn-state takeover and MUST NOT treat `DRAINING` alone, or a forced recovery after a missing ring endpoint, as permission to steal a live `DRAINING` lease. The locked claim row, not a stale pre-claim lookup, MUST be the source of the `DRAINING` decision. Expired, released, or `CLOSED` rows MUST remain takeover-eligible.

#### Scenario: Foreign claim refuses a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **WHEN** instance B claims the same key with `allow_takeover` false
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`
- **AND** A's lease expiry is unchanged

#### Scenario: Forced claim still refuses after an ACTIVE lookup becomes live DRAINING

- **GIVEN** instance A owns a durable session whose lookup snapshot is still `ACTIVE`
- **AND** instance B would force takeover because A's endpoint is missing
- **AND** A marks the row `DRAINING` with a live lease before B's claim lock
- **WHEN** B claims the same key with `allow_takeover` true
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`

#### Scenario: Missing owner endpoint does not force-steal a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **AND** the ring cannot resolve A's endpoint
- **WHEN** instance B creates a local HTTP-bridge session for the same key
- **THEN** the durable claim is issued with `allow_takeover` false
- **AND** A's owner and lease remain unchanged

#### Scenario: Expired DRAINING row remains takeover-eligible

- **GIVEN** a `DRAINING` durable session whose lease is expired or whose owner is released
- **WHEN** another instance claims the same key
- **THEN** that instance becomes the owner
- **AND** the row becomes `ACTIVE`

### Requirement: Hard continuity owner lookup fails closed

When a request depends on hard continuity ownership, the service MUST fail
closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT
continue with account selection that bypasses hard owner enforcement. A direct
WebSocket continuation already attached to its required open owner socket MUST
NOT be failed solely because a new per-turn selection attempt temporarily
excludes that owner.

#### Scenario: websocket previous-response owner lookup errors

- **WHEN** a websocket or HTTP fallback follow-up includes
  `previous_response_id`
- **AND** owner lookup errors prevent determining the required owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys

- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state,
  explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current
  replica

#### Scenario: required owner differs from the open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to an owner different from the
  currently open upstream account
- **THEN** the service retires the current upstream socket
- **AND** reconnects the unchanged anchored request to the required owner
- **AND** it does not forward any `x-codex-turn-state` associated with the
  retired account, whether supplied by the client or learned upstream

#### Scenario: required owner matches the healthy open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to the currently open owner
- **THEN** the service sends it on that socket without a new selector-based
  eligibility check

### Requirement: Request logs persist requested, actual, and billable service tiers separately
For Responses proxy traffic, the system MUST persist the operator-requested tier, the upstream-reported actual tier when available, and the effective billable tier used for pricing as separate request-log fields.

The legacy `fast` alias MUST be normalized to the canonical upstream value
`priority` before forwarding and before it is stored as the requested tier.
The upstream-reported `response.service_tier`, when present, remains the
authoritative actual tier even when it differs from the requested tier.

#### Scenario: Upstream reports a downgraded actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Fast alias is logged as a priority request
- **WHEN** a client sends a Responses request with `service_tier: "fast"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Upstream omits the actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response omits `service_tier`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = null`
- **AND** the persisted request log entry records billable `service_tier = "priority"`

### Requirement: API key service tier enforcement applies to upstream Responses requests

When an API key carries an enforced service tier, the proxy MUST override any
incoming Responses request service tier with that enforced value before route
selection. The omit-equivalent client values `auto` and `default` MUST count as
an omitted tier when tracking whether the enforced value supplied the request's
tier. The legacy alias `fast` MUST be treated as `priority`.

For a subscription-account route, when an authoritative account catalog says
the selected model never advertises the enforced tier, the proxy MUST remove
that tier from the effective request before account selection and upstream
forwarding. The resulting effective tier MUST survive internal owner
forwarding unchanged. This fallback MUST NOT remove an explicit non-default
client tier, MUST NOT alter a request routed through an external model source,
and MUST NOT apply when the account catalog has no authoritative answer for the
model.

#### Scenario: Enforced service tier overrides the request payload

- **GIVEN** the selected account model advertises the `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "priority"`

#### Scenario: Omit-equivalent request permits account-catalog fallback

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request omits `service_tier` or supplies `auto` or `default`
- **THEN** the account-routed upstream payload omits `service_tier`
- **AND** an internal owner forward preserves that effective omission

#### Scenario: Explicit non-default tier is not downgraded

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** a client explicitly requests `service_tier: "priority"` or the equivalent `fast` alias
- **THEN** API-key enforcement does not make the tier eligible for account-catalog fallback

#### Scenario: Fast alias is applied as priority

- **WHEN** an API key is configured with `enforcedServiceTier: "fast"`
- **THEN** the forwarded upstream payload uses the canonical value `priority`

### Requirement: Cursor GPT-5 model aliases normalize to canonical slugs

For Responses proxy traffic, the service MUST recognize Cursor-style GPT-5 model aliases formed by appending known suffix tokens
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs, including the GPT-5.6
personality slugs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged; `ultra` and `max` are not suffix tokens (they are not effort levels
every GPT-5-family base supports — `gpt-5.6-luna` advertises no `ultra`), so
labels such as `gpt-5.6-sol-ultra` pass through unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 personality alias normalizes reasoning and service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-extra-high-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.6-sol"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 ultra-suffixed label is not rewritten

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-ultra"`
- **THEN** the forwarded upstream request keeps `model: "gpt-5.6-sol-ultra"` unchanged

### Requirement: OpenAI-compatible Responses payload sanitation removes provider-specific thinking aliases

The shared OpenAI-compatible Responses sanitation path MUST normalize third-party thinking aliases into the canonical `reasoning` object before upstream forwarding. Unknown provider-specific thinking controls MUST NOT be passed through unchanged to the upstream ChatGPT backend.

#### Scenario: Shared payload sanitation maps enable_thinking

- **WHEN** an internal Responses payload contains `enable_thinking: true`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Explicit reasoning wins over provider aliases

- **WHEN** an internal Responses payload contains both `reasoning: {"effort":"high"}` and `thinking: {"type":"enabled"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "high"`
- **AND** the forwarded upstream payload does not include `thinking`

### Requirement: Public Responses streams expose renderable final text
For OpenAI-style streaming `/v1/responses` and `/backend-api/codex/responses`, the service MUST expose renderable `response.output_text.delta` events for assistant message text when upstream provides final text only in output item or terminal response output payloads. The service MUST NOT duplicate text deltas for an output item that already emitted a text delta.

#### Scenario: final output item text is exposed as a text delta
- **WHEN** upstream emits a `response.output_item.done` event with assistant message text and no prior text delta for that output item
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the final item event

#### Scenario: terminal response output text is exposed as a text delta
- **WHEN** upstream emits only a terminal `response.completed` event with assistant message text in `response.output`
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the terminal event

#### Scenario: existing text deltas are preserved without duplication
- **WHEN** upstream already emits a `response.output_text.delta` for an output item
- **THEN** the service forwards the stream without synthesizing another text delta for that same output item

### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

#### Scenario: Chat Completions tool arguments avoid snapshot duplication
- **WHEN** `/v1/chat/completions` maps Responses tool-call events that include incremental deltas and later finalized snapshots for the same tool call
- **THEN** the final `tool_calls[].function.arguments` value is exactly one valid JSON string for that tool call
- **AND** the adapter MUST NOT append full snapshot payloads on top of already-collected incremental argument deltas

#### Scenario: Parallel tool calls route arguments by output_index
- **WHEN** `/v1/chat/completions` maps Responses events for two or more parallel function calls
- **THEN** the adapter MUST route each event to its `tool_calls[]` slot using the event's `output_index` as the primary routing key
- **AND** the adapter MUST preserve a stable mapping from `output_index` to the same slot across `output_item.added`, `output_item.done`, `response.function_call_arguments.delta`, and `response.function_call_arguments.done` events for that call
- **AND** parallel tool calls MUST NOT collapse to index `0` when their argument-only events identify the owning call only via `item_id`

#### Scenario: Parallel tool calls also resolve through item_id aliases
- **WHEN** an `output_item.added` or `output_item.done` event exposes both `item.id` (e.g. `"fc_..."`) and `item.call_id` (e.g. `"call_..."`)
- **THEN** the adapter MUST register `item.id` as an alias to the same `tool_calls[]` slot as the `call_id`
- **AND** subsequent argument-only events that carry only `item_id` MUST resolve to that aliased slot, even if their `output_index` has not yet been observed

#### Scenario: Internal item_id never leaks into the public call identifier
- **WHEN** the adapter exposes a tool call to the client as `tool_calls[].id` or `tool_calls[].call_id`
- **THEN** the value MUST be the upstream `call_...` identifier and MUST NOT be substituted with the internal `fc_...` item id used solely for routing

### Requirement: Responses routing prefers budget-safe accounts
When serving Responses routes, the service MUST prefer eligible accounts that are still below the configured budget threshold over eligible accounts already above that threshold. If no below-threshold candidate exists, the service MAY fall back to the pressured candidates.

#### Scenario: Fresh Responses request avoids a near-exhausted account
- **WHEN** `/backend-api/codex/responses`, `/backend-api/codex/responses/compact`, `/v1/responses`, or `/v1/responses/compact` selects among multiple eligible active accounts
- **AND** one candidate is above the configured budget threshold
- **AND** another candidate remains below that threshold
- **THEN** the below-threshold candidate is chosen first

### Requirement: Upstream Responses event size budget
The service SHALL allow upstream Responses SSE events and upstream websocket message frames up to 16 MiB before treating them as oversized. The budget is a fixed application constant (`MAX_SSE_EVENT_BYTES` in `app/core/clients/proxy.py`); it MUST NOT be operator-configurable, and the serialized upstream `response.create` budget (15 MiB) MUST be derived from it so the envelope can never exceed the frame ceiling.

#### Scenario: built-in tool output exceeds the old 2 MiB limit
- **WHEN** upstream Responses traffic includes a single SSE event or websocket message frame larger than 2 MiB but not larger than 16 MiB
- **THEN** the proxy continues processing the event instead of closing the upstream websocket locally with `1009 message too big`

#### Scenario: The event budget is not an environment setting
- **WHEN** the process starts with `CODEX_LB_MAX_SSE_EVENT_BYTES` or `CODEX_LB_UPSTREAM_RESPONSE_CREATE_MAX_BYTES` set
- **THEN** the values are ignored, startup logs the removed-setting warning once, and the 16 MiB / 15 MiB budgets apply

### Requirement: Upstream Responses transport strategy
For streaming Codex/Responses proxy requests, the system MUST let operators choose the upstream transport strategy through dashboard settings, and the dashboard setting MUST be the only source of that choice: there MUST NOT be an environment variable for it, and the service MUST warn at startup when the removed `CODEX_LB_UPSTREAM_STREAM_TRANSPORT` variable is still set. The persisted strategy MUST be one of `auto`, `http`, or `websocket`; the settings API MUST reject any other value. Runtime readers MUST resolve the strategy from the dashboard settings snapshot through one shared resolver, and a snapshot still carrying the legacy `default` sentinel MUST resolve to `auto`. Upgrading MUST migrate persisted `default` rows to `auto`.

#### Scenario: Dashboard forces websocket upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"websocket"`
- **THEN** streaming Responses requests use the upstream websocket transport

#### Scenario: Dashboard forces HTTP upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"http"`
- **THEN** streaming Responses requests use the upstream HTTP/SSE transport

#### Scenario: Auto transport falls back when websocket upgrades are rejected
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** auto selection chose the websocket transport
- **AND** the upstream rejects the websocket upgrade with HTTP `426`
- **THEN** the proxy retries the request over the upstream HTTP/SSE transport

#### Scenario: Legacy default sentinel migrates to auto
- **GIVEN** a persisted settings row whose `upstream_stream_transport` is `"default"`
- **WHEN** the database is upgraded
- **THEN** the stored value is `"auto"` and `GET /api/settings` reports `"auto"`

#### Scenario: Environment variable no longer applies
- **GIVEN** the process runs with `CODEX_LB_UPSTREAM_STREAM_TRANSPORT=http` and the dashboard setting is `"websocket"`
- **WHEN** a streaming Responses request is resolved
- **THEN** the upstream websocket transport is used
- **AND** startup logged a removed-setting warning naming the variable

#### Scenario: Session affinity alone does not trigger websocket upstream transport
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** a request includes a `session_id`
- **AND** it does not include an allowlisted native Codex `originator` or explicit Codex websocket feature headers
- **THEN** the auto strategy MUST keep using the existing model-preference transport selection rules

#### Scenario: Auto transport honors websocket-preferred bootstrap models before registry warmup
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the model registry has not loaded a snapshot yet
- **AND** the request targets a locally bootstrapped websocket-preferred model family such as `gpt-5.4` or `gpt-5.4-*`
- **AND** the request does not include the built-in `image_generation` tool
- **THEN** the proxy chooses the upstream websocket transport

#### Scenario: Auto transport prefers HTTP for image-generation tool requests
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the request includes a built-in `image_generation` tool
- **THEN** the proxy chooses the upstream HTTP/SSE transport even if the model would otherwise prefer websocket

#### Scenario: Legacy settings preserve the pre-feature default
- **WHEN** transport selection runs against a legacy settings object that does not expose the newer upstream transport fields
- **THEN** the proxy MUST preserve the pre-feature HTTP transport default for model-preference auto-selection unless an explicit legacy websocket mode or native Codex websocket signal opts in

### Requirement: Responses-compatible tool payload handling
The service SHALL accept built-in Responses tool definitions on `/backend-api/codex/responses` and `/v1/responses` without locally rejecting them. The service MAY normalize documented aliases, but upstream model/tool compatibility validation MUST remain the upstream contract.

#### Scenario: full Responses request includes built-in tools
- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with built-in Responses tools such as `image_generation`, `computer_use`, `computer_use_preview`, `file_search`, or `code_interpreter`
- **THEN** the proxy forwards those tool objects upstream instead of returning a local `invalid_request_error`

### Requirement: Compact requests drop tool-only fields
The service SHALL remove `tools` and `tool_choice` from compact request payloads, and set `parallel_tool_calls` to `false`, before calling the upstream compact endpoint.

#### Scenario: compact request reuses a full Responses payload shape

- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `tools`, `tool_choice`, or `parallel_tool_calls`
- **THEN** the proxy drops `tools` and `tool_choice` before the upstream compact request
- **AND** the proxy sends `parallel_tool_calls` as `false`
- **AND** the compact request continues without a local or upstream `invalid_request_error` caused by `param="tools"`

### Requirement: Responses requests accept input_file content items with a file_id

The system SHALL accept `input_file` content items that reference an upload by `file_id` in `/backend-api/codex/responses` and `/v1/responses` request payloads (both list-form and string-form `input`). These items MUST be forwarded to upstream verbatim. The same MUST apply to `/responses/compact` request bodies. The proxy MUST NOT raise `input_file.file_id is not supported` for these items.

#### Scenario: input_file with file_id is accepted in a /responses request

- **WHEN** a client posts a `/v1/responses` request whose `input` contains a `{"type": "input_file", "file_id": "file_abc"}` content item
- **THEN** the request validates and the upstream payload includes that content item unchanged

#### Scenario: input_file with file_id is accepted in a compact request

- **WHEN** a client posts a `/responses/compact` request whose `input` contains an `input_file` item with a `file_id`
- **THEN** the request validates and is forwarded to upstream verbatim

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files` when a durable, unexpired pin for that `file_id` exists. The pin MUST be visible to every replica that shares the application database. A live file pin is hard ownership evidence: it MUST override prompt-cache or bare process-session locality and MUST agree with independently resolved turn-state, previous-response, bridge, or other hard ownership.

When multiple `file_id`s are referenced, all live pins MUST resolve to the same account. If at least one ID has a live pin and another ID has no live pin, the request MUST fail with `file_owner_unavailable`; if live pins resolve to different accounts, it MUST fail with `continuity_owner_conflict`. If none of the referenced IDs has a live pin, the proxy MUST preserve compatibility with files registered directly upstream or before durable ownership was observed by forwarding the opaque IDs verbatim under ordinary unpinned routing.

A live durable pin MUST NOT be reassigned to another account. Repeating the claim for the same account MUST be idempotent and MAY renew its expiry; an expired identifier MAY be claimed by a later upload.

Every hard file-owner decision MUST read the shared database and MUST NOT rely on a process-local owner cache. Authenticated inter-replica forwarding metadata MAY corroborate the freshly resolved durable owner but MUST NOT replace the receiver's database read. A missing or conflicting receiver-side durable owner MUST fail closed before account selection or upstream invocation. Pin expiry, reclaim, and cleanup MUST use database-authoritative statement time rather than a replica's application clock.

For a streaming Responses request whose durable file-owner lookup runs in the stream service, any API-key usage reservation acquired before that lookup MUST have exactly one cleanup owner if resolution fails or the request is cancelled. Within one replica, the API layer MUST own cleanup until the direct stream service enters its settlement-guarded `try/finally` or the local HTTP-bridge service successfully submits the request and installs its request-state finalizer. The service finalizer MUST own cleanup after that explicit boundary so those layers cannot both release the reservation. Merely completing the durable lookup MUST NOT transfer cleanup before a service finalizer is active, and an initial SSE heartbeat MUST NOT transfer ownership to the client.

When an authenticated HTTP-bridge origin forwards that reservation to another replica, the receiver MUST delay its successful HTTP 200 response until its service finalizer is active. That 200 response MUST be the cleanup-handoff acknowledgement that transfers ownership from the origin to the receiver. The origin MUST distinguish a request that has not been dispatched, a dispatch with no observed response status, a successful HTTP 200 acknowledgement, and a definitive non-200 rejection. Before dispatch or after a definitive non-200, receiver-side owner-revalidation failure or cancellation MUST propagate with cleanup remaining at the origin. After dispatch when no response status can be observed, the origin MUST NOT actively release or replay the reservation because the receiver may already own settlement; receiver settlement or bounded stale-reservation cleanup MUST resolve that ambiguity. After the acknowledgement, the receiver service finalizer MUST remain authoritative even if no upstream event has arrived. If a bounded startup probe hands pending preflight work to the response body and the body closes first, the active owner MUST cancel and await that work before scheduling one cancellation-safe release attempt. If that persistence write fails, the same cleanup owner MUST schedule one follow-up release attempt instead of abandoning the reservation. An SSE heartbeat or another frame MUST NOT transfer cleanup ownership. Compact service settlement MUST likewise suppress a second API-layer release after its single settlement attempt. Once a forwarded compact service has made that settlement attempt, including when both the primary finalize and the fallback release fail, a later receiver-side output validation failure or a `usage_settlement_failed` error MUST preserve HTTP 200 as the cleanup-handoff acknowledgement and surface a terminal `response.failed` event with the stable error code; it MUST NOT become a non-200 rejection that permits origin release or replay. A client disconnect after the initial SSE heartbeat MUST close the service stream even when the startup probe already completed. A cleanup-store failure MUST NOT replace a stable owner-resolution error. A cleanup-store failure MUST NOT mask the original stable owner error or cancellation. Owner-lookup failure or cancellation MUST NOT trigger account failover or another upstream attempt.

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a` on one replica
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` on another replica
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: file_id pin overrides prompt-cache locality

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST route to `account_a` and MUST NOT send the account-scoped file to the prompt-cache account

#### Scenario: opaque file_id without a live pin remains compatible

- **GIVEN** a request references a `file_id` registered directly upstream or before the system durably observed its upload
- **AND** no referenced file has a live durable pin
- **WHEN** the request is routed
- **THEN** the proxy MUST forward the `file_id` verbatim under ordinary unpinned routing
- **AND** it MUST NOT reject the request solely because owner metadata is absent

#### Scenario: file finalize resolves ownership across replicas

- **GIVEN** one replica registered `file_xyz` through `account_a`
- **WHEN** another replica handles `POST /backend-api/files/file_xyz/uploaded`
- **THEN** the proxy MUST finalize the file through `account_a`
- **AND** it MUST NOT fall back to a different eligible account

#### Scenario: concurrent live ownership claims do not overwrite

- **GIVEN** `file_xyz` has a live durable pin to `account_a`
- **WHEN** another replica attempts to pin `file_xyz` to `account_b`
- **THEN** the claim MUST fail with `continuity_owner_conflict`
- **AND** subsequent routing MUST still resolve `file_xyz` to `account_a`

#### Scenario: a replica observes an expired pin reclaimed by another replica

- **GIVEN** a replica previously resolved `file_xyz` to `account_a`
- **AND** the durable pin expires and another replica claims `file_xyz` for `account_b`
- **WHEN** the first replica resolves `file_xyz` again
- **THEN** it MUST read the durable owner and return `account_b`
- **AND** it MUST NOT return `account_a` from process-local state

#### Scenario: durable owner lookup failure fails closed

- **GIVEN** a request references a file whose owner decision requires the shared database
- **WHEN** the durable owner lookup fails
- **THEN** the request MUST fail before selecting or invoking an unpinned fallback account

#### Scenario: cancellation during owner lookup releases admission state

- **GIVEN** a request has acquired an API-key usage reservation before durable file-owner resolution completes
- **WHEN** the request is cancelled while the owner lookup is pending
- **THEN** exactly one cleanup owner MUST attempt to release or settle the reservation
- **AND** no account selection, upstream invocation, retry, or failover may occur

#### Scenario: delayed owner failure after stream handoff releases admission state

- **GIVEN** the streaming startup probe expires while durable file-owner resolution is still pending
- **WHEN** the lookup later fails or the response body is closed
- **THEN** the origin API MUST cancel and await any still-pending lookup
- **AND** the origin API MUST make exactly one release attempt
- **AND** a lookup failure MUST be represented by the stable `file_owner_unavailable` error

#### Scenario: failed reservation release is retried

- **GIVEN** a startup or disconnect cleanup owns an API-key reservation
- **WHEN** the first persistence release fails
- **THEN** the cleanup owner MUST schedule one follow-up release attempt
- **AND** it MUST NOT leave the reservation reserved with no later cleanup path

#### Scenario: forwarded owner metadata is revalidated against durable ownership

- **GIVEN** a replica receives authenticated forwarding metadata that identifies `account_a` as a referenced file's owner
- **WHEN** the receiver's fresh durable lookup has no live owner or identifies a different owner
- **THEN** the receiver MUST fail closed
- **AND** it MUST NOT route using the forwarded value alone
- **AND** it MUST propagate the preflight failure to the origin without releasing the origin reservation
- **AND** the originating request path MUST remain the sole cleanup owner because no successful handoff acknowledgement was sent

#### Scenario: forwarded stream acknowledges cleanup ownership before HTTP 200

- **GIVEN** the origin forwards a file-pinned streaming request and its API-key reservation to the authenticated owner replica
- **WHEN** the receiver completes durable owner revalidation and installs its service settlement finalizer
- **THEN** the receiver MAY return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** the origin MUST stop releasing the reservation after receiving that acknowledgement
- **AND** cancellation before the first upstream event MUST invoke only the receiver's service finalizer

#### Scenario: ambiguous owner dispatch defers active origin cleanup

- **GIVEN** the origin has begun dispatching a signed forwarded request carrying its reservation
- **WHEN** the transport fails before the origin can observe an HTTP status
- **THEN** the origin MUST NOT actively release or replay the reservation
- **AND** receiver settlement or stale-reservation cleanup MUST remain the only recovery paths

#### Scenario: definitive owner rejection retains origin cleanup

- **GIVEN** the origin dispatches a signed forwarded request carrying its reservation
- **WHEN** the receiver returns a non-200 response without acknowledging cleanup handoff
- **THEN** the origin MUST make exactly one cancellation-safe release attempt
- **AND** the receiver MUST NOT settle the origin reservation

#### Scenario: owner non-200 remains a rejection after body-read failure

- **GIVEN** the origin has observed a non-200 owner-forward status
- **WHEN** reading the rejection body then fails
- **THEN** the origin MUST treat the outcome as a definitive rejection
- **AND** it MUST NOT reclassify the dispatch as ambiguous

#### Scenario: compact service settlement is not released twice

- **GIVEN** terminal or direct compaction receives an API-key usage reservation
- **WHEN** the compact service makes its single settlement or release attempt
- **THEN** the API layer MUST NOT issue another release for that reservation
- **AND** a pre-service failure MUST still leave exactly one release attempt at the API layer

#### Scenario: malformed compact output after settlement preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** the settled response lacks a valid compaction output item
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement failure after fallback preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** usage settlement fails after a successful fallback release
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement attempt preserves handoff when both writes fail

- **GIVEN** a forwarded terminal compact request whose receiver service attempts settlement
- **WHEN** both reservation finalization and the fallback release fail
- **THEN** the receiver MUST still return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: completed startup probe still closes the service stream

- **GIVEN** the streaming startup probe already obtained the first service event
- **WHEN** the client disconnects after the initial SSE heartbeat
- **THEN** the origin MUST close the service stream
- **AND** reservation cleanup MUST still run if ownership has not transferred

### Requirement: Soft HTTP-bridge 1011 reconnect keeps a live file-pin owner

A still-unsubmitted HTTP-bridge reconnect MUST keep a live `input_file.file_id`
pin as a required owner after a soft session closes with `1011`.
When an HTTP-bridge session is soft (prompt-cache or request locality) and
upstream closed it with `1011`, a still-unsubmitted request that carries a
live `input_file.file_id` pin MUST keep that pin account as a required
reconnect owner. The proxy MUST NOT exclude that account solely because the
close code was `1011`, and MUST NOT fall back to another account while the
pin is live. If the required pin account is already excluded or cannot be
reconnected, the proxy MUST fail closed with the existing required-owner
unavailable error. A soft `1011` reconnect that has no live file pin and no
other required owner MAY still skip the closed account.

#### Scenario: Soft 1011 reconnect keeps the file-pin account required

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **WHEN** the proxy reconnects that session
- **THEN** account selection MUST treat `account_a` as the required owner
- **AND** it MUST NOT add `account_a` to the excluded-account set solely because of `1011`
- **AND** it MUST NOT enable preferred-account fallback to another account

#### Scenario: Soft 1011 reconnect without a file pin may skip the closed account

- **GIVEN** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the still-unsubmitted request has no live file pin and no other required owner
- **WHEN** the proxy reconnects that session
- **THEN** account selection MAY exclude `account_a` and choose another eligible account

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be selected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection cannot return `account_a`
- **WHEN** the proxy reconnects that session
- **THEN** the proxy MUST fail closed with the existing required-owner unavailable error
- **AND** it MUST NOT replace that envelope with a generic selection failure

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be connected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection returns `account_a`
- **AND** opening a replacement upstream for `account_a` fails
- **WHEN** the proxy reconnects that session on submit
- **THEN** the client-visible error MUST be the existing required-owner unavailable error
- **AND** it MUST NOT be replaced with a generic `upstream_unavailable` envelope

### Requirement: Codex backend session_id preserves account affinity

When a backend Codex Responses or compact request includes a nonblank
`thread-id`, the service MUST use a source-separated bounded key derived from
the independently parsed process session and thread identity for soft account
locality. If the thread has no mapping, selection MUST first prefer an eligible
source-separated process-session mapping and then persist the admitted thread
mapping. If no process-session mapping exists, the first admitted thread MUST
initialize that soft process preference atomically without overwriting a
concurrent or later first writer, unless its account is admitted only through
a recovery-probe reservation. A recovery-probe admission MUST NOT initialize
the immutable process preference; its reversible thread row MAY be persisted
independently until a normal admission establishes the process default.

When `thread-id` is absent, a non-empty accepted process-session header MUST
retain its established account-affinity behavior. Accepted process-session
headers are `session_id`, `session-id`, `x-codex-session-id`, and
`x-codex-conversation-id`, in that priority order. A client-supplied nonblank
`x-codex-turn-state` remains a more specific hard continuity key. If the
request lacks a client-supplied `prompt_cache_key`, the service MUST derive and
attach a stable `prompt_cache_key` before upstream forwarding so account
affinity and upstream prompt-cache routing can coexist. A client-supplied
`prompt_cache_key` MUST be forwarded unchanged and MUST NOT be used as thread
identity.

A turn state synthesized by the proxy for the current downstream WebSocket
handshake MUST NOT override client-supplied process/thread identity or a
prompt-cache key for routing or WebSocket continuity selection. The proxy MUST
seed WebSocket continuity storage under that synthesized turn state so a later
client echo can reuse the completed-turn owner. The proxy MUST continue to
forward that synthesized turn state upstream. A turn state sent by the client,
including one that the proxy generated and the client later echoed, remains a
client-supplied turn-state affinity key.

When a WebSocket handshake has neither a client-supplied turn state nor an
accepted process/thread identity, the proxy MUST store its generated turn state
as the WebSocket continuity key. A later connection that echoes that accepted
value MUST recover the same continuity state. Direct WebSocket retained
response, input-prefix, Responses Lite, and unresolved-tool state MUST use the
derived thread identity plus API-key scope, with count-bounded storage.
Request-log conversation grouping MUST continue to use raw `thread-id`.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing

- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `thread-id` or `prompt_cache_key`
- **THEN** the routing decision retains process-session `codex_session` affinity
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: backend WebSocket reconnect retains session affinity despite a generated turn state

- **WHEN** two backend Codex Responses WebSocket connections include the same process session and `thread-id` and omit `x-codex-turn-state`
- **AND** the proxy generates a distinct turn state for each handshake
- **THEN** both account selections use the same bounded thread-local affinity key
- **AND** each generated turn state is still forwarded to the upstream

#### Scenario: echoed generated turn state remains a client continuation key

- **WHEN** a client reconnects with a non-empty `x-codex-turn-state` value it received from an earlier proxy handshake
- **THEN** that turn state remains the routing and WebSocket continuity key ahead of broader process/thread locality
- **AND** full-resend continuity for that echoed turn state can reuse the earlier completed response anchor

#### Scenario: generated turn state seeds continuity without a session header

- **WHEN** a backend Codex Responses WebSocket handshake omits process/thread identity and `x-codex-turn-state`
- **AND** the proxy generates and returns a turn state for that handshake
- **THEN** the proxy stores its WebSocket continuity state under that generated value
- **AND WHEN** a later connection sends that value in `x-codex-turn-state`
- **THEN** it recovers the stored continuity state

#### Scenario: Root and child keep separate locality with one cache hint

- **GIVEN** root and child requests share a process session and explicit `prompt_cache_key`
- **AND** they carry different stable `thread-id` values
- **WHEN** backend Responses or compact routes them
- **THEN** they use different bounded internal thread keys
- **AND** both upstream payloads retain the original `prompt_cache_key`

#### Scenario: New thread inherits process preference without coupling siblings

- **GIVEN** a process-session soft row points to eligible account A
- **AND** a previously unseen thread in that process arrives
- **WHEN** selection admits the request
- **THEN** it prefers account A and persists a bounded row for that thread
- **AND** later movement of that thread does not rewrite the process row or a sibling row

#### Scenario: First thread initializes the process preference

- **GIVEN** a fresh process has no process-session or thread mapping
- **WHEN** its first thread is admitted on account A
- **THEN** it initializes the process preference to A with insert-if-absent
- **AND** a later sibling prefers A without gaining authority to rewrite that process preference

#### Scenario: Exact owner admission still initializes first-thread locality

- **GIVEN** a fresh process has no process-session or thread mapping
- **AND** an exact response, file, or bridge owner requires account A
- **WHEN** the first thread is admitted on account A through that hard owner
- **THEN** the thread row and absent process preference are persisted atomically
- **AND** the process preference remains insert-only if another thread already initialized it

#### Scenario: Recovery probe does not seed the process

- **GIVEN** a fresh process has no process-session mapping
- **WHEN** a thread is selected on probing account A through a recovery reservation
- **THEN** account A is not published as the immutable process preference
- **AND** a failed reservation commit can restore the reversible thread placement

#### Scenario: Direct WebSocket siblings do not share replay state

- **GIVEN** sibling threads share one process session and cache key
- **WHEN** each uses direct WebSocket Responses and one reconnects
- **THEN** retained response, prefix, Lite, and pending-tool state is read only from that thread
- **AND** the reconnect cannot inject or replay its sibling's state

#### Scenario: Unknown exact turn does not borrow broader thread replay

- **GIVEN** a direct WebSocket thread has retained replay or tool state
- **WHEN** a request supplies a nonblank client turn state with no exact in-memory alias
- **THEN** it does not reuse or replace the broader thread state
- **AND** only a previously resolved exact alias may refresh the thread alias

### Requirement: HTTP Responses routes preserve upstream websocket session continuity

When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the
service MUST preserve upstream Responses websocket session continuity on a
stable per-session bridge key instead of opening a brand new upstream session
for every eligible request. For backend Codex requests carrying `thread-id`,
the canonical bridge key MUST use the same derived logical thread identity used
for account locality and compact routing. Otherwise the bridge key MUST use an
explicit session/conversation header when present, then normalized
`prompt_cache_key`, deriving a stable key from the existing cache-affinity
inputs when the client omits one. While bridged, the service MUST preserve the
external HTTP/SSE contract, continue request logging with `transport = "http"`,
and keep requests from different bridge keys isolated.

An established live or durable thread bridge is hard continuity. The bridge
MUST retain request-scoped fork lanes for concurrent unanchored requests and
MUST preserve exact turn-state and previous-response aliases. A request with
`thread-id` MUST NOT fall back to the legacy canonical key derived from process
session and `prompt_cache_key`, because current Codex may share both across
siblings. It MAY recover an old bridge only through an exact hard alias.
Authenticated forwarded affinity kind/key values MUST remain verbatim and MUST
NOT be namespaced or hashed again.

#### Scenario: bridge forwards hard continuity keys to the owner replica

- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state`, an explicit legacy session header, or `thread-id`
- **AND** that request lands on a non-owner instance
- **THEN** the service MUST forward the request internally to the owner replica
- **AND** it MUST NOT return a topology-bearing `bridge_instance_mismatch` error to the client for that owner mismatch alone

#### Scenario: gateway-style prompt-cache bridge requests tolerate wrong-replica arrival

- **WHEN** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST treat the owner mismatch as a locality miss instead of a continuity failure

#### Scenario: forwarded bridge requests fail closed when owner forwarding loops

- **WHEN** a forwarded hard-continuity bridge request reaches another non-owner replica
- **THEN** the service MUST fail the request with a generic 5xx bridge-forward error
- **AND** it MUST NOT attempt another owner handoff

#### Scenario: local restart orphan is recovered by the replacement instance

- **WHEN** a single local bridge instance is replaced while durable hard-continuity ownership still references the old instance id
- **AND** the old owner has no distinct active forwarding endpoint from the current replacement instance
- **THEN** the replacement instance MUST treat the row as restart-orphaned and may claim durable ownership locally
- **AND** same-account takeover MUST preserve the latest persisted response anchor until a replacement response id is recorded
- **AND** normal client retries MUST NOT be stranded waiting for the old instance lease to expire

When request aliases resolve to different durable rows for the same account,
an explicitly requested previous-response alias MUST select its row even if
that row has since advanced to a newer response id. Without an explicitly
resolved previous-response alias, recovery MUST select the freshest row that
contains a persisted response anchor rather than using alias enumeration order.

#### Scenario: requested durable response alias survives same-account row divergence

- **GIVEN** turn-state and previous-response aliases resolve to different durable rows for the same account
- **AND** the request names the previous-response alias whose row has since advanced to a newer response id
- **WHEN** the service resolves durable continuity
- **THEN** it selects the row resolved by the requested previous-response alias
- **AND** it preserves that row's latest persisted response anchor

#### Scenario: Sequential siblings use distinct canonical bridges

- **GIVEN** root and child requests share process session and `prompt_cache_key`
- **AND** they carry different `thread-id` values
- **WHEN** the child starts after the root request completes
- **THEN** the child uses a different canonical bridge identity
- **AND** another child request reuses only the child's lane

#### Scenario: Old shared canonical lane is not a thread fallback

- **GIVEN** an old bridge exists under `(session-id, prompt_cache_key)`
- **WHEN** a request carries a new thread identity but no exact turn-state or previous-response alias
- **THEN** it does not attach to the old shared lane
- **AND** it creates or reuses its thread-canonical lane

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate in-process account pressure in addition to persisted usage. Account selection MUST account for in-flight response-create work, active streams, leased token/cost estimates, recent selection pressure, account health, and configured account-local caps. Selection and lease acquisition MUST be atomic with respect to other in-process selections, and the critical section MUST NOT perform database calls, network calls, sleeps, or other blocking I/O.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight pressure and caps
- **AND** one account does not receive all requests solely because persisted usage was stale

#### Scenario: File-pinned bridge request does not reroute under local pressure

- **GIVEN** an HTTP bridge `/v1/responses` request references an `input_file.file_id` pinned to an upstream account
- **AND** that owner account or bridge session rejects admission with local pressure before output starts
- **WHEN** the proxy handles the admission failure
- **THEN** it returns the owner account overload instead of soft-rerouting the payload to another account
- **AND** the file-scoped request is not replayed to an account that does not own the file

#### Scenario: Runtime lock excludes blocking I/O

- **WHEN** account selection holds the balancer runtime lock
- **THEN** the implementation performs only in-memory scoring and lease mutation
- **AND** database, network, sleep, or bridge queue waits happen outside that lock

### Requirement: Account leases release on all terminal paths

Every account-local lease acquired for a Responses request MUST be idempotently released or settled on success, upstream error, local startup error, bridge submit failure, startup probe conversion, non-streaming collect completion, failover, downstream disconnect, cancellation, timeout, and retry. A bounded stale-lease watchdog MUST reclaim leases that survive unexpected task cancellation or exceptions, and stale reclamation MUST emit warning/metric evidence. Leases MUST NOT be persisted to the database.

#### Scenario: Lease releases after downstream disconnect

- **WHEN** a streaming `/v1/responses` client disconnects before a terminal upstream event
- **THEN** the account stream lease is released exactly once
- **AND** later routing pressure no longer includes that stream

#### Scenario: WebSocket local account cap releases API-key reservation

- **GIVEN** a WebSocket `response.create` has reserved API-key usage
- **AND** account-local response-create lease acquisition fails with `account_response_create_cap`
- **WHEN** the proxy emits the local terminal failure
- **THEN** the API-key usage reservation is released
- **AND** the pending request is removed from websocket local state

#### Scenario: Stale watchdog recovers orphaned lease

- **WHEN** a request task exits unexpectedly after acquiring an account lease
- **AND** the lease exceeds the configured TTL
- **THEN** the watchdog releases the stale lease
- **AND** emits a low-cardinality warning/metric

#### Scenario: Active stream lease is not reclaimed before valid stream budget

- **GIVEN** a stream lease is older than the base lease TTL
- **AND** the configured Responses stream or HTTP bridge request budget has not elapsed
- **WHEN** account lease stale reclamation runs
- **THEN** the stream lease still counts against account-local stream pressure
- **AND** the proxy does not admit extra streams over the account stream cap by age alone

### Requirement: Public Responses streaming is proxy-timeout friendly

Streaming `/v1/responses` responses MUST include anti-buffering/cache headers suitable for SSE through common front-door proxies and MUST emit an early flushable SSE comment or event before long upstream startup waits can appear idle. Periodic SSE keepalive behavior MUST continue while waiting for upstream events. These heartbeat comments MUST NOT violate the public Responses event contract: OpenAI-contract events still begin with `response.created` when event parsing ignores comments.

#### Scenario: Streaming response includes anti-buffering headers

- **WHEN** a client starts streaming `POST /v1/responses`
- **THEN** the response headers include SSE content type and anti-buffering/cache directives
- **AND** the headers are present before upstream response completion

#### Scenario: Early heartbeat precedes long upstream silence

- **WHEN** upstream startup takes longer than the heartbeat interval
- **THEN** the client receives a flushable SSE heartbeat before a front-door origin idle timeout would trigger
- **AND** the first OpenAI-contract event remains `response.created` when upstream accepts the request

### Requirement: Codex WebSocket top-level previous-response errors are masked
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy MUST treat upstream `type: "error"` frames with top-level error fields as upstream error envelopes if the frame does not contain a nested `error` object. If those fields describe a `previous_response_not_found` continuity miss, the proxy MUST use the existing continuity fail-closed behavior and MUST NOT forward the raw upstream error envelope or the missing response id to the downstream Codex client. The proxy MUST surface the sanitized canonical `previous_response_not_found` code to the Codex-native client so an unmodified client recovers, while public `/v1/responses` clients receive `stream_incomplete`.

#### Scenario: ChatGPT backend emits top-level previous-response miss on Codex websocket
- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** the ChatGPT backend emits `{"type":"error","code":"previous_response_not_found","param":"previous_response_id",...}` without a nested `error` object
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Equal idle and request-budget stream deadlines preserve idle classification
When the configured upstream stream idle timeout is equal to the proxy request budget, and an already-started streaming Responses body has had no upstream activity for the full shared window, the system MUST classify the timeout as `stream_idle_timeout` even if scheduler jitter observes the deadline after it has elapsed. When the request budget is strictly shorter than the stream idle timeout, when the generic total timeout fires before an upstream response has started, when the remaining request budget for the next read is shorter than a fresh idle window, or when a generic total timeout follows recent upstream body activity, the system MUST continue to classify the timeout as `upstream_request_timeout`.

#### Scenario: Direct HTTP stream body deadline tie is classified as idle
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** the upstream HTTP response headers have been received
- **WHEN** reading the response body times out just after that shared deadline
- **THEN** the downstream failure event uses `error.code = "stream_idle_timeout"`
- **AND** the error message is `"Upstream stream idle timeout"`

#### Scenario: Pre-response total timeout remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **WHEN** the generic request total timeout fires before an upstream response has started
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Direct HTTP total timeout after recent activity remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** an upstream HTTP response body chunk was received less than a full idle window ago
- **WHEN** the generic request total timeout fires at the request-budget deadline
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Shorter request budget remains request-timeout classified
- **GIVEN** `proxy_request_budget_seconds` is strictly shorter than `stream_idle_timeout_seconds`
- **WHEN** the request budget elapses before the idle timeout
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Owner-forward receive deadline tie is classified as idle
- **GIVEN** an HTTP bridge owner-forward stream has equal idle and request-budget deadlines
- **AND** the remaining request budget for the next read is at least a full idle window
- **WHEN** receiving the next upstream chunk times out at that shared deadline
- **THEN** the owner-forward timeout uses `error_code = "stream_idle_timeout"`

#### Scenario: Owner-forward shorter remaining budget is request-timeout classified
- **GIVEN** an HTTP bridge owner-forward stream has equal configured idle and request-budget deadlines
- **AND** the remaining request budget for the next read is shorter than a fresh idle window
- **WHEN** receiving the next upstream chunk times out at the request-budget deadline
- **THEN** the owner-forward timeout uses `error_code = "upstream_request_timeout"`

### Requirement: Multiplexed websocket timeout ties preserve younger pending requests
When an upstream websocket or HTTP bridge session has multiple pending Responses turns and the oldest pending turn reaches an equal idle/request-budget deadline, the system MUST NOT fail all pending turns solely because the equal deadline is classified as `stream_idle_timeout`. It MUST fail only pending turns whose own request budget has elapsed, and it MUST keep younger pending turns queued until their own terminal event or timeout.

#### Scenario: Equal deadline on oldest pending request does not fail younger sibling
- **GIVEN** two pending websocket Responses requests share an upstream session
- **AND** the oldest request has reached an equal idle/request-budget deadline
- **AND** the younger request still has request budget remaining
- **WHEN** the upstream receive watchdog fires
- **THEN** the timeout classification is `stream_idle_timeout`
- **AND** the fail-all-pending path is not used
- **AND** only the expired oldest request is failed
- **AND** the younger request remains pending

### Requirement: HTTP bridge streams emit downstream liveness frames while pending

When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The interval is dashboard-managed: a non-NULL `dashboard_settings.sse_keepalive_interval_seconds` MUST override the environment value, `0` disables generated liveness frames, and the value MUST be read from the `SettingsCache` snapshot bound to the request rather than from the environment alone. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error. If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. If no response id is known yet, the Codex CLI route MUST emit an ignored `codex.keepalive` SSE data event because comment-only frames do not reset the CLI's EventSource idle timer. Public `/v1/responses` stream normalization MUST preserve SSE comment keepalives instead of treating them as malformed data, and MUST drop `codex.*` liveness events from the public OpenAI SDK contract surface.

Before a response id exists, a verified native Codex client on `/backend-api/codex/responses` MUST receive an event-bearing `codex.keepalive` JSON SSE frame even when payload-shape heuristics also require OpenAI-compatible response normalization, because comment-only frames do not reset the native client's parsed-event idle timer. Native identity MUST come from the existing native User-Agent or originator allowlist and MUST NOT be inferred from continuity headers. Explicit OpenAI SDK fingerprint markers, including `x-stainless-*` headers or an OpenAI User-Agent, MUST retain precedence for heartbeat framing and MUST receive comment liveness. Public `/v1/responses` and other non-native OpenAI SDK streams MUST retain comment heartbeats before `response.created`. Heartbeat selection MUST NOT disable authentication, payload validation, event normalization, fingerprint normalization, or routing policy.

#### Scenario: HTTP bridge emits response in-progress keepalive after response id is known
- **GIVEN** an HTTP bridge request has a known response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `response.in_progress` event for that response id
- **AND** the request remains pending

#### Scenario: HTTP bridge emits Codex keepalive before response id is known
- **GIVEN** an HTTP bridge request does not yet have a response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `codex.keepalive` SSE data event
- **AND** the request remains pending

#### Scenario: First HTTP bridge keepalive is delayed past startup probe
- **GIVEN** an HTTP bridge request is waiting for upstream queue events
- **AND** `sse_keepalive_interval_seconds` is shorter than the bridge startup-error probe window
- **WHEN** no upstream event arrives before the configured keepalive interval
- **THEN** the first generated keepalive is not emitted until the startup-error probe window has elapsed
- **AND** a startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response before any keepalive commits the stream

#### Scenario: HTTP bridge keepalive commits stream for later response-failed events
- **GIVEN** an HTTP bridge request emits a generated keepalive as its first downstream chunk
- **WHEN** the next upstream event is a `response.failed` with an HTTP status override
- **THEN** the `response.failed` event is forwarded on the SSE stream
- **AND** it is not raised as a startup HTTP error after bytes have already been emitted

#### Scenario: Public Responses normalizer preserves comment keepalive blocks
- **WHEN** the public `/v1/responses` stream normalizer receives an SSE comment keepalive block before a terminal event
- **THEN** it forwards the comment keepalive block unchanged
- **AND** it continues normalizing the subsequent Responses events normally

#### Scenario: Native Desktop shape receives parsed-event liveness
- **GIVEN** Codex Desktop sends `POST /backend-api/codex/responses` with a verified native User-Agent or originator
- **AND** its OpenAI-compatible payload and `Accept` header also trigger SDK-compatible event normalization
- **WHEN** no upstream event arrives before a response id is known
- **THEN** the proxy emits an event-bearing `codex.keepalive` JSON SSE frame
- **AND** it preserves any required response-event normalization

#### Scenario: Explicit SDK marker retains comment liveness
- **GIVEN** a request to `/backend-api/codex/responses` carries an `x-stainless-*` header or OpenAI User-Agent
- **WHEN** its payload also resembles a native Codex request
- **THEN** the proxy emits an SSE comment heartbeat before `response.created`
- **AND** it does not expose `codex.*` vendor events to the SDK stream

#### Scenario: Public v1 route never exposes native vendor heartbeat
- **GIVEN** a request targets public `/v1/responses`
- **WHEN** the request is pending before `response.created`
- **THEN** periodic liveness uses OpenAI-contract-safe comment frames
- **AND** the first data event remains `response.created`

#### Scenario: Dashboard keepalive interval overrides startup environment
- **GIVEN** the process environment leaves `sse_keepalive_interval_seconds` at 10 and an operator stores `0.5` through `PUT /api/settings`
- **WHEN** a Responses stream waits for its first upstream event on any replica
- **THEN** liveness frames are emitted every 0.5 seconds
- **AND** the request did not read `dashboard_settings` from the database beyond the cached snapshot

### Requirement: Codex WebSocket pre-created turns receive application heartbeats
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy SHALL emit a parseable Codex vendor heartbeat while a `response.create` request is pending but upstream has not yet emitted `response.created`. The heartbeat MUST be an application text frame so Codex clients reset stream-idle watchdogs that do not observe WebSocket protocol ping/pong frames. Once upstream assigns a response id, the proxy MUST continue using the existing `response.in_progress` heartbeat shape for that response id.

#### Scenario: Codex websocket upstream is silent before response.created
- **GIVEN** a Codex-native WebSocket `/backend-api/codex/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy emits a `codex.keepalive` text event downstream
- **AND** the request remains pending for the upstream `response.created` or terminal event

#### Scenario: OpenAI-style v1 websocket does not receive Codex vendor heartbeat
- **GIVEN** an OpenAI-style WebSocket `/v1/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy MUST NOT emit a `codex.keepalive` vendor event downstream

### Requirement: WebSocket terminal auth failures recover before visible output

When a Codex or OpenAI-compatible Responses WebSocket request receives an upstream terminal `response.failed` or `error` before downstream-visible output with `error.code = "invalid_api_key"` or `error.type = "authentication_error"`, the proxy MUST treat the failure as account-local auth state instead of immediately surfacing the terminal event. The proxy MUST preserve the existing no-replay rule after downstream-visible output or for non-replayable continuation requests.

#### Scenario: Session-ended WebSocket auth failure uses another account

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal auth failure whose message says the session ended or asks the user to log in again
- **WHEN** another eligible account can complete the request
- **THEN** the downstream WebSocket response succeeds from the other account
- **AND** the selected account is marked re-authentication-required and excluded from that replay

#### Scenario: Generic WebSocket auth failure refreshes once before failover

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal `invalid_api_key` failure
- **WHEN** the forced-refresh replay on the selected account also returns a pre-visible terminal `invalid_api_key` failure
- **THEN** the proxy excludes the selected account and tries another eligible account
- **AND** the downstream WebSocket response succeeds from the other account when it completes

#### Scenario: WebSocket auth failure after visible output is not replayed

- **GIVEN** a WebSocket response has emitted downstream-visible output
- **WHEN** upstream later returns a terminal `invalid_api_key` or `authentication_error`
- **THEN** the proxy MUST surface the terminal error without replaying the request on another account

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` or `401 token_invalidated`
response for the selected account, the proxy MUST attempt one forced token
refresh and retry the compact request on that same account. If the refreshed
retry also returns `401`, the proxy MUST classify and record the account
failure, exclude that account from the current compact request, and try another
eligible account when one is available. If the forced refresh itself confirms
a permanent credential failure, the proxy MUST mark the selected account for
re-authentication, exclude it from the current compact request, and try another
eligible account when account ownership permits. The proxy MUST NOT surface an
account-local `401` before exhausting eligible accounts, and MUST NOT move a
file-pinned or continuity-pinned compact request to another account. When no
safe replacement is available, the proxy MUST preserve the terminal auth error
and settlement behavior.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required`
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry

#### Scenario: Permanent forced-refresh failure uses another account

- **GIVEN** at least two accounts are eligible for an account-neutral compact request
- **AND** the selected account returns an upstream authentication failure
- **WHEN** its forced refresh reports a permanent revoked-credential failure
- **THEN** the selected account is marked `reauth_required` and excluded
- **AND** the compact request succeeds from another eligible account when it completes
- **AND** the selected account's authentication error is not surfaced to the client

#### Scenario: Permanent forced-refresh failure preserves an account pin

- **GIVEN** a compact request is pinned to an account by file or continuity ownership
- **AND** that account returns an upstream authentication failure
- **WHEN** its forced refresh reports a permanent credential failure
- **THEN** the account is marked `reauth_required`
- **AND** the request is not sent to another account
- **AND** the terminal authentication error is surfaced after settlement

### Requirement: Pre-visible proxy auth failures fail over after forced refresh

The proxy MUST treat repeated account-local authentication failures as
per-request account failures before any downstream-visible output is emitted.
When a proxy request on a non-compact surface retries with a refreshed token and
the refreshed retry still returns upstream `401 invalid_api_key` or
`401 token_invalidated`, the proxy MUST classify and record the selected account
failure, exclude that account from the current request, and try another eligible
account when one is available. The proxy MUST preserve the existing no-replay
rule after downstream-visible stream or websocket output has been emitted.

#### Scenario: Pre-visible streaming auth failure uses another account

- **GIVEN** at least two accounts are eligible for a streaming responses request
- **AND** the selected account returns `401 invalid_api_key` before downstream-visible output
- **WHEN** another eligible account can complete the request
- **THEN** the downstream stream succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Pre-visible token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a pre-visible proxy request
- **AND** the selected account returns `401 token_invalidated` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is marked `reauth_required`

#### Scenario: Non-stream proxy auth failure uses another account

- **GIVEN** at least two accounts are eligible for a thread-goal, Codex control,
  transcription, or file create/finalize request
- **AND** the selected account returns `401 invalid_api_key` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Websocket connect auth failure uses another account

- **GIVEN** at least two accounts are eligible for an upstream websocket connect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket
- **THEN** the websocket connect path excludes the invalidated account and tries another account

#### Scenario: HTTP bridge handshake auth failure uses another account

- **GIVEN** at least two accounts are eligible for HTTP bridge session creation or reconnect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket handshake
- **THEN** the HTTP bridge path excludes the invalidated account and tries another account

### Requirement: Codex WebSocket wrapped errors follow official client shape

When serving `/backend-api/codex/responses` or bridge-backed Responses WebSocket traffic, the service MUST classify upstream `type: "error"` frames using the same wrapped-error shape that the official Codex client accepts: a non-2xx `status` or `status_code` field indicates an upstream HTTP-style error, and the error detail MAY appear either in a nested `error` object or in top-level fields such as `code`, `message`, `param`, and `error_type`.

Top-level error normalization MUST NOT treat the event discriminator `type: "error"` as the upstream error type. If the frame provides `error_type`, the service MUST use that value as the error type for classification/rewrites. Existing continuity protection remains authoritative: frames describing `previous_response_not_found` MUST be rewritten or recovered through the established continuity path, surfacing the sanitized canonical `previous_response_not_found` code on the Codex-native route and `stream_incomplete` on public `/v1/responses`, without exposing the raw upstream error envelope or the missing response id.

#### Scenario: status_code alias is classified as upstream error status

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status_code":400,...}`
- **THEN** the service treats the HTTP-style error status as `400`
- **AND** applies the same error classification path as for `status: 400`

#### Scenario: top-level error_type is used for classification

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status":400,"error_type":"invalid_request_error","code":"previous_response_not_found",...}`
- **THEN** the normalized error detail has `type = "invalid_request_error"`
- **AND** the event discriminator `type = "error"` is not used as the upstream error type

#### Scenario: top-level previous-response miss surfaces the sanitized canonical code

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id

#### Scenario: top-level previous-response miss remains masked

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Backend Codex Responses preserve advertised image_generation tools

The service MUST accept HTTP and websocket `/backend-api/codex/responses`
request-create payloads that include top-level `tools` entries with
`type: "image_generation"`. During shared Responses validation and upstream
forwarding, the service MUST preserve those top-level `image_generation` tool
entries so Codex clients can expose and use the built-in image-generation
surface. The service MUST also preserve all other tool entries and the existing
built-in tool forwarding policy for public `/v1/*` routes.

#### Scenario: Backend Codex HTTP request preserves advertised image_generation tool

- **WHEN** a client sends `POST /backend-api/codex/responses` with
  `tools=[{"type":"image_generation"},{"type":"function","name":"x"}]`
- **THEN** the request is accepted instead of failing with
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool
- **AND** the remaining `function` tool is preserved

#### Scenario: Backend Codex websocket create preserves advertised image_generation tool

- **WHEN** a websocket `response.create` payload for
  `/backend-api/codex/responses` includes a top-level
  `{"type":"image_generation"}` tool entry
- **THEN** the backend Codex websocket request is accepted
- **AND** the forwarded upstream `response.create` payload preserves that
  `image_generation` tool entry

#### Scenario: Public v1 Responses built-in forwarding policy remains unchanged

- **WHEN** a client sends `/v1/responses` with
  `tools=[{"type":"image_generation"}]`
- **THEN** the service does not locally reject the built-in tool as an
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool

### Requirement: HTTP bridge startup waits fail with terminal local overload

When the HTTP responses bridge cannot start upstream work because its local bridge startup waits do not make progress within the configured proxy admission wait timeout, the service MUST surface a terminal local-overload error instead of leaving `/v1/responses`, `/backend-api/codex/responses`, or compact responses streams on keepalives only.

#### Scenario: HTTP bridge startup wait stalls before first upstream event

- **WHEN** a streaming Responses request enters the HTTP responses bridge
- **AND** bridge startup is blocked by local bridge admission state before any upstream `response.*` event can be emitted
- **AND** the wait exceeds the configured proxy admission wait timeout
- **THEN** the request fails with a terminal error
- **AND** the error payload identifies local proxy overload with `error.code = "proxy_overloaded"`

### Requirement: Accept duplicated /v1/ prefix under /backend-api/codex
The service MUST treat any inbound request whose path begins with `/backend-api/codex/v1/` followed by a non-empty rest as a transparent alias for the same path with the `/v1` segment removed. Some OpenAI-compatible clients append `/v1/` to whatever the operator configured as the base URL, producing paths like `/backend-api/codex/v1/models` or `/backend-api/codex/v1/responses`. The aliasing MUST be applied before routing so the canonical handler runs unchanged. The aliasing MUST NOT trigger for `/backend-api/codex/v1` or `/backend-api/codex` with no further path. The top-level OpenAI-style `/v1/<rest>` routes are unaffected.

#### Scenario: Misbehaving client requests duplicated prefix
- **WHEN** a client requests `GET /backend-api/codex/v1/models`
- **THEN** the response is identical to `GET /backend-api/codex/models`

#### Scenario: Canonical paths are unchanged
- **WHEN** a client requests `GET /backend-api/codex/models` or `GET /v1/models`
- **THEN** the request is routed to its existing handler without modification

### Requirement: Backend Responses endpoint accepts OpenAI-compatible request shapes
The `/backend-api/codex/responses` HTTP endpoint SHALL accept the OpenAI-compatible Responses request shape used by `/v1/responses`, including a plain string `input` and omitted or explicit `null` `instructions`. The endpoint MUST normalize that request into the internal Responses request model before forwarding upstream, MUST continue returning `text/event-stream` SSE Responses events, and MUST preserve Codex-specific session/cache affinity behavior for the backend route.

#### Scenario: OpenAI SDK streams through backend Responses path
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with `stream=true`, a model, and a plain string `input`
- **THEN** the proxy accepts the request without requiring `instructions`
- **AND** the response is a `text/event-stream` stream containing Responses events such as `response.output_text.delta` and `response.completed`

#### Scenario: Codex-private stream metadata is hidden from OpenAI SDK clients
- **WHEN** upstream emits a Codex-private stream event such as `codex.rate_limits` before `response.created`
- **THEN** the HTTP Responses stream omits the private event from the downstream SSE body
- **AND** OpenAI SDK clients can consume the stream without failing their Responses event ordering checks

#### Scenario: Strict function tool schemas are validated before streaming
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with a strict function tool schema that violates the supported JSON Schema subset
- **THEN** the proxy rejects the request with a deterministic 400 `invalid_function_parameters` error before opening the stream

#### Scenario: Codex-native backend Responses shape is preserved
- **WHEN** a Codex client sends `POST /backend-api/codex/responses` with `instructions`, array-shaped `input`, and Codex affinity headers
- **THEN** the proxy preserves the normalized request content and continues applying backend Codex session affinity

### Requirement: Codex WebSocket stale-anchor failures remain recoverable by a full-context retry
When serving or consuming the Codex-native `/backend-api/codex/responses` WebSocket route, upstream `previous_response_id` MUST be treated as an ephemeral optimization rather than durable conversation state. A stale-anchor continuity failure during a long-wait tool-output continuation MUST NOT hard-end the user turn before one full-context retry without `previous_response_id` has been attempted. The sanitized signal the service surfaces for a Codex-native stale-anchor failure MUST be the canonical `previous_response_not_found` error code, because that is the code an unmodified Codex client acts on to recover; the service MUST NOT substitute a proxy-specific classifier that standard clients do not recognize, and MUST NOT expose the raw upstream error envelope or the missing upstream response id.

#### Scenario: Long-running terminal wait invalidates the upstream previous response anchor
- **GIVEN** a Codex-native WebSocket session has completed a response with id `resp_old`
- **AND** the client later sends a `response.create` frame with `previous_response_id: "resp_old"` and tool-output or other delta input after a long idle period
- **WHEN** the upstream rejects `resp_old` with a stale-anchor error such as `previous_response_not_found`
- **THEN** the failure is classified as stale-anchor continuity loss
- **AND** the downstream signal uses `error.code = "previous_response_not_found"`, which an unmodified Codex client's built-in stale-anchor recovery retries once using full conversation history without `previous_response_id` before surfacing a turn-ending error
- **AND** the downstream payload does not expose the raw upstream error envelope or the missing upstream response id

#### Scenario: codex-lb sanitizes stale-anchor errors for client classification
- **WHEN** upstream emits a direct Codex-native WebSocket stale-anchor error
- **THEN** codex-lb MUST surface it with the canonical `error.code = "previous_response_not_found"` so an unmodified Codex client recognizes stale-anchor continuity loss without proxy-specific knowledge
- **AND** codex-lb MUST NOT forward the raw upstream error envelope or expose the missing upstream response id downstream
- **AND** codex-lb MUST NOT substitute a proxy-specific classifier that standard Codex clients do not act on
- **AND** the signal MUST let a compatible Codex client distinguish stale-anchor continuity loss from quota, policy, auth, and generic invalid-request failures

#### Scenario: Public /v1 responses keep generic continuity masking
- **WHEN** the stale-anchor failure is served to an OpenAI-compatible `/v1/responses` WebSocket client rather than the Codex-native route
- **THEN** the downstream event remains a retryable `stream_incomplete` continuity failure
- **AND** the downstream payload does not expose `previous_response_not_found` or the missing upstream response id

#### Scenario: Non-stale-anchor failures do not trigger full-context retry
- **WHEN** the upstream failure is quota, policy, auth, context-window, or another non-continuity error
- **THEN** the client MUST NOT convert it into a stale-anchor full-context retry
- **AND** codex-lb MUST preserve the original error class as much as safely possible

#### Scenario: ChatGPT backend omits param on an invalid previous response id
- **GIVEN** a request depends on a `previous_response_id`
- **WHEN** upstream returns `code = "invalid_request_error"` with the message `Invalid previous_response_id.`
- **AND** `param` is absent or equals `previous_response_id`
- **THEN** codex-lb MUST classify the failure as stale-anchor continuity loss
- **AND** the existing one-shot replay or sanitized canonical client signal MUST run
- **AND** the same generic error with another `param` MUST NOT trigger continuity recovery
- **AND** unrelated `invalid_request_error` messages MUST NOT trigger continuity recovery

#### Scenario: Verified HTTP full resend escapes a rejecting owner
- **GIVEN** an HTTP-bridge continuation carries a full input history that passes the existing durable full-resend and account-neutral projection checks
- **AND** the projected request has no account-scoped file references
- **WHEN** the continuity owner explicitly rejects its `previous_response_id` as not found before producing a response
- **THEN** codex-lb MUST remove the rejected anchor and replay the verified full request at most once on a fresh account-neutral bridge
- **AND** the rejecting owner account MUST be excluded from that replay
- **AND** stale session and turn-state affinity headers MUST NOT be forwarded to the replacement bridge
- **AND** the durable operation identity and settlement contract MUST remain attached to the replacement attempt
- **AND** anchor removal MUST NOT occur unless a registered durable operation id and the current durable session owner fence are available
- **AND** failure to reset the fenced operation spool MUST abort before the unanchored replacement is submitted
- **AND** delta-only or unverified requests MUST fail closed
- **AND** verified file/account-bound requests MUST NOT migrate accounts and MAY use only the same-owner replay described below
- **AND** an eventless transport failure without an explicit stale-anchor rejection MUST NOT by itself authorize cross-account replay
- **AND** no anchored or unanchored replacement MUST run when the request has already consumed an eventless replay, including delta-only and prefix-unverified requests
- **AND** an UNKNOWN recovery journal on an inactive durable owner MUST fail closed without being claimed for anchor removal or account migration
- **AND** explicit stale-anchor replacement MUST NOT depend on claiming the ambiguous-transport recovery journal
- **AND** durable full-resend safety MAY be proven either by retained prior output or by an exact stored pending-tool-call manifest match

#### Scenario: Verified owner-bound HTTP full resend drops only the rejected anchor
- **GIVEN** an HTTP-bridge continuation carries a prefix-verified, trim-safe full input history
- **AND** the retained request is not account-neutral because it contains account-bound tool or file history
- **WHEN** the continuity owner explicitly rejects its `previous_response_id` as not found before producing a response
- **THEN** codex-lb MUST remove the rejected anchor and replay the retained full request at most once on the same owner
- **AND** the replacement upstream request MUST NOT contain the rejected `previous_response_id`
- **AND** same-owner replay MUST use a unique owner-pinned internal key instead of bypassing the older hard-key retry circuit
- **AND** account-neutral replay admission MUST atomically claim the authorized original hard-key circuit generation
- **AND** local and durable retry-circuit state MUST NOT be deleted to authorize that replay
- **AND** successful completion of that verified replay MUST NOT clear the pre-existing local or durable circuit state
- **AND** successful completion MUST clear independent bridge quarantine state for both the replacement key and original hard key
- **AND** the request MUST NOT migrate to another account
- **AND** durable operation identity and settlement MUST remain attached to the replacement attempt
- **AND** a missing operation ledger, operation id, durable session id, owner epoch, or spool-reset capability MUST retain the anchor and fail closed
- **AND** delta-only or prefix-unverified requests MUST retain the existing fail-closed behavior
- **AND** an eventless transport failure without an explicit stale-anchor rejection MUST NOT by itself authorize this replay
- **AND** ordinary transport recovery without an explicit stale-anchor rejection MUST NOT bypass or clear the retry circuit
- **AND** an operation-journal recovery after an ambiguous transport failure MUST NOT remove the anchor or migrate accounts
- **AND** a request with a nonzero replay count MUST NOT dispatch another stale-anchor replacement
- **AND** a present blank or whitespace-only `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** a present non-string or null `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** blank or whitespace-only parameter presence MUST survive every upstream event-normalization layer and MUST NOT be rewritten to the canonical continuity code
- **AND** the verified replacement MUST NOT receive a clean-close or other transport-level resend after its first dispatch
- **AND** account-neutral generation claim MUST apply only to the local/durable circuit generation observed when recovery was authorized
- **AND** a circuit generation that wins the durable compare-and-set first MUST suppress the replacement before submit
- **AND** generation claim MUST use the original hard session key even when account-neutral recovery creates a new soft key
- **AND** generation claim MUST run for absent, below-threshold, expired, half-open, and active circuit states
- **AND** generation claim MUST use a monotonic field independent from failure observation time so delayed clock-skewed failures remain mergeable
- **AND** verified replacement MUST NOT enter authentication replay
- **AND** HTTP-bridge terminal normalization MUST preserve a present empty parameter in the normalized error envelope
- **AND** inactive-owner UNKNOWN journal inspection MUST apply to both account-neutral and owner-bound verified full resends
- **AND** a pre-dispatch failure after rebinding an existing durable operation MUST restore that operation's failed fence and MUST NOT delete its row
- **AND** a durable operation snapshot MUST distinguish newly inserted rows from rebound rows
- **AND** generic eventless transport retry MUST NOT convert an anchored safe-fresh request into an unanchored replay without explicit stale-anchor rejection
- **AND** rebound rollback MUST restore the prior session/account/model/parent ownership fields
- **AND** a restored rebound operation MUST retain its durable identity and require a fresh rebind before any in-memory capacity or gate retry dispatches
- **AND** successful replacement completion MUST clear the original-key quarantine only when it still matches the generation observed at recovery authorization
- **AND** explicit stale-anchor rejection after any emitted response event or downstream-visible output MUST fail closed without anchored fallback dispatch
- **AND** same-owner verified replay MUST use a unique internal key pinned to the proven owner rather than bypassing the original hard-key circuit

### Requirement: Codex WebSocket continuity source of truth is centralized
The behavior for Codex-native WebSocket previous-response continuity MUST be specified in this OpenSpec change rather than route-local or branch-local ad hoc patches. Future changes to this behavior MUST update the OpenSpec requirements before modifying code.

#### Scenario: Previous-response fix changes behavior
- **WHEN** a patch changes routing, replay, masking, retry, or failure behavior for Codex-native WebSocket `previous_response_id`
- **THEN** the patch includes an OpenSpec delta or updates the active continuity source of truth
- **AND** direct `/backend-api/codex/responses` WebSocket tests or Codex client WebSocket tests cover the changed behavior

### Requirement: Direct WebSocket previous-response misses never leak raw upstream errors
When a direct Responses WebSocket request depends on `previous_response_id`, the service MUST NOT send the raw upstream `previous_response_not_found` error envelope or the missing upstream response id to the downstream client. On the Codex-native `/backend-api/codex/responses` route the service MUST surface the sanitized canonical `error.code = "previous_response_not_found"` (raw envelope and id removed) so an unmodified Codex client recovers; on public `/v1/responses` the service MUST rewrite the failure to a retryable `stream_incomplete` continuity error. This applies to both `/v1/responses` and `/backend-api/codex/responses` WebSocket clients.

#### Scenario: Codex Desktop continue receives upstream previous-response miss before response.created
- **WHEN** a Codex-native `/backend-api/codex/responses` WebSocket `response.create` request includes `previous_response_id`
- **AND** upstream emits a top-level `type=error` payload with `code=previous_response_not_found` or `param=previous_response_id`
- **AND** no stable upstream `response.id` has been assigned yet
- **THEN** the downstream client receives either a transparent replay result or a retryable `previous_response_not_found` error that carries no raw upstream envelope
- **AND** the downstream payload does not include the raw upstream error envelope
- **AND** the downstream payload does not include the missing previous response id

#### Scenario: Codex Desktop continue has only request-log owner metadata
- **WHEN** a prior direct WebSocket turn completed and was persisted only in `request_logs`
- **AND** a later direct WebSocket follow-up references that completed response id
- **THEN** owner lookup uses request-log metadata or fails closed with a retryable error
- **AND** it does not continue on an unpinned account
- **AND** it does not expose the raw upstream error envelope or the missing previous response id

### Requirement: Failed precreated HTTP bridge replay retires stale sessions

When an HTTP bridge request is still pending before upstream `response.completed` and the upstream websocket closes or times out before the pending request can be completed, the service MUST fail the pending request terminally and retire the affected bridge session if precreated replay does not reconnect and resend successfully.

#### Scenario: Precreated replay fails after upstream disconnect

- **WHEN** an HTTP bridge request is pending before `response.completed`
- **AND** the upstream websocket closes before the request completes
- **AND** precreated replay fails to reconnect and resend the request
- **THEN** the pending request is removed from the bridge queue
- **AND** the per-session response-create gate is released
- **AND** the bridge session is closed and removed from local reuse
- **AND** the terminal error preserves the original failure code such as `stream_incomplete` or `upstream_request_timeout`

#### Scenario: Terminal logging failure does not preserve stale bridge ownership

- **WHEN** a failed pending HTTP bridge request is being logged as terminal
- **AND** request-log writing fails
- **THEN** the service still removes the stale bridge session from local reuse
- **AND** the service releases any durable bridge ownership for that stale session

#### Scenario: Concurrent waiter cannot submit on retired stale bridge

- **WHEN** an HTTP bridge request is waiting on a session response-create gate
- **AND** the upstream reader retires that same bridge session after a failed precreated replay
- **THEN** the waiting request or prewarm is rejected before it is appended to pending requests or sent upstream
- **AND** the retired bridge session remains closed and removed from local reuse
- **AND** the post-admission ownership check, pending enqueue, and upstream send are mutually exclusive with stale-session retirement

#### Scenario: Unregistered stale bridge reference cannot submit after admission

- **WHEN** an HTTP bridge request or prewarm holds a stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected after response-create gate admission and before it is appended or sent upstream
- **AND** response-create gate and admission state acquired by the rejected request is released

#### Scenario: Unregistered closed bridge reference cannot reconnect

- **WHEN** an HTTP bridge request holds a closed stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected before attempting to reconnect the stale bridge upstream

#### Scenario: Reader crash closes bridge before releasing pending gate

- **WHEN** an HTTP bridge upstream reader crashes while a pending request owns the response-create gate
- **AND** another request or prewarm is waiting on that same gate
- **THEN** the crashed bridge session is marked closed before the pending request gate is released
- **AND** the waiting request or prewarm cannot submit on the crashed bridge
- **AND** the crashed bridge session is removed from local reuse and its upstream resources are closed

#### Scenario: Prewarm cleanup does not consume visible queue slots

- **WHEN** a prewarm request is rejected or interrupted after response-create gate admission
- **AND** a visible HTTP bridge request is still counted in the session queue
- **THEN** prewarm cleanup releases its response-create gate and admission state
- **AND** the visible request queue count is preserved

### Requirement: Pre-dispatch Responses requests recover from local network transitions

When a Responses request encounters a classified local DNS or host-route failure and the transport proves that request dispatch did not occur, the proxy MUST retry on the same account with bounded backoff until the attempt succeeds or the existing request budget expires. A classified token-refresh network failure MUST receive the same bounded same-account recovery only when typed transport provenance proves the refresh POST was not dispatched. Recovery MUST NOT move account-owned continuation or file state to another account. Recovery client rotation, client construction, cleanup, and sleep MUST remain inside the original monotonic deadline, and existing keepalive behavior MUST remain active while an HTTP/SSE client waits. Post-connect send or receive failures, response/body-read failures, and serialized terminal response events with uncertain upstream delivery MUST retain the account-neutral network classification but MUST NOT be transparently replayed.

#### Scenario: HTTP stream survives a temporary DNS outage

- **WHEN** a streaming Responses request fails DNS resolution before request dispatch
- **AND** DNS resolution recovers before the request budget expires
- **THEN** the proxy retries the request on the same account
- **AND** the downstream stream receives the recovered upstream response instead of a terminal network error

#### Scenario: Native WebSocket connect survives a temporary DNS outage

- **WHEN** a native Responses WebSocket request cannot open its upstream WebSocket because of a classified local network failure
- **AND** connectivity recovers before the request budget expires
- **THEN** the proxy opens the upstream WebSocket on the same account
- **AND** does not exhaust or exclude unrelated accounts

#### Scenario: Recovery remains bounded

- **WHEN** the local network does not recover before the configured request budget expires
- **THEN** the proxy terminates the request with `error.code = "upstream_request_timeout"` and message `"Proxy request budget exhausted"`
- **AND** does not extend the deadline or replay downstream-visible output

#### Scenario: Token refresh survives a temporary DNS outage

- **WHEN** token refresh for the selected account reports a classified process-network failure
- **AND** typed transport provenance proves the refresh POST was not dispatched
- **AND** connectivity recovers within the original request deadline
- **THEN** the proxy retries refresh on the same account
- **AND** does not record the network failure against the account

#### Scenario: Token refresh response failure is not replayed

- **WHEN** token refresh reports a classified process-network failure while reading the response or body
- **AND** the proxy cannot prove the refresh POST was not dispatched
- **THEN** the failure retains the account-neutral process-network code
- **AND** the proxy does not retry the possibly consumed rotating refresh token

#### Scenario: Ambiguous compact POST failure is not replayed

- **WHEN** a compact POST reports a classified process-network failure without typed pre-dispatch provenance
- **THEN** the compact failure retains the account-neutral process-network code
- **AND** the proxy does not replay, penalize, or exclude the selected account

#### Scenario: Serialized terminal network failure is not replayed

- **WHEN** an upstream stream emits a terminal response event carrying the process-network code
- **AND** the proxy cannot prove that request dispatch did not occur
- **THEN** the terminal event is surfaced without transparent replay
- **AND** the selected account's health remains unchanged

#### Scenario: Post-connect WebSocket network failure is not replayed speculatively

- **WHEN** an upstream WebSocket send or receive reports a classified process-network failure after the connection opened
- **AND** the proxy cannot prove that `response.create` was not delivered
- **THEN** the pending request fails with the account-neutral process-network code
- **AND** the proxy does not transparently replay the request

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and
upstream-connect failure handling. If the pinned account cannot refresh or open
the upstream compact connection before any compact response is emitted, the proxy
MUST surface a stable upstream-unavailable failure for that request instead of
excluding the pinned account and replaying the compact request on another
account. This fail-closed rule applies only to file-pinned compact requests;
replayable compact/connect requests without a live file-id pin continue to use
the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure
- **GIVEN** `file_pinned` was uploaded through `account_a` and its durable pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over
- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the
  failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try
  another eligible account

#### Scenario: retained file-backed bridge replay remains owner-bound
- **GIVEN** an HTTP bridge precreated request uses a proxy-injected
  `previous_response_id` anchor
- **AND** the retained retry-safe full body references an account-scoped
  uploaded file through `input_file.file_id` or file-backed `input_image`
- **WHEN** the bridge retries after an upstream close before visible output
- **THEN** the proxy keeps the anchored request owner-bound instead of stripping
  the anchor, excluding the owner, and replaying the file reference on another
  account
- **AND** if the file owner cannot be reselected, the retry fails closed instead
  of reconnecting the bridge on a replacement account

#### Scenario: verified owner refresh failover releases the failed stream lease
- **GIVEN** a streaming request selects the previous-response owner and holds an
  account stream lease
- **AND** a locally verified full resend permits failover after that owner fails
  refresh or connect before output is emitted
- **WHEN** the proxy excludes the failed owner and selects a replacement account
- **THEN** the failed owner's stream lease is released before replacement
  selection so the owner does not retain stale local pressure

#### Scenario: post-selection owner failure remains owner-bound
- **GIVEN** a streaming bridge request selects the required previous-response owner and holds an account stream lease
- **AND** the request contains an otherwise verified full resend
- **WHEN** refresh, authentication, WebSocket connection, transport, or timeout fails before output
- **THEN** the failed owner's stream lease is released
- **AND** the request keeps the ordinary post-selection failure classification
- **AND** the proxy does not exclude the selected owner or activate cross-account full-resend recovery

### Requirement: Stale HTTP bridge previous-response aliases fail closed

The HTTP bridge MUST NOT treat a stale previous-response alias as a model
transition unless the indexed session's model is incompatible with the incoming
request. When a previous-response alias resolves to a closed or inactive session
for the same model and no durable recovery owner is available, the proxy MUST
surface the existing continuity-lost failure instead of creating or selecting a
replacement bridge.

#### Scenario: stale same-model previous-response alias fails closed

- **GIVEN** the previous-response index still points to an inactive HTTP bridge
  session for the same model
- **AND** no durable owner lookup is available for that response id
- **WHEN** a request arrives with that `previous_response_id`
- **THEN** the proxy fails closed with the stream-incomplete continuity error
- **AND** it does not create a replacement bridge for the stale response id

### Requirement: Cross-account bridge retries clear turn-state

When an HTTP bridge request is replayed or reconnected on an account other than the one that served its retired socket -- whether that account was excluded before the reconnect (a pre-visible request proven safe to replay on another account) or the reconnect selected a different account without an exclusion (an accepted replay left unexcluded for a hard-capable Codex session owner whose soft namespaced row moved because the owner was unselectable) -- the proxy MUST NOT carry a turn state learned on the retired account into the replacement handshake. The replacement connection's `x-codex-turn-state` header, if any, MUST NOT be one learned from the retired account, and the proxy MUST clear the retired account's upstream and downstream turn-state from the session. The handshake decides this from the account selection actually returned, not from whether the retired account was excluded. A reconnect that returns to the same account keeps offering that account its own retained turn state.

#### Scenario: safe bridge replay excludes the stalled account

- **GIVEN** a pre-visible HTTP bridge request is proven safe to replay
- **WHEN** the failed bridge account is excluded before reconnect
- **THEN** the proxy clears the retired account's turn-state fields and header
- **AND** the replacement account receives no turn-state from the retired socket

#### Scenario: Unexcluded accepted replay moved by a soft row opens the replacement without the owner's turn state

- **GIVEN** a native Codex bridge session on a hard `session_header` key whose accepted output-free replay left its owner unexcluded
- **AND** the owner's socket issued an upstream turn state on its handshake
- **AND** no raw legacy hard row exists for the bare session header, so the owner is only the soft-row preference and is unselectable at reconnect time
- **WHEN** the reconnect selection returns the other account
- **THEN** the replacement handshake carries no `x-codex-turn-state`
- **AND** the session retains no turn state learned on the owner
- **AND** the request is re-sent once to the replacement account within the single lifecycle the client is reading

#### Scenario: Same-account reconnect keeps the retained turn state

- **GIVEN** a bridge session that retained the upstream turn state issued on its current account's socket
- **WHEN** the reconnect selection returns that same account
- **THEN** the replacement handshake carries that retained turn state, unchanged from established reconnect behaviour

### Requirement: Pre-visible unary refresh/connect failures fail over

For unary proxy requests that have not emitted downstream-visible output, the proxy MUST treat retryable token-refresh or upstream-connect transport failures as account-local transient failures.

This applies to Codex thread-goal requests, Codex control requests,
transcription requests, and file create/finalize requests. When another
eligible account is available within the request budget, the proxy MUST record
the failed account, exclude it from the current request, and retry the unary
operation on the fallback account. The proxy MUST NOT fail over strict
account-owner requests whose upstream resource is bound to the selected account.

#### Scenario: Unary refresh transport failure uses another account

- **GIVEN** at least two accounts are eligible for a Codex thread-goal, Codex
  control, transcription, or file-create request
- **AND** the selected account fails during token refresh or upstream connect
  with a retryable transient transport error before downstream-visible output
- **WHEN** another eligible account can complete the request within the request
  budget
- **THEN** the downstream request succeeds from the fallback account
- **AND** the failed account is recorded and excluded from further attempts for
  that request

#### Scenario: Strict file-owner refresh failure fails closed

- **GIVEN** a file-finalize request is pinned to the account that owns the file
- **AND** the pinned account fails during token refresh or upstream connect with
  a retryable transient transport error before downstream-visible output
- **WHEN** another account would otherwise be eligible for proxy traffic
- **THEN** the proxy fails the request with an upstream-unavailable error
- **AND** the proxy does not send the file-finalize operation through another
  account

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw
(non-bridge) Responses stream path. This bypass MUST happen after rejecting
unsupported uploaded-image references and MUST be limited to the current
request; subsequent text-only requests MAY continue using the HTTP responses
bridge.

The raw (non-bridge) path is the source of truth for image validation and
upstream image error semantics. The bridge MUST NOT hold image requests waiting
for `response.created` when upstream rejects an invalid inline image payload.

This bridge bypass MUST NOT by itself pin the upstream stream transport. The
upstream transport for a bypassed image request MUST be resolved by the ordinary
upstream-transport precedence.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **THEN** the request is sent through the raw (non-bridge) stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge

#### Scenario: Image bypass does not pin the upstream transport

- **GIVEN** the HTTP responses bridge is enabled
- **AND** `upstream_stream_transport` is `"auto"`
- **WHEN** a Responses request carrying an inline `data:` image below the
  WebSocket frame budget bypasses the bridge
- **THEN** the request MUST NOT be forced onto upstream HTTP
- **AND** the configured transport policy MUST decide its upstream transport

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

#### Scenario: Pinned requests are not moved to another account

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **THEN** codex-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure.

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** codex-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. A permitted
file-free retry MUST select the replacement with cleared request and session
affinity, but MUST validate any raw legacy owner before changing the live
session or its durable owner generation. On success it MUST make exactly one
durable replacement claim before swapping the session, then clear or replace
the session affinity and local turn-state aliases. A legacy-owner conflict MUST
leave the original session open and unchanged. File-pinned requests MUST NOT
migrate.

A pre-created reconnect for a hard session-header bridge MUST remain bound to its established account, and a replacement connection MUST NOT inherit a stale turn-state header when it has no replacement turn state. If a permitted security retry swaps durable ownership but cannot submit `response.create` on the replacement socket, the replacement bridge MUST be retired rather than left reusable with partially rebound aliases. Once a reasoning prelude is buffered, all following reasoning-prelude events MUST remain buffered until the terminal security decision. For a direct WebSocket security retry, the service MUST reacquire the per-session create gate and shared/account create admission before queuing or sending the replay on the authorized replacement socket.

#### Scenario: Created HTTP bridge response is not replayed
- **WHEN** an HTTP bridge request has emitted `response.created` before a
  security-work authorization denial
- **THEN** the service does not reconnect or resend the request on another
  account
- **AND** it forwards the original terminal error

#### Scenario: Deferred reasoning blocks replay
- **WHEN** an HTTP bridge request buffers a reasoning prelude before a
  security-work authorization denial
- **THEN** that prelude blocks account-switch replay and is not emitted before
  the terminal security decision

#### Scenario: Legacy owner conflict fails before replacement mutation
- **GIVEN** a session-header security retry selects an authorized replacement account
- **AND** the raw legacy affinity row belongs to a different account
- **WHEN** the service validates the replacement
- **THEN** it does not claim the durable session for the replacement
- **AND** it leaves the original account, upstream, owner generation, aliases, and open session unchanged

#### Scenario: Hard session reconnect preserves one owner
- **GIVEN** a pre-created HTTP bridge request uses a hard session-header key
- **WHEN** its upstream socket closes before visible output
- **THEN** the reconnect requires the established account
- **AND** the session turn-state and owner affinity are not cleared for migration

#### Scenario: Failed replacement resend retires the bridge
- **GIVEN** a permitted security retry has swapped the bridge to an authorized account
- **WHEN** submitting `response.create` on that replacement socket fails
- **THEN** the replacement bridge is marked for retirement
- **AND** it is not left reusable with partially rebound continuity aliases

#### Scenario: Direct WebSocket security replay reacquires admission
- **GIVEN** a direct WebSocket security denial releases the first attempt's create admission
- **WHEN** the request is replayed on an authorized replacement account
- **THEN** the service reacquires create admission before queuing the replay
- **AND** the replay remains subject to the normal startup timeout and serialization gate

### Requirement: Responses request compatibility controls

The system SHALL accept OpenAI-compatible Responses request controls that clients may send for `/v1/responses` and `/backend-api/codex/responses` when those controls can be safely normalized before the ChatGPT-backed upstream request. Specifically, `truncation` values `"auto"` and `"disabled"` MUST pass request validation and MUST be omitted from the upstream payload because the current ChatGPT-backed path does not consume the field. Unsupported `truncation` values MUST still be rejected with HTTP 400.

#### Scenario: Truncation auto is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "auto"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

#### Scenario: Truncation disabled is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "disabled"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

### Requirement: HTTP bridge stale-session cleanup is bounded

The HTTP responses bridge MUST NOT hold the global bridge session registry lock
while awaiting operations that can block on a stale session's upstream websocket,
per-session pending lock, durable session repository, account lease release, or
other external cleanup work.

When stale bridge sessions are discovered during `/v1/responses`,
`/backend-api/codex/responses`, `/v1/responses/compact`, or
`/backend-api/codex/responses/compact` startup, the registry lock MAY be used to
remove closed or idle sessions from in-memory indexes, but potentially blocking
session close/fail-pending work MUST run after the lock is released or under a
bounded cleanup path. A wedged stale session MUST NOT prevent unrelated soft
HTTP Responses work from creating or reusing another bridge session.

Idle pruning MUST make pending-request decisions only while holding the
session's pending-request lock. If that lock cannot be acquired immediately,
the service MUST skip pruning that session instead of inferring that it is idle
from unlocked pending-request state.

If cleanup cannot complete within the bounded cleanup path, the service MUST log
a low-cardinality local bridge cleanup warning and continue protecting registry
progress. Requests that cannot safely proceed because a hard-continuity session
is unavailable MUST fail closed with an explicit local overload or continuity
error rather than silently hanging.

When a replacement bridge session claims the same durable key after stale local
session detachment, the durable owner generation MUST advance so that a late
cleanup from the stale local session cannot release or close the replacement
session's durable ownership. This MUST also apply when the detached local
session is retiring but still has visible in-flight requests and will release
its durable ownership later after draining. After a detached retiring session
finishes draining its visible requests, it MUST release its durable ownership
and account lease instead of only closing the upstream websocket.
If that retirement is initiated by the upstream-reader task after processing
the terminal upstream event, session close MUST NOT cancel or await the current
upstream-reader task itself.

When bridge capacity eviction removes an idle local session to admit a
replacement session, the evicted session's close MUST be awaited through a
bounded path before the replacement selects an account, so the evicted
session's account lease cannot cause a spurious no-account or local-capacity
failure.

If a request is cancelled while awaiting that pre-creation eviction close after
registering replacement session creation as in-flight, the service MUST fail or
remove the in-flight creation marker before propagating cancellation. Later
requests MUST NOT wait on an orphaned creation future that can never complete.

#### Scenario: wedged stale pending lock does not block fresh soft request

- **GIVEN** the HTTP responses bridge has an idle or stale local session whose
  pending-request lock does not complete promptly
- **WHEN** a new soft-affinity `/v1/responses` request starts bridge session
  selection
- **THEN** the global bridge registry lock is not held indefinitely by stale
  cleanup
- **AND** the stale session is not pruned based on unlocked pending-request
  state
- **AND** the new request either creates/reuses an eligible bridge session or
  returns an explicit bounded local error
- **AND** it does not hang before account selection or bridge create/reuse
  logging

#### Scenario: stale close runs outside registry lock

- **GIVEN** bridge startup identifies an idle stale session that must be closed
- **WHEN** closing that session awaits upstream-reader cancellation, websocket
  close, durable release, or account lease release
- **THEN** the global bridge registry lock is already released
- **AND** unrelated bridge startup requests can continue to inspect or mutate
  the registry

#### Scenario: stale durable release cannot fence out replacement owner

- **GIVEN** a stale or retiring bridge session for a durable key is replaced by
  a new local session after local detachment
- **WHEN** the stale session's bounded background close releases durable
  ownership after the replacement has claimed the same durable key
- **THEN** the stale release does not clear the replacement owner's durable
  lease
- **AND** follow-up requests for the replacement session do not receive a
  spurious bridge owner mismatch caused by the stale close

#### Scenario: detached retiring session releases resources after drain

- **GIVEN** a retiring bridge session was detached while visible requests were
  still draining
- **WHEN** those visible requests drain and the session is retired
- **THEN** the service releases the old session's durable ownership
- **AND** the service releases the old session's account lease
- **AND** upstream-reader-owned retirement does not self-cancel the current
  upstream reader task
- **AND** the detached session no longer holds bridge capacity until process
  exit

#### Scenario: LRU eviction releases lease before replacement account selection

- **GIVEN** the bridge is at local session capacity and an idle session is
  selected for LRU eviction
- **WHEN** a replacement bridge session is created after that eviction
- **THEN** the evicted session is closed through a bounded path before the
  replacement selects an account
- **AND** the evicted session's account lease does not cause the replacement to
  fail with a spurious no-account or local-capacity error

#### Scenario: cancellation during LRU close clears in-flight creation

- **GIVEN** the bridge is at local session capacity and an idle session is
  detached for LRU eviction before replacement creation
- **WHEN** the replacement request is cancelled while the bounded eviction close
  is still awaiting cleanup
- **THEN** the replacement in-flight creation marker is removed or failed before
  cancellation is propagated
- **AND** later requests for the same bridge key do not wait on that abandoned
  creation marker

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level `input` array contains exactly one `{"type":"compaction_trigger"}` item as its final element, the proxy SHALL remove that trigger before calling upstream compaction handling and SHALL emit a raw SSE stream that contains exactly one compaction output item. The internal compact request built for that flow MUST contain exactly one terminal `compaction_trigger` item on the compact wire, and the proxy MUST reject duplicate or non-terminal top-level `compaction_trigger` placement locally with HTTP 400 `invalid_request_error` before any upstream compact handling.

The stream MUST emit `response.created`, `response.output_item.added`, `response.output_item.done`, and `response.completed` in that order with monotonically increasing sequence numbers. The added event MUST expose the selected compaction item as in progress. The done event and terminal completed response MUST carry the same terminal `compaction` item. When the selected encrypted upstream compaction item carries a valid `cmp_` ID or status, the synthetic stream MUST preserve those values with its `encrypted_content`; it MUST NOT generate or rewrite a replacement item ID. A malformed, empty, or non-`cmp_` ID MUST be omitted while the opaque encrypted content remains unchanged.

Codex compact flows SHALL send the upstream compact request to `POST /backend-api/codex/responses` with `stream=true` and `store=false`, accept the upstream SSE response, and reconstruct one normalized compact response item from the terminal response lifecycle; they MUST NOT require the legacy `/backend-api/codex/responses/compact` upstream route to be available.

For Codex-affinity standalone compact requests, `POST /backend-api/codex/responses/compact` SHALL remain available as a compatibility endpoint with its subscription-backed compact routing contract, and SHALL normalize an upstream remote-compaction-v2 response that includes historical message output plus a compaction summary into the single compact output item required by Codex clients. A valid upstream `cmp_` compaction item `id` and any non-empty `status` MUST be preserved in that normalized output item. An empty, non-string, or non-`cmp_` ID MUST be omitted rather than rewritten; encrypted content MUST remain unchanged.

OpenAI-style `/v1/responses/compact` is otherwise unchanged by this requirement; when it receives duplicate top-level `compaction_trigger` items, codex-lb preserves the existing compatibility behavior and the forwarded compact input contains one terminal trigger.

#### Scenario: terminal trigger emits a complete compact lifecycle

- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one top-level `compaction_trigger`
- **THEN** the proxy strips the trigger and invokes compact handling
- **AND** it emits created, added, done, and completed events in that order
- **AND** their sequence numbers increase monotonically from zero
- **AND** the done event and completed response contain the same single terminal compaction item

#### Scenario: terminal trigger becomes one compact-wire trigger

- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one
  top-level `compaction_trigger`
- **THEN** the proxy strips that trigger before compact-input preparation
- **AND** the internal compact request contains exactly one terminal
  `compaction_trigger` item on its `input` array

#### Scenario: encrypted compaction item identity survives trigger streaming

- **WHEN** compaction handling for a terminal trigger returns encrypted content with a non-empty upstream `cmp_*` ID and terminal status
- **THEN** the added event exposes that ID with in-progress status
- **AND** the done event and completed response preserve the exact upstream ID, terminal status, and encrypted content
- **AND** the proxy does not synthesize a replacement item ID

#### Scenario: malformed trigger placement is rejected

- **WHEN** a `POST /backend-api/codex/responses` or
  `POST /backend-api/codex/responses/compact` request contains duplicate or
  non-terminal top-level `compaction_trigger` items
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compact handling

#### Scenario: Codex compact transport uses the Responses stream

- **WHEN** a valid terminal compaction trigger is submitted through a Codex
  compact flow
- **THEN** the proxy sends the compact request to
  `POST /backend-api/codex/responses` with `stream=true` and `store=false`
- **AND** it accepts the upstream SSE response and reconstructs one normalized
  compact response item from the terminal response lifecycle
- **AND** it does not require the legacy `/backend-api/codex/responses/compact`
  upstream route to be available

#### Scenario: Legacy message-shaped compact output does not get a rewritten item ID

- **WHEN** the upstream compact response exposes the encrypted compact payload
  as a legacy `message` item with a non-empty ID that does not begin with `cmp_`
- **THEN** the proxy converts that item to `type="compaction"` and omits the
  malformed ID
- **AND** the proxy preserves the encrypted content unchanged
- **AND** an existing ID that begins with `cmp_` is preserved byte-for-byte
- **AND** the proxy does not synthesize a `cmp_msg_...` ID
- **AND** ordinary message items outside the compact-output conversion remain
  unchanged

#### Scenario: Standalone Codex compact remains a compatibility endpoint

- **WHEN** a client calls `POST /backend-api/codex/responses/compact`
- **THEN** codex-lb preserves the endpoint and its subscription-backed compact
  routing contract
- **AND** malformed duplicate or non-terminal top-level triggers are rejected
  locally before any upstream compact attempt

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output

- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request receives upstream output that contains historical message items and one compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that compaction summary
- **AND** the normalized item preserves the compaction summary's valid `cmp_`-prefixed upstream ID and status
- **AND** it does not expose historical message items as standalone compact output

#### Scenario: OpenAI-compatible compact normalizes duplicate triggers

- **WHEN** a client calls `POST /v1/responses/compact` with duplicate
  top-level `compaction_trigger` items
- **THEN** codex-lb preserves the existing compatibility behavior and returns
  HTTP 200 when the compact operation succeeds
- **AND** the forwarded compact input contains one terminal trigger

### Requirement: Request logs expose upstream Responses transport
For streaming Responses proxy requests, persisted request logs MUST distinguish the downstream client transport from the upstream egress transport by recording the upstream transport in `request_logs.upstream_transport` while preserving `request_logs.transport` as the downstream client transport.

#### Scenario: downstream HTTP single-shot records upstream HTTP
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing chooses upstream HTTP for a single-shot Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"http"`

#### Scenario: downstream HTTP sticky records preserved auto upstream mode
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"auto"`

#### Scenario: historical or unrelated rows tolerate missing upstream transport
- **GIVEN** a request log row predates upstream transport persistence or belongs to a request kind that does not know its upstream transport
- **WHEN** the row is read
- **THEN** `upstream_transport` MAY be null
- **AND** the existing request-log response MUST remain valid

### Requirement: Request Logs API returns upstream transport
The Request Logs API MUST include `upstream_transport` on each request log entry so operators and dashboards can query upstream egress transport without overloading the existing downstream `transport` field.

#### Scenario: request logs response includes upstream transport
- **GIVEN** a persisted request log has `transport = "http"` and `upstream_transport = "auto"`
- **WHEN** a dashboard client fetches request logs
- **THEN** the returned entry includes `transport: "http"`
- **AND** the returned entry includes `upstream_transport: "auto"`

### Requirement: Upstream transport decisions emit low-cardinality metrics
Streaming Responses proxy requests MUST emit a low-cardinality Prometheus counter for upstream transport decisions. The metric MUST NOT include request id, account id, API key id, model, prompt cache key, or other high-cardinality identifiers.

#### Scenario: transport decision counter labels are bounded
- **WHEN** a streaming Responses request completes or terminates with an error
- **THEN** `codex_lb_upstream_transport_decisions_total` is incremented once
- **AND** its labels include only `downstream_transport`, `upstream_transport`, `policy`, `sticky`, and `status`
- **AND** `status` is `"success"` or `"error"`

### Requirement: Raw Responses streams require a terminal SSE event for success

For raw HTTP streaming Responses attempts, the proxy MUST NOT record request-log
status `success` or mark the selected account successful unless the stream
observed a terminal SSE event: `response.completed`, `response.failed`,
`response.incomplete`, or `error`. This requirement applies even when the
upstream HTTP response status was 200 because the stream body remains part of
the request outcome.

If the upstream iterator ends before a terminal event, the proxy MUST surface a
terminal `response.failed` SSE event with error code `stream_incomplete`, record
the request-log row as an upstream `stream_incomplete` error, and apply the
normal transient upstream account-health signal. If the downstream client
cancels or disconnects before a terminal event, the proxy MUST record the
request-log row with status `cancelled`, downstream error code
`client_disconnected`, and downstream failure metadata, and MUST NOT penalize
the upstream account.

#### Scenario: Raw stream upstream EOF is not successful

- **GIVEN** a raw HTTP streaming Responses request has emitted non-terminal SSE
  data
- **WHEN** the upstream stream ends before `response.completed`,
  `response.failed`, `response.incomplete`, or `error`
- **THEN** the downstream stream receives a terminal `response.failed` event
  with error code `stream_incomplete`
- **AND** the request log stores status `error`, error code
  `stream_incomplete`, and upstream failure metadata
- **AND** the selected account receives a transient upstream failure signal

#### Scenario: Raw stream downstream cancellation is client-side

- **GIVEN** a raw HTTP streaming Responses request has not observed a terminal
  SSE event
- **WHEN** the downstream client cancels or disconnects from the stream
- **THEN** the request log stores status `cancelled`, error code
  `client_disconnected`, and downstream failure metadata
- **AND** the selected account is not penalized for the client-side close

### Requirement: Responses SSE parsing uses only CR/LF line boundaries

When parsing streamed Responses Server-Sent Events, the service MUST treat only
CR (`\r`), LF (`\n`), and CRLF (`\r\n`) as SSE line boundaries. The parser MUST
NOT split a `data:` field on other Unicode line-boundary characters such as
U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR when those characters appear
inside the payload value. Multi-line `data:` fields delimited by CR, LF, or CRLF
MUST continue to be joined with `\n` before JSON decoding.

The streaming HTTP receive path MUST also treat CR-only blank lines (`\r\r`) as
complete SSE event separators, and any normalization of legacy event aliases
MUST preserve the event block's original CR, LF, or CRLF terminator style.

#### Scenario: Unicode separators inside JSON strings are preserved

- **WHEN** an upstream Responses SSE event contains a `data:` JSON payload whose
  string value includes unescaped U+2028 or U+2029
- **THEN** the parser preserves those characters inside the JSON string
- **AND** the event remains available to downstream response-event processing

#### Scenario: CR/LF-delimited multi-line data still joins

- **WHEN** an upstream Responses SSE event contains multiple `data:` lines
  delimited by CR, LF, or CRLF
- **THEN** the parser joins the field values with `\n`
- **AND** continues JSON decoding against the joined payload

#### Scenario: CR-only event separators dispatch complete events

- **WHEN** the HTTP streaming receive path receives an upstream SSE event ending
  in a CR-only blank line
- **THEN** it dispatches that event without waiting for EOF or an LF delimiter
- **AND** legacy event alias normalization preserves the CR-only blank-line
  terminator

### Requirement: Timed-out startup probes MUST settle first-item task exceptions

The proxy MUST retrieve eventual first-item task exceptions when a Responses or
chat-completions startup error probe times out while its first-item task is
still running and the returned stream is abandoned before iteration resumes.
This MUST prevent unhandled asyncio task diagnostics such as `Task exception was
never retrieved` or shielded-future exception logs for upstream
`ProxyResponseError` failures that arrive after the probe timeout.

If the returned stream is consumed later, the task result or exception MUST
remain observable through normal stream iteration.

#### Scenario: Abandoned timed-out probe consumes first-item exception

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **AND** the first-item task later raises `ProxyResponseError`
- **WHEN** the request path abandons the returned stream before consuming that task
- **THEN** the event loop does not emit an unhandled task-exception diagnostic
- **AND** task ownership is settled without changing the client-visible result

#### Scenario: Consumed timed-out probe preserves stream behavior

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **WHEN** the caller later iterates the returned stream
- **THEN** the first task's result or exception is still yielded or raised through the returned stream

### Requirement: Codex installation metadata is account-owned

For Codex response-create upstream requests, the service MUST attach a
server-owned per-account Codex installation id to upstream client metadata when
an account is selected. Inbound client-supplied Codex installation id headers or
metadata MUST NOT be trusted as the account installation id. Existing unrelated
client metadata such as turn metadata MUST be preserved.

#### Scenario: Inbound installation id is replaced

- **GIVEN** an account has a stored Codex installation id
- **AND** a client sends response-create metadata with a different
  `x-codex-installation-id`
- **WHEN** the request is forwarded upstream
- **THEN** the upstream metadata contains the account's stored installation id
- **AND** preserves unrelated metadata entries

#### Scenario: Inbound installation id header is stripped

- **WHEN** a client sends `X-Codex-Installation-Id`
- **THEN** the upstream request does not forward that header as a trusted
  client-supplied identity

### Requirement: Compact payloads omit unsupported client metadata

Compact request payload normalization MUST remove `client_metadata` before
forwarding compact requests upstream.

#### Scenario: Compact strips client metadata

- **WHEN** a compact payload includes `client_metadata`
- **THEN** the upstream compact payload omits it

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream. In this mode, no
`response.failed` synthesis is allowed before `yield` for those upstream frames.

#### Scenario: Raw backend error passthrough

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling sets `enforce_openai_sdk_contract=False`
- **WHEN** the proxy forwards that upstream event in the public stream
- **THEN** the downstream event MUST remain an `error` event
- **AND** `sequence_number`, `error_type`, and message fields from upstream must remain unchanged
- **AND** the event SHOULD NOT be rewritten into `response.failed` in the same stream step

### Requirement: Keep default contract shaping enabled unless explicitly disabled

The proxy MUST keep default contract shaping enabled unless explicitly
disabled. For backward-compatible behavior, when
`enforce_openai_sdk_contract` is omitted or `True`, current error-shaping
behavior MUST remain in place and convert error-type SSE frames as defined by
existing `responses-api-compat` contracts.

#### Scenario: Default public contract still emits response.failed

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling omits `enforce_openai_sdk_contract` or sets it to `True`
- **WHEN** the proxy forwards that upstream event
- **THEN** the downstream event MUST be normalized to `response.failed`

### Requirement: Retry-safe stale WebSocket anchors replay before owner fail-closed handling
When a direct Responses WebSocket request has a prepared retry-safe fresh upstream request body without `previous_response_id`, the service MUST use that replay path for upstream `previous_response_not_found` before applying preferred-owner unavailable handling. This applies when the stale anchor was proxy-injected from session continuity as well as when a client full-resend was classified retry-safe.

#### Scenario: proxy-injected stale anchor has a preferred owner
- **GIVEN** a WebSocket request has `previous_response_id`, a preferred owner account, and `fresh_upstream_request_is_retry_safe` with a no-anchor replay body
- **WHEN** upstream emits `previous_response_not_found` before `response.created`
- **THEN** the service reconnects and replays the prepared no-anchor request
- **AND** it does not rewrite the turn to `previous_response_owner_unavailable`

### Requirement: Codex WebSocket prewarm completions are classified separately
For a direct Responses WebSocket, the service MUST treat Codex turn metadata received on the HTTP handshake as connection-scoped metadata rather than applying its `request_kind` to every `response.create` frame. The service MUST classify an individual turn as `prewarm` when the connection metadata is `prewarm` and either that turn carries `generate: false` or its completed usage reports zero output tokens. Other turns on the same connection MUST be classified as `normal`.

Request logs for direct Responses WebSocket turns MUST persist the connection-scoped value separately as `connection_request_kind`. Empty-output prewarm completions MUST NOT update account success state or previous-response ownership, while still allowing the upstream terminal frame to pass through.

#### Scenario: generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a later `response.create` does not carry `generate: false` and upstream completes it with non-zero output tokens
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the completion remains eligible to update account success state and previous-response ownership

#### Scenario: empty prewarm completion does not look like user turn progress
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** a `response.create` carries `generate: false` or upstream completes it with zero output tokens
- **THEN** the request log records `request_kind` as `prewarm`
- **AND** the request log records `connection_request_kind` as `prewarm`
- **AND** the service does not mark the account successful for that completion
- **AND** the service does not remember the response id as a usable previous-response owner

#### Scenario: failed generated turn on a prewarm-opened connection is normal
- **GIVEN** a direct Responses WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **AND** a later `response.create` does not carry `generate: false`
- **WHEN** that turn fails before completed usage is available
- **THEN** the request log records `request_kind` as `normal`
- **AND** the request log records `connection_request_kind` as `prewarm`

### Requirement: Codex compact requests are bounded by the proxy request budget
When `/backend-api/codex/responses/compact` is called for Codex auto-compaction, the service MUST bound the upstream compact call by the remaining proxy compact request budget. That budget (the dashboard `compact_request_budget_seconds`) is the only total cap on the upstream compact call; there is no separate upstream compact timeout setting. The service MUST preserve Codex turn metadata `request_kind` in compact request logs so auto-compaction failures are distinguishable from normal user turns.

#### Scenario: auto-compaction cannot hang past the proxy budget
- **GIVEN** a Codex compact request carries `x-codex-turn-metadata` with `request_kind: "compaction"`
- **WHEN** the service calls upstream
- **THEN** the upstream call receives both connect and total timeout overrides from the remaining compact request budget
- **AND** no other total timeout is applied to the upstream compact call
- **AND** the request log records `request_kind` as `compaction`

### Requirement: Responses Lite signaling is derived from the normalized body

The service MUST accept Responses and compact requests that include
`X-OpenAI-Internal-Codex-Responses-Lite`, but MUST remove that inbound header
case-insensitively before generic upstream-header forwarding. The service MUST
NOT strip unrelated OpenAI SDK telemetry headers solely because they start with
`x-openai-`.

When an input array contains an item with `type = "additional_tools"`,
instruction normalization MUST leave the entire input array and top-level
`instructions` field unchanged. In particular, neither the tool item nor an
adjacent developer instructions message may be extracted from the native Lite
input prefix. The presence of the `additional_tools` item in the normalized
input array MUST be the authoritative signal that the request uses Responses
Lite.

If compact-request size handling trims oversized conversation history, it MUST
retain the `additional_tools` item and its immediately following developer
instructions message. The resulting compact payload MUST therefore retain the
body signal needed to synthesize the canonical Lite header.

For a Responses Lite body, upstream HTTP Responses and compact requests MUST
include the canonical `x-openai-internal-codex-responses-lite: true` header.
Upstream websocket handshakes MUST omit that header and each websocket
`response.create` body MUST instead include
`client_metadata.ws_request_header_x_openai_internal_codex_responses_lite = "true"`.
For a non-Lite HTTP body, the proxy MUST omit the synthesized HTTP header. A
websocket marker on an incremental frame without the full Lite input prefix MAY
remain only when the same request continuity state previously received
`response.created` for a Lite request derived from `additional_tools` using the
same effective upstream model, and the frame's `previous_response_id` references
the response ID recorded by the most recent such Lite acceptance. A frame
without a `previous_response_id`, or one referencing any other response, MUST
NOT receive trusted Lite treatment. The recorded acceptance ID MUST be the
response ID exposed downstream: when a transparent replay suppresses its
`response.created` and keeps rewriting events to the originally visible
response ID, Lite continuity records that visible ID rather than the hidden
upstream replay ID. The effective model comparison MUST occur
after alias normalization and API-key enforcement, and a merely prepared request
MUST NOT establish or clear trusted Lite continuity. Trusted state MUST update
in upstream request-acceptance order rather than terminal-event completion
order, and acceptance of a non-Lite request MUST NOT clear previously recorded
Lite continuity.
An accepted `generate = false` prewarm derived from an `additional_tools` prefix
MUST establish the same trusted continuity because a later request MAY reuse its
response ID without repeating that prefix.
A transparent fresh full-resend replay that clears `previous_response_id` (for
example after an upstream previous-response miss) severs that linkage, so the
replayed request MUST NOT carry the reserved marker unless its own input
contains the `additional_tools` prefix. Acceptance of such a replay MUST
reflect the replayed body: a marker-stripped replay MUST NOT be recorded as a
Lite acceptance (later frames referencing the replay's response ID are not
trusted), while a replay whose input retains the `additional_tools` prefix
MUST re-establish trusted Lite continuity.
Otherwise, the proxy MUST strip the reserved client-metadata marker. The
HTTP-to-websocket bridge MUST preserve its internally derived canonical marker
when it trims an already-stored input prefix or rebuilds the request during
forwarding or retry, even if the remaining input delta has no `additional_tools`
item.

#### Scenario: Instruction normalization preserves Lite tools and tool history

- **WHEN** a request input contains an `additional_tools` item, developer text,
  custom tool calls, and `custom_tool_call_output` items
- **THEN** top-level `instructions` remains unchanged
- **AND** the developer text, `additional_tools`, custom calls, and outputs all
  remain in their original input order

#### Scenario: HTTP and compact synthesize Lite only from the body

- **WHEN** a normalized HTTP Responses or compact payload contains an
  `additional_tools` input item
- **THEN** the upstream request includes
  `x-openai-internal-codex-responses-lite: true`
- **AND** the original inbound Lite header value is not forwarded verbatim

#### Scenario: Compact trimming retains the Lite prefix

- **GIVEN** an oversized Responses Lite compact input whose tool bundle exceeds
  the normally retained head budget
- **WHEN** compact size handling trims conversation history
- **THEN** the `additional_tools` item and adjacent developer instructions stay
  in their original order
- **AND** the upstream compact request includes the canonical Lite header

#### Scenario: Websocket uses a per-request Lite marker

- **WHEN** a websocket `response.create` payload contains an `additional_tools`
  input item
- **THEN** the upstream websocket handshake omits the Lite header
- **AND** the forwarded `response.create` payload contains the canonical
  per-request Lite client-metadata marker

#### Scenario: HTTP bridge trimming preserves Lite metadata

- **GIVEN** an HTTP Responses Lite request whose stored input prefix contains
  the `additional_tools` item
- **WHEN** the HTTP-to-websocket bridge trims that prefix and forwards only the
  new input delta
- **THEN** the forwarded `response.create` payload still contains the canonical
  per-request Lite client-metadata marker

#### Scenario: Incremental websocket marker requires trusted Lite continuity

- **GIVEN** a websocket request received `response.created` after establishing
  Lite mode from an `additional_tools` prefix for its effective upstream model
- **WHEN** a later same-model incremental frame contains the canonical marker,
  omits the already-known prefix, and its `previous_response_id` references the
  accepted Lite response
- **THEN** the forwarded frame retains the canonical marker
- **BUT WHEN** a request for another model supplies that marker without a Lite
  prefix or trusted same-model continuity
- **THEN** the proxy strips the marker
- **BUT WHEN** a same-model frame supplies that marker without a
  `previous_response_id`, or with one referencing a response other than the
  accepted Lite response
- **THEN** the proxy strips the marker
- **AND** the recorded Lite continuity remains available to later frames that
  do reference the accepted Lite response

#### Scenario: Suppressed-created replay keeps Lite continuity on the visible id

- **GIVEN** a Lite websocket request whose `response.created` was already sent
  downstream when the upstream connection is lost
- **WHEN** the proxy transparently replays the request, suppresses the new
  `response.created`, and rewrites downstream events to the original visible
  response id
- **THEN** a later same-model marker-only frame whose `previous_response_id`
  references the visible response id keeps the trusted marker
- **BUT WHEN** a frame references the hidden upstream replay id instead
- **THEN** the proxy strips the marker

#### Scenario: Fresh replay of a trusted incremental frame drops the marker

- **GIVEN** a trusted marker-only incremental websocket frame whose
  self-contained multi-item input yields a transparent fresh full-resend replay
- **WHEN** upstream reports the referenced previous response as not found and
  the proxy replays the request without `previous_response_id`
- **THEN** the replayed request omits the reserved client-metadata marker
- **AND** the accepted replay is not recorded as a Lite acceptance, so a later
  same-model frame carrying the marker with `previous_response_id` referencing
  the replay's response is not trusted and has its marker stripped
- **BUT WHEN** the replayed input itself contains the `additional_tools` prefix
- **THEN** the replayed request retains the canonical marker
- **AND** the accepted replay re-establishes trusted Lite continuity for later
  frames referencing its response ID

#### Scenario: Accepted Lite prewarm authorizes incremental reuse

- **GIVEN** a same-model Lite prewarm containing `additional_tools` receives
  `response.created`
- **WHEN** Codex reuses that response ID in a later frame with the canonical
  marker but without the already-sent Lite prefix
- **THEN** the forwarded frame retains the canonical marker whether its input
  delta is empty or contains new user input

#### Scenario: Stale inbound headers do not enable a non-Lite request

- **WHEN** an HTTP request has no `additional_tools` input item but includes an
  inbound Lite header
- **THEN** the upstream HTTP request omits the Lite signal
- **AND** existing Codex continuity and unrelated OpenAI telemetry headers are
  preserved

### Requirement: WebSocket tool-output deltas are not fresh-retryable

The service MUST NOT replay a direct WebSocket Responses request as a fresh turn
without the previous-response anchor when it includes `previous_response_id` and
only carries tool output items for tool calls that are not present in the same
payload after an upstream `previous_response_not_found`.

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn

- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses`
  follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`,
  `custom_tool_call_output`, or `apply_patch_call_output` items without their
  matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a
  response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without
  `previous_response_id`

### Requirement: Ultra reasoning effort is aliased to max on the upstream wire

The proxy MUST forward any outbound upstream Responses payload whose `reasoning.effort` resolves to `ultra` — whether requested by the client or injected by API-key reasoning enforcement — with `reasoning.effort: "max"`. `ultra` is a client-plane reasoning effort: GPT-5.6 Sol and Terra advertise it
in their catalog entries, but the reference Codex client rewrites it to `max`
before building the upstream Responses request
(`reasoning_effort_for_request` in codex-rs `core/src/client.rs` at release
rust-v0.144.1); its additional effect (proactive multi-agent mode) is purely
client-side. Source-routed chat-completions
payloads with an enforced `ultra` effort MUST likewise forward `max`. Code
paths that build upstream Responses payloads directly instead of passing
through the proxy request-policy rewrite — such as automation compact pings —
MUST apply the same aliasing before dispatch, while persisted automation
configuration and run history keep the configured client-plane `ultra` value.
`max`
and `xhigh` MUST be forwarded verbatim (no `max` → `xhigh` aliasing exists
upstream).

#### Scenario: Client-requested ultra forwards as max

- **WHEN** a client sends a Responses request for `gpt-5.6-sol` with `reasoning: {"effort": "ultra"}`
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Enforced ultra forwards as max

- **GIVEN** an API key configured with `enforcedReasoningEffort: "ultra"`
- **WHEN** a request is proxied with that API key
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Automation compact ping with ultra dispatches max

- **GIVEN** an automation configured with model `gpt-5.6-sol` and reasoning effort `ultra`
- **WHEN** an automation run dispatches its compact ping upstream
- **THEN** the dispatched compact payload uses `reasoning.effort: "max"`
- **AND** the stored automation run history keeps the configured `ultra` effort

#### Scenario: Max is forwarded verbatim

- **WHEN** a client sends a Responses request with `reasoning: {"effort": "max"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "max"`

### Requirement: Source-routed Responses tools are capability-filtered

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST forward `function` tools unchanged and MUST drop non-`function` tools the
source model has not declared support for. A source model declares support in
its `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools
(`web_search`, including the `web_search_preview` alias), and
`"experimental_supported_tools"` MAY list additional supported tool types.
When only some tools are dropped, a `tool_choice` that references a dropped
tool MUST be removed so the forwarded payload never names a tool that is not
present; `function`-typed choices MUST be preserved. When all tools are
dropped, `tools`, `tool_choice`, and `parallel_tool_calls` MUST be removed
together. Whenever a hosted tool is dropped, `include` entries specific to
that tool type (for example `web_search_call.*` for `web_search`,
`file_search_call.*` for `file_search`, `code_interpreter_call.*` for
`code_interpreter`, and `computer_call_output.*` for computer-use tools) MUST
be pruned from the forwarded payload; non-tool-specific entries (for example
`reasoning.encrypted_content`) MUST be kept, and the `include` field MUST be
removed entirely when pruning empties it. This filtering MUST apply on every
source-routed Responses surface (`/backend-api/codex/responses` and
`/v1/responses`).

#### Scenario: Codex-only tools are dropped for a plain source model

- **GIVEN** a Responses-capable source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `namespace` tool, and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool

#### Scenario: Search-capable source models keep web-search tools

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a Responses request with a `function` tool and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains both tools
- **AND** a `tool_choice` of `{"type": "web_search"}` is preserved

#### Scenario: tool_choice referencing a dropped tool is removed

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `tool_choice` `{"type": "web_search"}` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload contains no `tool_choice` key

#### Scenario: include entries of a dropped tool are pruned

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `include` `["web_search_call.action.sources", "reasoning.encrypted_content"]` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload's `include` contains only `"reasoning.encrypted_content"`

#### Scenario: Dropping every tool removes the tool-only fields

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request whose tools are all unsupported is forwarded to it
- **THEN** the forwarded payload contains no `tools`, `tool_choice`, or `parallel_tool_calls` keys

### Requirement: Source request overrides apply without clobbering proxy-owned keys

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST apply the model's `source_request_overrides` from `raw_metadata_json` to
the forwarded payload. The `options` override MUST merge key-wise into any
client-sent `options` object, with override values winning per key. The
overrides MUST NOT change the `model` key (owned by source selection) or the
`stream` key (owned by the proxy's response-handling mode).

#### Scenario: Ollama options are injected into the forwarded payload

- **GIVEN** a source model whose overrides are `{"options": {"num_ctx": 32768}}`
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload contains `"options": {"num_ctx": 32768}`

#### Scenario: model and stream overrides are ignored

- **GIVEN** a source model whose overrides contain `"model": "other-model"` and `"stream": false`
- **WHEN** a streaming Responses request for slug `local-model` is forwarded to the source
- **THEN** the forwarded payload keeps `model` as the routed source model
- **AND** the forwarded payload keeps `stream` as `true`

### Requirement: Interrupted tool calls receive synthetic outputs on anchored follow-ups
The service MUST track tool-call items completed by a streamed response that may still require a tool output — `function_call`, `custom_tool_call`, and `apply_patch_call` — together with each call's item type. When a follow-up `response.create` anchors on that completed response via `previous_response_id` and its input omits an output item for a tracked call id, the service MUST prepend a synthetic interrupted output item whose type matches the originating call type (`function_call` -> `function_call_output`, `custom_tool_call` -> `custom_tool_call_output`, `apply_patch_call` -> `apply_patch_call_output`) before forwarding the request upstream. This applies to the direct WebSocket route and to the HTTP responses bridge session path.

#### Scenario: interrupted custom tool call on the WebSocket route
- **GIVEN** a WebSocket `response.create` turn completes with a `custom_tool_call` item whose output was never sent (the turn was interrupted)
- **WHEN** the next `response.create` on the same session references that response via `previous_response_id` without a `custom_tool_call_output` for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input
- **AND** the follow-up does not fail with an upstream `No tool output found for custom tool call` error

#### Scenario: interrupted custom tool call on the HTTP bridge
- **GIVEN** an HTTP bridge session completes a response containing a `custom_tool_call` item whose output was never sent
- **WHEN** the next bridge request anchors on that response id (client-sent or proxy-injected `previous_response_id`) without an output item for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input

#### Scenario: interrupted function call keeps existing output type
- **WHEN** the pending tool call recorded from the previous response is a `function_call`
- **THEN** the synthetic interrupted output item is a `function_call_output` (existing behavior preserved)

#### Scenario: follow-up that carries the tool output is not modified
- **WHEN** the anchored follow-up input already contains a `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` item for a pending call id
- **THEN** the service does not inject a synthetic output for that call id

#### Scenario: injected bridge outputs stay subject to the request size guard
- **GIVEN** an HTTP bridge follow-up whose serialized `response.create` is close to the upstream byte limit
- **WHEN** synthetic interrupted outputs are injected
- **THEN** the service prepares the upstream request from the injected payload so the `response.create` slim/size guard runs against the bytes actually sent upstream
- **AND** an over-limit injected request is rejected locally with `payload_too_large` instead of being forwarded upstream

#### Scenario: stored input context reflects the injected upstream input
- **WHEN** an HTTP bridge follow-up gains synthetic interrupted outputs
- **THEN** the input item count, input fingerprint, and request usage budget recorded for the request are computed from the injected upstream-shaped input, so later full-resend/anchor comparisons on the same bridge session match what upstream actually stored

#### Scenario: unfingerprinted input turns keep the WebSocket continuity anchor
- **GIVEN** a WebSocket turn whose request input yields no prefix fingerprint (a string input — normalized to a single user message at request validation — or an empty input list)
- **WHEN** the response completes with pending tool-call items
- **THEN** the continuity state still records the completed response id and the pending tool-call metadata for all tracked call types, clearing only the prefix count/fingerprint pair
- **AND** a follow-up that anchors on that response id receives the synthetic interrupted outputs instead of leaking the upstream missing-tool-output 400

#### Scenario: local previous-response recovery retry keeps injected outputs
- **GIVEN** an HTTP bridge submit whose payload gained synthetic interrupted outputs and which fails before yielding with a previous-response continuity error
- **WHEN** the local recovery path re-prepares the anchored retry request
- **THEN** the synthetic interrupted outputs are re-injected from the failed session's pending tool-call state, so the recovered submit does not reintroduce the upstream missing-tool-output failure

#### Scenario: replayed apply_patch prefix is trimmed on anchored bridge follow-ups
- **GIVEN** an HTTP bridge follow-up that anchors via `previous_response_id` and replays a prior `apply_patch_call` item (marked as response output) followed by its `apply_patch_call_output`
- **WHEN** the bridge trims the previous-response prefix already covered by the anchor
- **THEN** `apply_patch_call` and `apply_patch_call_output` items are recognized by the trim exactly like the `function_call` and `custom_tool_call` variants, matching the WebSocket route's replay trim

#### Scenario: owner-forward failover recovery injects from local session state when available
- **GIVEN** a multi-instance bridge where an anchored follow-up is forwarded to the remote owner instance and the relay fails before yielding any bytes
- **WHEN** the local instance recovers by rebinding a local bridge session and resubmitting the anchored request
- **THEN** the service injects synthetic interrupted outputs when the rebound local session still holds the pending tool-call state for the anchored response id (for example after ownership flapped back to this instance)

#### Scenario: owner-forward failover recovery without local pending state is a known bounded gap
- **GIVEN** the same owner-forward failure, where the pending tool-call metadata exists only in the remote owner instance's memory (the durable bridge store does not persist pending call ids)
- **WHEN** the local recovery rebinds a fresh session that has no pending tool-call state
- **THEN** the anchored recovery request is resubmitted unmodified, without fabricated tool outputs (matching pre-injection behavior)
- **AND** if upstream rejects it with a missing-tool-output error, the extended classifier masks it as a retryable continuity failure instead of surfacing the raw upstream 400

### Requirement: Missing-tool-output classification covers all tool call variants
The service MUST classify an upstream `invalid_request_error` with `param=input` whose message starts with `No tool output found for function call call_`, `No tool output found for custom tool call call_`, `No tool output found for apply patch call call_`, or `No tool output found for tool search call call_` as a missing-tool-output continuity error, so the existing masking and retry recovery paths engage instead of forwarding the raw upstream 400 downstream. The hosted `No tool output found for web search call` wording MUST NOT be classified, because a `web_search_call` is executed upstream and carries no client-addressable tool output.

#### Scenario: custom tool call variant is masked on the HTTP bridge
- **WHEN** upstream emits `invalid_request_error` with `param=input` and message `No tool output found for custom tool call call_x`
- **AND** the pending bridge request carries `previous_response_id`
- **THEN** the service rewrites the error to a retryable `stream_incomplete` continuity failure
- **AND** the raw upstream message and call id are not exposed downstream

#### Scenario: tool search call variant is masked on the HTTP bridge
- **WHEN** upstream emits `invalid_request_error` with `param=input` and message `No tool output found for tool search call call_x`
- **AND** the pending bridge request carries `previous_response_id`
- **THEN** the service rewrites the error to a retryable `stream_incomplete` continuity failure
- **AND** the raw upstream message and call id are not exposed downstream

#### Scenario: hosted web search wording stays unclassified
- **WHEN** upstream emits `invalid_request_error` with `param=input` and a message starting `No tool output found for web search call`
- **THEN** the service does not treat it as a missing-tool-output continuity error

### Requirement: Non-message system and developer input items are preserved

When normalizing Responses or compact request `input`, the service MUST only
hoist items that are instruction messages — `system`/`developer`-role items
whose `type` is omitted or `"message"` — into the top-level `instructions`
field. Any `system`/`developer`-role input item carrying any other `type`
value, including item types the service does not model, MUST be forwarded
upstream unchanged and in its original input position. This preservation MUST
hold both when the request is validated and when the request is serialized for
upstream delivery, and it exempts the item from input sanitization: keys such
as `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call`
MUST NOT be stripped from a preserved item. When compact requests exceed the
upstream input budget and
the service trims the input middle, preserved non-message `system`/`developer`
items MUST be treated as trim anchors and retained in the trimmed payload
rather than replaced by the trim marker. When a non-message
`system`/`developer` item is preserved and the request carries no top-level
`instructions` and no hoistable instruction messages, the service MUST default
`instructions` to the empty string so the request still validates and
forwards. Requests whose input contains an `additional_tools` item remain
governed by the Responses Lite rule that leaves the entire input array and
top-level `instructions` unchanged.

#### Scenario: unknown non-message developer input item survives normalization

- **WHEN** a Responses or compact request `input` contains a typed, non-message
  item such as `{"type": "future_directive", "role": "developer", ...}`
  alongside developer instruction messages and user messages
- **THEN** the developer instruction messages are hoisted into `instructions`
- **AND** the `future_directive` item remains in `input` unchanged, in its
  original position
- **AND** the upstream-serialized payload retains the item unchanged

#### Scenario: preserved directive keeps reasoning and tool-call keys

- **WHEN** a Responses or compact request `input` contains a typed,
  non-message `system`/`developer` item carrying keys the interleaved
  reasoning sanitizer strips from message items (such as
  `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`)
- **THEN** the item is retained byte-identical after validation
- **AND** the upstream-serialized payload retains the item byte-identical

#### Scenario: directive-only request without instructions still validates

- **WHEN** a Responses or compact request omits top-level `instructions` and
  its `input` contains only a typed, non-message `system`/`developer` item
  (such as `{"type": "future_directive", "role": "developer", ...}`) alongside
  user messages
- **THEN** the request validates with `instructions` defaulted to `""`
- **AND** the directive item remains in `input` unchanged, including in the
  upstream-serialized payload

#### Scenario: preserved directive survives compact input trimming

- **WHEN** a compact request is large enough to trigger upstream input
  trimming and its input middle contains a typed, non-message
  `system`/`developer` item such as
  `{"type": "future_directive", "role": "developer", ...}`
- **THEN** the trimmed upstream payload retains the item unchanged
- **AND** the item is not replaced by the trim marker

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST preserve required Lite state through compact preparation, MUST validate the final transformed compact input against the upstream JSON wire budget, MUST reject policy rewrites to catalog-confirmed non-Lite models, and MUST suppress replayed code-mode side effects without collapsing distinct call identities. Compact trimming MAY omit a complete terminal non-state, non-side-effecting tool pair only when the pair plus required anchors and trim markers cannot fit the upstream wire budget. A latest output anchored by `previous_response_id` or a non-empty `conversation` remains required only when its matching call is absent from supplied input. A supplied call matches an output only when both `call_id` and the function/custom/apply-patch protocol variant are compatible. An unmatched latest tool call and a terminal tool call or matching pair classified as side-effecting by the canonical tool-safety classifier remain required compact context and MUST fail closed with `responses_compact_input_too_large` when they cannot fit. These guards MUST NOT weaken the body-derived Lite signal or trusted previous-response linkage rules. When already-observed inline image bytes alone make a required latest tool result too large, compact preparation MUST replace only those image bytes with an explicit textual omission marker rather than permanently poisoning the thread; this image-byte relaxation MUST NOT weaken fail-closed handling for oversized textual state.

#### Scenario: Oversized compact input keeps the Lite prelude
- **WHEN** compact input trimming is required for a Responses Lite request
- **THEN** every required `additional_tools` item remains in the upstream input
- **AND** typed and role-only system/developer state remains in the upstream input

#### Scenario: Compact input keeps a latest tool pair that fits
- **WHEN** compact trimming is required, the latest input item is a non-state, non-side-effecting tool call or tool output, and its complete pair fits with required anchors and trim markers
- **THEN** the latest item remains in the upstream input
- **AND** any matching call or output present in the supplied input is retained with it

#### Scenario: Oversized non-state tool tail leaves room for trim markers
- **WHEN** the latest input item is a non-state, non-side-effecting tool call or output whose complete pair cannot fit with required anchors and trim markers
- **THEN** the service omits the call and output together and represents the omission with a compact-trim marker
- **AND** it does not return `responses_compact_input_too_large` solely because the pair fit before marker framing
- **AND** the marker does not claim omitted terminal context was preserved

#### Scenario: Continuity-anchored latest tool output remains required
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest input item is a tool output without a matching call in the supplied input
- **THEN** the output remains in the upstream input because its call belongs to the prior response
- **AND** the service returns `responses_compact_input_too_large` when that required output cannot fit

#### Scenario: Ordinary non-patch paired tail may be omitted
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest ordinary, non-`apply_patch` tool output has a matching call in supplied input
- **THEN** compact trimming MAY omit the complete pair when it cannot fit
- **AND** this allowance does not apply to an `apply_patch` call or output

#### Scenario: Reused call ID from another tool variant does not satisfy continuity
- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest tool
  output reuses the `call_id` of an incompatible function/custom/apply-patch
  call variant in supplied input
- **THEN** the latest output remains required as continuity from the previous response
- **AND** the incompatible supplied call is not retained as its pair

#### Scenario: Oversized latest unmatched tool call fails closed
- **WHEN** the latest compact input item is an unmatched tool call that cannot fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large` rather than representing the call with a compact-trim marker

#### Scenario: Side-effecting tail remains required
- **WHEN** the latest compact input item is an `apply_patch_call`, `apply_patch_call_output`, or a tool call or matching pair classified as side-effecting by the canonical tool-safety classifier
- **THEN** the item and any matching counterpart remain required compact context
- **AND** the service returns `responses_compact_input_too_large` rather than omitting the side-effecting patch record when they cannot fit

#### Scenario: Reused call IDs keep only the required occurrence
- **WHEN** an older tool call and a required state-tool call reuse the same call ID
- **THEN** compact trimming retains the output matched to the required state-call occurrence
- **AND** it does not retain an oversized historical output solely because its earlier call reused that ID

#### Scenario: Exact-budget backtracking drops an optional tool pair together
- **WHEN** optional tool context fits the approximate item budget but trim-marker framing exceeds the exact wire cap
- **THEN** backtracking removes the optional call and its matching output as one group
- **AND** it does not re-add either counterpart while preserving every required item

#### Scenario: Oversized inline image does not poison terminal compaction
- **WHEN** compact input exceeds the upstream limit because a required latest eligible tool output contains an inline data-URL image that the model already observed
- **THEN** compact preparation retains the tool call and output identities
- **AND** first uses lossless context trimming when that can fit the request
- **AND** replaces only the inline image bytes with an explicit textual omission marker
- **AND** replaces an eligible legacy Chat `image_url` content part as a whole
  with a schema-valid text part before any generic string substitution
- **AND** preserves the other textual parts of the tool output
- **AND** accepted file-backed `input_file` references remain unchanged
- **AND** hosted `computer_call_output` screenshots remain fail-closed until a
  schema-valid compact placeholder is defined
- **AND** non-image required content that cannot fit still returns
  `responses_compact_input_too_large`

### Requirement: Compact trimming preserves prioritised historical side effects

The service MUST retain recognised historical side-effect tool calls as bounded
priority context when an oversized compact input is trimmed. It MUST use the
same side-effect classifier as downstream replay
deduplication. This includes code-mode `exec` and `collaboration` wrapper calls
as well as their lower-level tool spellings and recognised parallel batches.

For each retained historical side effect, compact trimming MUST retain its
matching call and output together. The service MUST reserve space for that
complete pair before selecting optional ordinary head or tail context. Required
state anchors and the current required item remain mandatory; if they leave no
room for a historical pair, the service MAY drop that pair together and retain a
trim marker instead.

A recognised side-effect call without a non-empty `call_id` MUST NOT be
retained as a historical side-effect anchor, because it cannot form a verified
call/output pair.

#### Scenario: Code-mode side effect survives an oversized compact input

- **WHEN** an oversized compact input contains a historical custom `exec` or
  `collaboration` call with its matching output outside required state context
- **THEN** the trimmed upstream input retains both the call and its output when
  the pair fits with required state
- **AND** optional ordinary tail context is dropped before that pair

#### Scenario: Historical side-effect pair cannot fit with required state

- **WHEN** required state anchors and the current required item leave no room
  for a historical side-effect call and its matching output
- **THEN** compact trimming drops the entire historical pair
- **AND** it does not retain only one member of that pair

#### Scenario: Side-effect call lacks a usable pair key

- **WHEN** an oversized compact input contains a recognised historical
  side-effect call without a non-empty `call_id`
- **THEN** compact trimming does not preserve that call as a side-effect anchor
- **AND** it does not emit an unpaired historical side-effect call upstream

#### Scenario: Final compact wire expansion is rejected locally

- **WHEN** Unicode escaping, JSON array framing, or image inlining makes the final compact input exceed the upstream limit
- **THEN** the service returns `responses_compact_input_too_large` before an upstream attempt
- **AND** any API-key reservation is released
- **AND** no upstream account is penalized

#### Scenario: Terminal compaction trigger validates before admission

- **WHEN** a streaming Responses request ends with `compaction_trigger` and its derived compact input cannot fit
- **THEN** the service returns the same invalid-client-payload response before admission, reservation, account selection, or upstream compact work

#### Scenario: Enforced non-Lite model rejects Lite input

- **WHEN** API-key policy rewrites Lite-shaped input to a model whose catalog metadata disables Responses Lite
- **THEN** the service rejects the request before any upstream HTTP or websocket attempt

#### Scenario: Replayed code-mode side effects are emitted once

- **WHEN** reconnect replay repeats the same code-mode `exec` or `collaboration` call identity
- **THEN** the downstream client receives that side-effecting call only once

#### Scenario: Distinct code-mode calls remain distinct

- **WHEN** request history has different call IDs with identical code-mode source text and matching outputs
- **THEN** every call and matching output remains in the forwarded history

### Requirement: Reasoning summaries omit blank HTML comment placeholders

Responses reasoning output items and summary delta/part events MUST remove standalone blank HTML comment placeholder lines from `summary_text` before forwarding them to clients, including markers split across delta boundaries. This cleanup applies to both `/backend-api/codex/responses` and `/v1/responses` streamed or collected output item paths. The cleanup MUST be limited to reasoning summary text and MUST NOT rewrite placeholder-free whitespace, assistant-visible message content, inline blank comments, or non-empty HTML comments.

#### Scenario: Codex CLI route does not expose blank comment marker

- **GIVEN** upstream emits a reasoning output item with `summary: [{"type":"summary_text","text":"**Planning**\n\n<!-- -->"}]`
- **WHEN** a Codex CLI client streams `POST /backend-api/codex/responses`
- **THEN** the forwarded reasoning summary text is `**Planning**`
- **AND** the stream does not contain `<!-- -->`

### Requirement: HTTP bridge admission waiters survive upstream replacement

The proxy MUST preserve an HTTP bridge session when its upstream connection
terminates while an unsent request is already waiting for that session's
response-create admission. It MUST fail the requests that were pending on the
terminated upstream but MUST NOT retire, unregister, prune, or release the
retained session while the unsent waiter owns the handoff.

After the waiter acquires admission, the proxy MUST reconnect the retained
session before sending the request. A waiter that has not entered the pending
request queue and has no upstream send timestamp MAY be sent exactly once on
that fresh connection. Hard-affinity sessions MUST retain their account and
continuity ownership during this handoff. If the session was replaced or
unregistered, or reconnection fails, the proxy MUST fail closed without sending
the waiter. Cancelling or failing the last waiter MUST allow the closed session
to retire and release its resources.

#### Scenario: admitted follow-up survives an upstream close

- **GIVEN** one HTTP bridge request is pending upstream
- **AND** a follow-up request is unsent and waiting on the same response-create gate
- **WHEN** the upstream connection closes before the follow-up acquires the gate
- **THEN** the pending request receives its terminal continuity failure
- **AND** the session remains registered and protected from pruning for the waiter
- **AND** the waiter reconnects the retained session and is sent exactly once
- **AND** the waiter does not receive an internal bridge-closed error

#### Scenario: unsafe handoff fails closed

- **GIVEN** an unsent waiter whose prior session was replaced or unregistered
- **OR** the retained session cannot reconnect
- **WHEN** the waiter acquires admission
- **THEN** the waiter is not sent
- **AND** the request receives an explicit retryable proxy error

### Requirement: Selected Codex installation identity is internally consistent

For native Codex requests, the service MUST use an account-specific installation id consistently.
When that id is applied, the service MUST use the same id in `x-codex-installation-id` and in
an existing `x-codex-turn-metadata.installation_id` field on every upstream
Responses transport. Missing, malformed, or non-object turn metadata MUST be
preserved rather than invented or discarded.

#### Scenario: Both canonical metadata carriers are present in a payload

- **WHEN** a native Responses payload contains both installation metadata
  carriers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Both canonical metadata carriers are present in headers

- **WHEN** a native HTTP or WebSocket request carries both installation
  metadata headers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Turn metadata cannot be safely rewritten

- **WHEN** `x-codex-turn-metadata` is malformed JSON, is not a JSON object, or
  does not contain `installation_id`
- **THEN** the service preserves that turn metadata unchanged
- **AND** it still applies the selected account id through the standalone
  installation-id carrier

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the
request has no account-scoped file requirement. A request with an account-
scoped file requirement MUST remain bound to its file owner.

#### Scenario: unanchored bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request is safely replayable before
  `response.created`
- **AND** it has no account-scoped file requirement
- **THEN** the bridge excludes the stalled account before reconnecting

#### Scenario: file-backed bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request requires its file-owner account
- **AND** it is retried before `response.created`
- **THEN** the bridge does not exclude or clear the required file owner

### Requirement: Direct capacity-wait progress follows the downstream stream contract

When direct HTTP/SSE streaming waits for recoverable local account capacity, the proxy MUST emit `codex.keepalive` progress events if the OpenAI SDK stream contract is disabled, regardless of whether the route propagates HTTP errors.
The proxy MUST continue suppressing those non-standard progress events before
startup when both HTTP error propagation and the OpenAI SDK stream contract are
enabled.

#### Scenario: Native image-capable bypass emits capacity progress

- **GIVEN** an image-capable native Codex request bypasses the HTTP responses bridge
- **AND** the route propagates HTTP errors with `enforce_openai_sdk_contract = false`
- **WHEN** direct account selection waits for `account_stream_cap` or `account_response_create_cap` to recover
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"` before capacity is released
- **AND** no upstream response attempt or terminal event occurs before capacity is released
- **AND** account selection retries and the real upstream completion is forwarded after capacity becomes available

#### Scenario: OpenAI SDK startup error remains structured

- **GIVEN** a route propagates HTTP errors with `enforce_openai_sdk_contract = true`
- **WHEN** a local account-capacity wait occurs before stream startup
- **THEN** the proxy MUST NOT emit `codex.keepalive` before startup
- **AND** a terminal local-cap failure remains available to the route's structured HTTP error path

### Requirement: Direct WebSocket replay never mixes numeric response sequences

For direct Responses WebSocket requests, the proxy MUST NOT transparently replay a request on a fresh upstream generation after any finite integer `sequence_number` frame for that request has been successfully sent downstream, except for the verified no-generation prewarm case defined below. When an upstream close would otherwise trigger replay, the proxy MUST settle the failed pending request without emitting frames from a new upstream generation under the existing downstream response id, and MUST close the downstream WebSocket with code 1011 so the client can retry on a fresh transport. When an upstream terminal error would otherwise trigger quota, authentication, security-work, or equivalent replay, the proxy MUST finalize and surface that terminal error without reconnecting. Suppressed frames and non-integer sequence sentinels MUST NOT by themselves disable otherwise-safe replay.

The sole numeric-sequence exception MUST require `request_kind = "prewarm"`, a
literal normalized `generate = false`, exactly one recorded
`response.created`, no visible output, sequence watermark `0`, a single pending
request, and the existing one-shot replay eligibility. The proxy MUST suppress
the replayed `response.created` and MUST NOT renumber or synthesize sequences.
If a later replay event has a finite integer sequence that does not advance
beyond the exposed watermark, the proxy MUST settle it as `stream_incomplete`,
emit no synthetic terminal frame, and close downstream with code 1011.

#### Scenario: Sequenced response is interrupted before completion
- **WHEN** a direct WebSocket model-generating request has emitted `response.created` or another frame with a finite integer `sequence_number`
- **AND** upstream closes before a terminal response event
- **THEN** codex-lb does not transparently replay that request under the existing downstream response id
- **AND** no lower replay sequence is emitted downstream
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Prewarm metadata without generate-false body is not sufficient
- **WHEN** a direct WebSocket request claims `request_kind = "prewarm"`
- **BUT** its normalized body does not contain the literal `generate = false`
- **AND** a numeric sequence has been sent downstream
- **THEN** codex-lb does not transparently replay the request

#### Scenario: Progressed prewarm is not replayed
- **WHEN** a verified no-generation prewarm has emitted `response.created` and
  any additional `response.*` progress event
- **AND** upstream closes before completion
- **THEN** codex-lb does not transparently replay the request
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Replayed prewarm sequence must advance
- **GIVEN** a verified no-generation prewarm is replayed after exposing
  `response.created` at sequence `0`
- **WHEN** a non-suppressed replay frame has a finite integer
  `sequence_number <= 0`
- **THEN** codex-lb emits no frame from that replay generation downstream
- **AND** it settles the request as `stream_incomplete`
- **AND** it closes the downstream WebSocket with code 1011

#### Scenario: Unsafe replay settles request ownership
- **WHEN** sequenced replay is refused after upstream close or a replay sequence fails to advance
- **THEN** response-create admission, account-local leases, API-key reservations, and request logging are finalized exactly once
- **AND** the failed attempt does not become a successful continuity owner

#### Scenario: Sequenced retryable terminal event is not replayed
- **WHEN** a direct WebSocket request has successfully emitted a finite integer `sequence_number`
- **AND** upstream emits a terminal error that would ordinarily trigger transparent quota, authentication, or security-work replay
- **THEN** codex-lb does not reconnect or resend the request
- **AND** the terminal error is finalized and remains client-visible under the existing error contract

#### Scenario: Sequence-free startup remains replayable
- **WHEN** upstream closes before any numeric sequence-bearing frame has been successfully sent downstream
- **AND** the request otherwise satisfies the existing one-shot replay guard
- **THEN** codex-lb MAY transparently replay the request on a fresh upstream connection

#### Scenario: Suppressed frame does not establish exposure
- **WHEN** codex-lb suppresses an upstream frame before downstream emission
- **AND** the suppressed frame contains a numeric `sequence_number`
- **THEN** that frame does not establish the downstream sequence watermark

### Requirement: Downstream websocket ingress accepts large response.create messages
The server MUST accept client-to-proxy websocket messages on the Responses websocket routes (`/backend-api/codex/responses`, `/v1/responses`) up to a configurable ingress budget before closing the connection at the protocol layer. The default budget MUST be 128 MiB, matching the HTTP responses-path decompressed body cap. The budget MUST be configurable via the `--ws-max-size` CLI flag and the `UVICORN_WS_MAX_SIZE` environment variable, with the CLI flag taking precedence. The server MUST continue to negotiate `permessage-deflate` on the client-facing websocket, and the ingress budget MUST apply to the decompressed message size.

#### Scenario: Oversized response.create reaches the application-level guard
- **WHEN** a client sends a single websocket text message larger than 16 MiB but within the configured ingress budget
- **THEN** the server delivers the message to the application layer instead of closing the connection with `1009 message too big`
- **AND** the application-level oversized-`response.create` handling (historical slimming, then local rejection) applies

#### Scenario: Operator overrides the ingress budget
- **WHEN** the operator starts the server with `--ws-max-size <bytes>` or sets `UVICORN_WS_MAX_SIZE=<bytes>`
- **THEN** the websocket ingress message budget uses the configured value
- **AND** an invalid (non-positive or non-integer) value fails startup with a clear error

### Requirement: Oversized response.create payloads are slimmed or rejected fail-fast before upstream send

When the service prepares a Responses `response.create` request for the upstream websocket, it MUST measure the serialized outbound request size before sending it upstream. If the payload exceeds the upstream websocket budget, the service MUST first attempt to slim only the historical portion of `input` that precedes the most recent user turn: historical inline images MUST be replaced with textual omission notices, and oversized historical tool outputs MUST be replaced with textual omission notices that preserve the item in sequence. Historical slimming MUST cover tool-call output items of every supported type — `function_call_output`, `custom_tool_call_output`, and `apply_patch_call_output` — including inline images nested inside list- or mapping-valued `output` content parts, which MUST be replaced with the image omission notice while non-image parts, item order, `call_id`, and `status` fields are preserved. If the request still exceeds budget after slimming, the service MUST fail locally with status `400` — not `413` — carrying `error.code = "payload_too_large"`, `error.type = "invalid_request_error"`, and `error.param = "input"`, because the official Codex client treats `400` as a non-retryable invalid-request error surfaced immediately while `413` triggers five full-payload retries followed by a sticky session-wide websocket-to-HTTP transport downgrade.

#### Scenario: Historical inline artifacts are slimmed and the latest user turn is preserved
- **WHEN** a Responses request exceeds the upstream websocket budget because historical inline images or historical oversized tool outputs dominate the serialized `input`
- **AND** replacing those historical artifacts with omission notices reduces the serialized request below budget
- **THEN** the service forwards the slimmed `response.create` upstream
- **AND** it preserves the most recent user turn unchanged

#### Scenario: HTTP Responses route fails locally with 400 when the payload still exceeds budget
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service returns HTTP `400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT allocate or reuse an upstream websocket bridge session for that request

#### Scenario: Websocket Responses route fails locally with a status-400 error event when the payload still exceeds budget
- **WHEN** a websocket `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service emits a websocket error event with `"type": "error"` and `"status": 400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT connect the upstream websocket for that request

#### Scenario: Inline images nested in historical tool-call outputs are slimmed
- **GIVEN** an oversized `response.create` whose historical `input` contains a
  `custom_tool_call_output` (or `function_call_output` /
  `apply_patch_call_output`) whose `output` is a list of content parts
  including `data:image/` inline images
- **WHEN** the size guard triggers historical slimming
- **THEN** each nested inline image part is replaced with the image omission
  notice part
- **AND** non-image parts, item order, `call_id`, and `status` are preserved
- **AND** the slimmed request is forwarded upstream when it fits the budget

#### Scenario: Oversized string outputs are slimmed for all tool-call output types
- **GIVEN** a historical `custom_tool_call_output` or `apply_patch_call_output`
  whose string `output` exceeds the oversized-tool-output threshold
- **WHEN** the size guard triggers historical slimming
- **THEN** the string output is replaced with the tool-output omission notice,
  matching the existing `function_call_output` behavior

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

#### Scenario: Remaining budget constrains all stream attempt timeouts
- **WHEN** account selection, account-capacity recovery, or token refresh leaves only part of the request budget available before a stream attempt starts
- **THEN** the proxy limits the upstream connect timeout, SSE idle timeout, and upstream request total timeout to that same remaining budget
- **AND** the client receives `response.failed` with `upstream_request_timeout` once that budget is exhausted instead of waiting through the full configured stream windows

#### Scenario: Forced refresh retry recomputes all attempt timeouts
- **WHEN** a first stream attempt fails with an authentication error that triggers a forced token refresh and retry
- **THEN** the proxy recomputes the remaining request budget after the refresh
- **AND** the retry attempt reapplies connect, idle, and total timeout limits from that recomputed budget

#### Scenario: Recoverable account-capacity wait is bounded by the request budget
- **WHEN** account selection reports a recoverable retry hint such as temporary rate-limit or stream-capacity exhaustion
- **AND** the streaming request still has remaining request budget
- **THEN** the proxy may wait for at most the smaller of the recovery hint and the remaining request budget before retrying selection
- **AND** if the budget is exhausted before an account becomes available, the request fails through the normal no-account or rate-limit error path instead of starting a fresh full-budget wait

#### Scenario: Local balancer rate-limit exhaustion is not treated as recoverable capacity
- **WHEN** account selection reports the local balancer message `Rate limit exceeded. Try again in Ns`
- **AND** the selection result is a local no-account failure with `no_accounts` or no explicit error code
- **THEN** the proxy does not enter an account-capacity recovery wait from that local retry hint
- **AND** the request returns through the normal no-account or rate-limit error path instead of repeatedly retrying the same local selection failure

#### Scenario: Local account cap selection waits instead of failing immediately
- **WHEN** account selection for a streaming Responses request fails locally with `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy treats the condition as a recoverable account-capacity wait within the request budget
- **AND** it retries account selection after the bounded wait instead of returning an immediate 429
- **AND** permanent `no_accounts` failures remain non-waitable unless they carry a distinct recoverable capacity or upstream quota signal

#### Scenario: Post-selection response-create capacity preserves routing invariants
- **WHEN** a selected account reaches `account_response_create_cap` before downstream output is visible
- **THEN** an unpinned request MUST prefer an eligible alternate account before waiting
- **AND** an owner-bound, file-pinned, or otherwise same-account retry MUST keep or reacquire its stream lease while waiting within the original request budget
- **AND** the same behavior applies after a forced token refresh

#### Scenario: SDK-contract propagated startup errors remain observable
- **WHEN** a route requests HTTP error propagation, enforces the OpenAI SDK stream contract, and waits for local account capacity before startup
- **THEN** the route MUST perform the bounded recovery wait instead of raising the first cap error immediately
- **AND** it MUST NOT emit an account-capacity keepalive before startup succeeds, so a terminal startup error can still use the route's structured error path

#### Scenario: Existing HTTP bridge session waits on submit capacity
- **WHEN** HTTP bridge session submission reaches `account_response_create_cap`
- **THEN** a hard-affinity or file-pinned request MUST wait and retry submission within the bridge request budget
- **AND** a soft-affinity request MUST retain its existing alternate-session reroute behavior before waiting on the saturated session

#### Scenario: WebSocket account selection waits on local caps
- **WHEN** downstream WebSocket account selection returns `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy MUST emit a `codex.keepalive` with status `waiting_for_account_capacity`
- **AND** retry selection within the original WebSocket request budget
- **AND** return the original local-cap error if that budget is already exhausted

### Requirement: Streaming account-capacity waits keep clients alive
When a streaming Responses request waits for temporary account capacity to recover before account selection can continue, the proxy MUST emit downstream progress events during the wait. HTTP/SSE and HTTP bridge streams MUST emit `codex.keepalive` events with `status = "waiting_for_account_capacity"`, request id, elapsed wait seconds, and retry-after seconds when known. HTTP bridge streams MAY also emit `response.in_progress` to satisfy OpenAI Responses stream parsers before later terminal events. WebSocket clients MUST receive equivalent `codex.keepalive` JSON messages. These progress events MUST NOT expose account emails, API keys, raw affinity keys, prompt content, or request payloads. Contract-shaped streams remain subject to the direct capacity-wait progress requirement, which suppresses non-standard progress events before startup when both HTTP error propagation and the OpenAI SDK stream contract are enabled.

#### Scenario: HTTP/SSE capacity wait emits keepalive
- **WHEN** `/v1/responses` streaming account selection can recover after a retry hint
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"`
- **AND** includes the request id, waited seconds, and bounded retry-after seconds

#### Scenario: HTTP bridge capacity wait preserves parser progress
- **WHEN** an HTTP responses bridge request waits for session creation or account selection capacity
- **THEN** the bridge stream emits a capacity-wait keepalive
- **AND** emits OpenAI-compatible in-progress events when needed so downstream Responses stream parsers do not time out before the terminal response

#### Scenario: WebSocket capacity wait emits JSON keepalive
- **WHEN** a WebSocket Responses request waits for account capacity recovery
- **THEN** the downstream WebSocket receives a JSON `codex.keepalive` message with `status = "waiting_for_account_capacity"`
- **AND** the connection remains open until selection retries, the request budget expires, or the client disconnects

### Requirement: Downstream-HTTP upstream transport follows a configurable policy

When a downstream HTTP/SSE request (`request_transport == "http"`) resolves its base upstream transport to `"websocket"`, the proxy MUST decide the final upstream transport using the configured `http_downstream_transport_policy`, after all higher-precedence rails have been applied, and the policy MUST NOT affect native WebSocket clients (`request_transport == "websocket"`), which keep their dedicated upstream WebSocket path.

Precedence (highest first), evaluated before the policy:

1. Outside the existing recent upstream WS failure cooldown, an explicit
   `upstream_stream_transport` override of `"http"` or `"websocket"` wins.
2. Oversized-payload bypass and the `image_generation` bypass force upstream
   HTTP. A request carrying `input_image` parts forces upstream HTTP only when
   its serialized payload exceeds the WebSocket frame budget, or when the
   payload still carries an external `http(s)` image URL that the proxy may be
   unable to inline; an inline `data:` image alone MUST NOT force upstream HTTP.
   These two residual `input_image` pins are deliberately evaluated ahead of an
   explicit `"websocket"` override wherever the request passes through the HTTP
   bridge routing decision — every `/v1/responses` and
   `/backend-api/codex/responses` request does — because that override
   short-circuits the size gate and an oversized image payload would otherwise
   fail locally with `400 payload_too_large`. A request that never reaches that
   decision, such as a `/v1/chat/completions` request whose bridge admission has
   already declined the bridge, follows item 1 instead.
3. The effective policy (per-API-key `transport_policy_override` when
   set, otherwise the global `http_downstream_transport_policy`) decides.

Policy values and behavior:

- `always_http` (and its alias `pinned`): the request MUST be sent over
  upstream HTTP `POST`, preserving the legacy unconditional pin.
- `always_websocket`: the request MUST keep upstream WebSocket whenever
  the base transport resolved to `"websocket"` without replacing a base
  `"auto"` transport mode with a hard `"websocket"` override.
- `smart` (default): the request MUST keep upstream WebSocket **iff** at
  least one sticky-continuation signal is present on the request, and
  MUST otherwise fall back to upstream HTTP. The sticky-continuation
  signals are:
  - a non-null `previous_response_id` on the request payload, **OR**
  - a `prompt_cache_key` present on the request model, **OR**
  - a Codex session header (`session_id`, `x-codex-session-id`, or
    `x-codex-conversation-id`), **OR**
  - an `x-codex-turn-state` continuity header, **OR**
  - a non-empty `conversation` identifier, **OR**
  - a structured tool-result input item (`function_call_output`,
    `custom_tool_call_output`, or `apply_patch_call_output`), **OR**
  - an assistant message followed by new user input in the supplied history.

Tool declarations, instruction messages, and user-only input sequences MUST NOT
alone count as continuation evidence. The existing recent upstream WS failure
cooldown MUST force upstream HTTP before these policy choices, including an
explicit WebSocket preference; clearing or expiring the marker restores normal
eligibility.

When a policy decision keeps upstream WebSocket, the proxy MUST preserve
the configured/base downstream transport mode passed to the upstream
client. In particular, a base `"auto"` mode MUST remain `"auto"` so the
existing WebSocket-handshake rejection fallback to upstream HTTP remains
available. The policy MAY force a concrete transport override only when
the decision is to downgrade to upstream HTTP.

The per-API-key `transport_policy_override`, when non-null, MUST be used
as the effective policy for requests authenticated by that key and MUST
take precedence over the global default. A null override MUST fall
through to the global `http_downstream_transport_policy`.

#### Scenario: single-shot downstream-HTTP request falls back to HTTP under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport resolves to `"websocket"`
- **AND** a downstream HTTP request carries no `previous_response_id`, no
  `prompt_cache_key`, no Codex session header, and no `x-codex-turn-state`
  header, conversation identifier, tool result, or assistant-to-user history
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: sticky downstream-HTTP request keeps WebSocket under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport mode is `"auto"` and resolves to `"websocket"`
- **AND** a downstream HTTP request carries any one of
  `previous_response_id`, `prompt_cache_key`, a Codex session header, or
  an `x-codex-turn-state` header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`
- **AND** an upstream WebSocket handshake rejection status eligible for
  auto fallback MUST transparently retry over upstream HTTP

#### Scenario: always_http policy preserves the legacy pin

- **GIVEN** `http_downstream_transport_policy` is `"always_http"` (or
  `"pinned"`) and the base upstream transport resolves to `"websocket"`
- **WHEN** a downstream HTTP request resolves the upstream transport,
  regardless of sticky signals
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: always_websocket policy never downgrades sticky-less HTTP

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
  and the base upstream transport mode is `"auto"` and resolves to
  `"websocket"`
- **WHEN** a downstream HTTP request with no sticky signals resolves the
  upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`

#### Scenario: per-key override wins over the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has
  `transport_policy_override = "always_http"`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`,
  because the per-key override takes precedence

#### Scenario: null per-key override follows the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has `transport_policy_override =
  null`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket, following the global
  `smart` policy

#### Scenario: explicit websocket override still beats the policy

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **AND** no recent upstream WS failure marker is active
- **WHEN** a single-shot downstream HTTP request with no sticky signals, and
  which trips none of the precedence item 2 bypasses, resolves the upstream
  transport under any policy
- **THEN** the explicit override MUST win and the request MUST use
  upstream WebSocket

#### Scenario: external image URL still forces HTTP under an explicit websocket override

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **AND** a request passing through the HTTP bridge routing decision carries an
  `input_image` part whose `image_url` is an external `http(s)` URL
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  override would otherwise short-circuit the residual pin and hand the upstream
  WebSocket a URL it does not accept

#### Scenario: oversized payload bypass still forces HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** the serialized request payload exceeds the WebSocket frame
  budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  oversized-payload bypass has higher precedence than the policy

#### Scenario: inline image alone does not force HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** a request carries an inline `data:` image below the WebSocket
  frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket

#### Scenario: external image URL still forces HTTP

- **GIVEN** `upstream_stream_transport` is `"auto"`
- **AND** a request carries an `input_image` part whose `image_url` is an
  external `http(s)` URL, anywhere in the input — including inside a
  tool-output array, which the image inliner never rewrites
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: native WebSocket clients are unaffected by the policy

- **GIVEN** any value of `http_downstream_transport_policy`
- **WHEN** a native WebSocket client (`request_transport == "websocket"`)
  streams a request
- **THEN** the client MUST keep its dedicated upstream WebSocket path and
  the policy MUST NOT downgrade it to HTTP

### Requirement: Request-scoped Codex metadata survives HTTP-to-WebSocket bridging

When an HTTP Responses request is translated into an upstream WebSocket `response.create` frame, the service MUST project nonblank `x-codex-turn-metadata`, `x-openai-subagent`, `x-codex-parent-thread-id`, and `x-codex-window-id` compatibility headers into that frame's `client_metadata`. This projection MUST happen for every request, including requests multiplexed over a reused upstream socket. A metadata value already supplied in the request body MUST remain authoritative over the compatibility header, and header matching MUST be case-insensitive.

#### Scenario: Reused bridge session receives a subagent turn

- **GIVEN** a parent HTTP request has opened an upstream Responses WebSocket
- **WHEN** a subagent HTTP request reuses that socket with subagent, parent-thread, and child-window headers
- **THEN** the subagent request's `response.create.client_metadata` contains those values
- **AND** the earlier parent frame retains its own window metadata
- **AND** no value is inherited solely from the socket handshake

#### Scenario: Body metadata remains canonical

- **WHEN** a request body and compatibility header provide different values for the same Codex metadata key
- **THEN** the upstream `response.create.client_metadata` retains the body value

### Requirement: Compact routing honors turn-state affinity

When a compact request carries a nonblank `x-codex-turn-state`, the service MUST classify that value as Codex-session affinity before considering a session header, prompt-cache affinity, or sticky-thread affinity. This precedence MUST apply even when generic Codex session-header affinity is disabled, matching the normal Responses path.

#### Scenario: Turn-state-only compact remains on the turn owner

- **GIVEN** a Responses turn established an account mapping for an `x-codex-turn-state` value
- **AND** another account becomes preferable under the non-sticky routing strategy
- **WHEN** `/responses/compact` carries only that turn-state continuity value
- **THEN** the compact request is routed to the account that owns the turn-state mapping

#### Scenario: Turn-state overrides less-specific affinity

- **WHEN** a compact request carries turn-state, session-header, and prompt-cache keys
- **THEN** its affinity key is the turn-state value
- **AND** its affinity kind is Codex session

### Requirement: Namespaced side-effect replay dedupe preserves call identity

For a namespaced side-effect function or custom-tool call, the service MUST use the call's namespace and call ID as part of downstream and replayed-history deduplication identity. An exact replay with the same namespace, name, call ID, and canonical arguments MUST remain suppressed. Calls with different namespaces or different nonblank call IDs MUST remain distinct, even when their names and canonical arguments match, and their matching outputs MUST remain in forwarded history.

Flat legacy side-effect calls MAY continue to use argument-based replay identity so reconnects that change only a call ID do not repeat shell, patch, or terminal side effects.

#### Scenario: Distinct namespaced spawns use identical arguments

- **WHEN** two `collaboration.spawn_agent` calls have identical arguments and different call IDs
- **THEN** both calls are forwarded
- **AND** both matching outputs remain in replayed request history

#### Scenario: Exact namespaced call is replayed after reconnect

- **WHEN** reconnect replay emits the same namespaced call ID and canonical arguments under a new response ID
- **THEN** the service suppresses the replayed downstream call

#### Scenario: Equal call identity appears in different namespaces

- **WHEN** two side-effect calls share a name, call ID, and arguments but have different namespaces
- **THEN** the service treats them as distinct calls

### Requirement: Compact requests preserve scoped turn-state ownership

When a compact request contains a real client-supplied `x-codex-turn-state`, the system MUST resolve the token only in the requesting API key scope and select only that owner account. If the owner cannot be resolved or selected, the request MUST fail closed and MUST NOT fall back to a generic sticky or load-balanced account. Proxy-synthesized first-turn placeholders (the `turn_*` / `http_turn_*` values codex-lb injects when the client did not supply one) are not real continuity tokens until registered as bridge aliases; an unregistered placeholder MUST NOT block file-owner routing, but a registered placeholder MUST still resolve to its owner account.

#### Scenario: Token belongs to the requesting API key

- **GIVEN** an active turn-state owner exists for the requesting API key
- **WHEN** the client submits a compact request with that token
- **THEN** compact selection is constrained to that owner account

#### Scenario: Unscoped sticky state cannot supply a turn-state owner

- **GIVEN** a turn-state token has no owner in the requesting API-key-scoped local or durable bridge indexes
- **WHEN** an unscoped sticky-session mapping exists for the same token
- **THEN** compact owner resolution fails closed
- **AND** the unscoped sticky-session mapping is not consulted

#### Scenario: Token belongs to a different API key or is unavailable

- **GIVEN** the token has no owner in the requesting API key scope
- **WHEN** the client submits a compact request with that token
- **THEN** the request fails with `turn_state_owner_unavailable`
- **AND** no generic account is selected

#### Scenario: Registered synthesized placeholder belongs to the requesting API key

- **GIVEN** a proxy-synthesized `http_turn_*` token has been registered as a bridge alias
- **WHEN** the client later submits a compact request with that token
- **THEN** compact selection is constrained to the registered owner account

#### Scenario: Synthesized first-turn placeholder does not override file-owner routing

- **GIVEN** the request carries only a proxy-synthesized `x-codex-turn-state`
- **AND** the payload references an `input_file.file_id` pinned to an account
- **WHEN** the client submits the compact request
- **THEN** compact routing may use the pinned file owner
- **AND** the synthesized placeholder does not trigger `turn_state_owner_unavailable`

### Requirement: Collected failures retain upstream turn-state metadata

The system MUST copy a real `x-codex-turn-state` received in a `response.metadata` event into the HTTP headers of a collected response, including when the later terminal event is `response.failed`.

#### Scenario: Metadata precedes a failed response

- **GIVEN** a collected response stream emits turn-state metadata
- **AND** the terminal response is failed
- **THEN** the returned HTTP error includes the captured turn-state header

### Requirement: WebSocket incomplete responses preserve the upstream reason in request logs

When an upstream Responses WebSocket terminal `response.incomplete` event contains a non-empty string at `response.incomplete_details.reason`, the service SHALL persist the request log with status `error` and SHALL preserve that reason as both `error_code` and `error_message`. The terminal event sent to the downstream client and the account-health treatment of an incomplete response SHALL remain unchanged.

#### Scenario: max-output limit is identifiable in a WebSocket request log

- **WHEN** the upstream emits `response.incomplete` with
  `incomplete_details.reason` equal to `max_output_tokens`
- **THEN** the corresponding WebSocket request log has status `error`,
  `error_code` equal to `max_output_tokens`, and `error_message` equal to
  `max_output_tokens`
- **AND** the account is not marked unhealthy solely because of that
  incomplete event

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Audio-transcriptions-compatible sources MAY
serve `/v1/audio/transcriptions`. Codex-native compaction, file upload,
control-plane, and websocket bridge paths MUST remain subscription-backed unless
a later requirement explicitly defines OpenAI-compatible source behavior for
those paths.

#### Scenario: Chat completions routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares chat-completions support
- **AND** the authenticated API key is allowed to use that source/model
- **WHEN** the client calls `POST /v1/chat/completions` with that model
- **THEN** the proxy forwards the request to the source's configured base URL
  using the source's upstream API key

#### Scenario: Codex-native Responses route uses Responses-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares Responses support
- **AND** it exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `deepseek-v4-flash`
- **THEN** the proxy forwards the request to that source's Responses endpoint

#### Scenario: Chat-only source is not used for Codex-native Responses route

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `local-coder`
- **THEN** the request is not routed to that source
- **AND** subscription-backed Codex routing rules continue to apply

#### Scenario: Compaction request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /backend-api/codex/responses` for that model whose
  input contains a `compaction_trigger` item
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription-backed Codex compaction path instead

#### Scenario: V1 compaction_trigger remains eligible for model sources

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /v1/responses` for that model whose input ends with
  a terminal `compaction_trigger` item
- **THEN** the request remains eligible for that Responses-compatible source
- **AND** it is not forced onto subscription account selection by the Codex-only
  compaction source-route exclusion

#### Scenario: File-referencing request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `/backend-api/codex/responses` or `/v1/responses` for that
  model whose input references an uploaded `input_file`/`input_image` `file_id`
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription path so the account-scoped file pin is honored

#### Scenario: Audio transcription routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares audio transcriptions support
- **AND** it exposes model `whisper-large-v3`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with multipart
  field `model=whisper-large-v3`
- **THEN** the proxy forwards the multipart request to the source's
  `/audio/transcriptions` endpoint
- **AND** the request uses the source's upstream API key

#### Scenario: Non-source transcription model keeps subscription validation

- **GIVEN** no audio-transcriptions-compatible source exposes model `gpt-4o-mini`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with
  `model=gpt-4o-mini`
- **THEN** the proxy returns the existing unsupported transcription model error

### Requirement: Previous-response source routing follows proven ownership

When a Responses request targets a configured Responses-compatible model source and carries `previous_response_id`, the proxy MUST use recorded subscription-account ownership as the veto for model-source routing. The proxy MUST NOT infer ownership from the response identifier's syntax. A recorded subscription owner MUST keep the request on subscription routing. When no subscription owner is recorded, the configured model source MUST remain authoritative, including when the identifier uses the canonical OpenAI `resp_` hexadecimal shape.

For the direct Responses WebSocket transport, a recorded subscription owner MUST keep the request on the owner-bound subscription path. A configured source model without a recorded subscription owner MUST retain the existing `model_source_requires_http_transport` fallback behavior.

#### Scenario: Recorded subscription owner overrides an HTTP model source

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is not forwarded to the model source
- **AND** subscription routing preserves the recorded account owner

#### Scenario: Canonical source response ID remains source-routed over HTTP

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source

#### Scenario: Direct WebSocket preserves a recorded subscription owner

- **GIVEN** a source is also configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the request remains on the owner-bound subscription WebSocket path
- **AND** the proxy does not emit `model_source_requires_http_transport`

#### Scenario: Direct WebSocket source continuation falls back to HTTP

- **GIVEN** a source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits `model_source_requires_http_transport`
- **AND** the request is not sent to a subscription upstream

### Requirement: Source-routed chat payloads are sanitized before forwarding

Source-routed `/v1/chat/completions` requests SHALL forward the client's
OpenAI-compatible payload with the following sanitization applied to the
outbound body:

- An empty `tools` array MUST be omitted, together with `tool_choice` and
  `parallel_tool_calls`, so tool-less requests reach the source without
  tool-calling artifacts.
- Non-standard reasoning toggles (`include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, and `reasoning_effort`) MUST be stripped
  unless the source model's catalog entry opts into reasoning via
  `raw_metadata_json` containing `"supports_reasoning": true`.
- An API key's enforced reasoning effort MAY still be applied after
  sanitization; explicit operator policy overrides the default strip.

#### Scenario: Empty tools array is not forwarded

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** a client calls `POST /v1/chat/completions` for that model without
  tools (or with `"tools": []`) and `"tool_choice": "none"`
- **THEN** the body forwarded to the source contains no `tools`, `tool_choice`,
  or `parallel_tool_calls` keys

#### Scenario: Reasoning toggles are stripped for non-reasoning source models

- **GIVEN** a source model whose catalog entry does not declare
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, or `reasoning_effort` in the request
- **THEN** none of those keys appear in the body forwarded to the source

#### Scenario: Catalog opt-in preserves reasoning toggles

- **GIVEN** a source model whose `raw_metadata_json` contains
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning: true`
- **THEN** the forwarded body preserves the client's reasoning fields

### Requirement: Source-routed audio transcriptions preserve OpenAI-compatible multipart semantics

Source-routed `/v1/audio/transcriptions` requests SHALL forward the inbound
audio file and non-file multipart fields to the selected source's
`/audio/transcriptions` endpoint. The proxy MUST use the stored source API key
for upstream authorization and MUST NOT forward the downstream client's
authorization credential. JSON and non-JSON successful upstream response bodies
SHALL be returned to the client with the upstream content type when present.

#### Scenario: Text transcription response passes through

- **GIVEN** an enabled OpenAI-compatible source exposes model `whisper-large-v3`
- **AND** the client requests `response_format=text`
- **WHEN** the source returns a plain text response
- **THEN** the proxy returns that response body without requiring JSON parsing

#### Scenario: Limited key requires token usage

- **GIVEN** an API key has token or cost limits
- **AND** a source-routed audio transcription response has no token-compatible
  usage fields
- **AND** the source model declares no per-minute audio rate
- **WHEN** the upstream source returns a successful transcription response
- **THEN** the proxy releases the reservation
- **AND** returns `usage_unavailable` instead of allowing unaccounted limited-key usage

### Requirement: Audio transcription sources MAY bill by duration

The proxy SHALL support per-minute audio billing for source models that
declare an `audio_per_minute` rate. When the rate is set and a source-routed
`/v1/audio/transcriptions` response carries a positive audio duration
(top-level `duration` seconds, or a `usage.seconds`/`usage.duration` fallback),
the proxy MUST settle cost as `duration_minutes * audio_per_minute` with zero
tokens, and MUST record that cost on the request log and against the API key's
`cost_usd` limit. Duration billing MUST take precedence over token pricing on
the transcription route. A model with no `audio_per_minute` rate MUST fall back
to token-usage settlement.

#### Scenario: Duration-priced model settles cost from audio length

- **GIVEN** an audio-transcriptions source model with `audio_per_minute = 0.30`
- **AND** an API key with a `cost_usd` limit
- **WHEN** a transcription response reports `duration = 120` seconds and no token usage
- **THEN** the API-key reservation is finalized with 0 tokens and $0.60 cost
- **AND** the request log records `cost_usd = 0.60`

#### Scenario: Duration billing does not require token usage for limited keys

- **GIVEN** an audio-transcriptions source model with an `audio_per_minute` rate
- **AND** an API key with token or cost limits
- **WHEN** a transcription response carries a positive duration but no token usage
- **THEN** the request succeeds and settles from duration
- **AND** the proxy does not return `usage_unavailable`

### Requirement: Upstream Responses payloads omit client-omitted request fields

The service MUST NOT emit top-level request fields the client omitted onto
upstream Responses payloads when the field's absence is meaningful upstream.
In particular, the proxy MUST NOT synthesize a top-level `"tools": []` from
the request model's default for clients that did not send the `tools` field,
on any upstream transport (websocket `response.create` frames, HTTP-bridge
bodies, and direct HTTP stream requests). An explicit client-sent
`"tools": []` MUST be forwarded as `[]`. `tool_choice` and
`parallel_tool_calls` MUST be forwarded only when the client sent them;
an explicit client-sent `parallel_tool_calls: false` MUST reach upstream.
The OpenAI-compatible `/v1/responses` conversion MUST propagate `tools`
omission into the native request so both routes behave identically.
Field omission MUST survive every re-serialization hop: the multi-instance
owner-forward body (internal bridge forward) MUST NOT contain fields the
client omitted, the owner instance receiving a forwarded request MUST NOT
re-mark `tools` as explicitly set, and model-source Responses egress payloads
MUST likewise omit fields the client never sent. The owner forward MUST carry
a v2 signature (`x-codex-bridge-signature-v2`) computed over the same
forwarding serialization that is posted as the body, and the forwarding
origin MUST NOT relay externally supplied `x-codex-bridge-*` headers. The
receiving instance MUST treat the v2 signature as authoritative only when it
validates: a valid v2 signature accepts the forward (proving the received
body was not rewritten, including an injected `"tools": []`); an absent or
invalid v2 header falls back to the legacy signature verification; the
forward is rejected only when neither verifies. Mere v2-header presence MUST
NOT block a legacy-signed forward, because pre-v2 origins relay unknown
inbound bridge headers verbatim and an external client could otherwise deny
legitimate forwards by planting a garbage v2 header. For rolling-upgrade
compatibility the origin MUST also keep sending the legacy signature headers
(computed over the plain dump with the synthesized `"tools": []`) so pre-v2
owners verify unchanged. ROLLOUT SHIM: the legacy header emission and the
legacy fallback are a one-release compatibility shim and MUST be removed in a
follow-up change once fleets are homogeneous on a v2-signing release (grep
for `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`); while the shim is
active the legacy fallback is exactly as strong as the pre-v2 scheme (a
body-only rewrite injecting `"tools": []` into a dual-signed forward
downgrades to the legacy digest and verifies), and removing the shim restores
strict v2-only rejection.

#### Scenario: Responses Lite request reaches upstream without a tools key

- **WHEN** a `/backend-api/codex/responses` request omits top-level `tools`
  and carries its tool bundle in an `additional_tools` input item
- **THEN** the upstream websocket `response.create` frame contains no
  top-level `tools` key
- **AND** the HTTP-bridge request body contains no top-level `tools` key

#### Scenario: Explicit empty tools array is forwarded

- **WHEN** a client sends `"tools": []` explicitly
- **THEN** the upstream payload contains `"tools": []`

#### Scenario: Unset optional tool fields stay absent

- **WHEN** a client omits `tool_choice` and `parallel_tool_calls`
- **THEN** the upstream payload contains neither field

#### Scenario: Owner-forwarded request keeps tools omitted across instances

- **WHEN** a request that omits top-level `tools` is forwarded to its owner
  instance over the internal HTTP bridge (multi-instance owner forward)
- **THEN** the owner-forward request body contains no top-level `tools` key
- **AND** the owner instance parses the forwarded body without marking
  `tools` as explicitly set, so its upstream payload contains no top-level
  `tools` key
- **AND** the owner-forward signature still verifies on the owner instance

#### Scenario: Owner-forward v2 signature covers the posted body

- **WHEN** an owner-forward body that omitted top-level `tools` is rewritten
  in transit to carry an injected explicit `"tools": []`
- **THEN** the v2 signature verification fails
- **AND** absent a valid legacy shim signature, the owner instance rejects
  the forwarded request with an invalid bridge-forward-signature error
  instead of re-marking `tools` as explicitly set
- **AND** generic body rewrites outside the synthesized-tools equivalence
  class fail both digests and are rejected even while the shim headers are
  present

#### Scenario: Mixed-version fleets keep verifying during a rolling upgrade

- **WHEN** an updated origin forwards a dual-signed tools-less body to an
  owner still running pre-v2 code
- **THEN** the legacy signature header matches the pre-v2 owner's
  recomputation over the plain dump, so the forward verifies unchanged
- **WHEN** a pre-v2 origin forwards a legacy-signed body (no v2 header) to
  an updated owner
- **THEN** the updated owner falls back to legacy verification and accepts
  the forward

#### Scenario: Spoofed v2 header does not deny legacy forwards

- **WHEN** a legacy-signed forward from a pre-v2 origin arrives carrying a
  garbage `x-codex-bridge-signature-v2` header that an external client
  planted (pre-v2 origins relay unknown inbound bridge headers verbatim)
- **THEN** the updated owner treats the invalid v2 signature as
  non-authoritative, falls back to legacy verification, and accepts the
  forward
- **AND** an updated origin strips externally supplied `x-codex-bridge-*`
  headers before forwarding, so its own forwards never relay a planted
  header

#### Scenario: Model-source Responses egress omits unsent tools

- **WHEN** a Responses request that omits top-level `tools` is routed to an
  openai-compatible model source
- **THEN** the payload sent to the model source contains no top-level
  `tools` key

### Requirement: Client tool entries are forwarded byte-preserved

The service MUST forward client-sent top-level `tools` entries to upstream
byte-preserved: the tool array order, per-object key order, unknown keys
(including unknown tool types such as `namespace` entries and non-standard
schema markers), and array-value order (for example `parameters.required`)
MUST reach upstream exactly as the client sent them. Tool canonicalization
(array sorting and recursive key sorting) MUST be used only for prompt-cache
affinity and observability hashing and MUST NOT mutate the outgoing payload.
The affinity/observability hash MUST remain insensitive to tool array order
and object key order.

#### Scenario: Reserved namespace tool survives byte-identical

- **WHEN** a client sends top-level `tools` containing a reserved
  `{"type": "namespace", "name": "collaboration", ...}` entry with nested
  function entries, `strict: false`, unknown property markers, and a
  non-alphabetical `required` array
- **THEN** the upstream `response.create` frame serializes that `tools` array
  byte-identical to the client's serialization

#### Scenario: Affinity hash ignores tool ordering

- **WHEN** two requests differ only in tool array order or tool object key
  order
- **THEN** their tools affinity/observability hash is identical

### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, websocket relay, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Schema validation of the parsed payload MUST run only for stream lifecycle frames (`response.created`, `response.completed`, `response.incomplete`, `response.failed`, `error`); all other frames MUST be classified from the parsed payload's `type` field (with a typeless payload carrying an `error` object classifying as `error`). Event framing, payload contents, dedupe/rewrite semantics, usage settlement, and error normalization MUST be unchanged.

A canonically framed SSE block — a leading `event: <type>` line followed by a single JSON-object `data:` line with LF framing — whose type requires no per-event consumer MAY skip payload parsing entirely and be relayed downstream with the upstream bytes verbatim (raw UTF-8 and upstream key order/spacing preserved; JSON-equivalent to the canonical re-encode). A frame MUST take the parse path when any consumer needs it: lifecycle/terminal frames, tool-call item frames (`response.output_item.added`, `response.output_item.done`), text-done frames (`response.output_text.done`, `response.content_part.done`), frames arriving while the TTFT first-token window is open (including a pending reasoning-delta window), frames carrying a `"service_tier"` marker, and any block without canonical framing (data-only blocks, multi-line data, or an `event:` field that does not lead the block). A parsed frame MUST be re-serialized with canonical `event: <type>` + `data:` framing when modified or when its source block lacked canonical framing. Legacy event-type alias rewrites MUST cover both the `data:` payload type and the `event:` framing line.

#### Scenario: Unmodified events pass through the /v1 normalizer verbatim

- **GIVEN** a canonical stream event that no normalizer branch rewrites
- **WHEN** the /v1 response normalizer processes it
- **THEN** the original block is yielded byte-identically without re-serialization

#### Scenario: Tool-call rewrite reuses the parsed event on the no-change path

- **GIVEN** an event without duplicate parallel tool calls
- **WHEN** the rewrite step runs with the caller's parsed event
- **THEN** it returns the original line, payload, and event without re-parsing or re-validating

#### Scenario: Rewritten events stay consistent

- **WHEN** the rewrite step removes duplicate tool calls
- **THEN** the returned line, payload, and validated event all reflect the rewritten content

#### Scenario: Unmodified canonical delta frames relay upstream bytes verbatim

- **GIVEN** a canonically framed `response.output_text.delta` frame containing raw UTF-8, arriving after the first visible token settled the TTFT window
- **WHEN** the streaming mixin processes it
- **THEN** the upstream block is yielded byte-identically without a JSON parse or `ensure_ascii` re-encode, and downstream text-visibility accounting still updates

#### Scenario: Data-only frames regain canonical framing

- **GIVEN** a delta frame without a leading `event:` line
- **WHEN** the streaming mixin processes it after the TTFT window settles
- **THEN** the frame is parsed and re-serialized with the canonical `event: <type>` line so named-event (EventSource) clients keep seeing the event name

#### Scenario: Legacy alias frames are rewritten on both lines

- **GIVEN** an upstream block whose `event:` line and `data:` payload both carry the legacy `response.text.delta` type
- **WHEN** the core client normalizes the block
- **THEN** both the `event:` framing line and the payload `type` read `response.output_text.delta`

#### Scenario: Error frames keep the full parse and rewrite path

- **GIVEN** a canonically framed `error` frame, or a frame whose payload carries a top-level `error` envelope
- **WHEN** the core client normalizes the stream for the SDK contract
- **THEN** the frame is parsed and rewritten to a terminal `response.failed` event exactly as before verbatim relay

#### Scenario: /v1 identity pass-through accepts verbatim raw-UTF-8 blocks

- **GIVEN** an upstream-verbatim canonical delta block containing raw UTF-8
- **WHEN** the /v1 normalizer leaves the parsed payload unmodified
- **THEN** the block passes through byte-identically (the identity gate compares parsed-payload object identity and the `event:` framing prefix, not re-serialized bytes)

#### Scenario: Delta frames skip schema validation

- **GIVEN** a stream of `response.output_text.delta` frames between `response.created` and `response.completed`
- **WHEN** the streaming mixin, websocket relay, or bridge upstream reader processes the stream
- **THEN** only the lifecycle frames are schema-validated, the delta frames are classified from the parsed payload dict, and downstream output, usage settlement, and error normalization are unchanged

#### Scenario: Identity websocket relay frames are forwarded without re-encoding

- **GIVEN** a websocket frame matched to a request whose downstream response-id rewrite does not apply
- **WHEN** the relay forwards the frame downstream
- **THEN** the upstream frame text is forwarded as-is instead of a canonical JSON re-encode

### Requirement: Durable bridge ownership distinguishes process incarnations

Durable HTTP bridge ownership MUST include a per-process owner epoch in
addition to the stable bridge instance id and the existing owner fencing epoch.
The process owner epoch MUST be generated when the process starts and MUST be
persisted on newly claimed durable HTTP bridge session rows.

On startup, an instance MUST retire durable HTTP bridge sessions whose
`owner_instance_id` equals the current instance id but whose process owner epoch
is missing or differs from the current process owner epoch. Retired rows MUST
be closed and MUST NOT remain attachable through session-header,
turn-state, previous-response, latest-turn-state, or latest-response lookup.
Retired rows MUST clear stored previous-response, latest-turn-state, input
fingerprint, and pending-tool continuity anchors before any future claim can
reuse the same canonical session key.

#### Scenario: Same-container restart retires previous-process rows

- **GIVEN** a durable HTTP bridge session is ACTIVE under instance
  `container-74e8e7cda9fb` and process epoch `boot-a`
- **WHEN** codex-lb starts again in the same container id with process epoch
  `boot-b`
- **THEN** startup closes the `boot-a` durable session row
- **AND** request-target lookup for that session header, turn state, or
  previous response no longer returns the closed row
- **AND** rows already owned by `boot-b` remain attachable

### Requirement: Dead durable anchors recover transparently when safe

The proxy MUST classify proven-dead durable anchors as automatic recovery
candidates before returning any client-visible error.

When a continuity-bound HTTP bridge request would otherwise return a retryable
`stream_idle_timeout` or cooldown terminal, and the durable lookup that supplied
the request's previous-response anchor is proven dead because its owner
instance, process owner epoch, or lease is no longer current, the proxy MUST
dispatch a fresh turn transparently when the request payload has an existing
safe replay proof, including account-neutral full-context resends and
proxy-injected anchor requests whose captured fresh body is replay-safe. The
client MUST receive the normal upstream stream for that fresh turn and MUST NOT
receive a bridge-specific recovery error.

When the request is bound to a client-provided anchor that cannot be safely
replayed as a fresh turn, the proxy MUST return the same OpenAI-compatible
`previous_response_not_found` error shape and HTTP status used by the existing
previous-response-not-found path. The proxy MUST NOT expose a
`bridge_continuity_recovery_required` code to clients. The proxy MUST keep the
existing retryable `stream_idle_timeout` semantics when the durable owner is
current and the failure is ordinary transient upstream silence.

#### Scenario: Previous-process anchor with replayable context recovers automatically

- **GIVEN** a request is bound to a durable previous-response anchor
- **AND** that durable row belongs to the same instance id but a different
  process owner epoch
- **AND** the payload has a safe full-context replay proof
- **WHEN** the bridge hits the pre-submit, startup-cooldown, or retry-circuit
  idle terminal path
- **THEN** the proxy dispatches the request as a fresh turn without the dead
  previous-response anchor
- **AND** the client receives the normal streaming response
- **AND** the response does not include `stream_idle_timeout` retry guidance or
  a bridge-specific recovery error

#### Scenario: Unreplayable client anchor uses the standard not-found contract

- **GIVEN** a request is bound to a client-provided durable previous-response
  anchor
- **AND** that durable row belongs to a dead owner
- **AND** the payload does not have a safe fresh-turn replay proof
- **WHEN** the bridge must fail closed
- **THEN** the client receives the standard `previous_response_not_found`
  error shape for `previous_response_id`
- **AND** HTTP error collection uses the standard previous-response-not-found
  status
- **AND** the response does not include a bridge-specific recovery code

#### Scenario: Current-owner silence remains retryable

- **GIVEN** a request is bound to a durable owner whose instance id, process
  owner epoch, and lease are current
- **WHEN** upstream produces no response events through the existing idle window
- **THEN** the proxy preserves the existing retryable `stream_idle_timeout`
  behavior

### Requirement: HTTP bridge model-transition isolation is single-pass

When an HTTP bridge request cannot reuse the session selected by its incoming affinity because that session uses an incompatible model, the service MUST preserve the resulting internal model-parallel key until bridge creation or reuse completes. It MUST NOT reapply the original session-header or turn-state fallback to the same request after selecting that fork.

#### Scenario: Fresh turn state falls back to a session on another model

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** lookup emits at most one model-transition fork for that request scope
- **AND** bridge creation continues under the internal key without closing or reusing the incompatible session

#### Scenario: Follow-up fallback has no previous-response lookup

- **GIVEN** a request carries a fresh generated turn-state header, a `previous_response_id` without a local or durable lookup, and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** the session-header fallback remains an anchored continuation for the rest of that lookup/create operation
- **AND** bridge creation continues under the internal key without a `continuity_lost` error

#### Scenario: Full cache preserves the incompatible parent

- **GIVEN** the HTTP bridge cache is at its session limit and a model transition isolates a session-header fallback into a child key
- **WHEN** creation needs to evict an idle session
- **THEN** the incompatible session-header parent MUST NOT be selected for that eviction
- **AND** ordinary LRU eviction remains eligible for other idle sessions

#### Scenario: In-flight parent completes before model isolation

- **GIVEN** a request waits for an in-flight session-header parent whose completed bridge uses an incompatible model
- **WHEN** the request isolates itself with an internal model-parallel key after that wait
- **THEN** the completed parent MUST receive the same capacity-eviction protection as an immediately available parent

#### Scenario: Compatible session fallback remains reusable

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses a compatible model
- **WHEN** lookup applies the session-header fallback
- **THEN** the compatible bridge remains eligible for normal reuse

### Requirement: Standalone Codex web search is forwarded faithfully

The proxy SHALL expose `POST /backend-api/codex/alpha/search` through the same
proxy-authenticated Codex control-request path used by other unary Codex control
endpoints. The proxy MUST preserve the inbound request body and query parameters,
MUST apply the existing API-key scope, account selection, token refresh, session
affinity, failover, and upstream-route policies, and MUST forward the request to
the upstream `POST /codex/alpha/search` path. Successful downstream responses
MUST preserve the upstream status and body and MUST include only response
headers allowed by the existing Codex control-response policy. Final non-2xx
responses MUST preserve their status while using the existing Codex control
OpenAI error-envelope normalization. The proxy MUST NOT parse, normalize, or
invent a local schema for successful search requests or responses.

#### Scenario: authenticated standalone search reaches the upstream Codex path

- **GIVEN** a valid proxy API key and at least one eligible ChatGPT account
- **WHEN** Codex sends `POST /backend-api/codex/alpha/search` with a JSON body and
  query parameters
- **THEN** the proxy forwards the unchanged body and query parameters to
  `POST /codex/alpha/search` using the selected account credentials
- **AND** the downstream client receives the upstream status and body

#### Scenario: unsafe upstream response headers are not exposed

- **WHEN** the upstream search response includes both allowlisted metadata and
  a response header outside the Codex control-response allowlist
- **THEN** the proxy returns the allowlisted metadata
- **AND** it omits the non-allowlisted response header

#### Scenario: final upstream search failures use the control error contract

- **WHEN** upstream search failure handling finishes with a non-2xx response
- **THEN** the proxy preserves the final HTTP status
- **AND** it returns the failure through the existing OpenAI error envelope
- **AND** existing account refresh, health, and failover handling remains active

#### Scenario: unsupported methods do not enter search forwarding

- **WHEN** a client sends a non-POST request to
  `/backend-api/codex/alpha/search`
- **THEN** the request does not enter the upstream search forwarding path

### Requirement: Pre-acceptance account-model rejections fail over safely

When upstream rejects a Responses request with `invalid_request_error` and the exact message `The '<model>' model is not supported when using Codex with a ChatGPT account.` before accepting the response, the proxy MUST classify the failure internally as `account_model_unsupported`. The quoted model MUST match
the requested model. For native WebSocket, HTTP responses bridge, and raw
HTTP/SSE transports, the proxy MUST make at most one transparent attempt on a
different account that advertises the same model, provided the request can move
without violating continuation or uploaded-file ownership. The proxy MUST
exclude the rejecting account only for that request and MUST NOT record an
account-health penalty for this rejection.

The proxy MUST NOT replay after any response id recognized in an upstream payload,
including a `response.failed` payload that carries `response.id` even when
`response.created` was not observed or an `error` payload with top-level
`response_id`, a nonterminal `response.*`
event, downstream sequence/output, another pending request on the shared
socket, or an earlier replay. If no compatible replacement is available, or
the request is account-bound, the proxy MUST preserve the original upstream
400 error instead of replacing it with `no_accounts`, `stream_incomplete`, or
another proxy-generated failure.

#### Scenario: stale model route retries another advertising account

- **GIVEN** two accounts advertise the requested model in the current routing snapshot
- **AND** upstream rejects the first account with the exact account/model unsupported envelope before `response.created`
- **WHEN** the request has no hard account or uploaded-file binding
- **THEN** the proxy excludes the first account for this request and retries once on the second account
- **AND** it forwards only the replacement attempt's response events downstream
- **AND** it does not penalize the first account's global health

#### Scenario: no replacement preserves the upstream rejection

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** no other compatible account is available
- **WHEN** transparent failover cannot select a replacement
- **THEN** the client receives the original HTTP 400 `invalid_request_error`
- **AND** the error is not rewritten to `no_accounts`, `stream_incomplete`, or HTTP 502

#### Scenario: selected replacement failure is surfaced

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** the proxy selects a different compatible replacement account
- **WHEN** that replacement attempt fails before acceptance
- **THEN** the client receives the replacement attempt's failure
- **AND** the skipped account's original HTTP 400 is not used as a fallback
- **AND** the proxy does not select a third account after a retryable replacement
  refresh, transport, or server failure

#### Scenario: failed bridge replacement retires without restoring rejected metadata

- **GIVEN** an HTTP responses bridge reconnect has selected and installed a
  replacement account after an account/model rejection
- **WHEN** replacement response-create lease acquisition or request send fails
- **THEN** the proxy forwards the replacement failure and retires that bridge
  session after draining the rejected request
- **AND** it does not restore the rejected account's turn state or headers onto
  the replacement socket

#### Scenario: accepted or visible request is never replayed

- **WHEN** the account/model unsupported envelope arrives after a response id, a nonterminal response event, downstream sequence/output, or an earlier replay
- **THEN** the proxy does not transparently replay the request on another account

#### Scenario: account-bound request is never migrated

- **WHEN** a rejected request depends on an account-scoped uploaded file or an owner-bound continuation without a verified self-contained fresh replay body
- **THEN** the proxy does not move the request to another account
- **AND** it preserves the original upstream rejection

### Requirement: Model-capacity messages are retryable transient failures

When upstream returns a temporary model-capacity failure whose message says that the selected model is at capacity, the proxy MUST treat the failure as retryable transient even if the upstream error code or HTTP status would otherwise look non-retryable.

#### Scenario: Selected model capacity with invalid request code is retryable

- **WHEN** upstream returns an error envelope with `error.message = "Selected model is at capacity. Please try a different model."`
- **AND** the normalized error code is `invalid_request_error`
- **AND** the HTTP status is `400`
- **THEN** `classify_upstream_failure` returns `failure_class = "retryable_transient"`
- **AND** pre-visible streaming/websocket paths are eligible to retry or fail over instead of surfacing a terminal client error.

#### Scenario: Serialized selected-model capacity event surfaces without replay

- **WHEN** a streaming Responses request receives a first upstream `response.failed` or `error` event whose message says the selected model is at capacity
- **AND** no downstream-visible output has been emitted
- **THEN** the proxy MUST surface that terminal event without transparently re-POSTing the request
- **AND** the absence of an upstream response id MUST NOT by itself prove the POST was safe to replay.

#### Scenario: Post-connect body-read disconnect is not replayed as capacity retry

- **WHEN** a streaming Responses request fails while reading the upstream stream body after the upstream request has been dispatched
- **AND** the failure is an `aiohttp` client error, timeout, EOF, or other transport/body-read close without typed pre-dispatch provenance
- **THEN** the proxy MUST surface the stream failure to the downstream client
- **AND** the proxy MUST NOT transparently re-POST the request as a model-capacity retry.

#### Scenario: Websocket connect failure retries before request dispatch

- **WHEN** an upstream websocket handshake raises a typed connector failure or connect timeout before the `response.create` frame is sent
- **THEN** the proxy MUST preserve typed pre-dispatch provenance and MAY retry or fail over before any downstream-visible output
- **AND** a websocket transport selection MUST NOT turn that failure into a terminal serialized SSE event.

#### Scenario: Direct HTTP TLS verification failure is not retried

- **WHEN** a direct HTTP stream raises a certificate or TLS connector failure before request dispatch
- **THEN** the proxy MUST surface the TLS failure without transparently retrying or failing over
- **AND** pre-dispatch provenance MUST NOT classify the non-transient TLS failure as retryable.

#### Scenario: Quota and rate-limit codes retain their stronger classification

- **WHEN** upstream returns a quota or rate-limit error code
- **THEN** the proxy MUST keep classifying it as quota or rate-limit before applying message-based model-capacity detection.

#### Scenario: Post-refresh transient exhaustion preserves every health signal

- **WHEN** one or more accounts each exhaust multiple same-account post-refresh transient retries before the request succeeds or terminates
- **THEN** the proxy MUST settle API-key usage before recording any deferred account-health failure
- **AND** each exhausted account MUST receive exactly one classified health failure plus one additional failure for every remaining exhausted retry
- **AND** selecting or exhausting a later account MUST NOT replace, lose, or duplicate an earlier account's deferred failures.

#### Scenario: Classified quota failures still use the model-capacity replay wait

- **WHEN** a replayable pre-created HTTP bridge request receives the selected-model capacity message with a quota or
  rate-limit error code
- **THEN** the proxy MUST preserve that quota or rate-limit classification for account health handling
- **AND** the proxy MUST still apply the model-capacity wait before replaying the request.

### Requirement: HTTP bridge model-capacity retry waits preserve stream contracts

The proxy MUST wait before replaying an HTTP bridge request with a selected-model capacity failure only when the
failure happened before any downstream-visible model output and the request is still replayable as a fresh request.
An already-forwarded `response.created` or `response.in_progress` does not disqualify the replay; the replay's
duplicate lifecycle prelude MUST be suppressed so the client observes exactly one `response.created`.

#### Scenario: Public propagated-error streams do not receive pre-retry keepalives

- **WHEN** a `/v1/responses`-compatible HTTP bridge stream is configured to propagate startup HTTP errors
- **AND** upstream returns a selected-model capacity error before `response.created`
- **THEN** the proxy MUST NOT emit `codex.keepalive` or account-capacity wait events before the retry completes.

#### Scenario: Accepted public streams are replayed without keepalives

- **WHEN** a `/v1/responses`-compatible HTTP bridge stream has already forwarded `response.created`
- **AND** upstream fails the response output-free with a selected-model capacity error
- **THEN** the proxy MUST replay the request within the single lifecycle without emitting `codex.keepalive` frames
- **AND** the proxy MUST NOT re-signal the pre-response startup wait for that request.

#### Scenario: Replay waits remain bounded by the original bridge deadline

- **WHEN** the selected-model capacity error arrives near or after the original bridge request deadline
- **THEN** the proxy MUST NOT start a fresh upstream replay after that deadline is exhausted.

#### Scenario: Only fresh replayable bridge requests wait

- **WHEN** the selected-model capacity error belongs to an anchored request that cannot be replayed without
  `previous_response_id`
- **THEN** the proxy MUST forward the terminal error promptly without sleeping for the model-capacity retry delay.

#### Scenario: Retry-safe injected anchors still wait

- **WHEN** the proxy injected `previous_response_id` and retained a fresh request body that is safe to replay without
  that anchor
- **AND** upstream returns a selected-model capacity error before visible output
- **THEN** the proxy MUST apply the model-capacity wait before stripping the injected anchor and replaying the fresh
  request.

#### Scenario: Accepted client-anchored requests forward the capacity message without waiting

- **WHEN** an accepted request (`response.created` forwarded) carries a `previous_response_id` the client supplied,
  even with a retry-safe fresh body retained
- **AND** upstream returns a selected-model capacity error before visible output
- **THEN** the proxy MUST NOT reserve, stage, or wait for that request
- **AND** the upstream terminal MUST be forwarded unchanged, exactly as the bare transparent-code branch forwards it.

#### Scenario: Remote-owner relay preserves the hidden startup wait

- **WHEN** an origin replica forwards a bridge request to its remote owner
- **THEN** the origin MUST keep its startup probe pending until the owner relay returns response headers or a terminal
  startup error
- **AND** a selected-model capacity wait on the owner MUST NOT cause the origin to commit HTTP 200 before that wait
  completes.

#### Scenario: Waiting keeps the retry tied to the pending request

- **WHEN** the proxy waits before replaying a selected-model capacity failure
- **THEN** the request MUST remain reserved in the bridge pending queue while it waits
- **AND** the proxy MUST retain the session response-create gate so a younger request cannot enter while the sole
  upstream reader is sleeping
- **AND** the proxy MUST release account-level and shared response-create capacity during the wait
- **AND** the proxy MUST reacquire both capacity leases before sending the replay
- **AND** the proxy MUST skip the replay if that queued request detaches before the wait completes
- **AND** when the wait branch gives that pending ownership up without a successful replay (the replay was refused or
  failed, or the session gate could not be re-claimed) it MUST record the request's terminal settlement claim, so an
  abort before finalization still settles the API-key reservation through the shielded abort settlement instead of
  orphaning the reservation, its heartbeat, and the re-claimed session gate.

#### Scenario: Accepted requests re-claim the session gate before waiting

- **WHEN** the selected-model capacity failure belongs to a request that already forwarded `response.created`
- **THEN** the proxy MUST re-claim the session response-create gate without waiting before it waits and replays
- **AND** if another `response.create` holds that gate the proxy MUST forward the upstream terminal unchanged.

### Requirement: WebSocket stale-anchor failures include diagnostic metadata
When a direct Responses WebSocket request fails closed because upstream rejects `previous_response_id` with `previous_response_not_found`, the service MUST emit stale-anchor diagnostic metadata in operator logs and request-log failure metadata. The metadata MUST distinguish `previous_response_source` (`client_supplied`, `proxy_injected`, or `unknown`), whether a fresh no-anchor replay body was available, owner lookup outcome/source, whether the matched previous response belongs to the same Codex session when known, and the previous-response age in seconds when known. The metadata MUST NOT expose raw `previous_response_id` values or request payload content.

#### Scenario: client-supplied stale anchor is classifiable
- **GIVEN** a direct WebSocket request arrives with a client-supplied `previous_response_id`
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=client_supplied`
- **AND** they include owner lookup and replay-availability metadata without raw response ids

#### Scenario: proxy-injected stale anchor is classifiable
- **GIVEN** codex-lb injects a session-continuity `previous_response_id` into a direct WebSocket request
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=proxy_injected`
- **AND** they state whether a retry-safe fresh no-anchor replay body was available
- **AND** owner lookup, age, and same-session fields remain explicit as `unknown` when unavailable rather than being omitted

#### Scenario: stale anchor owner hit records age and session relationship
- **GIVEN** owner lookup finds a previous response row for the rejected anchor
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics include the owner lookup source
- **AND** include previous-response age seconds and same-session status when those values can be derived

#### Scenario: account-only cache hits do not guess owner session metadata
- **GIVEN** owner resolution hits a request cache entry that retains the account id but not the matched request-log row
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics identify the owner lookup source as the request cache
- **AND** leave previous-response age and same-session status unknown rather than inferring them from the current request scope

### Requirement: Responses HTTP ingress uses the expanded bounded budget

HTTP requests to `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants, MUST use the larger of the general HTTP body budget (`MAX_DECOMPRESSED_BODY_BYTES`, 32 MiB) and the Responses body budget (`MAX_DECOMPRESSED_RESPONSES_BODY_BYTES`, 128 MiB) as both the raw-body and decompressed-body ingress budget. Both budgets are fixed application constants in `app/core/ingress_limits.py` and MUST NOT be operator-configurable; the Responses budget MUST remain 128 MiB and MUST be the same constant that seeds the downstream websocket `--ws-max-size` default.

The trailing-slash variants MUST be hidden aliases of the canonical HTTP handlers rather than redirects, so streamed bodies receive the same admission, authorization, and route behavior.

If either representation exceeds that budget, the service MUST stop before route logic or upstream forwarding and return HTTP 413 with an OpenAI-compatible error envelope carrying `error.code = payload_too_large` and `error.type = invalid_request_error`.

This transport-ingress 413 applies before parsing and is distinct from the existing application-level oversized-`response.create` guard. A request that fits the 128 MiB transport budget but still exceeds the upstream websocket budget after historical slimming MUST retain the existing HTTP 400 `payload_too_large` behavior and `param = input`.

#### Scenario: Larger Responses request fits both ingress checks

- **WHEN** a Responses HTTP request is larger than the general budget but no larger than the Responses budget in either raw or decompressed form
- **THEN** the ingress guards allow the request to continue to Responses route handling

#### Scenario: Trailing-slash Responses request is admitted without redirect

- **WHEN** a client sends a chunked HTTP request to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** the service applies the same ingress budget and handler as the corresponding canonical path
- **AND** it does not return a trailing-slash redirect before consuming the guarded body

#### Scenario: Responses raw body exceeds its budget

- **WHEN** a Responses HTTP request's raw body exceeds the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Responses expanded body exceeds its budget

- **WHEN** an encoded Responses HTTP request fits the raw budget but expands beyond the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Post-slimming application rejection remains 400

- **WHEN** a Responses HTTP request fits the raw and decompressed transport-ingress budget
- **AND** its serialized `response.create` still exceeds the upstream websocket budget after historical slimming
- **THEN** the existing application-level guard returns HTTP 400 with `error.code = payload_too_large`, `error.type = invalid_request_error`, and `error.param = input`

### Requirement: Thread-goal OpenAPI operations have unique stable identifiers
The generated OpenAPI document MUST assign a unique `operationId` to every documented HTTP operation. The GET and POST operations at `/backend-api/codex/thread/goal/get` MUST remain available through the same runtime behavior and MUST expose the deterministic identifiers `thread_goal_get_backend_api_codex_thread_goal_get_get` and `thread_goal_get_backend_api_codex_thread_goal_get_post`, respectively. Correcting this schema metadata MUST NOT change either method's authentication, dependency, request forwarding, upstream operation, response status, or response payload behavior.

#### Scenario: Full OpenAPI schema has unique operation identifiers
- **WHEN** an unauthenticated client requests `GET /openapi.json`
- **THEN** every documented HTTP operation has an `operationId`
- **AND** no two documented HTTP operations share an `operationId`

#### Scenario: Thread-goal methods publish deterministic identifiers
- **WHEN** an unauthenticated client inspects `/openapi.json`
- **THEN** `GET /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_get`
- **AND** `POST /backend-api/codex/thread/goal/get` has `operationId` `thread_goal_get_backend_api_codex_thread_goal_get_post`

#### Scenario: Thread-goal runtime forwarding remains compatible
- **WHEN** a client invokes either GET or POST `/backend-api/codex/thread/goal/get` with valid existing dependencies
- **THEN** the request is forwarded through the existing thread-goal handler using the original request method
- **AND** the upstream operation, response status, and response payload remain unchanged

### Requirement: Public synthetic Responses failures carry numeric sequences

Public streaming `POST /v1/responses` MUST emit every terminal
`response.failed` with a finite integer `sequence_number` so
strict OpenAI SDK Responses parsers recognize the terminal failure. If the
upstream or proxy-generated event omits a finite integer sequence, the public
normalizer MUST assign the next sequence after all finite integer sequences it
has observed in the same downstream stream. If it also synthesizes a leading
`response.created` from that failure, the created event MUST consume the next
sequence and the failure MUST use the following sequence so both events have
distinct values. Otherwise, if no finite integer sequence has been observed,
failure numbering MUST begin at zero.

The public normalizer MUST preserve an existing finite integer
`sequence_number` and advance its next-sequence watermark accordingly. This
repair MUST NOT change Codex-private backend stream shapes.

#### Scenario: Bridge failure after reasoning remains parseable

- **GIVEN** public `/v1/responses` has emitted sequenced reasoning events
- **WHEN** the upstream bridge closes before a terminal response
- **THEN** the downstream terminal `response.failed` carries the next numeric
  `sequence_number`
- **AND** a strict OpenAI SDK parser recognizes it as a terminal failure

#### Scenario: Leading failure follows synthesized created sequence

- **GIVEN** public `/v1/responses` has not emitted a finite integer sequence
- **WHEN** an unsequenced leading `response.failed` requires a synthesized
  `response.created`
- **THEN** the created event carries `sequence_number = 0`
- **AND** the terminal failure carries `sequence_number = 1`

#### Scenario: Failure after an unsequenced created event starts at zero

- **GIVEN** public `/v1/responses` has emitted an unsequenced
  `response.created` and no finite integer sequence
- **WHEN** the proxy synthesizes a terminal `response.failed`
- **THEN** the terminal event carries `sequence_number = 0`

#### Scenario: Valid upstream failure sequence remains unchanged

- **GIVEN** an upstream terminal `response.failed` carries a finite integer
  `sequence_number`
- **WHEN** the public normalizer forwards the event
- **THEN** it preserves that sequence number unchanged
- **AND** if it must synthesize a leading `response.created`, that event uses
  the immediately preceding integer sequence

#### Scenario: Backend Codex stream shape remains unchanged

- **GIVEN** a Codex-private backend Responses stream carries an unsequenced
  terminal failure
- **WHEN** the stream is served without the public OpenAI SDK contract
- **THEN** the proxy does not add a public compatibility sequence

### Requirement: Direct WebSocket capability intent is trusted and private

A direct Responses WebSocket MUST recognize the exact internal marker
`X-Codex-LB-Required-Capability: trusted_cyber` only after successful existing
proxy API-key authentication. It MUST accept one marker from either the
handshake headers or the current `response.create.client_metadata`. Duplicate,
conflicting, non-string, unknown, malformed, or unauthenticated signals MUST
fail before account selection. Raw duplicate JSON keys or duplicate
`client_metadata` containers MUST NOT collapse into an ordinary request. The
marker MUST be rejected on every downstream frame type other than
`response.create`.

The proxy MUST remove the capability header and the exact consumed metadata
key before upstream dispatch, request archival, diagnostics, and logging.
Unrelated client metadata MUST remain unchanged.

#### Scenario: Per-frame intent routes before upstream open
- **WHEN** an authenticated frame carries the exact metadata marker on a
  downstream socket opened without the header
- **THEN** the proxy establishes REQUIRED before opening or reusing an upstream
  socket

#### Scenario: Ambiguous or untrusted signal fails closed
- **WHEN** a signal is duplicated, malformed, unknown, or lacks an authenticated
  proxy API-key principal
- **THEN** the proxy returns a typed error before account or model-source
  dispatch

#### Scenario: Duplicate JSON cannot erase intent
- **WHEN** raw JSON repeats the capability key or repeats `client_metadata`
  around a capability marker
- **THEN** the proxy returns the typed unsupported-capability error before
  selection

#### Scenario: Capability metadata on another frame is rejected
- **WHEN** a downstream frame other than `response.create` contains the
  capability metadata key
- **THEN** the proxy returns a typed error without forwarding or archiving that
  frame upstream
- **AND** malformed JSON text is rejected rather than passed through an already
  open upstream socket
- **AND** binary downstream frames are rejected before parsing, archiving, or
  upstream forwarding

#### Scenario: Internal metadata is not forwarded or archived
- **WHEN** a valid capability-bearing frame is dispatched and archived
- **THEN** neither capability carrier appears in upstream headers, upstream
  payload, archive payload, diagnostics, or logs

### Requirement: A late capability cannot reuse an ordinary upstream socket

A later REQUIRED frame MUST NOT reuse an upstream socket selected for an
ordinary request on the same downstream WebSocket. An idle ordinary socket
MUST be retired before capable
reselection. If another frame is still pending, the proxy MUST fail closed
rather than change the account requirement beneath in-flight work. The socket's
selection contract, not whether its account happened to have the capability
grant, MUST determine whether it was selected as ordinary. Before reusing a
REQUIRED-selected socket, the proxy MUST revalidate the pinned account and its
current capability grant through the canonical selector.

#### Scenario: Idle ordinary socket is replaced
- **WHEN** an idle downstream session previously selected an ordinary account
  and a later frame establishes REQUIRED
- **THEN** the ordinary upstream is retired before the frame is sent
- **AND** the replacement selection requires a security-work-authorized account

#### Scenario: Pending ordinary work blocks a requirement change
- **WHEN** ordinary work is still pending and a later frame establishes REQUIRED
- **THEN** the later frame fails before upstream send
- **AND** the pending frame's account and request state are not rewritten

#### Scenario: Revoked capability grant prevents socket reuse
- **WHEN** a socket was selected for REQUIRED but its pinned account's grant is
  no longer valid at canonical revalidation
- **THEN** the stale socket does not receive the next REQUIRED frame
- **AND** an idle socket is retired before constrained reselection

#### Scenario: Revalidation uncertainty fails closed
- **WHEN** canonical account revalidation cannot complete for a REQUIRED socket
- **THEN** the frame receives a typed capability-routing-unavailable error
- **AND** its reservation is settled without forwarding the frame upstream

### Requirement: Proof-gated recovery attempts are durably fenced

When an HTTP bridge request has a verified, account-neutral, unanchored full
resend body, the proxy MUST record that request fingerprint in the durable
recovery journal before dispatching it upstream. The record MUST be owned by
the current durable session owner epoch and MUST start in `unknown` state.
Requests without that replay-safety proof MUST NOT create a recovery-journal
record.

#### Scenario: Safe resend is journaled before dispatch

- **GIVEN** a request has a verified full-resend body that is safe to replay
  without `previous_response_id`
- **WHEN** the proxy admits the request for upstream dispatch
- **THEN** the durable journal contains one `unknown` record for its session
  and request fingerprint before `response.create` is sent

#### Scenario: Suppressed request is not journaled

- **GIVEN** a hard session retry circuit is cooling down
- **WHEN** the request is rejected before upstream dispatch
- **THEN** no recovery-journal record is created or refreshed

### Requirement: Durable replay is limited to ambiguous transport outcomes

The proxy MUST consume an `unknown` recovery-journal record for a fresh
account-neutral replay only after an ambiguous transport outcome, represented
by `stream_incomplete`, `stream_idle_timeout`, or
`upstream_request_timeout`, and only before any response event or downstream
output. Explicit deterministic `response.failed` errors MUST settle normally
and MUST NOT trigger a cross-account replay or consume the recovery fence.

#### Scenario: Transport ambiguity permits one replay

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** the upstream closes or times out before any response event
- **WHEN** the bridge handles the ambiguous transport failure
- **THEN** the record is atomically claimed and the request is replayed once
  on a fresh account-neutral upstream session

#### Scenario: Deterministic failure is not replayed

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** upstream emits an explicit pre-output `response.failed` such as an
  invalid request or quota rejection
- **WHEN** the bridge handles that terminal event
- **THEN** it forwards the terminal failure
- **AND** it leaves the journal available for settlement without replaying on
  another account

### Requirement: Recovery journal settlement is owner-fenced and idempotent

After a replayed request reaches `response.completed`, the proxy MUST mark its
journal record `replayed` only through the current durable owner epoch and
MUST retain the downstream response id when available. Repeated settlement,
stale owners, and concurrent claim attempts MUST NOT produce a second replay.
The migration MUST be on the current Alembic head and startup schema checks
MUST require the journal table.

#### Scenario: Completed replay settles once

- **GIVEN** a replayed request completes successfully
- **WHEN** the completion event is processed
- **THEN** the matching journal record becomes `replayed`
- **AND** a later retry cannot claim it again

#### Scenario: Stale owner cannot settle or replay

- **GIVEN** a journal record belongs to a newer durable owner epoch
- **WHEN** an old replica attempts settlement or replay
- **THEN** the operation is rejected without changing the record state

### Requirement: Claimed HTTP bridge completed queues remain deliverable

When HTTP bridge processing of `response.completed` removes a request from
pending ownership, it MUST retain the request's downstream event queue for the
remainder of that completed operation. Later asynchronous bookkeeping or
request detachment MUST NOT revoke that claimed queue before the completed
operation's selected terminal event and end-of-stream marker are enqueued. If
fail-closed bookkeeping replaces the upstream completion with a terminal
failure, that selected failure event is the terminal event governed by this
requirement.

While the claimed completed-delivery operation remains active, ordinary stream
idle accounting MUST NOT replace the upstream completion with a synthetic idle
failure, and the stream MUST continue emitting its existing liveness frames.
The completed-queue claim and the terminal idle-timeout decision MUST be
serialized under the bridge pending lock. If completed processing wins that
serialization and claims a live queue, the timeout MUST be suppressed. If the
terminal event and end-of-stream marker are already queued when a concurrent
timeout finishes awaited recovery work, the completed claim MUST remain
authoritative until the stream consumes that queued delivery. If the
terminal idle timeout wins while no completed delivery is active, it MUST
revoke the request's mutable event queue before releasing the pending lock so a
later completed event cannot claim an orphaned queue.

The first idle-timeout suppression for one completed-delivery operation MUST
emit one bounded diagnostic containing the request ID, downstream response ID,
and elapsed seconds. Further liveness intervals for that same operation MUST
NOT repeat the diagnostic.

When that operation returns, raises, or is cancelled before delivery, idle
timeout behavior MUST resume.

If detachment removes the request from pending ownership first, existing
client-disconnect and drain behavior MUST remain unchanged.

#### Scenario: Completed processing claims the request before detachment

- **GIVEN** an HTTP bridge stream is waiting on its request event queue
- **AND** an upstream `response.completed` event removes that request from pending ownership
- **WHEN** request detachment overlaps later completed-event bookkeeping
- **THEN** the stream receives the terminal event selected for downstream delivery exactly once
- **AND** the stream receives its end-of-stream marker

#### Scenario: Completed bookkeeping exceeds the idle window

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** later completed bookkeeping exceeds the configured stream idle window
- **THEN** the stream continues emitting liveness frames
- **AND** it does not emit a synthetic idle failure while that operation remains active
- **AND** it logs the suppression once with request, response, and elapsed-time context

#### Scenario: Terminal idle timeout wins before completed processing

- **GIVEN** an HTTP bridge stream has exhausted its configured idle window
- **AND** no completed-delivery operation has claimed its queue
- **WHEN** the stream acquires the bridge pending lock before a concurrent completed event
- **THEN** it revokes the mutable event queue while still holding that lock
- **AND** it emits the existing synthetic idle failure
- **AND** later completed processing does not deliver to the revoked queue

#### Scenario: Completed delivery finishes during timeout recovery

- **GIVEN** an HTTP bridge timeout path is awaiting pre-response recovery work
- **AND** completed processing claims the live queue and enqueues its terminal event and end-of-stream marker
- **WHEN** completed processing returns before the timeout path rechecks ownership
- **THEN** the completed claim remains authoritative
- **AND** the stream consumes the queued completion without emitting a synthetic idle failure

#### Scenario: Completed bookkeeping aborts

- **GIVEN** completed-event processing has claimed a live request queue
- **WHEN** that completed-delivery operation exits without enqueueing its terminal event
- **THEN** idle timeout suppression ends
- **AND** the existing idle-timeout failure behavior resumes

#### Scenario: Detachment claims the request first

- **GIVEN** an HTTP bridge request is still pending
- **WHEN** detachment removes downstream queue ownership before completed-event matching
- **THEN** existing client-disconnect and upstream-drain behavior is preserved
- **AND** no completed event is delivered to another request

### Requirement: Replayed tool-call namespace metadata is local-only on upstream input

For standard and compact Responses requests, the proxy MUST omit `namespace` from every replayed `input` item whose `type` is `function_call`, `custom_tool_call`, or `apply_patch_call` before forwarding the request upstream. The proxy MUST preserve all other fields on that item, MUST retain the original namespace metadata for local call-identity and replay-deduplication processing, and MUST NOT alter client-provided top-level tool entries as part of this normalization.

#### Scenario: Standard Responses replay omits tool-call namespaces upstream

- **WHEN** a standard Responses request replays `function_call` and `custom_tool_call` input items with `namespace`
- **THEN** the upstream payload omits only those items' `namespace`
- **AND** preserves their remaining call fields
- **AND** the local request input retains the namespace metadata

#### Scenario: Compact Responses replay omits tool-call namespace upstream

- **WHEN** `/v1/responses/compact` replays a recognized tool-call input item with a namespace
- **THEN** its upstream payload omits the input item's `namespace`
- **AND** preserves the remaining tool-call fields

#### Scenario: WebSocket response.create omits tool-call namespaces upstream

- **WHEN** a Responses WebSocket request replays namespaced `function_call` and `custom_tool_call` input items
- **THEN** the upstream `response.create` frame omits only those items' `namespace`
- **AND** preserves their remaining call fields

#### Scenario: Configured Responses model source omits tool-call namespaces upstream

- **WHEN** `/v1/responses` routes a replayed namespaced tool call to a configured OpenAI-compatible Responses model source
- **THEN** the source payload omits only the call item's `namespace`
- **AND** preserves source-compatible request fields that the Codex upstream path does not support

#### Scenario: Account-neutral replay classification retains namespace identity

- **WHEN** an HTTP bridge evaluates a namespaced tool-call history for cross-account replay safety
- **THEN** the classifier input retains the namespace metadata
- **AND** the request fails closed rather than becoming account-neutral because of wire normalization

#### Scenario: Malformed replay item type does not fail serialization

- **WHEN** a permissively parsed input item has a non-string `type` and a `namespace`
- **THEN** outbound serialization does not raise an internal type error
- **AND** does not treat the item as a recognized replayed tool call

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is `namespace`
- **THEN** standard Responses serialization forwards that tool entry byte-identically

### Requirement: Responses-Lite replay proof tolerates only verified developer interleaving

When a fresh durable HTTP bridge classifies a client-unanchored Responses-Lite
full resend whose `additional_tools` bundle preserves developer messages inline,
the replay proof MUST tolerate a developer message only in the historical and
fresh positions defined below. Every other developer position or shape MUST
remain fail-closed.

A tolerated fresh developer message MUST have `type` omitted or equal to `message`,
MUST have role `developer`, MUST have no non-empty response-owned ID or phase,
MUST have no status or a `completed` status, MUST contain exact account-neutral
metadata with one nonblank `turn_id`, MUST contain exactly one self-contained
`input_text` content part, and MUST contain no unknown or account-scoped fields.
Explicit null or malformed item types MUST fail closed.

Classification MUST retain response-owned developer-message ID evidence until
these checks have completed, even when other response-owned IDs are projected
out. It MUST retain developer-role items before applying projection rules that
normally omit their declared item type, so a malformed developer item cannot
disappear before validation. A canonical Lite-prefix developer instruction MAY
appear immediately after the `additional_tools` bundle when it passes the same
account-neutral item checks as historical interleaving and has no response-owned
ID. A developer message in the stored prefix outside that canonical position or
the verified pending-call/matching-output interleave MUST fail closed. Non-Lite
`input` or `messages` forms whose instruction-role messages are normalized into
top-level `instructions` remain outside this requirement.

#### Scenario: Canonical Responses-Lite prefix remains transparent

- **GIVEN** a fingerprint-verified stored prefix begins with an `additional_tools` bundle
- **AND** a valid account-neutral developer instruction appears immediately after that bundle
- **WHEN** exact manifest or retained-output replay proof validates the stored prefix
- **THEN** the canonical developer instruction is transparent
- **AND** the original full input remains eligible for account-neutral replay

#### Scenario: Verified historical Responses-Lite developer message is transparent

- **GIVEN** a Responses-Lite input contains an `additional_tools` bundle
- **AND** its fingerprint-verified stored prefix contains a supported direct call
- **AND** a valid developer message appears before that call's matching output
- **AND** the fresh suffix exactly settles the durable pending-tool manifest
- **WHEN** the HTTP bridge opens a replacement session on the durable owner
- **THEN** it sends the original full input without injecting `previous_response_id`
- **AND** it sends the request once

#### Scenario: Other historical messages remain fail-closed

- **GIVEN** a supported direct call is pending in the verified stored prefix
- **WHEN** a user, assistant, system, malformed developer, or response-owned message appears before its output
- **THEN** exact manifest proof fails

#### Scenario: Other stored developer positions remain fail-closed

- **GIVEN** a fingerprint-verified stored prefix has no pending direct call
- **WHEN** a developer message appears outside the canonical adjacent Lite-prefix position
- **OR** the adjacent message has a response-owned ID
- **THEN** exact manifest and retained-output proofs fail

#### Scenario: Projection-omitted developer type remains visible to validation

- **GIVEN** a developer-role item declares a type normally omitted by replay projection
- **WHEN** account-neutral replay classification projects the full resend
- **THEN** the malformed developer item remains visible to replay proof
- **AND** replay classification fails closed

#### Scenario: Historical output remains mandatory

- **GIVEN** a valid developer message follows a supported historical call
- **WHEN** the matching output is missing or has another call ID or type
- **THEN** exact manifest proof fails

#### Scenario: Historical developer interleaving is bounded to one call and one message

- **GIVEN** a fingerprint-verified stored prefix opens a pending direct-call window
- **WHEN** that window holds more than one outstanding call at any point before the developer message
- **OR** a further call opens in that window after it has consumed a developer message
- **OR** a second developer message appears while the same window is still open
- **THEN** exact manifest proof fails
- **AND** a later window that holds exactly one outstanding call may still interleave one developer message

#### Scenario: Fresh developer suffix bounds are measured on the projected input

- **GIVEN** account-neutral replay classification projects the full resend
- **WHEN** the projection omits reasoning or completed bookkeeping items from the fresh suffix
- **THEN** the fresh developer suffix and terminality bounds are evaluated on the projected positions
- **AND** the accepted width is limited to shapes whose projected suffix satisfies those bounds

#### Scenario: Bounded fresh custom-tool developer interleave is transparent

- **GIVEN** the fingerprint-verified stored prefix is followed by a fresh suffix
- **AND** the durable pending-tool manifest contains exactly one `custom_tool_call`
- **WHEN** the entire suffix is exactly that custom call, one valid developer message, and its matching custom-tool output
- **THEN** exact manifest proof passes
- **AND** the original full input is sent once without injecting `previous_response_id`

#### Scenario: Other fresh tool-loop developer positions remain fail-closed

- **GIVEN** a durable pending-tool manifest
- **WHEN** a fresh developer message is used with a function or apply-patch call, appears in a parallel batch, is duplicated, lacks exact metadata, contains malformed or account-scoped content, or has leading or trailing suffix items
- **THEN** exact manifest proof fails

#### Scenario: Bounded retained-output developer follow-up is transparent

- **GIVEN** the fingerprint-verified stored prefix is followed by a completed assistant `final_answer`
- **AND** exactly one explicit user message follows that retained output
- **WHEN** one valid developer message is the terminal suffix item
- **THEN** retained-output proof passes
- **AND** the original full input is sent once without injecting `previous_response_id`

#### Scenario: Unproven retained-output developer follow-up remains fail-closed

- **GIVEN** a retained-output full resend
- **WHEN** the latest assistant output is not `final_answer`, the developer message is not terminal, the fresh input is raw or contains multiple user items, the developer metadata or content is not account-neutral, or the stored prefix contains historical developer interleaving
- **THEN** retained-output proof fails

### Requirement: Aborted terminal bookkeeping settles claimed reservations exactly once

The HTTP bridge MUST settle a request's API-key reservation exactly once even
when terminal-event bookkeeping aborts after removing the request from pending
ownership; that bookkeeping continuation exclusively owns the settlement. If
the continuation raises or is cancelled before finalization transfers that
settlement, the abort path MUST settle every request it still owns: the
reservation heartbeat MUST be cancelled, the
reservation MUST be released, and the downstream waiter SHOULD be unblocked
with an end-of-stream marker instead of waiting for its idle timeout. The
abort settlement MUST run to completion under cancellation (shielded), MUST
apply to the grouped previous-response error path's not-yet-finalized
remainder, and MUST NOT settle requests that a retry branch restored to
pending ownership. Settlement MUST remain idempotent so an abort overlapping
an already-transferred finalization cannot double-account usage.

If the abort settlement itself fails, the claim MUST be marked abandoned and
request detachment MUST be allowed to reclaim that settlement even though the
request is no longer in pending ownership. Detachment MUST NOT settle a live
claim whose bookkeeping continuation is still running.

#### Scenario: Completed bookkeeping raises after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** later completed bookkeeping raises before finalization
- **THEN** the reservation heartbeat task finishes
- **AND** the API-key reservation is released exactly once
- **AND** no reservation heartbeat touch runs afterward

#### Scenario: Completed bookkeeping is cancelled after the pending pop

- **GIVEN** an upstream `response.completed` event has removed a request with an API-key reservation from pending ownership
- **WHEN** the bookkeeping continuation is cancelled before finalization
- **THEN** the shielded abort settlement still cancels the heartbeat and releases the reservation
- **AND** the cancellation is re-raised after settlement

#### Scenario: Grouped previous-response finalization aborts mid-loop

- **GIVEN** a grouped previous-response error has removed multiple requests from pending ownership
- **WHEN** finalization aborts after settling only a prefix of those requests
- **THEN** every not-yet-finalized request in the group has its heartbeat cancelled and its reservation released

#### Scenario: Detachment reclaims an abandoned claim

- **GIVEN** terminal bookkeeping claimed a request out of pending ownership, aborted, and its abort settlement failed
- **WHEN** the downstream stream detaches that request
- **THEN** detachment cancels the heartbeat and releases the reservation even though the request is not in pending ownership

#### Scenario: Detachment leaves a live claim to its owner

- **GIVEN** terminal bookkeeping has claimed a request out of pending ownership and is still running
- **WHEN** the downstream stream detaches that request
- **THEN** detachment does not release the reservation out from under the in-flight finalization

### Requirement: Pool usage exhaustion is reported as a usage-limit error

The proxy MUST report pool-wide Responses usage exhaustion as a usage-limit
error. When every account eligible for a Responses request is exhausted by known
usage windows, the proxy MUST reject the request with HTTP `429` and an
OpenAI-style error envelope whose `error.code` and `error.type` are both
`usage_limit_reached`. If account selection has an authoritative upstream reset
timestamp for the exhausted pool, the response envelope MUST include that
timestamp as `error.resets_at`; the proxy MUST NOT expose the capped
human-facing retry hint or a synthesized fallback as `error.resets_at`. The
proxy MUST NOT collapse this condition into generic `no_accounts`,
`server_error`, or HTTP `503` semantics. Exhaustion classification MUST be
based on structured account state after the same eligibility filtering as
ordinary selection, and MUST NOT reclassify local capacity or overload codes
(account caps, admission gates, fair-share throttles) as usage exhaustion.

#### Scenario: Public Responses request exhausts the eligible usage pool

- **WHEN** account selection for a public `/v1/responses` or
  `/backend-api/codex/responses` request finds only usage-exhausted eligible
  accounts
- **THEN** the response status is HTTP `429`
- **AND** the response body has `error.code = "usage_limit_reached"`
- **AND** the response body has `error.type = "usage_limit_reached"`
- **AND** any selected pool reset timestamp is surfaced as `error.resets_at`

#### Scenario: Streaming selection failure preserves usage-limit semantics

- **WHEN** a streaming Responses request cannot select an account because every
  eligible account is usage-exhausted before downstream-visible output
- **THEN** the terminal error event uses `usage_limit_reached`
- **AND** clients do not receive a generic no-account/server-unavailable error

#### Scenario: Usage-limit selection failures are terminal, not waitable

- **WHEN** account selection fails with `usage_limit_reached` on a streaming,
  HTTP-bridge, or WebSocket Responses path
- **THEN** the proxy reports the structured usage-limit failure immediately
- **AND** it does not enter an account-capacity recovery wait for the
  remaining request budget before reporting it

#### Scenario: HTTP bridge retry loops do not wait on the usage-limit retry hint

- **GIVEN** HTTP bridge session creation or submission fails with
  `usage_limit_reached` whose message carries the selector's capped retry hint
  (`Rate limit exceeded. Try again in Ns`) because the exhausted pool's
  earliest reset is known
- **WHEN** the bridge retry loop evaluates its account-capacity wait plan
- **THEN** it derives no wait from that hint, keyed on the structured error
  code rather than the message text
- **AND** it emits no `codex.keepalive` with status
  `waiting_for_account_capacity` and consumes none of the bridge request budget
- **AND** it returns the HTTP `429` `usage_limit_reached` envelope immediately
  with `error.resets_at` and without a `Retry-After` header
- **AND** recoverable codes such as upstream `rate_limit_exceeded`, local
  account caps, and `response_create_gate_timeout` keep their bounded
  account-capacity wait

#### Scenario: Local capacity codes keep their rate-limit contract

- **WHEN** account selection fails with a local capacity or overload code such
  as `account_stream_cap` or `account_response_create_cap`
- **THEN** the response keeps HTTP `429` with `error.type = "rate_limit_error"`
  and the stable local error code
- **AND** the response is not reported as `usage_limit_reached`

#### Scenario: Unusable non-exhausted pools keep existing semantics

- **WHEN** every account is paused, deactivated, or requires re-authentication
  and no eligible account is exhausted by a known usage window
- **THEN** the pre-existing `no_accounts` failure semantics are preserved

#### Scenario: Owner-scoped exhaustion preserves continuity semantics

- **WHEN** a request is pinned to a previous-response or file owner account and
  only that owner is usage-exhausted while the wider eligible pool is usable
- **THEN** the proxy keeps the existing continuity-owner failure semantics
- **AND** it does not report pool-wide `usage_limit_reached`

### Requirement: Silent HTTP bridge sessions are quarantined from re-attach and reuse

When an HTTP bridge session proves silent/wedged, the proxy MUST quarantine its session key for a bounded window so later requests stop attaching to it. A session proves silent/wedged when either (a) a pending request being failed or retired carried a proxy-injected `previous_response_id`, had sent `response.create`, observed upstream response events, and never had `response.created` assigned, (b) the session key hits two consecutive eventless `missing_response_created_timeout` retires, or (c) the hard-affinity retry circuit for the key opens on an eventless poison-class failure (`stream_incomplete` or `stream_idle_timeout`), in which case the quarantine reason MUST be `retry_circuit_poisoned_anchor`. This holds for every path that fails or retires the request — partial stale-holder cleanup, the reader-failure funnel, and direct all-stale session retirement alike. The quarantine MUST be evaluated only when a request is already being failed or its session retired — never against a live owned turn — so a stream whose `response.created` was observed (including deferred-reasoning streams with long event gaps) MUST NOT be quarantined, and mere event silence during an owned live turn MUST NOT trigger quarantine by itself.

While a session key is quarantined: an existing session under that key MUST NOT be selected for reuse (a new request detaches it and proceeds on a fresh session), and for durable-anchor selection a quarantined session that is still open MUST count as absent, exactly as if it were already gone. The quarantine registry verdict is authoritative for the key: any session under the key while the quarantine window is active — including a freshly created replacement whose own completion has not yet cleared the quarantine — is equally excluded from reuse and equally absent for anchor selection. A fresh reattach whose incoming payload already looks like a full conversation resend MUST NOT receive a proxy-injected durable anchor through any injection point — the fresh-reattach injection, session-state hydration of the durable anchor, or the session-level injection — so the dispatch goes upstream genuinely unanchored with the client's own untrimmed payload. A payload that does not look like a full resend (a genuine delta-only continuation) MUST still receive the durable anchor, because it has no other way to convey prior conversation state. When no anchor exists anywhere for such a payload — the client supplied none and durable continuity was abandoned — and the key carries poison evidence (an active poison quarantine, or a durable circuit row still recording the poison episode), planning MUST fail the request closed as `bridge_previous_response_not_found` instead of dispatching it unanchored as a new conversation.

Quarantine state MUST be bounded and self-recovering: it is in-memory and session-scoped, expires by TTL (a live session that outlives its quarantine window MUST become reusable again), is cleared when a response completes on the same session key, and MUST NOT write account health or alter account selection.

A quarantine armed for reason `retry_circuit_poisoned_anchor` MUST NOT have that reason replaced by a weaker session-scoped fence while it is still active: the registry holds one entry per key, and the wedged-reattach and repeated-eventless fences carry no evidence about the anchor, so letting either overwrite the reason erases the only record that the anchor was proven dead.

The durable anchor abandonment MUST use the same capped threshold as the rest of this capability, in every funnel that can reach it. One poisoned anchor MUST be abandoned once per episode; a fenced or failed abandonment leaves it owed so the next strike retries — any next strike: the episode's owed poison class is recorded with it and MUST survive a later non-poison strike overwriting the durable failure detail, honored only against the exact reconciled lineage so a replaced row never inherits the stale owed state — and every funnel whose abandonment confirms MUST record the episode marker even while its circuit settlement remains outstanding, because the next strike finds empty continuity and cannot retry a settlement-only debt. The marker MUST reset whenever a durable load adopts a write this worker did not produce, so a replacement episode arriving at an equal or higher failure count is still allowed its one abandonment. The owed poison debt is carried by the durable row itself: an at-threshold poison detail MUST be sticky in the strike merge against non-poison overwrites — only a reset, settle, supersession, or another poison-class strike may change it — because counts and epochs cannot distinguish same-lineage advancement from a reset-and-overtaken replacement, and the sticky detail is what every replica re-derives the debt from; the process-local owed record still dies with every foreign write and re-arms from the adopted row. The process-local debt arms only when the recording strike is at or over the threshold: a below-threshold poison strike owes nothing yet, and a clean opener MUST NOT resurrect its detail into an owed clear against an episode that reached the threshold on non-poison evidence. Every funnel MUST derive its settlement from the failed request and the pending survivors snapshotted at decision time; none may settle unconditionally. An abandonment is owed only while its failure episode is still the registered one: a circuit settled by a concurrent success ends the episode, and a stale strike's captured count MUST NOT clear the fresh anchor that success persisted. An abandonment is also owed only while continuity actually remains: a durable session whose continuity columns are all empty owes nothing, because a clear there removes no failure cause, and the settle it authorized would reset a circuit cooling on genuinely unanchored failures. The continuity clear itself MUST be fenced on both continuity columns — the response anchor and the turn state — captured together when the episode was validated, and a completion MUST adopt any durable-only circuit row before capturing its quarantine fence and pre-settle poison detail — the settle's own load would otherwise arm a quarantine the already-captured fence can never clear, and a failed registration would find no detail to re-seed — and a completion MUST settle the circuit before it registers its fresh anchor, so a clear authorized against the poisoned anchor matches nothing once fresh continuity exists. When that settlement fails and the old episode is restored, the completion MUST NOT clear the quarantine, and the restored episode's owed abandonment MUST be suppressed as a transitional fence applied before the fresh anchor is published — a concurrent consult whose continuity read lands after publication would otherwise validate the old poison row and fence its rebind on the anchor just registered, deleting it — and that suppression MUST be rolled back when the registration then fails, restoring the owed clear so the old poisoned anchor is never left stored with no funnel willing to clear it — restored only onto the exact captured episode and lineage, since a state replaced or reconciled during the registration owns the key and never inherits the ended lineage's owed evidence; the cooldown stands until the next settle opportunity. A later poison-class strike recorded over the supersession sentinel MUST reset the one-clear marker: it is evidence against the freshly registered anchor and begins a new abandonment story of its own. The transitional suppression applies only to episodes carrying poison evidence — a clean episode owes no abandonment, and marking it would refuse the clear a later poison strike genuinely owes. That suppression MUST also be persisted by rewriting the surviving row's failure detail to a non-poison anchor-superseded class under the row's version fence — the local marker protects only its own worker, and another replica loading the surviving at-threshold poison row would otherwise arm quarantine against the fresh anchor and authorize an abandonment for failures recorded against the superseded one. The rewrite MUST NOT charge a failure or advance the row's version, so a concurrent strike merges onto the row in either order and its poison class outranks the supersession; a failed or outraced rewrite leaves the suppression process-local. Its fence MUST carry the observed failure count alongside the version, because a lagging-clock strike merges without moving the version while every landed merge increments the count — the count is what makes a strike that slipped in ahead of the rewrite outrank it. The fence MUST also carry the expected prior detail, forward and back: two completions can otherwise both believe they own the supersession of one shared row, and the loser's rollback would destroy the winner's, re-poisoning a freshly registered anchor; with the detail in the fence exactly one write owns each transition. A retired request that still holds a safe replay MUST NOT strike the circuit or trigger the abandonment on any funnel, matching the terminal and grouped paths. A request whose response has started holds no replay — the retry path refuses to dispatch one for it — so it MUST NOT block a settlement either, or the circuit is left cooling for its full backoff after a successful abandonment, protecting a replay that can never run; started means a counted response event or the deferred-reasoning prelude evidence that deliberately leaves the event count at zero. A funnel that drains or finalizes its requests before its settlement decision MUST carry the drained states into that decision — as a frozen snapshot when finalization empties the container it was handed — so a drained safe-replay holder still blocks the settle; only a handoff with genuinely no request states keeps striking. A completed verified stale-anchor replay MUST keep the source key's circuit and its durable row, and the one-clear marker on that surviving episode is process-local (see this change's `design.md` for the accepted tradeoff).

A quarantine armed for reason `retry_circuit_poisoned_anchor` MUST remain in force for at least the remaining cooldown of the circuit that armed it plus that circuit's half-open lease, because the probe it exists to protect is only admitted once that cooldown expires and may then be admitted anywhere inside the lease that follows. A suppression that assumes a remote half-open lease MUST be driven only by a confirmed dispatch-claim loss — a durable CAS that answered and matched nothing — never by a claim that timed out or errored, which is infrastructure trouble no probe owns. The quarantine registry's size cap MUST NOT evict such an entry before that deadline: the cap evicts only expired or weaker-fence entries and holds as a correctness bound rather than an unconditional one during an incident that quarantines more keys than the cap at once. The default TTL alone MUST NOT be relied on for this: it equals the circuit's maximum cooldown, so at that cooldown the quarantine would otherwise lapse in the same instant the cooldown does and hand the poisoned anchor back to the very request the cooldown was holding.

#### Scenario: Reattach streams events but response.created is never assigned (#1534)

- **GIVEN** a durable HTTP bridge session with a stored anchor whose fresh reattach injected a proxy-owned `previous_response_id`
- **AND** the reattached upstream stream delivers response events but `response.created` is never assigned
- **WHEN** the stream fails or the session is retired with that request still pending
- **THEN** the request fails terminally as before
- **AND** the session key is quarantined with reason `reattach_missing_response_created`

#### Scenario: All-stale direct retirement still quarantines the key

- **GIVEN** a wedged reattach (proxy-injected `previous_response_id`, `response.create` sent, response events observed, `response.created` never assigned) that is the ONLY stale pending request on its session
- **WHEN** the stuck-gate watchdog retires the session directly instead of failing the stale holder individually
- **THEN** the session key is quarantined with reason `reattach_missing_response_created`
- **AND** the next request takes the fresh no-anchor path instead of rebuilding the identical anchored reattach

#### Scenario: Next request after the wedge completes on the fresh path

- **GIVEN** a session key quarantined after a reattach that streamed events without `response.created`
- **WHEN** a later request arrives for the same key with a full-conversation-resend payload and no client `previous_response_id`
- **THEN** the proxy does not inject the durable anchor for that request
- **AND** the request is sent upstream unanchored with the client's own full payload
- **AND** the request can complete normally instead of rebuilding the identical wedged reattach

#### Scenario: Suppressed anchor does not come back through session state

- **GIVEN** a quarantined session key and a full-conversation-resend payload whose stored durable prefix is trimmable but whose fresh suffix does not retain the prior output
- **WHEN** the fresh-reattach durable-anchor injection is skipped because of the quarantine
- **THEN** the durable anchor is not rehydrated into the fresh session's completed-response state
- **AND** the session-level injection does not re-add the same anchor or trim the stored prefix
- **AND** the dispatch goes upstream genuinely unanchored with the client's untrimmed payload
- **AND** the suppression applies even when the fresh-reattach injection was already ineligible for other reasons (for example a conversation-scoped payload, a live alias session, or an active-owner forward that falls back to a local rebind)

#### Scenario: A poison quarantine outlives the cooldown that armed it

- **GIVEN** repeated eventless poison-class failures have driven a hard-affinity circuit to its maximum cooldown
- **WHEN** the quarantine is armed with reason `retry_circuit_poisoned_anchor` at that same instant
- **THEN** the quarantine window extends past the cooldown deadline by at least the circuit's half-open lease
- **AND** the probe admitted once that cooldown expires is still planned without the poisoned anchor

#### Scenario: Quarantined session is excluded from reuse selection

- **GIVEN** a session marked quarantined that is still live or retained for admission handoff
- **WHEN** a new request looks up that session key
- **THEN** the session is not considered reusable
- **AND** the request proceeds on a fresh session instead
- **AND** a replacement session created under the same still-quarantined key is likewise not reusable until a completion or the TTL clears the quarantine

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
- **AND** no durable row, janitor work, or account-health write was involved at any point

#### Scenario: Retry circuit opened by eventless failures quarantines the key

- **GIVEN** a hard-affinity bridge key whose retry circuit has one recorded eventless `stream_incomplete` failure
- **WHEN** a second eventless `stream_incomplete` failure opens the circuit
- **THEN** the session key is quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** a subsequent full-resend request on that key is dispatched unanchored through the existing fresh path
- **AND** a subsequent delta-only request on that key still receives the durable anchor

#### Scenario: Retry circuit opened by clean closes leaves the key unquarantined

- **GIVEN** a hard-affinity bridge key whose retry circuit has one recorded `clean_close` failure
- **WHEN** a second `clean_close` failure opens the circuit
- **THEN** the session key is not quarantined

### Requirement: Scoped operation identity

The system MUST include the normalized API-key scope in every durable HTTP
bridge operation fingerprint and MUST apply that scope to fingerprint and
completed-operation lookups.

#### Scenario: Equal requests from different keys remain isolated

- **WHEN** two API keys submit the same logical request
- **THEN** each key receives an independent durable operation identity

### Requirement: Recoverable startup takeover

Startup cleanup MUST retain sessions that own submitted, acknowledged, or
unknown operations and MUST detach ownership before a replacement instance
takes over.

#### Scenario: Restart preserves an in-flight operation

- **WHEN** an instance restarts while an operation is nonterminal
- **THEN** cleanup detaches the old owner without deleting the operation spool

### Requirement: Fresh retry transcript

When an explicit failed operation is rebound, the system MUST atomically remove
the prior operation events and reset event-byte/spool state before accepting new
events.

#### Scenario: Failed retry cannot replay stale failure output

- **WHEN** a failed operation is retried and later completes
- **THEN** replay contains only the new attempt's events

### Requirement: Proof-gated sibling anchoring

The system MUST advance a continuation to a completed sibling response only
when the sibling has the same parent and logical request fingerprint in the
same API-key scope.

#### Scenario: Distinct sibling input keeps its requested parent

- **WHEN** a request reuses a parent with a different fingerprint
- **THEN** the service does not silently anchor it to another child response

### Requirement: Single migration head

The Alembic graph MUST converge the durable operation revisions with the current
release head and MUST expose one canonical head after upgrade.

#### Scenario: Upgrade resolves one head

- **WHEN** migrations are upgraded to the release tip
- **THEN** Alembic reports one canonical head

### Requirement: Conservative spool defaults

New operation rows MUST start with an incomplete event spool on SQLite and
PostgreSQL. A transcript MUST become replayable only after terminal event drain
and explicit finalization.

#### Scenario: Nonterminal spool is not replayable

- **WHEN** an operation has events but no finalized terminal event
- **THEN** recovery does not replay its transcript as complete

### Requirement: Retain completed recovery transcripts

Startup ownership cleanup MUST retain sessions with operation transcripts that
remain inside the configured operation retention window, including completed
operations, and MUST let normal spool retention remove the operation rows.

#### Scenario: Recent completed transcript survives takeover

- **WHEN** startup cleanup sees a recent completed transcript
- **THEN** it retains the session until normal retention expires it

### Requirement: Continuous transcript retention

Operation transcript cleanup MUST run periodically in a leader-gated scheduler
and MUST delete eligible operations in bounded batches. A scheduler pass MUST
stop after it drains the eligible backlog or reaches its fixed batch-count or
wall-clock budget, and a later pass MUST be able to resume from the oldest
remaining eligible operation without changing retention eligibility or owner
fencing. When a successful pass reports likely backlog, the scheduler MUST
retry immediately instead of waiting the normal maintenance interval. When a
leader-election skip or failed pass preserves the backlog signal, the scheduler
MUST use a short bounded catch-up delay. Catch-up passes MUST run only operation
transcript retention rather than accelerating unrelated sticky, bridge-session,
or ring maintenance.
Disabling the existing sticky-session mapping cleanup switch MUST NOT disable
operation transcript retention; that switch MAY skip sticky mapping
maintenance while durable operation retention continues.

The retention window itself is dashboard-managed. It MUST resolve as code
default, then the deprecated environment alias, then a value stored in the
dashboard, and both the startup one-shot purge and each scheduler pass MUST
read it from the dashboard snapshot that pass already holds — once per pass,
never inside a runtime lock and never per operation — so a dashboard change
takes effect on the next tick on every replica without a restart.

Because the spool is the replay source for durable bridge recovery, the
effective window MUST cover every *configured* window in which a spooled
operation can still be read: the bridge session reuse window, the
stale-operation abandonment window, and the lifetime of a retry circuit that
has already admitted a claim. That floor
MUST be derived from those terms rather than fixed, because two of them are
themselves operator-tunable, and the settings API MUST refuse an update that
would leave the effective window below it, naming the binding term. A
deployment can already be below the floor without any update having passed that
check, because the environment alias alone decides the window while the
dashboard column is NULL; startup MUST warn about that state (warn-only,
naming the binding term and both values) so the first refusal is not a
surprise. The floor is a steady-state bound on the configuration, not a
retroactive one: a bridge
session already open keeps the longer idle TTL it captured, so shortening a
reuse window can still outlive the transcripts of sessions opened under the
previous window — the same non-retroactive behaviour the abandoned-row
retention derived from those windows already has.

#### Scenario: Retention drains a small backlog

- **WHEN** fewer operations are eligible than one scheduler-pass budget
- **THEN** the pass removes every eligible operation
- **AND** reports that no backlog is known to remain

#### Scenario: Retention drains all eligible batches

- **GIVEN** every eligible operation fits within one scheduler-pass budget
- **WHEN** transcript retention runs
- **THEN** that pass removes every eligible batch

#### Scenario: Retention yields with a large backlog

- **GIVEN** more operations are eligible than one scheduler-pass budget
- **WHEN** the pass reaches its batch-count or wall-clock budget
- **THEN** it commits the completed bounded batches and stops
- **AND** a later leader-gated pass resumes deletion from the oldest remaining
  eligible operation

#### Scenario: Successful backlog schedules immediate catch-up

- **GIVEN** a cleanup pass stops on a full final batch at its count or time
  budget
- **WHEN** the scheduler chooses the next delay
- **THEN** it retries immediately rather than waiting the normal maintenance
  interval
- **AND** the catch-up pass does not rerun unrelated maintenance

#### Scenario: Skipped or failed backlog uses bounded retry

- **GIVEN** the backlog signal remains after leader-election skips or a failed
  retention pass
- **WHEN** the scheduler chooses the next delay
- **THEN** it uses the bounded backlog retry delay

#### Scenario: Sticky cleanup toggle does not disable transcript retention

- **WHEN** sticky-session cleanup is disabled and the durable bridge schema is
  available
- **THEN** the leader-gated scheduler still deletes bounded batches of expired
  operation transcript rows while skipping sticky mapping cleanup

#### Scenario: Dashboard window applies without a restart

- **GIVEN** the environment alias sets a seven-day window and an operator
  stores a shorter window in the dashboard
- **WHEN** the next leader-gated retention pass runs
- **THEN** it cuts at the dashboard window, not the environment alias
- **AND** no replica was restarted

#### Scenario: A window already below the floor is reported at startup

- **GIVEN** the environment alias sets a window below the floor and no
  dashboard value has been stored
- **WHEN** the application starts
- **THEN** it logs a warning naming the binding term, the effective window and
  the floor
- **AND** startup continues

#### Scenario: Unset dashboard window keeps inheriting

- **GIVEN** no dashboard value has been stored
- **WHEN** a retention pass runs
- **THEN** it cuts at the environment alias, or at the code default when the
  alias is unset
- **AND** the stored dashboard value remains unset after any settings save that
  omits the field

### Requirement: Ordered deferred reasoning persistence

Deferred reasoning events released before a visible event MUST be persisted in
the same order in which they are delivered downstream, before the visible
event is persisted.

#### Scenario: Deferred events preserve downstream order

- **WHEN** buffered reasoning is released before visible output
- **THEN** the durable spool stores the reasoning blocks before that output

### Requirement: Per-operation disconnect classification

When a shared bridge websocket closes, each pending operation MUST be
classified from that operation's own observed response-event count. Activity
from a sibling request MUST NOT make an eventless operation safely retryable.

#### Scenario: Sibling output does not acknowledge an eventless request

- **WHEN** one pending request emitted output and another emitted none
- **THEN** the two operations receive different disconnect classifications

### Requirement: Abandoned operation retention

Operation retention MUST expire stale submitted and acknowledged rows in
addition to terminal and ambiguous rows, so a crashed or abandoned operation
cannot retain raw request data indefinitely.

#### Scenario: Stale abandoned request is purged

- **WHEN** a submitted operation exceeds retention age
- **THEN** its request data and event spool are removed

### Requirement: Acknowledged alias persistence failure

If upstream has acknowledged a response but local continuity-alias persistence
fails, the downstream error MUST NOT transition the durable operation to a
retryable failed state. The operation MUST remain acknowledged/ambiguous so an
identical retry cannot dispatch a duplicate upstream turn.

#### Scenario: Alias write failure remains fail-closed

- **WHEN** an acknowledged response cannot publish its continuity alias
- **THEN** the operation remains non-retryable and the client receives a terminal error

### Requirement: Cross-session nonterminal handoff

When a scoped operation fingerprint is found under a different durable
session, a nonterminal operation MUST be atomically rebound to the currently
owned session before its event spool is reset or a recovery attempt is sent.
Completed replayable operations MUST remain attached to their original session.
The handoff MUST be refused while the prior session has an unexpired owner
lease, preventing concurrent owners from dispatching the same turn.

#### Scenario: Active prior owner fences handoff

- **WHEN** a duplicate request finds a nonterminal operation under another session
- **AND** that session still has an unexpired owner lease
- **THEN** the operation remains with the prior session and no concurrent retry is dispatched

#### Scenario: Expired prior owner permits handoff

- **WHEN** the prior session lease is absent or expired
- **THEN** the operation can be atomically rebound before recovery

### Requirement: Fenced one-shot recovery dispatch

The durable recovery journal MUST persist a one-shot replay budget for every
recovery-safe request. The budget MUST be consumed atomically when a replay is
claimed for dispatch, and a caller that proves the replay never reached the
upstream send boundary MUST restore that claim under the same session owner
fence. A replacement session MUST retain or transfer a fenced origin owner
until the claim is rolled back or settled; selecting a replacement or failing
preflight MUST NOT permanently consume an unsent replay.

#### Scenario: Concurrent reconnects consume one replay

- **WHEN** concurrent reconnects observe the same ambiguous operation
- **THEN** exactly one owner atomically claims the persisted replay budget and
  other reconnects fail closed without dispatching a duplicate

#### Scenario: Pre-dispatch replacement failure restores the budget

- **WHEN** a replay claim is made but replacement admission or preflight fails
  before the exact upstream frame is sent
- **THEN** the claim returns to the available state and the fenced origin
  owner is released only after that rollback succeeds

#### Scenario: Successful replacement settles the origin journal

- **WHEN** a replacement session dispatches the claimed replay and receives a
  terminal response event
- **THEN** settlement uses the retained origin owner fence before releasing it
  and the replay budget cannot be claimed again

### Requirement: Lease-aware operation retention

Retention MUST NOT delete stale submitted or acknowledged operations while
their session is actively owned with an unexpired lease. The owner/lease
predicate MUST be rechecked in the deletion transaction.

#### Scenario: Active lease protects stale operation

- **WHEN** a stale operation belongs to a session with a live lease
- **THEN** retention leaves it intact

### Requirement: Failure spool/state ordering

For an explicit deterministic failure, the proxy MUST persist the terminal SSE
block before exposing the durable operation as failed. The event append and
failed-state transition MUST use the same owner fence and transaction when the
durable repository supports it.

#### Scenario: Concurrent retry cannot reset an unspooled failure

- **WHEN** a response failure is being settled while an identical reconnect is
  admitted
- **THEN** the reconnect observes the terminal operation fence and cannot reset
  or mix the previous failure into a new transcript

### Requirement: Partial disconnect acknowledgement

When a bridge disconnects after an operation has emitted any response event but
before a terminal event, the durable operation MUST remain acknowledged or
ambiguous. It MUST NOT be classified as retryable failed solely because the
disconnect was non-terminal.

#### Scenario: Partial output is never resent as a fresh turn

- **WHEN** the upstream closes after `response.created` but before completion
- **THEN** the operation remains non-retryable

### Requirement: Preserve repeated event occurrences

The durable event spool MUST preserve repeated identical SSE blocks as distinct
ordered occurrences. Event identity MUST include its operation-local sequence
position rather than content alone.

#### Scenario: Identical deltas replay twice

- **WHEN** two consecutive SSE blocks have identical text
- **THEN** both occurrences are present in the replay transcript

### Requirement: Stop event persistence during shutdown

Proxy shutdown MUST close the HTTP bridge event batcher and cancel its
background flusher before the process exits.

#### Scenario: Shutdown cancels the flusher

- **WHEN** the proxy service begins shutdown after queueing an event
- **THEN** the batcher's background task is cancelled and awaited

### Requirement: Classify response.incomplete as terminal

An anchored `response.incomplete` event MUST transition the durable operation to
an explicit terminal state and finalize its transcript so it is not left in an
unknown in-flight state.

#### Scenario: Incomplete response is replayable as terminal

- **WHEN** upstream emits `response.incomplete`
- **THEN** the operation is terminalized and its drained transcript is eligible for replay

### Requirement: Settle reservations before timeout health

When an eventless timeout retires a keyed bridge, the proxy MUST settle all
pending request reservations before recording the account timeout health signal.
If settlement fails, the health signal MUST NOT claim that cleanup completed.

#### Scenario: Failed reservation release does not poison health state

- **WHEN** the timeout cleanup cannot release a pending reservation
- **THEN** the account timeout signal is not recorded before that failure is surfaced

### Requirement: Replay finalized incomplete operations

A finalized `incomplete` operation transcript MUST be replayed for an identical
request and MUST NOT be reset or treated as an unknown in-flight operation.

#### Scenario: Reconnect receives stored incomplete transcript

- **WHEN** an identical request finds a finalized incomplete operation
- **THEN** the stored terminal transcript is delivered without a new upstream dispatch

### Requirement: Validate final response.create size

After adding durable operation metadata, the proxy MUST revalidate the exact
serialized `response.create` frame against the upstream size limit before
sending it.

#### Scenario: Metadata cannot create an oversized frame

- **WHEN** operation metadata makes the final frame exceed the configured limit
- **THEN** the request is rejected or slimmed before any upstream send

### Requirement: Responses routes preserve the Ultrafast service tier

Responses-compatible routes MUST accept the canonical `ultrafast` service tier and MUST forward it unchanged. When upstream reports the actual response tier, request logging MUST preserve `ultrafast` using the existing requested, actual, and billable tier contract.

#### Scenario: Explicit Ultrafast request is forwarded

- **WHEN** a client sends a Responses request with `service_tier: "ultrafast"`
- **THEN** the forwarded upstream payload contains `service_tier: "ultrafast"`

#### Scenario: Upstream confirms Ultrafast processing

- **WHEN** upstream completes a request with `response.service_tier: "ultrafast"`
- **THEN** the actual and billable request-log tiers are `ultrafast`

### Requirement: Account-bound retries remain on their dispatch owner

The proxy MUST bind a Responses request body that is not a canonical
account-neutral fresh replay to the account that first receives that exact
body. Every later selection for that request MUST treat the dispatch owner as a
strict required account across HTTP streaming, HTTP bridge, and direct
WebSocket transports.

The proxy MUST NOT exclude the dispatch owner and send the retained body to a
different account during stale-anchor recovery, retryable account failure,
Trusted Access migration or degradation, bridge reconnect, or WebSocket account
switching. If the required owner is unavailable, the proxy MUST fail closed
without dispatching the retained body to another account.

The proxy MAY perform one forced authentication refresh and replay a retained
account-bound body on the same dispatch owner. It MUST NOT use that refresh to
exclude the owner or migrate the body to another account, and a permanent
authentication failure MUST remain terminal for the bound body.

The proxy MAY clear the dispatch-owner binding only after verified recovery
replaces the exact wire body and the replacement passes the canonical
account-neutral-fresh-replay predicate. Removing `previous_response_id` alone
MUST NOT make retained account-scoped input portable.

Proxy-owned operation metadata that will be added at the send boundary MUST
remain bound to the current account unless an explicit operation-rebind path
replaces that identity before account selection. Installing a verified fresh
body and clearing its dispatch-owner binding MUST occur as one state
transition.

#### Scenario: Encrypted reasoning remains on its first dispatch account

- **GIVEN** account A first receives a Responses request containing encrypted
  reasoning or another account-scoped retained item
- **WHEN** a pre-visible retry excludes account A or requests a differently
  authorized account
- **THEN** the proxy does not dispatch the retained body to account B
- **AND** the retry fails closed when account A is unavailable

#### Scenario: Verified account-neutral fresh replay may change accounts

- **GIVEN** verified recovery removes a stale continuation anchor
- **AND** the exact replacement body contains only canonical account-neutral
  fresh input
- **WHEN** normal retry selection chooses account B
- **THEN** the proxy may dispatch the replacement body to account B

#### Scenario: Confirmed pre-dispatch failure does not create an owner

- **GIVEN** account A is selected for a nonportable Responses body
- **WHEN** transport evidence confirms the request failed before any upstream
  bytes were dispatched
- **THEN** the proxy does not record account A as the dispatch owner
- **AND** normal retry selection may dispatch the body first on account B

#### Scenario: HTTP bridge preserves payload ownership

- **GIVEN** an HTTP bridge request has already dispatched a nonportable body to
  account A
- **WHEN** pre-created recovery or reconnect selection excludes account A
- **THEN** the bridge does not submit that body on account B

#### Scenario: Direct WebSocket preserves payload ownership

- **GIVEN** a direct WebSocket request has already dispatched a nonportable body
  to account A
- **WHEN** retry handling prepares an account switch
- **THEN** the proxy rejects the switch unless the exact replacement body is a
  canonical account-neutral fresh replay

#### Scenario: Bound authentication refresh stays on the owner

- **GIVEN** a nonportable body is bound to account A
- **WHEN** account A reports a refreshable authentication failure before
  visible output
- **THEN** the proxy may refresh and replay once on account A
- **AND** it does not dispatch the retained body to account B

#### Scenario: HTTP bridge operation identity remains on its owner

- **GIVEN** an HTTP bridge retry retains a proxy-owned operation identity
- **AND** no explicit operation rebind has replaced that identity
- **WHEN** retry selection evaluates another account
- **THEN** the bridge requires the current operation owner

#### Scenario: Existing settlement ordering is unchanged

- **GIVEN** an API-key reservation requires settlement during the failed retry
- **WHEN** account health is updated
- **THEN** required settlement still completes before deferred health writes

### Requirement: Compact terminal SSE errors preserve top-level error type

When the compact Responses upstream terminates with a top-level SSE `type=error` frame, the proxy MUST preserve a supplied non-blank `error_type` in the emitted OpenAI error envelope. If `error_type` is absent, non-string, or blank, the proxy MUST use `server_error`. The proxy MUST preserve existing status, code, message, and parameter mapping, and MUST NOT alter nested OpenAI-style error-envelope behavior.

#### Scenario: Top-level invalid request type is preserved

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is `invalid_request_error`
- **THEN** the proxy returns HTTP 400 with `error.type=invalid_request_error`
- **AND** preserves the frame's code, message, and parameter

#### Scenario: Missing or blank top-level type uses compatibility fallback

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is absent or blank
- **THEN** the emitted OpenAI error envelope uses `error.type=server_error`
- **AND** existing status, code, message, and parameter mapping remains unchanged

#### Scenario: Nested compact error envelope remains unchanged

- **WHEN** compact upstream terminates with a nested OpenAI-style error envelope
- **THEN** the proxy preserves the nested type and all other mapped fields using the existing parser

### Requirement: Responses transports apply the global priority-tier prohibition consistently

The service MUST apply the same canonical priority-tier prohibition before
forwarding an upstream payload from native `/responses`, OpenAI-compatible
`/v1/responses`, native and `/v1` compact Responses, chat-to-Responses
conversion, WebSocket `response.create`, dashboard warmup, and internal
owner-forwarding paths whenever `prohibitFastMode` is enabled. WebSocket
`response.create` frames MUST use the `prohibitFastMode` policy snapshot
captured when the connection began.

#### Scenario: Native and OpenAI-compatible HTTP requests omit explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** `/responses` or `/v1/responses` receives an explicit priority service tier
- **THEN** the effective upstream payload omits `service_tier`

#### Scenario: Compact requests omit explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** `/responses/compact` or `/v1/responses/compact` receives an explicit priority service tier
- **THEN** the effective upstream payload omits `service_tier`

#### Scenario: Chat conversion omits explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a compatible chat-completions request carries an explicit priority service tier and is converted to Responses
- **THEN** the effective upstream Responses payload omits `service_tier`

#### Scenario: Owner forwarding cannot restore priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** an internal owner-forwarded payload carries a priority service tier
- **THEN** the receiving preparation boundary omits `service_tier` before upstream forwarding

### Requirement: Native direct HTTP egress preserves Responses streaming semantics

Direct Responses HTTP/SSE requests sent through native egress MUST preserve the existing normalized upstream payload and headers, rate-limit header ingestion, maximum SSE event size, idle and total request deadlines, terminal-event requirements, downstream event normalization, archives, and error envelope behavior. Downstream cancellation MUST cancel and await only the owned native request task, unregister its event stream, and leave unrelated multiplexed requests usable. Native transport selection MUST NOT change the public HTTP status or SSE framing contract.

#### Scenario: Native SSE response uses the ordinary parser

- **GIVEN** native direct egress returns an HTTP success for a streaming Responses request
- **WHEN** the proxy consumes the response
- **THEN** Rust frames body bytes into SSE blocks with the existing CR/LF, UTF-8 replacement, whitespace, EOF, and size-limit behavior
- **AND** Python applies the ordinary normalizer, terminal-event detection, and archive path without byte reframing
- **AND** the downstream event sequence matches the Python transport contract

#### Scenario: Downstream cancellation owns helper cleanup

- **GIVEN** one native helper generation owns multiple active requests
- **WHEN** one downstream stream is cancelled or closed before its terminal event
- **THEN** only that native request task is cancelled and awaited
- **AND** the helper and unrelated request streams remain usable
- **AND** the cancelled POST is not replayed through another HTTP client

#### Scenario: Framing failures retain public error behavior

- **WHEN** native SSE framing exceeds the configured event byte limit or receives no body bytes within the idle deadline
- **THEN** the public response uses the existing stream-event-too-large or stream-idle-timeout error behavior
- **AND** idle timeout remains account-neutral

### Requirement: HTTP session bridge admission obeys downstream transport policy

Before an HTTP/SSE Responses request enters the upstream WebSocket session bridge, the proxy MUST apply the same explicit-transport precedence and effective `http_downstream_transport_policy` used by the ordinary streaming retry path. Outside the existing recent upstream WS failure cooldown, an explicit upstream `http` selection MUST bypass the bridge, an explicit upstream `websocket` selection MUST retain it, and otherwise the per-key override or global policy MUST decide. A bridge bypass MUST continue through the ordinary HTTP streaming path without changing request or response shapes.

#### Scenario: Always-HTTP bypasses an enabled bridge

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective downstream-HTTP policy is `always_http` or `pinned`
- **WHEN** a downstream HTTP/SSE request is handled
- **THEN** the request bypasses the WebSocket session bridge
- **AND** is sent through the ordinary upstream HTTP path

#### Scenario: Smart bridge admission follows continuity signals

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective policy is `smart`
- **WHEN** a request has no sticky-continuation signal
- **THEN** it bypasses the bridge
- **BUT WHEN** any defined sticky-continuation signal is present
- **THEN** it remains eligible for the bridge

#### Scenario: Explicit transport wins before bridge admission

- **GIVEN** the HTTP Responses session bridge is enabled and no recent upstream WS failure marker is active
- **WHEN** upstream transport is explicitly `http`
- **THEN** the bridge is bypassed under every policy
- **BUT WHEN** upstream transport is explicitly `websocket`
- **THEN** the bridge remains enabled under every policy

#### Scenario: Per-key policy controls bridge admission

- **GIVEN** a per-API-key transport policy override is non-null
- **WHEN** bridge admission is evaluated
- **THEN** that override is used instead of the global policy

### Requirement: Responses WebSocket preserves bidirectional transport semantics

The Responses WebSocket relay MUST preserve ordered text and binary messages, selected subprotocol response metadata, close codes, and terminal error delivery across its downstream and upstream boundaries. Direct and account-routed upstream connections MUST use native Codex-family WebSocket egress when the fixed helper is available before dispatch, while Python MUST retain route-aware endpoint selection, fallback safety, metadata, and cleanup. Ping and pong control frames MUST remain transport-owned and MUST NOT surface as application events. A frame whose native send acknowledgement is ambiguous or failed MUST NOT be replayed.

#### Scenario: Native direct relay preserves frames

- **GIVEN** a direct or account-routed Responses WebSocket uses the native helper
- **WHEN** text and binary frames travel in both directions
- **THEN** their type, payload, and ordering are preserved
- **AND** control ping and pong frames are handled below the application relay

#### Scenario: Native direct relay preserves terminal close

- **WHEN** the native upstream sends a close frame
- **THEN** the relay observes its close code and reason
- **AND** the native connection is removed from the helper's active registry

#### Scenario: Ambiguous native frame send fails closed

- **GIVEN** a downstream `response.create` frame is dispatched to the helper
- **WHEN** acknowledgement fails because the helper or connection closes
- **THEN** the turn surfaces a terminal transport failure
- **AND** the frame is not resent on another transport

### Requirement: Synthesized downstream turn state must remain provenance-scoped

A turn-state value synthesized by codex-lb for downstream reconnect and
internal affinity MUST remain available to those consumers without being
presented to the upstream server as a client-originated initial WebSocket
handshake header. A nonblank turn-state explicitly supplied by the client or
issued by upstream MAY continue through the existing owner-bound continuity
path.

#### Scenario: Initial WebSocket uses internal synthesized affinity only

- **GIVEN** a client opens a Responses WebSocket without `x-codex-turn-state`
- **WHEN** codex-lb accepts the downstream connection and opens upstream
- **THEN** the downstream accept MUST include a synthesized `x-codex-turn-state`
- **AND** internal continuity MUST be able to use that synthesized value
- **AND** the initial upstream handshake MUST NOT include that value as an `x-codex-turn-state` header.

#### Scenario: Explicit client turn state retains continuity semantics

- **GIVEN** a client reconnects with a nonblank `x-codex-turn-state`
- **WHEN** codex-lb opens the corresponding upstream connection
- **THEN** the value MUST retain its explicit client continuity provenance
- **AND** the synthesized-state omission rule MUST NOT silently reclassify it as LB-generated state.

### Requirement: Backend non-streaming Responses preserve HTTP JSON transport

When `POST /backend-api/codex/responses` receives a valid request with
`stream: false`, the service MUST preserve that value in the upstream request,
MUST use upstream HTTP rather than WebSocket, and MUST return one
`application/json` Response object rather than an SSE stream. The service MUST
retain the existing account selection, error masking, usage settlement, and
request logging behavior around that request. This requirement does not change
the `/v1/responses` subscription compatibility path, which MAY aggregate an
upstream stream when required by the configured ChatGPT Codex backend.

When the completed HTTP exchange returns a canonical background acknowledgement
with `object = response`, a non-empty response ID without surrounding
whitespace, status `queued` or
`in_progress` matching the event type, and `output = []`, the proxy MUST treat
the transport as successful: the request log MUST use `status=success` without
`stream_incomplete`, account health MUST take the successful-request path, and
the account MUST NOT receive a transient error-health penalty. This transport
classification MUST NOT make malformed or partial response objects successful
and MUST NOT make `response.queued` or `response.in_progress` terminal for SSE
streams.

#### Scenario: Backend stream false stays false upstream

- **GIVEN** a client sends `POST /backend-api/codex/responses` with
  `stream: false`
- **WHEN** codex-lb forwards the request to an HTTP-capable upstream
- **THEN** the upstream request body MUST contain `stream: false`
- **AND** its request headers MUST advertise `Accept: application/json`
- **AND** codex-lb MUST NOT open an upstream WebSocket for that request.

#### Scenario: Backend non-streaming response remains JSON downstream

- **GIVEN** the upstream returns a successful single Response JSON object
- **WHEN** codex-lb completes the backend non-streaming request
- **THEN** the downstream response MUST have an `application/json` content type
- **AND** its Response fields MUST preserve the upstream values.

#### Scenario: Accepted background JSON settles successfully

- **GIVEN** a backend Responses request has `stream: false`
- **AND** upstream returns one valid Response object with status `queued` or
  `in_progress`
- **AND** the object has a non-empty, unpadded ID and an empty output list
- **WHEN** codex-lb finishes reading that HTTP response
- **THEN** the Response object is returned unchanged
- **AND** the request log records success without `stream_incomplete`
- **AND** the request log stores the returned response ID for later owner lookup
- **AND** account health records success without an error penalty

#### Scenario: Malformed background object retains error settlement

- **GIVEN** a backend non-streaming response reports queued or in-progress
- **BUT** its response object is missing canonical acknowledgement fields or
  contains malformed output items
- **WHEN** codex-lb validates the response
- **THEN** the external contract error is returned
- **AND** request-log and account-health settlement MUST remain on the error
  path

#### Scenario: Streaming progress EOF remains truncated

- **GIVEN** a Responses request uses streaming transport
- **AND** upstream emits `response.queued` or `response.in_progress`
- **WHEN** the stream ends before a terminal Responses event
- **THEN** settlement remains `stream_incomplete`
- **AND** existing request-log and account-health error handling remains
  unchanged

### Requirement: Native Codex HTTP attempts preserve verified transport fallback

Native Codex HTTP/SSE requests SHALL follow the same effective HTTP transport
policy as other HTTP requests. First-party User-Agent or originator identity
alone MUST NOT imply a previous WS failure or force upstream HTTP. The existing
recent upstream WS connect-failure marker and explicit upstream HTTP preference
MUST preserve HTTP fallback. Native downstream WebSocket requests MUST remain
on their dedicated WebSocket path.

#### Scenario: Healthy native HTTP continuation is promoted
- **GIVEN** a native Codex HTTP request carries continuation evidence
- **WHEN** upstream transport is automatic, HTTP policy is smart, and no recent WS failure is active
- **THEN** the request is eligible for the upstream WS bridge

#### Scenario: Verified Codex HTTP fallback is retained during outage
- **GIVEN** the existing recent upstream WS connect-failure marker is active
- **WHEN** a native Codex HTTP request arrives
- **THEN** codex-lb sends the attempt upstream over HTTP
- **AND** normal promotion eligibility returns when the marker clears or expires

#### Scenario: Explicit WebSocket remains authoritative when healthy
- **GIVEN** a native Codex HTTP request with no active WS transport failure marker
- **WHEN** the operator explicitly configures upstream WebSocket transport
- **THEN** the explicit WebSocket selection remains authoritative

### Requirement: Native Codex preserves upstream failure lifecycle

For a native Codex HTTP/SSE Responses request, an upstream transport timeout or
stream EOF without a terminal Responses event MUST terminate the downstream
stream without synthesizing `response.failed`, `error`, or `[DONE]`. The proxy
MUST still execute reservation, request-log, account-health, and owned-resource
cleanup before propagating the termination. Non-native and OpenAI-compatible
clients MUST retain the existing stable terminal-error shaping.

#### Scenario: Native Codex sees a truncated SSE lifecycle

- **GIVEN** a native Codex HTTP request has received a non-terminal SSE event
- **WHEN** upstream closes without a terminal event
- **THEN** downstream closes without a synthetic terminal event or `[DONE]`
- **AND** proxy cleanup and failure accounting still complete

#### Scenario: Non-native client keeps the terminal umbrella

- **GIVEN** an OpenAI SDK or other non-native client receives the same upstream
  truncation
- **WHEN** codex-lb normalizes the stream
- **THEN** the client receives the existing terminal `response.failed` shape

### Requirement: Propagated upstream rate limits preserve Retry-After

When a Responses upstream HTTP rejection carries a valid `Retry-After` header
and that rejection is propagated as a downstream HTTP response, codex-lb MUST
copy the field value unchanged. The proxy MUST accept only a bounded value with
no CR or LF and MUST NOT expose other upstream response headers through this
rule. A missing or invalid value MUST remain absent.

#### Scenario: Upstream 429 retry hint survives startup propagation

- **WHEN** upstream rejects a Responses request with HTTP 429 and
  `Retry-After: 1`
- **THEN** the propagated downstream 429 includes `Retry-After: 1`

#### Scenario: Unsafe retry hint is omitted

- **WHEN** an upstream retry hint contains a line break or exceeds the bounded
  field length
- **THEN** codex-lb does not copy that value downstream

### Requirement: Routed native Responses streams consume Rust-framed SSE

Account-routed streaming Responses HTTP requests using native egress MUST
delegate byte framing, event byte limits, and body-read idle deadlines to the
existing Rust SSE transport contract. Python MUST consume the framed events
without byte reframing, preserving normalization, terminal detection, rate-limit
headers, archives, route trace, and public error envelopes. Non-streaming HTTP
responses and HTTP errors MUST retain raw body consumption. Body failures and
cancellation MUST NOT replay a dispatched POST or switch its proxy endpoint.

#### Scenario: Routed native success skips Python byte framing

- **WHEN** the selected proxy endpoint returns a successful streaming Responses body
- **THEN** Rust emits the same SSE event contract as direct native streaming
- **AND** Python performs ordinary downstream event processing without scanning bytes

#### Scenario: Framing failure preserves route and error behavior

- **WHEN** a routed body exceeds its byte limit or goes idle
- **THEN** the existing stream-event-too-large or stream-idle-timeout envelope is produced
- **AND** route metadata remains associated with that attempt without endpoint replay

#### Scenario: Routed cancellation isolates the owned stream

- **WHEN** a routed native SSE stream closes or is cancelled, including in an already cancelled scope
- **THEN** its owned native request is cancelled and unregistered
- **AND** a locally created routed client finishes closing its session before cancellation propagates
- **AND** another active request in the same helper remains usable

#### Scenario: Routed error or non-streaming response remains raw

- **WHEN** the routed response is an HTTP error or the request disables streaming
- **THEN** the ordinary JSON/error body path and public response envelope are preserved

### Requirement: Native compact Responses preserve terminal and ownership contracts

Direct and account-routed compact requests MUST use native transport when the
helper has negotiated `http_compact_sse_v1` and `http_compact_collect_v1`. Native SSE framing MUST apply only
to successful responses selected by the outbound HTTP Content-Type rule; other
responses MUST retain raw body handling. Python MUST retain request shaping,
compact normalization, terminal error mapping, archives, routing, and settlement.
Responses MUST remain open until consumption finishes, and owned responses and
routed sessions MUST close on completion, failure, and cancellation. Missing
helpers MAY use Python transport only before dispatch. An installed helper lacking
either required compact capability MUST fail negotiation before dispatch without Python fallback. Native failures after
dispatch MUST NOT replay the POST through Python or another proxy endpoint.

#### Scenario: Compact completes before HTTP EOF

- **WHEN** response.completed follows compact output items while upstream keeps the body open
- **THEN** compact returns the existing normalized payload without waiting for EOF
- **AND** the owned transport request closes while unrelated helper requests stay usable

#### Scenario: Terminal and framing failures

- **WHEN** compact receives a terminal SSE error or exceeds its event/idle/total limit
- **THEN** existing compact public error codes, status mapping, and replay safety are preserved
- **AND** the response and owned client are cleaned up

#### Scenario: Routed fallback before dispatch

- **WHEN** a confirmed pre-dispatch connection failure permits the next configured endpoint
- **THEN** native SSE options follow that attempt and its exact route metadata is recorded
- **AND** an accepted response or ambiguous body failure never triggers endpoint replay

#### Scenario: Missing helper and cancelled caller

- **WHEN** the helper is missing before dispatch
- **THEN** the resolved Python transport receives no native-only option and retains compact parsing
- **AND** cancellation finishes owned response/session cleanup even in an already cancelled scope

#### Scenario: Cancellation while waiting for native response headers

- **WHEN** an already cancelled caller scope interrupts native response-head waiting
- **THEN** request cancellation completes and its stream registration is removed
- **AND** a completed native exchange wins a simultaneous shutdown/cancel race without an additional terminal event

### Requirement: Native compact collection preserves terminal output assembly

For direct and account-routed compact SSE requests with negotiated native
collection, Rust MUST collect output items and assemble the terminal response.
The last item for each integer output index MUST win; indexed items MUST be
ordered numerically, followed by unindexed done items in arrival order. A
nonempty terminal output array MUST take precedence over collected items.
Unknown JSON fields and integer values MUST be preserved. Python MUST retain
public shape normalization, error translation, archives, routing, and settlement.

#### Scenario: Completion without terminal output

- **WHEN** output-item events precede a completed response with missing or empty output
- **THEN** the result includes collected items in the documented order
- **AND** returns on completion without waiting for HTTP EOF or interpreting later events

#### Scenario: Existing terminal output

- **WHEN** the completed response contains a nonempty output array
- **THEN** that array is returned without merging earlier collected items

#### Scenario: Terminal failure or missing completion

- **WHEN** an SSE stream fails, is incomplete, has no valid completed response object, or ends before completion
- **THEN** the existing compact error envelope and failure classification are preserved

#### Scenario: Missing-helper compatibility

- **WHEN** the native helper is unavailable before dispatch
- **THEN** Python transport and collection preserve the same output assembly contract

### Requirement: Native HTTP interprets Responses stream events compatibly

Native direct and routed HTTP Responses streams MUST preserve the existing event
classification and legacy text/audio/audio-transcript alias normalization.
Unmodified events MUST retain their original text. SSE field parsing MUST
recognize only CR, LF, and CRLF boundaries and join data fields with LF.
Canonical event-line classification, malformed JSON, native passthrough mode,
unknown fields, and terminal behavior MUST match the Python path.

The synchronous Rust Responses library MUST own eligible alias normalization and
event classification. Python MUST retain request-context-dependent error mapping.
When alias serialization contains floating numbers, integers outside the Rust
JSON integer domain, or escaped surrogate strings, the interpreter MUST signal
Python normalization of the original event rather than lose data or reinterpret
its representation. This handoff MUST NOT replay the request.

#### Scenario: Legacy alias on framing and payload

- **WHEN** an event uses a supported legacy alias in its event line or payload
- **THEN** both surfaces are normalized according to the existing Python rules
- **AND** unrelated data and fields retain their values

#### Scenario: Unsupported alias JSON representation

- **WHEN** rewriting an alias requires a JSON representation outside the native serializer's supported domain
- **THEN** Python receives the original event and an explicit normalization marker
- **AND** the public result matches the ordinary Python transport

#### Scenario: Native error passthrough

- **WHEN** native passthrough receives an error event
- **THEN** it remains an error event and ends the stream under the existing policy
- **AND** SDK mode retains the existing request-context-dependent error conversion

### Requirement: HTTP bridge cleanup ownership survives caller cancellation

Grouped HTTP bridge terminal persistence MUST keep each terminal append barrier and terminal delivery barrier under exactly one strongly owned task until that barrier completes. A barrier MUST NOT be considered released before its callback finishes. If caller cancellation arrives while terminal append, terminal enqueue, or either barrier is pending, the service MUST complete the required terminal delivery and barrier ordering before propagating the original cancellation, and MUST NOT invoke either barrier callback more than once.

After an HTTP bridge session's resource-close owner completes, removal of that exact generation from detached-capacity tracking MUST remain independently owned until registry finalization completes. Cancellation while finalization waits for the bridge registry lock MUST NOT leave a closed generation consuming local bridge capacity, MUST NOT remove another generation, and MUST propagate only after the exact detached generation is finalized.

#### Scenario: Grouped terminal cancellation releases both barriers

- **GIVEN** grouped terminal failure persistence is waiting on a terminal append and its append barrier
- **WHEN** the caller's request scope is cancelled before either operation completes
- **THEN** the append-barrier callback completes exactly once
- **AND** the terminal event and end-of-stream marker are delivered to the downstream queue
- **AND** the delivery-barrier callback completes exactly once
- **AND** the original cancellation propagates only after those ordering points complete

#### Scenario: Cancelled terminal append fails after cancellation

- **GIVEN** caller cancellation has been deferred while a terminal append owner remains pending
- **WHEN** that append fails instead of returning a persistence result
- **THEN** fallback terminal delivery and both barrier ordering points still complete
- **AND** the append failure does not erase the original caller cancellation
- **AND** the original cancellation propagates after fallback delivery completes

#### Scenario: Re-entered barrier release re-awaits one owner

- **GIVEN** a terminal barrier callback has started but remains pending
- **WHEN** cleanup reaches the same barrier release path again after interruption
- **THEN** cleanup awaits the existing barrier owner
- **AND** it does not invoke the barrier callback a second time

#### Scenario: Cancelled detached finalization releases bridge capacity

- **GIVEN** a detached HTTP bridge session has completed resource close
- **AND** registry finalization is waiting for the bridge registry lock
- **WHEN** the session-close caller is cancelled
- **THEN** the finalization owner removes that exact detached generation after acquiring the lock
- **AND** no other bridge generation is removed
- **AND** caller cancellation propagates only after detached-capacity tracking is finalized

### Requirement: Passthrough Responses request fields are shape-checked, not deep-validated

The service MUST treat the `input`, `tools` and `text.format.schema` fields of `/backend-api/codex/responses` and `/v1/responses` requests, and the `messages` field of `/v1/responses` requests, as opaque JSON: it MUST NOT re-validate or coerce their nested values against a JSON value schema, and the nested values MUST reach the upstream payload byte-for-byte except where a documented normalization (tool type aliases, input sanitation, instruction hoisting) rewrites them. The service MUST still enforce the top-level shape locally: `input` MUST be a string or an array, `tools` MUST be an array, and `messages` MUST be an array when present. Because the upstream serializer cannot emit JSON nested deeper than roughly 250 container levels, a passthrough field whose objects/arrays nest deeper than 200 levels MUST be rejected at validation time rather than failing later while the request is being serialized. A shape or depth violation MUST be rejected with HTTP 400, `error.type = "invalid_request_error"`, and `error.param` naming the offending field. Non-finite numbers (for example `1e400`, which `json.loads` accepts but JSON cannot represent) serialize as `null` in the forwarded payload. The OpenAPI document MUST still be generated for every request model that declares these fields.

#### Scenario: Non-array tools are rejected with the tools param

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with `tools` set to `null`, a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "tools"`
- **AND** no upstream connection is opened

#### Scenario: Non-array messages are rejected with the messages param

- **WHEN** a client sends `/v1/responses` with `messages` set to a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "messages"`

#### Scenario: Non-string non-array input is still rejected

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with `input` set to a number, boolean, or object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "input"`

#### Scenario: Deeply nested passthrough values are rejected with the field param

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` (HTTP or the Responses WebSocket) with `input`, `tools`, `messages` or `text.format.schema` containing objects/arrays nested more than 200 levels deep, or `/backend-api/codex/responses/compact` with `input` nested more than 200 levels deep (`input` is the only passthrough field the compact model declares; `/v1/responses/compact` guards `input` and `messages`)
- **THEN** the proxy returns HTTP 400 (or a `status: 400` WebSocket error event) with `error.type = "invalid_request_error"` and `error.param` naming the field (`text.format.schema` for the schema)
- **AND** no upstream connection is opened

#### Scenario: Nested passthrough values are forwarded verbatim

- **GIVEN** a Responses request whose `tools[i].parameters` and `input` content contain nested arrays, objects, floats such as `1.0`, booleans and nulls
- **WHEN** the service builds the upstream payload
- **THEN** the serialized `tools` and untouched `input` entries are byte-identical to the client's JSON
- **AND** the `/v1/responses` conversion yields the same upstream payload bytes as before passthrough handling

#### Scenario: OpenAPI generation succeeds

- **WHEN** `GET /openapi.json` is requested
- **THEN** the document is returned with `V1ResponsesRequest`, `ResponsesCompactRequest` and the `/backend-api/codex/responses` request body schemas present

### Requirement: Responses Lite signaling enforces all-turns reasoning context

Every final upstream Responses payload that codex-lb advertises as Responses
Lite—by the canonical HTTP header or the canonical per-request websocket
client-metadata marker, whether body-derived, bridge-preserved, or
continuity-trusted—MUST contain the exact JSON string
`reasoning.context = "all_turns"`. Before upstream serialization, the service
MUST create a reasoning object when it is omitted or null and MUST replace an
absent, null, blank, differently-cased, otherwise different-string, or
non-string context value with `"all_turns"`. It MUST preserve
`reasoning.effort`, `reasoning.summary`, and every unrelated reasoning member.

This normalization MUST be idempotent, MUST NOT establish Lite classification
or continuity trust, MUST NOT reject an otherwise-valid Lite request solely for
a context mismatch, and MUST NOT remove the Lite signal. For requests not
advertised as Lite, this normalization MUST leave the client-supplied reasoning
shape unchanged. An invalid non-object reasoning container remains subject to
the existing client-payload validation contract.

#### Scenario: Body-derived Lite HTTP request omits reasoning

- **WHEN** a normalized HTTP Responses body contains an `additional_tools` input item and omits or nulls `reasoning`
- **THEN** the final upstream HTTP body contains `reasoning.context = "all_turns"`
- **AND** the request carries the canonical Responses Lite HTTP header

#### Scenario: Existing Lite reasoning members survive normalization

- **WHEN** a Responses Lite body includes reasoning effort, summary, or extension members and its context is absent, null, blank, differently cased, another string, or a non-string value
- **THEN** the final upstream body contains the exact string `reasoning.context = "all_turns"`
- **AND** every unrelated reasoning member retains its client-supplied value

#### Scenario: Compact Lite request uses the same invariant

- **WHEN** a compact request is advertised upstream as Responses Lite
- **THEN** its final upstream POST body contains `reasoning.context = "all_turns"`
- **AND** it carries the canonical Responses Lite HTTP header

#### Scenario: Websocket and HTTP fallback agree on Lite reasoning

- **WHEN** a body-derived Lite request is prepared for upstream websocket transport
- **THEN** its `response.create` body contains both the canonical Lite client-metadata marker and `reasoning.context = "all_turns"`
- **BUT WHEN** the websocket handshake falls back to upstream HTTP
- **THEN** the HTTP body retains `reasoning.context = "all_turns"`, the marker is absent, and the canonical Lite HTTP header is present

#### Scenario: HTTP bridge transformations preserve the invariant

- **GIVEN** an HTTP bridge request established Lite mode from an `additional_tools` prefix
- **WHEN** bridge trimming or retry builds a final `response.create` body whose input delta no longer contains that prefix
- **THEN** the body retains the internally derived canonical Lite marker
- **AND** it contains `reasoning.context = "all_turns"`

#### Scenario: Trusted marker-only continuation is normalized

- **GIVEN** a same-model websocket continuation has trusted Lite continuity to its referenced previous response
- **WHEN** its incremental body carries the canonical marker but omits the original `additional_tools` prefix
- **THEN** the final upstream body contains `reasoning.context = "all_turns"`
- **AND** the canonical marker remains present

#### Scenario: Untrusted and non-Lite requests are not normalized

- **WHEN** a non-Lite request supplies arbitrary reasoning context, an inbound Lite header, or a stale or otherwise untrusted websocket marker
- **THEN** the existing signal rules omit or strip the untrusted Lite signal
- **AND** this normalization does not alter the request's client-supplied reasoning shape

### Requirement: Fresh durable HTTP bridge preserves client-unanchored full resends

The service MUST preserve a client-unanchored full resend as the first request
on a fresh durable HTTP bridge. This applies when the request resolves a hard
durable conversation, has no client-supplied `previous_response_id`, has a
stored prefix matching that durable conversation, and has neither a reusable
local bridge nor a forwardable remote owner. The input projected through the
existing fresh-replay bookkeeping filter MUST also either retain completed
assistant output before fresh user input or have a suffix consisting only of
complete, self-contained direct tool call/output pairs that exactly match a
durable manifest of every call ID and call type emitted by the prior response.
The manifest MUST include every observed tool-call `output_item.added` event,
MUST require a matching `output_item.done` event with the same call ID and type,
MUST reconcile any tool calls present in terminal response output, and MUST be
persisted atomically with that response's durable alias. An incomplete,
conflicting, or malformed lifecycle MUST persist an unknown manifest rather
than a partial one. Duplicate call IDs in added, done, or terminal output MUST
invalidate the manifest even when their call types match. The serialized
manifest MUST bind its call map to the exact durable response ID, and a response
ID mismatch MUST be treated as an unknown manifest so rolling-upgrade writers
that do not know the manifest column cannot leave stale calls on a newer
response.
If a response contains a client-settled call type that the direct tool-loop
proof cannot represent, including `computer_call` or `mcp_approval_request`,
the service MUST treat the entire manifest as unknown rather than persist a
partial manifest for any parallel supported calls.
The service MUST submit that safe original full resend without adding
`previous_response_id`, MUST retain the durable preferred owner and hard
affinity, MUST NOT move the request through account-neutral replay, and MUST NOT
trim the stored prefix before that first send.

If the matching cumulative input omits prior output, contains an incomplete or
orphaned tool call/output sequence, omits any call in the durable manifest,
reuses a stored-prefix call ID, has no known durable manifest, or otherwise
lacks either safe context shape, the service MUST retain the existing
durable-anchor injection and prefix trimming behavior.

The service MUST NOT seed the newly created local session with the old durable
response in a way that re-injects the anchor before the original full resend is
submitted. Once the fresh request completes, ordinary live-session continuity
and trimming MAY resume from the newly completed response. Incremental requests
that rely on durable history, client-supplied anchors, owner-unavailable
handling, and existing account-neutral replay eligibility remain unchanged.

Complete full-resend eligibility MUST be represented by an immutable request-local proof that binds the current payload fingerprint to the durable session ID, owner account, latest response ID, stored count, stored fingerprint, and pending-tool-call manifest identity. The proof MUST be created only by the count, fingerprint, and retained-output or response-bound pending-tool-call verifier; it MUST NOT be accepted from a caller, persisted, deserialized, or ordinarily constructed, and mutation or durable-state substitution MUST invalidate it.

When that proof authorizes a fresh owner-bound bridge and the incoming affinity comes from a broad session header, the service MUST omit the downstream session/turn aliases from the fresh upstream connection and MUST NOT consult the broad legacy sticky row during account selection. It MUST retain the durable canonical bridge key, require the durable owner account, preserve Codex session behavior for subsequent turns, and leave the broad sticky row unchanged. Conflicting specific turn-state, previous-response, bridge, or file owners MUST still fail closed. This broad-alias reconciliation MUST NOT itself rebind the request to another account; existing account-neutral full-resend recovery after a genuine owner-unavailable result remains governed by its separate replay-safety requirements.

#### Scenario: Full resend opens a fresh bridge without a durable anchor
- **GIVEN** a client-unanchored full resend has a verified stored prefix and retained completed assistant output for a hard durable conversation
- **AND** no reusable local bridge or forwardable remote owner exists
- **WHEN** the service creates a fresh upstream WebSocket on the durable owner
- **THEN** its first `response.create` omits `previous_response_id`
- **AND** its input contains the original full resend
- **AND** its hard session affinity is retained

#### Scenario: Tool-loop resend does not require an assistant-message replay boundary
- **GIVEN** a verified client-unanchored full resend continues a tool loop with complete self-contained direct call/output pairs but no completed assistant-message boundary
- **AND** those calls and outputs exactly settle the durable prior-response call manifest
- **WHEN** it starts on a fresh durable bridge
- **THEN** the service submits the original request once on the durable owner
- **AND** retained-output checks used for cross-account replay do not block or rewrite that first send

#### Scenario: Omitted parallel tool call remains anchored
- **GIVEN** the durable prior-response manifest contains two parallel call IDs
- **WHEN** a matching cumulative input carries a complete call/output pair for only one ID
- **THEN** the service retains the durable `previous_response_id`
- **AND** it does not classify the suffix as a complete tool-loop resend

#### Scenario: Incomplete tool-call lifecycle keeps manifest unknown
- **GIVEN** a response emits added events for two parallel tool calls
- **AND** only one call reaches a matching done event before `response.completed`
- **WHEN** the durable response alias is persisted
- **THEN** its tool-call manifest is unknown rather than a one-call partial manifest
- **AND** a later direct tool-loop full resend remains anchored

#### Scenario: Unsupported parallel client-settled call keeps manifest unknown
- **GIVEN** a response emits a supported direct call and a parallel client-settled call that the replay proof cannot represent
- **WHEN** the durable response alias is persisted
- **THEN** its tool-call manifest is unknown rather than a partial supported-call manifest
- **AND** a later resend that settles only the supported call remains anchored

#### Scenario: Legacy durable row remains anchored
- **GIVEN** a durable response row predates the call-manifest migration or otherwise has an unknown manifest
- **WHEN** a matching cumulative input contains direct tool call/output items without a completed assistant-message boundary
- **THEN** the service retains the durable `previous_response_id`

#### Scenario: Older writer advances response without manifest
- **GIVEN** a durable row has a response-bound tool-call manifest
- **WHEN** an older rolling-upgrade writer advances `latest_response_id` without updating the manifest column
- **THEN** readers treat the mismatched manifest as unknown
- **AND** a later direct tool-loop full resend remains anchored

#### Scenario: Cumulative prompt without prior output remains anchored
- **GIVEN** a matching cumulative input contains fresh user input but omits the prior assistant output
- **WHEN** no reusable bridge exists for its hard durable conversation
- **THEN** the service retains the durable `previous_response_id`
- **AND** it trims the verified stored prefix through the existing anchored path
- **AND** it does not classify the original unanchored cumulative input as a safe fresh-upstream retry

#### Scenario: Failed owner forwarding preserves omitted response context
- **GIVEN** a matching cumulative input omits prior assistant output and initially resolves to a forwardable durable owner
- **WHEN** owner forwarding fails before any downstream output and the service performs local takeover
- **THEN** the local recovery request retains the durable `previous_response_id`
- **AND** it trims the verified stored prefix instead of submitting the cumulative input unanchored
- **AND** the injected anchor is not eligible for an unanchored fresh-upstream retry

#### Scenario: Refreshed takeover context no longer matches the resend
- **GIVEN** owner forwarding fails and the refreshed durable takeover row has different stored-input metadata
- **WHEN** the cumulative input cannot prefix-match that refreshed row
- **THEN** the service fails closed instead of pairing the refreshed response ID with stale prefix metadata

#### Scenario: Refreshed takeover account replaces stale routing
- **GIVEN** owner forwarding fails and the refreshed durable takeover row names a different account
- **WHEN** the service performs local takeover
- **THEN** the recovery session requires the refreshed account rather than the initial stale account
- **AND** a refreshed account that conflicts with another required owner fails closed

#### Scenario: Live bridge trimming remains unchanged
- **GIVEN** the durable conversation still has a reusable live bridge
- **WHEN** a trimmable full resend continues that live session
- **THEN** the existing session-level anchor and prefix-trimming behavior remains available

#### Scenario: stale broad session owner does not loop a verified full resend
- **GIVEN** a hard durable session is owned by account B and records a latest
  response ID, positive input count, and full fingerprint
- **AND** no live local bridge or active remote owner remains
- **AND** a broad legacy session-header sticky row points at account A
- **WHEN** the client sends a full resend whose stored prefix matches both
  durable values and whose suffix retains completed prior output before fresh
  input or exactly settles the response-bound pending-tool-call manifest
- **THEN** the service opens the fresh bridge on account B
- **AND** it submits the complete payload without `previous_response_id`
- **AND** it does not consult or rewrite the broad legacy row
- **AND** it omits downstream session and turn aliases from the fresh upstream
  connection

#### Scenario: recovered bridge keeps incremental continuity
- **GIVEN** a verified full resend established a fresh owner-bound bridge
- **WHEN** that bridge completes and a later incremental turn arrives
- **THEN** the later turn remains on the same account
- **AND** the bridge may use the newly established response anchor

#### Scenario: incomplete resend cannot bypass a broad owner conflict
- **GIVEN** a durable owner conflicts with a broad legacy session owner
- **WHEN** the input prefix does not match, retained prior output is absent, the
  payload or durable identity changes after verification, or the request is
  incremental
- **THEN** no full-resend proof authorizes reconciliation
- **AND** the request retains existing anchor and fail-closed owner behavior

#### Scenario: specific owner conflict remains fail-closed
- **GIVEN** a verified full resend also contains a turn-state,
  previous-response, bridge, or file owner that conflicts with the durable
  owner
- **WHEN** continuity is resolved
- **THEN** the request fails with `continuity_owner_conflict`
- **AND** the broad-session reconciliation does not choose either account

### Requirement: Subscription Responses adapts unsupported explicit prompt-cache controls

The proxy MUST omit public explicit prompt-cache controls from an HTTP Responses
request routed to the Codex subscription upstream. This applies to
`prompt_cache_options` and `prompt_cache_breakpoint` on a supported prompt
content block. It MUST preserve the prompt content and ordering and MUST
continue forwarding a client-supplied `prompt_cache_key` unchanged.

A successful HTTP response for such a request MUST include
`X-Codex-LB-Prompt-Cache-Mode: subscription-implicit`, because subscription
implicit caching and account affinity do not provide the exact explicit-prefix
semantics requested by the client. The proxy MUST NOT include that downgrade
header when the request is routed to an OpenAI-compatible model source, and the
model-source wire payload MUST preserve the explicit controls unchanged.

#### Scenario: Subscription request falls back to implicit caching

- **GIVEN** a `/v1/responses` request contains a `prompt_cache_key`,
  `prompt_cache_options`, and an explicit breakpoint on an `input_text` block
- **WHEN** the request is routed to a subscription account
- **THEN** the upstream subscription payload omits `prompt_cache_options` and
  the breakpoint
- **AND** preserves the input text, input order, and `prompt_cache_key`
- **AND** a successful response reports
  `X-Codex-LB-Prompt-Cache-Mode: subscription-implicit`

#### Scenario: Model source preserves public explicit-cache semantics

- **GIVEN** the same `/v1/responses` request is routed to an OpenAI-compatible
  model source
- **THEN** the model-source payload retains `prompt_cache_options`, every
  explicit breakpoint, and `prompt_cache_key`
- **AND** the response does not report a subscription implicit fallback

### Requirement: A stuck eventless HTTP bridge reattach invalidates its durable anchor for full-resend clients

When a proxy-injected durable `previous_response_id` anchor causes an HTTP bridge `response.create` to reach the eventless client-safe timeout (`missing_response_created_timeout`) without producing `response.created` or any other response event, and the client's own incoming payload for that request already looked like a full conversation resend, the proxy MUST clear that durable session's stored anchor — `latest_response_id`, `latest_input_item_count`, `latest_input_full_fingerprint`, and `latest_pending_tool_calls_json` — before releasing durable ownership, fenced to the session's current owner epoch. A client-supplied `previous_response_id` MUST NOT be cleared by this path. A proxy-injected anchor on a payload that did not look like a full resend (a genuine delta-only continuation) MUST NOT be cleared by this path, because the client has no other way to convey prior conversation state once the anchor is gone. The durable session's turn-state and identity MUST remain intact so a later request can still reattach without the stale anchor.

#### Scenario: Full-resend proxy-injected anchor times out and is cleared

- **GIVEN** a durable HTTP bridge session has a stored `latest_response_id`
- **AND** a fresh reattach injects that response id as `previous_response_id` because the client sent none
- **AND** the client's incoming payload already looked like a full conversation resend
- **WHEN** the resulting `response.create` reaches the eventless client-safe deadline with no `response.created` or other response event
- **THEN** the terminal `missing_response_created_timeout` failure is delivered as before
- **AND** the durable session's `latest_response_id`, input fingerprint, and pending tool-call manifest are cleared under the current owner epoch
- **AND** the durable session's turn-state alias remains available for reattachment

#### Scenario: Next reattach takes the fresh no-anchor path

- **GIVEN** a durable session's anchor was cleared after a stuck eventless timeout on a full-resend payload
- **WHEN** a later request reattaches to the same durable session with no `previous_response_id`
- **THEN** the proxy does not inject a `previous_response_id` anchor for that request
- **AND** the request proceeds on the existing unanchored full-resend/fresh path instead of repeating the cleared anchor

#### Scenario: Delta-only proxy-injected anchor is left intact

- **GIVEN** a fresh reattach injects a durable `previous_response_id` anchor because the client sent none
- **AND** the client's incoming payload did not look like a full conversation resend
- **WHEN** the resulting `response.create` reaches the eventless client-safe deadline with no `response.created` or other response event
- **THEN** the terminal `missing_response_created_timeout` failure is delivered as before
- **AND** the durable session's `latest_response_id` is not cleared
- **AND** the next reattach on that session still injects the same anchor, preserving the client's only way to convey prior context

#### Scenario: Client-supplied anchor is left untouched

- **GIVEN** a request supplied its own `previous_response_id` rather than receiving a proxy-injected one
- **WHEN** its `response.create` reaches the eventless client-safe timeout
- **THEN** the durable session's `latest_response_id` is not cleared
- **AND** later requests may still resolve that alias per existing continuity rules

#### Scenario: Fenced anchor-clear loses to a newer owner

- **GIVEN** a durable session's owner epoch has advanced past the retiring session's epoch before the anchor-clear write executes
- **WHEN** the stuck-timeout handling attempts to clear the anchor
- **THEN** the write is a no-op
- **AND** the newer owner's durable state is left untouched

### Requirement: Durable bridge claims fence out the retiring predecessor

A successful `claim_live_session` over an existing durable row MUST advance the owner epoch, including when the claiming instance already owns the row, so fenced updates issued by the predecessor local session — its release and any outstanding renewals — no-op after the claim instead of racing the successor. The claim's write MUST be authoritative: it MUST set every ownership field (owner, process epoch, owner epoch, lease, state, account) unconditionally, so a concurrent write committing between the claim's read and its commit cannot survive into the claim's result. A claim that returns successfully MUST reflect the claimant as the live owner. A session creator that has already lost its inflight registry slot MUST NOT claim the durable row at all: claiming would advance the epoch past the session that won and fence that winner's own renewals out of a row it legitimately owns. A creator that nonetheless fails to register — because another session already holds the registry slot for its key, or because a replacement creator holds the in-flight slot and has not published its session yet — MUST hand the epoch it claimed to that registered session when both point at the same row and selected the same account. When the registered session selected a DIFFERENT account it no longer shares the row — the claim rewrote the row's account binding and cleared its continuity aliases — so the creator MUST release the row rather than preserve it, letting that session be fenced promptly instead of dispatching against a row bound elsewhere, so the winner's renewals keep matching, and MUST NOT release the durable row. Independently of that handoff, a renewal fenced by an epoch advance from THIS process — matching the instance ID, the owner process epoch, and the row's account binding — MUST adopt the newer epoch when the renewing session still holds the registry slot for its key — that is a superseded creator, not an ownership loss — while a session whose slot a different local session holds MUST still be evicted with the existing retryable instance-mismatch contract: it claimed last, so its epoch is current and its fenced release would otherwise close the row out from under the registered session. Concurrent claims over the same row MUST serialize on the epoch: the write MUST land only if the epoch still matches the claim's read, and a losing claim MUST retry against fresh state (within a bounded budget), so two claimants can never hold colliding fences. A claim that loses to a concurrent writer MUST revalidate its takeover permission against the fresh read rather than reusing the caller's pre-claim decision, so a loser cannot steal the winner's now-live lease; a live foreign owner then fails closed and the real owner is reported. A caller retrying a claim MUST NOT restore takeover permission against a live foreign owner either, so the fail-closed outcome survives the retry rather than being undone by a fresh claim. The snapshot a claim returns MUST be the state that claim itself wrote — not a post-commit re-read, which a later claim's commit could have already overwritten with its own epoch.

#### Scenario: A successor claim fences the predecessor's release

- **GIVEN** a retiring bridge session and a successor session claiming the same durable row on the same instance
- **WHEN** the successor's claim commits before the predecessor's release lands
- **THEN** the release is fenced out by the advanced epoch and the row stays ACTIVE and owned by the instance

#### Scenario: A release committing mid-claim does not corrupt the claim

- **GIVEN** the predecessor's release commits between the successor claim's read and its write
- **WHEN** the claim commits
- **THEN** the claim's result reflects the claimant as the live owner with the advanced epoch
- **AND** the request proceeds instead of failing with `bridge_instance_mismatch`

#### Scenario: Racing successor claims cannot share an epoch

- **GIVEN** two successor claims that both read the same owner epoch before either writes
- **WHEN** both commit
- **THEN** they land on distinct epochs, with the loser retrying against fresh state

#### Scenario: A losing claimant does not steal the winner's lease

- **GIVEN** two replicas recovering the same released row, both permitted to take over
- **WHEN** one wins and the other re-reads the winner's now-live lease
- **THEN** the loser fails closed and reports the winner as owner instead of claiming the row
- **AND** the caller does not retry the claim with takeover permission against that live owner

#### Scenario: A rejected creator leaves the registered winner's row alone

- **GIVEN** an inflight waiter was evicted and a replacement session won the registry slot
- **WHEN** the stale creator finishes creating its session
- **THEN** it does not claim the durable row, so the winner's epoch is untouched
- **AND** it closes its own session without releasing the durable row, leaving the winner's row live
- **AND** if it had already claimed (eviction landing during the claim), the winner adopts that epoch so its renewals keep matching

#### Scenario: The registered session adopts a same-instance epoch advance

- **GIVEN** a session still holding the registry slot for its key whose durable row was advanced by this instance
- **WHEN** its lease renewal is fenced by that newer epoch
- **THEN** it adopts the epoch and keeps renewing instead of being evicted

#### Scenario: A newer process incarnation still fences the predecessor

- **GIVEN** two process incarnations sharing a configured instance ID across a graceful restart
- **WHEN** the successor claims and the predecessor's session renews
- **THEN** the predecessor is evicted rather than adopting the successor's epoch

#### Scenario: An advance that rebound the row to another account is not adopted

- **GIVEN** a registered session whose durable row was advanced and rebound to a different account
- **WHEN** its renewal is fenced by that advance
- **THEN** it is evicted rather than adopting the epoch, so it never dispatches on the other account's row

#### Scenario: A session that lost its slot is still evicted

- **GIVEN** a session whose registry slot is now held by a different local session
- **WHEN** its renewal is fenced
- **THEN** it is evicted and the retryable instance-mismatch error is raised

#### Scenario: A replacement that has not published yet is still the winner

- **GIVEN** a replacement creator holds the in-flight slot and has claimed but not yet registered its session
- **WHEN** the stale creator fails and settles
- **THEN** it does not release the durable row, which the replacement is about to publish against

#### Scenario: A row rebound away from the winner is released

- **GIVEN** a registered winner on one account and a stale creator whose claim rebound the row to another
- **WHEN** the stale creator settles
- **THEN** it releases the row instead of preserving it, so the winner is fenced promptly rather than dispatching against a row bound elsewhere

#### Scenario: A sole creator still releases its row

- **GIVEN** a failed creator with no registered session and no replacement in flight
- **WHEN** it settles
- **THEN** it releases the durable row rather than leaking it

#### Scenario: Foreign-claim rejection is unchanged

- **GIVEN** a durable row owned by another instance with a live lease
- **WHEN** a claim without takeover permission runs
- **THEN** the owner and lease remain unchanged, as before

### Requirement: Thread-goal routing uses payload thread identity

Thread-goal get, set, and clear operations carrying a nonblank payload
`threadId` MUST select account locality from that exact thread identity,
combined with the process session when available. An explicit client turn
state remains hard continuity and MUST retain its existing precedence or
conflict behavior. Other generic request headers MUST NOT cause one sibling
thread's goal operation to follow another sibling's locality. Existing
protocol forwarding and error behavior MUST remain unchanged.

#### Scenario: Sibling goal operations follow their own threads

- **GIVEN** sibling threads share a process session but have distinct `threadId` values
- **WHEN** each invokes thread-goal get, set, or clear
- **THEN** each operation uses its own bounded thread locality

### Requirement: HTTP bridge retry circuits count each upstream send attempt at most once

For an HTTP Responses bridge request, multiple local failure observers that
classify the same upstream `response.create` send attempt MUST contribute at
most one consecutive retry-circuit failure and at most one durable failure
persistence operation. A separately dispatched retry or replay MUST be treated
as a new send attempt and MAY contribute the next eligible failure under the
existing retry-circuit policy.

The proxy MUST capture the attempt being classified before awaiting recovery,
reconnection, settlement, or pending-request ownership that can dispatch a
newer attempt. When stale ownership is classified while holding the pending
lock, the classified request set and its attempt selection MUST come from that
same snapshot. A send attempt that is disarmed by send-failure or cancellation
cleanup, or that observes a matching upstream response lifecycle event before
its first failure claim, MUST NOT add a retry-circuit failure. A matched
response lifecycle event MUST mark its attempt observed even when downstream
delivery or ordinary response-event accounting is intentionally deferred.

The proxy MUST distinguish a failure path with no attempt identity from one
whose attempt identity is present but ineligible or ambiguous. Only the former
MAY preserve legacy unscoped recording. An ineligible attempt or multiple
eligible candidates MUST NOT fall back to an unscoped failure. Duplicate
observers MUST wait for the first claim's settlement and then use the current
circuit count; they MUST NOT expose a cached historical count after a later
failure or successful clear. Deduplication MUST NOT change existing failure
classes, thresholds, cooldowns, continuity guards, or cross-replica conflict
merging.

#### Scenario: reader and downstream watchdogs observe one eventless send

- **GIVEN** one hard-affinity HTTP bridge `response.create` send remains eventless
- **AND** the upstream reader watchdog and downstream stream-idle watchdog both classify that send
- **WHEN** both observers report the retry-circuit failure
- **THEN** the circuit's consecutive failure count increases by exactly one
- **AND** the failure is durably persisted exactly once
- **AND** the default two-failure circuit does not open from that send alone

#### Scenario: a separately dispatched retry is a second failure

- **GIVEN** one send attempt has already contributed one retry-circuit failure
- **WHEN** a later retry or replay dispatches a new `response.create` and that attempt also fails eligibility checks
- **THEN** the new attempt contributes a second failure
- **AND** the existing threshold and cooldown behavior may open the circuit

#### Scenario: a delayed old observer cannot count a newer attempt

- **GIVEN** an observer captured attempt A before recovery dispatched attempt B
- **AND** attempt A has already contributed its failure
- **WHEN** the delayed observer resumes after attempt B is current
- **THEN** it does not increment or persist another failure for attempt A
- **AND** it does not mark attempt B as recorded
- **AND** it observes the current circuit count, including attempt B's independent failure

#### Scenario: an upstream response wins the timeout race

- **GIVEN** a watchdog is evaluating an eventless send attempt
- **WHEN** a matching upstream response lifecycle event is observed before the attempt's first failure claim
- **THEN** that attempt does not contribute a retry-circuit failure

#### Scenario: a deferred reasoning prelude wins the timeout race

- **GIVEN** a matched reasoning lifecycle event is held for deferred downstream delivery
- **AND** ordinary response-event accounting remains zero for that prelude
- **WHEN** an eventless failure observer evaluates the same send attempt
- **THEN** the attempt is already marked as response-observed
- **AND** it does not contribute or persist a retry-circuit failure
- **AND** deferred-delivery and downstream-visibility behavior remain unchanged

#### Scenario: multiple pending attempts are ambiguous at a shared failure boundary

- **GIVEN** a shared cleanup boundary contains multiple distinct eligible send attempts
- **WHEN** the boundary cannot attribute its failure to exactly one physical send
- **THEN** it does not fall back to an unscoped retry-circuit failure
- **AND** it does not mark any candidate attempt as recorded
- **AND** a later observer with an exact attempt identity can still record each genuine failure independently

#### Scenario: pending-lock wait cannot replace the classified attempt

- **GIVEN** stale cleanup captures attempt A for a request before acquiring pending ownership
- **AND** recovery installs attempt B while cleanup is waiting for the pending lock
- **WHEN** cleanup later records the classified failure
- **THEN** it retains attempt A's identity
- **AND** it does not mark attempt B as recorded

#### Scenario: a cleared circuit is not recreated by a delayed duplicate

- **GIVEN** a send attempt contributed a failure and a later successful terminal response cleared the circuit
- **WHEN** another observer of the old send attempt resumes
- **THEN** the old observer does not recreate or persist the cleared failure
- **AND** it receives the current circuit count of zero

### Requirement: Source-owned models are not served over the WebSocket transport

Model sources are reachable only from the HTTP request path. When a WebSocket
Responses session requests a model that resolves to an enabled,
Responses-capable OpenAI-compatible model source, the system SHALL NOT dispatch
the request to a subscription account.

The check SHALL be applied on the connect path before account selection, and
SHALL also be applied to every prepared `response.create`, so that a turn which
switches to a source-owned model on an already-open subscription upstream is
also rejected instead of being forwarded.

Both checks SHALL evaluate the client's raw requested model, captured before
API-key enforcement normalizes model aliases (for example `gpt-5-high` to
`gpt-5`), alongside the normalized model — the same candidate list the HTTP
handlers build from `raw_source_model`, including substituting the API key's
`enforced_model` and, when fast mode is prohibited and the raw model is a
fast-mode alias, replacing the raw candidate with the normalized model. A
source that exposes an alias-named model MUST be matched on the WebSocket
transport whenever the HTTP path would route to it.

Both checks SHALL apply only to requests that are eligible for model-source
routing on the HTTP request path, judged on the full client input before any
WebSocket-specific trimming or anchor injection. A request whose input ends
with a terminal `compaction_trigger` item, or that references uploaded files
(`input_file` / file-backed `input_image` items), is excluded from source
routing over HTTP — the former is served by the upstream compact flow on the
turn's owner account, the latter is pinned to the subscription account that
received the upload — and MUST NOT be failed by either WebSocket guard even
when its model also resolves to an enabled source. Such requests proceed to
subscription account selection and the owner-routing rules, exactly as they
would after the HTTP route skips source selection. A malformed compaction
trigger (repeated, or not the final top-level input item) SHALL keep the
guards active: the HTTP route rejects that payload with a 400, the WebSocket
path forwards it verbatim, and the exclusion changes neither.

Both failures MUST use error code `model_source_requires_http_transport`. On the
connect path the failure MUST be emitted as a service-level connect failure
(HTTP status `503`), so that Codex clients fall back to the HTTP transport,
where source routing is applied. For a prepared `response.create` on an
established session the failure MUST be emitted as a terminal error for that
turn, and any usage reservation held for the turn MUST be released.

When source resolution is unavailable, the WebSocket transport MUST fall back to
subscription account selection rather than failing the request. The resolution
runs after a turn's usage reservation is acquired but before it is registered
for cleanup, so a propagating failure would end the session and strand the
reservation; the degraded behaviour is the pre-change one, where the
subscription upstream rejects the model. This applies to the WebSocket transport
only — the HTTP request path MUST continue to surface resolution failures, since
silently routing source traffic to a subscription account would be worse there.

#### Scenario: Source-owned model over WebSocket fails the connect

- **GIVEN** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a client opens a WebSocket Responses session requesting model `m`
- **THEN** the system fails the connect with error code `model_source_requires_http_transport`
- **AND** no subscription account is selected for the request

#### Scenario: Later turn switching to a source-owned model is rejected

- **GIVEN** a WebSocket Responses session already has an open subscription-account upstream
- **AND** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a subsequent `response.create` requests model `m`
- **THEN** the system emits a terminal error with code `model_source_requires_http_transport`
- **AND** the frame is not forwarded to the subscription account on the open upstream
- **AND** the turn's usage reservation is released

#### Scenario: An alias-named source model is rejected despite normalization

- **GIVEN** an enabled OpenAI-compatible model source exposes model `gpt-5-high` with Responses support
- **AND** an API key whose `allowed_models` contains exactly `gpt-5-high`
- **WHEN** the key sends a WebSocket `response.create` for `gpt-5-high`, which enforcement normalizes to `gpt-5`
- **THEN** the source-ownership check also considers the raw `gpt-5-high` candidate
- **AND** the request is rejected with `model_source_requires_http_transport` on the connect path and on socket reuse alike

#### Scenario: A file-referencing turn is dispatched to its pinned account, not failed

- **GIVEN** a WebSocket Responses session already has an open subscription-account upstream
- **AND** a later `response.create` references an uploaded `input_file` pinned to that account
- **AND** the request's model is also exposed by an enabled model source
- **WHEN** the turn is prepared for the open socket
- **THEN** the reuse guard does not fail the turn with `model_source_requires_http_transport`
- **AND** the turn is forwarded to the pinned subscription account

#### Scenario: A terminal compaction trigger is not failed by the WebSocket guards

- **GIVEN** a `response.create` whose final top-level input item is a `compaction_trigger`
- **AND** the request's model is also exposed by an enabled model source
- **WHEN** the request reaches the connect path or an already-open subscription upstream
- **THEN** neither WebSocket guard fails the request with `model_source_requires_http_transport`
- **AND** the connect path proceeds to subscription account selection, and an open upstream receives the turn

#### Scenario: An API key that enforces a source-owned model is rejected

- **GIVEN** an API key whose `enforced_model` resolves to an enabled model source
- **WHEN** the key opens a WebSocket Responses session requesting any model
- **THEN** the enforced model is resolved against the model sources
- **AND** the session fails with `model_source_requires_http_transport`

#### Scenario: Subscription models are unaffected

- **GIVEN** a model that is not served by any enabled model source
- **WHEN** a client opens a WebSocket Responses session requesting that model
- **THEN** account selection proceeds unchanged

#### Scenario: Source resolution failure falls back to subscription selection

- **GIVEN** the model-source catalog cannot be read
- **WHEN** a client opens a WebSocket Responses session
- **THEN** account selection proceeds as it did before the guard existed
- **AND** the session is not terminated by the resolution failure

### Requirement: Terminal append failure preserves authoritative settlement

When durable append of a terminal HTTP-bridge event raises after the operation was acknowledged, the proxy MUST attempt to persist the intended terminal operation state through the same operation, session, instance, and owner-epoch fence. Cancellation MUST be deferred through the append and any required fallback settlement. The event spool MUST remain incomplete, and the persistence failure MUST NOT replace or block the terminal event and end-of-stream marker already selected for downstream delivery. A rejected or failed fallback settlement MUST be logged and MUST NOT bypass the owner fence or overwrite a newer operation attempt admitted under the same owner epoch.

#### Scenario: Terminal append exception settles the current owner operation

- **GIVEN** an acknowledged HTTP-bridge operation owned by the current session epoch
- **WHEN** durable terminal-event append raises
- **THEN** the operation is persisted in the intended terminal state
- **AND** its event spool remains incomplete
- **AND** the terminal event and end-of-stream marker are queued before fallback settlement can stall
- **AND** reconnect or recovery does not observe the operation as acknowledged work

#### Scenario: Grouped failures deliver every sibling before settlement

- **GIVEN** one upstream error selects terminal failures for multiple pending operations
- **WHEN** the first operation's fallback settlement stalls
- **THEN** every selected operation attempts its owner-fenced terminal append before any terminal queue is exposed
- **AND** every selected operation then receives its terminal event and end-of-stream marker before fallback settlement
- **AND** sibling delivery does not wait for the first fallback settlement
- **AND** cancellation is preserved as the final outcome only after every pre-delivered sibling finishes settlement and finalization
- **AND** one sibling's finalization failure does not prevent later siblings from settling or replace pending cancellation

#### Scenario: Cancellation preserves terminal delivery authority

- **GIVEN** terminal append finishes while relay cancellation is deferred
- **WHEN** the append result becomes available
- **THEN** the terminal event and end-of-stream marker are queued
- **AND** a completed-delivery scope is marked authoritative before cleanup can deactivate it
- **AND** cancellation during that delivery-authority claim does not skip required fallback settlement
- **AND** cancellation is preserved only after delivery and required settlement

#### Scenario: Stale owner cannot settle after terminal append exception

- **GIVEN** an HTTP-bridge operation whose owner epoch has advanced
- **WHEN** the stale batcher encounters a terminal-event append exception
- **THEN** fallback settlement is rejected by the durable owner fence
- **AND** the stale batcher does not mutate the operation state

#### Scenario: Newer retry rejects delayed fallback settlement

- **GIVEN** terminal append committed its operation state before reporting an exception
- **AND** a retry under the same owner epoch has since reset the operation to submitted
- **WHEN** fallback settlement for the prior attempt runs
- **THEN** the fallback is rejected by an immutable recovery-attempt generation plus operation-state and persisted upstream-response identity fence
- **AND** the newer submitted attempt remains unchanged

#### Scenario: Replay alias preserves the acknowledged-attempt fence

- **GIVEN** a replay whose client-visible response alias differs from its persisted upstream response ID or whose active upstream response ID was reset before a replacement response was created
- **WHEN** durable terminal-event append raises
- **THEN** fallback settlement compares the acknowledged or already terminal operation against every response identity that may remain persisted when a replacement acknowledgement update fails
- **AND** persists the intended client-visible terminal response ID when present
- **AND** otherwise preserves the known upstream response ID

#### Scenario: Successful terminal append remains atomic and replayable

- **WHEN** durable terminal-event append succeeds
- **THEN** the terminal event and intended operation state are persisted atomically
- **AND** the completed event spool remains eligible for replay

### Requirement: Direct WebSocket scope cleanup has a bounded normal-operation budget

When a direct Responses WebSocket scope exits while the process is not using an
active shutdown drain deadline, the proxy MUST allow its existing scope
finalization task a fixed five-second bounded observation budget, separate from
the one-second generic child-task cancellation timeout. The finalizer MUST
continue to own request finalization and lease cleanup through that budget. If
the budget expires, the proxy MUST preserve the existing cancellation result,
leave unfinished cleanup tracked by the existing cleanup-task registry, and
MUST NOT cancel or silently abandon that cleanup solely because the observation
budget expired.

When an active shutdown drain deadline exists, the proxy MUST use the remaining
shared drain deadline instead of the normal-operation budget, so normal cleanup
allowance MUST NOT extend process shutdown.

#### Scenario: normal scope cleanup outlives generic child cancellation

- **GIVEN** a direct Responses WebSocket scope is cancelled while its existing
  request finalization takes longer than the generic one-second child-task
  cancellation timeout
- **AND** the finalization completes within the five-second normal-operation
  scope budget
- **WHEN** scope cleanup runs
- **THEN** the finalizer completes and request/lease ownership is released
- **AND** the scope preserves its cancellation result
- **AND** no cleanup task remains orphaned after the finalizer completes

#### Scenario: shutdown drain remains the upper bound

- **GIVEN** a direct Responses WebSocket scope is cancelled while an active
  shutdown drain deadline has less than five seconds remaining
- **WHEN** scope cleanup runs
- **THEN** the remaining shared drain deadline remains the upper bound
- **AND** the normal-operation five-second budget does not extend shutdown

### Requirement: Direct-egress upstream websockets do not offer permessage-deflate
When codex-lb opens a direct-egress upstream websocket (the Responses websocket or the
realtime live sideband over the `websockets` transport, used when no upstream proxy route
applies), it MUST NOT offer the `permessage-deflate` extension in the upstream handshake,
matching the routed and raw-handshake upstream transports, which already run uncompressed.
This requirement applies only to the proxy-to-upstream link: the server MUST continue to
negotiate `permessage-deflate` on the client-facing websocket, as required by the
downstream websocket ingress requirement.

#### Scenario: Direct upstream handshake omits the compression extension offer

- **WHEN** codex-lb connects an upstream websocket via the direct-egress `websockets` transport
- **THEN** the handshake does not offer `permessage-deflate` (the transport is invoked with compression disabled)
- **AND** persona headers, subprotocols, open-timeout, ping-timeout, message-size cap, and proxy resolution are unchanged

#### Scenario: Client-facing compression negotiation is unchanged

- **WHEN** a client connects to a Responses websocket route offering `permessage-deflate`
- **THEN** the server still negotiates `permessage-deflate` on the client-facing socket
- **AND** the downstream ingress budget continues to apply to the decompressed message size

### Requirement: Compact requests recover from quota-caused previous-response owner loss

When a compact request is pinned to a previous-response owner account, that pin is the only
continuity pin (no client-supplied turn-state owner and no input-file owner), and account
selection cannot return the pinned owner, the proxy MUST attempt account-neutral fresh-replay
recovery before surfacing the failure, provided every activation gate below holds. Outside the
gates the proxy MUST keep today's fail-closed failure for that request, MUST NOT send any part
of the payload to another account, and MUST record the fail-closed outcome on the continuity
fail-closed observability counter for the compact surface.

Recovery MUST activate only for quota-caused owner loss. At selection time, before the owner
was ever used for the request, the owner's persisted account status MUST be rate-limited or
quota-exhausted; an owner unselectable for any other reason (re-authentication required,
deactivated, paused, local capacity caps on an active account, or a failed status lookup)
stays owner-bound. An owner the selector skipped for routing policy — API-key assignment
scope, single-account routing, or a prior in-request exclusion — MUST stay owner-bound
regardless of its persisted quota status, because policy, not quota, caused that selection
loss. Mid-request, only a pre-visible quota or rate-limit failure of the pinned owner that
permits failover makes recovery eligible; post-selection authentication, refresh, transport,
timeout, and transient exclusions of the pinned owner keep their existing owner-bound
handling.

Recovery MUST NOT activate when the request carries a session identity (a turn-state or
session header) that can bind live or durable HTTP-bridge continuity, or when the resolved
affinity is session ownership (a Codex-session affinity key, a raw legacy session row, or a
conversation handle requiring an unambiguous owner): this recovery deliberately carries no
continuity-rebind machinery, so anything that would need rebinding stays owner-bound.
Prompt-cache and sticky-thread locality keys are advisory cache locality that ordinary sticky
selection already falls back from and do not block recovery.

Local verification MUST run against the exact serialized upstream-bound compact payload
without `previous_response_id`, after every transformation the compact serializer applies.
Transport-stage mutations that follow that serialization are outside the client payload being
proven and MUST be limited to proxy-injected, account-agnostic controls that are applied
identically to the owner send and the replay send (the Responses-Lite
`reasoning.context` control and inline image fetching); they carry no client or account
state, so the shared account-neutral rules — which the HTTP bridge replay paths likewise
apply to pre-transport serializations — retain their meaning. It
MUST require that serialized `input` to be a list of more than one item that is item-for-item
identical to the validated request `input`, so that no request whose wire history is dropped
or trimmed — including single-item collapse and oversized-input trim markers — is ever
replayed on another account, which could not resolve the omitted owner-resident context. It
MUST validate that same serialized payload against the shared account-neutral fresh-replay
rules: self-contained tool call/output pairing, no server-assigned item ids, no encrypted or
compaction state, no nonblank conversation or prompt handles, no account-scoped
file/container/vector handles, no hosted/MCP call state, and only recognized account-neutral
fields and shapes. Because a self-contained payload may still be a delta that relies on the
owner to hold the earlier conversation, the serialized `input` MUST additionally parse as a
transcript whose final segment retains completed assistant output followed only by fresh
client input, using the shared retained-prior-output rule anchored at the last assistant
message. Histories those gates cannot prove — including delta-shaped inputs without retained
assistant output and transcripts without fresh follow-up input — stay owner-bound.

This transcript-shape rule is the evidence ceiling of this scope: completeness relative to the
anchored conversation is not provable from the payload alone, and the durable prefix metadata
that could prove it is deliberately not consulted here (per the maintainer rescope of the
original change, which excluded durable-bridge plumbing from this recovery). A delta resend
that itself carries a completed assistant exchange ahead of the fresh input is therefore
indistinguishable from a full resend and MAY be recovered as the client's authoritative local
history — the same trust the shared account-neutral fresh-replay rules already grant a normal
turn that abandons an unavailable owner. Clients that resend partial histories under a
previous-response anchor accept summarization over that partial history when the owner is
quota-lost; the alternative surface is the current hard failure until the owner's quota
window resets.

For an eligible recovery, the proxy MUST remove `previous_response_id` from the upstream
compact payload, strip downstream session/turn affinity aliases from the upstream-bound
headers, exclude the unavailable owner account from the remaining attempts, and reselect among
the remaining eligible accounts with fallback enabled.

#### Scenario: Quota-excluded owner at selection time fails over with a verified full resend

- **GIVEN** account A owns the previous response referenced by a compact request and account B is eligible
- **AND** account A's persisted status is rate-limited or quota-exhausted
- **AND** the compact payload carries an account-neutral full-resend `input` that retains prior assistant output ahead of the new client input
- **AND** the request carries no session identity and no session-ownership affinity
- **WHEN** pinned account selection cannot return account A
- **THEN** the proxy sends the compact upstream exactly once on account B without `previous_response_id`
- **AND** the compact response is returned successfully

#### Scenario: Owner exhausts quota during the compact request

- **GIVEN** the pinned previous-response owner is selected for a compact request
- **AND** the upstream compact fails with a pre-visible quota or rate-limit error that permits failover
- **WHEN** reselection cannot return the now-excluded owner
- **THEN** the proxy applies the same account-neutral fresh-replay recovery on another eligible account
- **AND** the owner's quota failure is not surfaced to the client when the recovery succeeds

#### Scenario: Post-selection authentication failure on the pinned owner stays owner-bound

- **GIVEN** the pinned previous-response owner is selected for a compact request with an account-neutral full-resend `input`
- **AND** the upstream compact fails with `401` again after the forced token refresh, which excludes the owner from the remaining attempts
- **WHEN** reselection cannot return the now-excluded owner
- **THEN** the proxy surfaces the owner's authentication failure
- **AND** account-neutral fresh-replay recovery does not activate and no part of the payload is sent to another account

#### Scenario: Policy-skipped owner stays owner-bound despite quota status

- **GIVEN** a previous-response-pinned compact request whose owner account is excluded by API-key assignment scope or single-account routing
- **AND** that owner's persisted status is coincidentally rate-limited or quota-exhausted
- **WHEN** pinned account selection skips the owner
- **THEN** the request fails with the existing selection error
- **AND** account-neutral fresh-replay recovery does not activate

#### Scenario: Responses-Lite full resend behind a canonical tool bundle is recoverable

- **GIVEN** a quota-excluded previous-response-pinned compact request whose `input` opens with a canonical `additional_tools` bundle and its immediately following developer instruction
- **AND** the remaining transcript retains prior assistant output ahead of fresh client input and passes the account-neutral fresh-replay rules
- **WHEN** pinned account selection cannot return the owner
- **THEN** the shared canonical-Lite prefix handling recognizes the developer instruction
- **AND** the recovery replays the anchor-free payload on another eligible account

#### Scenario: Non-quota owner loss at selection time stays owner-bound

- **GIVEN** a previous-response-pinned compact request whose owner account is paused, deactivated, or requires re-authentication
- **WHEN** pinned account selection cannot return the owner
- **THEN** the request fails with the existing selection error
- **AND** account-neutral fresh-replay recovery does not activate
- **AND** the continuity fail-closed counter records the compact-surface outcome

#### Scenario: Non-neutral compact payload stays fail-closed

- **GIVEN** a pinned compact request whose `input` retains encrypted compaction state, server-assigned item ids, or account-scoped file handles
- **WHEN** the quota-excluded pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** no part of the payload is sent to another account
- **AND** the continuity fail-closed counter records the compact-surface outcome

#### Scenario: Delta-shaped history without retained output stays fail-closed

- **GIVEN** a pinned compact request whose multi-item `input` carries no retained assistant output ahead of fresh client input
- **WHEN** the quota-excluded pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** the proxy keeps the anchor and sends no part of the payload to another account

#### Scenario: History the wire serializer shortens stays fail-closed

- **GIVEN** a pinned compact request whose `input` loses history when serialized for upstream, either collapsing to a single item or being trimmed to a head, trim marker, and tail
- **WHEN** the quota-excluded pinned owner cannot be selected
- **THEN** the request fails with the existing selection or upstream error
- **AND** the proxy does not replay the shortened history on another account

#### Scenario: Session-identified compact stays owner-bound

- **GIVEN** a pinned compact request that carries a session or turn-state identity able to bind live or durable HTTP-bridge continuity
- **WHEN** the quota-excluded pinned owner cannot be selected
- **THEN** the request fails with the existing selection error
- **AND** account-neutral fresh-replay recovery does not activate

#### Scenario: Turn-state-pinned and file-pinned compacts remain owner-bound

- **GIVEN** a compact request pinned by a client-supplied turn-state owner or an input-file owner
- **WHEN** that owner account cannot be selected
- **THEN** the request fails closed with the existing continuity or selection error
- **AND** account-neutral fresh-replay recovery does not activate

#### Scenario: Additional owner pins record the fail-closed outcome

- **GIVEN** a compact request whose `previous_response_id` owner is also resolved by a turn-state or input-file pin naming the same account
- **WHEN** the pinned owner cannot be selected
- **THEN** the request fails closed with the existing selection error
- **AND** account-neutral fresh-replay recovery does not activate
- **AND** the continuity fail-closed counter records the compact-surface outcome

#### Scenario: Unresolvable previous-response owner remains fail-closed

- **GIVEN** a compact request whose `previous_response_id` owner cannot be resolved from any record
- **AND** more than one account is eligible
- **WHEN** the request is evaluated before account selection
- **THEN** the request fails with `previous_response_owner_unavailable`
- **AND** the proxy does not treat the missing owner as a selector result or replay on another account

### Requirement: Abrupt eventless upstream websocket drops remain account-neutral

When an HTTP bridge upstream websocket ends with a terminal transport message (a close or receive error) that carries no upstream-authored close frame, no established account-neutral transport classification (process-network, liveness-timeout, keepalive-timeout), and no application-layer output was observed for the pending requests (zero response events and no buffered reasoning prelude), the proxy MUST NOT write per-drop account error-health (`record_error`) for that unclassified `stream_incomplete` drop. The synthetic abnormal-closure code 1006, which RFC 6455 reserves and which adapters synthesize locally when the socket dies without a close frame, MUST be treated as frame-less. When such a drop settles its pending requests as failures, the proxy MUST record it into the windowed eventless account failure signal so that repeated eventless drops on the same account within the window still apply the drain penalty; drops recovered by the bounded pre-created replay keep their existing behavior, and drops already covered by an established account-neutral transport classification keep their existing contract and are not added to the signal. A failure that carries an upstream-authored close frame (including non-clean codes), occurs after application-layer output was observed, or arrives as a non-terminal protocol-invalid frame (for example a binary message) MUST keep the existing account penalty semantics. The per-bridge retry circuit MUST still record the failure at bridge scope.

#### Scenario: Sporadic frame-less drops do not strand a continuity-bound conversation

- **GIVEN** a conversation continuity-bound to account A via `previous_response_id`
- **AND** account A's upstream websocket drops three times with no close frame and zero response events, spread wider than the eventless failure window
- **WHEN** the client sends the next continuity-bound follow-up
- **THEN** account A's `error_count` receives no per-drop increment and stays below the error-backoff threshold
- **AND** the follow-up still routes to account A instead of failing with `previous_response_owner_unavailable`

#### Scenario: Repeated eventless drops inside the window still drain the account

- **GIVEN** an account whose upstream websocket drops with no close frame and zero response events on three separate bridge failures within the eventless failure window
- **WHEN** the third drop is recorded
- **THEN** the windowed eventless failure signal applies the minimum drain penalty so new turns avoid the account until its health probe succeeds

#### Scenario: Close frames and observed-output drops keep the account penalty

- **GIVEN** an upstream websocket ending that carries an upstream-authored close frame (for example 1008 or 1011) before any response event, or a frame-less drop after application-layer output was observed (streamed response events or a buffered reasoning prelude), or a non-terminal protocol-invalid binary frame
- **WHEN** the reader failure path settles the pending requests
- **THEN** the account penalty semantics are unchanged from before this change

### Requirement: Ambiguous HTTP bridge operations converge after owner loss

The durable HTTP bridge operation ledger MUST preserve duplicate suppression
while an operation is live or ambiguous, but an `unknown` or `acknowledged`
operation MAY transition to the terminal `abandoned` state only after its
`updated_at` is older than `max(1800 seconds,
http_responses_session_bridge_request_budget_seconds)`, no local canonical or
detached bridge request is pending for that operation, and the durable owning
session has no owner or an owner lease that has remained expired for at least
one additional durable lease period. The budget term is the effective
dashboard-managed value: a non-NULL
`dashboard_settings.http_responses_session_bridge_request_budget_seconds`
overrides the environment value, which overrides the 7200 second code default.

The maintenance sweep runs from the ring heartbeat outside any request
binding, so it MUST resolve that budget from one `SettingsCache` snapshot read
per sweep — never per candidate row and never inside the bridge registry lock.
When the snapshot cannot be read the sweep MUST log a warning and fall back to
the environment value rather than skipping the pass.

An ownerless session produced by a graceful lease release MUST remain
ineligible until its recorded `lease_expires_at` has aged through that same
durable lease period. Candidate reads MUST lock the operation and session rows
on PostgreSQL on both the normal predicate path and the oversized-protection
bounded-page path.

The transition MUST atomically compare the operation state, `updated_at`,
durable event-spool progress, session owner instance, and owner epoch. A
concurrent recovery claim, owner renewal/takeover, or status proof MUST win
over abandonment. A persisted nonterminal event MUST advance durable
event-spool progress; if that event commits after candidate selection but
before the abandonment compare-and-set, the compare-and-set MUST affect zero
rows. The operation row and all event history MUST remain available for normal
retention. The proxy MUST NOT automatically resend or cancel the ambiguous
upstream operation.
The maintenance sweep MUST render no more than the repository's database-safe
number of protected operation IDs in one expanding predicate. If the local
protection snapshot exceeds that bound, the sweep MUST use bounded candidate
pages and filter the full protection set without truncating it; every protected
operation MUST remain unchanged while unrelated eligible operations MAY still
transition. Each oversized-protection sweep MUST inspect no more than a finite
scan budget, return a keyset cursor for the last inspected eligible row, and
the next maintenance sweep MUST resume after that cursor. Once the eligible
range is exhausted, the cursor MUST wrap to the beginning so later rows cannot
be starved by a protected prefix.

#### Scenario: stale ownerless operation is abandoned

- **GIVEN** an operation is `unknown` or `acknowledged`
- **AND** its `updated_at` is older than the bounded inactivity cutoff
- **AND** no canonical or detached local bridge request is pending for it
- **AND** its durable session owner is absent or its lease is expired
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation becomes terminal `abandoned`
- **AND** its operation row and event history remain intact
- **AND** no upstream request is dispatched by the sweep

#### Scenario: oversized protected prefix advances across sweeps

- **GIVEN** the local protection snapshot exceeds the database-safe bind limit
- **AND** more stale eligible rows are protected than one sweep's finite scan
  budget
- **AND** a later stale eligible operation is not protected
- **WHEN** the bridge maintenance sweep runs
- **THEN** it inspects no more than the finite scan budget in that sweep
- **AND** it preserves a keyset cursor after the inspected protected prefix
- **AND** a later sweep resumes after that cursor and may abandon the later
  unprotected operation
- **AND** the protected operations remain unchanged

#### Scenario: oversized protection keeps the session lock fence

- **GIVEN** the local protection snapshot requires the bounded-page path
- **WHEN** PostgreSQL selects an abandonment candidate
- **THEN** the candidate operation and owning session rows remain locked until
  the abandonment transaction commits
- **AND** a concurrent renewal or takeover cannot commit behind the stale
  candidate snapshot

#### Scenario: live owner is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session has an unexpired owner lease
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains `unknown` or `acknowledged`

#### Scenario: a brief owner-renewal lapse is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session owner lease expired less than one durable lease
  period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the original owner may still renew or finalize it

#### Scenario: a recent ownerless release is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session was released to ownerless less than one durable
  lease period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the releasing replica may finish its pending settlement

#### Scenario: pending local work is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** a canonical or detached local bridge generation still has a pending
  request state for that operation
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains unchanged

#### Scenario: concurrent recovery wins

- **GIVEN** a stale `unknown` operation is selected for abandonment
- **WHEN** a recovery claim changes it to `submitted` before the CAS commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `submitted`

#### Scenario: concurrent status proof wins

- **GIVEN** a stale `acknowledged` operation is selected for abandonment
- **WHEN** a nonterminal status event is durably appended before the
  abandonment compare-and-set commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `acknowledged`
- **AND** the appended event remains available in the operation history

#### Scenario: late status proof cannot revive abandonment

- **GIVEN** an operation has become `abandoned`
- **WHEN** a late upstream event or status callback attempts to update it
- **THEN** the write is rejected or becomes a no-op
- **AND** the operation remains `abandoned`

#### Scenario: abandoned continuation requests full-history recovery

- **GIVEN** operation admission finds an existing operation in `abandoned`
- **WHEN** a client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code
  `previous_response_not_found` and parameter `previous_response_id`
- **AND** the error uses the canonical continuity contract that allows Codex
  to retry without `previous_response_id`

#### Scenario: abandoned hard turn-state requests full-history recovery

- **GIVEN** operation admission finds an existing hard turn-state operation in
  `abandoned`
- **AND** the request has no `previous_response_id`
- **WHEN** the client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code `previous_response_not_found`
  without a `previous_response_id` parameter
- **AND** the error instructs Codex to discard the hard continuity anchor and
  resend full history

#### Scenario: dashboard bridge budget sets the inactivity cutoff

- **GIVEN** the process environment sets a 120 second bridge request budget
- **AND** an operator has stored `10800` for
  `http_responses_session_bridge_request_budget_seconds` through
  `PUT /api/settings`
- **WHEN** the bridge maintenance sweep runs on any replica
- **THEN** the inactivity cutoff is 10800 seconds before the sweep time
- **AND** not the `max(1800, 120)` = 1800 seconds the environment alone implies

#### Scenario: snapshot failure keeps the environment budget

- **GIVEN** the `SettingsCache` snapshot cannot be read during a sweep
- **WHEN** the bridge maintenance sweep runs
- **THEN** the sweep still runs with the `max(1800 seconds, environment budget)`
  cutoff
- **AND** a warning is logged

### Requirement: Suppressed duplicate side-effect replays receive a dedicated terminal failure

When a replayed side-effecting tool call is suppressed and its upstream turn subsequently reports `response.completed`, the proxy MUST deliver a `response.failed` terminal with code `duplicate_tool_call_replay_suppressed`. It MUST use the downstream response id, treat the request as non-success, and MUST NOT count this intentionally fenced terminal as an HTTP bridge retry circuit failure.

#### Scenario: Direct SSE reports the dedicated terminal

- **GIVEN** a direct SSE request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives `response.failed` with code `duplicate_tool_call_replay_suppressed`
- **AND** the request log records `duplicate_tool_call_replay_suppressed`, not `stream_incomplete`

#### Scenario: HTTP bridge reports the dedicated terminal without retry-circuit failure

- **GIVEN** an HTTP bridge request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the client receives `response.failed` with code `duplicate_tool_call_replay_suppressed`
- **AND** the request log records `duplicate_tool_call_replay_suppressed`
- **AND** the HTTP bridge retry circuit is not incremented for that terminal

#### Scenario: WebSocket reports the dedicated terminal

- **GIVEN** a WebSocket request suppresses a replayed side-effecting tool call
- **WHEN** the upstream emits `response.completed` for that replay
- **THEN** the downstream terminal uses `duplicate_tool_call_replay_suppressed`
- **AND** the upstream account is not penalized for the intentionally suppressed replay

### Requirement: Source-routed Responses streaming stays alive and transport-aware

Streaming source-routed `POST /v1/responses` MUST reassemble upstream byte chunks into complete SSE event blocks, MUST apply public Responses stream normalization, and MUST inject SSE comment keepalives so idle upstream gaps do not trip front-door proxy timeouts. Native Codex clients that select a model source MUST keep `codex.*` events unfiltered and MUST receive `codex.keepalive` heartbeat framing, including an initial heartbeat, matching the subscription path.

#### Scenario: Keepalive precedes a slow first upstream event

- **WHEN** a source-routed `/v1/responses` stream is open and the upstream source has not yet produced its first event within the keepalive interval
- **THEN** the client receives an SSE comment keepalive frame before the first upstream event

#### Scenario: Native Codex source stream keeps vendor framing

- **WHEN** a native Codex client streams a source-routed response
- **THEN** `codex.*` events are forwarded rather than dropped
- **AND** heartbeats use `codex.keepalive` data framing with an initial heartbeat

### Requirement: Source SSE reassembly is byte-faithful and memory-bounded

Source-routed SSE reassembly MUST recognize blank-line event separators built from any two consecutive SSE line endings (CR, LF, or CRLF, including mixed pairs), MUST preserve the original terminator bytes of events the proxy does not rewrite, MUST preserve multi-byte UTF-8 sequences and CRLF pairs split across chunk boundaries, and MUST bound the reassembled event size by the configured maximum, closing the stream when the bound is exceeded. Data blocks the proxy cannot parse MUST be forwarded byte-identically, and after raw source data has been forwarded the proxy MUST NOT synthesize a terminal event for that stream. The shared separator scan MUST locate line-ending candidates with C-level search rather than per-byte iteration in Python.

#### Scenario: Mixed line endings dispatch complete events

- **WHEN** a source terminates an SSE event with CR-only, CRLF, or mixed blank-line separators, possibly split across chunk boundaries
- **THEN** the complete event is dispatched without waiting for additional upstream data
- **AND** events the proxy does not rewrite keep their original terminator bytes

#### Scenario: Oversized source event fails closed

- **WHEN** a reassembled source SSE event exceeds the configured maximum event size
- **THEN** the stream is closed with an event-too-large failure instead of buffering without bound

#### Scenario: Unparseable source data passes through

- **WHEN** a source emits a data block that is not parseable JSON
- **THEN** the block reaches the client byte-identically
- **AND** the proxy does not append a synthesized terminal event to that stream

### Requirement: Source stream settlement and cleanup are deterministic

Source-routed Responses streaming MUST keep API-key reservation settlement outermost so an early normalization return still settles the reservation without recording a client disconnect, and MUST close the stream iterator and its owning stream deterministically on completion, failure, or cancellation, including when iterator creation or iterator close raises.

#### Scenario: Error frame still settles the reservation

- **WHEN** normalization ends a source-routed stream early on an upstream error frame
- **THEN** the API-key reservation settles
- **AND** the request is not recorded as a client disconnect

#### Scenario: Stream resources close after cancellation

- **WHEN** a source-routed stream ends by completion, failure, or client cancellation
- **THEN** the stream iterator and its owning stream are both closed

### Requirement: Durable recovery transcripts use an explicit storage format

Each durable HTTP bridge operation MUST identify its transcript storage format.
Existing operations and new operations created before the chunk-writer cutover
MUST use `rows_v1`. A dual-reader release MUST replay both `rows_v1` event rows
and `chunks_v2` event chunks without changing the public SSE blocks or their
order. This expand release MUST continue writing `rows_v1` so rolling
deployments do not expose chunk-only data to an older replica.

#### Scenario: Historical row transcript remains replayable

- **GIVEN** an existing completed operation whose format is `rows_v1`
- **WHEN** recovery loads its transcript after the schema expansion
- **THEN** it receives the same ordered SSE blocks as before the migration

#### Scenario: Chunk transcript replays exact events

- **GIVEN** a completed `chunks_v2` operation with valid contiguous chunks
- **WHEN** recovery loads its transcript
- **THEN** every original SSE block is returned byte-for-byte in sequence order

#### Scenario: Expand release keeps the legacy writer

- **WHEN** the dual-reader release records or appends an ordinary operation
- **THEN** it persists the operation and events in `rows_v1` format

### Requirement: Chunk transcript decoding fails closed

Chunk decoding MUST enforce the operation's logical transcript byte bound and
the 65,536-event operation-wide chunk transcript limit. The chunk writer MUST
reject transcript growth beyond that same limit. Chunk decoding MUST reject
unknown codecs, decompression beyond the declared bound, hash or
byte-count mismatch, non-hexadecimal or incorrectly sized hashes, malformed
framing, invalid UTF-8, incorrect event count,
non-contiguous sequence ranges, and trailing bytes. Any rejected chunk MUST
make the transcript ineligible for recovery and MUST NOT produce a partial
replay or a new upstream dispatch.

#### Scenario: Corrupt chunk produces no partial transcript

- **GIVEN** a chunk payload whose hash, framing, or declared counts are invalid
- **WHEN** recovery loads the operation
- **THEN** the transcript is ineligible and no prefix is replayed

#### Scenario: Sequence gap produces no partial transcript

- **GIVEN** individually valid chunks whose sequence ranges contain a gap or
  overlap
- **WHEN** recovery loads the operation
- **THEN** the transcript is ineligible

#### Scenario: Logical event byte mismatch produces no transcript

- **GIVEN** decoded events do not total the operation's persisted `event_bytes`
- **WHEN** recovery loads either storage format
- **THEN** the transcript is ineligible

### Requirement: Transcript lifecycle handles both storage formats

Spool reset, failed-operation retry, rollback-before-dispatch, and retention
cleanup MUST inspect or delete both legacy event rows and event chunks under
the existing owner and operation fences. A format transition MUST NOT leave
stale transcript material that a later retry can mix into a fresh response.
Any path that clears all transcript material for a retry MUST reset the format
to `rows_v1` in the same transaction so the expand release remains a usable
rollback writer.

#### Scenario: Retry clears both transcript stores

- **GIVEN** an operation has legacy or chunk transcript material
- **WHEN** an owner-fenced retry resets the spool
- **THEN** both event stores are empty before new output is accepted

#### Scenario: Rollback refuses an operation with chunk evidence

- **GIVEN** an operation has a persisted chunk
- **WHEN** rollback-before-dispatch checks whether upstream work exists
- **THEN** it preserves the operation instead of deleting its durable fence

### Requirement: Chunk transcript writes require an explicit rollout selection

The durable HTTP bridge transcript writer MUST support `rows_v1` and
`chunks_v2` selection through one canonical setting and MUST default to
`rows_v1`. Enabling `chunks_v2` MUST NOT change public SSE output, replay
eligibility, logical byte caps, or owner fencing. A v2 writer MUST atomically
select `chunks_v2` on the first successful append only when the operation has
no legacy row or chunk material and no logical event bytes. A format conflict
MUST fail closed without persisting mixed material. Before compression, the
writer MUST validate owner fencing, logical byte capacity, and the reader's
cumulative transcript event-count limit. It MUST NOT mark a transcript complete
when the reader would reject its event count.

#### Scenario: Default release keeps writing rows

- **WHEN** no writer-format setting is provided
- **THEN** new durable transcript events are written as `rows_v1`

#### Scenario: First v2 append selects chunk format atomically

- **GIVEN** an owner-fenced operation with no persisted transcript material
- **AND** the writer format is `chunks_v2`
- **WHEN** its first event batch is persisted
- **THEN** the operation format and first chunk commit together

#### Scenario: Existing row transcript cannot switch formats

- **GIVEN** an operation already has a legacy event row or logical event bytes
- **WHEN** a v2 writer attempts to append
- **THEN** the append fails without writing a chunk or changing the format

#### Scenario: Rejected batch is not compressed

- **GIVEN** a batch exceeds the logical byte cap or fails owner fencing
- **WHEN** the v2 writer handles the batch
- **THEN** it rejects the batch before zlib compression

### Requirement: Chunk writer preserves batch and terminal settlement semantics

When `chunks_v2` is enabled, each successful nonterminal batch flush MUST
persist one ordered chunk containing the exact queued SSE blocks. The terminal
path MUST first drain pending chunks and then atomically persist the terminal
one-event chunk, authoritative operation state, optional response identifier,
and complete-spool marker. A size-cap failure or persistence error MUST leave
the transcript incomplete and use the existing terminal settlement fallback.

#### Scenario: Batch becomes one replay-equivalent chunk

- **WHEN** the in-memory batcher flushes multiple nonterminal events in v2 mode
- **THEN** one chunk is persisted
- **AND** replay returns every original event in the same order

#### Scenario: Terminal chunk and state commit together

- **WHEN** a terminal event fits inside the remaining logical byte budget
- **THEN** the terminal chunk, terminal operation state, response identifier,
  and complete marker commit atomically

#### Scenario: Oversized terminal event settles without complete transcript

- **WHEN** a terminal event exceeds the remaining logical byte budget
- **THEN** no terminal chunk is written
- **AND** the operation reaches its authoritative terminal state with an
  incomplete spool

#### Scenario: Event-count overflow settles without complete transcript

- **GIVEN** appending the terminal event would exceed the reader's cumulative
  transcript event-count limit
- **WHEN** the terminal path runs
- **THEN** no terminal chunk is written
- **AND** the operation reaches its authoritative terminal state with an
  incomplete spool

### Requirement: Connect-phase websocket transport failures surface without account penalty

The direct upstream websocket open MUST stamp host-scoped transport
provenance on the failures that prove the websocket transport itself did not
come up: a connect timeout, an invalid handshake, a 5xx upgrade rejection,
and a connect-phase network error other than host-wide network loss. It MUST
NOT stamp that provenance on failures that are scoped to something narrower
than the transport — credential-scoped handshake rejections (401, 403, 429
and any other sub-5xx status), TLS verification failures, host-wide network
loss, and every routed-proxy open, which proves nothing beyond the health of
one account's proxy endpoint.

When a Responses websocket upstream connect attempt fails carrying that
transport provenance, and the failure is not confirmed pre-dispatch route
evidence, the proxy MUST surface the classified failure to the client on that
attempt. It MUST NOT record account failure health for the selected account
and MUST NOT rotate to another account, because the failure is evidence about
the websocket transport, not the account, and penalizing the account starves
hard-affinity selection for the client's HTTP retry of the same turn.

Classification MUST key on that provenance rather than on the sanitized error
code, which cannot carry it in either direction: the Responses policy
preserves the upstream handshake body, so a direct 5xx upgrade rejection
surfaces as `upstream_error` or whatever code the edge returned, while OAuth
refresh transport errors, routed handshakes and TLS failures all share the
`upstream_unavailable` envelope. Failures without transport provenance MUST
retain the existing classify-penalize-failover behavior.

#### Scenario: websocket connect timeout surfaces without penalty

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream websocket open fails with a 5xx classified `upstream_unavailable` transport error carrying connect provenance
- **THEN** the failure surfaces to the client on the first attempt
- **AND** no transient account error is recorded for the selected account
- **AND** no other account is consumed by failover for that attempt

#### Scenario: OAuth refresh transport failure keeps account failover

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the account's token refresh fails with a transport error converted to a 502 `upstream_unavailable` without connect provenance
- **THEN** the failure is classified and recorded against the account
- **AND** the connect series proceeds with its existing failover decision toward healthy accounts

#### Scenario: account-scoped connect failure keeps the failover path

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream connect fails with an account-scoped error such as HTTP 401
- **THEN** the failure is classified and recorded against the account
- **AND** the connect series proceeds with its existing failover decision

#### Scenario: direct 5xx handshake rejection is transport evidence

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream rejects the upgrade with HTTP 503 and an unstructured body, which the client converts to code `upstream_error`
- **THEN** the failure carries websocket transport provenance
- **AND** it surfaces without an account penalty despite not matching a websocket-specific error code

#### Scenario: routed handshake failure keeps account failover

- **GIVEN** accounts reach upstream through different proxy routes
- **WHEN** one account's routed websocket open fails with an HTTP 5xx handshake
- **THEN** the failure carries no websocket transport provenance
- **AND** the connect series proceeds with its existing account/route failover decision instead of denying handshakes instance-wide

#### Scenario: TLS verification failure stays out of the transport fallback

- **GIVEN** a Responses websocket connect series selected an account
- **WHEN** the upstream websocket open fails TLS certificate verification
- **THEN** the failure carries no websocket transport provenance
- **AND** handshakes are not denied, because a raw HTTP retry reaches the same invalid TLS configuration

### Requirement: Websocket handshake denial steers Codex clients to HTTP during websocket outages

Codex clients activate their session-scoped HTTP transport fallback only when
the websocket handshake is rejected with HTTP 426 (`Upgrade Required`);
in-band error events — regardless of embedded status — retry on the websocket
transport. After a websocket connect failure carrying transport provenance,
or after a websocket open consumes the request budget once the direct
upstream connector itself has begun, the proxy MUST deny new Responses
websocket handshakes with HTTP 426 for a bounded window (60 seconds), and
MUST clear that denial state on the next successful direct upstream
websocket connect so the websocket transport resumes automatically. Clearing
is direct-scoped for the same reason arming is: a routed success proves only
that one account's proxy endpoint is healthy and MUST NOT readmit handshakes
during a direct-upstream outage. A deployment whose accounts are all routed
therefore never arms or clears the state, and a mixed one still expires it on
the bounded window. A request budget
that expires before the direct connector begins MUST NOT arm the denial
state: while the open is still waiting on local websocket-connect admission
or resolving the account's route that is local contention, and forcing every
client onto HTTP would amplify the overload it came from; and a stalled
routed open is route-scoped for the same reason a routed handshake failure
is, with no error for the routed exclusion to act on because the budget
cancels the open rather than failing it. While `upstream_stream_transport` is pinned to `"http"`, the
proxy MUST deny Responses websocket handshakes with HTTP 426 unconditionally.
The denial MUST NOT apply to the realtime websocket surfaces, whose upstream
is distinct, nor to a handshake carrying a required-capability header:
capability routing resolves only on this transport, so the downgrade would
send the session to an HTTP path that rejects the same capability, and the
client treats the switch as session-scoped and never returns.

Because a required capability may also be carried in a `response.create`
`client_metadata` field, which no handshake can observe, the HTTP responses
path MUST reject a capability signal found there with the same
transport-unsupported error it returns for the header. Capability resolution
fails closed to security-work-authorized accounts on the websocket path and
has no equivalent constraint on the HTTP path, so a metadata-only signal that
reached HTTP would otherwise enter ordinary account selection unconstrained.

#### Scenario: handshake denied while the transport-failure marker is armed

- **GIVEN** a connect-phase websocket transport failure occurred within the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426
- **AND** the client's session-scoped HTTP transport fallback can activate

#### Scenario: budget-exhausted websocket open arms the denial state

- **GIVEN** the request budget expires while the upstream websocket connector is stalled
- **WHEN** the budget-exhausted failure is emitted to the client
- **THEN** the transport-failure denial state is armed for subsequent handshakes

#### Scenario: budget exhausted in local admission does not arm the denial state

- **GIVEN** the request budget is shorter than the local websocket-connect admission wait
- **WHEN** the budget expires before the upstream connector begins
- **THEN** the failure surfaces as local admission evidence
- **AND** the transport-failure denial state is not armed

#### Scenario: budget exhausted in a routed connector does not arm the denial state

- **GIVEN** an account resolves to a proxy route and its routed websocket open stalls
- **WHEN** the request budget expires while that routed connector is running
- **THEN** the transport-failure denial state is not armed, because only that account's proxy endpoint was shown unhealthy

#### Scenario: handshake accepted after the denial window expires

- **GIVEN** the last connect-phase websocket transport failure is older than the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is accepted and the websocket transport is probed again

#### Scenario: a routed success does not clear the denial state

- **GIVEN** the transport-failure denial state is armed by direct-upstream evidence
- **WHEN** an account whose route resolves to a proxy endpoint opens its upstream websocket successfully
- **THEN** the denial state stays armed until a direct upstream connect succeeds or the bounded window expires

#### Scenario: pinned HTTP upstream transport denies websocket handshakes

- **GIVEN** `upstream_stream_transport` is pinned to `"http"`
- **WHEN** a client opens a Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426

#### Scenario: capability handshakes are never downgraded

- **GIVEN** the transport-failure denial state is armed
- **WHEN** a client opens a Responses websocket handshake carrying a required-capability header
- **THEN** the handshake is accepted rather than denied with 426, because capability routing exists only on this transport

#### Scenario: a metadata-only capability signal is rejected over HTTP

- **GIVEN** a Responses request arrives on the HTTP path carrying a required capability only in `client_metadata`
- **WHEN** the request is admitted
- **THEN** it is rejected with the transport-unsupported error rather than entering ordinary account selection without the authorization constraint

### Requirement: HTTP responses paths degrade to raw HTTP while the websocket transport is unavailable

The HTTP responses bridge holds upstream websocket sessions, so a pinned
`"http"` upstream transport MUST bypass the bridge and stream over raw HTTP.
While the websocket transport-failure denial state is armed, bridged and raw
HTTP Responses requests MUST pin the upstream transport to `"http"` and MUST
bypass the bridge, so a sticky follow-up that a client moved to the HTTP
route cannot resolve back onto the unavailable websocket upstream.

When bridge session creation fails carrying pre-submit session-creation
provenance **and** the same websocket transport provenance the failover
decision classifies on, before any line reached the client and with no
unsettled API-key usage reservation, the proxy MUST retry the turn over raw
HTTP with the upstream transport pinned to `"http"` for that request. That
decision MUST use the transport provenance rather than the sanitized error
code, which a direct 5xx bridge connect surfaces as `upstream_error` or
whatever the edge returned. A pre-submit failure without transport
provenance — an exhausted token-refresh loop or a routed handshake failure in
particular — is account or route evidence and MUST propagate unchanged.

Bridge session creation runs its own pre-dispatch failover and never reaches
the websocket failover decision, so when that fallback accepts a failure the
websocket transport classifier also recognizes, the proxy MUST arm the
transport-failure denial state; otherwise bridge-only traffic leaves it clear
and every later request re-attempts the unavailable websocket bridge before
falling back.

The raw-HTTP replay carries the incoming payload, not the bridge's prepared
payload, and the raw path never injects a response anchor. When bridge
session creation prepared a continuity anchor the incoming payload does not
carry, the proxy MUST NOT replay the turn over raw HTTP: doing so would send
the new turn alone and silently drop the prior conversation. The fallback
MUST NOT replay a failure without pre-submit provenance (the turn may already
have dispatched upstream), MUST NOT run after any line reached the client,
MUST NOT run while an API-key usage reservation is unsettled (reservation
settlement owns that path), and MUST NOT absorb non-transient failures.

When the bridge retry circuit's pre-dispatch submission gate suppresses a
request whose state is provably undispatched — no client or proxy-injected
continuation identity, no payload `conversation`, no file account pin, no
send attempt recorded for the request, and none of the unambiguous-boundary
markers (`response_id`, response events, downstream visibility, or a prior
replay) — the resulting
cooldown failure MUST carry the same pre-submit provenance and degrade to the
raw-HTTP fallback instead of a bounded 503. That undispatched proof MUST come
from state that is false before an actual send; a marker set optimistically
at request construction proves nothing and would make the fallback
unreachable. A cooldown suppression of an ambiguous continuation MUST keep
the bounded 503 with its retry hint.

That suppression MUST be identified by provenance the gate attaches, never by
its error code. An ordinary pre-submit budget exhaustion emits the same
`upstream_request_timeout` and collects the same pre-submit provenance while
session creation unwinds, but it is admission-queue or host-network evidence:
replaying it would double every request exactly when the instance is
saturated, and would feed a doomed raw-HTTP attempt into its own
process-network recovery wait. A budget exhaustion MUST therefore propagate
unchanged, and a cooldown suppression MUST NOT arm the websocket
transport-failure denial state, being bridge-scoped rather than transport
evidence.

#### Scenario: pinned HTTP upstream transport bypasses the bridge

- **GIVEN** the HTTP responses bridge is enabled and `upstream_stream_transport` is pinned to `"http"`
- **WHEN** the proxy receives a bridged Responses request
- **THEN** the bridge is bypassed and the request streams over raw HTTP

#### Scenario: armed transport-failure marker forces the HTTP upstream

- **GIVEN** the websocket transport-failure denial state is armed
- **WHEN** the proxy receives a Responses request on the HTTP route
- **THEN** the bridge is bypassed and the upstream transport is pinned to `"http"` for that request

#### Scenario: pre-submit bridge session-creation failure falls back to raw HTTP

- **GIVEN** the HTTP responses bridge is enabled with the default upstream transport
- **WHEN** bridge session creation fails with a 5xx classified `upstream_unavailable` error carrying pre-submit provenance before any line reached the client
- **THEN** the turn is retried over raw HTTP with the upstream transport pinned to `"http"`

#### Scenario: direct 5xx bridge connect falls back on its provenance

- **GIVEN** bridge session creation fails on a direct 5xx handshake, whose preserved upstream envelope carries the code `upstream_error`
- **WHEN** the failure reaches the bridge wrapper before any line reached the client
- **THEN** the turn is retried over raw HTTP, because the transport provenance and not the sanitized code decides

#### Scenario: routed bridge connect failures propagate unchanged

- **GIVEN** bridge session creation fails on a routed proxy handshake, which carries no transport provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay and the denial state stays clear

#### Scenario: bridge connect fallback arms the denial state

- **GIVEN** bridge session creation fails with a websocket connect failure carrying transport provenance
- **WHEN** the turn is retried over raw HTTP
- **THEN** the transport-failure denial state is armed
- **AND** subsequent HTTP requests bypass the bridge and the next websocket handshake is denied with HTTP 426

#### Scenario: bridge-prepared continuity anchors are not replayed over raw HTTP

- **GIVEN** an incoming Responses request carries no `previous_response_id` and the bridge injected the durable session anchor into its prepared payload
- **WHEN** bridge session creation then fails pre-submit with a transient `upstream_unavailable` error
- **THEN** the failure propagates without an HTTP replay, because the incoming payload alone would drop the prior conversation

#### Scenario: refresh-provenance failures propagate unchanged

- **GIVEN** bridge session creation exhausts token refresh for the selected account and surfaces a pre-submit 502 `upstream_unavailable` without connect provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay

#### Scenario: replay-safe cooldown suppression falls back to raw HTTP

- **GIVEN** the bridge retry circuit is cooling down and a fresh turn with no continuation identity and no dispatch markers is suppressed at the pre-dispatch submission gate
- **WHEN** the cooldown failure reaches the bridge wrapper before any line reached the client
- **THEN** the turn is retried over raw HTTP with the upstream transport pinned to `"http"`

#### Scenario: pre-submit budget exhaustion is not a cooldown suppression

- **GIVEN** bridge session creation exhausts the request budget and surfaces `upstream_request_timeout` with pre-submit provenance but no cooldown marker
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay, and the transport-failure denial state stays clear

#### Scenario: ambiguous cooldown suppression keeps the bounded 503

- **GIVEN** the bridge retry circuit is cooling down and a continuation whose delivery is ambiguous is suppressed
- **WHEN** the cooldown failure reaches the bridge wrapper
- **THEN** the bounded 503 with its retry hint propagates without an HTTP replay

#### Scenario: a conversation-scoped suppression keeps the bounded 503

- **GIVEN** a suppressed request carries a non-empty payload `conversation` but no anchor, turn-state key, file pin, or dispatch marker
- **WHEN** the cooldown failure reaches the bridge wrapper
- **THEN** the bounded 503 propagates, because `conversation` has no owner index and a raw-HTTP replay could not prove the bridge session's owner

#### Scenario: post-submit transient failures are not replayed

- **GIVEN** a bridged Responses request fails with a transient `upstream_unavailable` error without pre-submit session-creation provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay

#### Scenario: partially streamed bridge turns are not replayed

- **GIVEN** a bridged Responses request already streamed at least one line to the client
- **WHEN** the bridge fails with a transient `upstream_unavailable` error
- **THEN** the failure propagates without an HTTP replay

#### Scenario: unsettled API-key reservations propagate bridge failures

- **GIVEN** a bridged Responses request holds an unsettled API-key usage reservation
- **WHEN** bridge session creation fails with a transient `upstream_unavailable` error
- **THEN** the failure propagates and reservation settlement proceeds through its existing owner

### Requirement: Namespaced agent-control tool-call outputs survive historical slimming

Every live upstream path MUST preserve a historical `function_call_output` or
`custom_tool_call_output`
unchanged before forwarding an oversized Responses `response.create` when
its non-empty `call_id` matches a historical `function_call` or
`custom_tool_call` whose namespace is exactly `collaboration` or
`multi_agent_v1`. The service MUST determine this from the historical prefix of
the original request input before outbound payload normalization removes replay
namespaces, and use namespace and call ID rather than the tool name alone. A
recent namespaced call MUST NOT protect a historical output that reuses its
call ID. When historical calls of the same protocol reuse one call ID, the
service MUST pair each output with its nearest preceding unmatched call of
the same protocol and call ID, preserving the output only when that paired
call is namespaced; a historical output with no such preceding call MUST
remain eligible for the normal omission policy. Historical outputs
without such a matching call, including an unnamespaced user tool named
`wait_agent` or `send_input`, MUST remain eligible for the normal omission
policy.

#### Scenario: Agent wait output is retained while unrelated outputs are slimmed
- **WHEN** a historical `multi_agent_v1` `function_call` for `wait_agent` has
  a large matching `function_call_output` and the request also has a large
  shell output before the latest user turn
- **THEN** both the bridge/service and direct WebSocket paths preserve the
  agent wait output unchanged
- **AND** both paths replace the shell output with the historical tool-output
  omission notice

#### Scenario: A bare-name user tool is not exempt
- **WHEN** a historical unnamespaced `function_call` is named `wait_agent` or
  `send_input` and has a large matching `function_call_output`
- **THEN** each live slimming path leaves that output eligible for the normal
  historical tool-output omission policy

#### Scenario: Namespaced custom tool output is retained after wire normalization
- **WHEN** a historical `collaboration` `custom_tool_call` has a large
  matching `custom_tool_call_output`, and another custom call uses a namespace
  outside the agent-control allowlist
- **THEN** HTTP bridge and WebSocket bridge forwarding preserve the
  agent-control custom output even though both outbound payloads omit replay
  namespaces
- **AND** the unrelated custom output remains eligible for the historical
  tool-output omission policy

#### Scenario: Recent calls do not protect reused historical IDs
- **WHEN** a historical unrelated output reuses the call ID of an
  agent-control call that appears only after the latest user item
- **THEN** every live slimming path leaves the historical output eligible for
  the normal omission policy

#### Scenario: Same-protocol reused call IDs pair by occurrence
- **WHEN** a historical namespaced `function_call` and an ordinary
  `function_call` reuse one call ID, each followed by a large matching
  `function_call_output`
- **THEN** both the bridge/service and direct WebSocket paths preserve the
  namespaced pair's output unchanged
- **AND** both paths replace the ordinary pair's output with the historical
  tool-output omission notice

#### Scenario: Orphan outputs do not consume namespaced pairings
- **WHEN** a historical `function_call_output` precedes every matching call
  because its own call was trimmed from replay, and a later namespaced
  `function_call` reusing the same call ID is followed by its own large
  matching output
- **THEN** every live slimming path replaces the orphan output with the
  historical tool-output omission notice
- **AND** preserves the namespaced pair's output unchanged

### Requirement: Stale bridge retirement rechecks liveness after suspension

Before closing and unregistering a stale HTTP bridge session, the service MUST re-sample pending request liveness after retry-circuit bookkeeping awaits. A response event, response id, or equivalent response-created signal newly observed after the caller's pre-suspension snapshot MUST prevent stale retirement. A session that remains eventless MUST still be retired. Retirement entered from the reader-failure funnel, or for a session that was already closed when its last admission waiter cancelled, MUST NOT be revived by post-suspension signals: its pending turns were already terminally failed and its reader is condemned, and the completed-response anchor can be moved by durable-anchor rehydration without any upstream evidence.

#### Scenario: First response event arrives during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and then suspends for retry-circuit bookkeeping
- **AND** a pending turn receives its first response event before the close decision
- **THEN** the final decision samples pending state under `session.pending_lock`
- **AND** makes the registry decision under `_http_bridge_lock`
- **AND** the session remains registered, open, and reusable

#### Scenario: Detached generation is not revived by post-suspension liveness

- **WHEN** stale retirement observes post-suspension liveness for a session
- **AND** the acquisition loop has already detached that session from the registry during the suspension
- **THEN** the final decision does not clear the detached generation's retirement flags
- **AND** the detached generation still receives its bounded close so its socket, leases, and capacity slot are released

#### Scenario: Fence raised during suspension survives post-suspension liveness

- **WHEN** stale retirement suspends for retry-circuit bookkeeping
- **AND** a fence owner sets reconnect-requested or retire-after-drain on the still-registered session during the suspension while also advancing the event generation
- **THEN** the final decision does not clear the fence
- **AND** the session is unregistered and receives its bounded close instead of being revived

#### Scenario: Prelude-only upstream event during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and then suspends for retry-circuit bookkeeping
- **AND** an upstream event that advances only the session event generation arrives during the suspension
- **THEN** the final decision observes the generation change against the entry-time baseline
- **AND** the session remains registered, open, and reusable

#### Scenario: Reader-failure retirement is not revived by durable-anchor rehydration

- **WHEN** the reader-failure funnel retires a session whose pending turns were already terminally failed
- **AND** a concurrent durable-anchor rehydration moves the session's completed-response anchor during the retirement suspension
- **THEN** the final decision does not treat the anchor movement as liveness
- **AND** the session is unregistered and receives its bounded close

#### Scenario: Session remains eventless during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and suspends for retry-circuit bookkeeping
- **AND** no pending turn receives a response or response-created signal
- **THEN** the final decision retires and unregisters the session

### Requirement: Explicit upstream previous-response denials retire proxy-injected anchors

When upstream answers an HTTP bridge request with a `previous_response_not_found` terminal frame, and the `previous_response_id` on that request was injected by the proxy onto a full-resend-shaped payload, the proxy MUST retire that anchor on the first denial rather than waiting for the eventless-failure poison threshold. Retirement MUST clear the durable anchor only when the denied id is still the durable latest response and the session owner fence still matches; the write MUST clear the four anchor-bound fields and delete only the matching response-id alias, preserving turn-state and sibling response aliases. The proxy MUST clear the in-memory session carrier even if durable cleanup or alias unregistering fails.

Before awaiting durable cleanup, the proxy MUST publish the denied id to the live session. Publication MUST be serialized with the submitter's final tombstone check and upstream send so either an already-started send finishes first or publication wins and fences that send. Immediately before dispatch, any already-prepared request carrying that id as a proxy-injected anchor MUST fail closed without sending another upstream frame. This revalidation MUST close the retirement/dispatch race; it MUST NOT reject a client-supplied anchor merely because the same id is tombstoned for proxy injection.

The proxy MUST also retain the denied-id generation in a bounded process-local ledger independent of the canonical live-session registry. A request that captured the durable anchor before a live session existed MUST fail closed when that generation advances during owner lookup or successor session creation. Active requests MUST pin their ledger entries until finalization so pruning cannot remove a fence that is still needed.

When a request captures a proxy-injected anchor after this process has already recorded a denial for that id, the request MUST retain that denial observation and fail closed before dispatch even when the captured generation equals the current denial generation. Owner-forward recovery that injects a durable anchor MUST perform the same capture and denial observation; it MUST NOT rely on provenance copied from an initially unanchored request.

The proxy MUST NOT retire the anchor when:

- the anchor was supplied by the client, because removing it changes the meaning of the client's own request;
- the anchor was injected onto a payload that is not full-resend shaped, because a delta-only request has no other way to convey prior context once its anchor is gone;
- the session's current anchor is no longer the denied id, because a concurrent request may have completed and advanced it.

When a durable clear raises, the proxy MUST NOT report the anchor as retired, MUST still clear the in-memory anchor, and MUST retain the bounded cleanup retry so a transient failure is not lost. When the durable clear returns no matching row, the proxy MUST treat that no-match as a terminal fenced outcome for this cleanup attempt and MUST NOT spend the retry budget on it; it MUST preserve the local alias and denial fence because the durable owner or latest anchor may have advanced. A durable record that still carries the denied id can then be retired by the matching owner rather than by a stale epoch.

Retirement is bookkeeping and MUST NOT change how the denial is delivered downstream. A failure while retiring MUST NOT propagate into terminal-event handling.

A denial that settles several requests sharing one anchor MUST retire that anchor once, on the same terms.

The downstream error contract is unchanged: the denial is still reported to the client as `stream_incomplete`, so the client retains its own anchor and is not driven into a full-history resend.

#### Scenario: A denied proxy-injected anchor is retired immediately

- **GIVEN** an HTTP bridge session whose stored anchor was injected by the proxy
- **WHEN** upstream answers the anchored request with `previous_response_not_found`
- **THEN** the proxy clears the durable continuity record under the session's owner epoch
- **AND** clears the in-memory session anchor and its stored input count and prefix fingerprint
- **AND** the next turn on that session dispatches without a `previous_response_id`

#### Scenario: The following turn is not trimmed against a denied anchor

- **GIVEN** a proxy-injected anchor was denied by upstream on the previous turn
- **WHEN** the client sends a full resend of the conversation on the next turn
- **THEN** the request MUST NOT be trimmed against the denied anchor's stored prefix
- **AND** upstream receives the resent conversation rather than a suffix of it

#### Scenario: A concurrent completion protects the current anchor

- **GIVEN** a proxy-injected anchor is denied by upstream
- **AND** another request on the same session completed first and advanced the session anchor to a different response id
- **WHEN** the denial is handled
- **THEN** the proxy MUST NOT clear the session anchor
- **AND** it MUST still tombstone the denied id so an already-prepared proxy-injected request cannot dispatch it

#### Scenario: Client-supplied anchors are left alone

- **GIVEN** an HTTP bridge request carries a `previous_response_id` the client supplied
- **WHEN** upstream answers it with `previous_response_not_found`
- **THEN** the proxy MUST NOT retire the anchor on the client's behalf

#### Scenario: A delta-only payload keeps its injected anchor

- **GIVEN** the proxy injected an anchor onto a payload that is not full-resend shaped
- **WHEN** upstream answers that request with `previous_response_not_found`
- **THEN** the proxy MUST NOT clear the anchor
- **AND** the request keeps the only reference it has to its prior context

#### Scenario: A fan-out denial retires the shared anchor once

- **GIVEN** several pending requests on one session share a proxy-injected anchor
- **WHEN** upstream answers with a single `previous_response_not_found` that settles all of them together
- **THEN** the proxy retires that anchor before the grouped settlement completes

#### Scenario: A prepared denied anchor is rejected before dispatch

- **GIVEN** a request was prepared with a proxy-injected anchor
- **AND** another request receives `previous_response_not_found` for that anchor before the prepared request reaches the upstream send
- **WHEN** the prepared request reaches its final dispatch check
- **THEN** the proxy fails it closed as `stream_incomplete`
- **AND** the proxy MUST NOT send that denied anchor upstream again

#### Scenario: Denial publication wins against a prepared dispatch

- **GIVEN** a request is prepared with a proxy-injected anchor while another request receives `previous_response_not_found` for that anchor
- **WHEN** denial publication acquires session lifecycle ownership before the prepared request's final send section
- **THEN** the denied id is tombstoned before the prepared request revalidates
- **AND** the prepared request fails closed without sending an upstream frame

#### Scenario: A detached predecessor fences an absent-session capture

- **GIVEN** a request captures a proxy-injected durable anchor before a canonical live session exists
- **AND** a detached predecessor receives `previous_response_not_found` for that anchor while the request is resolving ownership
- **WHEN** successor session creation completes and the request reaches final dispatch
- **THEN** the process-local denial generation MUST fail the request closed as `stream_incomplete`
- **AND** the successor MUST NOT send the denied anchor upstream

#### Scenario: A stale durable recapture remains fenced after cleanup failure

- **GIVEN** a detached predecessor records a denial for a proxy-injected anchor
- **AND** durable anchor cleanup fails, leaving the durable row unchanged
- **WHEN** a later request captures that same durable anchor after the denial was recorded
- **THEN** the request MUST retain the existing denial observation
- **AND** it MUST fail closed as `stream_incomplete` before dispatch

#### Scenario: Owner-forward recovery observes an existing denial

- **GIVEN** owner-forward recovery injects a durable proxy anchor into a successor request
- **AND** this process has already recorded a denial for that anchor
- **WHEN** the recovery retry request state is prepared
- **THEN** the retry MUST retain the denial observation
- **AND** it MUST fail closed before sending the denied anchor upstream

#### Scenario: Sibling response aliases survive retirement

- **GIVEN** a session has a denied response alias and another valid response alias
- **WHEN** the denied anchor is retired
- **THEN** only the denied response alias is removed
- **AND** the valid response alias and turn-state aliases remain routable

#### Scenario: An unconfirmed durable clear still drops the in-memory anchor

- **GIVEN** a denied proxy-injected anchor whose durable clear is fenced or fails
- **WHEN** the denial is handled
- **THEN** the proxy MUST clear the in-memory session anchor
- **AND** MUST NOT report the anchor as retired

If alias unregistering raises after the durable clear, the same in-memory cleanup MUST still occur.

The process-local denial fence MUST retain its positive generation while any
request still pins the denied id, even after durable cleanup succeeds. Such a
prepared request MUST remain fenced until its final pin is released; the fence
may then be removed. Fence state MUST be bounded to one current denial slot per
active durable or local owner plus active request pins, and a session close or
durable-owner epoch change MUST retire the old owner's unpinned slot without
clearing a successor epoch's slot. A close MAY retain an otherwise unpinned
durable denial slot while that session still records an unresolved durable
cleanup, so a stale row cannot be recaptured; the slot MUST be retired when
cleanup succeeds or the durable row is confirmed absent.

When several late predecessor denials arrive for one durable owner, only the
newest unpinned predecessor slot MUST be retained while its durable cleanup is
unresolved. Older predecessor slots MUST remain fenced while request pins are
active, then MUST be retired when those pins release or when a newer owner
confirms durable cleanup. This keeps predecessor churn bounded without
allowing an already-prepared request to redispatch a denied anchor.

#### Scenario: Late predecessor churn remains bounded

- **GIVEN** one durable owner advances through more than the process-local
  denial-ledger bound
- **AND** each successor denial is followed by a late predecessor denial
- **WHEN** no predecessor request retains an active ledger pin
- **THEN** the ledger retains the current denial and at most the newest
  unresolved predecessor denial for that owner
- **AND** a current-owner durable clear retires the unresolved predecessor
  slot

#### Scenario: Pinned predecessor survives bounded churn until release

- **GIVEN** a late predecessor denial still has an active prepared-request pin
- **WHEN** a newer predecessor denial is recorded for the same durable owner
- **THEN** the pinned predecessor remains fenced until its request finalizes
- **AND** releasing the final pin removes that superseded predecessor slot

#### Scenario: A retirement failure cannot change the denial delivered downstream

- **GIVEN** the bookkeeping performed while retiring a denied anchor raises
- **WHEN** the denial is handled
- **THEN** the error MUST NOT propagate into terminal-event handling

#### Scenario: Durable cleanup preserves a prepared request's denial fence

- **GIVEN** a request has pinned a proxy-injected anchor while another request receives `previous_response_not_found`
- **WHEN** the durable clear succeeds
- **THEN** the denied fence keeps its positive generation until the prepared request releases its pin
- **AND** the prepared request remains fenced during that interval
- **AND** the fence is removed after the final pin is released

#### Scenario: A successor epoch survives predecessor fence cleanup

- **GIVEN** a successor owns the same durable session id at a newer epoch
- **WHEN** the predecessor closes or its durable clear completes
- **THEN** cleanup removes only the predecessor's unpinned fence state
- **AND** the successor's current denial slot remains active

### Requirement: Anchored recovery retries retain the provenance of the anchor they replay

When the HTTP bridge dispatches an anchored recovery retry that replays a `previous_response_id` the proxy injected, the retry request state MUST record that the anchor is proxy-injected. A recovery path that dispatches without an anchor MUST leave that provenance false, because there is no anchor for it to describe.
When a hard turn-state operation-ledger lookup injects an anchor after the request state was initially prepared without a `previous_response_id`, the request state MUST preserve the original payload's full-resend classification and use it when deciding whether a later denial may retire that injected anchor.

#### Scenario: An anchored recovery retry is attributable to the proxy

- **GIVEN** a request whose `previous_response_id` was injected by the proxy fails and enters anchored recovery
- **WHEN** the recovery retry replays the same anchor
- **THEN** the retry request state records the anchor as proxy-injected
- **AND** continuity diagnostics for the retry report `previous_response_source=proxy_injected` rather than `client_supplied`

#### Scenario: Anchor-free recovery retries claim no provenance

- **GIVEN** a recovery path dispatches without a `previous_response_id`
- **WHEN** the retry request state is prepared
- **THEN** it MUST NOT record a proxy-injected anchor

### Requirement: Denied proxy-injected bridge anchors have fenced lifecycle cleanup

When upstream rejects a proxy-injected `previous_response_id` with
`previous_response_not_found`, the HTTP bridge MUST publish a positive
process-local denial generation before any cleanup await. A prepared request
that captured that anchor before the denial MUST fail closed without another
upstream dispatch, including when the request was admitted before its session
was closed. The generation MUST remain available while any request pins it.

An owner transition MUST NOT allow a stale predecessor to replace a newer
durable owner's denial fence. An ownerless or process-local predecessor MUST
NOT overwrite a durable owner entry for the same response id. When local alias
cleanup fails without a durable owner, the bridge MUST retain a tracked retry
that can remove the alias and fence rather than abandoning an unbounded local
tombstone.

An unpinned stale-predecessor denial fence MUST remain available until a
current owner confirms that the matching durable anchor has been cleared;
releasing the stale request's final pin alone MUST NOT drop that fence.

When a sibling has already advanced the current response, its denial fence is
historical rather than unresolved cleanup. Closing the session MUST retire an
unpinned historical fence once durable ownership is released (including an
ownerless release result), while preserving unresolved current-anchor cleanup
and any pinned generations for their final request release.

#### Scenario: An admitted denial still cleans up after session close

- **GIVEN** a request was admitted with a proxy-injected anchor
- **AND** the bridge session is marked closed before upstream returns
  `previous_response_not_found`
- **WHEN** the terminal denial is handled
- **THEN** the denial generation is published and the request receives the
  existing downstream error contract
- **AND** the denied anchor is not re-injected by a later request

#### Scenario: A stale local predecessor cannot replace a durable fence

- **GIVEN** a durable owner holds a positive denial fence for response id `A`
- **WHEN** a late process-local predecessor records denial for the same `A`
- **THEN** the durable owner and generation remain authoritative
- **AND** the predecessor does not remove the durable owner mapping

#### Scenario: Local alias cleanup failure remains tracked

- **GIVEN** a denied anchor belongs only to a process-local session
- **WHEN** local alias unregistering fails transiently
- **THEN** the bridge tracks a bounded cleanup retry
- **AND** a successful retry removes the local alias and denial fence

#### Scenario: Sibling-advanced fence retires on ownerless close

- **GIVEN** a denial arrives after a sibling has advanced the session's current
  response
- **AND** no request still pins the denied generation
- **WHEN** closing the session releases durable ownership with no owner
- **THEN** the historical denial fence and owner mapping are removed
- **AND** unresolved current-anchor cleanup fences remain retained

### Requirement: Pre-response-start bridge silence has its own classification

The HTTP responses session bridge MUST NOT report a failure that occurs before
the request's first response event as `stream_idle_timeout`. Every terminal the
bridge produces while `response.created` has not been observed and no response
event has been counted MUST use the distinct code `bridge_eventless_timeout`.

That classification MUST appear in the bridge's own log line, in the durable
request-log failure metadata (`failure_detail` = `bridge_eventless_timeout`,
`failure_phase` = `bridge`), in the HTTP bridge retry-circuit `last_detail`, and
in the client-visible error payload. The retry-circuit detail MUST NOT be
aliased onto `stream_idle_timeout`.

The client-visible error MUST remain retryable: HTTP status `503` and a message
that states no response was created upstream and the request is safe to repeat.
The message MUST NOT attribute the failure to the upstream.

`stream_idle_timeout` remains the classification for a stream that produced at
least one response event and then went silent for `stream_idle_timeout_seconds`.

#### Scenario: Pre-response silence is reported as an eventless bridge timeout

- **GIVEN** an HTTP bridge request whose downstream event queue has produced no
  response events
- **WHEN** the pre-response silence budget expires
- **THEN** the emitted `response.failed` event carries code
  `bridge_eventless_timeout`
- **AND** the request log records `failure_detail=bridge_eventless_timeout` and
  `failure_phase=bridge`
- **AND** the hard-affinity retry circuit records `last_detail` of
  `bridge_eventless_timeout`
- **AND** the error message does not mention the upstream
- **AND** the equivalent HTTP error status is `503`

#### Scenario: Post-response idle keeps the stream idle classification

- **GIVEN** an HTTP bridge request that already received a response event
- **WHEN** the stream stays silent past `stream_idle_timeout_seconds`
- **THEN** the emitted `response.failed` event carries code
  `stream_idle_timeout`
- **AND** no `bridge_eventless_timeout` request-log detail is recorded

### Requirement: The pre-response silence budget is settings-derived

The pre-response silence budget MUST be a named quantity derived from
configuration, not the implicit product of `_STREAM_KEEPALIVE_MAX_COUNT` and
`sse_keepalive_interval_seconds`.

The budget MUST be the minimum of the fixed owner-side stuck gate
(`HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS`, 300 seconds; not a runtime
setting), `stream_idle_timeout_seconds`, and
`http_responses_session_bridge_request_budget_seconds`, so that the downstream
pre-response watchdog can never outlive the owner-side stuck gate, the
configured idle budget, or the request budget. The two settings-derived terms
MUST be read as their effective dashboard-managed values from the snapshot
bound to the request, a non-NULL `dashboard_settings` column of the same name
overriding the environment value. The number of pre-response
keepalive intervals waited MUST cover that budget. It MUST NOT drop below
`_STREAM_KEEPALIVE_MAX_COUNT` when the budget spans at least that many
keepalive intervals; when the configured budget is shorter, the count MUST
follow the budget instead, so the watchdog never outlives it.

#### Scenario: Default settings align the budget with the stuck gate

- **GIVEN** shipped defaults `sse_keepalive_interval_seconds=10` and
  `stream_idle_timeout_seconds=7200`, and the fixed `300` second stuck gate
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `300` seconds
- **AND** the pre-response keepalive count covers `300` seconds rather than the
  previous implicit `60` seconds

#### Scenario: A shorter idle timeout clamps the budget

- **GIVEN** `stream_idle_timeout_seconds=45` and a `300` second stuck gate
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `45` seconds

#### Scenario: Dashboard bridge budget is the term compared

- **GIVEN** the process environment sets a `7200` second bridge request budget
  and an operator has stored `650` through `PUT /api/settings`
- **AND** `stream_idle_timeout_seconds=7200` and the fixed `300` second stuck
  gate
- **WHEN** the pre-response silence budget is computed for a request
- **THEN** the bridge budget term is `650` seconds (the dashboard value)
- **AND** the budget is `300` seconds

### Requirement: Unmatched live upstream frames are recorded as liveness

The bridge MUST record an upstream text frame that matches no pending request,
while pending requests exist, as unmatched upstream liveness: an
`unmatched_upstream_liveness` bridge-event marker and a per-session counter. A subsequent `bridge_eventless_timeout` MUST report that counter so a
local matching wedge is distinguishable from a genuinely silent upstream.

Frames the bridge injects into its own downstream streams, in particular
`codex.keepalive`, MUST NOT be counted as upstream liveness.

#### Scenario: An unmatched upstream event is marked as liveness

- **GIVEN** an HTTP bridge session with one pending request
- **WHEN** an upstream response event arrives that matches no pending request
- **THEN** an `unmatched_upstream_liveness` bridge event is logged
- **AND** the session's unmatched upstream liveness counter increases

#### Scenario: A local keepalive frame is not upstream liveness

- **GIVEN** an HTTP bridge session with one pending request
- **WHEN** a `codex.keepalive` frame is processed
- **THEN** no `unmatched_upstream_liveness` bridge event is logged
- **AND** the session's unmatched upstream liveness counter is unchanged

### Requirement: Local bridge resets are reported as local

The bridge MUST identify local recovery resets as local bridge resets. When it
tears down and rebuilds its own upstream session (durable fresh replay,
context-overflow fresh turn, context-overflow rollover, or local
previous-response rebind), the terminal error message it settles pending
requests with MUST NOT claim the upstream websocket closed.

Reporting a local reset MUST remain account-health neutral for anchored
`stream_incomplete` settlements, exactly as the upstream-close wording was.

#### Scenario: Local recovery does not report an upstream close

- **GIVEN** the bridge performs any local reset-and-retry recovery
- **WHEN** it settles the pending requests of the session it is discarding
- **THEN** the settled error message says the bridge reset the session locally
- **AND** the message does not contain `Upstream websocket closed`
- **AND** the settlement does not mark the account unhealthy

### Requirement: Verified same-owner stale-anchor replacement remains owner-bound

When an HTTP-bridge continuation has a verified, prefix-safe full resend that
is not account-neutral because its retained tool or file context is owner-bound,
and the continuity owner explicitly rejects `previous_response_id` before
producing any response output, the proxy MUST remove only that rejected anchor
and attempt the bounded unanchored replacement on the proven owner account.
If a preferred owner account is available in this recovery path, admission MUST
set `fallback_on_preferred_account_unavailable` to false. If that owner cannot
accept the replacement, the proxy MUST fail closed rather than selecting an
alternate account. The replacement MUST retain the existing operation fence,
settlement, one-shot replay, and retry-circuit rules.

#### Scenario: Owner-bound replacement stays on the rejecting owner

- **GIVEN** a verified full resend contains retained owner-bound tool history
- **AND** upstream explicitly rejects its `previous_response_id` before output
- **WHEN** the proxy prepares the one-shot unanchored replacement
- **THEN** the replacement omits the rejected `previous_response_id`
- **AND** it is admitted with the proven owner as `preferred_account_id`
- **AND** preferred-owner fallback is disabled
- **AND** the replacement does not migrate accounts

#### Scenario: Unavailable owner fails closed

- **GIVEN** the owner-bound replacement has a proven preferred account
- **AND** that account is unavailable or saturated during replacement admission
- **WHEN** the proxy selects a bridge session
- **THEN** the request fails with the existing retryable owner-unavailable result
- **AND** no alternate account receives the retained owner-bound context

#### Scenario: Account-neutral replay remains independent

- **GIVEN** an explicit stale-anchor rejection passes the existing account-neutral
  full-resend proof
- **WHEN** the proxy performs account-neutral recovery
- **THEN** its existing owner-exclusion and account-neutral selection behavior
  remains unchanged

#### Scenario: Other recovery paths remain unchanged

- **WHEN** a request is delta-only, prefix-unverified, transport-only, or has no
  explicit stale-anchor rejection
- **THEN** the proxy does not use this owner-bound replacement rule
- **AND** its existing fail-closed or anchored recovery behavior remains in
  force

### Requirement: A disabled model source refuses its models instead of falling through

The system SHALL NOT dispatch to a subscription account a request whose model
is served by an OpenAI-compatible model source that an operator has switched
off. It SHALL refuse such a request with HTTP status `503` and error code
`model_source_disabled`.

"Switched off" covers both a disabled source row and a disabled model row on an
enabled source. The refusal SHALL apply on `/v1/chat/completions`,
`/v1/responses`, and `/backend-api/codex/responses`.

The refusal SHALL be decided by the ordinary source-selection rules with the
enabled-state filter inverted and nothing else changed: same candidate list
(raw client alias and normalized model), same API key model allowlist, same
source assignment scope, same subscription-registry precedence, same route
shape, same streaming requirement. A request that the ordinary lookup would
have missed for any reason other than enabled state MUST keep its existing
behaviour, including a model no source exposes, a source the API key is not
assigned to, a chat-only source asked for a Responses route, and a
subscription-registry slug that an unscoped API key never source-routes.

Requests excluded from source routing — a terminal `compaction_trigger`, and
Responses requests pinned to the subscription account that received an uploaded
file — MUST NOT be refused, and MUST proceed to subscription routing as before.

The WebSocket transport cannot forward to a model source, so its
source-ownership guards SHALL treat a model owned only by a switched-off source
as source-owned: the turn fails with the existing service-level
`model_source_requires_http_transport` refusal instead of dispatching to a
subscription account, and the client's HTTP fallback then meets the
`model_source_disabled` refusal above. The guards' existing exclusions — a
structurally excluded request and a recorded previous-response subscription
owner — keep bypassing the guard unchanged.

The refusal MUST happen before any usage reservation is taken, so a refused
request strands no reservation, and MUST NOT create a request log entry for a
dispatch that never happened.

#### Scenario: Chat request for a disabled source's model is refused

- **GIVEN** an OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`
- **AND** no subscription account is selected for the request
- **AND** no usage reservation is left held

#### Scenario: Responses request for a disabled source's model is refused

- **GIVEN** a Responses-capable OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client calls `POST /v1/responses` or `POST /backend-api/codex/responses` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`
- **AND** no subscription account is selected for the request

#### Scenario: A disabled model on an enabled source is refused

- **GIVEN** an enabled OpenAI-compatible model source whose model row for `m` is disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** the response is `503` with error code `model_source_disabled`

#### Scenario: A model no source exposes is unaffected

- **GIVEN** no model source exposes model `m`, enabled or disabled
- **WHEN** a client calls `POST /v1/chat/completions` with model `m`
- **THEN** subscription routing proceeds exactly as it did before this requirement

#### Scenario: A WebSocket turn for a disabled source's model bounces to HTTP

- **GIVEN** a Responses-capable OpenAI-compatible model source exposes model `m` and is disabled
- **WHEN** a client requests model `m` over the WebSocket transport, at connect time or on a later turn over an already-open socket
- **THEN** the turn is refused with the service-level `model_source_requires_http_transport` failure that makes Codex clients retry over the HTTP transport
- **AND** the turn is not forwarded to a subscription account upstream

#### Scenario: A subscription slug shadowed by a disabled source is unaffected

- **GIVEN** a disabled OpenAI-compatible model source lists a slug the subscription model registry already serves
- **AND** an API key without source assignment scoping
- **WHEN** the key requests that slug
- **THEN** the request is not refused with `model_source_disabled`
- **AND** subscription routing proceeds unchanged

### Requirement: Stale-anchor error parameters preserve presence and fail closed

When the proxy parses an upstream Responses or Chat Completions error, it MUST
distinguish an absent `param` from a present malformed value. A present
non-string, null, blank, or whitespace-only `param` MUST NOT authorize
previous-response recovery, full-history replay, account migration, or any
other proof-gated retry. A valid string parameter MAY be normalized by trimming
surrounding whitespace before public serialization.

#### Scenario: malformed parameter cannot authorize recovery

- **GIVEN** an anchored request receives a canonical stale-anchor code or
  previous-response-not-found message with `param = null`, a non-string value,
  or blank whitespace
- **WHEN** the proxy evaluates replay eligibility
- **THEN** the request fails closed and remains in the existing terminal path
- **AND** no unanchored replay or account switch is authorized

#### Scenario: absent parameter keeps the narrow parameterless classifier

- **GIVEN** an anchored request receives `code = invalid_request_error`, no
  `param`, and the exact normalized `Invalid previous_response_id.` message
- **WHEN** the proxy evaluates continuity recovery
- **THEN** the existing parameterless previous-response classifier may match
- **AND** unrelated invalid-request messages remain unmatched

### Requirement: Public error serializers omit malformed parameter metadata

When a public WebSocket or HTTP Responses serializer emits an error containing
a present malformed `param`, it MUST omit that field. It MUST preserve the
native event envelope and MUST NOT expose a raw stale `previous_response_id`.
A valid string parameter MUST remain available in trimmed form. This
sanitization applies regardless of the error code or message; only an error
that has no `param` metadata is left unchanged. When stale-anchor masking
applies, its generic terminal envelope takes precedence and may remove even a
valid `previous_response_id` parameter.

The Chat Completions adapter MUST apply the same parameter sanitization to the
nested error detail, but it MUST retain the documented Chat Completions error
envelope rather than forwarding the native Responses event type or outer
`response` object.

#### Scenario: native malformed error is sanitized without changing its shape

- **GIVEN** a native terminal `error` or `response.failed` frame contains a
  present malformed `param`
- **WHEN** the native stream is serialized for the client
- **THEN** the error retains its event type and other fields
- **AND** the malformed `param` is omitted

#### Scenario: Chat Completions errors keep their adapter envelope

- **GIVEN** a Chat Completions stream receives a native terminal error with a
  present malformed `param`
- **WHEN** the Chat adapter serializes the error
- **THEN** it emits the documented `{"error": ...}` Chat Completions shape
- **AND** the nested malformed `param` is omitted
- **AND** native Responses-only fields are not forwarded

#### Scenario: public stale-anchor error remains generic

- **GIVEN** a public `/v1/responses` stream receives a stale-anchor error,
  including a typeless or nested `response.failed` shape
- **WHEN** the API normalizes the stream
- **THEN** it emits the existing `stream_incomplete` envelope
- **AND** it removes the stale anchor metadata while preserving the nested
  response id when one was supplied

### Requirement: Typeless terminal errors retain settlement and correlation data

The streaming normalizers MUST classify a payload with a dictionary `error`
and no string `type` as an `error` event for terminal settlement. A nested
`response.failed` error MUST retain its outer response identifier when its
error details are masked or sanitized. A valid native error frame that needs no
sanitization MUST remain byte-identical.

#### Scenario: typeless error flushes pending terminal-adjacent state

- **GIVEN** a stream has buffered reasoning-summary data followed by a typeless
  error payload
- **WHEN** the normalizer processes the error
- **THEN** it flushes the buffered data before forwarding the terminal error

#### Scenario: nested terminal masking preserves response id

- **GIVEN** a `response.failed` payload has an outer `response.id` and a stale
  previous-response error
- **WHEN** the public normalizer masks the stale error
- **THEN** the terminal event keeps the same `response.id`
- **AND** the error details are the generic `stream_incomplete` shape

### Requirement: Optional recovery-spool cleanup MUST remain optional

For an anchored local HTTP bridge recovery that has not dispatched its
replacement request, the implementation MUST attempt the operation-spool
cleanup while the failed durable session still holds the operation owner fence.
If that cleanup is unavailable, refuses the owner, or raises, the optional
cleanup MUST NOT replace the original recovery path with a
`bridge_continuity_persistence_failed` response. The existing durable operation
rebind remains responsible for clearing stale attempt material before the
replacement dispatch.

#### Scenario: Optional reset refusal does not abort local recovery

- **GIVEN** an anchored operation belongs to the failed durable session
- **AND** local recovery has not dispatched a replacement request
- **WHEN** the optional spool reset returns `False`
- **THEN** recovery continues with the same operation identity
- **AND** the replacement is allowed to reach the normal fenced operation
  rebind path.

#### Scenario: Optional reset exception does not mask the upstream failure

- **GIVEN** the same anchored local recovery path
- **WHEN** the optional spool reset raises
- **THEN** the exception is handled as optional cleanup failure
- **AND** recovery does not emit a new continuity-persistence error solely for
  that cleanup failure.

### Requirement: Required replay-spool cleanup MUST fail closed

Before an account-neutral or owner-bound unanchored stale-anchor replay, the
implementation MUST keep the operation fence and required spool reset strict.
An unavailable or refused required reset, or a required reset operation that
raises an exception, MUST return the typed `bridge_continuity_persistence_failed`
error and MUST NOT dispatch the unanchored replacement request.

#### Scenario: Required reset refusal blocks unanchored replay

- **GIVEN** a verified stale-anchor full-resend replay
- **WHEN** the required operation-spool reset is unavailable or returns
  `False`
- **THEN** the request fails with `bridge_continuity_persistence_failed`
- **AND** no unanchored replacement request is sent.

### Requirement: classify edge challenge 403 narrowly

The proxy MUST classify a WebSocket handshake as an edge challenge only when
the response status is 403 and the response carries explicit challenge
evidence (`cf-mitigated: challenge`, or a Cloudflare-identified HTML body
containing known challenge markers). Structured JSON permission errors, local
`ip_forbidden`, ordinary reverse-proxy HTML, and missing evidence MUST remain
non-challenge failures.

#### Scenario: Cloudflare challenge is recognized

- **WHEN** a WebSocket handshake returns 403 with `cf-mitigated: challenge`
- **THEN** the response is classified as an edge challenge

#### Scenario: ordinary permission denial is not a challenge

- **WHEN** a WebSocket handshake returns structured JSON
  `permission_error` with status 403
- **THEN** the response remains a non-challenge permission failure

#### Scenario: unmarked reverse-proxy HTML is not a challenge

- **WHEN** a WebSocket handshake returns an HTML 403 from Nginx without
  challenge evidence
- **THEN** the response remains a non-challenge upstream failure

### Requirement: classified edge challenges are websocket transport failures

A direct-connect WebSocket handshake rejected with a classified edge
challenge MUST carry the same transport-failure provenance as a connect
timeout or 5xx upgrade rejection: the failure MUST surface without recording
an account-health penalty and MUST arm the bounded handshake-denial marker so
Codex clients are steered to the HTTP transport. Routed-proxy handshake
challenges MUST NOT arm the instance-wide marker.

#### Scenario: direct edge challenge steers clients to HTTP

- **WHEN** a direct upstream WebSocket handshake returns a classified edge
  challenge before any response event
- **THEN** the failure surfaces without an account penalty
- **AND** the next Responses WebSocket handshake is denied with HTTP 426
  while the marker is armed

#### Scenario: automatic transport falls back in-request

- **WHEN** the raw streaming path selects the websocket transport in `auto`
  mode
- **AND** the upstream handshake is rejected with a classified edge challenge
- **THEN** the proxy retries the request once over HTTP on the same account

#### Scenario: forced WebSocket preserves the challenge error

- **WHEN** upstream transport is forced to WebSocket
- **AND** the handshake returns an edge challenge
- **THEN** the proxy does not retry over HTTP

### Requirement: OpenAI error path families include their exact roots

Locally generated HTTP errors for exact `/v1` and `/backend-api` requests MUST
use the same OpenAI-compatible external error envelope as requests under
`/v1/` and `/backend-api/`. Exact roots, trailing-slash roots, and unknown child
paths MUST preserve equivalent HTTP status, error type, error code, and message
semantics. This classification MUST NOT change dashboard, static-asset, health,
or other non-OpenAI route error formats.

#### Scenario: Exact OpenAI family roots are not found

- **WHEN** a client sends `GET /v1` or `GET /backend-api`
- **THEN** the service returns HTTP 404
- **AND** the body is an OpenAI error envelope with
  `error.type = invalid_request_error`, `error.code = not_found`, and
  `error.message = Not Found`

#### Scenario: Equivalent OpenAI family paths remain consistent

- **WHEN** a client requests a trailing-slash root or unknown child under
  `/v1/` or `/backend-api/`
- **THEN** the service returns the same 404 OpenAI error contract as the exact
  family root

#### Scenario: Non-OpenAI routes retain their native error formats

- **WHEN** a request fails on a dashboard, static-asset, health, or other
  non-OpenAI route
- **THEN** the service retains that route family's existing external error
  format

### Requirement: HTTP bridge relays unchanged upstream events as their upstream JSON text

When an HTTP bridge session relays an upstream Responses event to a pending
request's downstream SSE stream and the proxy did not change the event's JSON
value (no downstream response-id alignment, tool-call rewrite, or error
masking applied), the relayed `data:` line MAY be the upstream JSON text
verbatim instead of a proxy re-serialization. The relayed block MUST use the
canonical framing `event: <type>\ndata: <json>\n\n` when the payload carries a
non-empty string `type`, and data-only framing `data: <json>\n\n` otherwise.
Any SSE-compliant parser MUST obtain the identical JSON value from the relayed
block that it would obtain from the proxy's re-serialized form. Events whose
JSON value the proxy changed MUST continue to be re-serialized from the
rewritten payload.

#### Scenario: Unchanged event with non-ASCII text is relayed as upstream UTF-8

- **GIVEN** an HTTP bridge request whose upstream response id already matches
  its downstream response id
- **WHEN** the upstream emits a single-line `response.output_text.delta` event
  whose `delta` contains Korean text and U+2028
- **THEN** the downstream block is `event: response.output_text.delta` followed
  by a `data:` line containing the upstream JSON text unchanged
- **AND** parsing the block yields the same JSON value as parsing the proxy's
  re-serialized form of that event

#### Scenario: Unchanged ASCII event is byte-identical to re-serialization

- **WHEN** the upstream emits a compact, ASCII-only event that the proxy does not
  rewrite
- **THEN** the relayed block is byte-identical to the block the proxy would have
  produced by re-serializing the parsed payload

#### Scenario: Rewritten event is re-serialized

- **GIVEN** an HTTP bridge request whose downstream response id differs from the
  upstream response id
- **WHEN** the upstream emits an event carrying the upstream response id
- **THEN** the relayed block is re-serialized from the rewritten payload and
  carries the downstream response id
- **AND** the upstream JSON text does not appear in the relayed block

#### Scenario: In-place trimmed parallel tool uses are re-serialized

- **GIVEN** an HTTP bridge request that already relayed a
  `response.output_item.done` `multi_tool_use.parallel` call containing a
  side-effect tool use
- **WHEN** the upstream emits a second `response.output_item.done`
  `multi_tool_use.parallel` call whose tool uses only partially repeat the first
- **THEN** the relayed block is re-serialized from the trimmed payload and
  carries only the non-duplicate tool uses
- **AND** the untrimmed upstream JSON text does not appear in the relayed block

#### Scenario: Typeless error frame stays data-only

- **WHEN** an upstream frame is a JSON object without a string `type` field
- **THEN** the relayed framing derived from that payload has no `event:` line

### Requirement: Per-request detached-session retire sweep bounds its lock wait

The fail-safe sweep that reconsiders detached HTTP-bridge generations on every bridge request MUST use one five-second monotonic deadline for its aggregate detached-session lock waits. Each retirement attempt MUST receive only the remaining time. Once the deadline expires, the sweep MUST stop starting attempts and emit one warning naming the number of unattempted sessions, if any. Timed-out and unattempted sessions MUST remain tracked with their lock state untouched for later sweeps and lifecycle cleanup. Session lifecycle owners (drain, close, cooldown-suppression retirement) MUST keep waiting for the lock without a bound so retirement decisions stay authoritative. Request cancellation MUST NOT bypass the shielded finalization sweep or transfer resource cleanup ownership.

#### Scenario: Busy detached lock does not park the request path

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is held by another task for longer than the bound
- **WHEN** a request runs the fail-safe sweep
- **THEN** the sweep returns after the bound without closing the session
- **AND** if sessions remain unattempted when the deadline expires, one warning reports their count
- **AND** the timed-out session remains tracked for later sweeps and lifecycle cleanup
- **AND** the lock remains owned by its holder with no stranded waiter

#### Scenario: Free detached lock still retires

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is free and which no turn owns
- **WHEN** a request runs the fail-safe sweep
- **THEN** the session is retired exactly as before

#### Scenario: Lifecycle owners keep the unbounded wait

- **GIVEN** a drain or close path calls the retire check without a bound while another task briefly holds the lock
- **WHEN** the holder releases
- **THEN** the retire check proceeds and retires the session

#### Scenario: Several busy sessions share one deadline

- **GIVEN** three detached sessions and a five-second sweep budget
- **WHEN** the first retirement attempt consumes three seconds and the second consumes its remaining two seconds
- **THEN** no third attempt starts and aggregate lock waiting is five seconds
- **AND** the deferred sessions remain tracked and a later sweep can retire them

#### Scenario: Cancelled request finalization uses the same deadline

- **WHEN** a bridge request is cancelled while several detached sessions have busy locks
- **THEN** shielded finalization uses one aggregate lock-wait deadline before cancellation propagates
- **AND** deferred sessions retain their existing cleanup owners

### Requirement: Cancelled streamed responses do not re-cancel deferred startup work every loop iteration

When a streamed Responses body is cancelled by its response scope (client disconnect or request teardown) while the startup-probe first-item task or the SSE keepalive chunk task is still running cancellation-deferring cleanup, the proxy MUST NOT re-deliver cancellation to that task on every event-loop iteration. The body MUST await such tasks through a per-waiter proxy future so the level cancellation is absorbed by the proxy and the awaited task receives at most one explicit teardown cancellation. Teardown MUST still wait for that task to settle before closing the iterator chain it drives, so no `aclose()` is attempted on a running async generator and the task's eventual exception is retrieved.

#### Scenario: Probe task is cancelled once while its cleanup is blocked

- **GIVEN** a streamed response whose startup-probe task is waiting on cancellation-deferring cleanup that has not completed
- **WHEN** the response scope is cancelled and the event loop runs many iterations
- **THEN** the probe task's cancellation count stays at the single explicit teardown cancel
- **AND** the deferred cleanup task is not cancelled
- **AND** the response body task finishes once the cleanup settles, without spinning the loop meanwhile

#### Scenario: Keepalive teardown waits for the chunk task without respinning it

- **GIVEN** the SSE keepalive injector's pending chunk task is still driving a cancellation-deferring source when the consumer is cancelled
- **WHEN** the event loop runs many iterations before that source's cleanup settles
- **THEN** the chunk task is cancelled at most once
- **AND** the source iterator is not closed while the chunk task drives it
- **AND** the source iterator is closed exactly once after the chunk task settles

### Requirement: Accepted output-free capacity failures are replayed within a single response lifecycle

When a native Codex HTTP bridge or direct WebSocket `response.create` has been accepted upstream — `response.created` and optionally `response.in_progress` were forwarded downstream and no output item, text or tool delta, reasoning prelude, or tool call has been observed — and the turn then fails output-free, the proxy MUST re-send the request exactly once and the client MUST observe a single response lifecycle: exactly one `response.created`, no duplicated `response.in_progress`, and every later frame (including `response.completed` or a second terminal failure) carrying the response id the client already read. The bounded clean-close retry that pre-created requests receive MUST NOT extend an accepted lifecycle to a third send.

An output-free failure is either a terminal `error` / `response.failed` whose normalized code is `server_is_overloaded`, `overloaded_error`, or `model_at_capacity`, or whose message names the selected-model capacity, or a transport close that is not account-neutral. The terminal MUST NOT name another response and MUST NOT report output items or billed output or reasoning tokens. Quota and rate-limit codes after acceptance MUST keep their stronger classification and MUST NOT be replayed. Anchored continuations without a retry-safe fresh payload, requests sharing the socket with another pending request, and requests whose replay budget is consumed MUST NOT be replayed. The other-pending check MUST be evaluated under the pending lock at the moment the replay is decided, after every await the terminal handling performs, never from a snapshot taken before such an await. The requirement "Direct WebSocket replay never mixes numeric response sequences" is unchanged: a direct WebSocket request whose forwarded prelude carried a finite integer `sequence_number` MUST NOT be replayed, and its capacity terminal or transport close keeps the existing fail-closed handling.

The replay MUST capture the client-visible response id and arm prelude suppression before the request's upstream response id is cleared. On the HTTP bridge the replay MUST re-claim the session response-create gate without waiting; when another request holds the gate the upstream terminal MUST be forwarded unchanged. The replay MUST re-acquire shared work admission before sending, and when the request body is account-neutral the failing account MUST be excluded from the replacement selection on the HTTP bridge and on a direct WebSocket whose affinity cannot resolve to a hard sticky owner. A replay that swaps a retry-safe fresh body in for an anchored one MUST re-derive its owner requirement from the fresh body (an account-neutral body releases the anchor owner's pin; an account-bound body keeps it), and the failing account MUST NOT be excluded while the replay is still required to reconnect to it. On the direct WebSocket surface the failing account MUST NOT be excluded either when the request's affinity may resolve to a hard `CODEX_SESSION` owner the request state does not carry -- a `CODEX_SESSION` affinity (bare session header or turn state) or any affinity that consults a raw legacy compatibility row (`legacy_selection_key`) -- because a resolved hard row narrows selection to its owner and excluding that owner fails every re-selection with `hard_affinity_saturated`; such a replay reconnects through selection without an exclusion, exactly as the created-only transport-close replay did before this change. When the fresh body cannot release that pin -- the client supplied the anchor, or the fresh body names an account-scoped upload -- a capacity terminal MUST re-send the anchored body to the account that accepted it and MUST NOT fail the turn closed as `previous_response_owner_unavailable`. The classified capacity code an accepted terminal is replayed under MUST be a transparent replay code (`model_at_capacity` is reported as `server_is_overloaded`). When the request is API-key-backed, the failing account's health write MUST wait for the request's reservation settlement, as for pre-created replays. On the HTTP bridge only a terminal transport message (close or error) MAY replay an accepted turn.

#### Scenario: Bridge terminal capacity error after acceptance is retried on another account

- **GIVEN** the HTTP responses session bridge is enabled and two accounts are selectable
- **AND** upstream delivers `response.created` and `response.in_progress` for a native Codex request and then an `error` with `code = "server_is_overloaded"` or `code = "model_at_capacity"` and no output
- **WHEN** the bridge processes that terminal
- **THEN** the request is re-sent once on the other account
- **AND** the client observes exactly one `response.created`
- **AND** the `response.completed` the client receives carries that `response.created` id

#### Scenario: Bridge bare overload code after an accepted anchored follow-up is replayed

- **GIVEN** an HTTP bridge follow-up turn whose `previous_response_id` the proxy injected and whose full resend is retained as a retry-safe fresh body
- **AND** upstream accepted it (`response.created` forwarded) and produced no output
- **WHEN** upstream then emits an `error` with code `server_is_overloaded` or `overloaded_error` whose message does not name the selected-model capacity
- **THEN** the bridge stages the single-lifecycle replay and hands the request to the pre-created retry exactly as it does after the selected-model capacity message (owner-switch prep with the fresh body, or the anchored body to its owner)
- **AND** a client-supplied anchor is forwarded unchanged, because the bridge's pre-created retry only re-sends proxy-injected anchors

#### Scenario: Bridge abrupt close after acceptance is retried on another account

- **GIVEN** an unanchored native Codex bridge request whose `response.created` and `response.in_progress` were forwarded
- **WHEN** the upstream websocket closes with a non-account-neutral close before any output
- **THEN** the request is re-sent once on another account within the same single response lifecycle

#### Scenario: WebSocket accepted capacity failures are retried within one lifecycle

- **GIVEN** a direct `/backend-api/codex/responses` WebSocket request whose `response.created` and `response.in_progress` were forwarded
- **AND** the connection carries no Codex session affinity that may resolve to a hard sticky owner (no `CODEX_SESSION` kind and no raw legacy compatibility lookup)
- **WHEN** upstream then emits an output-free capacity `error` or closes the transport abruptly
- **THEN** the proxy reconnects excluding the failing account and re-sends the request once
- **AND** the client observes exactly one `response.created` and one `response.in_progress` and a `response.completed` carrying that id

#### Scenario: A sequenced direct WebSocket prelude keeps the existing fail-closed contract

- **GIVEN** a direct `/backend-api/codex/responses` WebSocket request whose forwarded `response.created` (0) and `response.in_progress` (1) carried finite integer `sequence_number` values
- **WHEN** upstream then emits an output-free capacity `error`
- **THEN** the proxy MUST NOT reconnect or re-send the request and MUST finalize and surface that terminal unchanged
- **WHEN** upstream instead closes the transport abruptly before any output
- **THEN** the proxy MUST record the request as `stream_incomplete` without emitting a synthetic terminal under the visible id and MUST close the downstream WebSocket with code 1011
- **AND** in both cases no replacement account is connected

#### Scenario: Output before the capacity failure disables the replay

- **WHEN** any `response.output_item.added`, text or tool delta, or buffered reasoning prelude was observed before the capacity terminal or transport close
- **THEN** the proxy MUST NOT replay the request and MUST forward the terminal unchanged

#### Scenario: Terminals reporting output are not replayed

- **WHEN** the capacity terminal payload carries a non-empty `output` list, `usage.output_tokens > 0`, or `usage.output_tokens_details.reasoning_tokens > 0`
- **THEN** the proxy MUST NOT replay the request

#### Scenario: Quota and rate-limit codes after acceptance stay fail-closed

- **WHEN** an accepted request fails with `rate_limit_exceeded`, `usage_limit_reached`, `insufficient_quota`, `usage_not_included`, or `quota_exceeded`, even with the selected-model capacity message
- **THEN** the proxy MUST forward the terminal without replaying

#### Scenario: Anchored continuations without a retry-safe fresh payload are not replayed

- **WHEN** the accepted request carries `previous_response_id` and no retry-safe fresh payload is retained
- **THEN** the proxy MUST forward the failure without replaying

#### Scenario: A second capacity failure surfaces one terminal under the visible id

- **WHEN** the replayed request also fails output-free, including a clean upstream close before the replay's `response.created`
- **THEN** the proxy MUST NOT attempt a third send
- **AND** the client observes one terminal failure with no second `response.created` or `response.in_progress`

#### Scenario: An anchored accepted follow-up is replayed with its fresh body on another account

- **GIVEN** a direct WebSocket follow-up turn whose `previous_response_id` the proxy injected and whose full resend is retained as a retry-safe, account-neutral fresh body
- **AND** the connection carries no Codex session affinity that may resolve to a hard sticky owner
- **AND** upstream accepted the turn (`response.created` and `response.in_progress` forwarded) on the anchor's owner
- **WHEN** upstream then emits an output-free capacity `error` with code `server_is_overloaded` or `model_at_capacity`, or closes the transport abruptly
- **THEN** the proxy re-sends the fresh body without `previous_response_id` on another account, excluding the owner
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id

#### Scenario: An account-bound accepted replay reconnects to its owner

- **WHEN** an accepted replay's body still requires one account (bound replay owner, uploaded file, anchored owner, or turn-state owner)
- **THEN** the proxy MUST NOT exclude that account and MUST reconnect to it

#### Scenario: A Codex-session accepted replay keeps its hard sticky owner eligible

- **GIVEN** a direct WebSocket connection whose `session_id` (or thread) header selects a `CODEX_SESSION` affinity, so selection consults the raw legacy compatibility row for that key
- **AND** that raw row names one account as the hard owner while the request state carries no owner pin (unanchored, account-neutral turn)
- **AND** upstream accepted the turn on that owner (`response.created` and `response.in_progress` forwarded) and then emitted an output-free capacity `error` or closed the transport abruptly
- **WHEN** the proxy replays the turn
- **THEN** the proxy MUST NOT exclude the owner or request a sticky reallocation, and MUST reconnect through selection so the hard row resolves to the owner again
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never a connect failure after `hard_affinity_saturated`
- **AND** the owner still receives the capacity health penalty (deferred behind API-key settlement when the request is keyed)
- **AND** a request whose affinity cannot resolve to a hard owner (no `CODEX_SESSION` kind, no raw legacy lookup) is still excluded and moved

#### Scenario: A transport close of a client-anchored accepted turn reconnects to its owner

- **GIVEN** a direct WebSocket accepted turn (`response.created` and `response.in_progress` forwarded) whose `previous_response_id` the client supplied and whose full resend is retained as a retry-safe fresh body
- **WHEN** upstream closes the transport abruptly before any output
- **THEN** the proxy re-sends the fresh body once to the account that accepted it, keeping the owner pin the client's anchor established and without excluding that account
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id

#### Scenario: A turn-state session re-sends an accepted replay to its owner

- **GIVEN** a direct WebSocket connection whose `x-codex-turn-state` resolves to an owner account (the native Codex flow: the handshake token of the previous connection is echoed and the proxy injects the completed id as `previous_response_id`, retaining the full resend as a retry-safe fresh body)
- **AND** upstream accepted the follow-up on that owner and then emitted an output-free capacity `error` or closed the transport abruptly
- **WHEN** the proxy replays the turn with the fresh body
- **THEN** the turn-state owner pin survives the fresh-body install (it is a session pin, not a body pin) and the proxy MUST NOT exclude the owner
- **AND** the proxy reconnects to the owner on a fresh socket and re-sends the fresh body once
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never `previous_response_owner_unavailable`
- **AND** a pre-created owner replay in the same session (capacity code before `response.created`) is likewise re-sent to the owner instead of excluding it

#### Scenario: A capacity terminal of an anchored accepted turn that cannot leave its owner is re-sent to that owner

- **GIVEN** a direct WebSocket accepted turn (`response.created` and `response.in_progress` forwarded) that carries `previous_response_id` and retains a retry-safe fresh body
- **AND** the fresh body cannot release the anchor owner's pin: the client supplied the anchor, or the fresh body names an account-scoped uploaded file
- **WHEN** upstream emits an output-free capacity `error`
- **THEN** the proxy re-sends the anchored body once to the account that accepted it, without excluding it
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id
- **AND** the proxy MUST NOT rewrite the terminal into `previous_response_owner_unavailable`
- **AND** for an API-key-backed request the owner's health write waits for the reservation settlement

#### Scenario: Accepted replay health writes wait for API-key settlement

- **GIVEN** an API-key-backed accepted request that fails output-free and is replayed
- **WHEN** the replay reaches its terminal
- **THEN** the failing account's health write is applied only after the request's reservation settlement commits

#### Scenario: Another pending request or a busy create gate forwards the original error

- **WHEN** another request is pending on the same upstream socket, or another `response.create` holds the bridge session response-create gate
- **THEN** the proxy MUST forward the upstream terminal unchanged and MUST NOT modify the accepted request's identity

#### Scenario: A younger turn admitted while the accepted terminal is handled forwards the original error

- **GIVEN** a direct WebSocket accepted request (`response.created` and `response.in_progress` forwarded) that released the session response-create gate at `response.created`
- **AND** the sender admits and sends a younger `response.create` on the same upstream socket while the reader awaits the accepted request's thread-affinity refresh for its output-free capacity terminal
- **WHEN** the reader decides whether to replay the accepted request
- **THEN** the other-pending guard MUST observe the younger request
- **AND** the proxy MUST forward the upstream terminal unchanged, MUST NOT modify the accepted request's identity, and MUST NOT retire the shared socket under the younger request

#### Scenario: A transport close while another response shares the bridge socket replays nothing

- **GIVEN** an HTTP bridge upstream socket carrying an accepted, output-free request and a sibling: either a response the client is already reading, or a pre-created `response.create` that has not seen its `response.created` yet and still holds the session response-create gate
- **WHEN** the upstream socket closes abruptly
- **THEN** the proxy MUST NOT reconnect the accepted request alone, and MUST NOT reconnect the pre-created sibling alone either
- **AND** both pending requests fail closed with `stream_incomplete` promptly
- **AND** for the visible-sibling shape this is exactly what happened before accepted replays existed; for the pre-created-sibling shape it replaces the earlier behaviour of retrying the pre-created sibling alone while the accepted request stayed bound to the dead upstream until the stale pending sweep -- the pre-created sibling gives up its lone retry so no request is stranded

#### Scenario: A binary frame does not replay an accepted turn

- **WHEN** the bridge upstream socket yields a protocol-invalid binary frame while an accepted request is pending
- **THEN** the proxy MUST NOT replay the accepted request

### Requirement: HTTP-bridge reconnect distinguishes deleted file owners from transient misses

HTTP-bridge reconnect MUST mark a live required file-pin owner as continuity
provenance. It MUST map a selection miss immediately to the existing 502
`previous_response_owner_unavailable` response only when selection confirms
that the required owner account no longer exists. A transient required-owner
saturation MUST retain bounded recovery within the reconnect deadline and MUST
retry the same owner. If recovery does not succeed, reconnect MUST retain the
existing terminal fail-closed response.

#### Scenario: Deleted required file owner maps immediately

- **GIVEN** a reconnect request has a live file pin to `account_a`
- **AND** `account_a` no longer exists in the runtime account catalog
- **WHEN** continuity-owner selection confirms that disappearance
- **THEN** reconnect MUST return 502 `previous_response_owner_unavailable`
- **AND** it MUST NOT wait for generic account-selection recovery

#### Scenario: Transient required file-owner saturation recovers

- **GIVEN** a reconnect request has a live file pin to `account_a`
- **AND** selection reports that `account_a` is transiently saturated
- **WHEN** the existing bounded recovery wait permits another attempt
- **THEN** reconnect MUST wait within its existing deadline
- **AND** it MUST retry `account_a` without enabling fallback
- **AND** it MUST continue successfully if `account_a` recovers

#### Scenario: Terminal transient miss remains fail-closed

- **GIVEN** reconnect has any owner required to be preferred
- **AND** bounded recovery cannot select that owner before termination
- **WHEN** reconnect returns the terminal selection failure
- **THEN** it MUST return the existing 502 `previous_response_owner_unavailable`

### Requirement: HTTP bridge accepted replays keep a hard-capable Codex session owner eligible

When the HTTP responses session bridge replays an accepted, output-free native Codex turn within its single response lifecycle (the `replay_downstream_response_id` capture of the accepted output-free capacity replay) on a hard bridge session key (`session_header`, `thread_header`, or `turn_state_header`), and the session affinity the reconnect selects with may resolve a hard `CODEX_SESSION` owner the request state does not carry -- a `CODEX_SESSION` affinity (bare session header or turn state) or any affinity that consults a raw legacy compatibility row (`legacy_selection_key`) -- the bridge MUST NOT exclude the account that accepted the turn from the replacement selection. The replay MUST reconnect through selection without that exclusion, so a resolved hard row resolves to the same owner again and the request is re-sent to it, and the reconnect MUST NOT wait on `hard_affinity_saturated` for the owner the replay itself excluded. The replacement reconnect MUST otherwise follow the established fresh hard-request path: the owner's account-scoped response-create lease is released and re-acquired for the selected account, no owner pin is installed, and the retry does not require a same-account reconnect, so a soft namespaced row may still move the replay through selection when the owner is unavailable. When that selection returns a different account, the replacement handshake MUST carry no turn state learned on the owner's socket (the modified requirement "Cross-account bridge retries clear turn-state" below applies to this unexcluded move as well).

The created-only (pre-created) bridge replay MUST keep excluding the silent account exactly as before; a soft bridge session key MUST keep excluding the failing account so the accepted replay moves to another account; a model-fallback replay MUST keep excluding the rejecting account; and a hard session key whose affinity cannot resolve a hard owner MUST keep the exclusion. The predicate deciding whether an affinity may resolve a hard owner MUST be shared with the direct WebSocket surface.

#### Scenario: Bridge bare-session accepted failure is re-sent to its hard sticky owner

- **GIVEN** the HTTP responses session bridge is enabled, two accounts are selectable, and a native Codex request carries a `session_id` header, so the bridge session key is hard (`session_header`) and its affinity consults the raw legacy `CODEX_SESSION` row for that value
- **AND** that raw row names the accepting account as the hard owner while the request carries no owner pin (unanchored, account-neutral turn)
- **AND** upstream delivers `response.created` and `response.in_progress` on that owner and then an output-free capacity `error` (`server_is_overloaded` or `model_at_capacity`) or closes the transport abruptly (1011 or 1006) before any output
- **WHEN** the bridge replays the turn
- **THEN** the replacement selection is performed with no excluded account and resolves the raw row to the same owner
- **AND** the request is re-sent once to that owner on a fresh socket and never to the other account
- **AND** the client observes exactly one `response.created` and a `response.completed` carrying that id, never a `hard_affinity_saturated` selection failure or a synthetic `stream_incomplete`

#### Scenario: Created-only and soft-key bridge replays keep excluding the failing account

- **GIVEN** the same hard `session_header` bridge session whose affinity consults the raw legacy row
- **WHEN** a pre-created request (no `response.created` observed) is replayed after a transport close
- **THEN** the silent account is excluded from the replacement selection exactly as before this change
- **WHEN** instead an accepted output-free request on a soft bridge session key (for example `request` or `prompt_cache`) is replayed
- **THEN** the failing account is excluded and the replay moves to another account, unchanged

### Requirement: Public Responses preserve tool discovery output
The public Responses API SHALL preserve tool_search_output items, including their identifiers, execution mode, status, and loaded tool definitions, in output-item events, terminal response output, and collected JSON responses. Existing normalization of unrecognized output types SHALL remain unchanged.

#### Scenario: Discovery output arrives in streamed item events
- **WHEN** upstream emits tool_search_output in response.output_item.added or response.output_item.done
- **THEN** public streaming clients receive that item with its tool definitions intact

#### Scenario: Terminal output includes or omits discovered tools
- **WHEN** upstream completes with either populated output or an empty output list after emitting completed discovery items
- **THEN** public SSE terminal output and collected JSON output contain the completed tool_search_output item and loaded tool definitions

### Requirement: Codex exposes an explicit Daybreak Blue routing profile

The published Codex client configuration MUST keep the ordinary `codex-lb` provider free of `X-Codex-LB-Required-Capability` and MUST define a separate `codex-lb-daybreak-blue` provider that sources its proxy API key from `CODEX_LB_API_KEY` and whose static headers contain exactly one `X-Codex-LB-Required-Capability: trusted_cyber` carrier. A machine-local `daybreak-blue` profile file MUST select that provider and the canonical `gpt-5.6-sol` model. Activating the Daybreak profile MUST be explicit and MUST NOT modify the default provider selection. Direct Responses WebSocket ingress MUST require a valid proxy API key whenever the capability header is present, even when deployment-wide API-key auth is disabled, and MUST preserve the existing authentication behavior when the header is absent. Any capability-bearing HTTP request on an external provider-bound route that can select or forward an upstream account, including Responses, compact, thread-goal, Codex-control, opportunistic admission, warmup, files, transcription, chat, Images, and reset-credit consume routes, MUST authenticate the carrier and MUST then fail with `400 required_capability_transport_unsupported` before routing or upstream dispatch. Typed JSON Responses, compact, Chat Completions, Images generations, and reset-credit consume routes MUST apply that authenticate-then-deny check before FastAPI decodes the request body. A capability-bearing non-Responses WebSocket MUST apply the same authenticate-then-deny contract before owner lookup or upstream connection. Authenticated model-catalog, local API-key usage, and reset-credit listing requests MAY remain available because they perform no upstream account routing. The separate WHAM namespace MUST retain ordinary forwarding after the shared capability-header authentication rule and MUST NOT apply the Responses transport denial. Headerless ingress MUST retain its existing behavior. A legitimately forwarded internal bridge request MUST strip the capability carrier. If a carrier is appended to an otherwise valid signed internal bridge request, the target MUST authenticate it and fail closed before legacy-anchor validation, account selection, or upstream dispatch.

#### Scenario: Daybreak profile constrains the first attempt

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published `daybreak-blue` profile
- **AND** deployment-wide proxy API-key auth is disabled
- **THEN** capability ingress receives exactly one `trusted_cyber` carrier before the first account-selection call
- **AND** capability ingress validates the profile's proxy API key before accepting the carrier
- **AND** the first and every later selection requires an eligible security-work-authorized account
- **AND** no ordinary account receives an upstream attempt

#### Scenario: Ordinary provider remains unchanged

- **WHEN** an authenticated direct Responses WebSocket turn starts through the published ordinary `codex-lb` provider
- **THEN** the request contains no required-capability carrier
- **AND** capability ingress does not impose a new per-request API-key requirement
- **AND** the first account-selection call remains unconstrained by trusted-cyber routing

#### Scenario: Daybreak HTTP downgrade fails closed before routing

- **WHEN** Codex retains the Daybreak provider's capability carrier while falling back to an HTTP Responses or compact request
- **AND** the request supplies the profile's valid proxy API key
- **THEN** ingress returns `400 required_capability_transport_unsupported`
- **AND** no model source, account, reservation, bridge, or upstream attempt is selected
- **AND** the request is not replayed through ordinary routing

#### Scenario: Daybreak HTTP downgrade authenticates before transport denial

- **WHEN** a capability-bearing HTTP Responses request omits or supplies an invalid proxy API key
- **THEN** ingress returns the existing `401 invalid_api_key` authentication error
- **AND** no routing or upstream attempt occurs

#### Scenario: Ordinary HTTP routing remains unchanged

- **WHEN** the published ordinary provider sends an HTTP Responses request without the capability carrier
- **THEN** ingress does not impose the Daybreak per-request authentication or transport denial
- **AND** existing ordinary HTTP routing behavior is preserved

#### Scenario: Other provider-bound HTTP routes fail closed

- **WHEN** the authenticated Daybreak carrier reaches a thread-goal, Codex-control, opportunistic-admission, warmup, files, transcription, chat, Images, or reset-credit consume HTTP route
- **THEN** ingress returns `400 required_capability_transport_unsupported`
- **AND** no model source, account, reservation, owner binding, or upstream attempt is selected

#### Scenario: Non-Responses WebSocket fails closed

- **WHEN** the authenticated Daybreak carrier reaches a Live or Realtime WebSocket route
- **THEN** ingress returns `400 required_capability_transport_unsupported` during the handshake
- **AND** no call owner is resolved and no upstream connection is opened

#### Scenario: Signed internal forward cannot reintroduce the carrier

- **WHEN** an otherwise valid signed internal Responses bridge request arrives with the Daybreak carrier appended
- **THEN** ingress authenticates the proxy API key and returns `400 required_capability_transport_unsupported`
- **AND** no legacy bridge anchor, account selection, or upstream dispatch occurs

#### Scenario: Model-catalog initialization remains available

- **WHEN** the authenticated Daybreak provider requests `/backend-api/codex/models`
- **THEN** ingress returns the local model catalog
- **AND** no account is selected and no upstream request is made

#### Scenario: Profile does not grant authorization

- **WHEN** the Daybreak profile is selected without an authenticated proxy request or without an eligible security-work-authorized account
- **THEN** the existing capability-ingress or empty-capable-pool contract fails closed
- **AND** routing does not fall back to an ordinary account

### Requirement: Terminal stream settlement is immutable after delivery

When a Responses stream has observed and delivered a terminal event (`response.completed`, `response.failed`, `response.incomplete`, or `error`), a later downstream cancellation MUST NOT rewrite the terminal status, error, usage, or account-health settlement.

#### Scenario: Disconnect after terminal event

- **WHEN** the downstream closes after receiving a terminal event
- **THEN** the request log and settlement retain the terminal event's outcome
- **AND** the proxy does not record `client_disconnected` for that stream.

### Requirement: API-key reasoning allowlists reject disallowed explicit efforts

When an authenticated API key has a non-null
`allowedReasoningEfforts` policy, the proxy MUST derive the client-selected
effort from an explicit `reasoning.effort` or a supported model alias before
performing wire-level normalization. Policy values remain exact client-plane
choices: `xhigh` does not authorize `high`, and `ultra` does not authorize
`max`, even where their downstream wire forms coincide. If the client-selected
effort is not in the policy, the
proxy MUST reject the request before quota reservation, account or source
selection, or upstream dispatch. The rejection MUST use HTTP 403, OpenAI error type `permission_error`, code
`reasoning_effort_not_allowed`, and parameter `reasoning.effort`.

The policy MUST apply to Responses, compact Responses, and WebSocket Responses
requests. Its WebSocket error event MUST preserve the same error code and
parameter. It MUST evaluate the client-plane effort before existing
unsupported-effort fallback and `ultra` to `max` upstream-wire aliasing. A
request that omits an effort MUST retain current default behavior.
Applying the policy more than once to the same request, including across a
signed internal HTTP bridge hop, MUST be idempotent and MUST NOT re-authorize
an already-normalized wire value as though it were the original client choice.
Before source-routed Responses traffic is forwarded, accepted reasoning
aliases MUST be aligned with the authorized canonical `reasoning.effort` or
removed so a conflicting alias cannot select a disallowed effort upstream.
Blank alias strings MUST be treated as absent and MUST NOT mask a later
effort-bearing alias during authorization.
Disabled aliases MUST likewise be treated as inactive rather than masking a
separate enabled reasoning alias.
Provider reasoning metadata MUST be merged with enabled controls before effort
authorization instead of masking their implicit `medium` effort.
When no reasoning policy is active, source egress MUST retain existing
provider-shaped reasoning controls and their source-specific fields.
An allowlist MUST also preserve provider-shaped controls that select no effort;
their unrelated fields do not participate in effort authorization.

#### Scenario: Reject max before upstream dispatch

- **GIVEN** an API key with
  `allowedReasoningEfforts: ["minimal", "low", "medium", "high", "xhigh"]`
- **WHEN** a Responses request explicitly supplies `reasoning.effort: "max"`
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** no API-key quota reservation or upstream request is created

#### Scenario: Alias effort is evaluated as the client-selected value

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a client sends the model alias `gpt-5.6-sol-xhigh`
- **THEN** the proxy rejects the request with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

#### Scenario: Omitted effort remains compatible

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a Responses request omits `reasoning.effort` and uses no effort alias
- **THEN** the proxy does not add or replace a reasoning effort
- **AND** the request continues through the existing route

#### Scenario: Effort-less provider controls remain compatible

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"type": "adaptive", "budget_tokens": 2048}` without an effort
- **THEN** the source receives the original `thinking` object

#### Scenario: Source-routed conflicting alias cannot override policy

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: "max"`
- **THEN** the source receives the canonical `reasoning.effort: "low"`
- **AND** it does not receive the conflicting `thinking` alias

#### Scenario: Blank alias cannot hide a disallowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoningEffort: " "` and
  `thinking: "max"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Disabled alias cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `thinking: "disabled"` and
  `enable_thinking: true`
- **THEN** the service evaluates the enabled alias as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Provider metadata cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"summary": "auto", "enabled": true}`
- **THEN** the service evaluates the enabled control as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Effort-less provider control survives beside an allowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: {"type": "adaptive", "budget_tokens": 2048}`
- **THEN** the source receives both the authorized canonical effort and the
  original `thinking` object

### Requirement: Verified full resend can recover from selection-time owner loss

An HTTP bridge request MAY move from an unavailable continuity owner to another account only after a typed pre-visible `continuity_owner_unavailable` account-selection result, which the HTTP bridge maps to `previous_response_owner_unavailable`, and positive durable proof that the request contains the complete retained input history. A missing durable owner is not a selector result and MUST fail closed without replay. The durable row MUST provide a positive input-item count and full fingerprint, and the corresponding raw prefix of the incoming list-shaped input MUST match both before any projection occurs.

After the raw prefix proof, the service MUST construct a deterministic plaintext projection by omitting `reasoning`, `web_search_call`, `tool_search_call`, and `tool_search_output` items and removing upstream `id` fields from every retained input item. Retained `internal_chat_message_metadata_passthrough` MUST contain only a nonblank string `turn_id` when present. The projected suffix after the projected prefix MUST contain a completed assistant `output_text` or `refusal` boundary with nonblank content followed by nonblank fresh text or valid fresh file/image input. The suffix MAY contain multiple intervening turns only when every non-final user-input sequence is followed by another completed assistant boundary and the final sequence ends in fresh input. Direct intrinsic calls MAY precede an assistant boundary only when terminal completed or failed outputs settle every represented call in order. A call at the end of the verified raw prefix MAY be settled by its matching output at the start of the suffix. A direct-call/output sequence alone MUST NOT prove completeness because the persisted metadata does not identify omitted parallel calls. A matching prefix followed only by new user input, empty content, tool-call-only output, in-progress or partial retained output, duplicate, unmatched, or unresolved calls, or misordered call output MUST fail closed.

The service MUST validate the complete projected request after removing `previous_response_id`; it MUST reject nonblank conversation or prompt handles, remaining encrypted content, compaction, opaque account-scoped file/container/vector handles, nonportable file schemes, hosted, MCP, program-mediated, or unknown call or tool-choice state, unknown top-level fields, unknown or malformed top-level reasoning configuration, malformed message/content shapes, and tool outputs without exactly one matching intrinsic call. Assistant messages MUST contain only supported output parts, while user, system, and developer messages MUST contain only supported input parts. Inline data images and HTTP(S) file/image content MAY remain eligible. Eligible declared tools, tool choices, and retained direct calls MUST be shape-validated, account-neutral, and self-contained. Web-search filters, context size, and approximate location MUST use only the recognized nested fields and value types. An apply-patch call MUST use exactly one representation: a recognized structured `operation` with its exact discriminated fields, a nonblank legacy `patch`, or a nonblank legacy `input`.

For an eligible replay, the service MUST remove `previous_response_id`, strip every downstream session/turn alias, clear hard affinity, exclude the unavailable owner, prevent initial bridge-owner forwarding, and submit the complete projected request through a fresh server-namespaced recovery lane. It MUST NOT replay after downstream-visible output. Selection policy conflicts, authentication/connection failures after selection, incomplete history, or any unsafe request state MUST remain fail-closed.

#### Scenario: Client-supplied full resend moves from A to B

- **GIVEN** account A owns a completed previous response and its durable row stores the completed input count and fingerprint
- **AND** a follow-up supplies that previous response plus an account-neutral full resend whose retained prefix matches both values
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge removes the previous-response anchor and all stale affinity headers
- **AND** excludes account A and submits the complete fresh request once on account B
- **AND** the next turn for the recovered task remains on account B

#### Scenario: Proxy-injected anchor protects an equivalent full resend

- **GIVEN** a hard durable alias resolves a completed response and the incoming full resend matches its retained count and fingerprint
- **AND** the proxy injects that response as the reattach anchor
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the same fresh-replay rules apply after the injected anchor is removed

#### Scenario: Verified resend contains owner-bound reasoning

- **GIVEN** a verified full resend contains encrypted reasoning, server-assigned item IDs, and completed web or tool-search bookkeeping
- **AND** its retained assistant and direct-tool content is otherwise complete and portable
- **WHEN** required-owner selection returns typed `continuity_owner_unavailable` before output
- **THEN** the bridge omits the reasoning and search bookkeeping and strips upstream item identities
- **AND** no encrypted content or upstream item identity is sent to account B
- **AND** the validated plaintext projection is submitted once on account B

#### Scenario: Retained request contains account-scoped state

- **GIVEN** a full resend contains a conversation or prompt handle, compaction, encrypted content outside an omitted reasoning item, an opaque account-scoped file/container/vector handle, a nonportable file scheme, hosted or MCP call or tool-choice state, an unknown call type, or an unmatched tool output
- **WHEN** its required owner is unavailable
- **THEN** the request fails with `previous_response_owner_unavailable`
- **AND** none of that state is sent to another account

#### Scenario: Request shape is not completely understood

- **GIVEN** a purported full resend contains an unknown top-level field or malformed/unknown message content
- **WHEN** its required owner is unavailable
- **THEN** replay eligibility fails closed
- **AND** the service does not infer portability from the retained fingerprint alone

#### Scenario: Matching input prefix omits the prior response output

- **GIVEN** the incoming input prefix matches the durable count and fingerprint
- **AND** the suffix contains only a new user message, a direct-call/output sequence without a later completed assistant boundary, partial retained output, or unresolved direct calls
- **WHEN** the required owner is unavailable
- **THEN** replay eligibility fails closed with `previous_response_owner_unavailable`
- **AND** the proxy does not drop the previous-response anchor or send the incomplete transcript to another account

#### Scenario: Owner was selected before a later failure

- **GIVEN** the required owner was selected successfully
- **WHEN** refresh, authentication, WebSocket connection, transport, or timeout fails before output
- **THEN** the request keeps that ordinary failure classification
- **AND** the service does not activate cross-account full-resend recovery

#### Scenario: Durable continuity row has no account owner

- **GIVEN** a durable continuity row proves retained input but has no account owner
- **WHEN** the request is evaluated before account selection
- **THEN** the bridge returns `previous_response_owner_unavailable`
- **AND** it does not treat the missing owner as a typed selector miss or replay on another account

#### Scenario: Failure occurs after visible output

- **WHEN** any part of a response has become downstream-visible
- **THEN** the service does not replay the request on another account
- **AND** it terminates through the existing partial-output failure contract

### Requirement: Verified replay continuity remains task-specific and fenced

A recovery lane MUST use a server-namespaced key within the existing durable `internal_unanchored_parallel` kind. Once it registers a turn-state or previous-response alias, that specific alias MUST resolve the recovery lane ahead of a conflicting broad session-header alias, while the shared session-header alias remains unchanged for sibling tasks. Conflicting specific aliases MUST still fail with `continuity_owner_conflict`, and unrelated internal lanes MUST NOT receive recovery precedence.

Alias ownership changes MUST be atomic. A recovery lane MAY replace only an alias owned by a documented prompt-cache, session-header, or turn-state predecessor, or by an ownerless or released/null-lease or lease-expired prior recovery lane. It MUST NOT replace an actively leased recovery lane. An ordinary or stale session MUST NOT replace a recovery alias. Owner-epoch fencing MUST invalidate the fenced local session; rejection because an alias is protected MUST remove only the rejected alias and MUST preserve sibling aliases and the rest of the session.

A recovery lane MUST acquire and renew fenced durable ownership before publishing continuity or dispatching a request. Immediately before upstream dispatch, the lane MUST atomically publish the incoming turn-state alias and its latest-turn state. If cancellation occurs after that commit but before dispatch may have started, cleanup MUST roll back the provisional alias and latest-turn state before reporting cancellation. Rollback MAY restore the predecessor only when its owner epoch and account still match the registration receipt; otherwise rollback MUST remove only the provisional alias. Once dispatch may have started, the alias MUST remain on the recovery lane and the socket MUST retire rather than making an ambiguous resend possible. A completed response MUST NOT be advertised downstream as successful when its required durable response-alias publication fails.

#### Scenario: Recovered task and sibling share a session header

- **GIVEN** a recovered task registered a specific alias on account B
- **AND** its shared session header still resolves a sibling lane on account A
- **WHEN** the recovered task sends both aliases
- **THEN** durable lookup resolves account B's recovery lane
- **AND** session-header-only sibling traffic continues to resolve account A

#### Scenario: Specific recovery aliases conflict

- **GIVEN** a recovery turn-state alias and previous-response alias resolve different durable sessions
- **WHEN** both are supplied
- **THEN** the request fails with `continuity_owner_conflict`
- **AND** broad-alias precedence does not hide the conflict

#### Scenario: Stale session attempts to reclaim a recovery alias

- **GIVEN** a recovery lane owns a specific durable alias
- **WHEN** an ordinary or stale predecessor session registers the same alias
- **THEN** the durable write reports the alias as protected
- **AND** the recovery alias remains unchanged
- **AND** unrelated aliases on the rejected session remain usable

#### Scenario: Recovery lane rebinds a documented predecessor alias

- **GIVEN** a prompt-cache, session-header, turn-state, or ownerless/lease-expired prior recovery lane owns an alias for the same recovered task
- **WHEN** the current recovery lane registers that alias while its owner epoch is valid
- **THEN** the conditional durable write rebinds the alias atomically

#### Scenario: Active recovery lane protects its alias

- **GIVEN** an actively leased recovery lane owns a specific alias
- **WHEN** another recovery lane attempts to register that alias
- **THEN** the durable write reports the alias as protected
- **AND** the active recovery owner and alias remain unchanged

#### Scenario: Cancellation occurs after alias commit and before dispatch

- **GIVEN** a recovery lane atomically committed the incoming turn-state alias immediately before dispatch
- **WHEN** its task is cancelled before upstream send may have started
- **THEN** cleanup completes the fenced rollback before surfacing cancellation
- **AND** the predecessor is restored only if its captured epoch and account are unchanged
- **AND** otherwise only the provisional recovery alias is removed
- **AND** no upstream request is sent

#### Scenario: Cancellation occurs after dispatch may have started

- **GIVEN** a recovery lane committed the incoming turn-state alias
- **WHEN** cancellation occurs after upstream send may have started
- **THEN** the recovery alias remains authoritative
- **AND** the ambiguous socket retires before an admitted waiter can reconnect or submit on it

#### Scenario: Completed response alias cannot be persisted

- **WHEN** a recovery response reaches `response.completed`
- **AND** fenced durable publication of its response alias fails
- **THEN** downstream does not receive a successful completion for that response
- **AND** the recovery lane retires fail-closed

### Requirement: Recovery provenance survives lifecycle transitions

Reconnect, prewarm, authorization retry, and model-transition paths for a recovery lane MUST require its current account and MUST preserve typed ownership provenance. A model-transition descendant MUST receive a fresh server-namespaced recovery key. Every fresh connection to the replacement account MUST omit stale downstream affinity headers.

When a recovery request is later forwarded to its current replica owner, the origin MUST remove raw downstream affinity headers before signing the forward. The signed context MUST carry the recovered task's downstream turn-state and recovery-lane identity, and the target MUST NOT send retired aliases to the upstream account.

On process startup, a recent recovery row owned by the restarting instance MUST be retained as ownerless `DRAINING` continuity proof with an expired/released lease and unchanged `last_seen_at`. Recovery rows older than the existing ownerless cutoff MUST be deleted with their aliases, after which ordinary broad-alias resolution applies.

#### Scenario: Recovery reconnect stays on B

- **GIVEN** a verified replay established its recovery lane on account B
- **WHEN** the lane reconnects, prewarms, retries authorization, or changes to a compatible descendant session
- **THEN** account B is required through typed owner provenance
- **AND** no stale account-A affinity header reaches the new upstream connection

#### Scenario: Recovery lane is forwarded to its current replica owner

- **GIVEN** a later task turn reaches a non-owner replica with raw stale session aliases and a recovered downstream turn-state
- **WHEN** that replica forwards the recovery lane
- **THEN** the raw aliases are removed before signing
- **AND** only the recovered downstream turn-state and recovery identity are authenticated to the owner replica

#### Scenario: Replica restarts with recent recovery proof

- **GIVEN** a recent recovery row is owned by the restarting replica
- **WHEN** startup purges rows associated with that instance
- **THEN** the row becomes ownerless `DRAINING` proof without refreshing `last_seen_at`
- **AND** its specific alias continues to outrank a stale broad session alias

#### Scenario: Recovery proof ages past the cutoff

- **GIVEN** an ownerless or restarting-instance recovery row is older than the existing cutoff
- **WHEN** startup cleanup runs
- **THEN** the row and its aliases are deleted
- **AND** the stale proof no longer overrides ordinary broad-alias resolution

### Requirement: Ambiguous HTTP bridge prewarm dispatch retires the socket

If a prewarm `response.create` send may have started and its task is cancelled or otherwise interrupted before a terminal response, the service MUST mark the bridge closed and retiring before releasing response-create admission. Cleanup and retirement MUST finish despite repeated task cancellation. The service MUST NOT remove the prewarm demultiplexing state and then allow a visible request to reuse the same ambiguous socket.

#### Scenario: Prewarm task is cancelled during send

- **GIVEN** a prewarm frame may have been handed to the upstream transport
- **AND** a visible request is waiting on the same response-create gate
- **WHEN** the prewarm task is cancelled before a terminal response
- **THEN** the bridge is marked closed and retiring before the gate is released
- **AND** the admitted visible request is rejected without reconnecting or sending on that socket
- **AND** prewarm admission, pending state, account leases, and upstream resources are released exactly once

### Requirement: Bridge-local previous-response recovery classifies normalized error frames

When the HTTP bridge evaluates whether a failed anchored request may enter bridge-local previous-response recovery, it MUST classify the error frame with the same normalization as the WebSocket rewrite path: a missing or empty `code` MUST fall back to the error `type` before classification, and the parameterless ``Invalid `previous_response_id`.`` invalid-request shape MUST classify as a previous-response continuity miss. A classifiable previous-response rejection MUST route into previous-response recovery and MUST NOT be treated as an ambiguous transport failure that only feeds the retry-circuit cooldown.

#### Scenario: Terse parameterless rejection enters local recovery

- **GIVEN** an anchored HTTP bridge request fails with `type = "invalid_request_error"`, no `code`, no `param`, and the message ``Invalid `previous_response_id`.``
- **WHEN** the bridge evaluates bridge-local previous-response recovery for that failure
- **THEN** the failure classifies as a previous-response continuity miss
- **AND** the bridge attempts previous-response recovery instead of the ambiguous-transport path

#### Scenario: Code carried only in the error type classifies

- **GIVEN** an anchored HTTP bridge request fails with no `code` and `type = "previous_response_not_found"`
- **WHEN** the bridge evaluates bridge-local previous-response recovery for that failure
- **THEN** the failure enters previous-response recovery instead of the ambiguous-transport class

#### Scenario: Unrelated errors keep their classification

- **WHEN** a failed anchored request carries an error whose normalized code, param, and message do not match a previous-response continuity miss
- **THEN** the bridge MUST NOT classify it as a previous-response continuity miss

### Requirement: Ordered owner-unavailable settlement preserves external errors

Settlement ordering for streaming Responses owner-unavailable failures MUST NOT
change the downstream or request-log error envelope. The client and request log
MUST continue to use `previous_response_owner_unavailable`, including when the
original upstream code is retained for account-health recovery.

#### Scenario: Ordered rewrite keeps the public and logged classifier

- **WHEN** a streaming `/v1/responses` failure is rewritten after ordered
  reservation settlement
- **THEN** the downstream error code is `previous_response_owner_unavailable`
- **AND** the request-log error code is `previous_response_owner_unavailable`
- **AND** no raw stale-anchor identifier or source-ownership detail is exposed

### Requirement: Structured HTTP continuation promotion
Under automatic upstream transport and smart HTTP policy, the proxy SHALL
recognize a non-empty conversation identifier, a tool-result input item, or an
assistant response followed by new user input as continuation evidence, in
addition to existing response, cache, session and turn-state identifiers.
Tool declarations, instruction messages, and multiple user-only messages SHALL
NOT alone constitute continuation evidence.

#### Scenario: Full-history agent turn
- **WHEN** a smart HTTP request contains user, assistant, then user input without explicit continuity metadata
- **THEN** it is eligible for the upstream WS bridge
- **AND** the complete input remains intact unless existing verified hard-continuity rules authorize trimming

#### Scenario: Native identity without failure evidence
- **WHEN** a native Codex HTTP request has continuation evidence and upstream WS is healthy
- **THEN** the same smart/override policy as other HTTP requests applies
- **AND** native identity alone MUST NOT force HTTP

#### Scenario: Real upstream outage
- **WHEN** the existing recent upstream WS failure marker is active
- **THEN** HTTP entry paths MUST use upstream HTTP during its existing cooldown
- **AND** normal WS eligibility MUST return when it expires or clears

### Requirement: Inferred continuation locality remains soft
History-only bridge requests SHALL use a deterministic locality key based on
complete initial user input and instruction context, isolated by API-key scope.
Conversation identifiers SHALL have distinct locality from inferred histories.
Inferred locality SHALL NOT authorize previous-response injection, cross-account
replay, or dropping client history. Explicit turn/session ownership SHALL retain
precedence and existing recovery/fork/queue safeguards.

#### Scenario: Repeated history without response headers
- **WHEN** two compatible multi-turn requests retain the same initial user input and instructions
- **THEN** they can reuse the same healthy upstream connection without replaying response headers
- **AND** divergent complete initial inputs MUST NOT share an inferred locality key merely because their first 512 characters match

### Requirement: Chat Completions uses HTTP bridge policy
Subscription-backed Chat Completions SHALL apply the same HTTP bridge admission
and fallback policy to its converted Responses request for streaming and
non-streaming clients. Chat chunks, JSON, usage, error envelopes, reservation
settlement and source routing SHALL preserve their existing public contracts.

#### Scenario: Chat trailing slash uses the same handler
- **WHEN** a client posts to `/v1/chat/completions/`
- **THEN** the same authentication, routing, bridge and response contract as `/v1/chat/completions` SHALL apply

#### Scenario: Chat tool loop reuses upstream connection
- **WHEN** successive Chat requests include tool results and retain compatible initial context
- **THEN** eligible requests reuse the upstream WS bridge
- **AND** clients still receive Chat Completions responses

#### Scenario: Chat bridge fails before settlement handoff
- **WHEN** bridge startup fails or is cancelled before dispatch or service settlement ownership
- **THEN** the originating API releases its usage reservation
- **AND** an ambiguous owner-forward dispatch MUST NOT release the reservation without a definitive rejection

### Requirement: HTTP bridge routing reasons are observable
The proxy SHALL emit structured admission and bypass reasons and bounded-label
counters, and SHALL count bridge create/reuse/close/reconnect/idle-eviction events.
Metrics SHALL NOT label raw request, conversation, session, account or API-key identifiers.

#### Scenario: Identify policy exclusion and transport fallback
- **WHEN** a request remains HTTP because it is single-turn, policy-pinned, bridge-disabled, oversized, image-capable, or affected by a recent WS outage
- **THEN** its routing diagnostics distinguish that reason
- **AND** admission counters MUST NOT be represented as successful WS connections

### Requirement: Repeated zero-event idle failures poison dead anchors at the circuit threshold

For hard HTTP bridge keys, repeated zero-event idle failures MUST use the
existing durable retry-circuit counter to identify an anchor that should no
longer remain addressable; the counter resets on a completed response, so a run of consecutive failures proves the anchor never advanced. Both ambiguous eventless transport classes — `stream_idle_timeout` (including its aliased diagnostics) and `stream_incomplete` — MUST be able to trigger anchor poisoning at the threshold; a `clean_close` outcome MUST NOT itself trigger anchor poisoning. When consecutive failures for the same hard bridge
key reach the poison threshold, the proxy MUST abandon durable
continuity for that session and retire the bridge even when admission waiters
exist, and the shared retirement boundary MUST clear the poisoned durable anchor even when no admission waiter exists, while the session still owns its durable lease. If the clear cannot be confirmed on the waiterless retirement path, the proxy MUST re-attempt it when a later eligible eventless failure at or above the threshold retires the session. The poison threshold IS the retry circuit's own opening threshold
(`_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD`, two consecutive failures): a
fixed application constant, not a runtime setting.

Because the poison threshold coincides with the circuit's opening threshold,
the eventless poison-class strike that opens the circuit is also the strike
that authorizes the abandonment. The circuit opening MUST therefore quarantine
the key independently of the durable clear, as specified under the
silent-session quarantine requirement, so a full-resend probe after the
circuit opens is planned without the dead anchor even while that clear is
still awaiting I/O.

#### Scenario: Admission waiters cannot defer anchor poisoning forever
- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor

#### Scenario: Lease liveness comparison is timezone-safe
- **GIVEN** a durable bridge session whose `lease_expires_at` was read from a `timestamptz` column (offset-aware) on PostgreSQL
- **WHEN** the dead-owner classifier evaluates lease liveness against the application's naive-UTC clock
- **THEN** both timestamps MUST be normalized to naive UTC before comparison
- **AND** the anchored-lookup path MUST NOT raise on mixed-awareness datetimes

#### Scenario: Repeated eventless stream_incomplete failures poison the anchor
- **GIVEN** a hard durable bridge key has a stored durable anchor
- **AND** every anchored attempt fails eventlessly with `stream_incomplete` (for example a masked upstream previous-response rejection)
- **WHEN** consecutive failures for that key reach the poison threshold
- **THEN** the proxy clears the durable continuity anchors under the session's owner epoch
- **AND** the next attach starts from fresh durable state instead of looping through retry-circuit cooldown

#### Scenario: Waiterless retirement poisons the anchor at the threshold
- **GIVEN** a hard durable bridge key fails eventlessly with no admission waiters
- **WHEN** the shared retirement boundary records the eventless failure that reaches the poison threshold
- **THEN** the proxy clears the durable continuity anchors before releasing the durable lease

#### Scenario: Failed waiterless clear is re-attempted on the next threshold failure
- **GIVEN** the waiterless retirement path reached the poison threshold but the durable continuity clear could not be confirmed
- **WHEN** the next eligible eventless failure for the same key retires the session
- **THEN** the proxy re-attempts the durable continuity clear under the new session's owner epoch

#### Scenario: Clean closes never trigger anchor poisoning
- **WHEN** a `clean_close` retry-circuit outcome is recorded for a hard bridge key, at any consecutive-failure count
- **THEN** that outcome does not clear the durable continuity anchors

#### Scenario: The probe after the circuit opens is planned without the dead anchor

- **GIVEN** a hard durable bridge key has two consecutive eventless `stream_incomplete` failures, which open the circuit and reach the poison threshold in the same strike
- **WHEN** the cooldown expires and the next full-resend request is admitted as the probe
- **THEN** the key is quarantined and the probe is planned without the dead anchor
- **AND** the probe resends full history rather than the dead anchor

### Requirement: Eventless bridge failures terminate with a stable response id

When an anchored HTTP bridge continuation fails before any downstream response
event, the proxy MUST emit one terminal `response.failed` event.

That terminal event MUST include a stable `response.id` even when upstream
never emitted `response.created` or another response envelope before the
failure. Public `/v1/responses` normalization depends on that envelope to
synthesize the required leading `response.created` event without producing an
SDK parser failure.

#### Scenario: Eventless failure terminates with one response id

- **GIVEN** an anchored HTTP bridge continuation
- **AND** its upstream attempt fails before any downstream `response.*` event
- **WHEN** the bridge settles the turn
- **THEN** it emits one terminal `response.failed` event
- **AND** that terminal event includes a stable `response.id`

