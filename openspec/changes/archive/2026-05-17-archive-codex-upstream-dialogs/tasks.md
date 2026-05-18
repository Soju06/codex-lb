## 1. Implementation

- [x] 1.1 Add opt-in settings for full conversation archival.
- [x] 1.2 Persist upstream request/response payloads and stream events as JSONL.
- [x] 1.3 Redact credential-bearing headers in archive records.
- [x] 1.4 Compress new archive files as gzip JSONL and keep legacy JSONL readable.
- [x] 1.5 Add dashboard APIs and request detail UI for browsing archived records.
- [x] 1.6 Keep archive gzip writes and archive reads off the request event loop.
- [x] 1.7 Bound archive writer memory and preserve non-ASCII text as readable UTF-8.

## 2. Verification

- [x] 2.1 Add unit tests for archive writing, disabled mode, binary frame encoding, bounded queue fallback, UTF-8 text, and archive reading.
- [x] 2.2 Run targeted pytest, ruff, type checks, frontend build, and OpenSpec validation.
