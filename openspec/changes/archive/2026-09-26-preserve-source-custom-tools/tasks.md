## 1. Capability repair

- [x] 1.1 Add the existing explicit custom-tool opt-in to affected live model sources through the service; verify the running filter retains exec and apply_patch.
- [x] 1.2 Add route regression tests for inferred and explicit custom support, plain-source filtering, grammar, tool choices, and custom call SSE on both routes and trailing slashes; demonstrate the code-mode case fails before the fix.
- [x] 1.3 Infer custom support from declared code-mode/freeform capabilities in the shared resolver; verify the route regressions and related capability/overflow tests pass.

## 2. Verification and documentation

- [x] 2.1 Verify ch/linxaq reads and writes a marker in a disposable Codex CLI workspace using actual tools.
- [x] 2.2 Sync requirements and stable operational context, run lint and strict OpenSpec validation, record verification, and archive the verified change.
