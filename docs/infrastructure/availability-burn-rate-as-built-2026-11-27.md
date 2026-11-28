# GMS v5 — Availability Burn Rate Alarm As-Built (2026-11-27)

- **Evidence date:** 2026-11-27 JST (applied 21:41:50–21:42:05 JST = 12:41:50–12:42:05Z). §6 and §7 were added on 2026-11-28 (redesign applied 01:37:36–01:37:45 JST = 2026-11-27 16:37:36–16:37:45Z).
- **Owner:** Infrastructure / SRE
- **Scope:** `infra/terraform/availability/`, the existing EventBridge → Lambda → DynamoDB → Discord availability monitoring. No new Lambda, SNS topic, table or EC2.
- **Current status:**
  - **Applied in stage `off`.** The alarm evaluates. The rule is `DISABLED` and nothing is sent.
  - **CloudWatch evaluation measured** in an isolated test.
  - **Message redesign (§6) applied in stage `off` on 2026-11-28 01:37 JST (§7).** Its code path first ran in production during the `shadow` planned stop test that day.
  - **`shadow` from 2026-11-28 18:02 JST and `live` from 23:12:47 JST**:
    [P2 Rollout As-Built](./availability-p2-rollout-as-built-2026-11-28.md).
    The code path ran in production during the planned stop test.
- **Design and operation:** [Availability Runbook §13](./availability-external-runbook.md#13-availability-burn-rate-alarm-p2)
- **Earlier record:** [Spot Reason Tracking As-Built 2026-11-25](./spot-reason-tracking-as-built-2026-11-25.md)

> **Evidence classes.** Each statement below is one of these. Do not
> promote one class to another.
>
> - **Local test:** unit tests on the Mac with the fake DynamoDB, stub
>   CloudWatch and botocore's Stubber. No AWS calls: since 2026-11-28
>   `tests/no_network.py` blocks every non-loopback connection for the
>   test process, and the last test checks that none was attempted.
> - **Production configuration:** AWS output inspected by the Operator
>   after apply. Not re-queried while writing this document.
> - **Integration test:** CloudWatch behavior measured with temporary
>   alarms in a separate namespace. No production resource was used.
> - **Pending:** needs a stage change or a real alarm transition.

## 1. Purpose

The alarm notifies when the external HTTP probe has failed in at least
9 of the last 60 one-minute samples. For a 99 % / 7-day SLO this is the
first count above a 14.4x burn rate. It detects repeated interruptions,
such as several Spot stops in an hour, that the HTTP DOWN/RECOVERED
notifications report only one by one.

## 2. What was applied (stage `off`)

**Terraform apply (production configuration):** 4 added, 1 changed,
0 destroyed, from the reviewed plan file. No replacement.

| Resource | Change |
|---|---|
| `aws_cloudwatch_metric_alarm.burn_rate_fast` | New. `gmsv5-availability-burn-rate-fast`: `GMS/Availability` `HTTPUp` `Target=sunlightjetrans.com`, `Minimum`, 60 s, 9 of 60, `< 1`, `missing`. No actions, no unit |
| `aws_cloudwatch_event_rule.burn_rate` | New. `gmsv5-availability-burn-rate`, `DISABLED`. Pattern: this alarm's ARN and name, `CloudWatch Alarm State Change` |
| `aws_cloudwatch_event_target.burn_rate` | New. The existing function |
| `aws_lambda_permission.burn_rate` | New. `AllowBurnRateAlarmEvents`, source ARN = this rule only |
| `aws_lambda_function.availability` | New code (`burn_rate.py`) and 3 new variables: `BURN_RATE_ALARM_NAME`, `BURN_RATE_ALARM_ARN`, `BURN_RATE_DELIVERY=disabled`. The 7 existing variables are unchanged |

**Code (repository check).** The applied source is commit `d81c157` on
branch `feat/availability-burn-rate-alarm`. Before this document was
written, the deployed package (`CodeSha256`
`pAnDdowChBMFgb8+EEmlnXXBQBkL1o5T6gmBSkGYL7I=`) and the local build were
compared file by file with that commit: `burn_rate.py`, `deployment.py`,
`handler.py` and `spot_status.py` are byte-identical.

**Rollback artifact.** The previous code (`CodeSha256`
`cw8JPPcmFYdo5Xsc7YwD4zxK7QsCIw8CAsiINW9oiQo=`, the 2026-11-25 Spot
build) was downloaded from AWS before the apply and kept outside the
repository. Its 3 files are byte-identical to `main`. The local
`lambda-package.zip` is rebuilt by every plan, so it is never the
rollback artifact.

## 3. Checks after apply

Production configuration, inspected 2026-11-27 21:42–21:47 JST:

| Check | Result |
|---|---|
| Alarm | Values as in §2, no actions. `INSUFFICIENT_DATA` → `OK` about 1 minute after apply; reason "59 out of the last 60 datapoints were not less than the threshold … (minimum 52 datapoints for ALARM -> OK transition)" |
| Rule | `DISABLED`; pattern limited to the production alarm's ARN and name |
| Lambda permission | Only for this rule's ARN; the 4 existing permissions unchanged |
| Lambda | `CodeSha256` `pAnD…` = local package; `Successful`; runtime, timeout (90 s), memory (128 MB) and role unchanged |
| Environment | 7 existing variables unchanged by value; `BURN_RATE_DELIVERY=disabled`; ARN variable equals the alarm |
| HTTP probe | `HTTPUp` = 1 every minute; 5 invocations in the first 5 minutes, 0 errors, 0 throttles |
| Lambda log | 0 error or warning lines; max memory 95 MB; max duration 3.6 s |
| DynamoDB | HTTP row `up`; no `burnrate#` row (the rule is disabled, so the function was not invoked for the alarm) |
| Readiness | HTTP 200, `ready`, release `0b72c3a` |
| Plan after apply | No changes |

## 4. Isolated CloudWatch integration test

2026-11-27 21:47–23:05 JST (T = 12:47Z), approved by the Product Owner.

**Isolation.**

- Namespace `GMSv5/BurnRateIntegrationTest` and 7 temporary alarms
  `gmsv5-burnrate-itest-<case>`, configured like production except
  namespace and dimension.
- A temporary capture rule and log group (1-day retention) for the
  state-change events.
- Production was not touched: no write to `GMS/Availability`, no
  `set-alarm-state`, no production alarm, rule or Lambda change, no EC2
  stop.
- After the test, 0 temporary alarms, rules, log groups and resource
  policies remained. The production alarm was `OK`, its rule `DISABLED`,
  and no `burnrate#` row existed.

**Test data and transitions.** Minutes are after T. "Model" is a
60-minute wall-clock M-of-N model.

| Case | Data | Transitions (measured) | Model | Match |
|---|---|---|---|---|
| `c8` | 8 of 60 failed, then good samples | OK (+2.2) | OK | Yes |
| `c9` | 9 of 60 failed (every 2nd minute), then good | ALARM (+2.5), OK (+44.5) | OK at +43 | About 1 min later |
| `short2` | 2 consecutive failures | OK | OK | Yes |
| `normalize` | 9 failures 47–55 min back, then good | ALARM (+1.9), OK (+7.9) | OK at +6 | About 1–2 min later |
| `lookback` | 8 failures and 10 missing in the last 60 min, 1 failure 65 min back | OK | OK | Yes (65 min back is outside the range) |
| `sparse9` | Only 9 samples, all failed, then nothing | ALARM (+2.5), INSUFFICIENT_DATA (+65.5) | OK, then INSUFFICIENT_DATA | **No.** Premature ALARM, then no OK |
| `stop` | 60 good samples, then nothing | OK, INSUFFICIENT_DATA (+65.7) | INSUFFICIENT_DATA at +60 | About 6 min later |

**Events.** 11 state-change events for 11 alarm-history items. No
duplicates and no out-of-order delivery were seen. `resources` held one
ARN, `state.timestamp` used `+0000`, and the initial "Unchecked" state
emitted no event. The production ARN check rejected all test events
(`unmonitored alarm`).

**Cost.** 7 alarms for about 1.3 hours, 7 custom metrics, about 380
`PutMetricData` calls: about USD 0.01–0.02. The limit was USD 1.

## 5. Differences from the local model

Integration test results, applied to Runbook §13.1 and §13.2:

1. **The evaluation range is about 66 minutes**, not 60. Three
   observations support this: a failure 65 minutes back was outside it;
   INSUFFICIENT_DATA came about 65.5 minutes after the last datapoint;
   transitions ran about 1 minute behind the model. This is an
   **estimate**. AWS does not document it.
2. **ALARM → OK needs at least 52 non-breaching datapoints**, stated in
   the reason. Without them the alarm stays in ALARM. If data then stops,
   it goes straight to INSUFFICIENT_DATA (`sparse9`), and no NORMALIZED
   is sent.
3. **Premature ALARM:** 9 datapoints in total, all failed, raise ALARM.
4. **`reasonData` is not the evaluation window.** `recentDatapoints` had
   0–61 entries, with `null`. `evaluatedDatapoints` held only the 9
   breaching ones on ALARM and 46–52 on OK and INSUFFICIENT_DATA.
   The former "Observed" line computed a burn rate in 1 of 11 events
   (correctly) and said "not reported" in the other 10. It never showed
   a wrong number, but it was useless. §6 removes it.

The backtest result (4 episodes, 197 active minutes) stands for its
complete evidence. It cannot predict the gap cases above.

## 6. Message redesign (2026-11-28)

**Decision (Product Owner, 2026-11-28).** Messages support the first
response to an incident. They no longer report a burn rate: no
restored "Observed" line, and no count quoted from the CloudWatch reason.
The alarm is unchanged.

**Change (local test, repository check).**

- `lambda/burn_rate.py`: the Observed parsing and the burn rate, SLO and
  error budget lines are removed. The new line is `Alarm threshold: 9/60
  failed datapoints`. Context is looked up only after the lease is
  claimed, or after a shadow ledger mark.
- `lambda/burn_rate_context.py` (new):
  - `Now` comes from the existing HTTP state row.
  - `Last 60` and `Pattern` come from one `GetMetricData` call.
  - Details: Runbook §13.11.
- `handler.py`: wiring only. `main.tf`: statement
  `ReadBurnRateContextMetric` (`cloudwatch:GetMetricData`, `*`,
  `aws:RequestedRegion = ap-northeast-1`).

**Review fixes (Product Owner review, 2026-11-28, second commit).**

- **Shadow lifecycle.** The first version decided shadow messages from
  the live ledger (`notified_state`), which shadow never writes, so
  NORMALIZED and DATA RESTORED could never be logged. Shadow now has its
  own ledger, `shadow_notified_state` / `shadow_notified_at`, marked with
  a conditional write before the log line. Live never reads it and shadow
  never writes `notified_*` or a lease. All four kinds are logged with
  `"delivered": false` (Runbook §13.5).
- **Preview removed.** The read-only preview invocation path is gone
  from the production Lambda. Production context is checked in `shadow`.
- **Hard lookup deadline.** The first version checked the remaining
  budget before each call, but SDK timeouts are per socket operation, so
  a call could run past the 30 s budget. Each lookup now runs in a daemon
  thread and is abandoned at `min(start + 30 s, Lambda end − 25 s)`. The
  DynamoDB lookup uses a low-level (thread-safe) client. Guaranteed limits
  and estimates are separated in Runbook §13.11.

**Pre-apply findings (2026-11-28, third commit).** The pre-apply check
for the stage `off` re-apply was stopped before apply, on two findings:

- The DynamoDB client was created inside a worker thread from boto3's
  default Session, which boto3 documents as not thread safe.
- Abandoned workers had no count limit.

Fixes:

- Both clients are now created on the main thread, only when a lookup is
  about to run, and reused. Workers only make the read call.
- At most 2 workers exist per execution environment, abandoned ones
  included, enforced with a `BoundedSemaphore`. A slot is taken before
  the thread starts and released when the worker ends, including on
  error. With no free slot the line is `unavailable (busy)`, without
  waiting.
- A local check script had sent read requests to AWS with dummy
  credentials (rejected, no data). The test suite now blocks the network
  itself instead of relying on fake credentials.

Details: Runbook §13.11.

**Tests (local test).**

- 257 tests in total, all passing, five consecutive runs.
  - Burn rate state, messages and shadow lifecycle: 68.
  - Responder context, deadlines, clients and worker limit: 73.
  - Network guard: 3.
  - Other: 113 (unchanged except the guard import).
- Worker limit, measured with real threads and an API that never
  returns:
  - 25 consecutive invocations: at most 2 workers. From the 3rd on, the
    lines say `busy`.
  - 12 concurrent callers: exactly 2 started; 10 got `busy`, without
    waiting.
  - Slots stay taken after the caller stops waiting. They become free
    again when the workers end, including after exceptions and a failed
    thread start.
  - A late result never reached a later message. An abandoned worker
    made no table call after the handler returned.
- The shadow tests cover:
  - ALARM → OK;
  - OK → INSUFFICIENT_DATA → OK;
  - ALARM → INSUFFICIENT_DATA → OK, which is never NORMALIZED;
  - the same event sequence giving the same kinds in live and in shadow;
  - duplicate, stale, out-of-order and concurrent events;
  - a state change after the read;
  - both ledgers kept separate across `shadow` → `live`.
- Deadline tests use real threads with hung calls and check that the
  lines come back as `deadline` within the limit.
- Of the 164 tests before the change:
  - 5 that checked the removed Observed output were replaced by tests
    that it is gone.
  - 7 were updated for the new message format and for the design
    constants that left the Lambda; their intent is unchanged.
- Mutation runs on copies of the code detected all mutations:
  - 38 of 38 for context, shadow ledger, deadline, worker limit and
    client creation;
  - 10 of 10 for state handling.
- botocore's Stubber validated the GetMetricData and DynamoDB GetItem
  requests against the API models.

**Samples (local test, stub data).** Each is under 900 characters; the
relay limit is 1,800.

ALARM:

```text
HIGH — Availability Burn Rate: ALARM
Alarm threshold: 9/60 failed datapoints
Now: DOWN — 4 min (since first failed check 21:10 JST); 5 consecutive failed checks
Last 60 completed min (20:14 JST–21:14 JST): failed 9 / ok 51 / missing 0
Pattern: 3 observed episodes, longest 4 min (an episode at the window end is counted only inside it)
Alarm: OK -> ALARM at 21:14:41 JST (2026-11-27T12:14:41.120Z UTC, alarm state timestamp)
Target: sunlightjetrans.com (external HTTP probe, HTTPUp); alarm gmsv5-availability-burn-rate-fast
Many recent HTTP checks failed. Check the HTTP DOWN/RECOVERED and EC2/Spot notifications.
Now = latest HTTP check when this message was generated. Last 60 = the 60 completed minutes before the alarm transition, counted independently of CloudWatch's own evaluation (which can look further back).
```

NORMALIZED:

```text
HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)
Alarm threshold: 9/60 failed datapoints
Now: UP (last check 22:14 JST)
Last 60 completed min (21:13 JST–22:13 JST): failed 18 / ok 42 / missing 0
Pattern: 1 observed episode, longest 18 min
Alarm: ALARM -> OK at 22:13:41 JST (2026-11-27T13:13:41.500Z UTC, alarm state timestamp)
Target: sunlightjetrans.com (external HTTP probe, HTTPUp); alarm gmsv5-availability-burn-rate-fast
The CloudWatch alarm returned to OK: its 9-of-60 condition is no longer met. This is NOT when the site recovered and does not confirm it is up now; see Now and the HTTP DOWN/RECOVERED notifications.
Now = latest HTTP check when this message was generated. Last 60 = …
```

INSUFFICIENT_DATA (probe stopped):

```text
WARN — Availability Burn Rate: DATA INSUFFICIENT
Alarm threshold: 9/60 failed datapoints
Now: UNKNOWN — no HTTP check since 23:14 JST (last result: passed)
Last 60 completed min (23:20 JST–00:20 JST): failed 0 / ok 0 / missing 60
Pattern: no failed minutes
Alarm: OK -> INSUFFICIENT_DATA at 00:20:10 JST (2026-11-27T15:20:10.000Z UTC, alarm state timestamp)
Target: sunlightjetrans.com (external HTTP probe, HTTPUp); alarm gmsv5-availability-burn-rate-fast
CloudWatch has too few HTTPUp datapoints to evaluate the alarm. This is a monitoring data problem, not proof of downtime or recovery. Check the scheduled HTTP probe (Lambda gmsv5-availability).
Now = latest HTTP check when this message was generated. Last 60 = …
```

When both lookups fail, the lines read `Now: UNKNOWN — HTTP state
unavailable (TimeoutError)` and `Last 60 completed min: unavailable
(AccessDenied)`, and the message is still sent.

**Plan (production configuration, read only, 2026-11-28).**

- 0 to add, 2 to change, 0 to destroy.
- `aws_lambda_function.availability`: only `source_code_hash` changes,
  from `pAnD…` to the new build. All 10 environment variables are
  unchanged, including `BURN_RATE_DELIVERY=disabled`.
- `aws_iam_role_policy.lambda_runtime`: the one statement above is
  added. The other 5 statements are unchanged.
- Alarm, burn rate rule (`DISABLED`), target, permission and HTTP probe
  rule: no-op.
- That plan was not applied. The fixes below changed the code, and the
  plan applied in §7 was made from commit `d69cb6f`.

## 7. Redesign apply (stage `off`, 2026-11-28)

Approved by the Product Owner on 2026-11-28. Production configuration,
inspected by the Operator.

**Before apply.**

- HEAD `d69cb6f` and a clean working tree.
- SSO role `AdministratorAccess` (profile `gms-admin`), `ap-northeast-1`,
  workspace `default`.
- The saved plan was identical to a fresh plan against the current state:
  0 to add, 2 to change, 0 to destroy.
- Artifact SHA-256 `ziomjW9ojuoJo1UltpkkhqqIwLGB+SZ0zHfC/v1yw5k=`; its 5
  files are byte-identical to the commit.

**Apply.** The saved plan was applied at 01:37:36–01:37:45 JST: 0 added,
2 changed, 0 destroyed.

- `aws_iam_role_policy.lambda_runtime` took 2 s.
- `aws_lambda_function.availability` took 6 s.

**After apply.** Production configuration was captured immediately before
and after the apply and compared:

| Check | Result |
|---|---|
| Lambda | `CodeSha256` `pAnD…` → `ziomjW9ojuoJo1UltpkkhqqIwLGB+SZ0zHfC/v1yw5k=` (= artifact); `Successful` / `Active`. Only `CodeSha256` and `LastModified` changed; runtime, 90 s timeout, 128 MB, role and handler unchanged |
| IAM | One statement added, `ReadBurnRateContextMetric` (`cloudwatch:GetMetricData`, `*`, `aws:RequestedRegion = ap-northeast-1`). None removed; the other 5 identical |
| Environment | All 10 variables identical by value (compared as hashes); `BURN_RATE_DELIVERY=disabled` |
| Alarm | Configuration identical (9 of 60, `Minimum`, 60 s, `< 1`, `missing`, no actions, no unit); state `OK` since 2026-11-27 21:42 JST |
| EventBridge | Burn rate rule `DISABLED`; HTTP probe rule `ENABLED`, `rate(1 minute)`; the 5 Lambda permissions identical |
| HTTP probe | `HTTPUp` = 1, one sample per minute, 01:36–01:43 JST (8 minutes across the apply) |
| Lambda metrics (01:38–01:44 JST) | 5 invocations, 0 errors, 0 throttles |
| Lambda log | 22 lines: 0 error or warning lines, 0 burn rate lines (expected in `off`). Max memory 95 MB of 128 (as before). Max duration 3.5 s. One cold start, init 327 ms |
| DynamoDB | HTTP row `up`, 0 failures, last slot 01:43 JST; no `burnrate#` row |
| Readiness | HTTP 200 in 0.30 s, `ready`, release `0b72c3a` |
| Plan after apply | No changes |

The burn rate implementation for 9 of 60 is closed with this apply. The
remaining steps are operational: `shadow`, then `live`, each with its own
approval (Runbook §13.5).

## 8. Verification status

| Item | Status |
|---|---|
| Stage `off` apply and checks | Done (§2, §3) |
| CloudWatch 9-of-60 evaluation incl. gaps | Measured (§4, §5) |
| State, ordering, lease and message logic | Local test |
| Message redesign | Applied in `off` (§7). Not yet run in production |
| Shadow lifecycle (all four kinds, separate ledger) | Local test |
| Production context (HTTP row, `HTTPUp`, IAM) | Pending: `shadow`, at the next alarm transition |
| EventBridge → Lambda delivery of a real alarm event | Pending: `shadow` |
| Discord delivery of a burn rate message | Pending: `live` |

## 9. Residual risks

- **Shared fate:** one function both publishes `HTTPUp` and sends these
  messages. There is no meta-alarm.
- **No DLQ:** a message is lost if all three attempts fail. Duplicates
  are possible (at-least-once delivery).
- **NORMALIZED:** it lags recovery by about an hour, and is skipped when
  data stops during an ALARM.
- **About-66-minute range:** an estimate from one test.
- **`Last 60`:** it can differ from CloudWatch's evaluation, and late
  data shows as missing.
- **IAM:** the role can read any CloudWatch metric in Tokyo, because
  `GetMetricData` cannot be limited by resource or namespace.
- **`shadow`:** it shows nothing while the alarm stays OK.
- **Shadow log loss:** if the function dies between the shadow mark and
  the log line, that shadow message is not logged. `live` is unaffected.
- **`notify()` time:** the lookups never use the last 25 s of the
  Lambda. `notify()` itself (SSM with SDK defaults on a cold container,
  urlopen 8 s per socket operation) is not bounded by this change.
- **Abandoned lookups:** a lookup cut off at the deadline keeps running
  in a background thread until its SDK timeouts end. Its result is
  discarded. At most 2 such workers exist; while both are held, context
  lines say `busy`.
- **Client creation time:** client creation on the main thread is
  counted against the 30 s budget but is not interruptible once started.
  It is estimated, not bounded.

## 10. Rollback

Runbook §13.8. In short:

- **Stage `off`:** messages are already stopped.
- **Message redesign:** revert `a70f1b3`, `c209503` and `d69cb6f`. The
  plan has 2 changes: the Lambda code goes back to the `d81c157` build
  (`CodeSha256` `pAnD…`), and the IAM statement is removed.
- **Whole feature:** revert all four burn rate commits. The 4 resources
  are destroyed, the IAM statement is removed and the Lambda returns to
  the 2026-11-25 build.

## 11. Evidence handling

This document records resource names, configuration values, code hashes,
measured times and summarized results. It contains no AWS account ID,
credential, session token, Terraform State, Discord token or raw log
export.

The following are kept outside the repository because they contain ARNs
with the account ID:

- the integration test data, events, alarm history, run log and script,
  with SHA-256 checksums;
- the rollback package;
- the before and after snapshots of the 2026-11-28 apply, its apply log
  (account ID masked), its post-apply check output and script, with
  SHA-256 checksums.

Plan files and State stay local and are not in Git.
