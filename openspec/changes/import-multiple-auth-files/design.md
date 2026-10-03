## Context

The browser currently stores only `event.target.files[0]`, while the account import mutation and backend endpoint intentionally accept one file per request. The backend limit is security-sensitive and governed separately by `account-import`; this change therefore composes the existing request rather than widening its multipart shape.

## Goals / Non-Goals

**Goals:**

- Preserve every file selected in one browser picker interaction.
- Keep request fan-out sequential and deterministic.
- Make partial failure retry-safe by retaining only the failed and unattempted files.
- Keep existing per-file mutation success effects, including account-query refresh and success feedback.

**Non-Goals:**

- A bulk multipart endpoint or changes to server upload limits.
- Parallel account imports, rollback of accounts imported before a later failure, or aggregate server responses.
- Changes to account overwrite, identity, proxy-binding, usage-refresh, or audit behavior.

## Decisions

1. **Orchestrate the batch in the import dialog over the existing single-file callback.** The dialog already owns browser file selection and submit lifecycle, while the mutation owns one account import and its established side effects. This avoids a second API abstraction and preserves one request per file.

2. **Run imports sequentially in picker order.** Account import performs persistence and network work, and the picker has no useful concurrency bound. Sequential execution avoids unbounded fan-out, mutation-state races, and ambiguous ordering while retaining the backend's one-file resource budget.

3. **Remove each file from local pending state only after its request succeeds.** On failure, submission stops and the remaining state begins with the failed file. A remounted native input clears the browser's stale full selection, while a visible pending-file list remains the source of truth for retry.

4. **Keep failure presentation in the existing mutation path.** The mutation continues to populate the dialog error and toast. The dialog catches the rejected callback only to preserve its open state and pending queue; it does not replace or reinterpret the error.

## Risks / Trade-offs

- **A large selection can take time because requests are serialized.** → Disable file selection and submission for the complete batch and show the shrinking pending-file list as progress.
- **Some accounts may already be imported when a later file fails.** → Retain only the failed and unattempted files, preventing successful files from being submitted again on retry.
- **The browser file input cannot represent a filtered `FileList` portably.** → Remount it after partial failure and render pending filenames from React state.
