#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compose/docker-compose.ha.yml"
ENV_FILE="$REPO_ROOT/.env.local"
STATE_DIR="$REPO_ROOT/.codex-lb-ha"
ACTIVE_FILE="$STATE_DIR/active-slot"
PHASE_FILE="$STATE_DIR/draining-slot"
LOCK_FILE="$STATE_DIR/deploy.lock"
HA_PROJECT="codex-lb-ha"
HA_NETWORK="${CODEX_LB_HA_NETWORK:-codex-lb_default}"
DEFAULT_DRAIN_SECONDS=300
READY_ATTEMPTS="${_CODEX_LB_HA_READY_ATTEMPTS:-60}"
PUBLIC_READY_ATTEMPTS="${_CODEX_LB_HA_PUBLIC_READY_ATTEMPTS:-30}"

log() {
    printf '[codex-lb-ha] %s\n' "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  scripts/deploy-compose-ha.sh bootstrap [drain-seconds]
  scripts/deploy-compose-ha.sh deploy [drain-seconds]
  scripts/deploy-compose-ha.sh rollback [drain-seconds]
  scripts/deploy-compose-ha.sh status

bootstrap performs the one-time migration from stock Compose to HAProxy.
deploy builds the inactive slot, cuts traffic over after readiness, and drains
the previous slot. rollback is available while the previous slot is draining.
EOF
}

select_docker() {
    if [[ -n "${_CODEX_LB_HA_DOCKER_BIN:-}" ]]; then
        DOCKER=("$_CODEX_LB_HA_DOCKER_BIN")
    elif docker info >/dev/null 2>&1; then
        DOCKER=(docker)
    elif command -v sudo >/dev/null && sudo -n docker info >/dev/null 2>&1; then
        DOCKER=(sudo -n docker)
    else
        die "Docker is unavailable; run as a Docker-enabled user or configure passwordless sudo for Docker"
    fi
}

compose() {
    "${DOCKER[@]}" compose --project-name "$HA_PROJECT" --file "$COMPOSE_FILE" "$@"
}

stock_compose() {
    "${DOCKER[@]}" compose --project-name codex-lb --file "$REPO_ROOT/docker-compose.prod.yml" "$@"
}

env_value() {
    local key="$1"
    local value
    value="$({
        awk -v wanted="$key" '
            /^[[:space:]]*(#|$)/ { next }
            {
                line = $0
                sub(/^[[:space:]]*export[[:space:]]+/, "", line)
                split_at = index(line, "=")
                if (split_at == 0) next
                name = substr(line, 1, split_at - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                if (name == wanted) {
                    print substr(line, split_at + 1)
                    exit
                }
            }
        ' "$ENV_FILE"
    } || true)"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    printf '%s' "$value"
}

validate_prerequisites() {
    [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE"

    local database_url leader_enabled encryption_key_file
    database_url="$(env_value CODEX_LB_DATABASE_URL)"
    leader_enabled="$(env_value CODEX_LB_LEADER_ELECTION_ENABLED)"
    encryption_key_file="$(env_value CODEX_LB_ENCRYPTION_KEY_FILE)"

    case "$database_url" in
        postgresql://*|postgresql+*://*) ;;
        *) die "CODEX_LB_DATABASE_URL must select shared PostgreSQL before blue/green overlap" ;;
    esac

    case "${leader_enabled,,}" in
        false|0|no|off) die "CODEX_LB_LEADER_ELECTION_ENABLED must remain enabled during overlap" ;;
    esac

    if [[ -n "$encryption_key_file" && "$encryption_key_file" != /var/lib/codex-lb/* ]]; then
        die "CODEX_LB_ENCRYPTION_KEY_FILE must be inside the shared /var/lib/codex-lb volume"
    fi

    compose config --quiet >/dev/null || die "HA Compose configuration is invalid"
}

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
    chmod 0755 "$STATE_DIR"
}

acquire_lock() {
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "another HA deployment command owns $LOCK_FILE"
}

release_lock() {
    flock -u 9
}

ensure_network() {
    if ! "${DOCKER[@]}" network inspect "$HA_NETWORK" >/dev/null 2>&1; then
        log "Creating external Compose network $HA_NETWORK"
        "${DOCKER[@]}" network create "$HA_NETWORK" >/dev/null
    fi
}

ensure_data_volume() {
    if ! "${DOCKER[@]}" volume inspect codex-lb-data >/dev/null 2>&1; then
        log "Creating shared application volume codex-lb-data"
        "${DOCKER[@]}" volume create codex-lb-data >/dev/null
    fi
}

slot_service() {
    printf 'server-%s' "$1"
}

other_slot() {
    case "$1" in
        blue) printf 'green' ;;
        green) printf 'blue' ;;
        *) die "invalid slot '$1'" ;;
    esac
}

read_active_slot() {
    [[ -f "$ACTIVE_FILE" ]] || die "HA topology is not bootstrapped; run '$0 bootstrap'"
    local slot
    slot="$(<"$ACTIVE_FILE")"
    [[ "$slot" == blue || "$slot" == green ]] || die "invalid active slot state in $ACTIVE_FILE"
    printf '%s' "$slot"
}

write_active_slot() {
    printf '%s\n' "$1" >"$ACTIVE_FILE"
}

container_id() {
    compose ps --quiet "$(slot_service "$1")"
}

container_health() {
    local id
    id="$(container_id "$1")"
    [[ -n "$id" ]] || return 1
    "${DOCKER[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id"
}

wait_slot_ready() {
    local slot="$1"
    local attempt health
    for ((attempt = 1; attempt <= READY_ATTEMPTS; attempt++)); do
        health="$(container_health "$slot" 2>/dev/null || true)"
        if [[ "$health" == healthy ]]; then
            if compose exec --no-TTY haproxy \
                wget -q -T 3 -O /dev/null "http://server-$slot:2455/health/ready"; then
                local proxy_status
                proxy_status="$(server_status "$slot")"
                if [[ "$proxy_status" == UP* || "$proxy_status" == DRAIN* ]]; then
                    return 0
                fi
            fi
        fi
        sleep 2
    done
    return 1
}

wait_slot_container_ready() {
    local slot="$1"
    local attempt health
    for ((attempt = 1; attempt <= READY_ATTEMPTS; attempt++)); do
        health="$(container_health "$slot" 2>/dev/null || true)"
        [[ "$health" == healthy ]] && return 0
        sleep 2
    done
    return 1
}

runtime_command() {
    local commands="$1"
    printf '%s\n\n' "$commands" | compose exec --no-TTY haproxy \
        sh -c 'nc -w 3 127.0.0.1 9999'
}

set_runtime_slots() {
    local ready_slot="$1"
    local other_state="$2"
    local other
    other="$(other_slot "$ready_slot")"
    case "$other_state" in
        drain|maint) ;;
        *) die "invalid HAProxy state '$other_state'" ;;
    esac
    # Make the candidate eligible before removing the predecessor so there is
    # never a no-ready-backend interval. Weight zero drains existing sessions.
    runtime_command "set server codex_lb_slots/$ready_slot weight 1" >/dev/null || return 1
    runtime_command "set server codex_lb_slots/$other weight 0" >/dev/null || return 1
}

server_status() {
    local slot="$1"
    runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { gsub(/\r/, "", $18); print $18; exit }
    '
}

server_weight() {
    local slot="$1"
    runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { gsub(/\r/, "", $19); print $19; exit }
    '
}

active_sessions() {
    local slot="$1"
    local count
    count="$(runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { print $5; exit }
    ')"
    [[ "$count" =~ ^[0-9]+$ ]] || count=0
    printf '%s' "$count"
}

snapshot_runtime_state() {
    local temporary
    temporary="$(mktemp "$STATE_DIR/server-state.tmp.XXXXXX")"
    if runtime_command 'show servers state' >"$temporary"; then
        chmod 0644 "$temporary"
        mv "$temporary" "$STATE_DIR/server-state"
    else
        rm -f "$temporary"
        return 1
    fi
}

verify_public_ready() {
    compose exec --no-TTY haproxy \
        wget -q -T 5 -O /dev/null http://127.0.0.1:2455/health/ready
}

wait_public_ready() {
    local attempt
    for ((attempt = 1; attempt <= PUBLIC_READY_ATTEMPTS; attempt++)); do
        verify_public_ready >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

validate_drain_seconds() {
    local seconds="$1"
    [[ "$seconds" =~ ^[0-9]+$ ]] && ((seconds > 0)) || die "drain-seconds must be a positive integer"
}

finish_drain() {
    local draining_slot="$1"
    local expected_active="$2"
    local drain_seconds="$3"
    local deadline=$((SECONDS + drain_seconds))
    local remaining=0

    while ((SECONDS < deadline)); do
        if [[ "$(read_active_slot)" != "$expected_active" ]]; then
            log "Drain ownership changed after rollback; leaving $draining_slot running"
            return 0
        fi
        remaining="$(active_sessions "$draining_slot")"
        ((remaining == 0)) && break
        sleep 2
    done

    flock -n 9 || die "another HA deployment command is finalizing the rollout"
    if [[ "$(read_active_slot)" != "$expected_active" ]]; then
        release_lock
        log "Active slot changed; leaving $draining_slot running"
        return 0
    fi

    remaining="$(active_sessions "$draining_slot")"
    if ((remaining > 0)); then
        log "Drain bound reached with $remaining HAProxy session(s) on $draining_slot"
    fi
    runtime_command "set server codex_lb_slots/$draining_slot weight 0" >/dev/null
    snapshot_runtime_state
    compose stop "$(slot_service "$draining_slot")"
    rm -f "$PHASE_FILE"
    release_lock
    log "Stopped drained $draining_slot slot; $expected_active remains active"
}

bootstrap() {
    local drain_seconds="$1"
    validate_drain_seconds "$drain_seconds"
    ensure_state_dir
    acquire_lock
    validate_prerequisites
    [[ ! -f "$ACTIVE_FILE" ]] || die "HA topology is already bootstrapped"
    ensure_network
    ensure_data_volume

    log "Building and starting initial blue slot"
    compose up --detach --build --no-deps server-blue
    wait_slot_container_ready blue || {
        compose stop server-blue >/dev/null 2>&1 || true
        die "initial blue slot did not reach strict readiness"
    }

    local stock_id
    stock_id="$(stock_compose ps --quiet server 2>/dev/null || true)"
    if [[ -n "$stock_id" ]]; then
        log "Stopping the stock port owner for the one-time HAProxy topology migration"
        stock_compose stop --timeout "$drain_seconds" server
    fi

    write_active_slot blue
    if ! compose up --detach --no-deps haproxy; then
        rm -f "$ACTIVE_FILE"
        [[ -z "$stock_id" ]] || stock_compose up --detach --no-deps server
        die "HAProxy failed to start; restored the stock server when present"
    fi
    if ! wait_public_ready; then
        compose stop haproxy >/dev/null 2>&1 || true
        rm -f "$ACTIVE_FILE"
        [[ -z "$stock_id" ]] || stock_compose up --detach --no-deps server
        die "public readiness failed during bootstrap; restored the stock server when present"
    fi
    snapshot_runtime_state
    release_lock
    log "HAProxy topology is ready with blue active"
}

deploy() {
    local drain_seconds="$1"
    validate_drain_seconds "$drain_seconds"
    ensure_state_dir
    acquire_lock
    validate_prerequisites
    [[ ! -f "$PHASE_FILE" ]] || die "a predecessor is still draining; inspect status or rollback first"

    local active candidate status active_weight candidate_weight
    active="$(read_active_slot)"
    candidate="$(other_slot "$active")"
    status="$(server_status "$active")"
    [[ "$status" == UP* ]] || die "recorded active slot $active is not ready in HAProxy (status: ${status:-unknown})"
    active_weight="$(server_weight "$active")"
    candidate_weight="$(server_weight "$candidate")"
    [[ "$active_weight" =~ ^[1-9][0-9]*$ && "$candidate_weight" == 0 ]] || \
        die "HAProxy weights disagree with active-slot state; refusing an ambiguous cutover"

    log "Building and starting inactive $candidate slot"
    if ! compose up --detach --build --no-deps "$(slot_service "$candidate")"; then
        compose stop "$(slot_service "$candidate")" >/dev/null 2>&1 || true
        die "candidate $candidate failed to build or start; $active remains active"
    fi
    if ! wait_slot_ready "$candidate"; then
        compose stop "$(slot_service "$candidate")" >/dev/null 2>&1 || true
        die "candidate $candidate did not reach strict readiness; $active remains active"
    fi

    log "Switching new connections from $active to $candidate"
    if ! set_runtime_slots "$candidate" drain; then
        runtime_command "set server codex_lb_slots/$candidate weight 0" >/dev/null 2>&1 || true
        compose stop "$(slot_service "$candidate")" >/dev/null 2>&1 || true
        die "HAProxy rejected the cutover; $active remains active"
    fi
    if ! wait_public_ready; then
        set_runtime_slots "$active" maint || die "public verification failed and automatic HAProxy rollback also failed"
        snapshot_runtime_state
        compose stop "$(slot_service "$candidate")" >/dev/null 2>&1 || true
        die "public verification failed; traffic returned to $active"
    fi

    write_active_slot "$candidate"
    printf '%s\n' "$active" >"$PHASE_FILE"
    snapshot_runtime_state
    release_lock
    log "$candidate is active; draining $active for at most $drain_seconds seconds"
    finish_drain "$active" "$candidate" "$drain_seconds"
}

rollback() {
    local drain_seconds="$1"
    validate_drain_seconds "$drain_seconds"
    ensure_state_dir
    acquire_lock
    validate_prerequisites
    [[ -f "$PHASE_FILE" ]] || die "rollback is only available while a predecessor is draining"

    local current predecessor
    current="$(read_active_slot)"
    predecessor="$(<"$PHASE_FILE")"
    [[ "$predecessor" == blue || "$predecessor" == green ]] || die "invalid draining-slot state"
    [[ "$(container_health "$predecessor" 2>/dev/null || true)" == healthy ]] || \
        die "predecessor $predecessor is no longer healthy; refusing rollback"

    log "Returning new connections from $current to $predecessor"
    set_runtime_slots "$predecessor" drain
    if ! wait_public_ready; then
        set_runtime_slots "$current" drain || true
        die "rollback public verification failed; restored $current as active"
    fi
    write_active_slot "$predecessor"
    printf '%s\n' "$current" >"$PHASE_FILE"
    snapshot_runtime_state
    release_lock
    log "$predecessor is active again; draining $current"
    finish_drain "$current" "$predecessor" "$drain_seconds"
}

status() {
    ensure_state_dir
    local active="uninitialized"
    [[ ! -f "$ACTIVE_FILE" ]] || active="$(read_active_slot)"
    printf 'Active slot: %s\n' "$active"
    if [[ -f "$PHASE_FILE" ]]; then
        printf 'Draining slot: %s\n' "$(<"$PHASE_FILE")"
    else
        printf 'Draining slot: none\n'
    fi
    compose ps
    if compose ps --quiet haproxy | grep -q .; then
        runtime_command 'show stat' | awk -F, '
            $1 == "codex_lb_slots" && ($2 == "blue" || $2 == "green") {
                printf "%s: status=%s weight=%s sessions=%s\n", $2, $18, $19, $5
            }
        '
    fi
}

main() {
    local command="${1:-}"
    local drain_seconds="${2:-$DEFAULT_DRAIN_SECONDS}"
    case "$command" in
        bootstrap|deploy|rollback)
            [[ $# -le 2 ]] || { usage >&2; exit 2; }
            select_docker
            "$command" "$drain_seconds"
            ;;
        status)
            [[ $# -eq 1 ]] || { usage >&2; exit 2; }
            select_docker
            status
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
