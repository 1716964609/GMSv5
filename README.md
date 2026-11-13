# GMS v5 — Glossary Management System

**English** | [日本語](README.ja.md)

GMS v5 is a multilingual glossary management and AI-assisted translation web application. Users manage their own terminology, select terms for a translation request, and use a two-stage DeepL → OpenAI translation flow with their own API keys.

This repository follows Spec-Driven Development and Simon Martinelli's AI Unified Process (AIUP). The status below reflects the verified state as of 2026-11-14 JST. It distinguishes measured evidence, operator reports, and unverified items; the evidence is indexed in [Production State](docs/infrastructure/production-state.md). Infrastructure descriptions inherited from the October record have not been re-audited against AWS.

To find which document is the source of truth for a given question, and who owns it, start with the [Documentation Map](docs/README.md).

## Production Status

- Public URL: <https://sunlightjetrans.com>
- Current application release: `364d0b7ebd4868f6d54b271ce9144b001bd380ca` (verified in production 2026-11-14)
- [Build ARM64 Release #11 / Run 43435258778](https://github.com/1716964609/GMSv5/actions/runs/43435258778): Success; head SHA matches the release; artifact SHA256 verified per operator report
- Previous release: `9783463dcdd3f3b10f743ab3e1488978b842b4be` (2026-11-08, retained for rollback), built by [Release #10 / Run 42481781676](https://github.com/1716964609/GMSv5/actions/runs/42481781676)
- Earlier: `5f0c6e7`; Lavender LP commit: `de30d5a`
- Observability: server traces and metrics enabled and verified in production; Browser RUM is implemented but disabled
- Initial production release: `f5fb681` on 2026-10-25
- Runtime: Amazon Linux 2023 / ARM64 / Node.js 26 / Next.js Standalone
- Process: `gmsv5.service`
- Database: SQLite on an independent encrypted EBS volume

The DeepL IPv4 reachability issue was addressed in release `51cba4a` by adding a dedicated CloudFront Reverse Proxy. OpenAI continues to use the EC2 native IPv6 path.

The Product Owner confirmed the new Lavender LP and successful selection of 10,000 Terms in production. Select All was observed at approximately 27 seconds versus 40 seconds previously (about 32.5% reduction). This is not a controlled p95 comparison; the provisional ≤20-second / ≥50% improvement targets are not met by these observations and formal performance acceptance remains pending. Production migration completion logs and the final systemd state remain unverified. See the [2026-11-08 As-Built](docs/infrastructure/production-as-built-2026-11-08.md).

Release `364d0b7` adds OpenTelemetry instrumentation to the application; it contains no schema change. The application sends server traces and metrics over authenticated OTLP/HTTPS to a separate observability EC2 instance running an OTLP gateway, OpenTelemetry Collector, Tempo, Prometheus and Grafana. In the [Phase 1 acceptance record](docs/infrastructure/observability-as-built-2026-11-13.md#12-production-phase-1-acceptance), a production translation request was observed at about 6.87 seconds, with DeepL at about 3.22 seconds and OpenAI post-editing at about 3.64 seconds. That is a single observation, not a baseline or an SLO. Centralized logs, edge monitoring, alerting, SLOs and automated recovery are not complete, and the observability host is only partly reproducible from Terraform.

## Core Features

- Purple/Lavender public landing page with existing registration and login entry points
- Lazy Term pages and revision-aware bounded parallel Select All (default 2, operation/tab maximum 4), with atomic selection and a 10,000 unique Term ID limit
- Cognito-based registration, email verification, login, logout, and password recovery
- User-owned Glossary and Term CRUD
- Strict JSON glossary import with transactional All-or-Nothing behavior
- Owner-scoped terminology search and pagination
- Translation-time terminology conflict resolution without changing persistent Terms
- DeepL primary translation followed by OpenAI post-editing
- Encrypted Bring Your Own API Keys (BYOK)
- TXT export of the final translation
- Account deletion with immediate API-key deletion and 30-calendar-day internal retention

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 / React / TypeScript |
| UI | Tailwind CSS |
| Backend | Next.js App Router / Route Handlers |
| Runtime | Node.js 26 / Linux ARM64 |
| Architecture | Modular Monolith |
| Database | SQLite / independent encrypted EBS gp3 |
| Authentication | Amazon Cognito Lite / Classic Hosted UI |
| Public ingress | DreamHost DNS / CloudFront / Nginx |
| DeepL egress | Dedicated CloudFront Reverse Proxy |
| OpenAI egress | Native IPv6 from EC2 |
| IaC | Terraform and documented manual operations |
| Build | GitHub Actions ARM64 runner |
| Release transfer | S3 Dualstack / AWS Systems Manager |
| Process manager | systemd |
| Telemetry | OpenTelemetry SDK (server traces and metrics) |
| Observability | Separate EC2: OTLP gateway, OpenTelemetry Collector, Tempo, Prometheus, Grafana (Phase 1) |

For an interview-oriented walkthrough in Japanese, see the [production system diagram](README.ja.md#diagram-a--production-system-architecture), [application flow](README.ja.md#アプリケーション内部の処理), and [build and release diagram](README.ja.md#diagram-b--build--release-flow).

## Production Infrastructure Summary

### Web ingress

```text
Browser
  -> DreamHost DNS: sunlightjetrans.com
  -> Existing CloudFront: E1EF0DWDYA2XH8
  -> HTTPS / IPv6: origin-v5.sunlightjetrans.com
  -> EC2 Nginx: 443
  -> Next.js: 127.0.0.1:3000
  -> SQLite: /mnt/gms-data/gmsv5.sqlite on independent EBS
```

The existing public CloudFront distribution uses ACM for viewer TLS and connects to the EC2 origin over HTTPS/IPv6. Nginx uses the Let's Encrypt origin certificate. The EC2 instance has no public IPv4.

### DeepL-only egress

```text
Next.js / EC2
  -> HTTPS / IPv6
  -> Dedicated CloudFront: E3L3D4LI8CKI2M
     d7xjhnc06nb75.cloudfront.net
  -> HTTPS
  -> api-free.deepl.com
```

The dedicated distribution uses a CloudFront Functions runtime 2.0 function and CloudFront KeyValueStore. The function authenticates `x-gms-proxy-token`, removes that proxy token before forwarding, and allows only:

| Method | Path |
|---|---|
| POST | `/v2/translate` |
| POST | `/v2/glossaries` |
| DELETE | `/v2/glossaries/{id}` |

Caching is disabled and HTTPS is forced. The proxy token is not the user's DeepL API key. The user's key remains the DeepL `Authorization: DeepL-Auth-Key ...` credential.

### OpenAI egress

OpenAI is called directly over the EC2 native IPv6 path. It does not use the DeepL CloudFront proxy.

### Secrets and IAM

- systemd service: `gmsv5.service`
- environment file: `/etc/gmsv5/gmsv5.env`
- environment file permissions: `root:root 600`
- DeepL proxy token: SSM Parameter Store SecureString
- Parameter: `/gmsv5/deepl/proxy-token`
- access: EC2 IAM role with parameter read permission
- SSM: IPv6-capable endpoint

Secret values, API keys, Cognito client secrets, and encryption keys are never stored in this repository or its documentation.

## Build and Deployment

The ARM64 workflow is manually started with GitHub Actions `workflow_dispatch`. It runs `npm ci`, TypeScript checking, ESLint, and the Next.js production build, then creates a standalone tarball and SHA-256 checksum.

The production deployment includes manual operations:

1. Download the ARM64 artifact.
2. Verify its SHA-256 checksum.
3. Upload it to the versioned S3 release path.
4. Use AWS Systems Manager to download from the S3 Dualstack endpoint.
5. Verify the checksum again on EC2.
6. Extract under `/opt/gmsv5/releases/<commit>`.
7. Switch the systemd release and run readiness/HTTP health checks.

The first `51cba4a` cutover rolled back because the initial readiness check ran too early. Readiness Retry was then introduced and the deployment succeeded. This is part of the current manual procedure; it is not a claim of fully automatic CD.

## Local Development

```bash
npm install
cp .env.example .env.local
npm run dev
```

Static checks and tests:

```bash
npm run typecheck
npm run lint
npm run test:db
npm run test:usecases
npm run test:browserless
npm run test:observability
npm run test:e2e
```

Production build:

```bash
npm run build
npm start
```

Local migration:

```bash
npm run db:migrate
```

The local default database is `.data/gmsv5.sqlite`. Production must set `GMS_DATABASE_PATH` to an absolute path under the independently mounted data volume.

For local direct DeepL access, use `DEEPL_API_URL=https://api-free.deepl.com/v2/translate`. Production uses the dedicated CloudFront endpoint and requires the server-side `DEEPL_PROXY_TOKEN`; never expose it through a `NEXT_PUBLIC_*` variable.

Do not call paid external APIs from local tests unless explicitly intended. The automated provider tests use local mocks.

## Repository Structure and Ownership

This is a monorepo. Development and Infrastructure are separated by path so that Infrastructure can later move to its own repository.

| Area | Code | Documentation | Owner |
|---|---|---|---|
| Development | `src/`, `test/`, `e2e/`, `scripts/`, build configuration | [docs/development/](docs/development/README.md), specifications and `docs/design/` | Development |
| Infrastructure | `infra/` | [docs/infrastructure/](docs/infrastructure/README.md) | Infrastructure / SRE |

A change to application code does not touch `infra/`, and an infrastructure change does not touch `src/`, `test/` or `e2e/`. The two areas meet only at the release artifact, the environment variable names in `.env.example`, and the telemetry schema. The full boundary is in the [Documentation Map](docs/README.md).

## Documentation

| Document | Description |
|---|---|
| [Documentation Map](docs/README.md) | Source of truth per question, document precedence, ownership, reading order for AI agents |
| [Product Vision](docs/vision.md) | Product goals and scope |
| [Requirements](docs/requirements.md) | Functional and non-functional requirements |
| [Entity Model](docs/entity-model.md) | Domain entities and business constraints |
| [Use Cases](docs/use-cases.md) | System behavior and acceptance criteria |
| [Use Case Diagram](docs/use-case-diagram.puml) | PlantUML use case model |
| [Production Architecture](docs/architecture.md) | Current production architecture (As-Is) |
| [Production State](docs/infrastructure/production-state.md) | Current production state: measured results, Terraform scope, manual configuration, unimplemented items |
| [Development documentation](docs/development/README.md) | Application scope, design records, application observability |
| [Telemetry Schema v1](docs/development/observability/application-instrumentation.md) | Spans, metrics, attributes, configuration and redaction |
| [Infrastructure documentation](docs/infrastructure/README.md) | Infrastructure scope, evidence rules, As-Built index |
| [Observability As-Built 2026-11-13](docs/infrastructure/observability-as-built-2026-11-13.md) | Observability platform evidence; §12 is the 2026-11-14 production acceptance |
| [Production As-Built 2026-11-08](docs/infrastructure/production-as-built-2026-11-08.md) | Historical: Baseline 1.2 release `9783463` |
| [Production As-Built 2026-10-26](docs/infrastructure/production-as-built-2026-10-26.md) | Historical: initial build; still the latest evidence for ingress, TLS and runtime |
| [Architecture As-To-Be — Add Observability](docs/architecture-as-to-be-add-observability.md) | Target observability architecture, including items not yet implemented |

## Explicit Boundaries

The following are not represented as implemented production capabilities:

- Browser RUM in production
- Centralized logs, edge monitoring (CloudFront, Nginx, DNS) and production host metrics
- Alerting, SLOs and automated recovery of either EC2 instance
- Full Terraform reproducibility of the observability host (containers, configuration and certificates were set up manually)
- Fully automatic CD from Git push to EC2
- Automatic Spot replacement and ENI/EBS recovery
- Formal 1/2/4 concurrency performance comparison and achievement of the provisional p95 targets
- Comprehensive production E2E, zero-term translation and confirmed temporary glossary cleanup for this release
- Execution-log verification of production migration completion, final systemd state and rollback asset availability

The documentation refreshes record release status without changing functional acceptance criteria. Baseline 1.1 approval/release history and the earlier As-Built records are preserved. The 2026-11-14 refresh changed documentation only: no application code, Terraform, AWS, systemd, secret, database, build or deployment operation was performed.
