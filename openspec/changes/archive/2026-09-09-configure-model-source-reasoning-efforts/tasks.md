## 1. Model-source dashboard

- [x] 1.1 Extend model-source form state so it can parse, round-trip, and save
  supported reasoning efforts plus the default effort from raw metadata.
- [x] 1.2 Add create/edit dialog controls for the effort list and default
  selector without constraining operators to a fixed enum.
- [x] 1.3 Keep the existing reasoning toggle and preserve unrelated raw metadata
  keys during edits.

## 2. Verification

- [x] 2.1 Add focused frontend regression coverage for default seeding,
  arbitrary effort round-tripping, and stale-default normalization.
- [x] 2.2 Run focused frontend checks and strict OpenSpec validation for this
  change.
