# Replay: dashboard refresh-rate control

- Parent goal: smooth Codex account consumption and quota presentation in the
  codex-lb dashboard without changing routing inputs.
- Capability proved: the Appearance settings expose a persisted 5, 15, 30, or
  60 second dashboard refresh cadence with 15 seconds selected by default.
- Source: `codex/fix-consumption-smoothing` from upstream main `b311aea`.
- Build: Vite production output from the branch worktree.
- Environment: Playwright fixture data, Chromium, 1440 x 900 viewport at 2x
  device scale, full-page capture.
- Readiness: frontend dependencies installed, production build green, fixture
  API routes active, no real account credentials used.
- Visible sequence: open `/settings`, wait for network idle, confirm the
  Appearance panel is fully visible, locate `Dashboard refresh rate`, confirm
  `15s` is selected, then capture the full page.
- Expected result: `5s`, `15s`, `30s`, and `60s` controls are readable with
  `15s` selected and no clipped or overlapping controls.
- Semantic proof: frontend preference and query-hook tests prove selection,
  persistence, and application to overview and projection polling.
- Stop conditions: abort on login, permission, save, or external-network prompts.
- Recording: still image only, no video or pointer choreography required.
- Artifact: `docs/screenshots/settings.jpg`, SHA-256
  `69B3E5E0910B6517D5947C1A7E39B9FB2FF6725938B31D67DE34D688BC79BC5F`.
- Quality check: full-page image inspected at 2880 x 4800 pixels; the refresh
  row and all four choices are visible, aligned, and readable.
- Cleanup: preview server stopped. No real settings or accounts were mutated.
- Rollback: restore the prior screenshot and remove the preference row.

## Failed attempts excluded from replay

- The screenshot runner could not start its default `bun` command because bun
  is absent from PATH. The final path used the bundled Node runtime and an
  already-built Vite preview server.
