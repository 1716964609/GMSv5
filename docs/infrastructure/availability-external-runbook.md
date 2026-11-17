# GMS v5 — External Availability / Spot Event Runbook

- Owner: Infrastructure / SRE
- Date: 2026-11-17 JST
- Scope: `infra/terraform/availability/` only; existing production resources remain unchanged
- Status: **AWS DEPLOYED; HTTP, Discord and Grafana verified; real EC2 event delivery and UptimeRobot pending**

## 1. Architecture

```text
AWS EC2 service events ─┐
                       ├─ EventBridge ─────────────┐
Spot warning/rebalance ─┘                          │
                                                  v
EventBridge schedule (every 1m) ───────────────► Lambda Python 3.12 (no VPC attachment)
                                                  │
                     ┌────────────────────────────┼─────────────────────┐
                     v                            v                     v
            DynamoDB lifecycle rows         CloudWatch metrics     Public IPv4 CloudFront
            PK instance_id                  GMS/Availability       d17p4.../notify
            SK event_time                    HTTPUp                + SSM SecureString token
            + ~STATE marker                  StopToRecoverySeconds   │
                     ^                            │                  Discord
                     │                            v
        EC2 event stop/running                Grafana CloudWatch
                                            (requires read IAM + API path)

Lambda HTTP probe every 1m ──► https://sunlightjetrans.com/ (public CloudFront frontend)
UptimeRobot Free every 5m ────► same URL ───────► independent Discord integration
```

The Lambda function intentionally has **no VPC configuration**. A Lambda function without VPC attachment has public-internet access. It connects to public CloudFront frontends over IPv4 if the application DNS provides A records; it does **not** connect directly to the IPv6-only EC2 or the private host-network Nginx relay. Never add a VPC attachment without redesigning egress.

The existing DeepL and Discord CloudFront distributions are **not managed or modified** in this module. Lambda sends Discord JSON directly to the existing public CloudFront distribution, using the existing `X-GMS-Notify-Token` and a separately stored SSM SecureString; the secret value is never passed through Terraform.

## 2. Semantics and limitations

- `stopped` starts an EC2 stop interval; `running` is EC2 resumption. These events are best-effort and can be delayed or delivered out of order.
- For Application only, a public HTTP 200 observed after `running` is recorded as `recovered_at`. Polling is 9 attempts up to about 45 seconds, followed by the independent one-minute check if the service is still down.
- Observability has no public service-health endpoint, so an EC2 `running` event closes the *EC2 interval only*. The `recovery_proof` becomes `ec2_running_only`, **not** proof that Grafana or Prometheus recovered.
- `downtime_seconds` is an event-time-derived estimate from `stopped` to recovery. Actual user-visible downtime may start sooner (`stopping`) and cannot be determined exactly from these events.
- `trigger=spot_warning_observed` only if a prior Spot warning exists in the recent event state; otherwise `unknown`. Do **not** invent `manual` or `spot` attribution from state changes alone. CloudTrail is the independent investigation source.
- `HTTPUp` publishes 0 or 1 each minute, for public frontend HTTP response only. CloudFront caching and an HTTP 200 login page can produce false-positive service health. Later add a no-cache `/api/health` endpoint with a verified origin path. An empty sample interval is *unknown*, not automatically UP.
- DynamoDB has event rows with timestamp-prefixed `event_time` plus a reserved `~STATE` row per instance for reconciliation. CloudWatch `StopToRecoverySeconds` is not the same as continuous availability. For monthly sampled availability use `100 * AVG(HTTPUp)` over a monthly interval, and report sample coverage.
- Missing events, delayed events, Lambda timeouts, direct Discord errors, and Spot replacement are known gaps. This pilot is not an audit-grade, exactly-once recovery system. HTTP-only incidents have DynamoDB state tracking and DOWN/RECOVERED notifications, but no detailed duration rows.

## 3. Preflight (Mac Terminal, read-only)

```bash
cd ~/GMSv5
# The CloudFront-hosted public URL must have a public IPv4 DNS response for VPC-free Lambda.
dig +short A sunlightjetrans.com
curl -4 -sS -o /dev/null -w 'HTTP=%{http_code}\n' --max-time 10 https://sunlightjetrans.com/

# Verify both instance IDs exist, without modifying any EC2 instance.
aws ec2 describe-instances --region ap-northeast-1 \
  --instance-ids i-0decf6bc205e273d9 i-021170e56849c597b \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,AZ:Placement.AvailabilityZone}' \
  --output table
```

**Stop** if `dig A` is empty or the public site does not return HTTP 200. Verify CloudFront and DNS first, without changing existing distributions. A local Mac test is only preflight; a real Lambda invocation after apply is the authoritative test of Lambda egress.

## 4. Safely register the Discord relay token (Mac only)

The preexisting relay token resides in the operator-owned `~/.config/gmsv5/discord-relay-token`. The Parameter Store **name** is fixed by Terraform, but the **value** must be supplied outside Terraform. Do not print the token, place it in shell history, upload it to a repository, or paste it into chat.

```bash
TOKEN_FILE="$HOME/.config/gmsv5/discord-relay-token"
test -s "$TOKEN_FILE" || { echo 'Missing token file'; exit 1; }

aws ssm put-parameter \
  --region ap-northeast-1 \
  --name /gmsv5/availability/discord-relay-token \
  --type SecureString \
  --overwrite \
  --value "$(cat "$TOKEN_FILE")" \
  --query '{Name:Name,Version:Version}' --output table
```

This uses the default AWS-managed SSM KMS key. If a customer-managed KMS key is used instead, Lambda requires a separate, explicitly scoped `kms:Decrypt` permission. The shell's AWS CLI process arguments may temporarily contain the secret; run from a trusted single-user Mac session.

## 5. Terraform maintenance and validation

Deployment completed on **2026-11-17 JST**.

Deployed resources:

- Lambda: `gmsv5-availability` (Python 3.12)
- DynamoDB: `gmsv5-availability-events`
- CloudWatch namespace: `GMS/Availability`
- EventBridge: HTTP schedule, EC2 state and Spot events
- Grafana IAM policy: `gmsv5-availability-cloudwatch-read`

### Validation on Mac

Run from `~/GMSv5/infra/terraform/availability`:

- `.venv/bin/python -m unittest discover -s tests -v`
- `.venv/bin/python -m py_compile lambda/handler.py`
- `terraform fmt -check`
- `terraform validate`
- `terraform plan`

Verified results:

- Python tests: **19/19 PASS**
- Terraform validation: **PASS**
- Initial AWS deployment: **14 resources created**
- HTTP alert Lambda update: **1 resource updated**
- Grafana IAM policy: **1 resource created**

The Grafana role grants `cloudwatch:ListMetrics` and
`cloudwatch:GetMetricData`.

Review every Terraform plan before applying changes.

Never commit Terraform state, plan files, `.venv`,
AWS credentials, Discord tokens, or `grafana.env`.

## 6. Non-destructive end-to-end verification

Run the **synthetic smoke test** first. It creates a clearly marked `test-availability` DynamoDB event and `TestSignal` metric; it does NOT fabricate a stopped event for either real EC2 or modify EC2. It sends an actual Discord test message.

```bash
aws lambda invoke --region ap-northeast-1 \
  --function-name gmsv5-availability \
  --cli-binary-format raw-in-base64-out \
  --payload '{"source":"gmsv5.manual-test","test_mode":true,"id":"manual-smoke-001"}' \
  /tmp/gmsv5-availability-smoke.json
cat /tmp/gmsv5-availability-smoke.json
```

Verify DynamoDB and CloudWatch. Metrics may take a few minutes to become queryable.

```bash
aws dynamodb query --region ap-northeast-1 \
  --table-name gmsv5-availability-events \
  --key-condition-expression 'instance_id = :id' \
  --expression-attribute-values '{":id":{"S":"test-availability"}}' \
  --output json

aws cloudwatch list-metrics --region ap-northeast-1 \
  --namespace GMS/Availability --output table
```

Wait for scheduled checks to emit `HTTPUp` 1/0. Inspect `/aws/lambda/gmsv5-availability` in CloudWatch Logs. **If the synthetic invoke reports an HTTP, authorization or SSM error, stop here and investigate.**

## 7. Controlled real EC2 stop/start test (PRODUCTION DISRUPTION)

**Not executed as part of this package.** This is a deliberate production outage. Perform it only after the preflight, smoke test, backups and maintenance-window approval. Validate the Terraform `instance_interruption_behavior=stop` and EC2 persistence before any modification. Manually stopping Persistent Spot may alter the Spot request behavior; do not assume it is equivalent to an AWS-initiated Spot interruption. Confirm current AWS rules and prepare a rollback.

Prefer a controlled nonproduction instance for the first state-change integration test. If intentionally testing production, manually stop/start via AWS CLI only with explicit operational approval. Observe `stopped` and `running` EventBridge entries, the single incident row updated in DynamoDB, the HTTP 200 recovery timestamp, `StopToRecoverySeconds`, and Discord. Compare with independent UptimeRobot timestamps. Capture log IDs and verify idempotency. Do not state that the path is validated until the corresponding outputs are recorded.

## 8. Grafana CloudWatch integration

**Status: DEPLOYED AND VERIFIED — 2026-11-17 JST**

### 8.1 Network architecture

Grafana runs on the IPv4 Docker Bridge network
`gms-observability`, while its EC2 host has IPv6 connectivity.

Traffic path:

1. Grafana container: `172.18.0.7`
2. Squid proxy: `172.18.0.1:3128`
3. HTTPS CONNECT over the host's IPv6 connectivity
4. CloudWatch: `monitoring.ap-northeast-1.api.aws`

The existing Prometheus, Tempo and Discord relay remain
on their original network.

### 8.2 Squid proxy

Package: Squid 6.13 on Amazon Linux 2023.

Git source:

`infra/observability/squid/cloudwatch-only.conf`

EC2 destination:

`/etc/squid/squid.conf`

Access restrictions:

- Listen only on `172.18.0.1:3128`
- Allow client `172.18.0.7`
- Allow CONNECT to CloudWatch on TCP 443
- Deny all other destinations
- Disable caching

Verified:

- CloudWatch CONNECT: **200**
- Unauthorized `example.com` CONNECT: **403**
- Squid service: **active**
- Squid systemd startup: **enabled**

Validation commands on Observability EC2:

- `systemctl is-active squid`
- `systemctl is-enabled squid`
- `sudo ss -lntp | grep ':3128'`
- `sudo /usr/sbin/squid -k parse`

Squid requires the Docker gateway address `172.18.0.1`.
Automatic startup ordering after an EC2 reboot is not yet verified.

### 8.3 Grafana environment

Protected EC2 configuration:

`/opt/gmsv5-observability/grafana.env`

Required settings:

- `GF_AWS_PER_DATASOURCE_HTTP_PROXY_ENABLED=true`
- `HTTPS_PROXY=http://172.18.0.1:3128`
- `NO_PROXY=169.254.169.254,localhost,127.0.0.1,172.18.0.1,gms-prometheus,gms-tempo`

Do **not** set `HTTP_PROXY`.

IMDSv2 must bypass Squid so that Grafana can obtain its
EC2 IAM role credentials.

The existing Discord Contact Point uses internal HTTP
communication to the host gateway.

Never commit the actual `grafana.env` file.

### 8.4 Grafana container

Image: `grafana/grafana:13.3.1`

Required runtime configuration:

- Name: `gms-grafana`
- Network: `gms-observability`
- IPv4: `172.18.0.7`
- Port: `127.0.0.1:3000:3000`
- Restart policy: `unless-stopped`
- Data volume: `gms-grafana-data`
- Provisioning mount: `/opt/gmsv5-observability/grafana/provisioning`
- Protected environment file: `grafana.env`

The container is managed using Docker commands, not Compose.

Preserve the existing data volume when recreating Grafana.

Earlier Grafana containers were retained for rollback during
the 2026-11-17 migration.

### 8.5 CloudWatch datasource

Datasource UID: `gms-cloudwatch`

Git source:

`infra/observability/grafana/cloudwatch-datasource.yml`

EC2 destination:

`/opt/gmsv5-observability/grafana/provisioning/datasources/cloudwatch.yml`

Settings:

- Authentication: AWS SDK Default
- Region: `ap-northeast-1`
- Endpoint: `https://monitoring.ap-northeast-1.api.aws`
- Proxy type: `env`
- Namespace: `GMS/Availability`

The datasource uses the EC2 IAM role.
No static AWS access keys are stored in Grafana.

### 8.6 Availability dashboard

Dashboard UID: `gmsv5-external-availability`

Git source:

`infra/observability/grafana/external-availability-dashboard.json`

Panels:

1. Public HTTP Status
2. Public HTTP Availability — one-minute samples
3. EC2 Stop-to-Recovery — seconds

Verified using Grafana's CloudWatch query API:

- Query status: **200**
- Data frames: **1**
- HTTPUp data points: **45**
- Latest five values: **1, 1, 1, 1, 1**
- Browser dashboard: **displayed successfully**

The Stop-to-Recovery panel currently has no real incident
metric. This is expected.

### 8.7 Operational verification

On Observability EC2:

1. Check `systemctl is-active squid`.
2. Check `sudo docker ps --filter name=gms-grafana`.
3. Check `curl -fsS http://127.0.0.1:3000/api/health`.
4. Confirm Grafana's IPv4 still matches the Squid ACL.
5. Query CloudWatch `HTTPUp` through Grafana.
6. Verify Prometheus and Tempo datasources.
7. Verify the existing Discord alert Contact Point.

For CloudWatch connectivity from the Grafana container,
use the HTTPS endpoint through the configured Squid proxy.

An unsigned request to the CloudWatch root may return
HTTP 404 even when connectivity succeeds.

### 8.8 Recovery and limitations

For a fresh Observability EC2 deployment:

1. Restore the Docker `gms-observability` network.
2. Install Squid and restore its version-controlled ACL.
3. Verify the Docker gateway and Grafana client addresses.
4. Enable and start Squid.
5. Restore protected Grafana environment settings.
6. Restore or recreate the Grafana container, preserving its data.
7. Apply the version-controlled datasource provisioning.
8. Import the availability dashboard JSON through Grafana API.
9. Validate CloudWatch, Prometheus, Tempo and Discord.

Remaining limitations:

- A stale HTTPUp=1 can remain visible if monitoring stops.
- Missing HTTP samples do not prove availability.
- Real EC2 EventBridge delivery remains unverified.
- Real StopToRecoverySeconds has not yet been emitted.
- Squid/Grafana recovery after EC2 reboot is untested.
- UptimeRobot remains unconfigured.
- CloudWatch GetMetricData may incur charges.


### 8.9 Squid boot recovery configuration

The Spot recovery incident on 2026-11-17 confirmed a startup
race between Squid and the Docker bridge interface.

Version-controlled systemd drop-in:

`infra/observability/squid/10-docker-bridge.conf`

Deployment destination:

`/etc/systemd/system/squid.service.d/10-docker-bridge.conf`

Version-controlled readiness script:

`infra/observability/squid/gmsv5-wait-squid-bridge.sh`

Deployment destination:

`/usr/local/sbin/gmsv5-wait-squid-bridge.sh`

Installation steps on Observability EC2:

1. Install the readiness script with permissions 0755.
2. Install the systemd drop-in with permissions 0644.
3. Execute `sudo systemctl daemon-reload`.
4. Execute `sudo systemd-analyze verify squid.service`.
5. Execute the readiness script as user squid.
6. Restart Squid using `sudo systemctl restart squid`.
7. Check `systemctl is-active squid`.
8. Verify CloudWatch HTTPS CONNECT from Grafana.

The drop-in starts Squid after Docker and waits for
the Docker gateway address before binding the listener.

A manual Squid restart after remediation passed.
Full EC2 reboot recovery after remediation is not yet verified.

## 9. UptimeRobot (manual setup; independently hosted monitoring)

1. Create/log into a Free account at https://uptimerobot.com/ (no AWS access needed).
2. Add an HTTP(s) monitor named `GMS v5 Public Frontend` for `https://sunlightjetrans.com/`.
3. Set interval to the Free default of **5 minutes** and select the HTTP status check. Consider a keyword check only if a suitable constant public marker exists.
4. Under **Integrations**, add **Discord** (included in Free) and connect the chosen channel using its own Discord integration or separate webhook. Do not disclose or reuse the CloudFront KVS relay secret. Configure Down and Up messages and send a test.
5. Confirm a test Down/Up notification through the provider's supported UI, without deliberately taking production offline.
6. Document monitor ID, timezone, observed check interval and screenshots privately. Re-check actual availability after setup.

**Limitation:** Five-minute probes can miss one- to five-minute outages. UptimeRobot's clock and EventBridge event times are not interchangeable. Free Discord integration is supported, while arbitrary custom Webhook integration is not part of Free.

## 10. Acceptance / incident evidence

Mark each item separately: source code/unit tests; Terraform validated; AWS plan inspected; AWS apply succeeded; actual Lambda public IPv4 probe; DynamoDB synthetic record; CloudWatch metric; Discord test; EC2 event receipt; post-restart HTTP 200; Grafana datasource; UptimeRobot independent monitoring. Keep actual UTC timestamps and original resource IDs in a dated As-Built. Do not claim full-cycle success from unit tests alone.

Sources: AWS EventBridge EC2 event reference, AWS Lambda VPC/network docs, AWS CloudWatch pricing, AWS DynamoDB pricing, UptimeRobot Free plan and Discord integration documentation.

## 11. Production EC2 stop/start test commands — explicit maintenance approval only

**Use these ONLY in a prearranged maintenance window and only after §6 passes.** This intentionally interrupts the production website. In particular, manually stopping Persistent Spot can require a manual start, regardless of normal AWS Spot auto-restore behavior. Confirm database and EBS backup status before any invocation. Observe from a separate shell; keep the SSM session aware that it will disconnect.

```bash
# Mac Terminal. This section is NOT part of the non-destructive smoke test.
APP_ID=i-0decf6bc205e273d9
read -r -p 'Type APPROVE-PRODUCTION-OUTAGE to continue: ' ACK
[ "$ACK" = 'APPROVE-PRODUCTION-OUTAGE' ] || { echo 'Cancelled'; exit 1; }

aws ec2 stop-instances --region ap-northeast-1 --instance-ids "$APP_ID"
aws ec2 wait instance-stopped --region ap-northeast-1 --instance-ids "$APP_ID"
# On confirmed stopped state, START MANUALLY; do not wait indefinitely.
aws ec2 start-instances --region ap-northeast-1 --instance-ids "$APP_ID"
aws ec2 wait instance-running --region ap-northeast-1 --instance-ids "$APP_ID"
```

After start, run local Application SSM readiness and HTTP checks from the preceding [Spot Interruption Runbook](./spot-interruption-runbook.md), and verify the Lambda's incident row, CloudWatch event-duration metric, Discord message and external UptimeRobot result. If a Stop/Start API fails, do not keep experimenting: diagnose the state and restore service before continuing. **A manually induced outage tests state transitions and recovery logic, but does not prove a true Spot interruption warning was emitted.**
