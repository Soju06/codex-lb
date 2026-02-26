#!/usr/bin/env bash
set -euo pipefail

# codex-subagent.sh — Thin wrapper for Codex review via Claude Code
#
# Timeout bypass: run via Bash(run_in_background=true) in Claude Code.
# No sentinel files, no nohup, no IPC.
#
# Usage:
#   cat prompt.md | ./codex-subagent.sh --base main
#   echo "Review instructions" | ./codex-subagent.sh --uncommitted
#   ./codex-subagent.sh --base main              # no custom prompt
#
# Environment variables:
#   CODEX_REVIEW_MODEL     — Override Codex model (e.g., o3)
#   CODEX_REVIEW_REASONING — Override reasoning effort (e.g., high)

# --- Argument parsing ---
# All arguments are forwarded to `codex exec review`.
# Supported: --base <branch>, --uncommitted, --commit <sha>

CODEX_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base|--commit)
      CODEX_ARGS+=("$1" "$2"); shift 2 ;;
    --uncommitted)
      CODEX_ARGS+=("$1"); shift ;;
    *)
      echo "Warning: Unknown argument '$1' (passed through)" >&2
      CODEX_ARGS+=("$1"); shift ;;
  esac
done

CODEX_ARGS+=("--full-auto" "--ephemeral")

# --- Model overrides ---
if [[ -n "${CODEX_REVIEW_MODEL:-}" ]]; then
  CODEX_ARGS+=("-m" "$CODEX_REVIEW_MODEL")
fi
if [[ -n "${CODEX_REVIEW_REASONING:-}" ]]; then
  CODEX_ARGS+=("-c" "model_reasoning_effort=\"$CODEX_REVIEW_REASONING\"")
fi

# --- Execute ---
# Read prompt from stdin if available; otherwise run without custom prompt.
if [ -p /dev/stdin ]; then
  CODEX_ARGS+=("-")
  OUTPUT=$(codex exec review "${CODEX_ARGS[@]}" 2>&1)
else
  OUTPUT=$(codex exec review "${CODEX_ARGS[@]}" 2>&1)
fi
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo "Codex review failed (exit $EXIT_CODE):"
  echo "$OUTPUT"
  exit $EXIT_CODE
fi

# --- Parse output ---
# codex exec review output format:
#   ... (header, thinking, exec, collab blocks) ...
#   codex              <- last model response marker
#   <final review>     <- the actual review text
#   tokens used        <- footer
#   104,825            <- token count
#
# Strategy: reset buffer on each "^codex$", stop on "^tokens used$".
# Final buffer = last model response block.
PARSED=$(echo "$OUTPUT" | awk '
/^codex$/ { buf=""; capturing=1; next }
/^tokens used$/ { capturing=0; next }
capturing { buf = buf $0 "\n" }
END { printf "%s", buf }
')

if [ -n "$PARSED" ]; then
  echo "$PARSED"
else
  # Fallback: if parsing fails, return raw output
  echo "$OUTPUT"
fi

exit 0
