# Proposal

## Why

The owner-forward HTTP bridge only recognizes LF blank lines and strictly
decodes UTF-8. CRLF/CR events accumulate until EOF, and malformed UTF-8 aborts
the relay instead of matching the canonical Responses receiver.

## What Changes

- Reuse the canonical CR/LF separator detection for owner-forward events.
- Replace invalid UTF-8 when decoding complete events and trailing bytes.
- Cover incremental delivery and terminal event parsing through the bridge client.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: owner-forward SSE framing and decoding compatibility.

## Impact

The internal bridge receiver and its regression tests. No account selection,
reservation settlement, schema, settings, or public route changes.
