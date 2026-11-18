#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -lt 4 ]; then
    echo "Usage: transaction.sh deploy|recover ROOT STATE_DIR ADAPTER [SHA]" >&2
    exit 2
fi

STATE_DIR="$3"

exec python3 -B "$SCRIPT_DIR/state.py" \
    lock "$STATE_DIR" \
    bash "$SCRIPT_DIR/transaction_locked.sh" "$@"
