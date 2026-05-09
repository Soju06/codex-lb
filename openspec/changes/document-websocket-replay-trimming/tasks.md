## 1. Specification

- [x] 1.1 Add Responses websocket replay-trimming contract to OpenSpec.
- [x] 1.2 Capture safe fresh replay limits for injected continuity anchors.

## 2. Implementation

- [x] 2.1 Trim leading previous-response output items before forwarding anchored websocket full replay payloads.
- [x] 2.2 Preserve original input fingerprints/counts for continuity metadata before trimming request payloads.
- [x] 2.3 Wait for pending continuity metadata before using proxy-injected anchors.
- [x] 2.4 Bound downstream tool-call duplicate suppression state.

## 3. Verification

- [x] 3.1 Add targeted replay, continuity, and duplicate-tool-call regression tests.
- [x] 3.2 Run focused proxy utility tests and static checks.
