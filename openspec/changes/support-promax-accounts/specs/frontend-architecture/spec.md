## ADDED Requirements

### Requirement: Truthful Pro Max usage presentation

The dashboard SHALL display `promax` as Pro 500 and retain its observed
percentage usage. Estimated-credit subtotals SHALL visibly identify
unquantified accounts. A window containing only unquantified accounts
SHALL indicate unknown allowance rather than zero or exhausted allowance.
Incomplete weekly coverage SHALL suppress fleet credit forecasts before
using retained projections or local fallback calculations.

#### Scenario: Mixed dashboard quota
- **WHEN** a reported Pro Max weekly window is present alongside known plans
- **THEN** its observed remaining percentage is visible
- **AND** the subtotal excludes it explicitly
- **AND** no complete-fleet runway is presented

#### Scenario: Stale forecast cannot conceal incomplete coverage
- **WHEN** old forecast projections exist and current weekly coverage is incomplete
- **THEN** the dashboard does not resurrect a fleet forecast from those projections
- **AND** the unknown allowance explanation remains visible on desktop and mobile
