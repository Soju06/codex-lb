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
HA_EDGE_NETWORK="codex-lb-ha-edge"
DEFAULT_DRAIN_SECONDS=300
READY_ATTEMPTS="${_CODEX_LB_HA_READY_ATTEMPTS:-60}"
PUBLIC_READY_ATTEMPTS="${_CODEX_LB_HA_PUBLIC_READY_ATTEMPTS:-30}"
BASE_SLOTS=(blue green amber)
MINIMUM_ELIGIBLE=3
CONFIG_HASH_FILE="$STATE_DIR/haproxy-config.sha256"

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

bootstrap performs the one-time migration from stock Compose to three active
HAProxy backends. deploy adds a temporary surge backend, replaces blue, green and
amber one at a time, then retires the surge backend. rollback cancels only the
base-slot drain currently reported by status.
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
        *) die "CODEX_LB_DATABASE_URL must select shared PostgreSQL before multi-backend overlap" ;;
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

read_topology_state() {
    [[ -f "$ACTIVE_FILE" ]] || die "HA topology is not bootstrapped; run '$0 bootstrap'"
    local state
    state="$(<"$ACTIVE_FILE")"
    case "$state" in
        blue|green|blue,green|blue,green,amber) ;;
        *) die "invalid serving topology state in $ACTIVE_FILE" ;;
    esac
    printf '%s' "$state"
}

write_active_active_state() {
    printf 'blue,green,amber\n' >"$ACTIVE_FILE"
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

server_status() {
    local slot="$1"
    runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { gsub(/\r/, "", $18); print $18; exit }
    '
}

server_exists() {
    [[ -n "$(server_status "$1")" ]]
}

server_weight() {
    local slot="$1"
    runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { gsub(/\r/, "", $19); print $19; exit }
    '
}

server_state_field() {
    local slot="$1"
    local field="$2"
    runtime_command 'show servers state' | awk -v slot="$slot" -v field="$field" '
        $2 == "codex_lb_slots" && $4 == slot { gsub(/\r/, "", $field); print $field; exit }
    '
}

set_slot_weight() {
    local slot="$1"
    local weight="$2"
    local attempt observed
    runtime_command "set server codex_lb_slots/$slot weight $weight" >/dev/null || return 1
    for ((attempt = 1; attempt <= 10; attempt++)); do
        observed="$(server_weight "$slot")"
        if [[ "$weight" == 0 && "$observed" == 0 ]]; then
            return 0
        fi
        if [[ "$weight" != 0 && "$observed" =~ ^[1-9][0-9]*$ ]]; then
            return 0
        fi
        sleep 1
    done
    return 1
}

eligible_slot_count() {
    runtime_command 'show stat' | awk -F, '
        $1 == "codex_lb_slots" && ($2 == "blue" || $2 == "green" || $2 == "amber" || $2 == "surge") {
            gsub(/\r/, "", $18)
            gsub(/\r/, "", $19)
            if ($18 ~ /^UP/ && ($19 + 0) > 0) count++
        }
        END { print count + 0 }
    '
}

ensure_minimum_eligible() {
    local expected="$1"
    local actual
    actual="$(eligible_slot_count)"
    ((actual >= expected)) || {
        log "Expected at least $expected eligible HAProxy backends, found $actual"
        return 1
    }
}

ensure_runtime_server() {
    local slot="$1"
    local id address fqdn output server_id
    case "$slot" in blue) server_id=1 ;; green) server_id=2 ;; surge) server_id=3 ;; amber) server_id=4 ;; *) return 1 ;; esac
    id="$(container_id "$slot")"
    [[ -n "$id" ]] || return 1
    address="$("${DOCKER[@]}" inspect --format \
        "{{with index .NetworkSettings.Networks \"$HA_EDGE_NETWORK\"}}{{.IPAddress}}{{end}}" "$id")"
    [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || {
        log "Could not resolve the $slot container address on $HA_EDGE_NETWORK"
        return 1
    }

    if server_exists "$slot"; then
        fqdn="$(server_state_field "$slot" 18)"
        if [[ "$fqdn" == - || -z "$fqdn" ]]; then
            # A server added to a legacy HAProxy process has no DNS name. Its
            # container IP must be refreshed every time $slot is recreated.
            runtime_command "set server codex_lb_slots/$slot addr $address port 2455" >/dev/null || return 1
            [[ "$(server_state_field "$slot" 5)" == "$address" ]] || return 1
        fi
    else
        # HAProxy may still be the pre-$slot process during the first rollout
        # after upgrading this repository. Register the checked-in static
        # member without restarting the public frontend.
        output="$(runtime_command \
            "add server codex_lb_slots/$slot $address:2455 id $server_id check port 2455 inter 2s fall 2 rise 2 weight 0")" || return 1
        if [[ "$output" != *"New server registered"* ]] && ! server_exists "$slot"; then
            log "HAProxy rejected runtime $slot registration: ${output:-no response}"
            return 1
        fi
    fi
    runtime_command "enable health codex_lb_slots/$slot" >/dev/null || return 1
    runtime_command "enable server codex_lb_slots/$slot" >/dev/null || return 1
}

active_sessions() {
    local slot="$1"
    local count
    count="$(runtime_command 'show stat' | awk -F, -v slot="$slot" '
        $1 == "codex_lb_slots" && $2 == slot { print $5; exit }
    ')"
    [[ "$count" =~ ^[0-9]+$ ]] || return 1
    # A graceful reload leaves established sessions in old workers, outside
    # the new worker's stats. Conservatively use the full drain bound while
    # any old worker exists instead of treating its invisible sessions as zero.
    local workers
    workers="$(compose exec --no-TTY haproxy sh -c 'wc -w < /proc/1/task/1/children')" || return 1
    [[ "$workers" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]] || return 1
    if ((workers > 1 && count == 0)); then count=1; fi
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

write_phase() {
    local action="$1"
    local slot="$2"
    local remaining="${3:-}"
    printf '%s:%s:%s\n' "$action" "$slot" "$remaining" >"$PHASE_FILE"
}

slot_is_eligible() {
    local slot="$1"
    local status weight
    status="$(server_status "$slot")"
    weight="$(server_weight "$slot")"
    [[ "$status" == UP* && "$weight" =~ ^[1-9][0-9]*$ ]]
}

base_slots_are_eligible() {
    local slot
    for slot in "${BASE_SLOTS[@]}"; do
        slot_is_eligible "$slot" || return 1
    done
}

wait_for_sessions() {
    local slot="$1"
    local drain_seconds="$2"
    local expected_phase="$3"
    local deadline=$((SECONDS + drain_seconds))
    local remaining=0

    while ((SECONDS < deadline)); do
        if [[ ! -f "$PHASE_FILE" || "$(<"$PHASE_FILE")" != "$expected_phase" ]]; then
            log "Drain ownership changed; leaving $slot running"
            return 2
        fi
        remaining="$(active_sessions "$slot")" || return 1
        ((remaining == 0)) && return 0
        sleep 2
    done

    remaining="$(active_sessions "$slot")" || return 1
    if ((remaining > 0)); then
        log "Drain bound reached with $remaining HAProxy session(s) on $slot"
    fi
}

start_base_slot() {
    local slot="$1"
    # A previous attempt may have admitted the candidate before persistence
    # failed. Recovery must not force-recreate that now-serving container.
    if slot_is_eligible "$slot"; then
        local image candidate_image
        image="$("${DOCKER[@]}" inspect --format '{{.Image}}' "$(container_id "$slot")")" || return 1
        candidate_image="$("${DOCKER[@]}" inspect --format '{{.Image}}' "$(container_id surge)")" || return 1
        [[ -n "$image" && "$image" == "$candidate_image" ]] || {
            log "Serving $slot does not match the retained candidate; refusing un-drained recreation"
            return 1
        }
        wait_slot_ready "$slot" && wait_public_ready && snapshot_runtime_state
        return $?
    fi
    log "Starting replacement $slot slot from the image built for this rollout"
    compose up --detach --no-build --no-deps --force-recreate "$(slot_service "$slot")" || return 1
    wait_slot_container_ready "$slot" || return 1
    ensure_runtime_server "$slot" || return 1
    wait_slot_ready "$slot" || return 1
    set_slot_weight "$slot" 1 || return 1
    if ! wait_public_ready || ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE"; then
        set_slot_weight "$slot" 0 >/dev/null 2>&1 || true
        return 1
    fi
    snapshot_runtime_state || return 1
}

roll_base_slot() {
    local slot="$1"
    local remaining_slot="$2"
    local drain_seconds="$3"

    if ! slot_is_eligible "$slot"; then
        write_phase replacing "$slot" "$remaining_slot"
        start_base_slot "$slot" || {
            log "Replacement $slot did not reach strict readiness; existing eligible backends remain in service"
            return 1
        }
        rm -f "$PHASE_FILE"
        return 0
    fi

    ensure_minimum_eligible "$((MINIMUM_ELIGIBLE + 1))" || {
        log "Refusing to drain $slot without $MINIMUM_ELIGIBLE other eligible backends"
        return 1
    }

    local drain_phase="draining:$slot:$remaining_slot"
    write_phase draining "$slot" "$remaining_slot"
    log "Removing new connections from $slot before replacement"
    if ! set_slot_weight "$slot" 0; then
        rm -f "$PHASE_FILE"
        log "HAProxy rejected the drain for $slot"
        return 1
    fi
    if ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE" || ! wait_public_ready; then
        set_slot_weight "$slot" 1 >/dev/null 2>&1 || true
        snapshot_runtime_state || true
        rm -f "$PHASE_FILE"
        log "Drain verification failed; restored $slot"
        return 1
    fi
    if ! snapshot_runtime_state; then
        set_slot_weight "$slot" 1 >/dev/null 2>&1 || true
        rm -f "$PHASE_FILE"
        log "Could not persist the drain state; restored $slot"
        return 1
    fi
    release_lock
    log "Draining $slot for at most $drain_seconds seconds"

    local drain_result=0
    wait_for_sessions "$slot" "$drain_seconds" "$drain_phase" || drain_result=$?
    if ((drain_result != 0)); then
        log "Drain stopped before replacement; leaving $slot running"
        return "$drain_result"
    fi
    flock 9 || die "could not reacquire the HA deployment lock"
    if [[ ! -f "$PHASE_FILE" || "$(<"$PHASE_FILE")" != "$drain_phase" ]]; then
        release_lock
        log "Drain ownership changed; leaving $slot running"
        return 2
    fi

    write_phase replacing "$slot" "$remaining_slot"
    compose stop "$(slot_service "$slot")" || return 1
    if ! start_base_slot "$slot"; then
        log "Replacement $slot failed; surge and the other base slot remain eligible"
        return 1
    fi
    rm -f "$PHASE_FILE"
    log "Replacement $slot is active"
}

roll_slots() {
    local sequence="$1" drain_seconds="$2" slot remaining
    local -a slots
    IFS=, read -r -a slots <<<"$sequence"
    for slot in "${slots[@]}"; do
        [[ "$slot" == blue || "$slot" == green || "$slot" == amber ]] || return 1
        remaining="${sequence#"$slot"}"
        remaining="${remaining#,}"
        roll_base_slot "$slot" "$remaining" "$drain_seconds" || return $?
        sequence="$remaining"
    done
}

reload_proxy_config() {
    local expected mounted previous observed attempt
    expected="$(sha256sum "$REPO_ROOT/deploy/compose/haproxy.cfg" | awk '{print $1}')"
    [[ ! -f "$CONFIG_HASH_FILE" || "$(<"$CONFIG_HASH_FILE")" != "$expected" ]] || return 0
    mounted="$(compose exec --no-TTY haproxy sha256sum /usr/local/etc/haproxy/haproxy.cfg | awk '{print $1}')"
    [[ "$mounted" == "$expected" ]] || {
        log "Mounted HAProxy config differs from the checkout; refusing reload"
        return 1
    }
    compose exec --no-TTY haproxy haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg || return 1
    snapshot_runtime_state || return 1
    previous="$(runtime_command 'show info' | awk '/^Pid:/ {print $2}')"
    [[ "$previous" =~ ^[0-9]+$ ]] || return 1
    log "Gracefully reloading HAProxy; existing workers retain their connections"
    "${DOCKER[@]}" kill --signal USR2 "$(compose ps --quiet haproxy)" >/dev/null || return 1
    for ((attempt = 1; attempt <= PUBLIC_READY_ATTEMPTS; attempt++)); do
        observed="$(runtime_command 'show info' | awk '/^Pid:/ {print $2}')" || observed=""
        if [[ "$observed" =~ ^[0-9]+$ && "$observed" != "$previous" ]] && base_slots_are_eligible && wait_public_ready; then
            printf '%s\n' "$expected" >"$CONFIG_HASH_FILE"
            return 0
        fi
        sleep 1
    done
    log "New HAProxy worker did not pass readiness; leaving serving backends untouched"
    return 1
}

cleanup_uncommitted_surge() {
    if server_exists surge; then
        set_slot_weight surge 0 >/dev/null 2>&1 || true
        snapshot_runtime_state >/dev/null 2>&1 || true
    fi
    compose stop server-surge >/dev/null 2>&1 || true
}

activate_surge() {
    if slot_is_eligible surge; then
        log "Surge is already serving; refusing to recreate it without a recorded recovery phase"
        return 1
    fi
    log "Building and starting the temporary surge backend"
    if ! compose up --detach --build --no-deps server-surge; then
        cleanup_uncommitted_surge
        return 1
    fi
    if ! wait_slot_container_ready surge || ! ensure_runtime_server surge || ! wait_slot_ready surge; then
        cleanup_uncommitted_surge
        return 1
    fi
    if ! set_slot_weight surge 1 || ! wait_public_ready || ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE"; then
        cleanup_uncommitted_surge
        return 1
    fi
    if ! snapshot_runtime_state; then
        cleanup_uncommitted_surge
        return 1
    fi
    log "Surge backend is eligible; rollout capacity floor is $MINIMUM_ELIGIBLE"
}

retire_surge() {
    local drain_seconds="$1"
    base_slots_are_eligible || {
        log "Refusing to retire surge before all base backends are eligible"
        return 1
    }

    if ! server_exists surge; then
        rm -f "$PHASE_FILE"
        write_active_active_state
        release_lock
        return 0
    fi

    local retire_phase="retiring:surge:"
    write_phase retiring surge
    if ! set_slot_weight surge 0; then
        rm -f "$PHASE_FILE"
        return 1
    fi
    if ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE" || ! wait_public_ready; then
        set_slot_weight surge 1 >/dev/null 2>&1 || true
        rm -f "$PHASE_FILE"
        log "Surge retirement verification failed; restored surge traffic"
        return 1
    fi
    if ! snapshot_runtime_state; then
        set_slot_weight surge 1 >/dev/null 2>&1 || true
        rm -f "$PHASE_FILE"
        log "Could not persist surge drain state; restored surge traffic"
        return 1
    fi
    release_lock
    log "Draining the temporary surge backend for at most $drain_seconds seconds"

    local drain_result=0
    wait_for_sessions surge "$drain_seconds" "$retire_phase" || drain_result=$?
    ((drain_result == 0)) || return "$drain_result"
    flock 9 || die "could not reacquire the HA deployment lock"
    if [[ ! -f "$PHASE_FILE" || "$(<"$PHASE_FILE")" != "$retire_phase" ]]; then
        release_lock
        return 2
    fi
    compose stop server-surge
    if ! snapshot_runtime_state; then
        release_lock
        log "Surge stopped, but final HAProxy state could not be persisted"
        return 1
    fi
    rm -f "$PHASE_FILE"
    write_active_active_state
    release_lock
    log "Rollout complete: blue, green and amber are active; surge is stopped"
}

restore_stock_after_bootstrap_failure() {
    local stock_id="$1"
    compose stop haproxy server-blue server-green server-amber >/dev/null 2>&1 || true
    rm -f "$ACTIVE_FILE" "$PHASE_FILE"
    [[ -z "$stock_id" ]] || stock_compose up --detach --no-deps server
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

    log "Building and starting the initial blue, green and amber backends"
    if ! compose up --detach --build --no-deps server-blue server-green server-amber; then
        compose stop server-blue server-green server-amber >/dev/null 2>&1 || true
        die "initial active-active backends failed to build or start"
    fi
    if ! wait_slot_container_ready blue || ! wait_slot_container_ready green || ! wait_slot_container_ready amber; then
        compose stop server-blue server-green server-amber >/dev/null 2>&1 || true
        die "initial active-active backends did not reach strict readiness"
    fi

    local stock_id
    stock_id="$(stock_compose ps --quiet server 2>/dev/null || true)"
    if [[ -n "$stock_id" ]]; then
        log "Stopping the stock port owner for the one-time HAProxy topology migration"
        stock_compose stop --timeout "$drain_seconds" server
    fi

    if ! compose up --detach --no-deps haproxy; then
        restore_stock_after_bootstrap_failure "$stock_id"
        die "HAProxy failed to start; restored the stock server when present"
    fi
    if ! wait_slot_ready blue || ! wait_slot_ready green || ! wait_slot_ready amber || \
        ! set_slot_weight blue 1 || ! set_slot_weight green 1 || ! set_slot_weight amber 1 || \
        ! wait_public_ready || ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE"; then
        restore_stock_after_bootstrap_failure "$stock_id"
        die "active-active public readiness failed during bootstrap; restored the stock server when present"
    fi
    server_exists surge && set_slot_weight surge 0 >/dev/null 2>&1 || true
    compose stop server-surge >/dev/null 2>&1 || true
    write_active_active_state
    snapshot_runtime_state
    release_lock
    log "HAProxy topology is ready with blue, green and amber active"
}

resume_interrupted_rollout() {
    local phase="$1"
    local drain_seconds="$2"
    local action slot remaining

    if [[ "$phase" == blue || "$phase" == green ]]; then
        die "legacy drain for $phase is still open; run rollback before another deploy"
    fi
    IFS=: read -r action slot remaining <<<"$phase"
    case "$action:$slot" in
        retained:surge)
            slot_is_eligible surge || die "retained candidate surge is no longer eligible"
            log "Completing the retained candidate rollout without rebuilding surge"
            roll_slots "${remaining:-blue,green,amber}" "$drain_seconds" || return $?
            write_phase reloading haproxy
            reload_proxy_config || return 1
            retire_surge "$drain_seconds"
            return $?
            ;;
        draining:blue|draining:green|draining:amber)
            die "$slot is still draining; run rollback before another deploy"
            ;;
        replacing:blue|replacing:green|replacing:amber)
            slot_is_eligible surge || die "cannot resume $slot replacement without an eligible surge backend"
            log "Resuming interrupted replacement of $slot"
            start_base_slot "$slot" || die "could not resume replacement of $slot"
            rm -f "$PHASE_FILE"
            if [[ -n "$remaining" ]]; then
                local result=0
                roll_slots "$remaining" "$drain_seconds" || result=$?
                ((result == 0)) || return "$result"
            fi
            # Old two-base phase files cannot name the new third member.
            if ! slot_is_eligible amber; then
                roll_slots amber "$drain_seconds" || return $?
            fi
            write_phase reloading haproxy
            reload_proxy_config || return 1
            retire_surge "$drain_seconds"
            return $?
            ;;
        reloading:haproxy)
            reload_proxy_config || return 1
            retire_surge "$drain_seconds"
            return $?
            ;;
        retiring:surge)
            log "Resuming interrupted surge retirement"
            if ! slot_is_eligible amber; then
                set_slot_weight surge 1 || return 1
                roll_slots amber "$drain_seconds" || return $?
                write_phase reloading haproxy
                reload_proxy_config || return 1
            fi
            retire_surge "$drain_seconds"
            return $?
            ;;
        *) die "invalid rollout phase in $PHASE_FILE" ;;
    esac
}

deploy() {
    local drain_seconds="$1"
    validate_drain_seconds "$drain_seconds"
    ensure_state_dir
    acquire_lock
    validate_prerequisites
    local topology
    topology="$(read_topology_state)"
    [[ "$topology" == blue,green,amber ]] || MINIMUM_ELIGIBLE=2

    if [[ -f "$PHASE_FILE" ]]; then
        resume_interrupted_rollout "$(<"$PHASE_FILE")" "$drain_seconds"
        return $?
    fi

    if [[ "$topology" == blue ]]; then
        slot_is_eligible blue || die "legacy active slot blue is not eligible"
    elif [[ "$topology" == green ]]; then
        slot_is_eligible green || die "legacy active slot green is not eligible"
    else
        (slot_is_eligible blue || slot_is_eligible green || slot_is_eligible amber) || \
            die "neither steady-state backend is eligible"
    fi

    activate_surge || die "surge backend failed readiness; existing backends remain in service"

    local sequence="blue,green,amber"
    if ! slot_is_eligible green && slot_is_eligible blue; then
        sequence="green,blue,amber"
    elif [[ "$topology" == blue,green,amber ]] && ! slot_is_eligible amber; then
        sequence="amber,blue,green"
    fi
    local result=0
    roll_slots "$sequence" "$drain_seconds" || result=$?
    if ((result == 2)); then
        log "Rollout cancelled by rollback"
        return 0
    elif ((result != 0)); then
        die "base replacement incomplete; inspect status before retrying deploy"
    fi
    write_phase reloading haproxy
    reload_proxy_config || die "base backends are ready, but proxy configuration adoption is incomplete"

    retire_surge "$drain_seconds" || die "blue, green and amber are active, but surge retirement is incomplete"
}

rollback() {
    local drain_seconds="$1"
    validate_drain_seconds "$drain_seconds"
    ensure_state_dir
    acquire_lock
    validate_prerequisites
    [[ "$(read_topology_state)" == blue,green,amber ]] || MINIMUM_ELIGIBLE=2
    [[ -f "$PHASE_FILE" ]] || die "rollback is only available while a base slot is draining"

    local phase action predecessor ignored
    phase="$(<"$PHASE_FILE")"
    if [[ "$phase" == blue || "$phase" == green ]]; then
        predecessor="$phase"
    else
        IFS=: read -r action predecessor ignored <<<"$phase"
        [[ "$action" == draining && ("$predecessor" == blue || "$predecessor" == green || "$predecessor" == amber) ]] || \
            die "rollback is unavailable during rollout phase '$phase'"
    fi
    [[ "$(container_health "$predecessor" 2>/dev/null || true)" == healthy ]] || \
        die "draining backend $predecessor is no longer healthy; refusing rollback"

    log "Cancelling the drain and returning $predecessor to active service"
    set_slot_weight "$predecessor" 1 || die "HAProxy rejected rollback for $predecessor"
    if ! wait_public_ready || ! ensure_minimum_eligible "$MINIMUM_ELIGIBLE"; then
        set_slot_weight "$predecessor" 0 >/dev/null 2>&1 || true
        die "rollback public verification failed; restored the drain"
    fi
    rm -f "$PHASE_FILE"
    snapshot_runtime_state
    if ! base_slots_are_eligible; then
        write_phase retained surge blue,green,amber
        release_lock
        log "Legacy drain cancelled; retaining surge until a later deploy completes topology migration"
        return 0
    fi
    write_active_active_state

    if server_exists surge && slot_is_eligible surge; then
        retire_surge "$drain_seconds" || die "base-slot rollback succeeded, but surge retirement is incomplete"
    else
        compose stop server-surge >/dev/null 2>&1 || true
        release_lock
        log "Rollback complete: blue, green and amber remain active"
    fi
}

status() {
    ensure_state_dir
    local topology="uninitialized"
    [[ ! -f "$ACTIVE_FILE" ]] || topology="$(read_topology_state)"
    printf 'Serving topology: %s\n' "$topology"
    if [[ -f "$PHASE_FILE" ]]; then
        printf 'Rollout phase: %s\n' "$(<"$PHASE_FILE")"
    else
        printf 'Rollout phase: none\n'
    fi
    compose ps
    if compose ps --quiet haproxy | grep -q .; then
        runtime_command 'show stat' | awk -F, '
            $1 == "codex_lb_slots" && ($2 == "blue" || $2 == "green" || $2 == "amber" || $2 == "surge") {
                printf "%s: status=%s weight=%s sessions=%s\n", $2, $18, $19, $5
                if ($18 ~ /^UP/ && ($19 + 0) > 0) eligible++
            }
            END { printf "Eligible backends: %d\n", eligible + 0 }
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
