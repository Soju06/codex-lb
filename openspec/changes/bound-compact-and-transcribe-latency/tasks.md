## 1. Compact request budget model

- [x] 1.1 Define a compact request-path budget for selection, refresh, and connect phases
- [x] 1.2 Preserve the current default compact no-read-timeout contract unless an operator explicitly opts into a compact read/total timeout
- [x] 1.3 Add regression coverage for compact budget exhaustion before upstream completion

## 2. Transcription request budget model

- [x] 2.1 Add configurable transcription request-budget semantics instead of relying on hard-coded timeout values
- [x] 2.2 Clamp transcription freshness checks, connect timeout, and retry handling to the remaining budget
- [x] 2.3 Add regression coverage for transcription budget exhaustion and 401 refresh retry behavior under the new budget rules

## 3. Verification

- [x] 3.1 Validate updated OpenSpec requirements for responses compact and audio transcriptions
- [x] 3.2 Verify follow-up implementation preserves existing compact CLI-parity behavior by default
