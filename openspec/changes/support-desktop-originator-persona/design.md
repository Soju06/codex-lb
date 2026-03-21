## Overview

This change adds the smallest safe surface needed to support a Desktop-like Codex persona without changing account identity semantics.

## Decisions

### Expand native originator detection

`refs/codex` treats `codex_atlas` and `codex_chatgpt_desktop` as first-party chat originators. `codex-lb` should classify those the same way it already classifies `codex_cli_rs` and `Codex Desktop` for auto websocket transport selection.

### Make OAuth authorize originator configurable

The browser OAuth flow currently hardcodes `originator=codex_cli_rs`. A dedicated `oauth_originator` setting keeps the CLI default while letting operators intentionally request `codex_chatgpt_desktop` when they want the upstream auth flow to align with a Desktop persona.

### Preserve auth/account semantics

This change does not rewrite bearer tokens, account ids, or request-session headers. It only widens native-originator recognition and lets operators select the authorize originator explicitly.
