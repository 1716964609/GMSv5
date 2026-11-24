# GMS v5 — EC2 Spot Interruption and Recovery Runbook

- **Owner:** Infrastructure / SRE
- **Updated:** 2026-11-16 JST; cross-references added 2026-11-19; reason evidence (§9) added and deployed 2026-11-25
- **Scope:** Production Application EC2 and Observability EC2
- **Evidence:** [2026-11-16 As-Built Section 10](./observability-as-built-2026-11-16.md)
- **Status:** Operational recovery observed; automated recovery after a future interruption not fully validated.

> **Added 2026-11-19:** two things changed after this runbook was written.
> External monitoring independent of the Observability EC2 was deployed on
> 2026-11-17 ([As-Built](./availability-external-as-built-2026-11-17.md),
> [Runbook](./availability-external-runbook.md)). Application releases are
> now managed by a deployment engine with boot recovery
> ([CI/CD Runbook](./cicd-operations-runbook.md)). Boot recovery restores
> the previous release when the same instance restarts in the middle of a
> deployment. It does not replace an instance or rebuild its ENI or EBS
> volumes, and it has not been exercised by an actual Spot reclamation.

## 1. Incident model

Both EC2 instances are Persistent Spot requests with
interruption behavior `stop`. Both use `t4g.small` in
`ap-northeast-1a`, making them dependent on the same
Spot capacity pool.

AWS may reclaim Spot capacity with best-effort advance
interruption notification. Reallocation can be triggered
by capacity needs, but an On-Demand customer's specific
action cannot be inferred from CloudTrail alone.

Spot interruption can cause:
- Application downtime on the single Production EC2.
- Missing telemetry while Application or Observability is stopped.
- Grafana `DatasourceError` if Prometheus does not recover.
- Unavailable alerts if the entire Observability host is stopped.

## 2. Instance inventory

| Role | Instance ID | Spot request |
|---|---|---|
| Application | `i-0decf6bc205e273d9` | `sir-rmf7nbph` |
| Observability | `i-021170e56849c597b` | `sir-r4gr2zqj` |

Region: `ap-northeast-1`.

## 3. AWS-side investigation (Mac Terminal)

Read-only state inspection:

~~~bash
aws ec2 describe-instances \
  --region ap-northeast-1 \
  --instance-ids \
    i-0decf6bc205e273d9 \
    i-021170e56849c597b \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,LaunchTime:LaunchTime,AZ:Placement.AvailabilityZone}' \
  --output table
~~~

Read-only Spot request inspection:

~~~bash
aws ec2 describe-spot-instance-requests \
  --region ap-northeast-1 \
  --spot-instance-request-ids sir-rmf7nbph sir-r4gr2zqj \
  --query 'SpotInstanceRequests[].{Instance:InstanceId,State:State,Code:Status.Code,Message:Status.Message,Updated:Status.UpdateTime}' \
  --output table
~~~

Inspect CloudTrail `StopInstances` and `StartInstances`
for each instance. Preserve the exact UTC/JST conversion,
event ID, `invokedBy`, state transitions and Request ID.

Do not count repeated `stopped -> stopped` responses as
additional independent interruptions.

Current `fulfilled` status does not reveal the full
historical interruption reason. A missing EventBridge Archive
does not imply that a warning was never issued.

For interruptions after 2026-11-25 01:35 JST, when the
reason-evidence change was deployed, read the stored Spot request
status first (§9). The availability Lambda is designed to capture
it before the request returns to `fulfilled`. Real capture is not
yet verified.

## 4. Application recovery (Application SSM Session)

From the Mac:

~~~bash
aws ssm start-session \
  --target i-0decf6bc205e273d9 \
  --region ap-northeast-1
~~~

On the Application EC2, initially run read-only checks:

~~~bash
uptime -s
last -x | head -20
sudo systemctl is-active gmsv5.service
sudo systemctl show gmsv5.service \
  -p ActiveState -p SubState -p ActiveEnterTimestamp -p NRestarts
curl -sS --max-time 5 -o /dev/null \
  -w 'HTTP=%{http_code}\n' http://127.0.0.1:3000/
~~~

`active` and HTTP 200 confirm local process/readiness,
not externally measured CloudFront or browser availability.

Since 2026-11-19, also check boot recovery and the active release
(read-only):

~~~bash
systemctl status gmsv5-boot-prepare.service --no-pager
systemctl status gmsv5-boot-verify.service --no-pager
readlink /opt/gmsv5/current
curl -fsS http://127.0.0.1:3000/api/health/ready
~~~

The readiness response names the running release. It must match the
`current` link. If a deployment was in progress when the instance
stopped, follow "Boot recovery investigation" in the
[CI/CD Runbook](./cicd-operations-runbook.md) and do not start a new
deployment until the transaction is resolved.

Do not modify the Application release, database, systemd
or networking based only on an EC2 interruption event.

## 5. Observability recovery (Observability SSM Session)

From the Mac:

~~~bash
aws ssm start-session \
  --target i-021170e56849c597b \
  --region ap-northeast-1
~~~

On the Observability EC2, record evidence before intervention:

~~~bash
sudo docker ps -a \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Networks}}'

sudo docker inspect gms-prometheus \
  --format 'Status={{.State.Status}} Restart={{.HostConfig.RestartPolicy.Name}} Exit={{.State.ExitCode}} OOM={{.State.OOMKilled}}'

sudo docker logs --tail 60 gms-prometheus 2>&1
~~~

If Prometheus is stopped and manual recovery is authorized,
start the **existing** container rather than recreating it:

~~~bash
sudo docker start gms-prometheus
~~~

After startup completes, verify:

~~~bash
curl -fsS http://127.0.0.1:9090/-/ready

sudo docker exec gms-grafana \
  curl --noproxy '*' -fsS \
  http://gms-prometheus:9090/-/ready

curl -fsS http://127.0.0.1:9090/api/v1/status/runtimeinfo

sudo docker inspect gms-prometheus \
  --format 'Restart={{.HostConfig.RestartPolicy.Name}} Status={{.State.Status}}'
~~~

Expected: Prometheus Ready, Grafana access working,
effective retention `10d or 1GiB`, policy `always`.
Verify all four Scrape Targets using PromQL `up`.

Do not delete containers, TSDB volumes, Terraform State,
or the Root EBS during incident response.

## 6. Prometheus reload safety

For Prometheus configuration reload, avoid
`docker kill --signal=HUP gms-prometheus` because of
a suspected Docker manual-stop tracking interaction.

Use host PID delivery only after confirming
Prometheus is running:

~~~bash
PID="$(sudo docker inspect -f '{{.State.Pid}}' gms-prometheus)"
test "$PID" -gt 1 && sudo kill -HUP "$PID"
~~~

Then check `reloadConfigSuccess`, retention and readiness.
A successful reload is not evidence that automatic recovery
after future host restarts works.

## 7. Alert and SLO caveats

The November 16 Grafana `DatasourceError` reached Discord,
demonstrating failure notification while Grafana and its
egress relay remained available.

It does not prove that alerts are delivered if the entire
Observability EC2 is stopped. The Discord Resolved message
for this real incident is not yet confirmed.

The translation latency SLO covers successful server-side
translations only. It does not include EC2 availability,
translation errors or lost telemetry. Never interpret
missing data or low sample volume as compliance.

See [Translation SLO and Alerting Runbook](./observability/translation-slo-alerting-runbook.md).

## 8. Prevention and follow-up

- Preserve the `always` restart policy in reproducible provisioning.
- Test subsequent automatic EC2 boot recovery in a controlled window.
- Implement external monitoring independent of Observability EC2.
  (Done 2026-11-17; see the note at the top.)
- Capture future Spot interruption warnings and rebalance
  recommendations using EventBridge and protected event storage.
  (Done 2026-11-17 with EventBridge and DynamoDB.)
- Back up critical configuration, Terraform State and secrets
  outside Git; perform restore drills.
- Reduce correlated Spot risk or adopt an availability design
  consistent with the acceptable downtime budget.
- Distinguish Spot stop/start from instance termination/replacement;
  the latter may delete Observability Root EBS data.

## 9. Interruption reason evidence

> **Status (2026-11-25):** deployed at 01:35 JST
> ([As-Built](./spot-reason-tracking-as-built-2026-11-25.md)). Production configuration is verified. **Real
> Spot status collection, reason association and cause-bearing
> RECOVERED messages are not yet verified.** No Spot interruption
> has occurred since deployment. After the next one, follow §9.4.

Interruptions before this change have no captured reason code.
Capacity reclamation is suspected for them but not confirmed,
and they are not backfilled.

### 9.1 Where the reason comes from

The availability Lambda calls `DescribeInstances` (to find the
Spot request ID) and `DescribeSpotInstanceRequests` at each
rebalance recommendation, interruption warning, `stopping`,
`stopped` and `running` event. At `stopped` it retries about
2 and 5 seconds later while the reason is unresolved. Each
observation is stored before the next call. The data model is in
the [Availability Runbook §12](./availability-external-runbook.md#12-spot-interruption-reason-evidence).

Only the Spot request status code confirms a cause:

| AWS status code | Classification | Confirms a cause |
|---|---|---|
| `instance-stopped-no-capacity` | `CAPACITY` | Yes |
| `instance-stopped-by-price` | `PRICE` | Yes |
| `instance-stopped-by-user` | `USER` | Yes |
| `instance-stopped-by-experiment` | `EXPERIMENT` | Yes |
| `marked-for-stop`, `marked-for-stop-by-experiment` | `PENDING` | No |
| `fulfilled` | `UNKNOWN` | No |
| Any other or no code | `UNKNOWN` | No |

The raw AWS code is always stored, including for `UNKNOWN`.
Meanings follow the AWS
[Spot request status codes](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-request-status.html)
and the
[request status lifecycle](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instances-request-status-lifecycle.html).

Never conclude `CAPACITY` from any of these alone:

- An interruption warning or a rebalance recommendation.
- A low Spot price compared with the maximum price.
- The EC2 `StateReason`. `Server.SpotInstanceShutdown` covers
  both price and capacity in the AWS description, so it cannot
  separate them. It is stored as context only.

### 9.2 Investigation procedure (Mac Terminal, read-only)

1. Find the incident row (`stopped`) and its reason fields:

   ~~~bash
   aws dynamodb query \
     --profile gms-admin --region ap-northeast-1 \
     --table-name gmsv5-availability-events \
     --key-condition-expression 'instance_id = :pk' \
     --expression-attribute-values '{":pk":{"S":"i-0decf6bc205e273d9"}}' \
     --no-scan-index-forward --max-items 20 \
     --projection-expression 'event_time, event_type, stop_at, stopping_at, running_at, recovered_at, spot_reason, spot_reason_confirmed, spot_status_code, spot_status_update_time, spot_evidence_observed_at, spot_reason_conflict, spot_conflict_codes, interruption_warning_at, rebalance_recommendation_at'
   ~~~

2. Read every observation around that time, including rejected
   ones:

   ~~~bash
   aws dynamodb query \
     --profile gms-admin --region ap-northeast-1 \
     --table-name gmsv5-availability-events \
     --key-condition-expression 'instance_id = :pk AND event_time BETWEEN :from AND :to' \
     --expression-attribute-values '{":pk":{"S":"spot#i-0decf6bc205e273d9"},":from":{"S":"2026-11-25T00:00:00Z"},":to":{"S":"2026-11-25T23:59:59Z~"}}'
   ~~~

   Use `spot#i-021170e56849c597b` for the Observability EC2.

3. Interpret:
   - `spot_reason_confirmed = true`: the AWS status code in
     `spot_status_code` is the cause. Check
     `spot_status_update_time` against `stop_at`.
   - `spot_reason_conflict = true`: AWS returned more than one
     stop code for this incident. The first one is kept; the
     others are in `spot_conflict_codes`. Report both; do not
     choose one.
   - Not confirmed: the reason is unknown. `PENDING` only means
     the stop was in progress when observed.
   - Observation `attribution`: `in_window` was attached;
     `stale` predates this incident (for example the previous
     interruption's status); `after_incident` belongs to a later
     cycle; `ambiguous` fits more than one incident and was
     not attached to any; `unavailable` means the API call failed
     (see `error`); `legacy_incident` is an incident created
     before this change.
4. If no observation exists, check the Lambda log group
   `/aws/lambda/gmsv5-availability` for `Spot status` lines, then
   fall back to CloudTrail and the AWS commands in §3.

### 9.3 Limits

- EventBridge delivery is best effort. If AWS has already
  returned the request to `fulfilled` when the Lambda reads it,
  the reason stays `UNKNOWN`.
- The AWS documentation does not state how long an
  `instance-stopped-*` code stays visible for a persistent
  request with interruption behavior `stop`, or whether it is
  shown as `disabled` or `fulfilled` while stopped. Real capture
  rates are unknown until production data exists.
- Attachment uses time windows: up to 10 minutes before and
  after the stop, never before the launch of the run that the
  stop ended, and strictly before the next restart (no
  tolerance). If `running` events and launch times are missing
  during fast cycles, a correct status can be rejected as
  `stale`, `after_incident` or `ambiguous`. The rules reject
  rather than attach a status to the wrong incident. This is
  tested locally, not with real AWS timing.
- The interruption warning and the rebalance recommendation are
  context, not causes.

### 9.4 After the next natural interruption (read-only)

Purpose: the first production verification of §9. Do not create an
interruption: no EC2 stop, no Lambda invoke, no synthetic event, no
DynamoDB edit.

1. **Fix the time range.** From the Discord INTERRUPTION WARNING or
   RECOVERED message, or from the incident row (§9.2 step 1), note
   `stop_at` and `running_at` in UTC.
2. **Incident row.** Run §9.2 step 1. Record `spot_reason`,
   `spot_reason_confirmed`, `spot_status_code`,
   `spot_status_update_time`, `spot_evidence_observed_at`,
   `spot_evidence_phase`, `stopping_at`, the signal times and any
   conflict fields.
3. **Observation rows.** Run §9.2 step 2 for that day. For each row,
   record `phase`, `attempt`, `status_code`, `classification`,
   `status_update_time`, `observed_at`, `attribution`,
   `incident_result` and `error`.
4. **Lambda logs.** Use `START` and `END` as epoch milliseconds,
   for example from `stop_at` minus 10 minutes to `running_at` plus
   10 minutes:

   ~~~bash
   aws logs filter-log-events --profile gms-admin --region ap-northeast-1 \
     --log-group-name /aws/lambda/gmsv5-availability \
     --start-time "$START" --end-time "$END" \
     --filter-pattern '?"Spot status" ?"Task timed out" ?ERROR ?REPORT'
   ~~~

   Record the `Spot status ... result=` lines, any `Spot status
   evidence skipped`, and `Duration` and `Max Memory Used` from the
   `REPORT` line of the `stopped` invocation.
5. **Current AWS status (independent).** Run the
   `describe-spot-instance-requests` command in §3. It may already
   show `fulfilled`; that does not contradict stored evidence.
6. **Discord.** Check the RECOVERED message for the `Spot cause:`
   line and compare it with step 2.
7. **Judge and record** against the acceptance table in the
   [As-Built §7](./spot-reason-tracking-as-built-2026-11-25.md#7-next-naturally-occurring-interruption).
   Record `UNKNOWN`, `ambiguous`, `stale` or `unavailable` results as
   they are: they measure the capture rate. Write the result in a new
   dated document or an addendum to the As-Built. Do not edit the
   2026-11-25 evidence.

If the invocation timed out, ran out of memory or broke HTTP
monitoring or notifications, follow the rollback in the
[Availability Runbook §12.7](./availability-external-runbook.md#127-rollback).

**AWS reference:**
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html

**Interruption notices:**
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html
