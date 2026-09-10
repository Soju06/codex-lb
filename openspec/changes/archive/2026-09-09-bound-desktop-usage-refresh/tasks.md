- [x] 1. Add the aggregate refresh deadline and route regression coverage for fresh/stale observations and caller cancellation.
- [x] 2. Validate the affected tests/specs, apply the same fix to the local runtime, and verify deployment.
- [x] 3. Address the current-head review finding, archive the verified change and update the PR.

Verified on 2026-09-09: the unbounded route regression failed before the fix; 76 route/projection/composition tests and 17 shared-refresh ownership/cancellation tests passed after it. The preserved local runtime passed 77 affected tests and was deployed as `1a58a0065010cae81bedd0351792d183761a74cc`. An authenticated quota probe returned 200 with main quota allowed and no Luna reserve banner; actual Astra requests succeeded after cutover. The CodeRabbit thread was addressed with commit `259376baf` and resolved. Hosted follow-up checks remain separate from these local results.
