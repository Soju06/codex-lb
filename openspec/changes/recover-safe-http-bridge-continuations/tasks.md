## 1. Safe recovery

- [x] Gate fresh-upstream replay on the existing retry-safe full-resend proof,
  independent of whether the anchor was client-provided or injected.
- [x] Fail continuity-bound requests closed instead of waiting through an
  unusable retry-circuit cooldown.

## 2. Verification

- [x] Cover proof-gated replay and unsafe/session-bound replay behavior.
- [x] Cover continuity-bound classification separately from ordinary requests.
- [x] Run syntax, architecture, and whitespace validation.
