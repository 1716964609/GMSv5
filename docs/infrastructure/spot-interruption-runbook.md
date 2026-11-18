# GMS v5 — EC2 Spot Interruption and Recovery Runbook

- **Owner:** Infrastructure / SRE
- **Updated:** 2026-11-16 JST; cross-references added 2026-11-19
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

**AWS reference:**
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html

**Interruption notices:**
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html
