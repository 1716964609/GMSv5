# GMS v5 — CI/CD Operations Runbook

- **Owner:** Infrastructure / SRE
- **Updated:** 2026-11-19 JST
- **Status:** Automatic production deployment is enabled
  (`GMSV5_CD_ENABLED=true`).
- **Evidence:** [CI/CD As-Built 2026-11-19](cicd-as-built-2026-11-19.md)
- **Design and diagrams:** [Architecture §10](../architecture.md#10-ci--build--deployment)
- **Current state:** [Production State](production-state.md)

> Every merge into main starts a real production deployment while the
> gate is enabled. This includes documentation-only merges: the CD
> workflow has no path filter. Disable the gate first if a merge must
> not deploy.

## Normal deployment

A pull request must pass CI Quality Gate before merging into main.

When GMSV5_CD_ENABLED is true, main pushes trigger production
deployment using the protected production environment.

The workflow builds, publishes and deploys the exact commit.
Verify the deployment job, not only the overall workflow conclusion.

Expected terminal result:

- SSM_STATUS=Success OUTCOME=SUCCEEDED
- DEPLOYMENT_LOCK_RELEASED
- Public HTTPS: HTTP 200

If the requested commit is already the active release, the engine logs
ALREADY_DEPLOYED and reports SUCCEEDED without switching or restarting.

A release whose database schema fingerprint differs from the active
release is rejected before switching (outcome 10). Such a release needs
an operator-planned deployment; the automatic path does not run
database migrations.

## Deployment modes

| Mode | Behavior |
|---|---|
| build | Build and verify without publishing |
| publish | Build and publish without deploying |
| deploy | Build, publish and deploy |
| deploy-published | Verify and deploy an existing published artifact |

Do not rebuild and overwrite an existing immutable release.
Use deploy-published when an already published release must be retried.

The workflow only accepts the current main commit for production.

## Disable automatic deployment

    gh variable set GMSV5_CD_ENABLED --body false

This prevents new gated deployment jobs from starting.
It does not cancel or undo an already running deployment.

Do not merge new changes while investigating an unresolved deployment.

## Inspect recent deployments

    gh run list --workflow cd.yml --branch main --limit 5

Inspect the production job, deployment-result artifact and corresponding
SSM command invocation.

## Inspect EC2

Using an authorized administrative session:

    systemctl status gmsv5.service --no-pager
    readlink /opt/gmsv5/current
    python3 -B /opt/gmsv5/deploy/state.py show /var/lib/gmsv5-deploy
    curl -fsS http://127.0.0.1:3000/api/health/ready

Verify that the readiness release matches the current release symlink.

Do not print the application environment file, database contents,
authentication tokens or other sensitive settings.

## Deployment outcomes

| Code | Outcome | Operator action |
|---|---|---|
| 0 | SUCCEEDED | Verify active release and lock release |
| 10 | FAILED_BEFORE_SWITCH | Inspect preflight and compatibility |
| 20 | ROLLED_BACK | Confirm previous release readiness |
| 21 | ROLLBACK_FAILED | Disable CD and investigate |
| 22 | RECOVERY_REQUIRED or unknown | Disable CD and reconcile |

Unexpected codes, timeout or cancellation must be treated as unknown.
Do not automatically resend an unconfirmed SSM command.

## Persistent deployment lock

Lock key: deployment-control/lock.json

A retained lock prevents overlapping deployments.

Before manual lock removal:

1. Disable automatic CD.
2. Confirm that no production workflow or SSM command is active.
3. Inspect the distributed lock request ID and corresponding SSM command.
4. Inspect the EC2 transaction state, current release and readiness.
5. Recover unfinished transactions if necessary.
6. Verify that the system is in a consistent terminal state.
7. Remove the lock only using the ETag of the inspected lock object
   as the conditional DeleteObject If-Match value.

Never force-delete an unknown or active lock.

## Boot recovery investigation

    systemctl status gmsv5-boot-prepare.service --no-pager
    systemctl status gmsv5-boot-verify.service --no-pager
    journalctl -b -u gmsv5-boot-prepare.service \
      -u gmsv5-boot-verify.service --no-pager

Confirm the expected data filesystem is mounted and accessible.

For an unresolved deployment on a running system, after confirming
there is no active deployment and boot-prepare is active, the operator
may invoke the locked recovery procedure:

    sudo bash /opt/gmsv5/deploy/transaction.sh \
      recover /opt/gmsv5 /var/lib/gmsv5-deploy \
      /opt/gmsv5/deploy/systemd-adapter.sh

Verify readiness and transaction state after recovery.

If boot-prepare is not active, investigate the boot unit failure first.
Do not bypass the startup guard or manually rewrite transaction JSON.

## Database protection

Database files reside on the persistent data EBS volume.
Application rollback does not reverse database migrations.

Use the SQLite online backup API for consistent backups.
Run quick_check or integrity_check on the resulting backup.

The initial bootstrap backup is root-only on the EC2 root filesystem.
It is not a disaster recovery backup and may contain sensitive data.

Do not automatically restore an older database over live data.

## Monitoring

- Inspect GitHub Actions and SSM results.
- Inspect deployment records in DynamoDB.
- Inspect DeploymentEvents under GMS/Availability in CloudWatch.
- Verify Discord delivery separately.
- Filter CloudWatch metrics by Target outcome in Grafana.
- Distinguish deployment failures from EC2 Spot availability events.

Deployment events share the Lambda function, DynamoDB table and
CloudWatch namespace of the external availability monitor, but use
their own record key (`deployment#<instance>`) and their own metric
(`DeploymentEvents`, with the outcome in the `Target` dimension). See
[External Availability Runbook](availability-external-runbook.md) for
the shared components.

Verified in production (2026-11-19): started and succeeded events in
DynamoDB and CloudWatch, and started and succeeded Discord messages for
two deployments. Not verified in production: Discord delivery for the
failure outcomes, which have unit-test coverage only.

No deployment panel exists in Grafana yet. Query `DeploymentEvents`
through the existing CloudWatch datasource until one is added and
verified.

## Engine maintenance

Deployment engine files are root-owned.
Application deployment does not automatically update the engine.

Engine changes require a reviewed commit, verified checksums,
exclusive host lock and atomic installation.

Preserve a healthy baseline and compatible previous release.

## Known limitations

- No zero-downtime deployment guarantee.
- No automatic database rollback.
- No automatic release garbage collection.
- Unknown outcomes require operator reconciliation.
- Notification delivery is best-effort.
- Actual AWS Spot reclamation and full authenticated application
  functionality are outside the controlled recovery acceptance test.
- Single-instance production architecture.
- A changed database migration fingerprint blocks automatic deployment.
- Deployment engine installation and updates are operator-controlled.
- Boot recovery restores the application release on the same instance.
  It does not replace an instance or rebuild its ENI or EBS volumes.
- The rollback acceptance injected a controlled readiness failure. The
  boot recovery acceptance used a real OS reboot with a simulated
  interrupted transaction.
- The measured durations in the As-Built are process durations, not
  service downtime, an RTO or an SLO.
