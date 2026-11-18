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

exec python3 -B "$SCRIPT_DIR/state.py" \
    lock "$STATE_DIR" \
    bash "$SCRIPT_DIR/deploy_locked.sh" "$SHA"
