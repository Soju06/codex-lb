## 1. Specification

- [x] 1.1 Define frame-less HTTP bridge transport loss independently from
      replay safety and application progress.
- [x] 1.2 Preserve penalties for upstream-authored close frames and protocol or
      application errors.

## 2. Implementation

- [x] 2.1 Broaden the frame-less transport predicate to ignore response-event
      count.
- [x] 2.2 Remove output progress as a disqualifier for frame-less neutrality.
- [x] 2.3 Keep the windowed account signal restricted to genuinely eventless,
      output-free drops.
- [x] 2.4 Preserve positive transport-ending provenance and classify
      buffered-output durable operations as acknowledged.

## 3. Regression Coverage

- [x] 3.1 Cover frame-less drops after streamed events and buffered reasoning.
- [x] 3.2 Prove upstream-authored close frames still request an account penalty.
- [x] 3.3 Prove endpoint output remains valid SSE with one terminal failure, no
      transparent redispatch, and no account error write.
- [x] 3.4 Cover adapter-generated protocol errors and buffered-output durable
      operation classification.

## 4. Validation

- [x] 4.1 Run focused unit and integration tests.
- [x] 4.2 Run lint, type checks, and strict OpenSpec validation.
