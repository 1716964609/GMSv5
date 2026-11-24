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
