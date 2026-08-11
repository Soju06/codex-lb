## 1. Zustand store with localStorage persistence

- [x] 1.1 Create `frontend/src/hooks/use-date-format.ts` with `DateDisplayFormat` type (`"default"` | `"iso8601"`), Zustand store, and `getDateDisplayFormat()` accessor
- [x] 1.2 Persist preference to localStorage under key `codex-lb-date-display-format`

## 2. Date formatting logic

- [x] 2.1 Import `getDateDisplayFormat` into `formatters.ts`
- [x] 2.2 Add ISO date/time helper functions (`formatISODate`, `formatISOTime`)
- [x] 2.3 Add ISO 8601 branch in `formatTimeLong`: when active, return semantic `{ time: "HH:MM:SS", date: "YYYY-MM-DD" }` fields

## 3. Appearance settings UI

- [x] 3.1 Add `DATE_FORMAT_OPTIONS` constant (Default / ISO 8601)
- [x] 3.2 Wire `useDateDisplayFormatStore` into `AppearanceSettings` component
- [x] 3.3 Add date format toggle row (between Time format and Account rows)

## 4. Chart x-axis alignment

- [x] 4.1 Change `formatXTick` in `account-trend-chart.tsx` to `isoStr.slice(5, 10)` (MM-DD)
- [x] 4.2 Change `formatXTick` in `api-trend-chart.tsx` to `isoStr.slice(5, 10)` (MM-DD)

## 5. Internationalization

- [x] 5.1 Add `settings.appearance.dateFormat.{label,description,default,iso8601}` to `en.json`
- [x] 5.2 Add same keys to `zh-CN.json`
- [x] 5.3 Add same keys to `ko.json`

## 6. Testing

- [x] 6.1 Add `useDateDisplayFormatStore` initialization in `appearance-settings.test.tsx`
- [x] 6.2 Add test for date format toggle (select Default then ISO 8601, verify aria-pressed and store state)
- [x] 6.3 Verify all existing formatter tests and appearance settings tests pass

## 7. Review regressions

- [x] 7.1 Keep `formatTimeLong` field meanings stable and render ISO date-first ordering at display surfaces
- [x] 7.2 Subscribe rendered date surfaces to date-format preference changes so mounted values update immediately
- [x] 7.3 Add formatter and conversation-table regressions for semantic fields, ISO ordering, and live updates
- [x] 7.4 Route filename-missing archive record summaries through the subscribed shared formatter and cover live ISO updates
- [x] 7.5 Route reset-credit expiry timestamps through the subscribed shared formatter and cover live Default updates in both account surfaces
- [x] 7.6 Route daily report table dates through the subscribed preference and cover live Default/ISO updates
- [x] 7.7 Clarify that the preference applies only to read-only presentation text and excludes interactive controls and verbatim API/data representations
- [x] 7.8 Route the quota planner decision Peak label through the subscribed preference and cover live Default/ISO updates
