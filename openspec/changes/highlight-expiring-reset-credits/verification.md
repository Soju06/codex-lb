# Verification

Base: upstream `f8ffbac2099a113fba54dfd8d77774f5bca80ffa` (`main`).

- Account list expiry, account list, item and page suites: **56 passed**.
- Includes exact 72-hour boundary, elapsed/missing/invalid expiry, zero credits, both visibility controls, time advancing without refetch, refreshed data, and timer cleanup.
- Frontend ESLint, TypeScript and Vite build: passed.
- Strict change validation: passed. `openspec validate --specs --strict`: **67 passed, 0 failed**.
- Simplicity budgets and `git diff --check`: passed.
- Desktop (1440px) and mobile (390px) before/after screenshots captured with Playwright using synthetic data; after screenshots checked for horizontal page overflow. Images are in `evidence/`.

## Scope and limits

Only the relevant local CI subset was run; the full repository CI matrix and cloud review remain merge gates. No deployment or migration changes. Screenshots use API fixtures; backend catalog behavior is checked separately by API integration tests for the image-picker change. The change stays active until upstream merge, following maintainer guidance on #2065.
