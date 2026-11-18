#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

MODE="$1"
ROOT="$2"
STATE_DIR="$3"
ADAPTER="$4"

log() {
    printf '%s %s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

state() {
    python3 -B "$SCRIPT_DIR/state.py" \
        "$1" "$STATE_DIR" "${@:2}"
}

field() {
    state show | python3 -c \
        'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' \
        "$1"
}

transition() {
    state transition "$@" || return 1
}

current() {
    state current "$ROOT"
}

rollback() {
    local reason="$1"
    local previous phase

    previous="$(field previous)" || return 22
    phase="$(field phase)" || return 22

    if [ "$phase" != "ROLLING_BACK" ]; then
        transition ROLLING_BACK "$reason" || return 22
    fi

    log "ROLLBACK_STARTED previous=$previous"

    if state switch "$ROOT" &&
       "$ADAPTER" restart "$previous" &&
       "$ADAPTER" health "$previous"; then

        transition ROLLED_BACK "$reason" || return 22
        log "ROLLBACK_SUCCEEDED previous=$previous"
        return 20
    fi

    transition ROLLBACK_FAILED "$reason" || return 22
    log "ROLLBACK_FAILED previous=$previous"
    return 21
}

deploy() {
    local target="$1"
    local previous phase

    if [[ ! "$target" =~ ^[a-f0-9]{40}$ ]]; then
        log "INVALID_TARGET_SHA"
        return 2
    fi

    if [ -e "$STATE_DIR/state.json" ] ||
       [ -L "$STATE_DIR/state.json" ]; then

        phase="$(field phase)" || return 22

        case "$phase" in
            SUCCEEDED|ROLLED_BACK|FAILED_BEFORE_SWITCH)
                ;;
            *)
                log "UNRESOLVED_TRANSACTION phase=$phase"
                return 22
                ;;
        esac
    fi

    previous="$(current)" || return 10

    if ! "$ADAPTER" health "$previous"; then
        log "BASELINE_UNHEALTHY previous=$previous"
        return 10
    fi

    if [ "$previous" = "$target" ]; then
        log "ALREADY_DEPLOYED release=$target"
        return 0
    fi

    state begin "$previous" "$target" || return 22

    if ! python3 -B "$SCRIPT_DIR/compatibility.py" \
        "$ROOT" "$previous" "$target"; then

        transition FAILED_BEFORE_SWITCH \
            "schema_or_release_validation_failed" || return 22

        log "DEPLOYMENT_REJECTED_BEFORE_SWITCH"
        return 10
    fi

    transition VERIFIED || return 22

    # Persist intent before changing the current symlink.
    transition SWITCHING || return 22

    if ! state switch "$ROOT"; then
        rollback "release_switch_failed"
        return $?
    fi

    transition VALIDATING || return 22

    if ! "$ADAPTER" restart "$target"; then
        rollback "application_restart_failed"
        return $?
    fi

    if ! "$ADAPTER" health "$target"; then
        rollback "readiness_failed"
        return $?
    fi

    transition SUCCEEDED || return 22

    log "DEPLOYMENT_SUCCEEDED release=$target"
    return 0
}

recover() {
    local phase previous active rc

    if [ ! -e "$STATE_DIR/state.json" ] &&
       [ ! -L "$STATE_DIR/state.json" ]; then

        log "NO_DEPLOYMENT_TRANSACTION"
        return 0
    fi

    phase="$(field phase)" || return 22

    case "$phase" in
        SUCCEEDED|ROLLED_BACK|FAILED_BEFORE_SWITCH)
            log "NO_RECOVERY_REQUIRED phase=$phase"
            return 0
            ;;

        PREPARING|VERIFIED)
            previous="$(field previous)" || return 22
            active="$(current)" || return 22

            if [ "$active" != "$previous" ]; then
                log "UNEXPECTED_CURRENT_RELEASE"
                return 22
            fi

            transition FAILED_BEFORE_SWITCH \
                "interrupted_before_switch" || return 22

            log "INTERRUPTED_BEFORE_SWITCH"
            return 0
            ;;

        SWITCHING|VALIDATING|ROLLING_BACK|ROLLBACK_FAILED)
            rc=0
            rollback "interrupted_deployment" || rc=$?

            if [ "$rc" -eq 20 ]; then
                log "RECOVERY_SUCCEEDED"
                return 0
            fi

            return "$rc"
            ;;

        *)
            log "UNKNOWN_DEPLOYMENT_STATE"
            return 22
            ;;
    esac
}

case "$MODE" in
    deploy)
        [ "$#" -eq 5 ] || exit 2
        deploy "$5"
        ;;
    recover)
        [ "$#" -eq 4 ] || exit 2
        recover
        ;;
    *)
        exit 2
        ;;
esac
