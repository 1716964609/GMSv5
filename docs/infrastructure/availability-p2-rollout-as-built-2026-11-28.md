# GMS v5 — P2 Production Rollout As-Built (2026-11-28)

- **Evidence date:** 2026-11-28 JST
- **Owner:** Infrastructure / SRE
- **Scope:** the two availability alarms (burn rate 9/60, continuous failure 6/6) moving from `off` to `shadow`, the planned stop test, and `live`. Code and configuration as of `973ae68`; no new feature.
- **Current status:**
  - **Stage 1 done: both alarms in `shadow` since 18:02:27 JST (§3).**
    Rules `ENABLED`, deliveries `disabled`.
  - **Stage 2 done:** final pre-stop checks, 12 of 12 PASS (§4).
  - **Stage 3 done:** planned stop at 18:29:33 JST, manual start at
    18:41:34. Both alarms went OK → ALARM → OK in `shadow` and were
    back to OK by 19:33:53 (§5).
  - Stages 4–5 (`live`, completion) not started; each needs its own
    approval.
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

## 4. Stage 2 — final pre-stop checks (read only, 18:14–18:16 JST)

All 12 checks passed:

1. Availability 99.45 %, 55.0 minutes consumed, 45.8 left, coverage
   100 %.
2. The last 60 minutes had no failure and no missing minute.
3. Both alarms OK.
4. Both rules `ENABLED`, both deliveries `disabled`.
5. Lambda `AY3K…`.
6. Application and Observability EC2: running, instance, system and EBS
   status `ok`, SSM Online.
7. Readiness `ready`.
8. No deployment: no SSM command in 12 hours, no open pull request, no
   running workflow. CD is enabled, so the merge freeze holds by not
   pushing or merging.
9. Traffic: 6–17 CloudFront requests per 5 minutes, about the probe
   baseline. Requests in flight cannot be seen.
10. Latest data-volume snapshot `snap-04917fe2efa5fda6b` (04:36 JST)
    `completed`.
11. SSO valid until 23:19 JST.
12. Runbook §16 reviewed. Start polling is bounded by wall-clock time.

**Other conditions.**

- The Spot request was `active` / `fulfilled`, with no Spot event since
  10:52 JST.
- The Spot price in 1a was 0.0078.
- An immediate re-check at 18:29:22–18:29:26 JST was unchanged.

## 5. Stage 3 — planned stop (2026-11-28)

Approved by the Product Owner. Runbook §16.3 was used. The start was
scripted to run once at T0+12:00, only if the instance was `stopped`,
with a T0+15:00 wall-clock bound. A read-only watcher logged EC2, Spot,
HTTP, readiness and alarm changes every 15 s. Times in the watcher
column are its 15-second polls; the other times are from AWS records.

| JST | T0+ | Event |
|---|---|---|
| 18:29:33 | 0:00 | **T0**: `stop-instances` (running → stopping) |
| 18:29 | | First failed HTTPUp minute (probe `TimeoutError`) |
| 18:29:44 | 0:11 | EC2 `stopped` event (event time; incident row opened) |
| 18:30:31 | ~0:58 | Watcher: `stopped` |
| 18:31:04 | ~1:31 | Watcher: Spot request `disabled` / `instance-stopped-by-user`. At 18:30:31 it was still `active` / `marked-for-stop` |
| 18:35:41.7 | 6:08 | **6/6 ALARM**. Lambda accepted it at 18:35:42.0 and logged the shadow line at 18:35:42.7 |
| 18:38:53.0 | 9:20 | **9/60 ALARM**. Accepted at 18:38:56, shadow line at 18:38:57 |
| 18:40 | | Last failed HTTPUp minute |
| 18:41:34 | 12:01 | `start-instances`, called once; the state was `stopped` just before |
| 18:41:39 | 12:06 | EC2 `running` event (event time) |
| 18:41:47 | 12:14 | `running`; SSM Online; Spot `active` / `fulfilled`. There was no capacity problem |
| 18:41:49 | 12:16 | HTTP 200 observed by the Lambda; incident closed (`http200`, 725 s event-time downtime) |
| 18:42:03 | 12:30 | Watcher: public HTTP 200, readiness `ready`, release `0b72c3a` |
| 18:42:41.7 | 13:08 | **6/6 OK**; shadow line (NORMALIZED) at 18:42:42 |
| 19:33:53.0 | 64:20 | **9/60 OK**; shadow line (NORMALIZED) |

**Results** (read only, collected at 19:34–19:35 JST).

| Check | Result |
|---|---|
| HTTPUp | 12 failed minutes, 18:29–18:40 JST; first good minute 18:41. 70 of 70 minutes present (no missing data) |
| Alarm history vs events | 4 state updates (6/6 OK→ALARM→OK, 9/60 OK→ALARM→OK). The rules invoked the Lambda 2 + 2 times with 0 failures; 4 `accepted` lines; no duplicate or stale event |
| Shadow lines | 4, all `"delivered": false`. ALARM ×2: `priority` high, `mention` true, `allowed_mentions` `{"users": ["USER_ID_MASKED"]}`. NORMALIZED ×2: `priority` low, `mention` false, `{"parse": []}`. In each, the payload `content` equals the mention prefix (if any) plus the message |
| Context | All four messages had `Now` (DOWN at ALARM, UP at NORMALIZED) and the `Last 60` / `Pattern` lines. 0 "unavailable" warnings |
| Real HIGH sends | None: 0 `notified` lines and 0 `notify_failed` |
| DynamoDB | `contfail#` and `burnrate#` rows: state OK / previous ALARM, `shadow_notified_state` OK. **No `notified_*` and no lease** (live ledger untouched) |
| Lambda (T0 to 19:34) | 71 invocations, 0 errors, 0 throttles. Alarm invocations 0.6–3.7 s and 93–112 MB. Max overall 14.2 s: the EC2 `running` handler, whose post-start HTTP polling is bounded below the 90 s timeout. Max memory 112 MB of 128 |
| Log warnings | 13, all `Public HTTP probe failed: TimeoutError` during the stop: 12 scheduled probes plus 1 in the post-start polling |
| User ID | Not in any log line or kept evidence |
| Application | Running, readiness `ready`, public HTTP 200 since 18:42 |
| Error budget (19:35 JST) | Availability 99.34 %, **67.0 minutes consumed (66.47 %), 33.8 left**, coverage 100 %. The test consumed 12 minutes, against 14 assumed |

**Differences from the plan (recorded, not hidden).**

- **Spot request status.** The Spot request became `disabled` /
  `instance-stopped-by-user` about 30 s after `stopped`.
- **Spot reason evidence.** The incident recorded
  `spot_status_code` `marked-for-stop` (class `PENDING`), because its
  reads ran before that change. The trigger was correctly `unknown`: no
  Spot warning was seen. The user stop is therefore not identified as
  `instance-stopped-by-user` in the incident evidence.
- **9/60 normalization.** It came at T0+64.3 minutes, earlier than the
  estimated T0+66–70. The estimate came from the integration test and
  is not guaranteed.
- **Event-time downtime.** The incident's 725 s counts from the EC2
  `stopped` event (18:29:44) to the HTTP 200 observation (18:41:49).
  The service was unreachable from about T0 (18:29:33).

**Confirmed by the Product Owner (pending at the time of writing):**

- the real LOW messages: HTTP DOWN/RECOVERED and EC2 RECOVERED;
- UptimeRobot DOWN/UP;
- no HIGH push.

**Evidence.** Kept outside the repository with checksums:

- pre-stop state, stop response, start log, watcher log;
- final collection script and output, post-test state.

A scan found no user ID in it. SetAlarmState was not used and there was
no second stop.
