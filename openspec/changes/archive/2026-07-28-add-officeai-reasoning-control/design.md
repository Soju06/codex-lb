# Design: OfficeAI reasoning-effort control

## Context

OfficeAI's local API-key request model has a boolean thinking capability but no
reasoning-effort field. Its configured endpoint already targets the local
codex-lb `/v1` API, whose Chat Completions adapter supports reasoning effort.

## Decisions

1. The proxy owns request shaping; the protected OfficeAI assemblies remain
   untouched.
2. The feature is opt-in through an `officeai-reasoning.json` control file
   beside the active SQLite database. This avoids adding another global
   `CODEX_LB_*` setting for one local desktop integration.
3. The control file contains `enabled`, `effort`, and an optional API-key
   prefix. The prefix prevents unrelated API keys from inheriting the local
   choice.
4. The override runs only on `/v1/chat/completions` and only when none of
   `reasoning`, `reasoning_effort`, or `reasoningEffort` explicitly supplies an
   effort. A boolean `enable_thinking` remains eligible for a concrete effort
   override.
5. `maximum` selects the last advertised model effort after converting the
   client-only `ultra` level to wire-safe `max`. Unknown models fall back to
   `high`.
6. Existing API-key enforcement runs after this override and therefore retains
   final authority.
7. Missing, malformed, or unreadable files fail open and leave the request
   unchanged.

## Local UI

The Windows Forms control is independent from WPS process injection. It tracks
the visible `wps.exe` top-level window, stays near the bottom-right corner, and
writes the control file atomically. The default selection is `maximum`.

## Risks

- Other callers sharing the same API key and Chat Completions route can inherit
  the selection. API-key prefix scoping and the narrow route reduce this risk;
  a dedicated OfficeAI key remains the cleanest future isolation.
- WPS window geometry can vary by version and DPI. Positioning is best-effort
  and the bar keeps its last position when no visible WPS window is available.
