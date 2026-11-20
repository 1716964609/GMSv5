# GMS v5 — Infrastructure Documentation

- **Owner:** Infrastructure / SRE（Platform Engineering）
- **対象コード:** [`infra/`](../../infra/)
- **基準日:** 2026-11-20（JST）
- **上位の案内:** [docs/README.md](../README.md)

このディレクトリは、GMS v5を動かす基盤の**実際の状態**と、その証跡を置く場所である。Applicationの仕様・設計・Telemetry Schemaは[Development文書](../development/README.md)にある。

## 1. Infrastructureの範囲

Infrastructureが責任を持つもの：

- AWS資源：EC2、EBS、EBS Snapshot、Data Lifecycle Manager、Security Group、IAM、S3、CloudFront（Free PlanのSubscriptionを含む）、Route 53、Cognito、SSM Parameter StoreとSSM Document、Lambda、DynamoDB、EventBridge
- ホスト構成：Nginx、systemd、Node.js Runtime、環境変数ファイル、証明書
- 監視基盤：OTLP Gateway、OTel Collector、Tempo、Prometheus、Grafana、外部Availability監視
- Releaseの配布と切替、Rollbackの仕組み：CD Workflow、Deployment Engine（[`infra/deployment/`](../../infra/deployment/)）、Boot Recovery、Deploymentの監視
- Deployment Engineの導入と更新（手動）、結果が不明なDeploymentの確認
- Secretの実値の発行・保管・配布・Rotation
- Backupと復旧：DLMによるData EBSのSnapshot、Restore Drill、復旧手順
- 公開DNS（Route 53。RegistrarのNameserver委任はDreamHost）と公開経路（DNS / CDN）の性能

Infrastructureが持たないもの：`src/`、`test/`、`e2e/`のコード、業務仕様、Telemetryの名前と属性の定義。境界の全体は[docs/README.md §4](../README.md#4-責任分担とコードの境界)にある。

## 2. 文書の一覧と状態

| 文書 | 種別 | 状態（2026-11-20） |
|---|---|---|
| [production-state.md](./production-state.md) | 現在状態の要約 | **Current。最初に読む。** 実測・Terraform管理範囲・手動構築・未実装を分けて記載 |
| [dns-migration-as-built-2026-11-20.md](./dns-migration-as-built-2026-11-20.md) | 証跡（DNS / CDN / Free Plan） | **最新。** DreamHostからRoute 53への権威DNSの移行、時系列、Edge選択とHealth APIの遅延、Smoke test、CloudFront Free Plan、TerraformのImport、切り戻しの条件、残作業 |
| [dr-as-built-2026-11-20.md](./dr-as-built-2026-11-20.md) | 証跡（Backup / DR / 性能 / CDN） | Backup / DRについては最新。DLMの適用、隔離DR演習のRPO / RTOとDB検証、CR-01の隔離Benchmark、DNS / CDN調査、DR資源のCleanup。詳細な実測値と測定条件はこれを正とする。§12にDLMの初回Snapshot（2026-11-20追記）。§8のDNS / CDNは移行前の調査で、後継はDNS Migration As-Built |
| [disaster-recovery-runbook.md](./disaster-recovery-runbook.md) | Runbook | 障害の分類と判断、Snapshotからの隔離復元、DB検証、Cleanup、DLMの日常確認、Route 53でのOriginのDNS変更（§8.1）。本番Cutoverは承認が要る設計上のGate（未実行） |
| [cicd-as-built-2026-11-19.md](./cicd-as-built-2026-11-19.md) | 証跡（CI/CD） | CI/CDについては最新。CI/CD、Automatic Rollback、Boot Recovery、Deployment Observabilityの本番受入。末尾の追補が、`main`へのPushによる最終DeploymentとDiscord着信の確認を記録する |
| [cicd-operations-runbook.md](./cicd-operations-runbook.md) | Runbook | Deploymentの確認、自動CDの停止、結果コード、分散Lock、Boot Recoveryの調査 |
| [availability-external-runbook.md](./availability-external-runbook.md) | Runbook | EventBridge、Lambda、HTTP外部監視、Spot復旧確認 |
| [availability-external-as-built-2026-11-17.md](./availability-external-as-built-2026-11-17.md) | 証跡（Availability） | AWS適用済み。Lambda・DynamoDB・CloudWatch・Discord・Grafanaの主要経路を検証済み |
| [observability-as-built-2026-11-16.md](./observability-as-built-2026-11-16.md) | 証跡（監視 / SLO / 通知） | 監視基盤・SLO・通知については最新。Prometheus retention / 4パネル / Discord通知 / Rule評価、残課題を記録 |
| [observability/translation-slo-alerting-runbook.md](./observability/translation-slo-alerting-runbook.md) | Runbook | SLO、診断、通知、Secret・復旧と未検証事項 |
| [spot-interruption-runbook.md](./spot-interruption-runbook.md) | Runbook | Application / ObservabilityのSpot中断調査、復旧確認、Prometheus再起動安全策 |
| [observability-as-built-2026-11-13.md](./observability-as-built-2026-11-13.md) | 証跡（監視基盤） | §12が最新（2026-11-14）。§11は2026-11-13夜の記録で、「未完了」とした項目の一部を§12が置き換える。§1〜§10は初期構築時のSnapshot |
| [production-as-built-2026-11-08.md](./production-as-built-2026-11-08.md) | 証跡（Production） | Historical。Baseline 1.2（Release `9783463`）の記録。「Current release」と手動Releaseの記載はSuperseded |
| [production-as-built-2026-10-26.md](./production-as-built-2026-10-26.md) | 証跡（Production） | Historical。初期構築とDeepL Egress修正の記録。Ingress、TLS、Nginx、認証、永続化については今もこれが最新の証跡（DNSは2026-11-20からDNS Migration As-Builtが後継）。Releaseの配置と切替（§6.4）、手動DeploymentについてはSuperseded |

監視は領域ごとに完成度が異なる。領域別の状態は[production-state.md §3.8](./production-state.md)にまとめてある。

### 2.1 Backup / 災害復旧のSource of Truth

| 問い | 正とするもの |
|---|---|
| Backup Policyとして何を宣言しているか | [`infra/terraform/v5/backup.tf`](../../infra/terraform/v5/backup.tf)（宣言。AWS実環境の確認結果ではない） |
| 実際に何を適用し、何を測ったか | [DR As-Built 2026-11-20](./dr-as-built-2026-11-20.md)（Plan / Apply、RPO / RTO、DB検証、Cleanupの実測） |
| 今どうなっているか、何が未確認か | [production-state.md §3.9](./production-state.md#39-backup--disaster-recovery2026-11-20) |
| 障害時にどう復旧するか | [Disaster Recovery Runbook](./disaster-recovery-runbook.md) |
| 恒久構成の説明と図 | [Architecture §16](../architecture.md#16-backup-and-recovery) |

DeploymentやSpot中断などDB復元を要しない障害は、先に[CI/CD Runbook](./cicd-operations-runbook.md)と[Spot Runbook](./spot-interruption-runbook.md)を確認する（DR Runbook §2）。

### 2.2 公開DNSのSource of Truth

| 問い | 正とするもの |
|---|---|
| Hosted ZoneとRecord、Free PlanのSubscriptionとして何を宣言しているか | [`infra/terraform/v5/dns.tf`](../../infra/terraform/v5/dns.tf)、[`dns-import.tf`](../../infra/terraform/v5/dns-import.tf)、[`pricing-plan.tf`](../../infra/terraform/v5/pricing-plan.tf)（宣言） |
| 移行で何をし、何を測ったか | [DNS Migration As-Built 2026-11-20](./dns-migration-as-built-2026-11-20.md) |
| 今どうなっているか、何が未確認か | [production-state.md §3.10](./production-state.md#310-公開経路の性能dns--cdn2026-11-20) |
| OriginのRecordをどう変えるか | [DR Runbook §8.1](./disaster-recovery-runbook.md#81-origin-dns-changes--route-53-and-terraform) |
| 恒久構成の説明と図 | [Architecture §4](../architecture.md#4-ingress--既存web公開経路) |

RegistrarのNameserver委任（DreamHost）、公開CloudFront DistributionとWAFそのもの、自動生成のNS / SOAはTerraformの宣言に含まれない。

関連する横断文書：

- [Architecture As-Is](../architecture.md) — 本番構成の設計上の説明。CI/CD、状態遷移、Rollback、Boot Recovery、Deployment監視の図は§10と§15、Backupと復旧は§16
- [Architecture As-To-Be — Add Observability](../architecture-as-to-be-add-observability.md) — 監視基盤の目標構成。未実装の項目を含む

## 3. コードの配置

| Path | 内容 |
|---|---|
| [`infra/terraform/v5/`](../../infra/terraform/v5/) | Production：EC2、ENI、EBS、IAM、Release用S3、DeepL用CloudFront、Cognito。`cicd.tf`と`deployment-lock.tf`がGitHub OIDCのDeploy Role、SSM Document `GMSv5-DeployRelease`、分散Lockの権限を宣言する。`backup.tf`がDLMのPolicyとIAM Roleを宣言する（AWSへのApplyは2026-11-20。[DR As-Built §3](./dr-as-built-2026-11-20.md#3-permanent-backup-automation--amazon-data-lifecycle-manager)）。`dns.tf`と`dns-import.tf`がRoute 53のHosted ZoneとRecord、`pricing-plan.tf`がCloudFront Free PlanのSubscriptionを宣言する（2026-11-20にImport、No changes。[DNS Migration As-Built §11](./dns-migration-as-built-2026-11-20.md#11-terraform-management)） |
| [`infra/terraform/observability/`](../../infra/terraform/observability/) | Observability EC2：EC2、Security Group、IAM |
| [`infra/terraform/discord-alert-egress/`](../../infra/terraform/discord-alert-egress/) | Discord通知専用CloudFront、Function、KVS、Origin Request Policy |
| [`infra/terraform/availability/`](../../infra/terraform/availability/) | 外部監視：Lambda、DynamoDB、EventBridge、IAM。`deployment.tf`と`lambda/deployment.py`がDeploymentイベントを扱う。`tests/`はAvailability監視のテスト（33件） |
| [`infra/deployment/`](../../infra/deployment/) | Deployment Engine：Artifactの検証、公開、取得、安全な展開、Preflight、Transaction、Automatic Rollback、Boot Recovery、分散Lock、SSM実行。`systemd/`はBoot RecoveryのUnitとDrop-in、`tests/`はEngineのテスト（140件） |
| [`infra/observability/grafana/`](../../infra/observability/grafana/) | Grafana Dashboard / Alert Rule / Contact Point / CloudWatch Data Sourceの機密検査後のExport |
| [`infra/observability/discord-relay/`](../../infra/observability/discord-relay/) | Nginx Relayの**Secretなし**再構築テンプレート |
| [`infra/observability/squid/`](../../infra/observability/squid/) | GrafanaからCloudWatchへ出るためのSquidのACL、systemd Drop-in、待機Script |
| [`.github/workflows/cd.yml`](../../.github/workflows/cd.yml)、[`verify-aws-oidc.yml`](../../.github/workflows/verify-aws-oidc.yml) | 本番への配布と、OIDC認証の確認 |

各ディレクトリが何を宣言し、何を宣言していないかは[production-state.md §4〜§5](./production-state.md#4-terraformの管理範囲宣言)にまとめた。要点は次のとおり。

- `v5`、`observability`、`discord-alert-egress`、`availability`はそれぞれ独立したRoot Moduleで、Stateはローカルファイルである。StateとPlanはGit管理外で、Secretを含み得る。
- ホスト上の実設定（Docker、Container、Nginx、`gmsv5.service`、証明書、Collector / Tempo / Prometheus）は未収録。Discord RelayのRedacted TemplateとGrafanaのExportは2026-11-16、SquidとCloudWatch Data Sourceは2026-11-17、Deployment EngineとBoot RecoveryのUnitは2026-11-19に追加した。**実際のSecret入り設定・再構築の完全自動化は存在しない**。
- Deployment Engineはリポジトリにあるが、本番ホストへの導入と更新はCDの対象外で、Operatorが行う。リポジトリの`infra/deployment/`を変えても、本番のEngineは自動では変わらない。
- GitHub側の設定（Branch保護、Environment `production`、Repository変数）はリポジトリのファイルに定義が無い。

## 4. 記録のルール

1. **宣言と実測を分ける。** Terraformに書いてあることは、AWSで確認したことではない。
2. **到達と受信を分ける。** `up`が1であることは、本番のTelemetryが届いた証拠ではない。
3. **計画と適用を分ける。** As-To-Beの変更を、適用済みの状態として書かない。
4. **証跡は日付付きの記録に書く。** `production-state.md`は要約であり、証跡の代わりにならない。
5. **古い記録は消さない。** 置き換えられた記述には「Superseded」と後継の場所を追記する。
6. **推測で補わない。** ログが無いものは「未確認」「証跡未提示」と書く。
7. **Secretを書かない。** Token、Password、秘密鍵、`gmsv5.env`の内容、Terraform State、CloudFrontの設定JSONは、リポジトリにも文書にも入れない。Parameter名とFile Pathまでを書く。

## 5. Developmentとの受け渡し

InfrastructureがApplicationへ提供するもの：

| 項目 | 現在の内容 |
|---|---|
| Runtime | Linux ARM64、Node.js 26、systemd `gmsv5.service`、実行ユーザー`gmsv5` |
| 待受 | Next.jsは`127.0.0.1:3000`。外部公開はNginxとCloudFrontが担う |
| 永続化 | `GMS_DATABASE_PATH`が指す、独立EBS上のPath |
| 環境変数の実値 | `/etc/gmsv5/gmsv5.env`とSSM Parameter Store |
| Telemetryの送信先 | OTLP/HTTPSのEndpoint、認証Header、CA証明書のPath |
| Release切替 | `main`へのMergeで自動的に行う。`/opt/gmsv5/releases/<full-commit-sha>`への展開、`/opt/gmsv5/current`の原子的な置換、再起動、Readiness 3回連続の確認、失敗時のAutomatic Rollback |
| 再起動時の復旧 | Boot Recovery。中断したDeploymentを直前のReleaseへ戻す |
| Deploymentの結果の通知 | DynamoDB、CloudWatch、Discord |

InfrastructureがDevelopmentから受け取るもの：

| 項目 | 現在の内容 |
|---|---|
| Release Artifact | [`build-arm64.yml`](../../.github/workflows/build-arm64.yml)が作るStandaloneの`tar.gz`、SHA256、`manifest.json` |
| Health Endpoint | `GET /api/health/live`、`GET /api/health/ready`。Readinessは`status`と`release`（Build SHA）を返す。Deployment Engineはこの応答で合否を決める |
| DB Schema Fingerprint | `src/lib/db/migrations.ts`のSHA256。変わるReleaseは自動Deploymentが拒否する |
| 環境変数の名前と意味 | [`.env.example`](../../.env.example) |
| Telemetryの仕様 | [Telemetry Schema v1](../development/observability/application-instrumentation.md)（Span名、Metric名、属性、送信Protocol） |
| Releaseごとの注意 | DB Migrationの有無、追加の環境変数、Rollback時の注意 |

送信先のPort、認証Header、証明書のPathなど、Application側の設定に影響する変更は、適用前にDevelopmentへ伝える。逆に、Developmentが環境変数やTelemetryの名前を変える場合は、適用前にInfrastructureへ伝える。

## 6. 変更したときに更新するもの

| 変更 | 更新する文書 |
|---|---|
| Terraformのapply | 日付付きAs-Built（Plan / Applyの結果）、`production-state.md` §4 |
| ホスト上の手動変更 | 日付付きAs-Built（実行したことと結果）、`production-state.md` §5 |
| 通常の自動Deployment | 文書の更新は不要。証跡はGitHub ActionsのRun、`deployment-result`のArtifact、DynamoDBの記録に残る |
| 受入や節目となるRelease、失敗・Rollback・不明な結果 | 日付付きAs-Built（Run、SHA256、SSMの結果、Readiness）、`production-state.md` §3.1 |
| Deployment Engineの導入・更新 | 日付付きAs-Built（対象Commit、実行したことと結果）、`production-state.md` §5.2 |
| CD Workflow、Deployment Engine、Lock、結果コードの変更 | [Architecture §10](../architecture.md#10-ci--build--deployment)、[CI/CD Runbook](./cicd-operations-runbook.md) |
| 構成の恒久的な変更 | [Architecture As-Is](../architecture.md) |
| Backup Policyの変更、Restore Drill | 日付付きAs-Built（Plan / Apply、測定条件と結果）、`production-state.md` §3.9、手順が変わった場合は[DR Runbook](./disaster-recovery-runbook.md)、構成が変わった場合は[Architecture §16](../architecture.md#16-backup-and-recovery) |
| DNS Recordの変更、Registrarの委任の変更 | `dns.tf`のPlan / Applyの結果を日付付きAs-Built、`production-state.md` §3.10と§4.1、手順が変わった場合は[DR Runbook §8.1](./disaster-recovery-runbook.md#81-origin-dns-changes--route-53-and-terraform)、構成が変わった場合は[Architecture §4](../architecture.md#4-ingress--既存web公開経路) |
| 手動構築部分のIaC化 | `production-state.md` §5から§4へ移す |

## 7. Repository分離に向けた現状

`infra/`と`docs/infrastructure/`は、別Repositoryへ移せる単位としてまとめてある。分離の前に解消が要る結合は次のとおり。いずれも現状の記述であり、対応済みではない。

| 結合 | 現状 |
|---|---|
| Terraform State | ローカルファイル。Remote BackendとLockが無い |
| Stack間の値の受け渡し | Observability側のSecurity Group規則に、Production EC2のIPv6アドレスがリテラルで書かれている |
| Lintの対象 | Applicationの`npm run lint`が`infra/terraform/v5/deepl-proxy-auth.js`と`infra/terraform/discord-alert-egress/auth.js`も走査している（既存のWarning 4件、Error 0件。2026-11-19に実行して確認） |
| Build Workflow | `.github/workflows/build-arm64.yml`はApplicationをBuildするためApplication側に残る。ただし現在は`infra/deployment/`のテストとScript（Manifest生成、展開の確認）を呼び出している |
| CI / CD Workflow | `ci.yml`は両領域のテストを1つのJobで実行する。`cd.yml`はBuild Workflowを再利用し、`infra/deployment/`のScriptを呼ぶ。分離するにはWorkflowの分割が要る |
| Readinessの契約 | Deployment Engine（`infra/`）が、Application（`src/`）のHealth Endpointの応答形式に依存する |
| 文書の相互リンク | `docs/infrastructure/`と`docs/development/`、`docs/architecture*.md`の間に相対リンクがある。分離時はURLへの書き換えが要る |
| 横断文書 | `architecture.md`と`architecture-as-to-be-add-observability.md`は両方の領域を扱う。どちらのRepositoryに置くかは未決定 |
| ホスト構成 | リポジトリに無い。分離先でも再現できない |
