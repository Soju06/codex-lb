## Context

Clipboard interactions are used from multiple UI flows, including OAuth authorization URL copy and API key post-create copy. Existing copy behavior relied on `navigator.clipboard.writeText`, which can fail in non-secure contexts and within dialog focus management. The current implementation work introduces a shared utility plus dialog-aware fallback handling and associated component wiring.

## Goals / Non-Goals

**Goals:**
- Make copy actions reliable in both secure and non-secure browser contexts.
- Ensure fallback copy works inside dialog focus scopes used by the UI library.
- Keep copy behavior centralized in a shared utility to avoid duplicated browser-compat logic.
- Add regression tests that cover secure, fallback, and dialog-scoped fallback paths.

**Non-Goals:**
- Redesign dialog visual behavior or keyboard-focus policy beyond copy-trigger handling.
- Replace `document.execCommand("copy")` with a new polyfill package.
- Change backend APIs or OAuth/API-key payload contracts.

## Decisions

- **Decision: Introduce a shared clipboard utility with optional container.**
  - **Why:** A shared utility keeps browser branching in one place and avoids fragmented fallback logic.
  - **Alternative considered:** Per-component inline fallback logic. Rejected due to duplication and inconsistent behavior risk.

- **Decision: Accept a caller-provided fallback container.**
  - **Why:** Dialogs use focus scopes; mounting fallback textarea under `document.body` can fail to focus in those scopes.
  - **Alternative considered:** Utility auto-discovery via hardcoded selectors. Rejected as brittle and framework-coupled.

- **Decision: Pass dialog container from copy-trigger context.**
  - **Why:** The triggering button already has stable locality (`closest("[role='dialog']")`), enabling explicit and testable behavior.
  - **Alternative considered:** Global activeElement heuristics inside utility. Rejected due to implicit coupling and reduced clarity.

- **Decision: Add pointer-focus safeguards on copy buttons.**
  - **Why:** Prevent sticky focus states on copy triggers after pointer interactions in dialog-heavy flows.
  - **Alternative considered:** Blur-only after async copy completion. Rejected as insufficient in some focus-managed contexts.

## Risks / Trade-offs

- **[Risk] Container detection at call sites can drift across components** -> **Mitigation:** Keep shared `CopyButton` responsible for dialog container forwarding and cover with tests.
- **[Risk] `execCommand("copy")` remains legacy behavior** -> **Mitigation:** Keep it as the compatibility path while still invoking modern Clipboard API when available.
- **[Risk] Pointer-specific focus handling could affect accessibility expectations** -> **Mitigation:** Limit behavior to pointer event path and keep keyboard-triggered copy semantics intact.

## Migration Plan

1. Add `copyToClipboard` utility with secure-path and fallback-path support plus optional container parameter.
2. Update shared `CopyButton` and OAuth dialog copy action to pass dialog container context.
3. Add/expand tests for utility fallback and dialog container behavior.
4. Run targeted frontend tests and typecheck.
5. No data migration or backend rollout steps required.

## Open Questions

- None.
