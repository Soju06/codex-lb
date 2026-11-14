# Distinguish local receive limits from peer rejection

## Why
Local adapter receive overflow proves an oversized upstream response, not an oversized client request. The existing 1009 contract conflates these directions.

## Proposed change (NOT implemented)
Track close provenance and distinguish a received peer CLOSE1009 from locally generated receive-limit failure. Define a truthful server/upstream error envelope for local receive overflow, preserving no automatic replay after ambiguous image submission, account neutrality, reservation settlement, and stream finalization. The exact external code/status requires review before implementation.

## Scope and status
Proposal only: no runtime behavior, existing normative spec assertions, Native code or configuration changes. The existing 1009 contract remains unchanged pending a separately approved implementation and regression coverage. No OpenSpec CLI is installed; CLI validation has not been performed.
