## 1. Quota contract

- [x] 1.1 Preserve the validated raw caller envelope without changing existing serialization; verify both usage-fetch transport paths.
- [x] 1.2 Implement strict pooled projection and original-account composition; verify weighted, exhausted, missing, stale, malformed and per-model cases.
- [x] 1.3 Expose the authenticated Desktop quota endpoint and alias; verify route-level identity, authorization, cancellation and session ownership.

## 2. Relay and setup

- [x] 2.1 Add the optional loopback relay command; verify HTTP/WebSocket forwarding, fixed destinations, header/body preservation, failures and cleanup against fake upstreams.
- [x] 2.2 Write linked setup/context documentation with configuration, version limits, activation and rollback; verify docs and strict OpenSpec validation.

- [x] 2.3 Run the relay inside the normal server lifecycle with default-off loopback/container modes; verify startup failures, active-request cleanup and listener configuration.
- [x] 2.4 Build and deploy one container atop the exact existing runtime revision, preserving its database graph, ports and settings; remove the temporary trial after cutover.

## 3. Acceptance and delivery

- [x] 3.1 Complete applicable isolated repository checks and review the exact candidate against the pinned base.
- [x] 3.2 Verify retained displayed identity, genuine native pooled quota and an actual Astra request through LB in the authorized real trial.
- [x] 3.3 After real acceptance, sync and verify specs, archive verified work, commit, push and open the focused upstream PR with exact evidence.
- [x] 3.4 Inspect current-head hosted checks and review feedback, address in-scope findings, and hand off readiness and maintainer blockers without merging.

Task 3.1 earlier code evidence is bound to `1aa75e14f2cdcc6f5223a64b68c573f7b207a630` and base `c0beaaadd96a89f0240582b5449bf4dd50647c7d`; cookie and embedded-lifecycle changes need their own affected checks. Task 3.2 passed with candidate `b1f1e234d7b5c1e6897f3c9127b77122398c7a7e` on 2026-09-09. The user confirmed their name, purple accent and 38% remaining, authenticated native usage reported 62% used with the reserve-only gate cleared, and the existing LB recorded successful `gpt-6-astra` WebSocket requests. See [observed trial](../../../specs/desktop-pooled-usage/context.md#observed-desktop-trial). The user subsequently authorized one-container integration and issue/PR publication.

One-container delivery passed on 2026-09-09 with local runtime `effc2a944313fbf3325330785ba3a3d3e95a1076`, preserving its deployed parent and database graph. The user confirmed retained name, accent and native usage after cutover; authenticated native quota requests and successful Astra requests were observed on the replacement. The temporary trial, copied credentials and obsolete diagnostic containers were removed. Issue #2285 and PR #2286 are open. The latest hosted CI run for upstream candidate `3f8af6be0` passed all applicable checks; CodeRabbit was still processing at handoff with no review threads yet. Nothing was merged.
