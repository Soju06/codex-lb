## 1. Contract

- [x] 1.1 Specify the repository/host orchestration boundary and preserved
  fail-closed behavior.
- [x] 1.2 Specify centralized atomic artifacts and cleanup safety.

## 2. Implementation

- [x] 2.1 Add shared typed artifact I/O and migrate the canary/gates.
- [x] 2.2 Add the repository-owned fast-suite orchestrator with bounded,
  enumerated cleanup and explicit operator paths.
- [x] 2.3 Replace the installed Bash implementation with declarative argv and
  update the runbook.

## 3. Verification

- [x] 3.1 Add artifact and suite tests for success, command failure, path
  rejection, permissions, cleanup, privacy failure, and marker content.
- [x] 3.2 Run traffic/proxy tests, lint, typing, strict OpenSpec validation,
  retained-evidence gates, scheduler dry-run, and a forced smoke suite.
- [x] 3.3 Verify timer activation and archive the completed change.
