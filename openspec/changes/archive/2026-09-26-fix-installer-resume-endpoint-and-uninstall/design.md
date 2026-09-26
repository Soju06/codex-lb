## Context

The installer chooses `codex-lb` and writes only its provider endpoint. Root `openai_base_url` also needs to point to the load balancer for resumed built-in-provider chats. Existing installers retain manual backup directories but have no uninstall command.

## Goals / Non-Goals

**Goals:** Route old built-in-provider chats through the configured endpoint/key, support repeat installs, and undo all owned client files offline without touching history.

**Non-Goals:** Rewrite session records, globally change environment variables, override administrator policy or explicit CLI flags, migrate arbitrary custom providers, or rewrite project files.

## Decisions

- Write `openai_base_url` at the root alongside the existing custom provider. The installed Codex 0.157.1 was exercised against a local HTTP server: explicit config beats inherited `OPENAI_BASE_URL` and file-backed auth supplies the new key despite an old `OPENAI_API_KEY`. Avoid persistent environment edits that would affect other OpenAI clients.
- Follow current official Codex semantics: project-local provider/endpoint keys are ignored. Keep projects intact; explicit user profiles/CLI overrides and older clients may still require manual review.
- Save a credential-free offline `codex-lb-uninstall.sh` or `.ps1` in the selected Codex home. UI shows a copyable command, and install output prints the path. The uninstaller anchors itself to its own directory, so later CODEX_HOME changes cannot restore the wrong home.
- A versioned `.codex-lb-install-state.json` stores the original backup directory and SHA-256/absence for config, auth, catalog and any pre-existing local uninstaller. Repeat installs retain this baseline and continue creating per-run snapshots. The first install with this version establishes the baseline; older untracked backups are never guessed.
- Validate state shape, fixed file names, directory confinement, target type and original backup hashes before install/uninstall mutations. Stage all replacements, make a private snapshot of current files on uninstall, restore originally present files and remove originally absent files. Delete lifecycle state last, leaving backups and chat history intact. Missing state is a safe no-op; invalid state fails before replacement.
- Unix uses the existing Python 3 prerequisite; Windows uses built-in PowerShell and the current private ACL pattern. Uninstall has no network access and embeds no API key.

## Risks / Trade-offs

- Multi-file replacement can fail partway -> retain original state/backups until restoration completes, plus a current-state recovery snapshot.
- Users may edit config after installation -> preserve that state in the uninstall snapshot before restoring the original baseline.
- Selected profiles or explicit command-line provider overrides remain higher-priority -> explain the boundary and test the normal resumed built-in-provider path with the actual CLI.
- Very old or custom-provider sessions may not use the built-in provider -> do not claim universal session migration; preserve all history and provider identity records.

## Sources

- https://learn.chatgpt.com/docs/config-file/config-reference — root `openai_base_url`, project-local exclusions.
- https://learn.chatgpt.com/docs/config-file/config-advanced — built-in provider redirection and inability to override reserved provider IDs.
