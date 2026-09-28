## Context

See [proposal.md](./proposal.md) for the reported parity failure. Main already routes the SCIM card, credential dialog, errors, and summaries through the shared translation bundle.

## Goals / Non-Goals

- Restore the existing Japanese locale contract using the same translation keys and call sites.
- Keep this sync limited to translations and their specification; SCIM behavior and credentials are unchanged.

## Decisions

- Append the missing Japanese entries in the English bundle's order, retaining existing Japanese translations. An English fallback would leave the locale contract and integrity checks unsatisfied.
- Use the existing `社内認証` wording, translate credentials consistently as `認証情報`, and retain `{{label}}` and `{{when}}`. For example, the issued-credential description identifies the administrator's label without translating it.
- Reuse the existing locale integrity and organisation tests. The maintainer waived new screenshots for this sync; existing screenshots and the earlier locale regression tests remain applicable.

## Risks / Trade-offs

- Main may add keys again before merge → record the integrated main revision and final parity counts.
- Credential copy could misstate availability or account matching → preserve the English distinction between a one-time secret, replaceable credentials, and email matching that avoids duplicate accounts.
