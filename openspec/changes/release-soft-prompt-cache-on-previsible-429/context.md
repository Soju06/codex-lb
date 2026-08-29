# Soft prompt-cache release on pre-visible 429

## Purpose

Restore eligible-account failover for inline-image streaming requests that
carry only soft `prompt_cache_key` affinity. A 429 on the warm-cache account
must not fail the whole request when another image-capable account exists.

See the delta and main `responses-api-compat` specs for normative rules.

## Decision rationale

Adjacent pre-visible paths already set `reallocate_sticky=True` after excluding
an account (confirmed pre-dispatch connect failure, verified fresh replay).
The `failover_next` branch excluded the account but left the sticky policy
unreleased, so prompt-cache selection kept preferring the excluded owner.

Alternatives considered:

- Clear the sticky key entirely. Rejected: the balancer already knows how to
  rebind on `reallocate_sticky=True`; inventing a second release path would
  drift from the adjacent failover sites.
- Reallocate every excluded account, including file pins. Rejected: file
  ownership is hard and must fail closed.
- Add a `CODEX_LB_*` toggle. Rejected: this is a correctness hole, not an
  operator preference.

## Constraints

- File-pinned, previous-response, and turn-state owners stay required.
- API-key reservation settlement must still happen before error-health writes.
- Excluded accounts must remain out of the next selection loop.
- No new settings.

## Failure modes

- Soft prompt-cache + inline image + pre-visible 429, two image-capable
  accounts: must retry the second account.
- Same request with a live `input_file.file_id` pin: must stay on the file
  owner and fail closed if that owner is 429.

## Example

1. Accounts A and B both accept `gpt-5.6-sol` images.
2. Client sends streaming `/v1/responses` with `prompt_cache_key` and an
   inline `input_image`.
3. Affinity selects A; A returns HTTP 429 before any visible stream bytes.
4. Retry excludes A, sets `reallocate_sticky=True`, and completes on B.

## Related

- Issue #1924
- `openspec/specs/responses-api-compat/spec.md`
- Adjacent `reallocate_sticky=True` sites in streaming retry
