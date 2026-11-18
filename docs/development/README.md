# GMS v5 — Development Documentation

- **Owner:** Development（Application Engineering）
- **対象コード:** [`src/`](../../src/)、[`test/`](../../test/)、[`e2e/`](../../e2e/)
- **基準日:** 2026-11-19（JST）
- **上位の案内:** [docs/README.md](../README.md)

このディレクトリは、Applicationの設計と検証の文書を置く場所である。AWS・ホスト・監視基盤の実際の状態は[Infrastructure文書](../infrastructure/README.md)にある。

## 1. Developmentの範囲

Developmentが責任を持つもの：

| Path | 内容 |
|---|---|
| `src/app/` | 画面とRoute Handlers |
| `src/lib/` | 業務サービス、SQLiteアクセスとMigration、翻訳Provider、認証、入力検証 |
| `src/app/api/health/`、`src/lib/health/` | Liveness / Readiness。Deploymentの合否判定に使われる |
| `src/instrumentation.ts`、`src/lib/observability/` | OpenTelemetryの初期化、Span、Metrics、Redaction、Browser RUM |
| `test/` | 単体・結合テスト（`node --test`） |
| `e2e/`、`playwright.config.ts` | ブラウザE2E |
| `scripts/` | Migration、アカウント削除処理、LP用のサンプルデータ |
| `package.json`、`package-lock.json`、`next.config.ts`、`tsconfig.json`、`eslint.config.mjs`、`postcss.config.mjs` | 依存とBuild設定 |
| `.env.example` | 環境変数の名前と意味 |
| `.github/workflows/build-arm64.yml` | Release ArtifactのBuild |
| `.github/workflows/ci.yml`のうちApplicationの検査 | 型チェック、Lint、Applicationテスト、Production Build |

Developmentが持たないもの：`infra/`のTerraformとDeployment Engine、`.github/workflows/cd.yml`、本番の環境変数の実値とSecret、Nginx・systemdなどのホスト構成、監視基盤、本番Releaseの切替の仕組み。境界の全体は[docs/README.md §4](../README.md#4-責任分担とコードの境界)にある。

**`main`へのMergeは本番Deploymentを起動する**（2026-11-19から。自動CDが有効な間）。Pull RequestはCI Quality Gateの成功が無ければMergeできない。仕組みは[Architecture §10](../architecture.md#10-ci--build--deployment)にある。

## 2. 文書の一覧と状態

### 2.1 仕様

仕様の文書は`docs/`直下にある。今回の整理で場所は変えていない。

| 文書 | 内容 |
|---|---|
| [Product Vision](../vision.md) | 製品の目的と範囲 |
| [Requirements](../requirements.md) | 機能要件・非機能要件 |
| [Entity Model](../entity-model.md) | 業務Entityと制約 |
| [Use Cases](../use-cases.md)、[Use Case Diagram](../use-case-diagram.puml) | 振る舞いと受入条件 |

### 2.2 設計

| 文書 | 内容 |
|---|---|
| [CR-01 技術設計](../design/cr-01-parallel-fetch-design.md) | 制限付き並列取得、改訂管理、性能測定計画 |
| [Baseline 1.2 デザイン記録](../design/baseline-1.2-proposal.md) | 公開LPの承認済み方針 |
| [Architecture Evolution v1→v5](../GMS_Architecture_Evolution_v1-v5_fixed.md) | 過去Versionとの比較。経緯の説明用で、現在状態の根拠にはしない |

### 2.3 Application Observability

| 文書 | 種別 | 状態（2026-11-19） |
|---|---|---|
| [application-instrumentation.md](./observability/application-instrumentation.md) | 設計 / Telemetry Schema v1 | **Current。** Span名、Metric名、属性、環境変数、Redactionの定義はこれを正とする |
| [application-instrumentation-verification-2026-11-13.md](./observability/application-instrumentation-verification-2026-11-13.md) | ローカル検証記録 | Historical。ローカル検証の証跡として有効。「Not Verified」「SRE Handoff」の一部は本番で確認済み |
| [application-release-readiness-2026-11-13.md](./observability/application-release-readiness-2026-11-13.md) | Release判定・Deployment / Rollback手順 | Historical。判定・Gate・有効化時の設定例はSuperseded。手動のDeployment手順とRelease切り戻し手順も、2026-11-19の自動Deployment導入でSuperseded。Telemetryのみの無効化（§8.2）は引き続き参照できる |

旧パス`docs/observability/`には転送用のスタブを残してある。

## 3. Application Observabilityの現在の状態

| 項目 | 状態 | 根拠 |
|---|---|---|
| 実装 | Commit `75d5b17`。本番Release `364d0b7`以降に含まれる | [production-state.md §3.1](../infrastructure/production-state.md) |
| Server Traces / Metrics | 本番で有効。実リクエストのTraceとMetricsを確認済み | [Observability As-Built §12](../infrastructure/observability-as-built-2026-11-13.md#12-production-phase-1-acceptance) |
| Browser RUM | 実装済み。本番では無効。Sampling / Privacy方針（OD-10）は未承認 | 同上 |
| Telemetry無効時の挙動 | `GMS_OTEL_ENABLED`が`true`でなければSDKを読み込まない | [Telemetry Schema](./observability/application-instrumentation.md) DD-02 |
| 単体・結合テスト | 125件（2026-11-19に`main`の`53ece4d`と同じコードでローカル実行し全件成功）。`75d5b17`の時点では114件（既存83件、Observability 31件）だった。増えた11件はHealth Endpointのテストである | `npm test`、[Release Readiness §3.1](./observability/application-release-readiness-2026-11-13.md) |
| CIでのテスト実行 | CI Quality Gateが、Pull Requestごとに型チェック、Lint、Applicationテスト、Production Buildを実行する。Build Workflowも同じ検査とApplicationテストを実行する。ブラウザE2Eはどちらも実行しない。「Build Workflowは型チェック・Lint・Buildのみ」という2026-11-14の記述はSuperseded | [`ci.yml`](../../.github/workflows/ci.yml)、[`build-arm64.yml`](../../.github/workflows/build-arm64.yml) |
| Lint | Error 0件、Warning 4件（いずれも`infra/terraform/`配下のCloudFront Function。2026-11-19に実行して確認） | `npm run lint` |

Development側に残っている課題（[Release Readiness §4](./observability/application-release-readiness-2026-11-13.md)。2026-11-13の記録であり、今回は再確認していない）：

- **E-1：** LPのE2E SpecがCR-02の文言に追従しておらず失敗する。
- **E-2：** E2Eの`webServer`が開発者の`.env.local`を読み込む。
- **E-3：** 開発ツリーでは`npm run lint`が生成物`test-results/`を走査する。

## 4. 検証コマンド

```bash
npm run typecheck
npm run lint
npm test
npm run build
npm run test:e2e
```

`npm test`は`node --experimental-strip-types --test test/*.test.ts`と同じである。CI Quality Gateは上のうち`test:e2e`以外を実行し、加えてInfrastructure側のテスト（Deployment Engine、Availability監視）を実行する。

外部API（DeepL、OpenAI、Cognito）はMockで代替する。課金が発生する実APIを自動テストから呼ばない。

## 5. Infrastructureとの受け渡しでDevelopmentが守ること

1. **環境変数を追加・変更したら**、`.env.example`と[Telemetry Schema §7](./observability/application-instrumentation.md)を更新し、適用前にInfrastructureへ伝える。
2. **Span名・Metric名・属性を変えたら**、Telemetry Schemaと`test/observability.test.ts`を更新する。DashboardとAlertはこの名前に依存する。
3. **Telemetryは既定で無効**とし、送信に失敗してもApplicationの応答を変えない。
4. **原文・訳文・用語・API Key・Cookie・Query StringをTelemetryに入れない。** Redactionのテストを通す。
5. **DB Migrationを含むRelease**は、その旨とRollback時の注意を記録する。**自動Deploymentでは配布できない。** `src/lib/db/migrations.ts`のSHA256（DB Schema Fingerprint）が稼働中のReleaseと異なると、Deployment Engineが切替前に拒否する。File全体のHashなので、コメントだけの変更でも変わる。このFileを変えるときは、Mergeの前にInfrastructureと配布方法を決める。Automatic RollbackはDBを戻さない。
6. **Artifactの形式**（Standaloneの`tar.gz`、`server.js`の位置）と待受（`127.0.0.1:3000`）を変えるときは、事前にInfrastructureと合意する。
7. **`infra/`と本番環境を変更しない。** 必要な変更はInfrastructureへ依頼する。
8. **Readinessの契約を保つ。** `GET /api/health/ready`は、準備ができたときに200と`{"status":"ready","release":"<Build SHA>"}`を返す。Deployment Engineはこの形式で合否を決める。形式を変えるときは、事前にInfrastructureと合意する。
9. **`GMS_BUILD_SHA`をBuild時に埋め込む。** Readinessが返すReleaseは、この値である。

## 6. 変更したときに更新するもの

| 変更 | 更新する文書 |
|---|---|
| 仕様 | Requirements、Use Cases、Entity Modelを先に更新し、その後に実装する |
| 設計判断 | `docs/design/`、またはTelemetry Schemaの「Design Decisions」 |
| Telemetryの定義 | Telemetry Schema |
| 検証結果 | 日付付きの検証記録を新しく作る。過去の記録は書き換えない |
| 本番に反映された事実 | Developmentでは書かない。Infrastructureの[production-state.md](../infrastructure/production-state.md)を参照する |
| テストの追加・削除 | 件数を文書に書いている場所（[Architecture §10.1](../architecture.md#101-pull-requestとci-quality-gate)、本書§3）を合わせる |
