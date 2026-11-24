# GMS v5 — Spot Interruption Reason Tracking As-Built (2026-11-25)

- **Evidence date:** 2026-11-25 JST (deployed 01:35:54 JST = 2026-11-24T16:35:54Z)
- **Owner:** Infrastructure / SRE
- **Scope:** `infra/terraform/availability/` — the existing EventBridge → Lambda → DynamoDB → Discord availability monitoring. No new monitoring system.
- **Current status:** **DEPLOYED. Production configuration and existing HTTP monitoring verified. Real Spot reason capture NOT YET VERIFIED** (no Spot interruption since deployment).
- **Design and operation:** [Availability Runbook §12](./availability-external-runbook.md#12-spot-interruption-reason-evidence) (data model, write rules, budget, rollback), [Spot Interruption Runbook §9](./spot-interruption-runbook.md#9-interruption-reason-evidence) (classification, investigation, next-interruption check)
- **Earlier record:** [Availability As-Built 2026-11-17](./availability-external-as-built-2026-11-17.md)

> **Evidence classes.** Each statement below is one of these. Do not
> promote one class to another.
>
> - **Local test:** unit and integration tests on the Mac with fake
>   DynamoDB and EC2. No AWS calls.
> - **Repository check:** comparison of files in this repository, made
>   while writing this document.
> - **Production configuration:** AWS output and Lambda logs inspected by
>   the Operator after apply. Not re-queried while writing this document.
> - **Pending:** needs a naturally occurring Spot interruption. Nothing in
>   this document verifies real Spot reason capture.

## 1. Purpose

Spot interruptions of the Application and Observability EC2 between
2026-11-17 and 2026-11-24 JST have no recorded AWS reason code (Operator
report: 32 Application and 34 Observability interruptions; both Persistent
Spot `t4g.small` in `ap-northeast-1a`, interruption behavior `stop`). Capacity
reclamation is suspected for them, not confirmed. They are not backfilled.

This change reads the Spot request status (`DescribeSpotInstanceRequests`)
at each EC2 lifecycle and Spot signal event, before the request returns to
`fulfilled`, and stores it with the incident.

## 2. What was deployed

**Terraform apply (production configuration):** 0 added, 3 changed,
0 destroyed. No resource created, destroyed or replaced.

| Resource | Change |
|---|---|
| `aws_lambda_function.availability` | New code (adds `spot_status.py`) and environment `SPOT_STATUS_COLLECTION=enabled` |
| `aws_iam_role_policy.lambda_runtime` | `ReadEC2State` allows `ec2:DescribeInstances` and `ec2:DescribeSpotInstanceRequests`; no EC2 write action |
| `aws_cloudwatch_event_rule.state` | State pattern `stopping`, `stopped`, `running` |

The DynamoDB table `gmsv5-availability-events` (keys, TTL, capacity) was not
changed. New attributes and the `spot#<instance-id>` partition need no
schema change.

## 3. Production configuration evidence

Inspected by the Operator after apply (2026-11-25 JST):

| Item | Value |
|---|---|
| Lambda `LastModified` | 2026-11-24T16:35:54Z |
| `State` / `LastUpdateStatus` | `Active` / `Successful` |
| Runtime / timeout / memory | Python 3.12 / 90 s / 128 MB |
| `SPOT_STATUS_COLLECTION` | `enabled` |
| Previous `CodeSha256` | `tazsiB2aDUX4rcaLje518P4DioS2M2j0d0aj2faEumY=` |
| Deployed `CodeSha256` | `cw8JPPcmFYdo5Xsc7YwD4zxK7QsCIw8CAsiINW9oiQo=` |
| EventBridge state rule | Includes `stopping`, `stopped`, `running` |
| Lambda IAM | Allows `DescribeInstances` and `DescribeSpotInstanceRequests` |

**Repository check (2026-11-25):** the local
`infra/terraform/availability/lambda-package.zip` produced by the apply has
SHA-256 (base64) `cw8JPPcmFYdo5Xsc7YwD4zxK7QsCIw8CAsiINW9oiQo=`, equal to the
deployed `CodeSha256`. It contains exactly `deployment.py`, `handler.py` and
`spot_status.py`, each byte-identical to the files in `lambda/` committed
with this document. To re-check after a later change:

```bash
cd ~/GMSv5/infra/terraform/availability
openssl dgst -sha256 -binary lambda-package.zip | base64
for f in handler.py deployment.py spot_status.py; do
  unzip -p lambda-package.zip "$f" | cmp - "lambda/$f" && echo "$f matches"
done
aws lambda get-function-configuration --profile gms-admin --region ap-northeast-1 \
  --function-name gmsv5-availability --query CodeSha256 --output text
```

## 4. Local test evidence

| Check | Result |
|---|---|
| Existing availability tests | 33/33 PASS |
| New tests (`test_spot_status.py` 52, `test_spot_handler.py` 28) | 80/80 PASS |
| Total (`unittest discover -s tests`) | 113/113 PASS |
| Mutation checks in the final review (8 safeguards broken one at a time) | 8/8 detected |
| `terraform fmt -check` / `terraform validate` | PASS / PASS |

What the tests cover: classification; missing Spot request; API errors and
throttling; stale and post-restart status; `fulfilled` after a confirmed
cause; rapid cycles; missing and delayed `running` events; overlapping
windows (`ambiguous`); duplicate and out-of-order events; concurrent
writers; warning versus rebalance; `stopping`; the warning message being
sent before any status read; worst-case execution budget; and unchanged
HTTP, synthetic, deployment and recovery behavior. The DynamoDB fake
evaluates the real condition and update expressions. It is not DynamoDB.

## 5. Post-deployment runtime evidence

Inspected by the Operator:

| Item | Value |
|---|---|
| First inspected execution after deployment | 2026-11-24T16:36:52Z (01:36:52 JST), completed |
| Duration / Init Duration | 3374.35 ms / 301.67 ms |
| Max memory used | 94 MB of 128 MB |
| Errors in the inspected Lambda logs | None observed |
| `HTTPUp` | 1 for seven consecutive minutes, 01:30–01:36 JST. The 01:36 sample is after deployment; 01:30–01:35 are before it |
| DynamoDB HTTP state | `last_slot` 2026-11-24T16:37, `http_status` `up`, `http_failures` 0 |
| `spot#<instance-id>` partitions (both instances) | 0 records |

Reading:

- The scheduled HTTP probe works with the new code: **PASS**.
- Zero observation records are consistent with no EC2 state or Spot signal
  event since deployment. They do not show that collection works.
- The inspected execution was most likely a scheduled HTTP probe (inferred:
  no observation rows exist, and the event source was not recorded). That
  path does not load `spot_status.py` or create an EC2 client. **Memory and
  duration of an EC2-event invocation with the collector have not been
  measured.**

## 6. Verification status

| Area | Status | Evidence class |
|---|---|---|
| Infrastructure deployment | **PASS** | Production configuration |
| Deployed code equals this repository | **PASS** | Repository check |
| Existing scheduled HTTP monitoring | **PASS** | Production configuration |
| Implementation logic | **PASS (local)** | Local test |
| Actual Spot status collection | **PENDING** | — |
| Actual incident reason association | **PENDING** | — |
| Cause-bearing RECOVERED notification | **PENDING** | — |
| Memory and duration of EC2-event invocations | **PENDING** | — |
| Real-world reason capture rate | **UNKNOWN** | — |

Do not report the PENDING items as verified until the procedure in §7 has
been run on a real interruption and recorded.

## 7. Next naturally occurring interruption

Do not create a test interruption: no EC2 stop, no Lambda invoke, no
synthetic Spot event. Wait for a real one and run the read-only procedure
in [Spot Interruption Runbook §9.4](./spot-interruption-runbook.md#94-after-the-next-natural-interruption-read-only).

Acceptance for the PENDING items:

| Item | PASS when |
|---|---|
| Status collection | A `stopped`-phase observation row exists for the incident with a raw `status_code` and no `error` |
| Reason association | The incident has `spot_reason_confirmed = true`, and an observation with `attribution = in_window` has the same code, with `status_update_time` inside the incident window |
| RECOVERED notification | The Discord RECOVERED message has `Spot cause: <class> (confirmed by AWS Spot request status)` matching DynamoDB |
| Execution budget | The invocation `REPORT` shows no timeout, and memory stays below 128 MB with margin |

If the result is `UNKNOWN`, `ambiguous`, `stale` or `unavailable`, record
it as it is. It is still evidence for the capture rate, not a failure to
hide. Record the result in a new dated document or an addendum to this one.

## 8. Rollback

Procedures: [Availability Runbook §12.7](./availability-external-runbook.md#127-rollback).

- **Feature flag (first choice):** set `SPOT_STATUS_COLLECTION` to any
  other value. The collector stops, `stopping` events are ignored again, and
  RECOVERED messages return to the previous text. The IAM permission and the
  `stopping` rule pattern stay. Both are read-only or harmless.
- **Full rollback:** revert the change in Git and apply. Expected plan: the
  same three resources changed, nothing added or destroyed.
- **Data:** no DynamoDB change in either case. Observation rows expire by
  the existing 400-day TTL. The previous code never reads the `spot#`
  partition or the new attributes.

## 9. Known limits

- The capture rate depends on AWS still showing an `instance-stopped-*` code
  when the Lambda reads it. AWS does not document how long it stays.
- The 128 MB memory setting has not been measured on the collector path
  (§5).
- DNS resolution and a slow-trickling response are not bounded by the SDK
  timeouts used in the execution budget.
- A RECOVERED message can say `UNKNOWN` although evidence is stored seconds
  later (late `stopped` events). DynamoDB is the record.
- Interruptions before deployment remain without a reason code.

## 10. Evidence handling

This document records resource names, configuration values, code hashes and
summarized log results. It contains no AWS account ID, credential, session
token, Terraform State, Discord token or raw log export.
