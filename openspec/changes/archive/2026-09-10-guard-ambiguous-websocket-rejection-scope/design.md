## Context

The finalizer selects one health penalty for a batch, but this selection alone does not attribute a shared transport error to that request.

## Goals / Non-Goals

Protect ambiguous holds without disabling recovery for one known scope. Keep existing health penalty and settlement ordering intact.

## Decisions

A nonempty per-request error override identifies that request's error; production sets it from a matched response event. Without that attribution, retain scope only when all pending requests share the same model and tier. Otherwise pass unknown model and tier to the existing rejection writer.

This is conservative when another pending request is neutral. Guessing which different scope caused a shared account error could incorrectly restore routing. Capture the result before cleanup awaits, as before.

## Risks / Trade-offs

Some mixed batches stay held until ordinary recovery or another attributable rejection establishes scope. Public probe tests cover mixed models, mixed tiers and one common scope; the existing request-specific batch test remains unchanged.
