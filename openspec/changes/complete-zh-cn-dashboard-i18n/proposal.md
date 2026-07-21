## Why

The dashboard i18n foundation ships a `zh-CN` locale bundle with complete key
coverage, but 101 entries still hold English copy, so Chinese operators hit
mixed-language surfaces across Accounts, Automations, Dashboard, Firewall,
Model Sources, Quota Planner, Sticky Sessions, Upstream Proxy, and shared
components.

## What Changes

- Translate the remaining 101 untranslated `zh-CN` entries to Simplified
  Chinese.
- Unify terminology with existing `zh-CN` translations (e.g. render
  `accountBurnProjection` as 账户消耗预测, matching the settings appearance
  section).
- Translate the Automations trigger filter label and runs column header so
  mixed-label groups (状态 / 类型 / 触发方式) render fully in Chinese.
- Keep protocol names, product names, model/API terms, and compact operational
  abbreviations in English where translating them would read less naturally,
  matching the `ko` locale's convention (OAuth, TOTP, Model, API Key,
  Credits, Quota, etc.).

## Impact

- Frontend-only copy change.
- Existing English copy remains the default and the fallback locale.
- No server API, database schema, or proxy behavior changes.
