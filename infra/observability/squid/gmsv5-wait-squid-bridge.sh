#!/usr/bin/env bash
set -euo pipefail

for attempt in $(seq 1 60); do
  if /usr/sbin/ip -4 -o addr show |
     /usr/bin/grep -Fq ' inet 172.18.0.1/16 '; then
    echo "Squid bridge 172.18.0.1 is ready"
    exit 0
  fi
  /usr/bin/sleep 1
done

echo "Squid bridge 172.18.0.1 not ready after 60 seconds" >&2
exit 1
