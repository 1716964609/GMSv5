# GMS — Architecture Evolution (v1 → v5)

**用語管理システムから、AI翻訳Webアプリケーションへ。設計とインフラの変遷をMermaidで振り返る。**

[日本語README](../README.ja.md) · [English README](../README.md) · [v5の現在のArchitecture](../README.ja.md#diagram-a--production-system-architecture)

この資料は、GMS v1・v2・v3.1・v4・v5の**当時の実装・構築記録**を、面接などで説明しやすい粒度で比較するものです。すべての図はGitHubが表示できるMermaid形式で、コードから編集できます。

> **情報の根拠と制約**
> - **v1：** 当時のREADMEとソースコードに基づく論理構成。過去のAWS実環境は今回再確認していません。
> - **v2：** `v2-reconstructed` ブランチに保存されたREADME・Nginx・systemd・fstabによる再構成Baseline。元の全ソースと完全一致するという意味ではありません。
> - **v3.1：** Terraform・GitHub Actionsと、当時の最終統合試験の記録による構成。現在のAWS実状態は再監査していません。
> - **v4：** ソースコード上のアプリケーション構成。**本番未デプロイ**です。
> - **v5：** 2026年11月8日のリリース記録と現行のREADME・コードによる構成。本番の網羅的E2E・正式な性能受入が全て完了したという意味ではありません。

## 1. 全体像 — 5世代の変遷

```mermaid
flowchart LR
    V1["v1<br/>Spring Boot + RDS<br/>用語検索・管理"]
    V2["v2<br/>EC2内MySQL + 独立EBS<br/>永続データ分離"]
    V3["v3.1<br/>Terraform + CI/CD<br/>監視・復旧設計"]
    V4["v4<br/>Next.js + TypeScript<br/>DeepL → OpenAI<br/>未デプロイ"]
    V5["v5<br/>Cognito / BYOK<br/>IPv6-first 本番公開"]
    V1 -->|"DB配置・永続化の見直し"| V2
    V2 -->|"再現性・運用自動化"| V3
    V3 -->|"アプリケーション再設計"| V4
    V4 -->|"機能拡張・本番実装"| V5
```

バージョン番号が上がったことは、全機能が単純に上位互換で引き継がれたことを意味しません。特に**v4は、用語管理アプリから二段階AI翻訳を中心とした製品へ転換した実装検証段階**です。

## 2. GMS v1 — Spring BootとAmazon RDS

**目的：** 専門用語の検索・閲覧と、管理者による用語・用語リストのCRUD。

```mermaid
flowchart TB
    B["Browser<br/>HTML / CSS / JavaScript"]
    NG["Nginx<br/>配置・詳細設定は未確認"]
    subgraph EC2["Amazon EC2"]
        APP["Java 17 / Spring Boot 3.3.3"]
        SEC["Spring Security<br/>認証・CSRF"]
        CT["Controller → Service → Repository<br/>Spring Data JPA"]
        APP --> CT
        SEC -.-> APP
    end
    subgraph RDS["Amazon RDS"]
        SQL[("MySQL<br/>Users / Terms / Lists")]
    end
    B -->|"Webアクセス"| NG
    NG -->|"アプリへの論理的な接続"| APP
    CT --> SQL
```

READMEには**EC2、RDS、Nginx、Let's Encrypt**が記載されています。ただし、当時の**Nginx配置先・TLS終端位置・具体的な通信ポート・VPC構成は確認できない**ため図では断定していません。前回、利用者の指定で追加したDreamHost DNSも、v1の公開README単体から確認できないためこの図には入れていません。

**根拠：** [v1 README](https://github.com/1716964609/GMS-v1/blob/master/README.md) · [pom.xml](https://github.com/1716964609/GMS-v1/blob/master/pom.xml) · [SecurityConfig.java](https://github.com/1716964609/GMS-v1/blob/master/src/main/java/com/example/GMS_v1/Config/SecurityConfig.java)

## 3. GMS v2 — EC2上のMySQLと独立EBS

**設計の変化：** Javaの主要業務ロジックを継承しつつ、MySQLをEC2上に移動。データ用EBSをRoot Volumeから分離し、OS・アプリケーションとDBデータのライフサイクルを分けました。

```mermaid
flowchart TB
    B["Browser"]
    subgraph EC2["EC2 Spot / Amazon Linux 2023"]
        NG["Nginx :80"]
        APP["Java 17 / Spring Boot<br/>Tomcat :8080"]
        SRV["Controller → Service → Repository<br/>Spring Security / Hibernate"]
        SQL[("MySQL<br/>localhost:3306")]
        SD["systemd<br/>tgms.service"]
        NG -->|"proxy_pass http://[::1]:8080"| APP
        APP --> SRV
        SRV --> SQL
        SD -.->|"プロセス管理"| APP
    end
    ROOT[("Root EBS<br/>OS / Runtime")]
    DATA[("Dedicated EBS<br/>/var/lib/mysql")]
    B -->|"Webアクセス"| NG
    ROOT -.->|"Root filesystem"| APP
    SQL --> DATA
```

Nginx設定にはIPv6 loopback **`[::1]:8080`** へのproxy_pass、Spring Bootの設定には8080番ポートが記載されています。これは保存された**設定上の経路**であり、今回のライブ疎通検証ではありません。systemdの`tgms.service`でアプリケーションを管理し、v2固定時にはRoot EBSとMySQL Data EBSのSnapshotを取得したとREADMEに記録されています。

このv2の本番DBとしてRDSは描いていません。また、**当時のMySQL Data EBSが暗号化されていたとは記録から確認していない**ため、暗号化EBSとは表記していません。

**根拠：** [v2 README](https://github.com/1716964609/GMS-v1/blob/v2-reconstructed/README.md) · [Nginx](https://github.com/1716964609/GMS-v1/blob/v2-reconstructed/infra/legacy-v2/nginx/default.conf) · [systemd](https://github.com/1716964609/GMS-v1/blob/v2-reconstructed/infra/legacy-v2/systemd/tgms.service) · [fstab](https://github.com/1716964609/GMS-v1/blob/v2-reconstructed/infra/legacy-v2/fstab)

## 4. GMS v3.1 — IaC、CI/CD、Observabilityと復旧

**設計の変化：** v2のSpring Boot・MySQL・EBSを基本に、TerraformによるAWS構成管理、CloudFront/WAFによる公開入口、自動デプロイ、Actuatorの可観測性、復旧方針を組み込みました。

### 4.1 Production Infrastructure

```mermaid
flowchart TB
    B["Web Browser"]
    DNS["DreamHost<br/>Authoritative DNS"]
    CF["Amazon CloudFront<br/>Viewer HTTPS"]
    ACM["AWS ACM<br/>証明書 us-east-1"]
    WAF["AWS WAF<br/>CloudFrontスコープ"]
    subgraph AWS["AWS Tokyo / VPC / Public Subnet"]
        ENI["ENI<br/>IPv6 Origin"]
        subgraph EC2["EC2 Spot t3a.micro"]
            NG["Nginx :80"]
            APP["Spring Boot 3.3.3<br/>Java 17 / systemd"]
            DB[("MySQL<br/>EC2内")]
            NG -->|"IPv6 loopback :8080"| APP
            APP --> DB
        end
        ENI --> NG
    end
    EBS[("Dedicated EBS<br/>/var/lib/mysql")]
    TF["Terraform<br/>AWS Infrastructure as Code"]
    B -.->|"DNS照会"| DNS
    B -->|"HTTPS"| CF
    CF -.->|"Origin DNS照会"| DNS
    ACM -.-> CF
    WAF -.-> CF
    CF -->|"HTTP over IPv6"| ENI
    DB --> EBS
    TF -.->|"管理"| CF
    TF -.->|"管理"| ENI
    TF -.->|"管理"| EBS
```

**DNSはHTTPリクエストを中継しません。** DreamHostは公開ドメインとOriginホスト名の名前解決を担います。v3.1ではCloudFrontがViewer HTTPSを扱い、**Origin側はHTTP / IPv6**で接続していました。この点はv5のHTTPS Originと異なります。

Terraformの対象はVPC/Subnet/ENI/EC2/EBS、CloudFront/ACM/WAF、IAM/OIDC、S3等です。DreamHost DNSはTerraform管理の範囲外でした。

**根拠：** [v3.1 README](https://github.com/1716964609/GMS-v1/blob/v3.1/README.md) · [Architecture](https://github.com/1716964609/GMS-v1/blob/v3.1/infra/docs/architecture.md) · [CloudFront Terraform](https://github.com/1716964609/GMS-v1/blob/v3.1/infra/terraform/cloudfront.tf) · [EC2定義](https://github.com/1716964609/GMS-v1/blob/v3.1/infra/terraform/instance.tf)

### 4.2 GitHub Actions CI/CD

```mermaid
flowchart LR
    G["Git push to v3.1"]
    CI["GitHub Actions CI<br/>Java 17 / MySQL / Maven"]
    JAR["JAR + SHA256"]
    OIDC["GitHub OIDC<br/>AWS IAM Role"]
    S3[("Amazon S3<br/>Release Ledger")]
    SSM["AWS SSM<br/>Run Command"]
    EC2["Production EC2<br/>JAR / tgms.service"]
    HC["Actuator Health Check<br/>失敗時は旧JARへ切戻し"]
    G --> CI --> JAR --> S3 --> SSM --> EC2 --> HC
    CI --> OIDC
    OIDC -.->|"認証・権限付与"| SSM
```

### 4.3 Observability — ローカル監視環境

```mermaid
flowchart LR
    APP["Production Spring Boot<br/>/actuator/prometheus"]
    TUN["SSH Tunnel"]
    subgraph MAC["Mac / 必要時に起動"]
        PROM["Prometheus"]
        GRAF["Grafana"]
        PROM --> GRAF
    end
    APP --> TUN --> PROM
```

v3.1ブランチへのpushでは、CI成功後のJARを**GitHub OIDC → S3 → SSM**で本番配布するWorkflowが実装されています。失敗時の旧JAR切戻し処理も記述されています。対してPrometheus/Grafanaは**Mac上で必要に応じて稼働**する想定であり、本番で24時間稼働する監視サービスではありません。

**根拠：** [CI/CD workflow](https://github.com/1716964609/GMS-v1/blob/v3.1/.github/workflows/ci.yml) · [Prometheus設定](https://github.com/1716964609/GMS-v1/blob/v3.1/infra/monitoring/prometheus/prometheus.yml) · [v3.1 README](https://github.com/1716964609/GMS-v1/blob/v3.1/README.md)

## 5. GMS v4 — Next.js Modular Monolithと二段階AI翻訳

**設計の変化：** Java/Spring Boot中心の構成から、Next.js、React、TypeScript、SQLite/Drizzle ORMへ。ユーザーがアップロードした用語を使い、DeepLの一次翻訳をOpenAIがポストエディットする新ワークフローを実装しました。

**v4は本番未デプロイのため、以下はApplication Architectureです。**

```mermaid
flowchart TB
    USER["Browser / React UI<br/>原文・言語・JSON用語集"]
    API["Next.js Route Handler<br/>POST /api/translate"]
    VALID["JSON Glossary Validation<br/>最大1,000件"]
    WORK["Translation Service<br/>言語ペアに基づく用語選別"]
    MODE{"Provider Mode"}
    DEEPL["DeepL Adapter<br/>一次翻訳"]
    OPENAI["OpenAI Adapter<br/>ポストエディティング"]
    MOCK["Mock Providers<br/>開発・テスト"]
    DONE["二段階処理の成功"]
    UPLOAD{"JSONアップロードあり?"}
    REPO["Glossary Repository<br/>Atomic Append"]
    DB[("SQLite / Drizzle ORM<br/>glossary_entries")]
    RESP["Final Translation<br/>結果表示・TXT出力"]
    USER --> API --> VALID --> WORK --> MODE
    MODE -->|"real"| DEEPL -->|"Intermediate Translation"| OPENAI --> DONE
    MODE -->|"mock"| MOCK --> DONE
    DONE --> UPLOAD
    UPLOAD -->|"Yes"| REPO --> DB
    DB -->|"保存成功"| RESP
    UPLOAD -->|"No"| RESP
    RESP --> USER
```

real modeではDeepLとOpenAIを逐次呼び、defaultのmock modeでは実際の外部APIへアクセスしません。**その要求でアップロードしたJSONの用語だけ**を翻訳に使い、以前の保存済みGlossaryは新たな翻訳に自動適用しません。JSONがある場合は両翻訳成功後にDBへ一括追記し、その保存が失敗すれば成功結果を返しません。原文・中間訳・最終訳・翻訳履歴は永続化しません。

v3.1で使用したAWS/Terraform/CloudFront/監視構成がv4にデプロイされた、という主張はしません。

**根拠：** [v4 README](https://github.com/1716964609/GMSv4/blob/main/README.md) · [Translation Service](https://github.com/1716964609/GMSv4/blob/main/src/modules/translation/service.ts) · [Provider切替](https://github.com/1716964609/GMSv4/blob/main/src/modules/translation/runtime.ts) · [SQLite Schema](https://github.com/1716964609/GMSv4/blob/main/src/db/schema.ts)

## 6. GMS v5 — ユーザー別用語集とIPv6-first Production

**設計の変化：** v4の二段階翻訳を発展させ、Cognito認証、ユーザー別Glossary/Term CRUD、暗号化BYOK、用語の選択、AWS本番運用、公開LPを統合。詳細な最新図は[日本語README](../README.ja.md)にあります。

### 6.1 Production System Architecture

```mermaid
flowchart TB
    B["Browser"]
    DNS["DreamHost DNS<br/>名前解決"]
    CF["CloudFront<br/>公開Ingress / HTTPS"]
    AUTH["Amazon Cognito<br/>OAuth Code + PKCE"]
    subgraph EC2["AWS EC2 Spot t4g.small / ARM64 / IPv6-only"]
        NG["Nginx<br/>Origin HTTPS"]
        APP["Next.js 16 / Node.js 26<br/>Modular Monolith"]
        DB[("SQLite<br/>用語・ユーザー・暗号化APIキー")]
        NG -->|"127.0.0.1:3000"| APP --> DB
    end
    EBS[("独立・暗号化EBS gp3<br/>SQLite永続化")]
    DCF["CloudFront<br/>DeepL専用Reverse Proxy"]
    DEEPL["DeepL API"]
    OPENAI["OpenAI API"]
    B -.->|"DNS照会"| DNS
    B -->|"HTTPS"| CF
    CF -->|"HTTPS over IPv6"| NG
    B -.->|"ログイン"| AUTH
    APP -.->|"認証コード交換・検証"| AUTH
    DB --- EBS
    APP -->|"HTTPS / IPv6"| DCF
    DCF -->|"HTTPS / IPv4側Origin"| DEEPL
    APP -->|"直接HTTPS / IPv6"| OPENAI
```

v5ではCloudFrontからNginxへのOrigin接続も**HTTPS over IPv6**です。EC2はパブリックIPv4を持ちません。DeepL向けには専用CloudFront Reverse Proxyを使い、OpenAIへはEC2からネイティブIPv6で接続します。公開用CloudFrontとDeepL用CloudFrontは**別Distribution**です。

v4と異なり、v5では**ユーザーが永続GlossaryからTerm IDを選択**し、バックエンドで所有権・現在の用語を再検証したうえで翻訳へ渡します。

### 6.2 Revision-aware Select All

```mermaid
flowchart TB
    UI["Browser<br/>Select All"]
    FIRST["最初のTermページ取得<br/>termSetVersion確定"]
    POOL["制限付き並列ページ取得<br/>既定2 / 最大4"]
    CHECK["ページ・ID・件数・版を検証"]
    VERIFY["最後に改訂版を再確認"]
    COMMIT["10,000 ID以内なら<br/>選択集合へ一括反映"]
    ABORT["エラー / 409 / Abort<br/>今回の選択変更は0件"]
    DB["SQLite<br/>glossary_term_revisions"]
    UI --> FIRST --> POOL --> CHECK --> VERIFY
    VERIFY -->|"成功"| COMMIT
    FIRST -.-> DB
    POOL -.-> DB
    VERIFY -.-> DB
    FIRST -->|"失敗"| ABORT
    POOL -->|"失敗"| ABORT
    CHECK -->|"不一致"| ABORT
    VERIFY -->|"版不一致"| ABORT
```

改訂版は取得中の変更を**検出**するもので、過去版を固定して読む厳密snapshotではありません。2026年11月8日の本番ブラウザで10,000件選択を確認したとの記録があり、所要時間は約27秒（旧版約40秒）でした。ただし**正式なp95性能受入は未完了**です。本番migration v2適用ログ・systemd最終状態についても[As-Built](infrastructure/production-as-built-2026-11-08.md)の確認範囲を優先してください。

**根拠：** [v5日本語README](../README.ja.md) · [Production Architecture](architecture.md) · [CR-01設計](design/cr-01-parallel-fetch-design.md) · [2026-11-08 As-Built](infrastructure/production-as-built-2026-11-08.md)

## 7. Deployment Evolution — v3.1とv5のCI/CDは異なる

```mermaid
flowchart TB
    subgraph V3["v3.1：push後の自動Deployment"]
        A1["push to v3.1"] --> A2["GitHub Actions<br/>build + test"]
        A2 --> A3["OIDC → S3 → SSM"]
        A3 --> A4["EC2 / systemd<br/>health / rollback"]
    end
    subgraph V5["v5：手動工程を含むRelease"]
        B1["workflow_dispatch"] --> B2["GitHub Actions ARM64<br/>build / artifact"]
        B2 --> B3["手動でArtifact検証・S3配布"]
        B3 --> B4["SSM経由でEC2切替<br/>systemd / readiness"]
    end
```

v3.1の自動CD、v4の未デプロイ、v5の手動リリースを混同しないことが重要です。v5のGitHub Actions Buildには、開発段階の単体・結合テストを自動実行するステップがないことも、現行READMEで区別しています。

## 8. バージョン比較

| 観点 | v1 | v2 | v3.1 | v4 | v5 |
|---|---|---|---|---|---|
| 主な目的 | 用語検索・管理 | データ分離・Baseline | IaC・CI/CD・観測・復旧 | 二段階AI翻訳 | 用語管理と翻訳の公開製品 |
| Runtime | Spring Boot | Spring Boot | Spring Boot | Next.js / Node.js | Next.js / Node.js |
| DB | RDS MySQL | EC2内MySQL | EC2内MySQL | SQLite / Drizzle | SQLite / node:sqlite |
| 永続データ | RDS | 専用EBS | 専用EBS + Snapshot | ローカルSQLite | 暗号化独立EBS |
| 公開入口 | EC2・Nginx（詳細未確認） | EC2・Nginx | CloudFront / HTTP IPv6 Origin | **未デプロイ** | CloudFront / HTTPS IPv6 Origin |
| 構築・配布 | 記録不足 | systemd / 手動 | Terraform / 自動CI/CD | ローカル開発・テスト | Terraform＋手動設定 / ARM64 Build＋手動Release |
| Observability | 確認資料なし | 確認資料なし | Actuator＋Mac上Prometheus/Grafana | 自動テスト中心 | As-Built・検証記録 |
| 翻訳 | 用語検索・管理 | 用語検索・管理 | 用語検索・管理 | DeepL → OpenAI | DeepL → OpenAI＋永続Glossary選択 |

「確認資料なし」は、その時代に該当機能が絶対存在しなかったという意味ではありません。旧バージョンの記述は、それぞれの指定ブランチと保存された資料の範囲に限定しています。

## 9. 設計判断の変遷をどう説明するか

**v1 → v2：Computeとデータのライフサイクル分離。** DBをRDSからEC2内へ移したことは、単純な性能・可用性向上ではありません。運用責任が増える一方、専用EBSとSnapshotによるデータ保護・復旧方針を明確にしました。

**v2 → v3.1：再現性と運用自動化。** Terraform、CI/CD、Observability、復旧手順を導入し、システムを「動く状態」だけでなく「再構築・監視・配布できる状態」へ近づけました。

**v3.1 → v4：アプリケーションそのものの目的と境界を再設計。** Spring Boot用語管理からNext.jsの二段階翻訳へ。ただしv4の成果は実装・テストであり、AWS本番運用の継続を意味しません。

**v4 → v5：実際の利用者と本番運用に向けた拡張。** Cognito認証、ユーザー別用語集、BYOK、IPv6-first Networking、DeepLの接続制約への対応を追加。一方、単一EC2、Spot中断、手動リリースなどのトレードオフは残っています。

---

**現行v5の正確な設計と確認済み実績は** [README.ja.md](../README.ja.md)、[Production Architecture](architecture.md)、[2026-11-08 As-Built](infrastructure/production-as-built-2026-11-08.md) **を優先してください。** この資料は世代間の比較であり、旧AWS環境を再稼働して確認した記録ではありません。
