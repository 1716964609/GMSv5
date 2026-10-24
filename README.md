# GMS v5 — Glossary Management System

GMS v5 is a multilingual glossary management and AI-assisted translation web application, developed using **Spec-Driven Development (SDD)** and the **AI Unified Process (AIUP)**.

The application enables users to manage their own terminology collections and perform two-stage translations using DeepL and OpenAI with user-provided API keys.

## Project Status

**Work in Progress — AIUP Step 8 Use Case Implementation**

The requirements, entity model, and use cases have been defined. The initial software architecture has also been drafted.

The Next.js project scaffolding, SQLite migration, domain services, Route Handlers, and initial UI for the defined Use Cases are now in place. Cognito, DeepL, and OpenAI require environment configuration and remain externally unverified.

## Core Features

- User registration, authentication, and account management
- Multilingual glossary and terminology CRUD
- JSON glossary import with transactional validation
- Terminology search and selection
- Translation-time resolution of conflicting terminology
- DeepL translation followed by OpenAI post-editing
- Bring Your Own API Key (BYOK)
- TXT translation export

## Technology Stack

The following stack is planned for the initial implementation.

| Layer | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript |
| UI | Tailwind CSS, shadcn/ui |
| Backend | Next.js Route Handlers |
| Database | SQLite |
| Authentication | Amazon Cognito |
| Infrastructure | AWS EC2, EBS, CloudFront, Nginx |
| Infrastructure as Code | Terraform |
| CI/CD | GitHub Actions, OIDC, S3, AWS Systems Manager |
| Observability | Prometheus, Grafana |

**Architecture:** Modular Monolith

The application is designed for low-cost deployment on an AWS EC2 instance, with database storage maintained on a separate EBS volume.

## Development Methodology

GMS v5 follows the **AI Unified Process**, based on Simon Martinelli's *Spec-Driven Development: From Specs to Code with AI Agents* (2026).

The development workflow connects requirements, domain entities, system use cases, implementation, and automated tests.

Specifications serve as the authoritative reference for AI-assisted development.

## Project Documentation

| Document | Description |
|---|---|
| [Product Vision](docs/vision.md) | Product goals and scope |
| [Requirements](docs/requirements.md) | Functional and non-functional requirements |
| [Entity Model](docs/entity-model.md) | Domain entities and business constraints |
| [Use Cases](docs/use-cases.md) | System behavior and acceptance criteria |
| [Use Case Diagram](docs/use-case-diagram.puml) | PlantUML representation of system use cases |
| [Architecture](docs/architecture.md) | Proposed technical architecture |

## Development Roadmap

Specification → Scaffolding → Database Migration → Use Case Implementation → Automated Testing → Deployment

The project is currently transitioning from specification to implementation.

## Local Development

```bash
npm install
npm run dev
```

For static checks and local tests:

```bash
npm run typecheck
npm run lint
npm run test:db
npm run test:usecases
```

For a production build:

```bash
npm run build
npm start
```

To apply the local SQLite migration:

```bash
npm run db:migrate
npm run test:db
```

Development defaults to `.data/gmsv5.sqlite`. In production, set
`GMS_DATABASE_PATH` to an absolute path under the independently mounted data
volume; the migration command does not use a fallback path in production.

Copy `.env.example` to the deployment environment and configure the Cognito
Hosted UI, session secret, credential encryption key, and provider endpoints.
Do not commit secrets or call paid external APIs from local tests.

---

**GMS v5 is an independent software engineering project exploring specification-driven, AI-assisted development and practical cloud infrastructure engineering.**
