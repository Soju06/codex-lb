## Why

The dashboard fronts every capability at once: six top-level nav destinations and a thirteen-section Settings page. A first-run operator who only needs to import accounts, hand out an API key, and point a client at the proxy has to scan power-user surfaces (automations scheduling, routing tuning, upstream proxy pools, model sources, firewall, quota phase planner, sticky-session administration) before finding the basics. PRINCIPLES.md P3 calls for progressive disclosure: core workflows stay one glance away, power features move behind an explicit "Advanced" boundary.

## What Changes

- Header navigation splits into core destinations (Dashboard, Reports, Accounts, APIs, Settings) rendered as top-level pills, and advanced destinations (Automations) grouped behind an "Advanced" dropdown on desktop and an "Advanced"-labeled group in the mobile menu. Routes are unchanged: `/automations` deep links keep working.
- The Settings page keeps core sections flat (Appearance, Import, Guest Access, Password, Session, TOTP, API Keys) and wraps advanced sections (Routing, Upstream Proxy, Model Sources, Firewall, Quota Planner, Sticky Sessions) in a collapsed-by-default Advanced settings group that expands in one interaction.
- Advanced settings sections are unmounted while the group is collapsed, so their data queries (firewall entries, quota planner, sticky sessions, model sources) fire on expand instead of on page load — an intentional behavior change.
- New `AdvancedSettingsGroup` component and a shadcn-style `collapsible` UI wrapper over the existing `radix-ui` package (no new dependency); `nav.advanced` and `settings.advanced.*` i18n keys in both locales.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Settings page gains a collapsed-by-default Advanced group; header navigation gains a progressive-disclosure requirement (core items top-level, Automations under Advanced, direct routes preserved).

## Impact

- Code: `frontend/src/components/layout/app-header.tsx`, `frontend/src/features/settings/components/settings-page.tsx`, `frontend/src/features/settings/components/advanced-settings-group.tsx` (new), `frontend/src/components/ui/collapsible.tsx` (new), `frontend/src/i18n/locales/en.json`, `frontend/src/i18n/locales/zh-CN.json`
- Tests: `frontend/src/components/layout/app-header.test.tsx`, `frontend/src/features/settings/components/settings-page.test.tsx`, `frontend/src/__integration__/automations-flow.test.tsx`, `frontend/src/__integration__/firewall-flow.test.tsx`
- Specs: `openspec/specs/frontend-architecture/spec.md`
