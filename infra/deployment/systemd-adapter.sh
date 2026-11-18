#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$#" -eq 2 ] || exit 2

ACTION="$1"
SHA="$2"

[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2

case "$ACTION" in
    restart)
        timeout 90s systemctl restart gmsv5.service
        ;;

    health)
        systemctl is-active --quiet gmsv5.service

        python3 -B "$SCRIPT_DIR/healthcheck.py" "$SHA"
        ;;

    *)
        exit 2
        ;;
esac
