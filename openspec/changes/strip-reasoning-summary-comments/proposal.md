# Strip blank HTML comment placeholders from reasoning summaries

## Why

Recent Codex reasoning summary items can include a standalone `<!-- -->` markdown placeholder after the visible summary heading. Codex CLI renders summary text directly, so the placeholder becomes visible between tool calls.

## What changes

- Remove standalone blank HTML comment lines from Responses reasoning `summary_text` fields while proxying streamed and collected response items.
- Limit cleanup to reasoning summary text; assistant-visible content and non-empty comments are preserved.
