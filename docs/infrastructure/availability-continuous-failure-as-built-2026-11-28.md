# GMS v5 — Continuous Failure Alarm (6 of 6) As-Built (2026-11-28)

- **Evidence date:** 2026-11-28 JST. The integration test ran 02:01–02:54 JST (2026-11-27 17:01–17:54Z).
- **Owner:** Infrastructure / SRE
- **Scope:** `infra/terraform/availability/`. One CloudWatch alarm, one EventBridge rule, the existing Lambda, table and Discord egress. No new Lambda, SNS topic, table, EC2 or IAM permission.
- **Current status:**
  - **Implemented and tested locally. Not applied.**
  - CloudWatch evaluation measured in an isolated test.
  - The missing-data choice (§4) awaits the Product Owner's final decision.
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
  INSUFFICIENT_DATA, which sends DATA INSUFFICIENT, about 12 minutes
  later.
- **Cost of the choice:** not strictly 6 wall-clock minutes. The 6 most
  recent real datapoints within about 12 minutes are evaluated, and
  missing minutes are skipped. Messages say so.

The strict variants and reliable detection of late data cannot both be
had with the options available here. The final decision rests with the
Product Owner. Switching to `notBreaching` is a one-line change in
`continuous_failure.tf` and the message text.

## 5. Message samples (local test)

Rendered offline with stub context; under 1,000 characters each.

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
CloudWatch has had no HTTPUp datapoints for about 12 minutes. This is a monitoring data problem, not proof of downtime or recovery. Check the scheduled HTTP probe (Lambda gmsv5-availability).
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

Production configuration, read only (2026-11-28). Not applied.

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

## 8. Verification status

| Item | Status |
|---|---|
| CloudWatch evaluation of 6 of 6, missing data, late data | Integration test (§3) |
| Profiles, separate rows, ledgers, leases, delivery modes, name/ARN pairing | Local test |
| Parsing of real events, including metric math | Local test with captured events |
| 9-of-60 messages and state handling unchanged | Local test |
| Production configuration | Pending: apply in `off` |
| Delivery of a real event to the Lambda, Discord delivery | Pending: `shadow`, `live` |

## 9. Residual risks

- Not strictly 6 wall-clock minutes (§4).
- The about-12-minute evaluation range is an estimate from one test.
- Late data delays ALARM by about the delay.
- A flapping site can give several ALARM and NORMALIZED pairs; one
  passed check ends the alarm.
- Shared fate with the probe, at-least-once delivery, no DLQ, and worker
  slots shared with the burn rate alarm (Runbook §13.3, §13.4, §13.11).

## 10. Evidence handling

This document contains no AWS account ID, credential, session token,
Terraform State or Discord token. The integration test data, publish
log, run log, alarm configuration and history, captured events and the
test script are kept outside the repository with SHA-256 checksums,
because they contain ARNs with the account ID.
