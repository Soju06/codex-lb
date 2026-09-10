## ADDED Requirements

### Requirement: The bridge operation spool window is dashboard-managed with a derived floor

The durable HTTP bridge operation spool holds the raw request payload of a
bridged turn and the response events recovery replays, so its retention window
is prompt-data retention and MUST be manageable from the Settings data
retention card alongside the request-log and usage-history windows. Unlike
those two, it MUST NOT be disable-able: the window is a positive duration in
seconds, resolved as code default, then the deprecated environment alias, then
a non-NULL `dashboard_settings` column of the same name. The settings API MUST
expose the effective value together with its provenance and MUST accept the
tri-state update used by the other inheritable settings: a field absent from
the payload leaves the stored value unchanged, a field present with `null`
clears it back to inheriting, and a field present with a value stores it.

The API MUST reject an update whose effective window falls below the longest
window in which a spooled operation can still be read, and the rejection MUST
name the binding term. That floor MUST be derived from the reuse windows in
force — not a fixed number — because operators can raise some of them, and the
same check MUST therefore also reject raising a reuse window past a stored
spool window.

A deployment can already be below the floor without any update having passed
that check, because the environment alias alone decides the window while the
column is NULL. That state MUST NOT make the settings surface read-only, and it
MUST NOT suspend the check either: while below the floor an update MUST be
accepted only when it leaves the effective window no shorter and the floor no
higher than it found them, and MUST be rejected when it would deepen the
violation. The API MUST report the current floor so the dashboard can state it
and mirror the check before submitting.

#### Scenario: Card manages the spool window with its provenance

- **GIVEN** no dashboard value has been stored
- **WHEN** an operator views the data retention card
- **THEN** the spool window is shown as inherited, stating that the spool holds
  raw request payloads and that a shorter window deletes them sooner
- **AND** the card states the floor the API would enforce

#### Scenario: A window below the replay floor is rejected

- **WHEN** a dashboard settings update would leave the effective spool window
  below the derived floor
- **THEN** the API rejects the update with a validation error naming the
  binding term
- **AND** the stored settings remain unchanged

#### Scenario: Raising a reuse window past the stored spool window is rejected

- **GIVEN** a stored spool window equal to the current floor
- **WHEN** an update raises one of the reuse windows the floor is derived from
  without also raising the spool window
- **THEN** the API rejects the update
- **AND** the same update is accepted when it raises both together

#### Scenario: A configuration already below the floor may only improve

- **GIVEN** an effective spool window below the floor that no update stored
- **WHEN** an update changes an unrelated setting, or raises the window toward
  the floor
- **THEN** the API accepts it
- **AND** an update that lowers the window further, or raises one of the terms
  the floor is derived from, is rejected

#### Scenario: Present-null returns the spool window to inheriting

- **GIVEN** a stored dashboard spool window
- **WHEN** a client PUTs `null` for it
- **THEN** the stored value returns to NULL and the effective value falls back
  to the environment alias, or the code default when the alias is unset
