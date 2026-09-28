# Design

## Context

See [proposal.md](proposal.md). The bridge owns its scheduler and request-budget
timeouts, while the canonical client already implements SSE separator detection.

## Goals / Non-Goals

Align byte framing and UTF-8 decoding. Preserve existing scheduler ownership and
timeout classification; account selection and reservation settlement are outside
this change.

## Decisions

Reuse `_find_sse_separator` instead of a second delimiter implementation. Use a
bytearray and the canonical overlap cursor to avoid rescanning a growing event.
Track a trailing CR so the next chunk's LF is consumed as its continuation.
Decode only complete events (or final residue), using replacement for malformed
UTF-8. Preserve original event bytes apart from the canonical split-CRLF handling.

Reusing the entire canonical async receiver would replace the bridge's scheduler
and budget semantics, so only its pure separator helper is shared.

## Risks / Trade-offs

A CR at the end of a separator can finish the event before its optional LF
arrives; tests feed one byte at a time to protect against a stray LF entering
the next event. This fixes framing, but does not introduce a new maximum event
size policy for the bridge.
