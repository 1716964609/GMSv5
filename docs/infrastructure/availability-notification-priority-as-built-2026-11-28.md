# GMS v5 — Discord Notification Priority As-Built (2026-11-28)

- **Evidence date:** 2026-11-28 JST (applied in stage `off` 16:13:15–16:13:28 JST, §7)
- **Owner:** Infrastructure / SRE
- **Scope:** `infra/terraform/availability/` only. The `#gms-alerts` channel, the webhook, the CloudFront relay (`discord-alert-egress`), IAM and the alarms are unchanged.
- **Current status:**
  - **Applied in stage `off` (§7).** The user ID is set in `terraform.tfvars`
    and in the Lambda.
  - Both alarms have been `live` since 2026-11-28 23:12:47 JST:
    [P2 Rollout As-Built](./availability-p2-rollout-as-built-2026-11-28.md).
    HIGH alarm messages now mention the configured user.
  - **Device test done (§8):** LOW passed; HIGH passed on Retry 1 after
    a Discord setting change. The first HIGH reached Discord with the
    mention but gave no iPhone push.
- **Design and operation:** [Availability Runbook §15](./availability-external-runbook.md#15-discord-notification-priority)

> **Evidence classes.**
>
> - **Repository check:** reading the code of this repository.
> - **Documentation:** the Discord developer documentation.
> - **Local test:** unit tests on the Mac with the network blocked
>   (`tests/no_network.py`).
> - **Production configuration:** Terraform plan, read only.

## 1. Decisions (Product Owner, 2026-11-28)

| Item | Decision |
|---|---|
| HIGH | Burn rate 9/60 and continuous failure 6/6: ALARM and DATA INSUFFICIENT. A mention of one user at the start of `content`; `allowed_mentions` = `{"users": [ID]}`, without `parse` |
| LOW | Everything else (NORMALIZED, DATA RESTORED, HTTP DOWN/UP, Spot, rebalance, deployment). Existing `{"parse": []}` kept |
| UptimeRobot | No mention |
| User ID | Terraform variable `discord_alert_user_id`, `sensitive = true`, only in `terraform.tfvars` (not in Git) |
| Safety | A plan fails if an alarm rollout is `live` and the ID is empty. The Lambda sends without a mention and warns if the ID is missing or invalid |
| Test messages | The existing synthetic test, extended with `priority` (`low` default, `high`) |

## 2. Findings before the change

**Repository check.**

- `notify()` sent `{"content": message[:1800], "allowed_mentions": {"parse": []}}`
  to the CloudFront `/notify` path, so no message could ping anyone.
  It used no embeds.
- The relay function `auth.js` checks method, URI and token, deletes the
  token header and rewrites the URI to the webhook path. CloudFront
  Functions cannot read or change the body.
- The origin request policy forwards only `Content-Type`, with no query
  string, so the webhook answers 204 (`wait` is not used).

**Documentation (Discord).**

- `parse` is "mutually exclusive" with the other fields.
- `users` takes at most 100 IDs. Only IDs that are both listed and in
  the content are pinged.
- Without `allowed_mentions`, webhooks parse user mentions.
- `content` is at most 2,000 characters.
- The mention format is `<@USER_ID>`.

## 3. Change (local test)

| File | Change |
|---|---|
| `lambda/handler.py` | `discord_payload()` builds LOW and HIGH payloads. `alert_user_id()` validates `DISCORD_ALERT_USER_ID` (17–20 digits) and warns without logging the value. `notify(message, mention=False)` keeps the LOW default; `notify_high()` adds the mention. `masked_payload()` gives shadow logs the exact payload with `USER_ID_MASKED`. The synthetic test accepts `priority` `low` (default) or `high`, rejects anything else, and marks HIGH as TEST |
| `lambda/burn_rate.py` | `HIGH_KINDS = {alarm, insufficient}` go to `notify_high`; NORMALIZED and DATA RESTORED go to `notify`. Shadow lines add `priority`, `mention` and the masked `payload` |
| `variables.tf` | `discord_alert_user_id`: sensitive, default `""`, validation `^([0-9]{17,20})?$` |
| `main.tf` | Lambda variable `DISCORD_ALERT_USER_ID`; precondition: the ID must be set if either alarm rollout is `live` |
| `terraform.tfvars.example` | A commented placeholder line |
| `tests/test_notification_priority.py` | New tests (§5) |
| `tests/test_burn_rate.py`, `tests/test_continuous_failure.py` | Two handler routing fixtures also capture `notify_high`. Assertions unchanged |

All other notifications call `notify(message)` exactly as before.

## 4. Payload samples (local test, test ID shown as `<USER_ID>`)

HIGH (continuous failure ALARM):

```json
{"content": "<@<USER_ID>> HIGH — Availability Continuous Failure: ALARM\nAlarm threshold: 6/6 failed datapoints in a row …",
 "allowed_mentions": {"users": ["<USER_ID>"]}}
```

LOW (burn rate NORMALIZED; the same for every other LOW message):

```json
{"content": "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)\nAlarm threshold: 9/60 failed datapoints …",
 "allowed_mentions": {"parse": []}}
```

Shadow log fields for a HIGH message:

```json
{"priority": "high", "mention": true,
 "payload": {"content": "<@USER_ID_MASKED> HIGH — Availability Burn Rate: ALARM\n…",
             "allowed_mentions": {"users": ["USER_ID_MASKED"]}}}
```

HIGH synthetic test:

```json
{"content": "<@<USER_ID>> [GMS v5 AVAILABILITY TEST] HIGH priority TEST at …; mention path check only, no incident, no EC2 stop",
 "allowed_mentions": {"users": ["<USER_ID>"]}}
```

The NORMALIZED header keeps its earlier `HIGH —` label. It is now sent
as LOW, without a mention.

## 5. Tests

**Local test.**

- 307 tests in total, all passing, three consecutive runs: the 284
  earlier tests plus 23 new ones. The network was blocked.
- The new tests cover:
  - exact LOW and HIGH payloads, with no `parse` and no `roles` on HIGH;
  - the mention kept first when the message is truncated;
  - `@everyone`, `@here`, role and other-user mentions in a message
    never pinging (LOW parses nothing; HIGH lists only the one ID);
  - 7 missing or invalid IDs sending without a mention and warning
    without the value;
  - the masked shadow payload;
  - the real send path with `urlopen` replaced: `notify` is LOW even
    with an ID set, `notify_high` is HIGH, HTTP DOWN/RECOVERED and the
    Spot warning stay LOW;
  - an AST check that only `notify_high` can add a mention and only the
    HIGH synthetic test calls it directly;
  - both alarms × 4 kinds routed to HIGH or LOW;
  - shadow lines with priority, mention and masked payload, and no ID;
  - the synthetic test default, `low`, `high` and 7 rejected values;
  - the Terraform variable, environment entry and precondition;
  - `terraform.tfvars` not tracked;
  - no 17–20 digit number in the module or `docs/infrastructure/`.
- Mutation runs on copies, each first checked to pass unmutated,
  detected all 78 mutations: 17 new (payload shape, ID validation,
  routing, masking, synthetic priority, wiring, precondition,
  sensitivity) and the 61 earlier ones.

**Plan checks (production configuration, read only, `-refresh=false`).**

- `burn_rate_alarm_rollout=live` with no ID: plan failed, "Resource
  precondition failed … Set discord_alert_user_id in terraform.tfvars
  (not in Git) before any alarm rollout is live".
- `continuous_failure_alarm_rollout=live` with no ID: plan failed the
  same way.
- A malformed ID: plan failed with "Use an empty string or a 17-20 digit
  Discord user ID." The value was not echoed.

## 6. Terraform plan

Production configuration, read only (2026-11-28), before the ID was set.
`terraform.tfvars` did not exist yet, so `discord_alert_user_id` was
empty. The plan applied in §7 was regenerated after the Product Owner
set the ID.

- **0 to add, 1 to change, 0 to destroy:** `aws_lambda_function.availability`
  only.
- Code: `CodeSha256` `NRgr…` → `AY3KE7zQPDNa0HvEUKUgmtVyxV37bEHmKShMXqLNpv4=`.
- Environment: the 13 existing variables identical by value, plus
  `DISCORD_ALERT_USER_ID` (empty, marked sensitive), 14 in total.
- No-op: IAM, both alarms, rules, targets and permissions, the HTTP probe
  rule, the table and the relay module (separate root, not planned).

## 7. Apply (stage `off`, 2026-11-28)

Approved by the Product Owner on 2026-11-28, after the Product Owner set
the user ID in `terraform.tfvars`. Production configuration, inspected by
the Operator. **The ID was never printed.** Every check below compared it
inside a script and printed only true or false. The snapshots record
only "present", "valid format" and "equals `terraform.tfvars`", not the
value or a hash of it.

**Before apply.**

- HEAD `a1c9d8b`, clean working tree.
- `terraform.tfvars` ignored by Git and not tracked. It sets only
  `discord_alert_user_id`, once, in a valid 17–20 digit format.
- AWS SSO role `AdministratorAccess`, `ap-northeast-1`, workspace
  `default`.
- Regenerated plan: 0 to add, 1 to change, 0 to destroy. Only
  `aws_lambda_function.availability` changes: code, environment and the
  computed `last_modified`.
- Artifact `AY3KE7zQPDNa0HvEUKUgmtVyxV37bEHmKShMXqLNpv4=` (5 files equal to
  the commit).
- Environment: the 13 existing variables identical by value, plus
  `DISCORD_ALERT_USER_ID`, equal to the `terraform.tfvars` value and
  sensitive in the plan.
- The text plan did not contain the ID.
- Both alarm rules `DISABLED`; both deliveries `disabled`.

**Apply.** The saved plan was applied at 16:13:15–16:13:28 JST: 0 added,
1 changed, 0 destroyed. The Lambda update took 11 s.

**After apply** (snapshots before and after; runtime checks at 16:19:59
JST).

| Check | Result |
|---|---|
| Lambda | `CodeSha256` `NRgr…` → `AY3KE7zQPDNa0HvEUKUgmtVyxV37bEHmKShMXqLNpv4=`; `Successful` / `Active`; runtime, 90 s timeout, 128 MB, role and handler unchanged |
| Environment | 14 variables. The 13 existing ones identical by value. `DISCORD_ALERT_USER_ID`: present, valid format, equal to `terraform.tfvars` (value not shown). Both deliveries `disabled` |
| Alarms | Both configurations identical. Burn rate `OK` since 2026-11-27 21:42 JST; continuous failure `OK` since 03:29:41 JST |
| Rules, targets, permissions | Identical. Both alarm rules `DISABLED`; HTTP probe rule `ENABLED` |
| IAM | Inline policy, policy names and attached policies identical |
| HTTP probe | `HTTPUp` = 1, one sample per minute, 16:11–16:18 JST (8 minutes across the apply) |
| Lambda metrics (16:14–16:19 JST) | 5 invocations, 0 errors, 0 throttles |
| Lambda log | 22 lines: 0 error or warning lines (so no mention warning), 0 alarm-notification lines. Max memory 95 MB of 128. Max duration 3.6 s. One cold start, init 262 ms |
| DynamoDB | HTTP row `up`, 0 failures, last slot 16:19 JST; no `burnrate#` or `contfail#` row |
| Readiness | HTTP 200 in 0.40 s, `ready`, release `0b72c3a` |
| Plan after apply | No changes |

**Evidence.** The evidence folder outside the repository holds:

- the before and after snapshots and the apply log, with the account ID
  masked;
- the plan summary, the post-apply output and the scripts;
- SHA-256 checksums.

A scan found the ID in none of these files. The JSON plan, which
contains the ID in plain text, was not kept and was deleted from the
working directory.

## 8. Device test (2026-11-28)

Test messages through the production path: synthetic test event →
Lambda → CloudFront relay → Discord `#gms-alerts`. Each was approved
separately and sent once, with a new test ID. No alarm state row,
`set-alarm-state` or EC2 was involved, and the AWS code and configuration
were not changed. Device results were confirmed by the Product Owner on
the iPhone.

| Test | Sent (JST) | Lambda and delivery | Discord | iPhone push | Result |
|---|---|---|---|---|---|
| LOW `priority-low-20261128T0727Z` | 16:27:12 | 200, `{"synthetic": true, "new": true}`; relay accepted; no warning | Arrived, no mention | None (expected) | **PASS** |
| HIGH `priority-high-20261128T0736Z` | 16:35:58 | 200, `"priority": "high"`; relay accepted; no mention warning | Arrived with the mention | **None** | **FAIL** |
| HIGH Retry 1 `priority-high-retry1-20261128T0800Z` | 16:59:57 invoked, 16:59:58 arrived | 200, `"priority": "high"`; relay accepted; no mention warning | Arrived with the mention | Lock screen, app badge and the in-app mention | **PASS** |

- **Lambda side, every test:**
  - each test wrote one dedup row `synthetic#<id>` and one `TestSignal`;
  - 0 Lambda errors and throttles;
  - no warning in the log, and no 17–20 digit number in it;
  - `HTTPUp` stayed 1;
  - no `burnrate#` or `contfail#` row;
  - both alarms `OK`, both rules `DISABLED`, both deliveries `disabled`.
- **Delivery delay:** almost immediate, but not measured.
- **First HIGH failure.** The exact cause is not established. Before
  Retry 1, the Product Owner set Discord's mobile push inactivity timeout
  to 1 minute, checked the server, channel and iOS settings, and waited
  before the send. Discord received the same payload both times, so the
  difference is on the Discord app or device side, not in AWS.
- **Evidence:** responses, invoke metadata, masked tail logs, check
  output and summaries are kept outside the repository with checksums.
  A scan found no user ID in them.

**Operating prerequisites outside Terraform.** HIGH pushes rely on these
Discord and iOS settings. Re-check them after a device change, an app
reinstall or any settings change, with a HIGH test message (Runbook
§15.4).

| Setting | Required |
|---|---|
| Discord server and channel notification setting | Only @mentions |
| Server and channel mute | Off |
| Discord mobile push notifications | On |
| Discord mobile push inactivity timeout | 1 minute |
| iOS: Discord notifications, lock screen, sound | Allowed / on |

**Known limitation.** A HIGH message can be delivered with a valid
mention and still produce no push or sound:

- an iOS Focus mode;
- Discord being active on a desktop (push is held during the inactivity
  timeout);
- other device notification controls.

AWS cannot detect this. The message is still in `#gms-alerts`.

## 9. Before `shadow` and `live`

- Keep `terraform.tfvars` with the ID on the Mac used for applies. The
  precondition refuses a `live` plan without it.
- Re-run the HIGH test message after any change to the prerequisites
  above.

## 10. Residual risks

- **Whether a ping reaches the person depends on Discord and the
  device.** This was seen in the first HIGH test (§8). The prerequisites
  in §8 must hold, and Focus modes, desktop activity or device controls
  can still suppress the push or sound.
- **A wrong but well-formed ID** pings someone else or nobody, without
  an error. The HIGH test message catches this.
- **Another checkout without `terraform.tfvars`** would plan the ID back
  to empty. The precondition stops this once a rollout is `live`;
  before that, it shows only in the plan.
- **Payload rejected by Discord.** Discord's own validation is not
  reproduced locally. If it rejected the HIGH payload, the send would
  fail and be retried, then be lost (no DLQ). The HIGH test message is
  the check.
- **Two HIGH messages per probe outage:** DATA INSUFFICIENT from 6/6
  (about 12 minutes) and from 9/60 (about 66 minutes). Both times are
  integration test observations.
- **Where the ID is stored:** it is in the local State, the Lambda
  configuration and JSON plans (`terraform show -json` prints sensitive
  values). None of them is public, but mask the ID in evidence.

## 11. Evidence handling

This document contains no Discord user ID, webhook path, relay token,
AWS account ID, credential or Terraform State. Tests use an ID built at
run time from repeated digits, and a test checks that no 17–20 digit
number appears in this module or in `docs/infrastructure/`.
