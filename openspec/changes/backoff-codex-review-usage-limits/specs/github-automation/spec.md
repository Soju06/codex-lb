## ADDED Requirements

### Requirement: Codex review trigger usage-limit backoff

The Codex label synchronization script MUST NOT post a new `@codex review` comment while the comment sender's latest Codex response within the configured backoff window is a usage-limit reply. A usage-limit reply is a Codex response whose body starts (after optional leading whitespace) with the quota envelope "You have reached your Codex usage limits"; Codex reviews that merely discuss usage limits MUST NOT latch the backoff. Usage-limit evidence MUST be attributed to the sender whose request comment preceded the reply, and a newer normal Codex response for that same sender MUST lift the backoff; a clean THUMBS_UP reaction by a Codex reviewer on the sender's request comment counts as a normal response. Backoff state MUST be shared across all repositories processed in one run, so a usage limit observed in one repository suppresses the remaining review requests in the run; classified timelines from repositories without their own triggers MUST still contribute evidence. Before posting review requests in a repository, the script MUST also gather the repository's recent issue comments (within the backoff window, grouped per issue) as evidence, so quota replies on pull requests outside the current selection — including single `--pr` runs and closed pull requests — still latch the backoff; a failure to gather this evidence degrades to the classified-timeline evidence with a warning. When no quota evidence exists for the sender, the script MUST post the first `@codex review`, wait briefly, reread that pull request's timeline, and suppress the remaining review requests in the run if that probe observed a usage-limit reply; probing MUST stop once a normal Codex response has been observed, and MUST NOT run when the review request was not actually posted (for example after a tolerated write denial). Apply-loop status lines and error reports MUST reference the pull request of the decision being applied.

The script MUST resolve the sender identity in a way that works with GitHub App installation tokens (which cannot call `GET /user`): it prefers the app slug exported by the workflow (`GH_APP_SLUG`, yielding `<slug>[bot]`) and falls back to `GET /user` for PAT-backed runs. Once the run has switched to the fallback token, the app slug no longer describes the active identity: sender resolution MUST ignore it, and review triggers MUST be suppressed with a warning because posted comments would no longer be authored by the resolved sender. The review-request POST itself MUST NOT be silently retried under the fallback token after a rate-limit response: the fallback activates for subsequent calls, but the identity-sensitive comment fails instead of posting under the wrong author. If the sender cannot be resolved, only the review-trigger path is disabled (with a warning per affected decision); label synchronization and workflow-run approvals MUST proceed.

#### Scenario: Recent usage-limit reply latches the backoff

- **GIVEN** the sender's `@codex review` comment was answered by a Codex usage-limit reply within the backoff window
- **AND** the sender has no newer normal Codex response
- **WHEN** the script would trigger a missing Codex review
- **THEN** it skips the `@codex review` post and surfaces a write warning naming the usage-limit evidence

#### Scenario: Reviews that merely discuss usage limits do not latch

- **GIVEN** a Codex review whose body discusses usage limits but does not start with the quota envelope
- **WHEN** the script classifies Codex responses for the backoff
- **THEN** the response is treated as a normal Codex response, not a usage-limit reply

#### Scenario: Newer normal response lifts the backoff

- **GIVEN** the sender received a Codex usage-limit reply within the backoff window
- **AND** the same sender has a newer normal Codex response
- **WHEN** the script would trigger a missing Codex review
- **THEN** it posts the `@codex review` comment

#### Scenario: Newer clean reaction lifts the backoff

- **GIVEN** the sender received a Codex usage-limit reply within the backoff window
- **AND** a Codex reviewer later reacted with THUMBS_UP to the sender's `@codex review` comment
- **WHEN** the script would trigger a missing Codex review
- **THEN** it posts the `@codex review` comment

#### Scenario: Senders are attributed independently

- **GIVEN** the sender received a Codex usage-limit reply within the backoff window
- **AND** only a different account has a newer normal Codex response
- **WHEN** the script would trigger a missing Codex review
- **THEN** it still skips the `@codex review` post for the sender

#### Scenario: Backoff persists across repositories in one run

- **GIVEN** a run selecting multiple repositories
- **AND** the sender's usage limit was observed while processing an earlier repository
- **WHEN** the script would trigger a missing Codex review in a later repository
- **THEN** it skips the `@codex review` post there as well

#### Scenario: Evidence from a non-triggering repository still counts

- **GIVEN** an earlier repository whose classified timelines contain the sender's usage-limit reply but whose decisions need no review trigger
- **WHEN** a later repository in the same run would trigger a missing Codex review
- **THEN** the earlier repository's evidence latches the backoff and the post is skipped

#### Scenario: Quota evidence outside the selected pull requests still counts

- **GIVEN** a single `--pr` run where the sender's usage-limit reply lives on a different (possibly closed) pull request of the repository
- **WHEN** the script would trigger a missing Codex review
- **THEN** the repository's recent issue comments provide the evidence and the post is skipped

#### Scenario: Probe requires an actual post

- **GIVEN** the review-request comment was not posted because the write was denied and tolerated
- **WHEN** the script would otherwise probe for a quota reply
- **THEN** it neither waits nor rereads the pull request timeline for that decision

#### Scenario: No-data probe latches off remaining triggers

- **GIVEN** no Codex quota evidence exists for the sender in the classified timelines
- **WHEN** the script posts the first `@codex review` of the run
- **THEN** it waits the configured probe interval, rereads that pull request's timeline, and skips the remaining review requests if the probe observed a usage-limit reply

#### Scenario: Probing stops after a normal response

- **GIVEN** a normal Codex response for the sender has already been observed
- **WHEN** the script posts further `@codex review` comments in the run
- **THEN** it does not wait or reread pull request timelines for those posts

#### Scenario: Installation tokens resolve the sender from the app slug

- **GIVEN** the run authenticates with a GitHub App installation token and the workflow exports the app slug
- **WHEN** the script resolves the `@codex review` sender
- **THEN** it derives `<slug>[bot]` without calling `GET /user`

#### Scenario: Sender resolution failure only disables review triggers

- **GIVEN** the sender cannot be resolved from either the app slug or `GET /user`
- **WHEN** the script applies decisions
- **THEN** label synchronization proceeds and each suppressed review trigger surfaces a warning naming the unresolved sender

#### Scenario: Fallback token activation suppresses review triggers

- **GIVEN** the run has switched to `GH_FALLBACK_TOKEN` after rate-limit exhaustion
- **WHEN** the script would trigger a missing Codex review
- **THEN** it skips the `@codex review` post with a warning, because the comment author would no longer match the resolved sender

#### Scenario: The review-request POST is not retried under the fallback identity

- **GIVEN** the review-request comment POST itself hits the primary token's rate limit
- **WHEN** the fallback token activates
- **THEN** the POST fails instead of being silently retried under the fallback identity, while later calls use the fallback token

#### Scenario: Apply status is attributed to the applied pull request

- **WHEN** the script applies decisions for multiple pull requests in one run
- **THEN** each status line and error report references the pull request of the decision being applied

### Requirement: Apply-time reclassification

Before performing writes for a classified decision (label changes, legacy label removal, workflow-run approvals, or review triggers), the Codex label synchronization script MUST reclassify the pull request and act on the fresh evidence only. If the head SHA no longer matches the SHA the decision was classified against, the decision MUST be skipped with a warning. If the head is unchanged but the evidence changed (checks, reviews, mergeability), the writes MUST follow the fresh decision, and a review trigger MUST only fire when both the original and the fresh classification want it. The freshly read timeline MUST feed the shared usage-limit backoff so quota replies that arrived after bulk classification suppress the remaining review requests. Reclassification read failures MUST honor `--tolerate-read-errors` (log and skip the decision without failing the run). Decisions without pending writes need not be reclassified.

#### Scenario: Stale decision is skipped after a head move

- **GIVEN** a pull request whose head changed between classification and apply
- **WHEN** the script reaches that decision in the apply loop
- **THEN** it skips all writes for the decision and warns that the head moved

#### Scenario: Same-head evidence changes are applied fresh

- **GIVEN** a pull request whose head is unchanged but where Codex raised a new finding after classification
- **WHEN** the script reaches that decision in the apply loop
- **THEN** the writes reflect the fresh classification instead of the superseded one

#### Scenario: Fresh quota evidence suppresses later triggers

- **GIVEN** a quota reply that arrived between bulk classification and apply-time reclassification of one pull request
- **WHEN** later decisions in the run would trigger missing Codex reviews
- **THEN** the reclassified timeline has latched the backoff and those posts are skipped

#### Scenario: Reclassification honors tolerant reads

- **GIVEN** a run with `--tolerate-read-errors`
- **WHEN** apply-time reclassification of one pull request fails with a GitHub read error
- **THEN** the decision is logged and skipped without failing the run
