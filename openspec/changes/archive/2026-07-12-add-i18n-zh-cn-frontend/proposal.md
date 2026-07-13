## Why

The dashboard ships English-only. To welcome non-English contributors and operators, we need a runtime language switch and a Simplified Chinese (`zh-CN`) translation. Today every visible string is hard-coded inline in JSX, which makes both translation and per-deployment language overrides impossible without forking.

This change introduces the **i18n foundation** (`i18next` + `react-i18next` with browser language detection), a language switcher in the app header, and migrates the **authentication surface** (login, TOTP, bootstrap setup screen, app header chrome, status bar labels) end-to-end. The rest of the feature areas (`accounts`, `api-keys`, `apis`, `dashboard`, `firewall`, `settings`, `sticky-sessions`) intentionally stay English in this PR and will be migrated in follow-up PRs of similar scope.

## What Changes

- Add `i18next`, `react-i18next`, and `i18next-browser-languagedetector` to the frontend dependencies.
- Add `frontend/src/i18n/index.ts` that initialises `i18next` with `en` and `zh-CN` resources, persists the user's choice in `localStorage` under `codex-lb-language`, and falls back to `en` when the detected language is unsupported.
- Add `frontend/src/i18n/locales/en.json` and `frontend/src/i18n/locales/zh-CN.json` with the keys needed for the in-scope surface.
- Wire `i18next` initialisation through `main.tsx` so it runs before React hydration.
- Add a language switcher control to `AppHeader` (desktop and mobile menu) that toggles between **English** and **简体中文**.
- Migrate the in-scope components — `AppHeader`, `StatusBar`, `AuthGate`, `LoginForm`, `TotpDialog`, `BootstrapSetupScreen` — to read text via `useTranslation`. `StatusBar`'s routing-strategy label is intentionally left English in this PR because translating it requires touching the `settings` module.
- Out of scope (deferred): all `features/**` components beyond `auth`, `utils/formatters.ts` strings such as `"in 5m"` / `"Cached"` / `"Missing"`, and zod schema error messages. They keep their existing English copy until follow-up PRs migrate them feature-by-feature.

## Impact

- New runtime dependency (~50KB gzipped for `i18next` + `react-i18next`).
- A user whose browser language matches `zh*` will see the in-scope surface in Simplified Chinese on first load. All other languages keep the existing English experience byte-for-byte.
- The chosen language is persisted in `localStorage` under `codex-lb-language`. Clearing site data resets to detection.
- Until the follow-up PRs land, users who pick Simplified Chinese will see a partially-translated UI: the auth surface, header, and the small set of status-bar labels are translated; everything else stays English. This is documented in `frontend-architecture` so reviewers understand it is intentional, not an oversight.
- No backend changes. Server-emitted error strings keep their English content; user-visible mapping of those errors will be addressed alongside the relevant feature migration.
