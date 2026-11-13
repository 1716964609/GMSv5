#!/bin/bash
set -euo pipefail

# Enable dual-stack AWS endpoints for SSM Agent.
# Required because this EC2 has no public IPv4
# and does not use an IPv4 NAT gateway.

python3 - <<'PYSSM'
import json
from pathlib import Path

base = Path("/etc/amazon/ssm")
config = base / "amazon-ssm-agent.json"
template = base / "amazon-ssm-agent.json.template"

source = config if config.exists() else template

if not source.exists():
    raise SystemExit("SSM Agent configuration missing")

data = json.loads(source.read_text())
agent = data.setdefault("Agent", {})
agent["Region"] = "ap-northeast-1"
agent["UseDualStackEndpoint"] = True

config.write_text(json.dumps(data, indent=2) + "\n")
config.chmod(0o600)
PYSSM

systemctl enable --now amazon-ssm-agent
systemctl restart amazon-ssm-agent
