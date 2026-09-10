## Context

The retry loop has a cancellation-deferring terminal finalizer plus inline post-terminal error-health writes. An ordinary health exception can enter the attempt's broad exception handler or escape an exception branch. See proposal.md for the reproduced symptom.

## Goals / Non-Goals

Contain account-error health exceptions after a terminal response. Keep existing settlement and cancellation ownership. Settlement failures, pre-terminal health decisions, and success-recording policy are outside this change.

## Decisions

Use one local helper around post-terminal `_handle_stream_error` calls. Catch `Exception`, log with exception information, and return. Do not catch `BaseException`, move health before settlement, or suppress errors across the entire attempt. The local helper retains access to request logging context without expanding the service interface. Keep this narrow edit in the existing retry module; splitting the attempt state machine is unrelated work.

## Risks / Trade-offs

The failed health update remains unpersisted. Logging exposes the failure while preserving the already delivered response. Existing cancellation and settlement-order regressions protect cleanup behavior.
