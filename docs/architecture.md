# GMS v5 — Software Architecture Draft v1.0

**開発方法論：** AI Unified Process（AIUP）  
**状態：** Architecture Draft — 実装前レビュー用  
**対象：** GMS v5 MVP  
**基本方針：** Low-cost, Single-Instance Modular Monolith

## 1. Architecture Principles

GMS v5は、ユーザー所有の多言語用語集と、DeepL・OpenAIを使用した2段階翻訳を提供するWeb SaaSである。

設計原則：

- AWS EC2上で動作するModular Monolithを採用する。
- フロントエンド、バックエンド、データベースを単一のProduction EC2に配置する。
- Kubernetes、EKS、Argo CD、ECRは導入しない。
- コンテナを使用せず、Next.js Standaloneを直接実行する。
- データの永続性はEC2のライフサイクルから分離する。
- 既存GMS v3.1のCloudFront、Persistent ENI、OIDC、S3、SSMの設計実績を再利用する。
- 不必要な分散システム化やインフラの複雑化を避ける。

## 2. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js / React |
| UI | Tailwind CSS / shadcn/ui |
| Language | TypeScript |
| Backend | Next.js Route Handlers |
| API | RESTful HTTP / JSON |
| Runtime | Node.js |
| Packaging | Next.js Standalone / tar.gz |
| Architecture | Modular Monolith |
| Compute | Amazon EC2 Spot t4g.small |
| Database | SQLite |
| Database Storage | 独立EBS gp3 |
| Reverse Proxy | Nginx |
| DNS | DreamHost |
| CDN | Amazon CloudFront |
| Network | IPv6 / Persistent ENI |
| Viewer TLS | AWS Certificate Manager |
| Origin TLS | Let's Encrypt / Nginx |
| Authentication | Amazon Cognito Lite |
| Login UI | Cognito Classic Hosted UI |
| Email | Cognito標準メール |
| Periodic Tasks | cron |
| Metrics | Node.js Prometheus Client |
| Monitoring | Prometheus / Grafana |
| Monitoring Compute | Amazon EC2 t4g.micro |
| IaC | Terraform |
| CI | GitHub Actions |
| CD | GitHub OIDC / AWS IAM / S3 / SSM |
| Process Manager | systemd |

UIの詳細は既存要件を基に実装時に具体化する。Product Ownerによる新たなUI技術選定は原則不要とする。

## 3. Runtime Architecture

### Public Request Path

```text
Browser
   |
   | HTTPS
   v
DreamHost DNS (名前解決)
   |
   v
Amazon CloudFront
   | ACM / TLS
   | Dynamic Cache Disabled
   |
   | HTTPS / IPv6
   v
Origin Domain
   |
   v
Persistent ENI / IPv6
   |
   v
Production EC2 — t4g.small
   |
   v
Nginx :443
   | Let's Encrypt TLS
   |
   v
Next.js / Node.js :3000
   |-- React Frontend
   |-- RESTful Route Handlers
   |-- Domain Services
   |-- Translation Orchestrator
   |
   v
SQLite
   |
   v
Dedicated EBS gp3
```

NginxからNext.jsへの接続はEC2内部のLoopbackを使用し、Node.jsの待受ポートを外部公開しない。

CloudFrontとOrigin間のTLSは必須とする。Origin証明書の更新はcronで自動化する。

CloudFrontの動的コンテンツキャッシュは初期段階では無効とする。

## 4. Compute and Persistence

### Production EC2

- EC2 Graviton `t4g.small`
- Spot Instance
- Linux / ARM64
- ルートEBS：OS、Nginx、Node.js、デプロイ済み成果物
- 独立EBS gp3：SQLiteの永続データ
- 必要なデータボリュームは終了時に自動削除しない

SQLiteのメインDB、WAL、関連ファイルは独立EBS上に配置する。

### Startup Sequence

1. EC2の起動とBootstrap
2. 必要なAWSリソース・ENI・EBSの接続状態確認
3. 独立EBSのマウント
4. SQLiteデータファイルの存在とアクセス権限の確認
5. Next.jsサービスの起動
6. Nginxサービスの起動・疎通確認
7. Health Checkの成功確認

systemdでサービスの起動順序、依存関係、自動再起動を管理する。

データEBSがマウントされていない場合、空のSQLite DBをルートEBSへ新規作成して起動してはならない。

### Availability

単一Production EC2の停止によるサービス停止をMVPでは許容する。

Spotの停止・再開、ASGによる代替インスタンス補充、固定ENIと独立EBSの再接続については実装前に一つの復旧方式へ確定する。

## 5. Application Modules

Next.jsの単一アプリケーション内に、論理的に分離したモジュールを設ける。

- Authentication Integration
- User / Account Management
- Glossary Management
- Term Management
- JSON Import
- Translation Orchestration
- API Credential Management
- Account Deletion Lifecycle

Route HandlersはHTTPの入口を担当し、業務ルールは独立したTypeScriptモジュールへ配置する。

Entity ModelのUser、Glossary、Term、ApiCredentialを物理データモデルへマッピングする。

SQLiteのスキーマ、一意性制約、トランザクションおよびMigrationは既存の仕様書から設計・生成し、テストで検証する。

## 6. Authentication and Security

認証はCognito Liteを使用する。

- メールアドレス・パスワード認証
- メール確認
- ログイン・ログアウト
- パスワード再設定
- 認証セッション管理

GMS Backendは、認証済みユーザーの`ACTIVE`状態とデータ所有権を検証する。

BYO APIキーはアプリケーション側で暗号化して保管し、登録後の平文再表示を禁止する。

暗号鍵の保管とローテーション方式はセキュリティ詳細設計で確定する。

CloudFrontのキャッシュ設定、Nginx、SQLiteアクセス、API入力検証では、既存のNFRに定義された基本的なセキュリティ要件を維持する。

## 7. CI/CD Architecture

既存GMS v3.1のGitHub OIDC + S3 + SSM方式を採用する。

```text
Git Push
   |
   v
GitHub Actions
   |-- Lint
   |-- Type Check
   |-- Tests
   |-- Build
   v
Next.js Standalone Artifact
   |
   v
GitHub OIDC
   |
   v
AWS IAM Role
   |
   v
S3 Release Artifacts
   |  Commit SHA / SHA-256
   |
   v
AWS SSM Run Command
   |
   v
Production EC2
   |-- Download
   |-- Integrity Verification
   |-- Extract / Install
   |-- systemd Restart
   v
Health Check
```

長期有効なAWSアクセスキーやSSH秘密鍵をGitHub Actionsに保存しない。

ECR、Docker、Ansible、Argo CDは使用しない。

ロールバックの詳細な自動化は後続設計とする。リリース成果物はバージョンごとに保存し、再デプロイ可能な構成を維持する。

## 8. Observability

監視専用EC2として` t4g.micro `を採用する。

- Production EC2：アプリケーションのメトリクス公開
- Monitoring EC2：Prometheus Server
- Monitoring EC2：Grafana Server

監視用EC2へのアクセスは制限し、メトリクスの収集にユーザーの機密情報を含めない。

メトリクス保持期間とPrometheusのリソース使用量を制限する。

t4g.microでメモリ不足になる場合は、収集対象・保持期間の見直し、またはインスタンスサイズ変更を検討する。

## 9. Periodic Operations

cronを使用して以下を実施する。

- 期限超過アカウントの恒久削除
- Let's Encrypt証明書更新
- 必要な定期メンテナンス

アカウント削除処理は冪等性を持たせ、再実行可能とする。

Spot停止中に実行されなかった処理は、復旧後の次回実行で期限超過分を処理する。

これはMVPの運用上の暫定方針であり、既存仕様にある30暦日後の削除期限を常時厳密に保証するものではない。正式な削除SLAが必要になる段階で再設計する。

## 10. Deferred Decisions

以下はArchitecture Draft時点では未確定とする。

- Spot `stop`方式とASG代替インスタンス方式の最終選択
- 固定ENI・独立EBSの復旧自動化
- EBS Snapshotの取得頻度と保持期間
- SQLiteの物理スキーマ・Migration実装
- CognitoとNext.jsの具体的なセッション連携
- APIキー暗号鍵の管理方式
- Let's Encryptの自動ドメイン検証方式
- Prometheusの詳細な収集設定・保持期間
- デプロイ失敗時のロールバック
- 法令上の保持義務とMVPの削除SLA

これらは後続の詳細設計で扱い、未決定の項目を勝手に実装上の既定値として扱わない。

## 11. Implementation Policy

AIUP Chapter 8の実践ワークフローに従い、次工程はProject Scaffoldingとする。

最初は最小限の実行可能なNext.jsアプリケーションを構築する。

その後、Database Migration、Use Case Implementation、Browserless Tests、E2E Testsを段階的に進める。

アプリケーションが動作するMVPに到達した後、追加のセキュリティ研究・スキャンを実施する。

**この文書はArchitecture Draftであり、実装開始を指示するものではない。**