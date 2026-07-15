## 1. Header navigation

- [x] 1.1 Split `NAV_ITEMS` into `CORE_NAV_ITEMS` (dashboard, reports, accounts, apis, settings) and `ADVANCED_NAV_ITEMS` (automations) in `app-header.tsx`.
- [x] 1.2 Desktop: render an "Advanced" dropdown after the core pills using the existing dropdown-menu primitive; mark the trigger active when an advanced route is current.
- [x] 1.3 Mobile sheet: render core items, then a divider and an "Advanced" labeled group with the advanced items; keep the accounts reset-credits badge on the core Accounts item.
- [x] 1.4 Coverage: core items render as top-level links; Automations is not a top-level link; opening Advanced reveals Automations and navigates; trigger active-state follows the route; `/automations` deep link still renders.

## 2. Settings page advanced group

- [x] 2.1 Add a shadcn-style `collapsible` wrapper over the installed `radix-ui` Collapsible primitive.
- [x] 2.2 Add `AdvancedSettingsGroup` (collapsed by default, children unmounted while closed so advanced queries fire on expand).
- [x] 2.3 Wrap Routing, Upstream Proxy, Model Sources, Firewall, Quota Planner, and Sticky Sessions sections in the group; keep Appearance, Import, Guest Access, Password, Session, TOTP, and API Keys flat; update the page subtitle copy.
- [x] 2.4 Coverage: advanced sections do not mount initially; one expand interaction mounts all of them; read-only guest disabling still reaches expanded advanced sections; firewall flow expands the group before exercising add/remove; legacy `/firewall` redirect still lands on `/settings`.

## 3. i18n

- [x] 3.1 Add `nav.advanced` and `settings.advanced.{title,description,show,hide}` to `en.json` and `zh-CN.json`.

## 4. Validation

- [x] 4.1 Run frontend lint, typecheck, tests, and build; `openspec validate dashboard-progressive-disclosure --strict` and `openspec validate --specs`.
