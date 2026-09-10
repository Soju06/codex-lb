## Purpose

Show how each key's daily token consumption and cost contribute to its group's recent totals. The delta spec defines the observable contract.

## Decisions and example

For a window from 11 August at 12:30 UTC through 10 September at 12:30 UTC, the chart includes dates 11 August through 10 September, with partial first and last dates. Alice and Bob each have a line; switching to Cost (USD) uses the same daily data. Hide Alice to inspect Bob, or expand the daily table to read exact values.

## Constraints and failure modes

Daily data shares the group endpoint's authentication, authorization, retention, and cleanup rules. Zero days remain visible. Daily values include recent usage before a member joined, exactly like existing totals. Raw data and folded data contribute once. Date labels use UTC independently of browser timezone.

## Operations

No schema, environment, or deployment setting is introduced. The chart uses existing persisted data. Stable guidance will be synced to the capability context and API-key guide after verification.
