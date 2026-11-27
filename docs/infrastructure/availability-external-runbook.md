# GMS v5 — External Availability / Spot Event Runbook

- Owner: Infrastructure / SRE
- Date: 2026-11-17 JST
- Scope: `infra/terraform/availability/` only; existing production resources remain unchanged
- Status: **AWS DEPLOYED; HTTP, Discord and Grafana verified; real EC2 event delivery and UptimeRobot pending**

> **Added 2026-11-19:** this runbook is kept as written on 2026-11-17.
> Read it with three later facts in mind.
>
> 1. [As-Built §7](./availability-external-as-built-2026-11-17.md#7-actual-spot-recovery-and-squid-failure)
>    records that real EC2 lifecycle and HTTP recovery notifications were
>    observed after actual Spot interruptions later on 2026-11-17, and
>    that the dashboard showed real `StopToRecoverySeconds` data. The
>    statements below that real EC2 event delivery is unverified and that
>    the metric has not been emitted predate that record. Recovery of
>    Squid and Grafana after an EC2 reboot following the remediation, and
>    UptimeRobot, are still pending.
> 2. Since 2026-11-19 the same Lambda function also records production
>    deployment events (`infra/terraform/availability/deployment.tf`,
>    `lambda/deployment.py`): records keyed `deployment#<instance>` and
>    the metric `DeploymentEvents`. They are separate from the EC2 and
>    HTTP records described here. See
>    [CI/CD Operations Runbook](./cicd-operations-runbook.md) and
>    [Architecture §15](../architecture.md#15-deployment-observability2026-11-19).
> 3. The test suite of this module now has 33 tests, including the
>    deployment handler tests, and runs in the CI Quality Gate. The
>    "19/19" result in §5 is the 2026-11-17 figure.
>
> **Added 2026-11-25:** the Spot interruption reason evidence change
> (§12) adds `lambda/spot_status.py`, the EC2 `stopping` state to the
> state-change rule, `ec2:DescribeSpotInstanceRequests` to the Lambda
> role and 80 tests (113 in total). It was **deployed on 2026-11-25 at
> 01:35 JST** ([As-Built](./spot-reason-tracking-as-built-2026-11-25.md)). Real Spot reason capture is not yet
> verified. §1 and §2 still describe the rest of the module correctly.
>
> **Added 2026-11-27, updated 2026-11-28:** §13 describes the availability
> burn rate alarm (P2): a CloudWatch alarm on `HTTPUp`, an EventBridge rule
> and `lambda/burn_rate.py` in the same function. It was **applied in
> stage `off` on 2026-11-27 at 21:42 JST** (no messages; the rule is
> `DISABLED`), and CloudWatch's evaluation was measured in an isolated
> integration test the same day
> ([As-Built](./availability-burn-rate-as-built-2026-11-27.md)). The
> 2026-11-28 message redesign (§13.4, §13.11: current HTTP state and the
> last 60 minutes instead of an observed burn rate, 257 tests in total)
> was **applied in stage `off` on 2026-11-28 at 01:37 JST**, with its
> shadow ledger, hard lookup deadline and lookup thread limit.

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

## 12. Spot interruption reason evidence

**Status (2026-11-25): deployed (01:35 JST); production configuration
and the existing HTTP monitoring verified. Real Spot status collection,
reason association and cause-bearing RECOVERED messages are PENDING
until a natural interruption occurs.** Evidence:
[Spot Reason Tracking As-Built](./spot-reason-tracking-as-built-2026-11-25.md). Classification and the
investigation procedure are in the
[Spot Interruption Runbook §9](./spot-interruption-runbook.md#9-interruption-reason-evidence).

### 12.1 What changes

| Item | Change |
|---|---|
| EventBridge rule `gmsv5-availability-instance-state` | Adds `stopping` to `stopped` and `running`. Targets and other rules unchanged. |
| Lambda role policy `ReadEC2State` | Adds `ec2:DescribeSpotInstanceRequests` (read only, `Resource: *` because the action has no resource-level permission). No EC2 write action. |
| Lambda environment | `SPOT_STATUS_COLLECTION=enabled`. Any other value restores the previous behavior, including ignoring `stopping`. Changing it needs a Terraform apply. |
| Lambda code | New `lambda/spot_status.py`, packaged by the existing `archive_file` (`source_dir = lambda`). `handler.py` calls it after the existing processing. |
| DynamoDB | Same table, keys and 400-day TTL. New attributes and a new partition prefix only; no schema change. |

The collector fails open: API errors, throttling, a missing Spot
request, stale or ambiguous status, or too little Lambda time are
recorded or skipped, and the existing processing continues.

**Execution budget.** The collector always runs after the existing
processing of the same event, and it uses its own EC2 and DynamoDB
clients (connect 2 s, read 3 s, two attempts, standard retry mode). The
handler's own DynamoDB, CloudWatch and Discord calls are unchanged.

| Item | Worst case |
|---|---|
| One SDK call (DescribeInstances, DescribeSpotInstanceRequests or one DynamoDB call) | 2 × (2 s + 3 s) + backoff ≤ 1 s, plus ≤ 5 s if AWS sends `x-amz-retry-after` = **16 s** |
| One observation read and saved (2 EC2 calls + 1 PutItem) | **48 s** |
| Explicit retries (`stopped` only) | waits until about +2 s and +5 s after the first read |

- Before every AWS call: at least 16 s + 2 s must remain, otherwise the
  collector stops.
- Before every attempt, including the first: the wait, 48 s and 2 s must
  fit in the remaining time.
- So the collector stops with at least 2 s left for the handler to
  return. If about 88 s remain when it starts and every call takes its
  worst case, a `stopped` event gets one observation read and saved,
  and no retry (tested).
- Not covered by the SDK timeouts: DNS resolution, and a response that
  keeps sending data slower than the read timeout.

### 12.2 Data model

**Incident row** — the existing `stopped` row (`instance_id` = instance,
`event_time` = `<stop time>#<event id>`). New attributes, set only on
incidents created after deployment:

| Attribute | Meaning |
|---|---|
| `spot_window_start` | Earliest Spot status time that can belong to this stop: 10 minutes before `stop_at`, or the previous `running` if later |
| `spot_previous_running_at`, `spot_previous_stop_at` | The last `running` and `stopped` times known when this stop was processed, if earlier |
| `spot_run_started_at` | Launch time of the run that this stop ended, read from `DescribeInstances` before any restart |
| `spot_reason` | `CAPACITY`, `PRICE`, `USER`, `EXPERIMENT`, `PENDING` or `UNKNOWN` (initially `UNKNOWN`) |
| `spot_reason_confirmed` | `true` only for an AWS `instance-stopped-*` code inside the window |
| `spot_status_code`, `spot_status_message`, `spot_status_update_time` | Raw AWS Spot request status |
| `spot_evidence_observed_at`, `spot_evidence_phase`, `spot_request_id` | When and at which event the evidence was read |
| `spot_reason_conflict`, `spot_conflict_codes` | Set when another confirmed code was observed for the same incident |
| `stopping_at` | Time of the EC2 `stopping` event, if within the window |
| `interruption_warning_at`, `rebalance_recommendation_at` | Separate signal times, if within the window |

`stop_at`, `running_at`, `recovered_at`, `downtime_seconds`,
`recovery_proof` and `trigger` keep their meaning.
`StopToRecoverySeconds` is still measured from `stop_at`, not
`stopping_at`.

**`~STATE` row** — adds `last_interruption_warning`,
`last_rebalance_recommendation` and `last_stopping`, each written only if
newer. `last_spot_warning` is still written for both signal kinds, as
before, and still drives `trigger=spot_warning_observed`.

**Observation rows** — one per API read, append only:

| Key | Value |
|---|---|
| `instance_id` | `spot#<instance id>` (separate partition, so the existing incident queries are not affected) |
| `event_time` | `<event time>#<event id>#<phase>#<attempt>`; a duplicate delivery of the same event writes nothing |
| Content | `phase`, `observed_at`, `spot_request_id`, raw status code, message and update time, `request_state`, `max_price`, `interruption_behavior`, `instance_type`, `availability_zone`, EC2 state, `launch_time`, `StateReason` code and message, `classification`, `authoritative`, `attribution`, `incident_key`, `incident_result`, `error`, `expires_at` (event time + 400 days) |

### 12.3 Write rules and concurrency

- An observation is saved before it is attached to an incident and
  before the next retry, so a later failure cannot lose it.
- It is attached only if its `Status.UpdateTime` is inside the
  incident window:
  - Not before `spot_window_start`, the previous `running`, or the
    launch of the run that this stop ended.
  - Before the next restart, with no tolerance: the `running` time, a
    launch time after the stop, or the run start of a later incident.
    A status set after the instance started again belongs to a later
    cycle, even seconds later.
  - Within 10 minutes after the stop.

  Otherwise it stays as an observation with `attribution` `stale` or
  `after_incident`.
- If the status also fits the window of another incident within 20
  minutes and no restart separates them (for example, `running` events
  and launch times are both unknown), the `attribution` is `ambiguous`.
  The observation is kept, and no incident gets a cause from it.
- Conditional updates protect the incident row:
  - A confirmed code is written only if no confirmed code exists.
  - `PENDING` / `UNKNOWN` (including `fulfilled`) is written only if
    nothing is confirmed and its update time is not older than the
    stored one. It can never replace a confirmed reason, whatever the
    order of concurrent Lambda executions.
  - A different confirmed code for an already confirmed incident is
    added to `spot_conflict_codes` with `spot_reason_conflict = true`.
    The first code is kept and the conflict is shown in Discord.
  - Every incident update requires `event_type = stopped`, so no row
    is created by an update.
- Observations made before the `stopped` event (warning, rebalance,
  `stopping`) are linked when `stopped` is processed. If one of those
  events arrives after `stopped`, it is linked to the active incident
  when its time is inside the window. A warning, rebalance or
  `stopping` time belongs only to the first stop after it. It is not
  recorded on an incident if a previous stop, or a launch of this run,
  falls between them.
- Historical rows are never backfilled. Missing evidence stays
  `UNKNOWN`.

### 12.4 Notifications

No new notification type and no message per API call.

- Interruption Warning and Rebalance Recommendation messages are
  unchanged and are sent before any status read. The status at that
  moment is stored in DynamoDB only. A slow or failed read cannot delay
  or block these messages. If Discord fails, the status is still read
  and the original error is raised as before.
- RECOVERED messages add one line: `Spot cause: <class> (confirmed by
  AWS Spot request status)` with the code, status time and observation
  time; or `(not confirmed)`; or `UNKNOWN (no AWS Spot status
  evidence)`. Conflicts are listed.
- HTTP DOWN / RECOVERED and deployment messages are unchanged.

### 12.5 Order of processing

| Event | Existing processing | Then |
|---|---|---|
| `stopped` | Incident row and `~STATE` saved | Window set; first status read; earlier observations linked; retries at about +2 s and +5 s while unresolved |
| `running` | Pairing, HTTP 200 polling (Application) or running-only proof (Observability), RECOVERED | One status read |
| Warning / rebalance | Event row and `~STATE` saved; Discord message | One status read |
| `stopping` | — (previously ignored) | `last_stopping` and one status read; no incident is opened |

The RECOVERED message uses the evidence stored at that moment. When the
Observability EC2 recovers in the same invocation as a late `stopped`
event, it can say `UNKNOWN` although evidence is stored seconds later.
The DynamoDB row is the record.

### 12.6 Deployment record

- Applied 2026-11-25 JST: 0 added, 3 changed, 0 destroyed (the Lambda
  function, the role policy and the state-change rule).
- Deployed `CodeSha256` `cw8JPPcmFYdo5Xsc7YwD4zxK7QsCIw8CAsiINW9oiQo=`.
  The previous one was `tazsiB2aDUX4rcaLje518P4DioS2M2j0d0aj2faEumY=`.
- Python tests: 113 (§5). Run them before any further change.
- The first real interruption is the first production evidence for this
  section. Follow
  [Spot Interruption Runbook §9.4](./spot-interruption-runbook.md#94-after-the-next-natural-interruption-read-only)
  and record the result in a dated document. Do not mark this section
  verified before that.

### 12.7 Rollback

Roll back if, after an EC2 or Spot event, the Lambda times out, runs
out of memory or reports errors, or if HTTP monitoring, RECOVERED or
deployment notifications regress. Inspect first (read only):

```bash
aws logs filter-log-events --profile gms-admin --region ap-northeast-1 \
  --log-group-name /aws/lambda/gmsv5-availability \
  --start-time "$(($(date +%s) - 3600))000" \
  --filter-pattern '?"Task timed out" ?"Runtime.OutOfMemory" ?ERROR ?"Spot status evidence skipped"'
```

**A. Feature flag (first choice).** Disables only the collector.

1. In `infra/terraform/availability/main.tf`, set
   `SPOT_STATUS_COLLECTION = "disabled"`.
2. Run the tests, `terraform fmt -check`, `terraform validate` and
   `terraform plan`. Expected: 1 to change
   (`aws_lambda_function.availability`, environment only), nothing added
   or destroyed. Apply only that plan.
3. Effect: `stopping` events are ignored again
   (`{"ignored": "other state"}`), no status is read, and RECOVERED
   messages return to the previous text. The IAM read permission and the `stopping` pattern
   stay. Each `stopping` event costs one short Lambda invocation.

If Terraform cannot be used quickly, change only this variable with the
AWS CLI. `update-function-configuration --environment` replaces the whole
map, so read and write back all variables. The map holds names and URLs,
not secrets.

```bash
ENV_FILE="$(mktemp)"
aws lambda get-function-configuration --profile gms-admin --region ap-northeast-1 \
  --function-name gmsv5-availability --query Environment --output json > "$ENV_FILE"
python3 - "$ENV_FILE" <<'EOF'
import json, sys
path = sys.argv[1]
env = json.load(open(path))
env["Variables"]["SPOT_STATUS_COLLECTION"] = "disabled"
json.dump(env, open(path, "w"))
EOF
aws lambda update-function-configuration --profile gms-admin --region ap-northeast-1 \
  --function-name gmsv5-availability --environment "file://$ENV_FILE" \
  --query '{State:State,Update:LastUpdateStatus}'
```

Then make the same change in `main.tf` the same day. `terraform plan`
must show no changes.

**B. Full Terraform rollback.** Restores the code, IAM policy and rule.

1. Revert the change in Git: `git revert` of its commit, or of the merge
   commit after merge. The result must equal `a0ff5dc` for
   `infra/terraform/availability/main.tf` and `lambda/`, with
   `lambda/spot_status.py` removed.
2. Run the tests (33 remain), `fmt -check`, `validate` and `plan`.
   Expected: 3 to change (`aws_lambda_function.availability`,
   `aws_iam_role_policy.lambda_runtime`,
   `aws_cloudwatch_event_rule.state`), 0 to add, 0 to destroy. Do not
   apply a plan that replaces or destroys anything.
3. After apply, compare `CodeSha256` with the previous value above. If
   it differs, check that the package contents equal the `a0ff5dc`
   sources (the archive is rebuilt locally).

**Data in both cases.** Do not change DynamoDB. Observation rows in
`spot#<instance-id>` expire by the existing 400-day TTL. The previous code
never reads that partition or the new incident attributes. Keep them as
evidence.

## 13. Availability burn rate alarm (P2)

**Status (2026-11-28):**

- **Applied in stage `off` (2026-11-27 21:42 JST).** The alarm evaluates;
  the rule is `DISABLED` and `BURN_RATE_DELIVERY` is `disabled`, so no
  message is sent. Checks after apply passed
  ([As-Built §3](./availability-burn-rate-as-built-2026-11-27.md#3-checks-after-apply)).
- **CloudWatch evaluation measured** in an isolated integration test
  (§13.6). The results changed §13.1 and §13.2.
- **Message redesign applied in stage `off` (2026-11-28 01:37 JST):**
  messages no longer show an observed burn rate. They show the current
  HTTP state and a count of the last 60 minutes (§13.4, §13.11). The
  Lambda role has `cloudwatch:GetMetricData`. Checks after apply passed
  ([As-Built §7](./availability-burn-rate-as-built-2026-11-27.md#7-redesign-apply-stage-off-2026-11-28)).
- **Not done:** `shadow`, `live`, and delivery through EventBridge to the
  Lambda.

Record applies in a dated As-Built, not here.

### 13.1 What it detects

The alarm fires when the external HTTP probe has failed often enough
in the last hour to consume the weekly error budget fast. It detects
accumulated interruptions, for example repeated Spot stops. It does not
replace the HTTP DOWN/RECOVERED notifications (§2), which report the
current state.

| Item | Value |
|---|---|
| SLO | 99 % availability, rolling 7 days. Error budget 100.8 min/week |
| Metric | `GMS/Availability` `HTTPUp`, `Target=sunlightjetrans.com`, one sample per minute from the scheduled probe |
| Alarm | `gmsv5-availability-burn-rate-fast`: `Minimum` over 60 s, `HTTPUp < 1` in at least **9 of the last 60** periods, `treat_missing_data = missing` |
| Why 9 of 60 (design rationale) | On a full window, 9 failed samples = 15.0x burn rate, the first value above the 14.4x design threshold; 8 = 13.3x stays OK. Messages do not show burn rates (§13.4) |
| Not used | A short (5 min) window. In the backtest it split one episode into several |

Backtest (evidence `cloudwatch-2026-11-27`, 15,645 expected minutes,
105 failed samples, `burn-rate-backtest-v1.json`): 1h / 14.4x gives
4 episodes, 197 active minutes, crossings at 11/17 09:08, 11/20 08:58,
11/24 09:06 and 11/24 14:06 JST. Replaying the same evidence through the
configured 9-of-60 rule (a local model, not CloudWatch) gives the same
4 episodes and 197 minutes.

Timing (measured in the integration test on 2026-11-27, §13.6, except
where noted):

- **ALARM** came about 1 minute after the model's crossing (evaluation
  once a minute plus ingestion). Real outages were not measured.
- **OK** needs fewer than 9 breaching datapoints **and at least 52
  non-breaching datapoints** in the evaluation range. CloudWatch writes
  this in the reason ("minimum 52 datapoints for ALARM -> OK
  transition"). 52 = 60 − 9 + 1. After 9 consecutive failures, OK comes
  about 52 minutes after the ninth one, if the probe keeps publishing.
  NORMALIZED therefore arrives long after the site is back. It is not
  the recovery time; the HTTP RECOVERED notification is.
- **ALARM → INSUFFICIENT_DATA directly** when the probe stops during an
  ALARM: there are never 52 good datapoints, so no OK and no NORMALIZED
  is sent. The INSUFFICIENT message follows instead (case `sparse9`).
- **INSUFFICIENT_DATA** under `missing` came about 66 minutes after the
  last published datapoint (cases `sparse9`, `stop`).

### 13.2 CloudWatch evaluation is not identical to the backtest

The backtest (§13.1) evaluated only complete 60-minute wall-clock windows.
The integration test on 2026-11-27 (§13.6) measured what CloudWatch does
instead. AWS documents the general rules
([missing data](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html),
[alarm evaluation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html))
but not the exact range for this alarm.

| Behavior | Measured | Basis |
|---|---|---|
| Evaluation range | **About 66 minutes** (60 periods plus about 6). A failure 65 minutes back was outside it; INSUFFICIENT_DATA came about 65.5–65.7 minutes after the last datapoint; transitions were about 1 minute later than a 60-minute wall-clock model | **Estimate** from three observations. Not documented by AWS. It may differ with other gaps |
| ALARM → OK | Needs ≥ 52 non-breaching datapoints as well as < 9 breaching ones | Stated in the alarm reason |
| Premature ALARM | 9 datapoints in total, all failed, raised ALARM | Matches the AWS documentation |
| ALARM → INSUFFICIENT_DATA | Directly, without OK, when data stops during ALARM | Case `sparse9` |
| `reasonData.recentDatapoints` | 0–61 values, with `null`; not the evaluated window | 11 events |
| `reasonData.evaluatedDatapoints` | Only the 9 breaching datapoints on ALARM; 46–52 on OK and INSUFFICIENT_DATA | 11 events |

Consequences:

- The backtest's 4 episodes and 197 minutes hold for that complete
  evidence. With gaps, CloudWatch can alarm earlier (premature ALARM),
  clear later (52-datapoint rule) or skip OK. The backtest model cannot
  predict these cases. Use CloudWatch's alarm history as the record.
- `reasonData` cannot give a reliable failed-sample count or burn rate.
  The former "Observed" line used `recentDatapoints` and could compute a
  rate in only 1 of 11 real events (correctly), so it was removed on
  2026-11-28 (§13.4). The counts quoted in the reason sentence were not
  used either: on OK, "N out of the last 60" counts missing minutes as
  not breaching, so 60 − N is not the number of failures.
- The message's "Last 60" count (§13.11) is a separate wall-clock
  window. It can differ from what CloudWatch evaluated, especially with
  missing data. That is expected and the message says so.

In the 2026-11-17–11-27 evidence, data is missing only before 11/17 03:43
JST and no crossing is near it, so the backtest result is unaffected.

### 13.3 Path and independence from EC2

```text
EventBridge rate(1 minute) → Lambda gmsv5-availability (probe) → CloudWatch HTTPUp
CloudWatch alarm (native evaluation)
  → EventBridge rule gmsv5-availability-burn-rate (this alarm's ARN and name only)
  → Lambda gmsv5-availability → burn_rate.py
      (only for a message to send: burn_rate_context.py reads the HTTP
       state row and GetMetricData HTTPUp, read only, §13.11)
  → notify() → Discord CloudFront distribution (discord-alert-egress) → discord.com
```

No EC2, Nginx, Grafana or Prometheus is in the path. No new Lambda, SNS
topic or table. The 2026-11-28 redesign adds one read-only permission to
the execution role, `cloudwatch:GetMetricData` (§13.11). The only
resource-based permission lets this rule invoke the function.

The alarm has no actions. CloudWatch sends state-change events to
EventBridge whether or not an alarm has actions, so delivery is gated by
the rule state and a Lambda variable (§13.5), not by
`actions_enabled`.

**Shared fate:** the same function publishes `HTTPUp` and sends the
messages. If the function is broken or cannot reach Discord, the probe
and the alarm notification fail together. A meta-alarm on the function's
errors through another path is not part of P2.

### 13.4 State handling and delivery

Records: one row, `instance_id = burnrate#gmsv5-availability-burn-rate-fast`,
`event_time = ~STATE`. It is separate from EC2, `http#`, `deployment#`
and `spot#` rows and has no TTL. Writes happen only on alarm state
changes: 3 updates per delivered notification, 2 per shadow-logged one.
None happen per minute.

| Attribute | Meaning |
|---|---|
| `alarm_state`, `state_at`, `previous_state`, `event_id`, `reason_kind` | The newest accepted state change. `state_at` is `detail.state.timestamp` in UTC with ms |
| `notified_state`, `notified_at` | **Live ledger.** The last message actually delivered, and the state it was for. Written only by `live` |
| `shadow_notified_state`, `shadow_notified_at` | **Shadow ledger.** The last message logged (not delivered) in `shadow`. Written only by `shadow`; `live` never reads it |
| `lease_at`, `lease_until` | A send in progress (90 s lease, equal to the Lambda timeout) |

Rules:

1. An event is accepted only if its `state_at` is newer than the stored
   one (conditional update). Duplicates and older events never
   overwrite a newer state.
2. Every invocation then decides from the row, not from its event, what
   the **current** state is still owed:

   | Current state | Last delivered | Message |
   |---|---|---|
   | ALARM | not ALARM | `HIGH — Availability Burn Rate: ALARM` |
   | OK | ALARM | `HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)` |
   | OK | INSUFFICIENT_DATA | `INFO — Availability Burn Rate: DATA RESTORED` |
   | OK | nothing or OK | none (includes the first INSUFFICIENT_DATA → OK after creation) |
   | INSUFFICIENT_DATA, reason `Unchecked: ...` | any | none (created or reset, not a data gap) |
   | INSUFFICIENT_DATA | not INSUFFICIENT_DATA | `WARN — Availability Burn Rate: DATA INSUFFICIENT` |

3. Send: claim the lease (condition: still the current state, not yet
   delivered, no live lease) → `notify()` → record `notified_*` and drop
   the lease. Only a successful `notify()` is recorded.
4. If `notify()` fails, the lease is released and the error is raised.
   Lambda's asynchronous retry (default: 2 retries, about 1 and 2
   minutes later) redelivers the event, and rule 2 sends again.
5. An invocation that finds a live lease raises `NotificationInFlight`
   and is retried later. A sender that finds the state changed during its
   send re-checks and sends what the new state is owed. Then an ALARM
   in flight cannot hide the following OK.

**Message content (2026-11-28).** The purpose is the first response to
an incident, not reporting a burn rate. No burn rate, availability or
error budget is computed or shown; the 14.4x rationale stays in §13.1.

| Line | Meaning |
|---|---|
| Header | Severity label and kind. `HIGH`/`WARN`/`INFO` is a label; it does not set a Discord priority |
| `Alarm threshold: 9/60 failed datapoints` | The configured condition. Never a measurement |
| `Now: UP / DOWN / FAILING / UNKNOWN …` | The latest scheduled HTTP check **when the message is generated**, not at the alarm transition (§13.11) |
| `Last 60 completed min (HH:MM–HH:MM JST): failed F / ok O / missing M` | HTTPUp for the 60 completed minutes before the alarm state timestamp. F + O + M = 60. An independent wall-clock window, not CloudWatch's evaluation range (§13.2) |
| `Pattern: N observed episodes, longest L min` | Runs of consecutive failed minutes in that window. A missing minute ends a run, because a failure across a gap is not proven continuous. An episode touching a window edge is counted only inside the window, and the line says so |
| `Alarm: PREV -> STATE at HH:MM:SS JST (… UTC, alarm state timestamp)` | The transition |
| Meaning and footnote | What the kind means and what it does not prove |

NORMALIZED says that the CloudWatch alarm returned to OK and that this
is not when the site recovered. DATA RESTORED says it does not confirm
the site is up. INSUFFICIENT_DATA says it is a monitoring data problem,
not downtime or recovery. If a lookup fails, its line says
`unavailable (<code>)` or `Now: UNKNOWN — …` and the message is still
sent. Samples: [As-Built §6](./availability-burn-rate-as-built-2026-11-27.md#6-message-redesign-2026-11-28).

**Residual cases (at-least-once, not exactly-once):**

| Case | Result |
|---|---|
| `notify()` succeeded, then recording failed or the function timed out | Retry sends the message again (duplicate) |
| The relay timed out on the client but Discord received the message | Duplicate on retry |
| All three attempts fail (Discord, CloudFront or SSM unavailable for about 3 minutes) | Message lost. Log line `{"burn_rate": "notify_failed", ...}`. No DLQ |
| ALARM delivery failed on all attempts and the alarm is already OK again | Neither ALARM nor NORMALIZED is sent. The episode is only in the CloudWatch alarm history |
| Event older than 6 hours (Lambda maximum event age) | Dropped by Lambda |

### 13.5 Rollout gate

The variable `burn_rate_alarm_rollout` in `variables.tf` sets both gates:

| Value | EventBridge rule | `BURN_RATE_DELIVERY` | Effect |
|---|---|---|---|
| `off` (default) | `DISABLED` | `disabled` | Alarm evaluates. The Lambda is not invoked |
| `shadow` | `ENABLED` | `disabled` | The Lambda records state and logs `{"burn_rate": "shadow", "delivered": false, "would_send": ..., "message": "<full text>"}`, with production context. No Discord message |
| `live` | `ENABLED` | `enabled` | Discord messages |

Change the default in `variables.tf` in a reviewed commit for each
stage. `terraform.tfvars` is not in Git and `-var` is not kept, so either
would make the next plan revert the stage. The Lambda sends only when
the variable is exactly `enabled`.

Changing stages:

- `off` → `shadow` changes only the rule.
- `shadow` → `live` changes only the Lambda environment.
- Moving to `live` during an ALARM sends nothing for that episode,
  because nothing was delivered for it. The next transition is handled
  normally.

**What `shadow` shows.** Shadow follows the same lifecycle as `live`:
ALARM, NORMALIZED, DATA INSUFFICIENT and DATA RESTORED are logged when
`live` would send them, with the full text and production context. It
decides from its own ledger (`shadow_notified_*`, §13.4), so:

- Each owed message is logged at most once. Shadow marks its ledger with
  a conditional write **before** logging, so a duplicate, older or
  concurrent event logs nothing and makes no context lookup. If the
  function dies between the mark and the log line, that one shadow
  message is missing from the log (no retry); `live` is unaffected.
- ALARM → INSUFFICIENT_DATA logs DATA INSUFFICIENT, never NORMALIZED.
  The later OK logs DATA RESTORED.
- Shadow writes neither `notified_*` nor a lease. A shadow message is
  never a delivery: the log line says `"delivered": false`, and the
  result lists it as `shadow:<kind>`.
- `live` ignores the shadow ledger. After `shadow` → `live`, live starts
  from its own (empty) ledger as described above. A later `live` →
  `shadow` switch starts shadow from its old ledger, so its first log
  line can refer to a state that `live` already announced.

While the site is healthy the alarm stays OK and shadow logs nothing.

**Alarm state check around the `live` switch.** Run immediately before
the apply and again immediately after it:

```bash
aws cloudwatch describe-alarms --profile gms-admin --region ap-northeast-1 \
  --alarm-names gmsv5-availability-burn-rate-fast \
  --query 'MetricAlarms[0].{State:StateValue,Updated:StateUpdatedTimestamp,Reason:StateReason}'
```

- **Before:** apply only if `State` is `OK`. If it is `ALARM` or
  `INSUFFICIENT_DATA`, postpone the switch as a rule until it has been
  `OK` again, and investigate the cause. An episode that is already
  running when `live` starts is never announced, and its OK is not
  announced either.
- **After:** confirm that `State` is still `OK` and `Updated` has not
  changed. If the state changed during the apply, record it. Read the
  Lambda log for a `{"burn_rate": ...}` line at that time and report it
  with the switch. If the state is `ALARM` and no message arrived, the
  episode was not announced (see above). Do not use `set-alarm-state` to
  replay it.

**First apply (stage `off`).** Expected plan: 4 to add
(`aws_cloudwatch_metric_alarm.burn_rate_fast`,
`aws_cloudwatch_event_rule.burn_rate`,
`aws_cloudwatch_event_target.burn_rate`,
`aws_lambda_permission.burn_rate`). The plan also has 1 to change:
`aws_lambda_function.availability`, with new code and the variables
`BURN_RATE_ALARM_NAME`, `BURN_RATE_ALARM_ARN` and `BURN_RATE_DELIVERY`.
Nothing is destroyed or replaced. Do not apply a plan that touches the
table, IAM role policies, other rules or the Discord distribution.
Applied 2026-11-27 with exactly this plan.

**Message redesign apply (2026-11-28 commit, stage stays `off`).**
Expected plan: 0 to add, 2 to change, 0 to destroy:

- `aws_lambda_function.availability`: `source_code_hash` only (new code
  with `burn_rate_context.py`). All 10 environment variables unchanged,
  `BURN_RATE_DELIVERY` still `disabled`.
- `aws_iam_role_policy.lambda_runtime`: one statement added,
  `ReadBurnRateContextMetric` (§13.11). The other 5 statements unchanged.

The alarm, both burn rate rule resources, the permission, the HTTP probe
rule and the table must be no-op. Do not apply a plan with anything else.

### 13.6 Integration test (isolated, no production outage)

**Done on 2026-11-27** with Product Owner approval
([As-Built §4](./availability-burn-rate-as-built-2026-11-27.md#4-isolated-cloudwatch-integration-test)).
It measured CloudWatch's evaluation (§13.1, §13.2). It did not test
EventBridge → Lambda delivery to the production function.

How it was run, for a repeat:

- Namespace `GMSv5/BurnRateIntegrationTest`, one temporary alarm per case
  named `gmsv5-burnrate-itest-<case>`, identical to production except
  namespace and `Target=itest-<case>`. Never `GMS/Availability`, never
  the production alarm, no `set-alarm-state`.
- Samples were backdated up to 90 minutes (`put-metric-data`, 0 or 1,
  `Unit=Count`), then the continuing cases got one good sample per minute
  for 75 minutes.
- A temporary capture rule (`gmsv5-burnrate-itest-capture`, prefix match)
  sent the state-change events to a temporary log group with 1-day
  retention. Alarm history and events were saved, then every temporary
  resource was deleted and the deletion checked.
- Cases: 8 of 60 failed; 9 of 60; 2 consecutive; 9 old failures
  (normalize); 8 failures + 10 missing + 1 failure 65 minutes back
  (lookback); only 9 samples, all failed, then nothing (sparse9); 60 good
  samples, then nothing (stop).
- Duration about 1 h 20 min. Cost about USD 0.01–0.02 (limit USD 1).

The production ARN check rejected the test events as `unmonitored
alarm`; it must never be relaxed for a test.

**Lambda path (not yet tested).** Delivery of a real state-change event
to the function, and the message text with production context, are
first seen in `shadow` when the alarm next changes state. Forcing that
with `aws cloudwatch set-alarm-state` on the production alarm is a
notification-path test only, never a test of the 9-of-60 evaluation,
and needs separate Product Owner approval. In `live` it sends real
messages.

### 13.7 Checks after apply (read only)

```bash
aws cloudwatch describe-alarms --profile gms-admin --region ap-northeast-1 \
  --alarm-names gmsv5-availability-burn-rate-fast \
  --query 'MetricAlarms[0].{State:StateValue,Reason:StateReason,M:DatapointsToAlarm,N:EvaluationPeriods,Missing:TreatMissingData,Actions:AlarmActions}'
aws events describe-rule --profile gms-admin --region ap-northeast-1 \
  --name gmsv5-availability-burn-rate --query '{State:State,Pattern:EventPattern}'
aws events list-targets-by-rule --profile gms-admin --region ap-northeast-1 \
  --rule gmsv5-availability-burn-rate --query 'Targets[].Arn'
aws lambda get-function-configuration --profile gms-admin --region ap-northeast-1 \
  --function-name gmsv5-availability \
  --query '{Sha:CodeSha256,Delivery:Environment.Variables.BURN_RATE_DELIVERY}'
```

Expected after the first apply:

- The alarm is `OK` once it has evaluated. It is `ALARM` only if 9 of
  the last 60 samples really failed.
- The rule is `DISABLED`, and its target is the function.
- `BURN_RATE_DELIVERY` is `disabled`.
- The existing checks still pass: HTTP probe, `HTTPUp` = 1 and no new
  Lambda errors. HTTP, EC2, Spot and deployment notifications behave as
  before.

Additionally after the message redesign apply:

- The role policy has `ReadBurnRateContextMetric` with only
  `cloudwatch:GetMetricData` and the `aws:RequestedRegion` condition:
  `aws iam get-role-policy --role-name gmsv5-availability-lambda-role
  --policy-name gmsv5-availability-runtime --query 'PolicyDocument.Statement[].Sid'`.
- Production context is first exercised in `shadow` (§13.5). There, a
  `Last 60 completed min: unavailable (AccessDenied)` line means the IAM
  statement is missing.

Do not print the ARN-bearing outputs into documents. They contain the
account ID.

### 13.8 Rollback

- **Stop messages (first choice):** set `burn_rate_alarm_rollout` to
  `off` and apply. Plan: 1 change (the rule), or 2 when coming from
  `live` (rule and Lambda environment). In an emergency,
  `aws events disable-rule --name gmsv5-availability-burn-rate` stops
  invocations immediately. Then make the same change in Terraform the
  same day so that the plan shows no changes.
- **Full rollback:** revert both burn rate commits (the message
  redesign first) and apply. Expected plan: the 4 resources of §13.5
  destroyed, the `ReadBurnRateContextMetric` statement removed and the
  Lambda function changed back to the 2026-11-25 build. Nothing else.
- **Message redesign only:** revert its commit and apply. Expected plan:
  2 changes, the Lambda code back to the 2026-11-27 `off` build and the
  `ReadBurnRateContextMetric` statement removed. The alarm and rule stay.
- **Data:** leave the `burnrate#` row. The previous code never reads it.

### 13.9 Cost

| Item | Incremental cost |
|---|---|
| One standard-resolution metric alarm | USD 0.10 per month at list price, or none within the CloudWatch free tier of 10 alarm metrics. No other alarm existed in the read-only check before P2. Confirm on the CloudWatch pricing page for the region |
| `HTTPUp` metric | None. It already exists |
| EventBridge | None for AWS service events (CloudWatch alarm state changes) delivered to rules |
| Lambda | A few invocations per state change. The backtest had 4 episodes in 10 days, so a few dozen per month. Negligible, within the free tier |
| DynamoDB (on-demand) | About 3 writes and 3–5 reads per notification. Negligible |
| CloudWatch Logs | A few lines per state change. Negligible |
| GetMetricData (2026-11-28 redesign) | One metric requested per message sent or shadow-logged. List price USD 0.01 per 1,000 metrics requested; not in the free tier. A few dozen per month: below USD 0.001 |
| DynamoDB for `Now` | One strongly consistent read of the existing HTTP row per message. Negligible |
| Integration test (§13.6) | About USD 0.01–0.02, once (2026-11-27) |

### 13.10 Limitations

- CloudWatch's evaluation with missing data differs from the backtest
  (§13.2). The about-66-minute range is an estimate from one test.
- NORMALIZED can lag the real recovery by about an hour, and is skipped
  when the probe stops during an ALARM (§13.1). It never gives the
  recovery time.
- `Last 60` is not what CloudWatch evaluated, and data that arrives late
  shows as missing (§13.11).
- Shared fate: the same function publishes `HTTPUp` and sends these
  messages (§13.3). A broken function or unreachable Discord loses both.
  No meta-alarm.
- Delivery is at least once with the residual cases in §13.4. No DLQ: a
  message whose three attempts all fail is lost (log line only).
- The 25 s left for `notify()` and its record is a reserve the lookups
  never use, not a bound on `notify()` itself (§13.11).
- An abandoned lookup holds one of the 2 worker slots until its SDK call
  ends. While both are held, the context lines say `unavailable (busy)`.
- The alarm sees only the external ingress probe (HTTP 200 on `/`), not
  translation API success. P3 covers translation metrics separately.

### 13.11 Responder context lookups (2026-11-28 redesign)

`lambda/burn_rate_context.py` adds the `Now`, `Last 60` and `Pattern`
lines. Both lookups are read only and best effort.

**When.** Only for a message that is about to be sent, after the lease
is claimed, or that is logged in `shadow` after its ledger mark. Events rejected by the ARN and name check, older or
duplicate events, held leases and states owed nothing make no lookup.
A Lambda retry after a failed send looks up again.

**Now.** One `GetItem` (strongly consistent, low-level client, existing permission) of the
existing HTTP row `instance_id = http#sunlightjetrans.com`,
`event_time = ~STATE`, written by the scheduled probe
(`track_http_status` in `handler.py`):

| Attribute | Written | Meaning |
|---|---|---|
| `last_slot` | Every probe | Scheduled minute of the latest check, `YYYY-MM-DDTHH:MM` UTC |
| `http_failures` | Every probe | Consecutive failed checks; 0 after a passed check |
| `http_status` | `up` on a passed check; `down` at the 2nd consecutive failure | DOWN is confirmed at the second failure. The confirmation time is not stored |
| `first_fail_at` | First failed check of a run | When that check was recorded (Lambda clock, s) |
| `down_since` | At DOWN confirmation | Copy of `first_fail_at`; removed on a passed check |

| Line | Condition |
|---|---|
| `Now: UP (last check HH:MM JST)` | Latest check is fresh, passed, status `up` |
| `Now: DOWN — N min (since first failed check HH:MM JST); K consecutive failed checks` | Fresh, status `down`. N counts from the **first failed check**, not from the DOWN confirmation, to the message time |
| `Now: FAILING — …; DOWN not confirmed (2 needed)` | Fresh, one failed check, not yet DOWN |
| `Now: UNKNOWN — no HTTP check since HH:MM JST (last result: …)` | The latest check is 180 s or more old (or in the future) |
| `Now: UNKNOWN — …` | No row, malformed row, read failure, or time budget used up |

It never says UP for a stale or unreadable state.

**Last 60.** One `GetMetricData` call (rarely a second page):
`GMS/Availability` `HTTPUp`, `Target=sunlightjetrans.com`, period 60,
`Minimum`, no unit, region `ap-northeast-1`. These are fixed in the code
and match the alarm (a test compares them with `burn_rate.tf`); nothing
in the event selects them. The window is
`[floor(state_at) − 60 min, floor(state_at))`, from the alarm state
timestamp, never from the current time, so a retry hours later counts
the same minutes. `StartTime` is inclusive and `EndTime` exclusive in
GetMetricData. Each slot is failed (`< 1`, the alarm's comparison), ok
or missing. Duplicates keep the minimum; out-of-range, off-minute,
non-numeric and non-finite datapoints are ignored and counted. Order
does not matter. `PartialData` follows `NextToken` up to 2 pages, then
the line says `partial API response`. `Forbidden`, `InternalError` or a
malformed response make the line `unavailable`.

**Time limits.** SDK timeouts apply per socket operation, so they do
not bound a call. The bound is a wall-clock deadline in the code:

| Limit | Guaranteed by | Value |
|---|---|---|
| Deadline for all lookups of one invocation | Code (each call runs in a worker thread; the caller waits only until the deadline, then abandons it) | `min(t0 + 30 s, Lambda end − 25 s)`, `t0` = start of the burn rate handling, before `accept` |
| Lookups never wait into the last 25 s of the Lambda | Code (same deadline) | Lambda timeout 90 s |
| A lookup or client creation is not started | Code | When less than 2 s remain before the deadline |
| Pages | Code | At most 2 GetMetricData calls, both under the same deadline |
| Lookup worker threads per execution environment | Code (`BoundedSemaphore(2)`, taken before the thread starts, released only when the worker ends, also on error) | At most 2, including abandoned ones. With none free the line is `unavailable (busy)`, without waiting |
| Invocation length, lease | Lambda timeout | 90 s, equal to the lease (§13.4) |

| Estimate (SDK, CPU and network dependent) | Value |
|---|---|
| Client creation (both clients, once per execution environment, on the main thread) | About 7 ms and 2 MB measured locally; slower on a cold 128 MB Lambda (not measured). It counts against the 30 s budget, because the deadline is fixed at `t0`, but it cannot be interrupted: a creation that started with ≥ 2 s left is not cut off. It is local work (service model, endpoint, credentials from the Lambda environment), with no network call |
| One lookup when AWS answers | Tens of ms |
| One lookup when timeouts fire | About 10–13 s: 2 attempts × (2 s connect + 3 s per read) plus standard-mode backoff. DNS is not covered by the connect timeout. A slow trickle can extend a read. The deadline cuts the wait off |
| How long an abandoned worker runs | Until its SDK call ends (same estimate). It cannot hold more than 2 slots |
| `notify()` | `urlopen` timeout 8 s per socket operation. On a cold container `relay_token()` calls SSM with SDK defaults (60 s connect and read, legacy retries). The delivery record uses the handler's table client with SDK defaults. Existing code shared with all notifications; not bounded by this change |

So the 30 s and the 25 s are guaranteed as limits on waiting for lookups.
They do not bound client creation once started, nor `notify()` and the
record. If those overrun, the Lambda times out and its retry sends again
(a possible duplicate, §13.4).

**Threads.**

- Both low-level clients are created on the main thread, only when the
  first lookup of an invocation is about to run (never at import, in
  the HTTP probe path, or for an event owed nothing), and reused for the
  life of the execution environment.
- A creation failure makes both lines `unavailable (<error>)` and is not
  retried within the invocation. The message is still sent.
- Workers never create a boto3 Session, client or resource. They only
  make the one read call (`GetItem` or `GetMetricData`) on a client
  they are handed.
- A worker's result goes to a buffer owned by that one call, so a result
  that arrives after the deadline is discarded. It cannot reach a later
  message, a state write or another event.
- A worker sends no message, writes nothing and logs nothing.

**IAM.** Statement `ReadBurnRateContextMetric`: `cloudwatch:GetMetricData`
on `*`, condition `aws:RequestedRegion = ap-northeast-1`. GetMetricData
has no resource ARN for classic metrics (its only resource type,
`dataset`, is optional and for PromQL datasets), and `cloudwatch:namespace`
does not apply to it
([Service Authorization Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudwatch.html)),
so the role can read any CloudWatch metric in Tokyo. The code reads one.

**Failures.** Any error or timeout becomes `unavailable (<code>)` or
`Now: UNKNOWN — HTTP state unavailable (<code>)`, with the AWS error
code or exception class only, never AWS's message text. The message is
sent anyway. The log line is `Burn rate context <part> unavailable: <code>`.

## 14. Continuous failure alarm (6 of 6)

**Status (2026-11-28): applied in stage `off` at 03:29 JST.** The alarm
evaluates. Its rule is `DISABLED` and `CONTINUOUS_FAILURE_DELIVERY` is
`disabled`, so nothing is sent. CloudWatch's evaluation was measured in
an isolated integration test. The missing-data choice (§14.2) was
confirmed by the Product Owner.
Record applies in a dated As-Built
([Continuous Failure As-Built](./availability-continuous-failure-as-built-2026-11-28.md)).

### 14.1 What it detects

| Signal | Detects | Priority |
|---|---|---|
| HTTP DOWN / RECOVERED (§2) | 2 consecutive failed checks, and the first passed one | Existing |
| Continuous failure `gmsv5-availability-continuous-failure` (this section) | The 6 most recent one-minute checks all failed: a long outage, early | HIGH |
| Burn rate `gmsv5-availability-burn-rate-fast` (§13) | 9 of the last 60 checks failed: accumulated interruptions | HIGH |
| UptimeRobot (§9) | Independent of AWS | Low |

Backtest (evidence `cloudwatch-2026-11-27`,
`continuous-failure-backtest-v1.json`, strict wall-clock model): of 34
failure episodes, 4 lasted 6 minutes or more.

- 1 occurred while the burn rate alarm was already active.
- 1 was detected 3 minutes before the burn rate alarm.
- 2 were never alarmed by the burn rate alarm during the episode.
- The 30 shorter episodes are not alarmed.

Replaying the same evidence through the sliding-window model of §14.2
gives the same 4 episodes: no missing minute is next to a failure in that
evidence. Both alarms notify independently. There is no suppression and
no composite alarm.

### 14.2 Missing data: integration test and choice

Isolated test on 2026-11-28 (namespace
`GMSv5/ContinuousFailureIntegrationTest`, 44 temporary alarms, about
55 minutes):

- **Variants compared on the same data:**
  - sliding window with `notBreaching`;
  - sliding window with `missing`;
  - metric math `FILL(m1, 1)`;
  - wall clock window with `notBreaching`.
- **Continuous delay:** every datapoint of a case was published 70, 130 or
  250 s after its minute began, not only one late datapoint.
- **Evidence:** outside the repository
  ([As-Built §3](./availability-continuous-failure-as-built-2026-11-28.md#3-isolated-cloudwatch-integration-test)).

| Case | sliding `notBreaching` | sliding `missing` | `FILL(m1, 1)` | wall clock |
|---|---|---|---|---|
| 5 consecutive failures; spread failures | OK | OK | OK | OK |
| 6, 8 and 14 consecutive failures | ALARM | ALARM | ALARM | ALARM |
| `FFF-FFF` (one missing minute) | **ALARM** | **ALARM** | OK | OK |
| Continuous delay 70 s | ALARM | ALARM | ALARM | ALARM |
| Continuous delay 130 s and 250 s | ALARM (about 3 and 5 min) | ALARM (about 3 and 4.5 min) | **never** | **never** |
| Probe stops during ALARM | OK after about 6 min ("treated as NonBreaching") | **INSUFFICIENT_DATA after about 12 min** | OK after about 2 min | OK after about 2 min |
| No data at all | OK | INSUFFICIENT_DATA | OK | OK |

Times are minutes after the end of the last datapoint. Common to all
variants:

- ALARM came 1.1–2.0 minutes after the sixth failed minute began.
- OK came 1.1–2.0 minutes after the last failed minute ended.
- No variant flapped. The 14-minute and delayed runs stayed in ALARM
  until real recovery.
- 95 events matched 95 history items, with no duplicates.

**Choice: sliding window with `treat_missing_data = "missing"`.**

- `FILL(m1, 1)` and the wall clock window are strict about missing
  minutes, but they **never alarmed** when data arrived 130 s or more
  late: the newest minute was always filled or missing. Rejected, as
  the AWS documentation on metric math warns.
- With the sliding window, CloudWatch evaluates the **6 most recent real
  datapoints** in an evaluation range of about 12 periods. A missing
  minute between failures does not break the run (`FFF-FFF` alarmed),
  so this is not strictly 6 wall-clock minutes. The range is estimated
  from the `stop` case, the same N + 6 pattern as §13.2. It is not
  documented by AWS.
- `missing` (not `notBreaching`): leaving ALARM needs a **real passed
  check** among the 6 most recent datapoints, so a stopped probe never
  produces NORMALIZED. It goes to INSUFFICIENT_DATA instead. In the
  isolated test that took 11.7 minutes after the last datapoint (an
  observation from one test, not a guaranteed or measured production
  value), much earlier than §13 (about 66 minutes, also observed). With `notBreaching`, the same stop produced OK,
  stated as "missing datapoint was treated as [NonBreaching]".

### 14.3 Configuration and path

- **Alarm:** `gmsv5-availability-continuous-failure`, `GMS/Availability`
  `HTTPUp` `Target=sunlightjetrans.com`, `Minimum`, 60 s, 6 of 6, `< 1`,
  `missing`. Sliding window (default), no unit, no actions.
- **Rule:** `gmsv5-availability-continuous-failure`, matching this alarm's
  ARN and name only, with Lambda permission
  `AllowContinuousFailureAlarmEvents`.
- **Lambda:** the existing function. `handler.py` picks the alarm's
  profile by `detail.alarmName`. `burn_rate.py` then applies the same
  name and ARN check, ordering, lease, live and shadow ledgers and
  context lookups as §13.4 and §13.11.
- **State row:** `contfail#gmsv5-availability-continuous-failure` /
  `~STATE`, separate from `burnrate#…`. An event whose name and ARN
  belong to different alarms is rejected.
- **Environment:** `CONTINUOUS_FAILURE_ALARM_NAME`,
  `CONTINUOUS_FAILURE_ALARM_ARN`, `CONTINUOUS_FAILURE_DELIVERY`. The name
  and ARN are built from the region, the caller account and the alarm
  name, so a plan shows the existing variables unchanged.
- **IAM:** no change; the role already has GetItem and GetMetricData.

### 14.4 Messages

Headers: `HIGH — Availability Continuous Failure: ALARM`,
`… NORMALIZED (alarm back to OK)`, `WARN … DATA INSUFFICIENT`,
`INFO … DATA RESTORED`. Line 2 is `Alarm threshold: 6/6 failed datapoints
in a row (the most recent ones; missing minutes are skipped)`. `Now`,
`Last 60` and `Pattern` are as in §13.11.

- **ALARM** says that the 6 most recent checks all failed and that a
  missing minute does not break the run.
- **NORMALIZED** says that a passed check is among the 6 most recent
  datapoints, that this is not the confirmed recovery time and does not
  confirm the site is up.
- **DATA INSUFFICIENT** says "Insufficient HTTPUp data to evaluate the
  alarm. This is a monitoring data issue, not evidence of an outage or
  recovery." It gives no duration. `Now` (last check time) and `Last 60`
  (missing count) show the actual data. The about-12-minute delay before
  it (§14.2) is an integration test observation, not a guaranteed time.

Samples:
[As-Built §5](./availability-continuous-failure-as-built-2026-11-28.md#5-message-samples-local-test).
A 17-minute outage gives, in order:

1. HTTP DOWN.
2. Continuous ALARM (about 6–7 minutes after the outage starts).
3. Burn rate ALARM (about 9–10 minutes).
4. HTTP RECOVERED.
5. Continuous NORMALIZED (1–2 minutes after recovery).
6. Burn rate NORMALIZED (about 52 minutes later).

### 14.5 Rollout gate

The variable `continuous_failure_alarm_rollout` (`off` default,
`shadow`, `live`) sets this alarm's rule state and
`CONTINUOUS_FAILURE_DELIVERY` exactly as §13.5 does for the burn rate
alarm. The two variables are independent. The shadow behavior and the
alarm state check around `live` are the same as in §13.5, with this
alarm's name.

**First apply (stage `off`).** Expected plan: 4 to add, 1 to change,
0 to destroy:

- added: `aws_cloudwatch_metric_alarm.continuous_failure`,
  `aws_cloudwatch_event_rule.continuous_failure` (`DISABLED`),
  `aws_cloudwatch_event_target.continuous_failure` and
  `aws_lambda_permission.continuous_failure`;
- changed: `aws_lambda_function.availability`, with new code and 3 new
  variables. The existing 10 variables are unchanged.

IAM, the table, the burn rate alarm and rule, and the HTTP probe rule
must be no-op.

### 14.6 Checks after apply (read only)

The same as §13.7, for this alarm and rule:

- Configuration as in §14.3; rule `DISABLED`; `CONTINUOUS_FAILURE_DELIVERY=disabled`.
- The alarm `OK` once evaluated.
- The burn rate alarm, its rule and its 10 variables unchanged.
- HTTP probe and `HTTPUp` continuing, no Lambda errors.

### 14.7 Rollback

- Stop messages: set the variable to `off` and apply.
- Remove: revert the commit and apply. Expected plan: the 4 resources
  destroyed, and the Lambda back to the previous build with 10
  variables. Leave the `contfail#` row; the previous code never reads it.

### 14.8 Cost

| Item | Incremental cost |
|---|---|
| One standard-resolution metric alarm | USD 0.10 per month at list price, or within the free tier of 10 alarm metrics (2 alarms in this module). Confirm on the CloudWatch pricing page |
| Lambda, DynamoDB, GetMetricData, Logs | A few calls per state change; negligible |
| Integration test (2026-11-28) | Below USD 0.05, once (limit USD 1) |

### 14.9 Limitations

- Not strictly 6 wall-clock minutes. Failures within about 12 minutes on
  both sides of a missing minute can alarm (§14.2). The strict variants
  could not detect continuously late data.
- The about-12-minute range is an estimate from one test.
- Late data delays ALARM by about the delay (3–5 minutes at 130–250 s).
- A stopped probe gives DATA INSUFFICIENT, from this alarm after about 12
  minutes and from §13 after about 66 minutes. Both are observations in
  isolated tests, not guaranteed times. A broken function sends
  nothing: shared fate as in §13.3. There is no meta-alarm.
- NORMALIZED needs only one passed check. A flapping site can give
  several ALARM and NORMALIZED pairs. This was not seen in the
  evidence, where failures were contiguous.
- Delivery is at least once; no DLQ (§13.4). Worker slots (§13.11) are
  shared with the burn rate alarm.
