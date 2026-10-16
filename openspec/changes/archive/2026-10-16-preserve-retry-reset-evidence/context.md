## Purpose and scope

This change resumes the focused retry-health fix from
[closed PR #2440](https://github.com/Soju06/codex-lb/pull/2440), originally
authored by DongwonTTuna. It is a partial fix for
[#1707](https://github.com/Soju06/codex-lb/issues/1707).

## Evidence and limits

The restored HTTP-route regression fails on upstream main 33ec57dd: an upstream
reset at 2000432000 is persisted as 2000000030. The test uses synthetic account
data, isolated SQLite, and stubbed upstream sockets.

The investigated local deployment reports 1.25.0-beta.9 and contains the three
message-only HTTP retry branches. Its reported failure also has missing prior
output and transient owner-selection symptoms; the original upstream reset
frame was not retained. These observations do not prove that the metadata bug
caused that particular incident, or that this patch resolves all its failures.

## Related work and constraints

[#2486](https://github.com/Soju06/codex-lb/pull/2486) addresses a hard owner
serving transient backoff. Automatic reviewer sessions have their own
continuation state and can fail independently after a parent conversation
moves. Neither problem is solved by dropping required ownership or suppressing
approval errors here.

## Example

Account A receives a usage-limit error with a five-day reset. Recording only
the message selects a short fallback deadline and makes A appear recoverable
within the request budget. Retaining the real deadline lets a later eligible
request use the existing retirement path and select B. Explicit client-owned
anchors and file-bound requests keep their existing restrictions.
