## ADDED Requirements

### Requirement: APIs supports compact List and original Detail views

The APIs page SHALL default to the original sidebar/detail layout and offer a full-width List mode remembered locally. List rows SHALL display name, key prefix, status, lifetime request count, expiry, last used time and available pooled/API limit bars. Search, filters, sorting and selection SHALL survive view switching. Selecting a List row by pointer or keyboard SHALL open existing key details and management in a dismissible dialog.

#### Scenario: List row management
- **WHEN** an operator selects a key in List
- **THEN** details, usage charts and existing edit, regenerate, enable/disable and delete actions are available for that key
- **AND** closing the dialog returns to the same List state
- **AND** detail usage queries run only for a visible selected detail panel
- **AND** the edit dialog remains within the phone viewport with a reachable Close control and scrollable form

#### Scenario: Default and remembered view
- **WHEN** no valid view preference exists or browser storage is unavailable
- **THEN** the original Detail view is shown and switching views remains usable
- **WHEN** a saved List preference exists
- **THEN** the page opens in List view

#### Scenario: Compact responsive rows
- **WHEN** List renders at desktop and 320px phone widths
- **THEN** metadata and quota bars remain readable without horizontal page overflow
- **AND** only key prefixes, not secret key values, appear in the list

### Requirement: APIs filters keys by recorded usage

Both APIs views SHALL offer All usage, Key not used and Used filters. A key SHALL match Key not used only when lastUsedAt is null and its lifetime usageSummary has no positive request count. Either a last-use timestamp or positive request count SHALL qualify as Used. Usage filters SHALL combine with name/prefix search and status filters, and SHALL update when refreshed list data changes. The overview Used/Idle counts SHALL use the same definition.

#### Scenario: Historical usage evidence
- **WHEN** a key has a last-use timestamp but zero or unavailable summary requests
- **THEN** it matches Used and is excluded from Key not used
- **WHEN** a key has positive lifetime requests but no last-use timestamp
- **THEN** it also matches Used

#### Scenario: Unused key and combined filters
- **WHEN** a key has no last-use timestamp and zero or unavailable summary requests
- **THEN** it matches Key not used subject to search and status filters
- **AND** no matches shows filter-empty guidance without hiding the Create action

### Requirement: APIs List supports sorting and pagination

APIs SHALL offer ascending and descending name, status, lifetime requests, expiry and last-used sorting through the dropdown and desktop List headers. Unknown numeric/date values SHALL sort last in both directions. List SHALL paginate at 24 keys, filter and sort before pagination, and return to page one when search, filters or sort change. Sort controls SHALL expose their active direction accessibly.

#### Scenario: Sort filtered results
- **WHEN** sorting changes on a later page
- **THEN** all matching keys are reordered before the first page is displayed
- **AND** selected key and filters remain intact

#### Scenario: Unknown and zero usage
- **WHEN** lifetime requests are sorted ascending
- **THEN** zero precedes positive counts and missing summaries remain last
- **AND** descending puts positive counts before zero and missing summaries remain last
