## Why

The dashboard i18n foundation ships a `zh-CN` locale bundle with complete key
coverage, but some entries still hold English copy, so Chinese operators hit
mixed-language surfaces across Accounts, Automations, Dashboard, Firewall,
Model Sources, Quota Planner, Sticky Sessions, Upstream Proxy, and shared
components.

## What Changes

- Translate the remaining untranslated `zh-CN` entries to Simplified Chinese.
- Unify terminology with existing `zh-CN` translations (e.g. consistently
  render `forceProbe` as 强制探测).
- Keep dashboard numeric units stable across locales: compact quantities use
  `K`/`M`/`B`, and USD values use the `$` prefix.
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
