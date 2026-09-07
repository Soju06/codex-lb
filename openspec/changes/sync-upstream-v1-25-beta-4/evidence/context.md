# Route composition screenshots

Captured on 2026-09-07 with Chromium/Playwright at 1440 × 900, dark theme, English, reduced motion. Before is fork commit `2930dec0`; after is the uncommitted integration of beta.4 `15ccd901`. Both use built frontend assets and synthetic local HTTP fixtures, never production data. The baseline build uses the installed candidate frontend dependency set to isolate source differences. Screenshots show route composition, not live service readiness; operator data requests other than synthetic authentication/version are deliberately unavailable.

- [Before unknown route](before-unknown-route.png): no page recovery content.
- [After unknown route](after-unknown-route.png): not-found screen within the administrator shell, focused recovery heading, dashboard link.
- [Before key entry](before-key-entry.png) and [after key entry](after-key-entry.png): standalone API-key entry; browser assertions recorded zero administrator API requests for both.

The full browser smoke suite separately verifies built assets against the real application and an isolated empty database. React integration tests cover navigation from an unknown administrator route to key entry, password-required unauthenticated key access, valid key data loading, lazy-route failure and keyboard recovery. These images do not replace those behavioral checks.
