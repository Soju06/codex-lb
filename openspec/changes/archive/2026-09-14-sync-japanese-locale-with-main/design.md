## Context

See [proposal.md](proposal.md) for the reported failure. The merged English bundle is the key inventory; existing locale tests already enforce Japanese key equality, nonempty values, interpolation, and inline markup.

## Goals / Non-Goals

Restore Japanese coverage using the existing flat bundle and established terms such as `社内認証`, `ログイン`, and `本人確認`. Authentication behavior, provider configuration, and locale selection do not need implementation changes.

## Decisions

- Preserve all retained translations and their order. Add translations for missing English keys and remove keys no longer present in English; retaining obsolete entries would continue to fail the parity gate.
- Preserve provider names, claim names, URLs, and interpolation values. For example, the pending-account screen names `{{provider}}` and retains the administrator's sign-in reference.
- Use the existing locale integrity tests and fixture-backed browser harness. English/Japanese captures of the same OIDC and account-management screens show the resulting copy and layout without contacting a real identity provider.

## Risks / Trade-offs

- Main can add or remove keys again before publication: record the merged main revision and key counts with the test results.
- Copy could misstate an authentication condition: preserve the English meaning, including the ten-minute, single-use test-login grant and the distinction between identity-provider recognition and a dashboard account.
- Passing key checks alone cannot reveal layout issues: inspect representative browser captures before archiving.
