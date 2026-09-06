## Context

See [proposal.md](proposal.md) for motivation. The existing panel already owns platform selection and cancels stale installer fetches. Copy actions intentionally export the current key while previews mask it.

## Goals / Non-Goals

**Goals:** Improve scanning and action hierarchy using the existing design tokens, icons, native inputs, and copy component.

**Non-Goals:** Changing Overview, generated commands, authentication, client installation, or API behavior; adding dependencies or navigation items.

## Decisions

- Use numbered platform/command steps and a separate file-export card instead of adding nested tabs. This keeps all setup methods discoverable without extra interaction state.
- Use a two-column desktop layout with guidance in a sidebar; stack on mobile. A dark terminal surface distinguishes commands in either theme.
- Keep native radio inputs and explicit labels, visible focus, and a check indicator for selection rather than relying only on color.
- Preserve fetch ownership and reuse existing copy/download actions. Style the copy action locally so other dashboard copy buttons remain unchanged.

## Risks / Trade-offs

- Long commands or translated copy can overflow → use minimum-width-zero grid items, wrapping command text, and a bounded script preview; check narrow viewports and all locales.
- Concise guidance can hide important caveats → keep prerequisite, replacement, restart, remote-environment, and key-export warnings visible outside collapsed previews.
- UI snapshots use synthetic data → retain real route-level mocked integration tests for authentication and export behavior, and do not claim native installer verification from screenshots.
