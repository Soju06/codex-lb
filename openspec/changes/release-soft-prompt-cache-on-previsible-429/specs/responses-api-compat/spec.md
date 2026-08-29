## ADDED Requirements

### Requirement: Soft prompt-cache streaming failover releases affinity on pre-visible 429

The proxy MUST release soft `prompt_cache_key` affinity when a streaming
Responses request fails with a pre-visible classified `failover_next` error,
including HTTP 429: it MUST exclude that account, set `reallocate_sticky=True`
for the next selection, and retry an eligible compatible account. An inline
`input_image` MUST NOT keep the request pinned to the excluded soft-cache
account.

A live `input_file.file_id` pin, turn-state owner, or other required account
owner MUST remain fail-closed: the proxy MUST NOT dispatch that request to a
different account after the owner returns a pre-visible 429.

#### Scenario: Inline-image prompt-cache 429 fails over to another account

- **GIVEN** at least two image-capable accounts are eligible
- **AND** a streaming Responses request includes an inline `input_image` and a
  `prompt_cache_key`
- **AND** the request has no live file pin, turn-state owner, or
  previous-response owner
- **WHEN** the prompt-cache-selected account returns HTTP 429 before any
  stream bytes are visible
- **THEN** the proxy excludes that account
- **AND** the next selection uses `reallocate_sticky=True`
- **AND** the request completes on another eligible compatible account

#### Scenario: File-pinned prompt-cache 429 stays fail-closed

- **GIVEN** a streaming Responses request references a live
  `input_file.file_id` pin on account A
- **AND** the request also carries a `prompt_cache_key`
- **WHEN** account A returns HTTP 429 before any stream bytes are visible
- **THEN** the proxy does not dispatch the request to another account
- **AND** the request fails closed on the file owner
