# GMS v5 — External Availability and Spot Event Monitoring As-Built (2026-11-17)

- **Evidence date:** 2026-11-17 JST
- **Owner:** Infrastructure / SRE
- **Scope:** Independent `infra/terraform/availability/` module
- **Current status:** **DEPLOYED / CORE PATHS VERIFIED; operational hardening remains**
- **Dependencies:** [2026-11-16 Observability As-Built](./observability-as-built-2026-11-16.md), [Spot Interruption Runbook](./spot-interruption-runbook.md), [External Availability Runbook](./availability-external-runbook.md)

## 1. Mission and motivating incident

Both GMS v5 EC2 instances are Persistent Spot `t4g.small` in `ap-northeast-1a`. On Nov 16, 2026, the Application EC2 experienced three Spot-managed stop/start cycles. The Observability EC2 also stopped and restarted, after which Prometheus failed to recover without manual intervention. An in-host Grafana alert cannot signal during a complete host outage. The mission is to create an AWS-managed, outside-the-host event/HTTP monitoring channel.

## 2. Deployed architecture

- AWS region: `ap-northeast-1`.
- Lambda: `gmsv5-availability`, Python 3.12, no VPC attachment.
- DynamoDB: `gmsv5-availability-events`.
- CloudWatch namespace: `GMS/Availability`.
- EventBridge: EC2 state changes, Spot interruption/rebalance signals, and one-minute HTTP checks.
- Public HTTP target: `https://sunlightjetrans.com/`.
- Discord delivery: existing authenticated CloudFront relay using an SSM SecureString.
- Grafana: `grafana/grafana:13.3.1` on Observability EC2.
- Grafana CloudWatch datasource UID: `gms-cloudwatch`.
- Dashboard UID: `gmsv5-external-availability`.
- CloudWatch API endpoint: `https://monitoring.ap-northeast-1.api.aws`.
- Outbound proxy: Squid 6.13 on `172.18.0.1:3128`, restricted to Grafana and CloudWatch HTTPS CONNECT.
- Grafana IAM role: `gmsv5-observability-role`, with `ListMetrics` and `GetMetricData`.

The original Prometheus, Tempo, and Discord relay services remain separate.
The Application and Observability EC2 instances were not deliberately stopped
during this deployment.

## 3. Verification evidence — 2026-11-17 JST

| Test | Evidence | Result |
|---|---|---|
| Terraform initial deployment | 14 created, 0 changed, 0 destroyed | PASS |
| Lambda HTTP alert deployment | 1 Lambda updated | PASS |
| Grafana IAM deployment | 1 IAM inline policy created | PASS |
| Python tests | 19 tests, `OK` | PASS |
| Terraform fmt / validate | Completed successfully | PASS |
| HTTP monitoring | `HTTPUp=1` from real scheduled Lambda executions | PASS |
| DynamoDB HTTP state | `http_status=up`, `http_failures=0` | PASS |
| Synthetic HTTP DOWN/RECOVERED | Real DynamoDB and Discord, duplicate suppressed | PASS |
| Synthetic EC2 lifecycle | 130 seconds calculated using mocked HTTP recovery | PASS |
| EventBridge pattern matching | 8 of 8 patterns matched | PASS |
| CloudFront caching | `CachingDisabled` policy confirmed | PASS |
| Public origin | Origin IPv6 matches Application EC2 | PASS |
| Grafana CloudWatch query | HTTP 200, 45 data points, no error | PASS |
| Grafana dashboard | Three panels displayed in browser | PASS |
| Squid CloudWatch CONNECT | CONNECT 200 | PASS |
| Squid unauthorized destination | CONNECT 403 | PASS |
| Real EC2 event delivery after deployment | No complete incident observed | NOT VERIFIED |
| Real StopToRecoverySeconds metric | Not yet emitted | NOT VERIFIED |
| UptimeRobot independent monitor | Not configured | PENDING |
| Reboot recovery of Squid and Grafana | Not tested | NOT VERIFIED |

The Synthetic EC2 test used a fictional instance ID and mocked the
HTTP recovery timestamp and CloudWatch metric publishing. Its 130-second
value is not a measured production outage.

The Synthetic HTTP test used actual DynamoDB and Discord endpoints,
but executed the handler locally rather than through the production
Lambda runtime.

## 4. Correctness boundaries

- All external availability checks are from an AWS-managed Lambda function not attached to the application VPC. A public A record/CloudFront IPv4 frontend must be proven before apply.
- A public HTTP 200 on `/` is an ingress-level check. It does not demonstrate that DeepL/OpenAI translation works. HTTP 200 may be cached or represent a login page.
- EC2 event delivery is best-effort, and events may be reordered or duplicated. State-change events alone do not reveal whether the trigger was Spot or manual. The `trigger` field remains `unknown` unless a warning was recorded; check CloudTrail to attribute causation.
- `recovered_at` for Application means first observed 200 after `running` (within polling granularity). Observability has no public service endpoint and is marked `ec2_running_only`; no Prometheus-ready assertion is made.
- `StopToRecoverySeconds` measures a stop-event-to-recovery interval; `HTTPUp` is a periodically sampled public site metric. Neither equals exact continuous downtime, and both are separate from the successful-translation latency SLO.
- DynamoDB `PAY_PER_REQUEST` is not guaranteed inside the always-free capacity tier. CloudWatch free custom-metric allowance depends on total account use; Grafana CloudWatch `GetMetricData` calls may be charged. Strict $0 is therefore not demonstrated.

## 5. Operational limitations and remaining acceptance

1. The Grafana UP stat uses the most recent non-null sample. If monitoring
   stops entirely, it may continue to display an older UP value.
   Add freshness and missing-data detection.
2. HTTP alert transitions are stored in DynamoDB, but a failure between
   committing a transition and sending Discord does not guarantee retry.
3. Two recorded failures trigger HTTP DOWN; missing scheduled intervals
   are not independently treated as failures.
4. Spot warning delivery and EC2 state-change ordering remain
   best-effort; real-event reconciliation is not yet proven.
5. Observability EC2 recovery currently means EC2 running, not verified
   Prometheus/Grafana readiness.
6. Squid and Grafana startup ordering after Spot recovery has not been
   tested. Squid depends on the Docker bridge address `172.18.0.1`.
7. Squid permits the present Grafana container address `172.18.0.7`.
   Recreated containers must preserve or explicitly update this ACL.
8. UptimeRobot is not configured. No independent third-party availability
   history can yet be claimed.
9. CloudWatch metric reads and other AWS services may incur costs.

## 6. Version-controlled deployment assets

- `infra/terraform/availability/`: Lambda, DynamoDB, IAM, EventBridge.
- `infra/observability/squid/cloudwatch-only.conf`: verified Squid ACL.
- `infra/observability/grafana/cloudwatch-datasource.yml`: verified datasource.
- `infra/observability/grafana/external-availability-dashboard.json`: deployed dashboard.
- `docs/infrastructure/availability-external-runbook.md`: recovery and validation.

The host-local `grafana.env`, Discord tokens, Terraform state and
Grafana SQLite database must never be committed to Git.
