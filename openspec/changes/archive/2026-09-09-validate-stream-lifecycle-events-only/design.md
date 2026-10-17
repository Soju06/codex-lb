# Design — validate-stream-lifecycle-events-only

## Context

py-spy GIL profiles attribute ~3% of proxy CPU to `validate_python` on the
stream hot path, plus a second full validation per websocket frame inside the
parallel-tool-call rewrite and redundant JSON extract+parse in the core client
and websocket relay. Every consumer of the validated `OpenAIEvent` model reads
it only on lifecycle frames; delta frames need only the `type` string.

## Goals / Non-Goals

**Goals:** pydantic validation only on lifecycle frames; one `json.loads` per
frame per owning layer; byte-identical SSE output; unchanged settlement,
error-classification, and terminal-detection semantics.

**Non-Goals:** verbatim relay of unmodified SSE delta frames (the
`format_sse_event` canonical re-encode in the streaming mixin stays — that is
the separate `relay-unmodified-sse-frames-verbatim` follow-up); touching the
chat/completions bridge (genuine cross-dialect translation keeps full
parsing); the `/v1` public normalizer (independent per-chunk consumer); the
cold rewrite/prewarm paths (`limit_warmup`, `quota_planner`,
`http_bridge/helpers`, `websocket/helpers` rewrite builders), which run once
per stream or per rewrite.

## Decisions

- **Lifecycle set = {response.created, response.completed,
  response.incomplete, response.failed, error}.** Terminal frames carry the
  usage and error fields settlement needs; `response.created` is included
  because the websocket path assigns the upstream response id from the
  validated model there.
- **Classify-then-validate.** `classify_event_type(payload)` mirrors the dict
  branch of `_event_type_from_payload` exactly (string `type` wins; a typeless
  dict `error` classifies as `"error"`). Ordering classification before
  validation is equivalence-preserving: when validation succeeds,
  `event.type == payload["type"]`; when it fails (e.g. typeless error
  payloads, or a lifecycle `type` with a non-dict `response` — `OpenAIEvent`
  has no before-validator on `response`), today's code already fell back to
  the same dict branch with `event=None`.
- **`event=None` for non-lifecycle frames compiles against existing
  structure.** Every downstream read of `event.response`/`event.error` in the
  streaming mixin, websocket finalization, and bridge settlement is gated on a
  terminal `event_type`, so non-lifecycle `None` never reaches them.
- **Core-client deletion is a pure no-op.** At the two SSE loops the
  `elif normalized_event_type` branch performed the identical terminal check
  from the same payload the deleted `parse_sse_event` re-parsed; whenever the
  model validated, its `type` equalled `normalized_event_type`. The websocket
  receive loops now use the payload `type` string directly, matching the
  dict semantics the SSE loops already had (a malformed lifecycle frame that
  failed whole-event validation now counts for terminal detection there, as
  it already did on the SSE loops).
- **Rewrite helpers no longer validate on the unchanged path.** The
  `event is None` fallback in `rewrite_parallel_tool_call_text/_sse_line` was
  the second per-frame validation on the websocket path; callers now own
  lifecycle-gated validation and the helpers classify from the dict. The
  changed path (actual dedupe rewrite of `response.output_item.done`) still
  validates the rewritten payload as before.
- **Websocket identity skip.** `_rewrite_websocket_downstream_response_id`
  returns the same object when no replay rewrite applies; only then is the
  per-frame `json.dumps` skipped and the upstream text relayed unchanged.
  Frames without a matched request state were already relayed verbatim, so
  downstream clients already accept upstream-encoded frames on this surface.

## Risks / Trade-offs

- [Whitespace-padded response ids] Non-lifecycle frames that carry a
  `response` object (`response.in_progress`) now resolve their response id via
  the dict fallback, which strips whitespace, where the model path did not.
  Upstream ids are never whitespace-padded; `response.created` (the id
  assignment point) keeps the model path.
- [Usage on delta frames] If upstream ever emitted `usage` on non-lifecycle
  frames it would no longer be validated — accepted limitation; today usage
  appears only on `response.completed`/`response.incomplete`, and no consumer
  reads usage outside terminal branches.
- [Websocket byte relaxation] Identity frames relay upstream JSON text
  (raw UTF-8, upstream spacing) instead of the canonical
  `ensure_ascii` re-encode. JSON-semantically identical, and consistent with
  the existing unmatched-frame behavior on the same socket.
- [First-frame response-id capture] A stream whose first frame is a
  non-lifecycle `response`-carrying frame (never observed; upstream always
  opens with `response.created`) no longer captures `settlement.response_id`
  from that frame; it is still captured at the terminal frame.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
