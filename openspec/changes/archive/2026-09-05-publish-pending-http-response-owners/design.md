## Context

See [proposal.md](proposal.md). `_stream_once` parses only `_LIFECYCLE_EVENT_TYPES` before publishing an owner. The existing JSON adapter already preserves queued/in-progress upstream IDs, and settlement accepts those acknowledgements. The parser omits both event types. The later-event fast path also needs to retain queued frames for parsing.

## Goals / Non-Goals

**Goals:** Feed supported pending lifecycle events through the existing validated extraction and publication path.

**Non-Goals:** Change the cache, resolver, authorization rules, persistence ownership, or provider readiness semantics.

## Decisions

- Add queued/in-progress to the shared lifecycle classifier and queued to the HTTP must-parse set. Reusing these classifications avoids a second ownership-only parser. The shared classifier also serves WebSocket and HTTP bridge parsing; run their existing suites for that bounded blast radius.
- Extend the existing socket-based owner regression with an in-progress event after a delta and both canonical background JSON statuses. Keep real selection, upstream adaptation, collection, and persistence, delaying only the owning log seam. Do not repeat the fixture or substitute the resolver.
- Keep the established local-event and local-ID exclusions. Their existing route cases run with the expanded cases.

## Risks / Trade-offs

Shared lifecycle parsing can expose additional validated response fields to WebSocket and bridge consumers. Their terminal decisions use explicit event types; existing parsing and bridge suites verify that classification does not turn pending events into terminal events.

The upstream may reject a continuation of unfinished work. The proxy preserves that result after resolving its known account.
