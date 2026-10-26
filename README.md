# GMS v5 — Glossary Management System

GMS v5 is a multilingual glossary management and AI-assisted translation web application. Users manage their own terminology, select terms for a translation request, and use a two-stage DeepL → OpenAI translation flow with their own API keys.

This repository follows Spec-Driven Development and Simon Martinelli's AI Unified Process (AIUP). The production documentation baseline below reflects the verified production configuration as of 2026-10-26 JST.

## Production Status

- Public URL: <https://sunlightjetrans.com>
- Current application release: `51cba4a`
- Initial production release: `f5fb681` on 2026-10-25
- Runtime: Amazon Linux 2023 / ARM64 / Node.js 26 / Next.js Standalone
- Process: `gmsv5.service`
- Database: SQLite on an independent encrypted EBS volume

The DeepL IPv4 reachability issue was addressed in release `51cba4a` by adding a dedicated CloudFront Reverse Proxy. OpenAI continues to use the EC2 native IPv6 path.

## Core Features

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

## Documentation

| Document | Description |
|---|---|
| [Product Vision](docs/vision.md) | Product goals and scope |
| [Requirements](docs/requirements.md) | Functional and non-functional requirements |
| [Entity Model](docs/entity-model.md) | Domain entities and business constraints |
| [Use Cases](docs/use-cases.md) | System behavior and acceptance criteria |
| [Use Case Diagram](docs/use-case-diagram.puml) | PlantUML use case model |
| [Production Architecture](docs/architecture.md) | Current production architecture |
| [Production As-Built 2026-10-26](docs/infrastructure/production-as-built-2026-10-26.md) | Current production implementation record |

## Explicit Boundaries

The following are not represented as implemented production capabilities:

- Prometheus/Grafana production monitoring
- Fully automatic CD from Git push to EC2
- Automatic Spot replacement and ENI/EBS recovery
- Resolution of the large-glossary bulk loading problem
- Unverified behavior such as zero-term translation, selecting 10,000 terms, or confirmed temporary glossary cleanup

The product specifications in `docs/vision.md`, `docs/requirements.md`, `docs/entity-model.md`, `docs/use-cases.md`, and `docs/use-case-diagram.puml` remain unchanged by this documentation refresh.
