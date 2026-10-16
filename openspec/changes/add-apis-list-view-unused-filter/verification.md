# Verification

Based on upstream main `f8ffbac2`.

- 21 focused tests passed, including read-only users in both layouts, filters, sorting, pagination and dialogs.
- Full frontend ESLint, TypeScript build and production Vite build passed.
- Playwright screenshot/overflow checks passed at 1440px and 390px.
- Strict OpenSpec validation passed for this change and all 67 main specifications.

## Commands

```sh
cd frontend
node node_modules/vitest/vitest.mjs run src/features/apis/components/api-list.test.tsx src/features/apis/components/apis-page.test.tsx src/features/api-keys/components/api-keys-overview.test.tsx
node node_modules/eslint/bin/eslint.js .
node node_modules/@typescript/native/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

OpenSpec: `openspec validate --specs --strict` and `openspec validate add-apis-list-view-unused-filter --strict` (CLI 1.12.0). Screenshots use synthetic fixtures. Cloud CI and maintainer review are separate merge gates; the change remains active pending review.
