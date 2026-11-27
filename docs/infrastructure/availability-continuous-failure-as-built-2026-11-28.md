# GMS v5 — Continuous Failure Alarm (6 of 6) As-Built (2026-11-28)

- **Evidence date:** 2026-11-28 JST. The integration test ran 02:01–02:54 JST (2026-11-27 17:01–17:54Z). Applied in stage `off` 03:29:08–03:29:22 JST (2026-11-27 18:29:08–18:29:22Z), §8.
- **Owner:** Infrastructure / SRE
- **Scope:** `infra/terraform/availability/`. One CloudWatch alarm, one EventBridge rule, the existing Lambda, table and Discord egress. No new Lambda, SNS topic, table, EC2 or IAM permission.
- **Current status:**
  - **Applied in stage `off` (§8), and the DATA INSUFFICIENT text change applied the same way (§9).** Both alarms evaluate; both rules are
    `DISABLED` and both delivery variables `disabled`, so nothing is
    sent.
  - CloudWatch evaluation measured in an isolated test.
  - Missing-data choice confirmed by the Product Owner (§4).
  - `shadow` and `live` not started.
- **Design and operation:** [Availability Runbook §14](./availability-external-runbook.md#14-continuous-failure-alarm-6-of-6)
- **Related:** [Burn Rate Alarm As-Built](./availability-burn-rate-as-built-2026-11-27.md)

> **Evidence classes.**
>
> - **Integration test:** CloudWatch behavior measured with temporary
>   alarms in a separate namespace. No production resource was used.
> - **Local test:** unit tests on the Mac. The network is blocked for the
>   test process (`tests/no_network.py`).
> - **Production configuration:** read-only AWS output or Terraform plan.
> - **Pending:** needs a stage change or an approval.

## 1. Purpose

A HIGH alarm for a long continuous outage, earlier than the 9-of-60
burn rate alarm. In the backtest (strict wall-clock model, 2026-11-17 to
11-27 JST), 4 of 34 failure episodes lasted 6 minutes or more:

- 1 occurred while the burn rate alarm was already active;
- 1 was detected 3 minutes earlier than by the burn rate alarm;
- 2 were never alarmed by the burn rate alarm during the episode.

The threshold 6 of 6 was approved by the Product Owner.

## 2. Decisions (Product Owner, 2026-11-28)

| Item | Decision |
|---|---|
| Threshold | 6 of 6, `Minimum` over 60 s, `HTTPUp < 1` |
| Missing data | `FILL(m1, 1)` only if the integration test shows it still detects continuously late data; otherwise a plain alarm, with its non-strict behavior stated |
| Related-alarm line | Not added |
| Both alarms fire | Both notify, independently. No suppression or composite alarm |
| State | Separate profile, state row, lease, shadow and live ledgers, rule and delivery mode per alarm |
| 9-of-60 | Messages and state handling unchanged |

## 3. Isolated CloudWatch integration test

**Isolation.** Namespace `GMSv5/ContinuousFailureIntegrationTest`.

- 44 temporary alarms `gmsv5-contfail-itest-<case>-<variant>`.
- A temporary capture rule and log group (1-day retention) for the
  state-change events.
- Production untouched: no write to `GMS/Availability`, no production
  alarm, rule or Lambda change, no `set-alarm-state`, no EC2 stop.

**Variants.** The same data was evaluated by one alarm per variant.

| Variant | Configuration |
|---|---|
| sliding `notBreaching` | Metric alarm, 6 of 6, default sliding window |
| sliding `missing` | The same, with `missing` |
| `FILL(m1, 1)` | Metric math, `notBreaching` for an empty result |
| wall clock | Sliding `notBreaching` with `EvaluationWindow = WallClockWindow` (supported by AWS provider 6.73.0 as `evaluation_window { wall_clock_window {} }`) |

**Cases.** Real-time data, one sample per minute, published 5 s into the
minute like the production probe, except the delay cases.

| Case | Data |
|---|---|
| `c5` | 5 consecutive failures |
| `c6` | 6 consecutive failures |
| `c8rec` | 8 consecutive failures, then passed checks |
| `long14` | 14 consecutive failures |
| `spread` | Failures alternating with passed checks |
| `gap` | 3 failures, 1 missing minute, 3 failures |
| `stop` | 7 failures, then no data |
| `allmiss` | No data at all |
| `delay70` / `delay130` / `delay250` | 15 consecutive failures; **every** datapoint of the case published 70, 130 or 250 s after its minute began |

**Results.** A, then the time after the sixth failed minute began. The
exit, then the time after the last failed minute ended.

| Case | sliding `notBreaching` | sliding `missing` | `FILL(m1, 1)` | wall clock |
|---|---|---|---|---|
| `c5`, `spread` | OK | OK | OK | OK |
| `c6` | A 1.3 → OK 1.3 | A 1.5 → OK 1.5 | A 1.1 → OK 1.1 | A 1.6 → OK 1.6 |
| `c8rec` | A 1.1 → OK 1.1 | A 1.8 → OK 1.8 | A 1.8 → OK 1.8 | A 2.0 → OK 2.0 |
| `long14` | A 1.6 → OK 1.6 | A 1.3 → OK 1.3 | A 1.8 → OK 1.8 | A 1.4 → OK 1.4 |
| `gap` | **A 1.2** → OK 1.2 | **A 1.9** → OK 1.9 | OK | OK |
| `stop` | A 1.3 → **OK 6.3** | A 1.7 → **INSUFFICIENT_DATA 11.7** | A 1.7 → OK 1.7 | A 2.0 → OK 2.0 |
| `allmiss` | OK | INSUFFICIENT_DATA | OK | OK |
| `delay70` | A 1.4 → OK 1.4 | A 1.9 → OK 1.9 | A 1.5 → OK 1.5 | A 2.0 → OK 2.0 |
| `delay130` | A 3.0 → OK 3.0 | A 3.1 → OK 3.1 | **never ALARM** | **never ALARM** |
| `delay250` | A 5.0 → OK 5.0 | A 4.5 → OK 4.5 | **never ALARM** | **never ALARM** |

**Observations.**

- **Strict variants and late data.** `FILL(m1, 1)` and the wall clock
  window broke the `gap` run, as intended. They never alarmed when every
  datapoint arrived 130 s or more late: the newest period was always
  filled (`FILL`) or missing (wall clock). This is the case the AWS
  metric math documentation warns about.
- **Sliding window.** It alarmed across the missing minute. The `gap`
  reason lists 17:17, 17:16, 17:15 and 17:13Z, skipping 17:14. Late
  data only delayed it.
- **Probe stop under `notBreaching`.** The exit reason was "5 datapoints
  were received for 6 periods and 1 missing datapoint was treated as
  [NonBreaching]".
- **Probe stop under `missing`.** The reason was "Insufficient Data: 6
  datapoints were unknown", 11.7 minutes after the last datapoint. This
  suggests an evaluation range of about 12 periods (N + 6, like the
  9-of-60 alarm's about 66). It is an **estimate**.
- **ALARM → OK.** It needed "minimum 1 datapoint for ALARM -> OK
  transition" (N − M + 1).
- **No wrong transitions.** No alarm returned to OK early, flapped or
  alarmed twice during the long, delayed or 14-minute runs.
- **Events.** 95 events matched 95 history items, with no duplicates.
  The metric math event carries `configuration.metrics` with the
  expression. The fields the Lambda reads are the same.

**Cleanup.** Every temporary resource was deleted, and an independent
check found none left: 0 alarms, rules, log groups and resource
policies. Afterwards the production burn rate alarm was `OK`, its rule
`DISABLED`, the Lambda unchanged, and `GMS/Availability` had only its
existing metrics.

**Cost.** About USD 0.01–0.02 (limit USD 1):

- 44 alarms for about 53 minutes;
- 11 test metrics;
- about 590 `PutMetricData` calls;
- about 310 `DescribeAlarms` calls.

## 4. Choice

**Sliding window, `treat_missing_data = "missing"`**, the same as the
9-of-60 alarm.

- **Not `FILL(m1, 1)` or the wall clock window:** they fail the adoption
  condition, because continuously late data was never detected.
- **Not `notBreaching`:** a stopped probe turns ALARM into OK, which
  would send NORMALIZED.
- **`missing`:** OK requires a real passed check. A stopped probe gives
  INSUFFICIENT_DATA, which sends DATA INSUFFICIENT. In this test that
  came 11.7 minutes after the last datapoint. This is an observation from
  one test, not a guaranteed time.
- **Cost of the choice:** not strictly 6 wall-clock minutes. The 6 most
  recent real datapoints within about 12 minutes are evaluated, and
  missing minutes are skipped. Messages say so.

The strict variants and reliable detection of late data cannot both be
had with the options available here.

**Final decision (Product Owner, 2026-11-28).** Sliding window with
`missing`. `FILL`, the wall clock window and `notBreaching` are not
used. Reliable detection of long outages takes priority over strict
6 wall-clock minutes. Evaluation across a missing minute is accepted as
known behavior, and the residual risks in §11 are accepted.

The "about 12 minutes" before DATA INSUFFICIENT is the integration test
observation (11.7 minutes), not a guaranteed or production-measured
time. The build applied in §8 still stated "for about 12 minutes" in the
DATA INSUFFICIENT text. The number was removed from the message on
2026-11-28 (§9).

## 5. Message samples (local test)

Rendered offline with stub context; under 1,000 characters each. The
DATA INSUFFICIENT sample shows the text after §9.

```text
HIGH — Availability Continuous Failure: ALARM
Alarm threshold: 6/6 failed datapoints in a row (the most recent ones; missing minutes are skipped)
Now: DOWN — 7 min (since first failed check 08:49 JST); 7 consecutive failed checks
Last 60 completed min (07:56 JST–08:56 JST): failed 7 / ok 53 / missing 0
Pattern: 1 observed episode, longest 7 min (an episode at the window end is counted only inside it)
Alarm: OK -> ALARM at 08:56:20 JST (2026-11-19T23:56:20.000Z UTC, alarm state timestamp)
Target: sunlightjetrans.com (external HTTP probe, HTTPUp); alarm gmsv5-availability-continuous-failure
The 6 most recent HTTP checks all failed: an ongoing outage signal. A missing minute between failures does not break the run. Check the HTTP DOWN/RECOVERED and EC2/Spot notifications.
Now = latest HTTP check when this message was generated. Last 60 = …
```

```text
HIGH — Availability Continuous Failure: NORMALIZED (alarm back to OK)
Alarm threshold: 6/6 failed datapoints in a row (the most recent ones; missing minutes are skipped)
Now: UP (last check 09:07 JST)
Last 60 completed min (08:07 JST–09:07 JST): failed 17 / ok 43 / missing 0
Pattern: 1 observed episode, longest 17 min
Alarm: ALARM -> OK at 09:07:20 JST (2026-11-20T00:07:20.000Z UTC, alarm state timestamp)
…
The CloudWatch alarm returned to OK: a passed check is among the 6 most recent datapoints. This is NOT the confirmed recovery time and does not confirm the site is up now; see Now and the HTTP DOWN/RECOVERED notifications.
```

```text
WARN — Availability Continuous Failure: DATA INSUFFICIENT
Alarm threshold: 6/6 failed datapoints in a row (the most recent ones; missing minutes are skipped)
Now: UNKNOWN — no HTTP check since 11:59 JST (last result: DOWN)
Last 60 completed min (11:12 JST–12:12 JST): failed 9 / ok 39 / missing 12
Pattern: 1 observed episode, longest 9 min; missing minutes split episodes
Alarm: ALARM -> INSUFFICIENT_DATA at 12:12:20 JST (2026-11-20T03:12:20.000Z UTC, alarm state timestamp)
…
Insufficient HTTPUp data to evaluate the alarm. This is a monitoring data issue, not evidence of an outage or recovery.
```

## 6. Implementation (local test)

| File | Change |
|---|---|
| `continuous_failure.tf` | New: alarm, rule (`DISABLED` while `off`), target, Lambda permission, output |
| `variables.tf` | `continuous_failure_alarm_rollout`, default `off` |
| `main.tf` | 3 Lambda variables: `CONTINUOUS_FAILURE_ALARM_NAME`, `_ARN`, `_DELIVERY`. Name and ARN are built from known values (region, caller account, alarm name), so the plan shows the other 10 variables unchanged; a test checks the ARN form |
| `lambda/burn_rate.py` | `Profile` (title, threshold, row prefix, meaning). `BURN_RATE` keeps the 9-of-60 texts and `burnrate#` row exactly; `CONTINUOUS_FAILURE` uses `contfail#` |
| `lambda/handler.py` | Picks the profile by `detail.alarmName`; the burn rate profile is the fallback, so unmatched events are rejected as before |
| `tests/test_continuous_failure.py`, `tests/fixtures/continuous-failure-events.json` | New tests; 6 real events from §3 with the account ID replaced by `000000000000` |

**Tests (local test).**

- 283 tests in total, all passing, three consecutive runs.
  - Burn rate state, messages and shadow: 68.
  - Context, deadlines, clients and worker limit: 73.
  - Continuous failure: 26.
  - Network guard: 3.
  - Other: 113.
- All 257 earlier tests are kept unchanged.
- The continuous failure tests cover:
  - the profile texts, and the 9-of-60 texts and row key unchanged;
  - independent ordering, duplicates, leases, delivery modes and ledgers
    with both alarms on one table;
  - shadow lifecycle and shadow text;
  - name/ARN pairs crossed between the alarms, rejected directly and
    through the handler;
  - handler dispatch, including a burn-rate-only environment;
  - replay of the 6 real events, including the metric math event;
  - Terraform consistency: alarm values, rule pattern and gate,
    permission, variables, no IAM change, burn rate files untouched.
- Mutation runs on copies of the code detected all 61 mutations:
  - 13 for profiles, dispatch and Terraform gating;
  - 38 for context, deadlines, worker limit, clients and shadow;
  - 10 for state handling.

## 7. Terraform plan

Production configuration, read only (2026-11-28). This plan was applied in §8.

- **4 to add, 1 to change, 0 to destroy.**
- Added:
  - `aws_cloudwatch_metric_alarm.continuous_failure` (values as in §6,
    sliding window, `missing`, no actions);
  - `aws_cloudwatch_event_rule.continuous_failure` (`DISABLED`);
  - `aws_cloudwatch_event_target.continuous_failure`;
  - `aws_lambda_permission.continuous_failure`
    (`AllowContinuousFailureAlarmEvents`).
- Changed: `aws_lambda_function.availability`.
  - Code: `CodeSha256` `ziom…` → `rwQ4c1wNkqiEOJzPzHsjSSHLBlhq8Y8Irkh/TEusxlk=`.
  - Environment: the 10 existing variables identical by value, plus
    `CONTINUOUS_FAILURE_ALARM_NAME`, `CONTINUOUS_FAILURE_ALARM_ARN`
    (same account and region as the burn rate ARN) and
    `CONTINUOUS_FAILURE_DELIVERY=disabled`.
- No-op: IAM role policy, burn rate alarm, rule, target and permission,
  HTTP probe rule, table.

## 8. Apply (stage `off`, 2026-11-28)

Approved by the Product Owner on 2026-11-28. Production configuration,
inspected by the Operator.

**Before apply.**

- HEAD `a9e88de` and a clean working tree.
- SSO role `AdministratorAccess` (profile `gms-admin`), `ap-northeast-1`,
  workspace `default`.
- The saved plan was identical to a fresh plan: 4 to add, 1 to change,
  0 to destroy.
- Artifact SHA-256 `rwQ4c1wNkqiEOJzPzHsjSSHLBlhq8Y8Irkh/TEusxlk=`; its 5
  files are byte-identical to the commit.
- In the plan, both rules were `DISABLED` and both deliveries `disabled`.

**Apply.** The saved plan was applied at 03:29:08–03:29:22 JST: 4 added,
1 changed, 0 destroyed. The Lambda update took 12 s.

**After apply.** Production configuration was captured immediately before
and after the apply and compared. The runtime checks ran at 03:35:55
JST.

| Check | Result |
|---|---|
| Lambda | `CodeSha256` `ziom…` → `rwQ4c1wNkqiEOJzPzHsjSSHLBlhq8Y8Irkh/TEusxlk=`; `Successful` / `Active`; runtime, 90 s timeout, 128 MB, role and handler unchanged |
| Environment | 13 variables. The 10 existing ones identical by value (hash comparison). Added: `CONTINUOUS_FAILURE_ALARM_NAME`, `CONTINUOUS_FAILURE_ALARM_ARN` (equal to the created alarm's real ARN) and `CONTINUOUS_FAILURE_DELIVERY=disabled`. `BURN_RATE_DELIVERY=disabled` |
| 6/6 alarm | As configured: `GMS/Availability` `HTTPUp` `Target=sunlightjetrans.com`, `Minimum`, 60 s, 6 of 6, `< 1`, `missing`; no actions, unit, metric math or evaluation window. `INSUFFICIENT_DATA` at creation (03:29:10) → `OK` at 03:29:41 JST. No other transition by 03:35 |
| 9/60 alarm | Configuration identical; `OK` since 2026-11-27 21:42 JST |
| Rules and targets | `gmsv5-availability-burn-rate` `DISABLED`, unchanged. `gmsv5-availability-continuous-failure` `DISABLED`; its pattern matches only the 6/6 alarm's name and ARN; its one target is the function. HTTP probe rule `ENABLED`, `rate(1 minute)`, unchanged |
| Lambda permissions | 6: the existing 5 unchanged, plus `AllowContinuousFailureAlarmEvents` with the 6/6 rule as source |
| IAM | Inline policy, policy names and attached policies identical |
| HTTP probe | `HTTPUp` = 1, one sample per minute, 03:27–03:34 JST (8 minutes across the apply) |
| Lambda metrics (03:30–03:35 JST) | 5 invocations, 0 errors, 0 throttles |
| Lambda log | 22 lines: 0 error or warning lines, 0 burn rate or continuous failure lines (expected in `off`). Max memory 95 MB of 128. Max duration 3.6 s. One cold start, init 318 ms |
| DynamoDB | HTTP row `up`, 0 failures, last slot 03:35 JST; no `burnrate#` or `contfail#` row |
| Readiness | HTTP 200 in 0.54 s, `ready`, release `0b72c3a` |
| Plan after apply | No changes |

The check output's HTTPUp heading still says "since 16:36Z", left over
from the previous script. The queried range was 18:27Z onward, as its
timestamps show.

## 9. DATA INSUFFICIENT text change (2026-11-28)

Requested by the Product Owner on 2026-11-28 after the §8 apply.

| | Text |
|---|---|
| Before (applied in §8) | "CloudWatch has had no HTTPUp datapoints for about 12 minutes. This is a monitoring data problem, not proof of downtime or recovery. Check the scheduled HTTP probe (Lambda gmsv5-availability)." |
| After | "Insufficient HTTPUp data to evaluate the alarm. This is a monitoring data issue, not evidence of an outage or recovery." |

- Only the 6-of-6 DATA INSUFFICIENT text changed (`CONTINUOUS_MEANING`
  in `lambda/burn_rate.py`). The 9-of-60 texts, alarm settings, state
  handling, IAM and notification logic are unchanged.
- **Tests (local test):** 284 in total, all passing: the 283 earlier
  tests plus one for the exact new text, which also checks the 9-of-60
  text is unchanged. Mutation runs: all 61 detected (51 context, profile and shadow, 10 state).
- **Plan (from `968a1a7`):** 0 to add, 1 to change, 0 to destroy. Only
  the Lambda `source_code_hash` changed (`last_modified` is computed by
  AWS). All 13 environment variables were identical.

**Apply (stage `off`, approved by the Product Owner).** The plan was
applied at 04:10:19–04:10:26 JST: 0 added, 1 changed, 0 destroyed.
Production configuration was captured before and after it.

| Check | Result |
|---|---|
| Lambda | `CodeSha256` `rwQ4…` → `NRgrC1cjzpkmtEH1c+YCZ4kkwn6JWLoSoi1m4PlDJuc=` (= artifact; the 5 files equal `968a1a7`); `Successful` / `Active`. Only `CodeSha256` and `LastModified` changed |
| Environment | 13 variables identical by value; both deliveries `disabled` |
| Alarms | Both configurations identical. Burn rate `OK` since 2026-11-27 21:42 JST; continuous failure `OK` since 03:29:41 JST |
| Rules, targets, permissions | Identical. Both alarm rules `DISABLED`; HTTP probe rule `ENABLED` |
| IAM | Inline policy, policy names and attached policies identical |
| HTTP probe | `HTTPUp` = 1, one sample per minute, 04:08–04:15 JST (8 minutes across the apply) |
| Lambda metrics (04:11–04:16 JST) | 5 invocations, 0 errors, 0 throttles |
| Lambda log | 22 lines: 0 error or warning lines, 0 alarm-notification lines. Max memory 95 MB of 128. Max duration 3.5 s. One cold start, init 325 ms |
| DynamoDB | HTTP row `up`, 0 failures, last slot 04:16 JST; no `burnrate#` or `contfail#` row |
| Readiness | HTTP 200 in 0.23 s, `ready`, release `0b72c3a` |
| Plan after apply | No changes |


## 10. Verification status

| Item | Status |
|---|---|
| CloudWatch evaluation of 6 of 6, missing data, late data | Integration test (§3) |
| Profiles, separate rows, ledgers, leases, delivery modes, name/ARN pairing | Local test |
| Parsing of real events, including metric math | Local test with captured events |
| 9-of-60 messages and state handling unchanged | Local test |
| Production configuration | Applied in `off` and checked (§8) |
| Delivery of a real event to the Lambda, Discord delivery | Pending: `shadow`, `live` |

## 11. Residual risks

- Not strictly 6 wall-clock minutes (§4).
- The about-12-minute evaluation range is an estimate from one test.
- Late data delays ALARM by about the delay.
- A flapping site can give several ALARM and NORMALIZED pairs; one
  passed check ends the alarm.
- Shared fate with the probe, at-least-once delivery, no DLQ, and worker
  slots shared with the burn rate alarm (Runbook §13.3, §13.4, §13.11).

## 12. Evidence handling

This document contains no AWS account ID, credential, session token,
Terraform State or Discord token. The integration test data, publish
log, run log, alarm configuration and history, captured events and the
test script are kept outside the repository with SHA-256 checksums,
because they contain ARNs with the account ID. The same applies to the
before and after snapshots of the 2026-11-28 apply, its apply log
(account ID masked), post-apply check output and scripts, and to those of
the §9 apply.
