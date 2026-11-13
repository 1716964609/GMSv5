# GMS v5 — Infrastructure Documentation

- **Owner:** Infrastructure / SRE（Platform Engineering）
- **対象コード:** [`infra/`](../../infra/)
- **基準日:** 2026-11-14（JST）
- **上位の案内:** [docs/README.md](../README.md)

このディレクトリは、GMS v5を動かす基盤の**実際の状態**と、その証跡を置く場所である。Applicationの仕様・設計・Telemetry Schemaは[Development文書](../development/README.md)にある。

## 1. Infrastructureの範囲

Infrastructureが責任を持つもの：

- AWS資源：EC2、EBS、Security Group、IAM、S3、CloudFront、Cognito、SSM Parameter Store
- ホスト構成：Nginx、systemd、Node.js Runtime、環境変数ファイル、証明書
- 監視基盤：OTLP Gateway、OTel Collector、Tempo、Prometheus、Grafana
- Releaseの配布と切替、Rollback
- Secretの実値の発行・保管・配布・Rotation
- Backupと復旧

Infrastructureが持たないもの：`src/`、`test/`、`e2e/`のコード、業務仕様、Telemetryの名前と属性の定義。境界の全体は[docs/README.md §4](../README.md#4-責任分担とコードの境界)にある。

## 2. 文書の一覧と状態

| 文書 | 種別 | 状態（2026-11-14） |
|---|---|---|
| [production-state.md](./production-state.md) | 現在状態の要約 | **Current。最初に読む。** 実測・Terraform管理範囲・手動構築・未実装を分けて記載 |
| [observability-as-built-2026-11-13.md](./observability-as-built-2026-11-13.md) | 証跡（監視基盤） | §12が最新（2026-11-14）。§11は2026-11-13夜の記録で、「未完了」とした項目の一部を§12が置き換える。§1〜§10は初期構築時のSnapshot |
| [production-as-built-2026-11-08.md](./production-as-built-2026-11-08.md) | 証跡（Production） | Historical。Baseline 1.2（Release `9783463`）の記録。「Current release」の記載はSuperseded |
| [production-as-built-2026-10-26.md](./production-as-built-2026-10-26.md) | 証跡（Production） | Historical。初期構築とDeepL Egress修正の記録。Ingress、TLS、Nginx、Runtimeについては今もこれが最新の証跡 |

関連する横断文書：

- [Architecture As-Is](../architecture.md) — 本番構成の設計上の説明
- [Architecture As-To-Be — Add Observability](../architecture-as-to-be-add-observability.md) — 監視基盤の目標構成。未実装の項目を含む

## 3. コードの配置

| Path | 内容 |
|---|---|
| [`infra/terraform/v5/`](../../infra/terraform/v5/) | Production：EC2、ENI、EBS、IAM、Release用S3、DeepL用CloudFront、Cognito |
| [`infra/terraform/observability/`](../../infra/terraform/observability/) | Observability EC2：EC2、Security Group、IAM |

各ディレクトリが何を宣言し、何を宣言していないかは[production-state.md §4〜§5](./production-state.md#4-terraformの管理範囲宣言)にまとめた。要点は次のとおり。

- 2つは独立したRoot Moduleで、Stateはローカルファイルである。StateとPlanはGit管理外で、Secretを含み得る。
- ホスト上の構成（Docker、Container、Nginx、systemd、証明書、Collector / Tempo / Prometheus / Grafanaの設定）はリポジトリに無い。As-Built記録の記述だけが手がかりである。

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
| Release切替 | `/opt/gmsv5/releases/<sha>`への展開、systemd切替、Readiness確認、Rollback |

InfrastructureがDevelopmentから受け取るもの：

| 項目 | 現在の内容 |
|---|---|
| Release Artifact | [`build-arm64.yml`](../../.github/workflows/build-arm64.yml)が作るStandaloneの`tar.gz`とSHA256 |
| 環境変数の名前と意味 | [`.env.example`](../../.env.example) |
| Telemetryの仕様 | [Telemetry Schema v1](../development/observability/application-instrumentation.md)（Span名、Metric名、属性、送信Protocol） |
| Releaseごとの注意 | DB Migrationの有無、追加の環境変数、Rollback時の注意 |

送信先のPort、認証Header、証明書のPathなど、Application側の設定に影響する変更は、適用前にDevelopmentへ伝える。逆に、Developmentが環境変数やTelemetryの名前を変える場合は、適用前にInfrastructureへ伝える。

## 6. 変更したときに更新するもの

| 変更 | 更新する文書 |
|---|---|
| Terraformのapply | 日付付きAs-Built（Plan / Applyの結果）、`production-state.md` §4 |
| ホスト上の手動変更 | 日付付きAs-Built（実行したことと結果）、`production-state.md` §5 |
| 本番Releaseの切替 | 日付付きAs-Built（Build Run、SHA256、切替結果、Readiness）、`production-state.md` §3.1、[Root README](../../README.md) |
| 構成の恒久的な変更 | [Architecture As-Is](../architecture.md) |
| 手動構築部分のIaC化 | `production-state.md` §5から§4へ移す |

## 7. Repository分離に向けた現状

`infra/`と`docs/infrastructure/`は、別Repositoryへ移せる単位としてまとめてある。分離の前に解消が要る結合は次のとおり。いずれも現状の記述であり、対応済みではない。

| 結合 | 現状 |
|---|---|
| Terraform State | ローカルファイル。Remote BackendとLockが無い |
| Stack間の値の受け渡し | Observability側のSecurity Group規則に、Production EC2のIPv6アドレスがリテラルで書かれている |
| Lintの対象 | Applicationの`npm run lint`が`infra/terraform/v5/deepl-proxy-auth.js`も走査している（既存のWarning 2件） |
| Build Workflow | `.github/workflows/build-arm64.yml`はApplicationをBuildするためApplication側に残る。Artifactの置き場（S3）と切替はInfrastructure側 |
| 文書の相互リンク | `docs/infrastructure/`と`docs/development/`、`docs/architecture*.md`の間に相対リンクがある。分離時はURLへの書き換えが要る |
| 横断文書 | `architecture.md`と`architecture-as-to-be-add-observability.md`は両方の領域を扱う。どちらのRepositoryに置くかは未決定 |
| ホスト構成 | リポジトリに無い。分離先でも再現できない |
