# frontend-architecture Delta

## MODIFIED Requirements

### Requirement: Dashboard account cards show live credit state

Account summary responses SHALL expose nullable upstream purchased-credit metadata as `creditsHas`, `creditsUnlimited`, and `creditsBalance`, alongside calculated remaining subscription credits for each available quota window. Dashboard card and list views MUST present calculated subscription quota and purchased credits as separate labeled metrics and MUST NOT use one as a fallback replacement for the other.

When `creditsUnlimited` is true, the purchased-credit metric SHALL render `Unlimited`. Otherwise, it SHALL render the numeric `creditsBalance` when available and `-` when unavailable. The subscription metric SHALL select remaining credits with the following precedence: monthly credits for monthly-only accounts; secondary credits for weekly-only accounts; otherwise secondary credits when available, falling back to primary credits. It SHALL render `-` when the selected value is unavailable.

The compact list SHALL sort subscription and purchased credits independently. A persisted legacy `credits` sort preference SHALL migrate to the purchased-credit sort so existing operator preferences remain valid after upgrade.

#### Scenario: Zero purchased balance does not hide subscription quota

- **WHEN** an account summary has `creditsBalance = 0.0`
- **AND** `remainingCreditsSecondary = 35910.0`
- **THEN** the dashboard shows subscription quota `35910.00`
- **AND** separately shows purchased credits `0.00`

#### Scenario: Unlimited applies only to purchased credits

- **WHEN** an account summary has `creditsUnlimited = true`
- **THEN** the purchased-credit metric shows `Unlimited`
- **AND** the subscription metric still shows its own remaining quota value or `-`

#### Scenario: Missing metrics render independently

- **WHEN** an account summary has no purchased credit balance and no calculated remaining subscription credits
- **THEN** both separately labeled metrics show `-`

#### Scenario: Unlimited credits render explicitly

- **WHEN** an account summary has `creditsUnlimited = true`
- **THEN** the dashboard account card shows purchased credits as `Unlimited`
- **AND** the subscription quota remains independently visible

#### Scenario: Positive credit balance renders on the card

- **WHEN** an account summary includes `creditsBalance = 1.5`
- **THEN** the dashboard account card shows purchased credits as `1.50`
- **AND** does not replace the subscription quota value

#### Scenario: Missing credit data renders a placeholder

- **WHEN** an account summary has no purchased credit balance and no remaining subscription credit value
- **THEN** the dashboard account card shows `-` for both separately labeled metrics

#### Scenario: Legacy credit sort remains valid

- **WHEN** local dashboard preferences contain the legacy `credits` sort key
- **THEN** the dashboard migrates it to the purchased-credit sort key
- **AND** persists the migrated preference
