## 1. Zustand store with localStorage persistence

- [x] 1.1 Create `frontend/src/hooks/use-date-format.ts` with `DateDisplayFormat` type (`"default"` | `"iso8601"`), Zustand store, and `getDateDisplayFormat()` accessor
- [x] 1.2 Persist preference to localStorage under key `codex-lb-date-display-format`

## 2. Date formatting logic

- [x] 2.1 Import `getDateDisplayFormat` into `formatters.ts`
- [x] 2.2 Add ISO date/time helper functions (`formatISODate`, `formatISOTime`)
- [x] 2.3 Add ISO 8601 branch in `formatTimeLong`: when active, return `{ time: "YYYY-MM-DD", date: "HH:MM:SS" }`

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
