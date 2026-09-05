#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC_DIR="$ROOT/spec"
JAR="$SPEC_DIR/tla2tools.jar"
TLA_VERSION="v1.7.4"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/${TLA_VERSION}/tla2tools.jar"
TLA2TOOLS_SHA256="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
ACTIVE_TLC_METADIR=""

cleanup_tlc_metadir() {
  if [[ -n "$ACTIVE_TLC_METADIR" && "$ACTIVE_TLC_METADIR" == "$SPEC_DIR/states/"* ]]; then
    rm -rf -- "$ACTIVE_TLC_METADIR"
  fi
  ACTIVE_TLC_METADIR=""
}

trap cleanup_tlc_metadir EXIT

# Each weakening must fail with the invariant it was built to demonstrate.
# A "PROPERTY:<Name>" expectation means the weakening must keep every invariant
# and instead violate that one named temporal property; the property name is
# bound by asserting the config declares exactly that property.
declare -A EXPECTED_VIOLATION=(
  ["weak-ignore-owner-epoch.cfg"]="Inv1AnchorCurrent|Inv5SingleOwnerCAS"
  ["weak-ignore-anchor-lineage.cfg"]="Inv1AnchorCurrent"
  ["weak-single-shared-timeout.cfg"]="Inv2DeadlineOrdering"
  ["weak-skip-release-on-cancel.cfg"]="Inv3ReservationSettledExactlyOnce"
  ["weak-double-settle.cfg"]="Inv3ReservationSettledExactlyOnce"
  ["weak-stale-cache.cfg"]="Inv4FreshSnapshots"
  ["weak-stale-route-acquire.cfg"]="Inv4FreshSnapshots"
  ["weak-non-atomic-claim.cfg"]="Inv5SingleOwnerCAS"
  ["weak-misroute-producer.cfg"]="Inv6TerminalIsolation"
  ["weak-lost-waiter.cfg"]="Inv7GateAccounting"
  ["weak-shutdown-admit.cfg"]="Inv8ShutdownDrain"
  ["weak-leak-owner-on-terminal.cfg"]="Inv9TerminalOwnerReleased"
  ["weak-popped-not-finalized.cfg"]="Inv3ReservationSettledExactlyOnce"
  ["weak-cross-account-anchor.cfg"]="Inv10AnchorAccountOwnership"
  ["weak-conflated-timers.cfg"]="Inv11PreResponseBudget"
  ["weak-unbounded-backoff.cfg"]="PROPERTY:TearEventuallyRecovers"
)

sha256() {
  sha256sum "$1" | awk '{print $1}'
}

download_tlc() {
  if [[ -s "$JAR" ]]; then
    local existing_sha
    existing_sha="$(sha256 "$JAR")"
    if [[ "$existing_sha" == "$TLA2TOOLS_SHA256" ]]; then
      return
    fi
    echo "Existing tla2tools.jar sha256 mismatch; re-downloading." >&2
  fi

  local tmp_dir tmp_jar downloaded_sha
  tmp_dir="$(mktemp -d)"
  tmp_jar="$tmp_dir/tla2tools.jar"
  echo "Downloading tla2tools.jar ${TLA_VERSION} from GitHub releases..."
  curl -L --fail --retry 3 -o "$tmp_jar" "$TLA_URL"
  downloaded_sha="$(sha256 "$tmp_jar")"
  if [[ "$downloaded_sha" != "$TLA2TOOLS_SHA256" ]]; then
    rm -rf "$tmp_dir"
    echo "sha256 mismatch for tla2tools.jar: expected ${TLA2TOOLS_SHA256}, got ${downloaded_sha}" >&2
    exit 1
  fi
  mv "$tmp_jar" "$JAR"
  rmdir "$tmp_dir"
}

# Wall-clock budget for the full model only.  The weakenings all terminate in
# seconds because TLC stops at the first counterexample; the full model has no
# counterexample to stop at and its state space is far larger than a reviewer
# will sit through.  0 disables the budget and runs to exhaustion.
FULL_TIMEOUT_SECONDS="${CODEX_LB_TLC_FULL_TIMEOUT_SECONDS:-1800}"

run_tlc() {
  local cfg="$1"
  local out="$2"
  local budget="${3:-0}"
  local status=0
  local -a launcher=()
  if [[ "$budget" != "0" ]]; then
    # SIGINT rather than SIGTERM: TLC installs a handler that prints the
    # progress statistics we parse below before exiting.
    launcher=(timeout --signal=INT --kill-after=60 "$budget")
  fi
  mkdir -p "$SPEC_DIR/states"
  ACTIVE_TLC_METADIR="$(mktemp -d "$SPEC_DIR/states/run.XXXXXX")"
  # Pin the JVM locale: TLC groups the digits of its state counts with the
  # platform separator, and the parsers below expect one fixed grouping.
  "${launcher[@]}" java -XX:+UseParallelGC \
    -Duser.language=en -Duser.country=US \
    -jar "$JAR" \
    -workers auto \
    -metadir "$ACTIVE_TLC_METADIR" \
    -config "$cfg" \
    "$SPEC_DIR/CoreOwnership.tla" >"$out" 2>&1 || status=$?
  cleanup_tlc_metadir
  return "$status"
}

search_depth() {
  local out="$1"
  grep -oE 'Progress\(([0-9]+)\)' "$out" \
    | tail -n 1 \
    | sed -E 's/^Progress\(([0-9]+)\)$/\1/' || true
}

# Distinct-state count from the last periodic progress report.  Interrupting
# TLC skips the final summary line that ``state_count`` reads.
progress_state_count() {
  local out="$1"
  grep -E '^Progress\([0-9]+\)' "$out" \
    | tail -n 1 \
    | grep -oE '[0-9,]+ distinct states found' \
    | sed -E 's/ distinct states found$//' \
    | tr -d ',' || true
}

state_count() {
  local out="$1"
  grep -E 'states generated, [0-9,]+ distinct states found' "$out" \
    | tail -n 1 \
    | sed -E 's/^.*states generated, ([0-9,]+) distinct states found.*$/\1/' \
    | tr -d ',' || true
}

expect_full_pass() {
  local out="$SPEC_DIR/.tlc-full.out"
  local status=0
  echo "== Full model =="
  run_tlc "$SPEC_DIR/CoreOwnership.cfg" "$out" "$FULL_TIMEOUT_SECONDS" || status=$?

  # A counterexample fails the run whether or not the budget expired: TLC has
  # already printed it, and a violation found early is still a violation.
  if grep -qE 'Error:|Deadlock reached' "$out"; then
    cat "$out"
    echo "Full model reported an error/deadlock." >&2
    exit 1
  fi

  local distinct depth
  distinct="$(state_count "$out")"
  if [[ -z "$distinct" ]]; then
    distinct="$(progress_state_count "$out")"
  fi
  depth="$(search_depth "$out")"

  # 124/130 are timeout(1) reporting that the budget expired.
  if [[ "$status" == "124" || "$status" == "130" ]]; then
    if [[ -z "$distinct" ]]; then
      cat "$out"
      echo "Full model produced no progress statistics within ${FULL_TIMEOUT_SECONDS}s." >&2
      exit 1
    fi
    echo "PARTIAL full: no violation through depth ${depth:-?} after ${FULL_TIMEOUT_SECONDS}s" \
      "(distinct states=${distinct}); the state space was NOT exhausted."
    echo "  Set CODEX_LB_TLC_FULL_TIMEOUT_SECONDS=0 to run the full model to exhaustion."
    return
  fi

  if [[ "$status" != "0" ]]; then
    cat "$out"
    echo "Full model failed; expected zero violations and no deadlock." >&2
    exit 1
  fi
  if [[ -z "$distinct" ]]; then
    cat "$out"
    echo "Could not parse full-model state count." >&2
    exit 1
  fi
  echo "PASS full: distinct states=${distinct}; zero violations; deadlock checking enabled."
}

expect_weakening_fails() {
  local cfg="$1"
  local cfg_name label expected out rc distinct violation
  cfg_name="$(basename "$cfg")"
  label="${cfg_name%.cfg}"
  expected="${EXPECTED_VIOLATION[$cfg_name]:-}"
  out="$SPEC_DIR/.tlc-${label}.out"
  echo "== Weakening ${label} =="
  if [[ -z "$expected" ]]; then
    echo "No expected violation mapping for ${cfg_name}." >&2
    exit 1
  fi

  set +e
  run_tlc "$cfg" "$out"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    cat "$out"
    echo "Weakening ${label} passed; expected ${expected} counterexample." >&2
    exit 1
  fi

  if [[ "$expected" == PROPERTY:* ]]; then
    local property declared
    property="${expected#PROPERTY:}"
    declared="$(sed -n '/^PROPERTY/,$p' "$cfg" | tail -n +2 | tr -d '[:space:]')"
    if [[ "$declared" != "$property" ]]; then
      echo "Weakening ${label} must declare exactly PROPERTY ${property}; found '${declared}'." >&2
      exit 1
    fi
    if grep -qE 'Error: Invariant [A-Za-z0-9]+ is violated\.' "$out"; then
      cat "$out"
      echo "Weakening ${label} violated an invariant; expected only ${property}." >&2
      exit 1
    fi
    if ! grep -q 'Error: Temporal properties were violated\.' "$out"; then
      cat "$out"
      echo "Weakening ${label} failed without a temporal-property violation." >&2
      exit 1
    fi
    if ! grep -q 'Error: The following behavior constitutes a counter-example:' "$out"; then
      cat "$out"
      echo "Weakening ${label} failed without a TLC counterexample trace." >&2
      exit 1
    fi
    distinct="$(state_count "$out")"
    if [[ -z "$distinct" ]]; then
      cat "$out"
      echo "Could not parse weakening ${label} state count." >&2
      exit 1
    fi
    echo "COUNTEREXAMPLE ${label}: Error: Temporal property ${property} is violated.; distinct states=${distinct}."
    return
  fi

  if ! grep -q 'Error: The behavior up to this point is:' "$out"; then
    cat "$out"
    echo "Weakening ${label} failed without a TLC counterexample trace." >&2
    exit 1
  fi
  if ! grep -qE "Error: Invariant (${expected}) is violated\\." "$out"; then
    cat "$out"
    echo "Weakening ${label} failed through an unexpected violation; expected ${expected}." >&2
    exit 1
  fi
  distinct="$(state_count "$out")"
  if [[ -z "$distinct" ]]; then
    cat "$out"
    echo "Could not parse weakening ${label} state count." >&2
    exit 1
  fi
  violation="$(grep -E "Error: Invariant (${expected}) is violated\\." "$out" | head -n 1)"
  echo "COUNTEREXAMPLE ${label}: ${violation}; distinct states=${distinct}."
}

main() {
  download_tlc
  expect_full_pass

  local failures=0 cfg
  for cfg in "$SPEC_DIR"/weak-*.cfg; do
    expect_weakening_fails "$cfg"
    failures=$((failures + 1))
  done

  if [[ "$failures" -ne "${#EXPECTED_VIOLATION[@]}" ]]; then
    echo "Checked ${failures} weakenings; expected ${#EXPECTED_VIOLATION[@]} mapped weakenings." >&2
    exit 1
  fi
  echo "PASS weakenings: ${failures} mapped counterexample-producing negative controls."
}

main "$@"
