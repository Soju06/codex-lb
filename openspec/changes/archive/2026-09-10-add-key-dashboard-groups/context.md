## Purpose and scope

Group keys shares recent usage for an administrator-defined team; see the delta spec for requirements. It does not share credentials or allow another key's requests to be inspected.

## Decisions and example

In the administrator API Keys page, set Usage group to `Team A` on Alice's and Bob's keys. Either key can then open Group keys and compare both keys' last 30 days of requests, tokens, cache tokens, and cost. Clear the field to remove a member. Membership shares the full recent window, including usage before joining.

## Constraints and failure modes

Names are case-sensitive after whitespace trimming and each key belongs to at most one group. No group exists until assigned. Inactive members remain visible for usage accounting but cannot authenticate. Group reads resolve current database membership. Historical rollups survive raw retention; a pruned partial-hour edge cannot be recovered with finer precision than its hourly aggregate.

## Operations

The nullable field upgrades automatically with existing migration tooling. No environment setting is introduced. Stable guidance will be synced to the capability context and existing API-key docs after verification.
