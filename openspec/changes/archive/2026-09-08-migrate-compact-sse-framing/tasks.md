## 1. Contracts

- [x] 1.1 Specify compact content-type selection, optional total deadlines, ownership, and cleanup.
- [x] 1.2 Add real-worker regressions proving compact skips Python framing and keeps JSON/error compatibility.

## 2. Implementation

- [x] 2.1 Extend the negotiated native transport for content-type-aware framing and optional total timeouts.
- [x] 2.2 Move direct and routed compact SSE consumption to the native framer with owned response/session cleanup.
- [x] 2.3 Preserve missing-helper fallback, route metadata, and failure/replay provenance.

## 3. Verification

- [x] 3.1 Verify terminal completion without EOF, partial activity, idle/total/size failures, and cancellation isolation.
- [x] 3.2 Run focused Python/Rust tests, lint/types, architecture checks, and strict OpenSpec validation.
- [x] 3.3 Update architecture docs, synchronize specifications, archive verified changes, and back up source changes to NAS.
