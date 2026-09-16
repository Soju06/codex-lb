# Evidence and scope

Issue #2134 reported Docker 1.24.0, Plus accounts, OpenCode, and a valid
`POST /v1/images/generations` request for `gpt-image-2`. Main at
`0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0` still used `gpt-5.5` unconditionally.
The documented Images route supports this request; the host override was removed.
The maintainer accepted the ordered selector in issue comment 5586534068.

The public-route regression reproduced HTTP 400 `model_not_found` with a
simulated upstream rejecting `gpt-5.5`. The same input succeeds after selection
changes to `gpt-5.6-luna`, retaining `gpt-image-2` in the image tool.
This proves the local mechanism and accepted requirement. It does not verify
real account entitlement or guarantee the reporter's upstream remains unchanged.

For example, a stale catalog advertising both candidates now selects Luna.
A catalog advertising only unsuppressed `gpt-5.5` selects that candidate.
If no candidate qualifies, selection still defaults to Luna and existing
routing/error handling applies. No retry is added. Explicit probe models are
preserved. No tool-ID or catalog redesign from #2304 is included.
