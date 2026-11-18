# GMS v5 — CI/CD and Automatic Rollback As-Built

Status: Production acceptance passed for deployment and recovery.
Evidence date: 2026-11-19 JST.

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
