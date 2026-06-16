## Why

The previous change (`add-i18n-zh-cn-frontend`) introduced the i18n foundation and translated the auth surface, leaving feature pages still hard-coded to English. The Settings page is the second-most-trafficked surface after the login flow and surfaces every dashboard configuration knob (routing, appearance, password, TOTP, sticky sessions, firewall, API keys), so leaving it untranslated negates much of the value of the foundation. This change migrates the Settings page itself.

## What Changes

- Extend `frontend/src/i18n/locales/{en,zh-CN}.json` with the `settings.*` namespace (page chrome, appearance, routing, import, session, password, TOTP), plus a small `common.cancel` key shared by every settings dialog.
- Migrate `SettingsPage`, `AppearanceSettings`, `RoutingSettings`, `ImportSettings`, `SessionSettings`, `PasswordSettings`, and `TotpSettings` to read all visible text via `useTranslation`, including dialog titles, descriptions, button labels, switch labels, status messages, validation/long-session warnings, and toast strings.
- The session-lifetime "Enter a whole number of hours" warning is rendered with `<Trans>` so the embedded `<code>1.5</code>` example survives translation.
- The TOTP code-length zod validator stores an i18n key (`settings.totp.validation.codeLength`) instead of the literal English message; both setup and disable forms resolve the key through `t()` when rendering `FormMessage`. This keeps the schema decoupled from the i18n instance while still presenting the right string per locale.
- Out of scope (deferred to follow-up PRs): the `ApiKeysSection`, `FirewallSection`, and `StickySessionsSection` rendered inside Settings — these belong to other capabilities and will be migrated in their own PRs to keep the diff focused. The dynamic routing-strategy label rendered by `StatusBar` (`getRoutingLabel`) is also still English; it will move once the routing label generator is refactored.

## Impact

- Users who select Simplified Chinese now see the Settings page entirely in Chinese (except for the deferred sections noted above).
- Existing tests continue to assert against English copy and keep passing because `en` remains the resolved default in the test environment.
- No new runtime dependencies; the change reuses `i18next` and `react-i18next` already added in the foundation PR.
- The TOTP zod schema's error message becomes a translation key. Any consumer that reads `error.message` directly (without `t()`) would get the key string. Inside this codebase only `FormMessage` consumes it, and both call sites have been updated.
- No backend changes.
