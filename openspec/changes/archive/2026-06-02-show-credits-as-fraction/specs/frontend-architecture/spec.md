## ADDED Requirements

### Requirement: Dashboard usage donuts present credits as a raw fraction
The dashboard's primary and secondary usage donuts MUST present the remaining credit count as a `remaining/total` fraction with locale-aware thousands separators (e.g. `7,331/7,560`), with a `Credits` caption above the fraction. The donut titles MUST read `Hourly Credits` and `Weekly Credits` so the title and caption agree.

Compact-format abbreviation (e.g. `7.33k`) MUST NOT be used in the donut center for these panels; the existing per-account legend rows and the percent-used caption may continue to use compact formatting unchanged.

#### Scenario: Dashboard donut shows raw fraction
- **WHEN** the dashboard renders a usage donut with `remaining=7331` and `total=7560`
- **THEN** the donut title reads `Weekly Credits` or `Hourly Credits`
- **AND** the center renders the fraction `7,331/7,560` under a `Credits` caption
