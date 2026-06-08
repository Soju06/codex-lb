## 1. Pull request guard

- [x] 1.1 Detect PRs that change release-managed version files to a beta version.
- [x] 1.2 Fail release-managed version changes unless all managed files agree
      before deciding whether the change targets a beta version.
- [x] 1.3 Fail beta PRs unless the head branch is the canonical
      `release/beta-X.Y.Z-beta.N` branch for the target version.
- [x] 1.4 Fail those PRs unless the head repository is the canonical repository.
- [x] 1.5 Fail those PRs unless the PR body records checked validation evidence
      for the exact PR head SHA.
- [x] 1.6 Re-run the guard on PR body edits so validation checklist updates are
      reflected before merge.

## 2. Publish guard

- [x] 2.1 Re-check the canonical branch and validation evidence in
      `publish-beta-release.yml` before creating a tag or GitHub prerelease.
- [x] 2.2 Require the published merge commit tree to match the validated PR head
      tree before tag creation, so `main` cannot advance after validation.
- [x] 2.3 Reject merged beta PRs whose head repository is not the canonical
      repository, even if the fork used the canonical beta branch name.
- [x] 2.4 Keep the publish check stdlib-only so it can run before dependency
      installation or artifact publishing.

## 3. Beta PR template

- [x] 3.1 Add an unchecked release-candidate validation checklist to
      automation-generated beta PR bodies.
- [x] 3.2 Include the exact candidate SHA in that template so stale validation is
      detected when the release PR branch changes.

## 4. Verification

- [x] 4.1 Add unit tests for non-canonical branch rejection, forked canonical
      branch rejection, missing evidence, stale SHA evidence, and passing
      validated canonical PRs.
- [x] 4.2 Run focused ruff and pytest checks for the guard implementation.
- [x] 4.3 Validate the OpenSpec change strictly.
- [ ] 4.4 Confirm GitHub CI and Codex review on the PR head.
