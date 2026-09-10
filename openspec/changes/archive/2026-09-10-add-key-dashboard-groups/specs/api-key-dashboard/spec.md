## ADDED Requirements

### Requirement: Administrator-managed key usage groups

Administrator API-key creation and editing SHALL accept an optional `usageGroup` name of at most 128 characters after trimming surrounding whitespace. Names SHALL be case-sensitive; blank or null SHALL mean ungrouped. Keys with the same non-empty name SHALL belong to one usage-sharing group. Omitting the property during update MUST preserve membership. Existing keys MUST remain ungrouped after migration. Only callers with dashboard write access SHALL assign or remove membership.

#### Scenario: Assign and remove a member

- **WHEN** an administrator assigns the same group name to two keys in the API-key forms
- **THEN** both keys can see the group's aggregate statistics
- **AND** clearing one key's group removes its access and its entry on subsequent group reads

#### Scenario: Preserve existing installations and updates

- **WHEN** an existing database is upgraded or a key is edited without `usageGroup`
- **THEN** upgrade leaves historical keys ungrouped and unrelated edits preserve existing membership
- **AND** regenerating a key preserves membership

### Requirement: Privacy-safe thirty-day group usage

`GET /api/key-dashboard/group` SHALL require an active, unexpired Bearer key regardless of the global proxy authentication setting. The server MUST derive current membership from persisted data using that credential and MUST NOT accept a caller-selected group or key. Ungrouped keys SHALL receive a null group and an empty member list. Grouped keys SHALL receive current members, including the caller and inactive or expired members, with zero totals for unused keys, sorted by display name with a deterministic tie-break. Deleted or reassigned keys MUST NOT appear.

The response SHALL contain the group name, UTC `from` and `until` timestamps, and per-member display name, masked prefix, caller indicator, request count, total tokens, cached input tokens, and USD cost for `[until - 30 days, until)`. It MUST NOT expose raw keys, hashes, database IDs, peer request logs, limits, account/source/routing assignments, or client identity. Statistics SHALL exclude warmup and limit-warmup requests and include preserved historical usage after account deletion. Retained hourly aggregates SHALL contribute without double counting raw history; unavailable partial-hour edges after raw-log retention SHALL follow the existing usage-rollup boundary semantics.

#### Scenario: Isolate group statistics and the time window

- **GIVEN** keys in two groups have recent and older usage
- **WHEN** a member requests group statistics
- **THEN** only its current group's members and usage within the preceding 30 days are returned
- **AND** caller-supplied selectors cannot expand that scope

#### Scenario: Membership removal takes effect on the next read

- **GIVEN** a member's authentication metadata has been cached
- **WHEN** an administrator removes or changes its group
- **THEN** the next group read uses current persisted membership

#### Scenario: Reject invalid credentials

- **WHEN** a missing, unknown, inactive, or expired key requests the group endpoint
- **THEN** it receives the independent key-dashboard 401 response

#### Scenario: Preserve aggregated history

- **GIVEN** hourly aggregates cover older requests and newer requests remain in raw logs
- **WHEN** a group member loads statistics
- **THEN** folded requests count once and newer requests also contribute

### Requirement: Group keys dashboard tab

The authenticated key dashboard SHALL provide an accessible Group keys tab alongside Overview and Install. It SHALL load group data only when opened, display the 30-day period, group totals and each member's request/token/cache/cost totals, identify the caller, and show a clear ungrouped state directing the holder to an administrator. Group loading errors SHALL offer retry. Refresh SHALL reload the active group's data. Disconnect or any group 401 MUST clear the credential and group data; unmounting or replacing a request MUST discard late responses. The member table MUST remain usable on narrow screens without page-level horizontal overflow.

#### Scenario: Inspect and refresh group usage

- **WHEN** a grouped key opens Group keys and activates Refresh
- **THEN** it sees the group totals and each member's statistics over the fixed 30-day period from a refreshed response

#### Scenario: Ungrouped key and failed requests

- **WHEN** an ungrouped key opens Group keys
- **THEN** it sees a no-group message
- **AND** a failed group request displays an error and retry action

#### Scenario: Leave the group tab or disconnect during loading

- **WHEN** a pending group request completes after the tab unmounts or the user disconnects
- **THEN** its response does not restore old group data or credentials
