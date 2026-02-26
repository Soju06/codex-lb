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
HAS_DIFF_TARGET=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base|--commit)
      CODEX_ARGS+=("$1" "$2"); HAS_DIFF_TARGET=true; shift 2 ;;
    --uncommitted)
      CODEX_ARGS+=("$1"); HAS_DIFF_TARGET=true; shift ;;
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
# Codex CLI v0.105.0: --base/--commit/--uncommitted and [PROMPT] are mutually
# exclusive. When a diff target is specified, stdin prompt is ignored (Codex
# uses its built-in review logic). When no diff target, stdin prompt is passed.
if [ -p /dev/stdin ]; then
  if $HAS_DIFF_TARGET; then
    # Drain stdin and discard — cannot combine with --base/--commit/--uncommitted
    cat > /dev/null
    echo "Note: stdin prompt ignored (incompatible with --base/--commit/--uncommitted in Codex CLI)" >&2
  else
    CODEX_ARGS+=("-")
  fi
fi
# Temporarily disable errexit so we can capture the exit code and surface
# the diagnostic output instead of silently terminating.
set +e
OUTPUT=$(codex exec review "${CODEX_ARGS[@]}" 2>&1)
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -ne 0 ]; then
  echo "Codex review failed (exit $EXIT_CODE):"
  echo "$OUTPUT"
  exit $EXIT_CODE
fi

# --- Parse output ---
# codex exec review output format (v0.105.0):
#   ... (header, thinking, exec blocks) ...
#   codex              <- model response marker (may appear multiple times)
#   <response text>
#   codex              <- next model response marker (resets capture)
#   <final review>     <- the actual review text
#   <EOF>              <- no footer in current CLI version
#
# Strategy: reset buffer on each "^codex$" so only the last model
# response block survives. "^tokens used$" is kept as a guard for
# older/future CLI versions that may append a footer.
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
