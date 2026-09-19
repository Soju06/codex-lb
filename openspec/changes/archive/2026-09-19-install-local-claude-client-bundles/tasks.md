## Implementation

- [x] Package clients, sibling helpers, policy, and installer locally before activation.
- [x] Preserve preview, backups, repeat installation, and managed-only uninstall.
- [x] Add source-removal and replacement-failure regression coverage.
- [x] Update runtime-portability spec and context.
- [x] Check working-directory access and bound process file-descriptor limits before Claude startup.

## Validation

- [x] Run focused installer tests and Bash syntax validation.
- [x] Run strict OpenSpec validation for the change and runtime-portability spec.

## Evidence

- Installer regression suite: 17 passed, including installed commands and bundled
  uninstall after source removal, repeated installs, version retention, and backup conflicts.
- Launcher/startup suite: coordinator reports 94 passed and live `cc` returned
  `READY_OK` with exit 0 in 3.4 seconds.
- Bash syntax, targeted Ruff, and `git diff --check` passed.
- Strict OpenSpec validation passed for this change and runtime-portability.
  Repository-wide strict validation ran and reported 35 passed, 2 failed.
  Existing `anthropic-messages-compat` and `oauth-refresh-safety` requirements
  lack scenario headers; these unrelated specs were not changed.
