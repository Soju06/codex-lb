## Context

See [proposal.md](proposal.md) for motivation. The dashboard already loads flat
locale JSON bundles synchronously through i18next and shares one locale registry
between its detector and desktop/mobile menus. Intl formatters currently map only
English, Chinese, and Korean; the expiry calendar uses English defaults.

## Goals / Non-Goals

Keep the existing registry, persistence key, detection precedence, and fallback.
Translate frontend-owned copy without changing technical identifiers, backend
error payloads, user content, timezone selection, or API serialization.

## Decisions

- Add `ja.json` alongside existing resources instead of introducing a second
  translation system or server preference. Use native `日本語` in every menu.
- Match Japanese base tags in the existing normalizer and map Japanese to
  `ja-JP` in Intl formatters. Keep compact quantities and currency invariant.
- Use the already installed calendar/date locale support for Japanese; preserve
  explicit caller options. Cover accessible calendar labels as well as pixels.
- Keep complete key and placeholder parity, including existing plural variants
  even though Japanese does not distinguish singular and plural.
- Document usage in the existing Configuration page with a link to the owning
  OpenSpec capability. Sync durable rationale to its context document.

## Risks / Trade-offs

- Translation drift or damaged interpolation → compare all keys, variables,
  and inline tags against English and test representative interpolated UI.
- Dense labels may wrap differently → inspect desktop and narrow mobile pages
  with fixture data and retain before/after screenshots.
- Calendar locale support may not translate accessible labels automatically →
  inspect rendered accessible names and cover the expiry picker interaction.
- Eagerly loaded translations increase the entry bundle → use the established
  resource loading approach and verify the production build.

## Migration Plan

Ship with the regular frontend build. No data or settings migration is needed.
Existing language preferences continue to work; unsupported preferences still
fall back to English if this addition is reverted.
