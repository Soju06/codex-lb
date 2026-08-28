# Tasks

## Step 1: OpenSpec

- [x] Add and validate the proposal, design, context, and capability deltas.
- [x] Confirm the tri-state contract with the maintainer before implementation.

## Step 2: Backend Contract

- [x] Add raw nullable override fields to settings service data and response schemas.
- [x] Thread explicit-null clear intent through settings API and service data.
- [x] Update the settings repository to clear, set, or preserve each override distinctly.
- [x] Preserve effective-value validation, optimistic version checks, cache invalidation, and audit tracing.

## Step 3: Frontend

- [x] Add override fields to frontend schemas and settings state.
- [x] Render empty inherit inputs with effective-value hints.
- [x] Submit only changed capacity fields, including explicit `null` clears.
- [x] Preserve capacity validation and localization.

## Step 4: Tests and Verification

- [x] Add backend unit and settings API round-trip tests for absent, null, and numeric states.
- [x] Add frontend schema, payload, and routing component tests.
- [x] Run focused tests, lint, type checks, and strict OpenSpec validation.
