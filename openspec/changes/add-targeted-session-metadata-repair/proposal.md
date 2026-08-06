## Why

After a provider change and a Codex update, a session can retain conflicting `openai` and `codex-lb` tags between its JSONL metadata and SQLite thread index. The session is then absent from the active provider's list even though its conversation data is intact.

## What Changes

- Add a ProviderSwitcher action that previews session metadata tag mismatches for the currently active provider.
- Allow an explicitly confirmed repair to retag only the identified mismatched session metadata; do not run a whole-home retag or change the selected provider.
- Preserve the existing backup, quiescence, and targeted verification guarantees for any repaired metadata.

## Capabilities

### New Capabilities

- `targeted-session-metadata-repair`: Detect and reconcile provider-tag mismatches between Codex session JSONL metadata and its SQLite thread index for individual sessions.

### Modified Capabilities

- None.

## Impact

- `app/codex_sessions_retag.py` and `app/cli.py` gain a session-ID-scoped metadata repair interface.
- ProviderSwitcher gains a compact preview-and-confirm action and its process adapter gains typed mismatch results.
- Targeted Python and ProviderSwitcher regression coverage verifies that normal sessions and unrelated sessions are unchanged.
