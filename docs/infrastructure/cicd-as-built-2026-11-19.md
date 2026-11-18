# GMS v5 — CI/CD and Automatic Rollback As-Built

Status: Production acceptance passed for deployment and recovery.
Evidence date: 2026-11-19 JST.

> **Reading note (added 2026-11-19 by the documentation synchronization):**
> the body below was written before the final push-triggered deployment
> and before Discord delivery was confirmed. It is preserved as recorded.
> Three statements in it are superseded by the
> [final acceptance addendum](#addendum--final-acceptance-2026-11-19-approximately-0202-jst):
> the last accepted release, the pending Discord confirmation, and the
> pending activation of the continuous deployment gate.
> Current-state summary: [Production State](production-state.md).
> Diagrams: [Architecture §10](../architecture.md#10-ci--build--deployment).

## Architecture

GitHub Pull Request
  -> CI Quality Gate
  -> Protected main branch
  -> Linux ARM64 build
  -> Artifact checksum and manifest verification
  -> GitHub OIDC / AWS STS
  -> Immutable S3 release publication
  -> Persistent S3 deployment lock
  -> SSM GMSv5-DeployRelease
  -> EC2 preflight and safe staging
  -> Atomic current symlink replacement
  -> systemd restart
  -> Release-aware readiness verification
  -> Commit or automatic rollback.

The existing IPv6-only ARM64 EC2 architecture is preserved.
S3 access uses the regional dualstack endpoint.
No static AWS credentials are stored in GitHub Actions.

## CI Quality Gate

- TypeScript type checking
- ESLint
- Application unit tests
- Deployment engine tests
- Availability monitoring tests
- Production build
- Standalone artifact validation

Verified test inventory:

| Suite | Tests |
|---|---:|
| Application | 125 |
| Deployment | 140 |
| Availability | 33 |
| Total | 298 |

## Release management

- Application: /opt/gmsv5/releases/<full-commit-sha>
- Active release: /opt/gmsv5/current
- Deployment engine: /opt/gmsv5/deploy
- Configuration: /etc/gmsv5-deploy.json
- Transaction state: /var/lib/gmsv5-deploy/state.json
- Transaction history: /var/lib/gmsv5-deploy/history
- Database: /mnt/gms-data/gmsv5.sqlite
- Distributed lock: deployment-control/lock.json

Application releases are root-owned and immutable to the application user.
Directory permissions are normalized independently of the caller's umask.

The S3 release manifest includes the commit SHA, artifact SHA256,
architecture, operating system, Node.js version, schema fingerprint,
and GitHub Actions build provenance.

Conditional S3 writes prevent the deployment role from overwriting
published release objects.

## Health checks

- GET /api/health/live
- GET /api/health/ready

Readiness validates the expected build SHA, database accessibility,
required tables and migration compatibility.

Deployment acceptance requires three consecutive successful probes.

## Automatic rollback

The previous release remains available during deployment.

Transaction phases:

PREPARING -> VERIFIED -> SWITCHING -> VALIDATING -> SUCCEEDED

Failure after switching:

ROLLING_BACK -> ROLLED_BACK or ROLLBACK_FAILED

The engine persists transaction intent before replacing the symlink.
Rollback restores the previous release and restarts the application.
Rollback is successful only after the previous release passes readiness.

## Boot recovery

- gmsv5-boot-prepare.service
- gmsv5-boot-verify.service
- Application startup guard

Incomplete transactions are inspected before application startup.
The recovery process restores the previous release when necessary
and verifies its readiness after startup.

## Deployment observability

SSM invocation status events are processed through:

EventBridge -> Lambda -> DynamoDB / CloudWatch / Discord relay.

CloudWatch namespace: GMS/Availability
Metric: DeploymentEvents
Dimension: Target

Actual started and succeeded events were verified in DynamoDB.
The SUCCEEDED CloudWatch metric was verified.

Discord relay delivery must be confirmed in the receiving channel.
Deployment metrics are available for Grafana CloudWatch queries.

## Production acceptance evidence

| Test | Evidence | Result |
|---|---|---|
| CI Quality Gate | GitHub run 44347795775 | PASS |
| AWS OIDC authentication | GitHub run 44342032601 | PASS |
| S3 publication | GitHub run 44343784975 | PASS |
| Production artifact download | EC2 SSM execution | PASS |
| SQLite online backup | quick_check | PASS |
| Runtime acceptance | Isolated Linux ARM64 service | PASS |
| Baseline cutover | 2026-11-19 01:26 JST | PASS |
| Automated deployment | GitHub run 44348885925 | PASS |
| Persistent S3 lock | Actual acquire and conditional release | PASS |
| Automatic rollback | 2026-11-19 01:50 JST | PASS |
| Boot recovery | 2026-11-19 01:53 JST | PASS |
| Public HTTPS | HTTP 200 after recovery | PASS |

Initial managed baseline:

f48a3e505ce605a70f29ff6e8925483130f05f78

Last accepted application release:

d50dafb1185122990e0f272cd84933d6e690bec7

## Measured recovery durations

- Baseline cutover procedure: 12 seconds
- Automatic rollback state transition: approximately 11.4 seconds
- Complete rollback acceptance exercise: 46 seconds
- Boot prepare to verified recovery: approximately 14 seconds

These durations are procedure measurements, not externally
measured downtime or guaranteed recovery objectives.

## Operational boundaries

- Single-instance architecture; deployments are not zero-downtime.
- Automatic rollback restores application code, not database contents.
- Incompatible migration fingerprints block deployment.
- Deployment engine upgrades remain operator-controlled.
- Unknown deployment outcomes retain the distributed lock.
- Spot interruption recovery depends on retained storage and transaction state.
- The boot test used an actual OS reboot with a simulated interrupted
  transaction, not an actual AWS capacity reclamation event.
- The rollback test used a controlled readiness failure.
- Discord handoff is best-effort and not an exactly-once delivery guarantee.
- Backup retention and release garbage collection are operator responsibilities.
- Existing GitHub Actions Node.js 20 runtime warnings remain non-blocking.
- Continuous deployment gate activation is the final rollout step.

See [CI/CD Operations Runbook](cicd-operations-runbook.md).

---

## Addendum — Final acceptance (2026-11-19, approximately 02:02 JST)

This addendum records what happened after the body above was written.
Nothing above was removed. Where the two disagree, this addendum is the
later record and takes precedence.

Evidence classes used here:

- **GitHub (re-read):** run conclusions, triggering events, head commits
  and job timestamps were read back from GitHub on 2026-11-19.
- **Operator evidence:** SSM command results, DynamoDB records,
  CloudWatch metrics, Discord messages and public HTTPS checks were
  supplied by the operator at handoff. AWS, the EC2 host and Discord
  were not queried again while this addendum was written.

### Statements in the body that are superseded

| Body statement | Final state |
|---|---|
| Last accepted application release `d50dafb…` | `53ece4d8809cdf439cf9c182ce1f41c0b017e3af`, deployed by a push to main |
| "Discord relay delivery must be confirmed in the receiving channel." | Confirmed. Started and succeeded notifications were received for two production deployments, including the final one |
| "Continuous deployment gate activation is the final rollout step." | Done. `GMSV5_CD_ENABLED` is `true` (re-read from GitHub on 2026-11-19) |

### Pull requests

| PR | Content | Merge commit |
|---|---|---|
| #4 | CI/CD and automatic rollback implementation | `f850d94` |
| #5 | Artifact verification invocation fix | `f48a3e5` |
| #6 | Release directory permission fix | `d50dafb` |
| #7 | Production acceptance documentation and runbook | `53ece4d` |

All four are merged into main. Main requires a pull request and the
`Quality Gate` status check, and the rule applies to administrators
(re-read from GitHub on 2026-11-19).

### Production release history

| Release | Description |
|---|---|
| `364d0b7ebd4868f6d54b271ce9144b001bd380ca` | Previous release, deployed manually (observability release, 2026-11-14) |
| `f48a3e505ce605a70f29ff6e8925483130f05f78` | Initial managed production baseline (operator cutover through SSM) |
| `d50dafb1185122990e0f272cd84933d6e690bec7` | First successful GitHub Actions deployment (started with `workflow_dispatch`) |
| `53ece4d8809cdf439cf9c182ce1f41c0b017e3af` | Final deployment, triggered by the push that merged PR #7 |

### GitHub Actions evidence

| Run ID | Workflow / trigger | Head commit | Verification |
|---|---|---|---|
| `44342032601` | Verify AWS OIDC / `workflow_dispatch` | `f850d94` | Actual GitHub OIDC authentication |
| `44343784975` | Continuous Deployment / `workflow_dispatch` | `f48a3e5` | Immutable S3 artifact publication |
| `44347795775` | CI Quality Gate / `pull_request` | `bb252be` | Quality Gate after the permission regression fix |
| `44348885925` | Continuous Deployment / `workflow_dispatch` | `d50dafb` | First automated production deployment |
| `44353371110` | Continuous Deployment / `push` | `53ece4d` | Final push-triggered automatic production deployment |

All five runs concluded with `success`.

### SSM production acceptance evidence

Operator evidence. Command IDs identify the invocation; their output is
not reproduced here.

| Command ID | Verification |
|---|---|
| `95c6e3cd-b7d5-4e30-821b-945ab6d481a5` | Production bootstrap staging |
| `220482cc-b7dd-4cf4-800e-670f09df9f0a` | SQLite online backup |
| `dffac4bb-17be-4246-bad1-21730f702b05` | Isolated production runtime acceptance |
| `a251331b-e3de-4c22-a30b-efad7b3c1a0f` | Deployment engine installation |
| `c7a88c91-ddfd-47d6-a35f-f7a5b7293e28` | Initial production baseline cutover |
| `89b28ae2-b175-4ba3-afb4-7e17bacbf601` | Deployment engine permission fix |
| `b1166d13-551f-4222-9703-d713361d2530` | First automated production deployment |
| `37ec19a5-1838-42fb-b787-2219f24a08c5` | Controlled automatic rollback acceptance |
| `916c204f-614c-482e-b892-ca285262b776` | Interrupted transaction and OS reboot preparation |
| `d0029e44-3b8c-44d0-990a-d8582d919a7f` | Actual boot recovery verification |
| `b59f8941-6d4f-4813-b454-24beb8cf628d` | Final fully automatic push-triggered deployment |

### Final deployment

GitHub Actions run `44353371110` started when PR #7 was merged into main.

| Item | Result |
|---|---|
| Release | `53ece4d8809cdf439cf9c182ce1f41c0b017e3af` |
| Artifact SHA256 | `1b77cf3f6c480c260fa25db6ae79a985d564002d85c458c86da549ae2f2ca672` |
| SSM outcome | `SUCCEEDED` |
| Distributed lock | Acquired and released |
| Public HTTPS | HTTP 200 |
| Discord | `IN_PROGRESS` and `SUCCEEDED` received |
| Automatic CD gate | Enabled |
| Verified completion | 2026-11-19, approximately 02:02 JST |

### Acceptance results after the addendum

| Test | Result |
|---|---|
| Production artifact download and staging | PASS |
| SQLite online backup and `quick_check` | PASS |
| Actual production runtime compatibility | PASS |
| Initial baseline cutover | PASS |
| Automatic production deployment | PASS |
| Persistent S3 lock acquisition and conditional release | PASS |
| Controlled automatic rollback on the production EC2 | PASS |
| Boot recovery after an actual OS reboot | PASS |
| DynamoDB deployment event records | PASS |
| CloudWatch deployment metrics | PASS |
| Discord started and succeeded notification delivery | PASS |
| Public HTTPS after deployment and recovery | HTTP 200 |
| Push-triggered deployment from main | PASS |
| Automatic CD enabled | Confirmed `true` |

### Additional timing evidence

| Measurement | Result |
|---|---:|
| Final ARM64 build job | 71 seconds |
| Final publish and deploy job | 69 seconds |

Both come from the job timestamps of run `44353371110`. Like the
durations in the body, they are measured process durations. They are not
externally measured service downtime, a statistically established RTO,
or a guaranteed SLO.

### Engineering findings corrected during acceptance

Both findings were fixed before the final deployment. They are recorded
as engineering findings, not as open production failures.

1. **Artifact verification invocation (PR #5).** The first CD workflow
   passed a string to a Python function that expects a `pathlib.Path`.
   The workflow now calls the existing artifact verification CLI
   (`artifact.py verify`), which performs the conversion.
2. **Restrictive umask (PR #6).** During the isolated production runtime
   acceptance, `umask 077` left extracted nested directories at mode
   700, so the non-root application user could not read the Next.js
   package configuration. The extractor now normalizes every release
   directory to 755 independently of the caller's umask, with regression
   tests. The corrected engine was installed on the production host
   without restarting the application.

### Boundaries added or made explicit by the final acceptance

These add to "Operational boundaries" above.

- Actual Discord delivery was verified for successful deployments
  (started and succeeded). Failure outcomes (`FAILED_BEFORE_SWITCH`,
  `ROLLED_BACK`, `ROLLBACK_FAILED`, `RECOVERY_REQUIRED`,
  `OUTCOME_UNKNOWN`) are covered by unit tests; their delivery to
  Discord from a real production failure was not observed.
- No deployment-specific Grafana panel was added or verified.
  `DeploymentEvents` is available in CloudWatch and can be queried
  through the existing Grafana CloudWatch datasource.
- Authenticated end-to-end application behavior and translation were
  not revalidated as part of this acceptance.
- The initial SQLite backup taken at bootstrap is not a separate
  disaster recovery backup.
- There is no automatic release garbage collection.
- Automatic instance replacement and ENI/EBS reconstruction are a
  different capability from application-level boot recovery, and are
  not implemented.
- Four ESLint warnings remain (unused identifiers in the two CloudFront
  Function sources under `infra/terraform/`). ESLint reports no errors.
- The 298 tests are the combined Quality Gate inventory. The CI Quality
  Gate runs all three suites. The release build that the CD workflow
  reuses runs the application and deployment engine suites, not the
  availability monitoring suite.
- A documentation-only merge into main also starts a real production
  deployment, because the CD workflow has no path filter.
