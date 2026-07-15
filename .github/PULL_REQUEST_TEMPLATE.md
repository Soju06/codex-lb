<!--
Thanks for contributing to codex-lb! 🙏
Fill in the sections below. Delete sections that don't apply.
-->

## Summary

<!-- One or two sentences: what does this PR change and why? -->

## Type of change

<!-- Check the box that matches your PR. Commit titles must follow Conventional Commits. -->

- [ ] `fix:` — bug fix (no behavior change beyond the bug)
- [ ] `feat:` — new user-facing feature or capability
- [ ] `refactor:` — internal refactor (no behavior change, no API change)
- [ ] `docs:` — documentation only
- [ ] `chore:` / `ci:` / `build:` — tooling, CI, packaging
- [ ] `test:` — test-only change
- [ ] **Breaking change** (also append `!` after the type, e.g. `feat!:` or include `BREAKING CHANGE:` footer)

Linked issue: <!-- e.g. Closes #123, Fixes #456 -->

## OpenSpec

<!--
codex-lb is OpenSpec-first. If this PR changes observable behavior, requirements,
contracts, or schema, it needs an OpenSpec change under openspec/changes/<change>/.

If this PR touches an upstream-mimicking code path (Codex CLI / ChatGPT request
shape, image pipeline, response.create framing, etc.), it must stay
**codex-faithful** — i.e. preserve the exact wire format the real upstream
emits. Call out anything that intentionally diverges, and link the spec section
that records the divergence.
-->

- [ ] This PR includes / updates an OpenSpec change
- [ ] Not applicable — bug fix that matches the existing spec
- [ ] Not applicable — docs / CI / chore only
- [ ] This PR touches a codex-faithful path (image pipeline, request/response
      shape, SSE framing, OAuth flow) and preserves upstream-equivalent behavior

Change directory: <!-- openspec/changes/<change>/ -->

## Changes

<!-- Bulleted list of the substantive changes. Group by area if multiple. -->

-
-

## Simplicity

<!-- See PRINCIPLES.md. Delete this section if the PR adds no setting, no README
     section, no dashboard nav item, and changes no default. -->

- [ ] New feature defaults to **off** or works with **zero config**
- [ ] No new required setup step (or maintainer approval via `simplicity-budget-approved` label)
- New setting(s) and why each can't be a default: <!-- name → justification -->
- [ ] README sections / `.env.example` / dashboard nav within budget (or `simplicity-budget-approved` label requested)

## Test plan

<!--
How did you verify this works? Paste the commands you ran and the relevant outputs.
Required: unit tests for new logic, integration tests for new endpoints.
-->

```
# uv run pytest tests/unit/test_<area>.py -q
# uv run pytest tests/integration/test_<area>.py -q
```

## Screenshots / output

<!-- REQUIRED for dashboard-visible changes: before/after screenshots (or a short
     recording). For proxy behavior: example request + response, or a log excerpt.
     Delete only if this PR has no user-visible surface. -->

## Checklist

- [ ] Title is in Conventional Commits format (`<type>(<scope>)?: <subject>`).
- [ ] Linked the related issue / discussion above.
- [ ] Added or updated tests covering the change.
- [ ] Ran `uv run pre-commit run local-ci --hook-stage manual --all-files` or the relevant `make <target>` subset locally.
- [ ] If touching specs: `openspec validate --specs` passes and `/opsx:verify` is clean.
- [ ] Simplicity gates reviewed: the five simplicity rules (PRINCIPLES.md P1-P5).
- [ ] CHANGELOG is **not** edited by hand (release-please handles it).
