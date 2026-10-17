## Context

See proposal.md. The existing terminal reader already parses incomplete reasons for logs; circuit classification only reads response.error.

## Goals / Non-Goals

Use the current reader and recorder, with no owner-claim API dependency. Probe ownership and expiry remain outside this classifier.

## Decisions

Read the existing incomplete-reason helper only when response.error is absent, and recognize exactly stream_incomplete. Reusing the recorder preserves all eligibility and duplicate gates. Treating every incomplete reason as a failure would penalize neutral outcomes.

## Risks / Trade-offs

A broad reason fallback could change account health or logs. Reader regressions assert both alongside circuit evidence and downstream output. Existing large reader module remains cohesive; this seven-line classification belongs at its existing terminal classification branch and requires no new abstraction.

## Migration Plan

No data migration. Deploy or revert the classifier with its tests and specification.
