#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC_DIR="$ROOT/spec"
JAR="$SPEC_DIR/tla2tools.jar"
TLA_VERSION="v1.7.4"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/${TLA_VERSION}/tla2tools.jar"
TLA2TOOLS_SHA256="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"

# Each weakening must fail with the invariant it was built to demonstrate.
# A "PROPERTY:<Name>" expectation means the weakening must keep every invariant
# and instead violate that one named temporal property; the property name is
# bound by asserting the config declares exactly that property.
declare -A EXPECTED_VIOLATION=(
  ["weak-ignore-owner-epoch.cfg"]="Inv1AnchorCurrent"
  ["weak-single-shared-timeout.cfg"]="Inv2DeadlineOrdering"
  ["weak-skip-release-on-cancel.cfg"]="Inv3ReservationSettledExactlyOnce"
  ["weak-double-settle.cfg"]="Inv3ReservationSettledExactlyOnce"
  ["weak-stale-cache.cfg"]="Inv4FreshSnapshots"
  ["weak-non-atomic-claim.cfg"]="Inv5SingleOwnerCAS"
  ["weak-lost-waiter.cfg"]="Inv6TerminalIsolation|Inv7GateAccounting"
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

run_tlc() {
  local cfg="$1"
  local out="$2"
  java -XX:+UseParallelGC -jar "$JAR" \
    -workers auto \
    -config "$cfg" \
    "$SPEC_DIR/CoreOwnership.tla" >"$out" 2>&1
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
  echo "== Full model =="
  if ! run_tlc "$SPEC_DIR/CoreOwnership.cfg" "$out"; then
    cat "$out"
    echo "Full model failed; expected zero violations and no deadlock." >&2
    exit 1
  fi
  if grep -qE 'Error:|Deadlock reached' "$out"; then
    cat "$out"
    echo "Full model reported an error/deadlock despite zero exit." >&2
    exit 1
  fi
  local distinct
  distinct="$(state_count "$out")"
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
