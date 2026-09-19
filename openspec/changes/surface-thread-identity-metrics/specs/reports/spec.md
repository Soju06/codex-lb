## ADDED Requirements

### Requirement: Thread identity metrics are served per keyed and unkeyed facet

The dashboard MUST expose `GET /api/reports/thread-identity`, returning the
selected date range split into a **keyed** facet (requests whose normalized
`conversation_id` or, failing that, normalized `session_id` is present) and an
**unkeyed** facet (requests carrying neither). Each facet MUST report the mean
accounts per conversation, the share of conversations served by exactly one
account, the turn-to-turn account switch rate, the cache hit ratio and the
facet's share of requests; the response MUST also report the unkeyed share of
all requests in the window and the thresholds each figure was computed with.

A thread is identified by its normalized `conversation_id`, else its normalized
`session_id`, else — for unkeyed traffic only — its `api_key_id`. Requests with
no thread identity at all are counted as requests but MUST NOT be grouped with
one another. Row scope MUST match the rest of the Reports page: internal
warm-up traffic excluded, soft-deleted rows retained.

The figures MUST be defined as: accounts per conversation is the mean count of
distinct attributed accounts over threads with at least three requests in the
window; a turn is a pair of consecutive requests in one thread less than ten
minutes apart whose accounts are both known and whose `input_tokens` are both
known and did not shrink, and the switch rate is the share of turns whose
account changed; the
cache hit ratio is summed `cached_input_tokens` over summed `input_tokens`
across successful requests with more than 5,000 input tokens.

#### Scenario: A conversation served by two accounts reports a factor of two

- **GIVEN** one conversation with three requests in the window, two served by
  one account and one by another
- **WHEN** `GET /api/reports/thread-identity` is requested for that range
- **THEN** the keyed facet reports one conversation with a mean of 2.0 accounts
  and a single-account conversation share of zero

#### Scenario: Conversations below the request floor are not counted

- **GIVEN** a thread with fewer than three requests in the window
- **WHEN** the endpoint aggregates the range
- **THEN** that thread contributes to the request counts but not to the
  conversation count, the mean accounts per conversation, or the
  single-account share

#### Scenario: A turn is only counted when it can be attributed and measured

- **GIVEN** two consecutive requests in one thread that are ten minutes or more
  apart, or whose `input_tokens` shrank, or where either request has no account,
  or where either request has no recorded `input_tokens`
- **WHEN** the endpoint aggregates the range
- **THEN** that pair is excluded from both the turn count and the switch count

#### Scenario: One thread keyed by either column is not split in two

- **GIVEN** a thread whose requests carry the same value in `conversation_id`
  and `session_id`, and some of whose requests carry only one of the two
- **WHEN** the endpoint aggregates the range
- **THEN** all of those requests belong to one conversation

#### Scenario: The cache hit ratio samples only large successful requests

- **GIVEN** a window containing a successful request above 5,000 input tokens, a
  failed request above that floor, and a successful request at or below it
- **WHEN** the endpoint aggregates the range
- **THEN** only the first request's `input_tokens` and `cached_input_tokens`
  contribute to the cache hit ratio

### Requirement: Thread identity metrics are pool-wide and window-capped

`GET /api/reports/thread-identity` MUST accept only the Reports date range and
timezone. It MUST NOT accept account, API key, model or user-agent filters,
because scoping the window to one account would force every conversation to a
single account and report an accounts-per-conversation factor of 1.0 by
construction.

Because the figures are computed from raw request logs rather than from the
report rollup, every statement MUST be bounded by the selected window, and a
range wider than seven days MUST answer with `available: false` and the ceiling
rather than executing the scan. The endpoint's result MAY be cached, and the
dashboard MUST NOT issue the request while the card that renders it is hidden.

#### Scenario: A range wider than the ceiling is declined, not scanned

- **WHEN** `GET /api/reports/thread-identity` is requested for a range longer
  than seven days
- **THEN** it responds `available: false` with the ceiling and the requested
  window length, and reports no metrics

#### Scenario: Hiding the card stops the query

- **GIVEN** an operator has hidden the thread identity card in the Reports
  chart selector
- **WHEN** the Reports page loads or its date range changes
- **THEN** no thread identity request is issued

### Requirement: Approximate and decayed thread identity figures are disclosed

Unkeyed traffic carries no conversation or session identifier, so its
conversation and switch figures are reconstructed by grouping on `api_key_id`.
The response MUST mark the unkeyed facet as an approximate grouping and the
dashboard MUST tell the operator that those two figures are a reconstruction
rather than a true thread grouping.

Deleting an account detaches `account_id` from that account's request history,
which lowers a past window's accounts-per-conversation factor and switch rate
without lowering its request counts. Each facet MUST therefore report the share
of its requests that carry no attributed account, and the dashboard MUST
disclose that share whenever it is non-zero, so a decayed factor is not read as
a real reduction in account spread.

#### Scenario: The unkeyed column is labelled approximate

- **WHEN** the thread identity card renders a window containing unkeyed traffic
- **THEN** the unkeyed facet is flagged as an approximate thread grouping and
  the card states that its conversation and switch figures are reconstructed
  from the API key

#### Scenario: Detached account attribution is reported alongside the factor

- **GIVEN** a window in which some requests were served by an account that has
  since been deleted
- **WHEN** the endpoint aggregates the range
- **THEN** the affected facet reports a non-zero unattributed request share
- **AND** the card states what fraction of the window has no account
  attribution and that the window's account-spread figures read low as a result

#### Scenario: A fully attributed window carries no disclaimer

- **GIVEN** a window in which every request still has an attributed account
- **WHEN** the card renders
- **THEN** no attribution disclaimer is shown
