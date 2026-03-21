## Overview

This change adds the smallest safe surface needed to support a Desktop-like Codex persona without changing account identity semantics.

## Decisions

### Expand native originator detection

`refs/codex` treats `codex_atlas` and `codex_chatgpt_desktop` as first-party chat originators. `codex-lb` should classify those the same way it already classifies `codex_cli_rs` and `Codex Desktop` for auto websocket transport selection.

### Default OAuth authorize originator to the Desktop persona

The browser OAuth flow should default to `codex_chatgpt_desktop` so codex-lb presents the Desktop persona unless an operator explicitly overrides it. A dedicated `oauth_originator` setting still lets operators fall back to `codex_cli_rs` or another Codex originator when they need to compare behavior.

### Preserve auth/account semantics

This change does not rewrite bearer tokens, account ids, or request-session headers. It only widens native-originator recognition and lets operators select the authorize originator explicitly.
