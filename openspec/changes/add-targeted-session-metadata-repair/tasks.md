## 1. Targeted metadata interface

- [x] 1.1 Add typed mismatch preview and session-ID-scoped retag support to the Codex session metadata module.
- [x] 1.2 Expose read-only preview and confirmed repair subcommands without altering existing whole-home retag behavior.

## 2. ProviderSwitcher action

- [x] 2.1 Add typed process results and a quiescence-guarded preview/apply adapter for targeted metadata repair.
- [x] 2.2 Add a compact UI action that displays candidate IDs and requires confirmation before repair.

## 3. Focused verification and package

- [x] 3.1 Add focused Python and ProviderSwitcher regression cases for a SQLite-only mismatch and unrelated-session preservation.
- [x] 3.2 Run targeted tests, strict OpenSpec validation, publish the local beta artifact, and run its isolated self-test.
