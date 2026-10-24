#!/bin/bash
set -euo pipefail

CONFIG=/etc/amazon/ssm/amazon-ssm-agent.json

if [ ! -f "$CONFIG" ]; then
  cp /etc/amazon/ssm/amazon-ssm-agent.json.template "$CONFIG"
fi

python3 - <<'PY'
import json
from pathlib import Path

p = Path("/etc/amazon/ssm/amazon-ssm-agent.json")
config = json.loads(p.read_text())
config.setdefault("Agent", {})["Region"] = "ap-northeast-1"
config["Agent"]["UseDualStackEndpoint"] = True
p.write_text(json.dumps(config, indent=2) + "\n")
PY

systemctl enable --now amazon-ssm-agent
systemctl restart amazon-ssm-agent
