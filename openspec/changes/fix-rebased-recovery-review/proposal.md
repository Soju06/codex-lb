## Why

Root UNKNOWN recovery must apply the same safety checks as reconstructed
transcripts. Explicit upstream errors must survive an interrupted output-item
lifecycle. Rebase validation exposed both missing contracts.

## What Changes

- Validate and sanitize root replay before authorizing UNKNOWN recovery.
- Preserve explicit failure terminals after unfinished output items.
- Add regression coverage for unsafe roots and interrupted error responses.
- Register the branch's existing ten recovery controls with upstream's new T3
  tier map and explicit dashboard migration backlog; preserve their defaults
  and prior privacy/storage/at-least-once rationale in the settings ratchet.
- Align mixed opaque-output tests with the strict lifecycle contract and
  isolate the unrelated startup DB seed in the membership lifecycle test.

## Impact

- Responses compatibility and HTTP bridge recovery only; no new settings.
