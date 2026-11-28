# GMS v5 — P2 Production Rollout As-Built (2026-11-28)

- **Evidence date:** 2026-11-28 JST
- **Owner:** Infrastructure / SRE
- **Scope:** the two availability alarms (burn rate 9/60, continuous failure 6/6) moving from `off` to `shadow`, the planned stop test, and `live`. Code and configuration as of `973ae68`; no new feature.
- **Current status:**
  - **Stage 1 done: both alarms in `shadow` since 18:02:27 JST (§3).**
    Rules `ENABLED`, deliveries `disabled`.
  - Stages 2–5 (final pre-stop checks, planned stop, `live`, completion) not started; each needs its own approval.
- **Procedure:** [Availability Runbook §16](./availability-external-runbook.md#16-planned-stop-test-and-live-rollout)
- **Related:**
  - [Burn Rate As-Built](./availability-burn-rate-as-built-2026-11-27.md)
  - [Continuous Failure As-Built](./availability-continuous-failure-as-built-2026-11-28.md)
  - [Notification Priority As-Built](./availability-notification-priority-as-built-2026-11-28.md)

## 1. Decision and accepted risks (Product Owner, 2026-11-28)

The earlier plan to postpone the planned stop to 2026-12-01 was withdrawn.
The whole sequence runs today:

1. `shadow`.
2. One 12-minute planned stop of the Application EC2.
3. Both alarms back to OK.
4. `live`.
5. As-Built, Push and Merge.

Each stage keeps its approval boundary. An unmet safety condition stops
the sequence, and no check is skipped to finish today.

- **The planned stop:** once only, with a manual start. The Observability
  EC2 is not stopped. There is no stop test after `live`.
- **What it verifies:** alarm evaluation and production event handling
  are verified by the planned stop under `shadow`.
- **Discord:** real delivery and the iPhone push are taken as verified
  by the synthetic HIGH / LOW device test of the same day.
- **No extra snapshot.** The existing daily DLM snapshot is used. Reason:
  no recent data changes or new users.
- **Spot capacity shortage:** handled manually. No automatic recovery
  and no new feature.

**Accepted risks (not resolved).**

- **Error budget.** At 17:25 JST: SLO 99 % / rolling 7 days, availability
  99.45 %, budget 100.8 minutes, 55.0 consumed, 45.8 left, coverage
  100 %. A 12-minute stop plus 2 minutes of start would leave about
  31.8 minutes. Natural Spot interruptions before 2026-12-01 evening can
  take availability below 99 %. Accepted for this experimental
  environment.
- **Restart.** AWS does not start a user-stopped persistent Spot
  instance, and a start can fail for lack of capacity. The recovery time
  is not guaranteed.

## 2. Pre-stop investigation (read only, 2026-11-28 17:25 JST)

- **Monitoring data.** The Grafana figures were recomputed from
  CloudWatch with the dashboard's formulas and matched exactly. The last
  60 minutes had no failure and no missing minute.
- **Alarms and delivery.** Both alarms OK, both rules `DISABLED`, both
  deliveries `disabled`.
- **Application.** Lambda `AY3KE7zQPDNa0HvEUKUgmtVyxV37bEHmKShMXqLNpv4=`.
  Readiness `ready`.
- **Deployments.** None in progress; the last was 2026-11-27 19:00 JST.
- **Traffic.** About 6 requests per 5 minutes on the public CloudFront
  distribution, roughly the probes alone. User requests in flight cannot
  be seen directly.
- **Application EC2:**
  - t4g.small in 1a, persistent Spot (`stop`), stop protection off;
  - primary ENI with the origin IPv6 (`origin-v5`, TTL 60);
  - data on a separate EBS with a daily DLM snapshot;
  - `gmsv5.service` starts at boot.
- **Spot history.** 20 AWS-initiated Spot stops in the 7 days before,
  2 of them on this day (10:17 and 10:52). Each recovered automatically
  in 57–117 s.
- **AWS documentation:**
  - after a user stop the request is `disabled` and AWS does not start
    it;
  - a start needs capacity;
  - the instance type cannot be changed while the instance is stopped;
  - the primary ENI cannot be detached.
- **Alternative recovery.** DR Runbook §4–§8; the production cutover has
  never been run (Runbook §16.3).

## 3. Stage 1 — `shadow`

**Change (repository check, local test).**

- `variables.tf`: both rollout defaults `off` → `shadow`, in a reviewed
  commit as Runbook §13.5 requires. In `shadow`:
  - the rule is `ENABLED`;
  - `*_DELIVERY` stays `disabled`, because only `live` sets `enabled`;
  - the precondition is satisfied, because it applies only to `live`.
- Two tests that pinned the committed stage now expect `shadow`, and the
  mutation P13 was retargeted to `shadow` → `live`.
- One test was added: `ShadowStageEndToEndTests`. It drives both alarms
  through `handler.handler` with both deliveries `disabled`. For each
  alarm it sends ALARM, a duplicate ALARM and OK.

**What runs in `shadow`, and the tests that cover it.**

| Item | Covered by |
|---|---|
| Alarm event validation (name/ARN pair, source, state) | `ParseTests`, `RejectionTests`, `HandlerDispatchTests` |
| Context lookups (Now, Last 60) | `ShadowContextTests`, `ShadowStageEndToEndTests` |
| Shadow ledger | `ShadowLifecycleTests` (`shadow_notified_*`) |
| Duplicate and stale events | `test_duplicates_and_stale_events_log_once`, concurrent mark, state change after read, end-to-end duplicates |
| Payload with the user ID masked | `test_shadow_log_shows_priority_mention_and_masked_payload`, end-to-end |
| HIGH / LOW priority | `test_kinds_route_to_high_or_low`, end-to-end (ALARM high with mention, NORMALIZED low) |
| No real send | Shadow tests assert nothing sent; end-to-end asserts `urlopen` never called |
| Live state untouched | `assert_no_live_fields`, `test_live_never_writes_the_shadow_ledger`, end-to-end (no `notified_*` or lease) |

**Tests.**

- 308 tests in total, all passing, three consecutive runs: the 307
  earlier tests plus the end-to-end test. The network was blocked.
- Mutation runs on copies, each first checked to pass unmutated:
  78 / 78 detected.

**Production state before the apply (read only, 17:43 JST).**

- Availability 99.45 %, 55.0 minutes consumed, 45.8 left, coverage 100 %.
- The last 60 minutes had no failure and no missing minute.
- Both alarms OK; both rules `DISABLED`; both deliveries `disabled`.
- Lambda `AY3K…`, 14 variables, user ID valid (value not shown).
- Readiness `ready`.
- AWS SSO credentials valid until 23:19 JST.

**Plan (from `d9c5806`, read only).**

- 0 to add, 2 to change, 0 to destroy: `aws_cloudwatch_event_rule.burn_rate`
  and `aws_cloudwatch_event_rule.continuous_failure`. Only `state`
  changes, `DISABLED` → `ENABLED`.
- Everything else no-op: the Lambda (code, the 14 variables, both
  deliveries), IAM, the alarms, targets, permissions, the HTTP probe, the
  table and the relay.
- The text plan did not contain the user ID.

**Apply.** 18:02:25–18:02:27 JST: 0 added, 2 changed, 0 destroyed.

**After apply** (snapshots before and after; runtime checks at 18:09:05
JST).

| Check | Result |
|---|---|
| Rules | Both `DISABLED` → `ENABLED`; patterns and targets unchanged; HTTP probe rule unchanged |
| Deliveries | Both `disabled` |
| Lambda | `CodeSha256` `AY3K…`, `LastModified`, the 14 variables (hash comparison), timeout, memory and role all unchanged. User ID present, valid and equal to `terraform.tfvars` (value not shown) |
| Alarms | Both configurations unchanged; both `OK`; no state update since the apply |
| IAM, permissions | Unchanged |
| HTTP probe | `HTTPUp` = 1, one sample per minute, 18:00–18:08 JST (9 minutes across the apply) |
| Lambda metrics (18:03–18:09 JST) | 5 invocations (probes), 0 errors, 0 throttles. Max duration 0.42 s, max memory 100 MB of 128, no cold start |
| Lambda log | 21 lines: 0 error or warning lines, 0 burn rate, shadow or notified lines |
| Rule invocations | `gmsv5-availability-burn-rate` and `gmsv5-availability-continuous-failure`: 0 invocations, 0 failed. Expected: no alarm state change |
| DynamoDB | HTTP row `up`, 0 failures; no `burnrate#` or `contfail#` row |
| Unintended Discord messages | None: no notify path ran (no HTTP failure, no alarm event) |
| Readiness | HTTP 200, `ready`, release `0b72c3a` |
| Plan after apply | No changes |

No natural failure occurred during the checks. The evidence (snapshots,
apply log with the account ID masked, plan summary, check output and
scripts, checksums) is kept outside the repository. A scan found no
user ID in it.

**Stopped here for approval of Stage 2.**
