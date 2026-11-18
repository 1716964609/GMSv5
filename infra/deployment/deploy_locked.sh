#!/usr/bin/env bash
set -euo pipefail

export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
umask 022

SCRIPT_DIR=/opt/gmsv5/deploy
ROOT=/opt/gmsv5
STATE_DIR=/var/lib/gmsv5-deploy

[ "$(id -u)" -eq 0 ] || exit 2
[ "$#" -eq 1 ] || exit 2

SHA="$1"

[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2

echo "DEPLOYMENT_REQUESTED sha=$SHA"

if [ -e "$STATE_DIR/state.json" ] ||
   [ -L "$STATE_DIR/state.json" ]; then

    PHASE="$(
        python3 -B "$SCRIPT_DIR/state.py" \
            show "$STATE_DIR" |
        python3 -c \
            'import json,sys; print(json.load(sys.stdin)["phase"])'
    )"

    case "$PHASE" in
        SUCCEEDED|ROLLED_BACK|FAILED_BEFORE_SWITCH)
            ;;
        *)
            echo "UNRESOLVED_TRANSACTION phase=$PHASE" >&2
            exit 22
            ;;
    esac
fi

BUCKET="$(
    python3 -B "$SCRIPT_DIR/preflight.py"
)"

echo "PREFLIGHT: PASS"

DOWNLOAD_DIR="$(mktemp -d "$ROOT/.download-XXXXXX")"

cleanup() {
    rm -rf -- "$DOWNLOAD_DIR"
}

trap cleanup EXIT

python3 -B "$SCRIPT_DIR/download.py" \
    "$BUCKET" "$SHA" "$DOWNLOAD_DIR"

python3 -B "$SCRIPT_DIR/stage.py" \
    "$ROOT" "$DOWNLOAD_DIR" "$SHA"

# The outer process already holds the exclusive lock.
# Do not acquire the same lock again.

bash "$SCRIPT_DIR/transaction_locked.sh" \
    deploy \
    "$ROOT" \
    "$STATE_DIR" \
    "$SCRIPT_DIR/systemd-adapter.sh" \
    "$SHA"
