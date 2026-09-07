# Install presentation verification

## Assessment

The added presentation requirement and all three scenarios are covered. No implementation blockers or design deviations remain. Overview, command generation, installer fetching/cancellation, credential export, and backend endpoints are unchanged by this polish.

- **Completeness:** Numbered platform/command sections, shell label, filename, file actions, guidance, and security card are implemented in `frontend/src/features/key-dashboard/components/key-install-panel.tsx`. English, Korean, and Chinese labels are present.
- **Correctness:** The route-level integration suite exercises keyboard radio selection, checked/focus state, guidance outside collapsed previews, shell/filename updates, copy/download contents, hidden keys, stale responses, reconnects, and independent authentication failures.
- **Coherence:** Existing React state and cancellation ownership are retained. Native radio inputs, existing icons and copy/download utilities are reused; no global component styles, dependencies, or navigation entries were added. Stable requirements and presentation rationale are synced to `openspec/specs/api-key-dashboard/`.

## Automated checks

Run from `frontend/`:

```sh
node node_modules/vitest/vitest.mjs run src/__integration__/key-dashboard-flow.test.tsx src/features/key-dashboard/install.test.ts src/__integration__/auth-flow.test.tsx src/utils/clipboard.test.ts
node node_modules/@typescript/native/bin/tsc -b
node node_modules/eslint/bin/eslint.js src/features/key-dashboard/components/key-install-panel.tsx src/__integration__/key-dashboard-flow.test.tsx
node node_modules/vite/bin/vite.js build
```

Results: 18 tests across 4 files passed; TypeScript, scoped ESLint, and production build passed. `git diff --check` passed.

Strict validation passed for this change and the `api-key-dashboard` main spec. Repository-wide `openspec validate --specs` remains 58/59 passing: the pre-existing `model-source-routing` spec lacks a required Purpose section. That unrelated spec was not modified.

## Browser checks

Chromium with synthetic key/profile/installer responses, not production credentials:

- Desktop 1440px and mobile 390px in light/dark mode: no page-level horizontal overflow or browser exceptions.
- Windows at 390px with expanded script preview: no page-level overflow and no raw key in rendered text.
- Native keyboard selection moved Windows to Linux; the focused card had a visible ring, and Tab reached Copy command.
- Korean and Chinese at 390px, 768px, and 1024px: no horizontal overflow or untranslated Install keys.
- Screenshot inspection confirmed clear action hierarchy, readable command wrapping, stacked mobile cards, and theme-aware primary button styling.

These checks validate the UI only; they do not execute the generated installer on macOS or Windows.

## Screenshots

- Before: [desktop](screenshots/install-before-light-desktop.png), [mobile](screenshots/install-before-light-mobile.png).
- After: [desktop light](screenshots/install-after-light-desktop.png), [desktop dark](screenshots/install-after-dark-desktop.png), [mobile light](screenshots/install-after-light-mobile.png), [mobile dark](screenshots/install-after-dark-mobile.png), [Windows mobile preview](screenshots/install-after-windows-mobile.png).

## Release boundary

This UI polish is local and uncommitted; it was not included in the earlier HA rollout that completed during this work. No deployment, commit, or push was performed for this change.
