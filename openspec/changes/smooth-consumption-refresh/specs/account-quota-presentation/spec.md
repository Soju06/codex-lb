## ADDED Requirements

### Requirement: Account quota refreshes preserve visual continuity

Account-facing quota surfaces MUST retain the last valid percentage when a
refresh briefly reports an unknown or non-finite value. They MUST keep the
corresponding quota row mounted for the same bounded hold and MUST NOT render
the unknown refresh state as zero. When a later valid percentage arrives, the
visible number and bar MUST ease from the displayed value to the new value at
0.1 percent display resolution. Reduced-motion preferences MUST update the
value without animation. Raw percentages used by sorting and routing MUST stay
unchanged.

#### Scenario: Temporary unknown value does not drain the bar

- **GIVEN** an account quota row displays a valid remaining percentage
- **WHEN** a refresh temporarily reports that percentage as unknown or non-finite
- **THEN** the account card, account list row, and account detail usage panel keep the last valid percentage visible
- **AND** the quota row remains mounted
- **AND** the bar does not drain to zero

#### Scenario: Fresh percentage replaces the held value smoothly

- **GIVEN** an account quota row is displaying a valid or held percentage
- **WHEN** a later refresh reports a different valid percentage
- **THEN** the displayed number and bar ease to the fresh percentage
- **AND** the visible number can change in 0.1 percent increments
- **AND** the raw percentage used by sorting and routing is unchanged

#### Scenario: Reduced motion skips the transition

- **GIVEN** the user prefers reduced motion
- **WHEN** a fresh valid percentage replaces the displayed percentage
- **THEN** the quota surface displays the fresh percentage without animation
