# GMS v5 — Application Observability: Production Release Readiness

**Document Type:** Release Readiness / Deployment & Rollback Runbook
**Recorded:** 2026-11-13 (JST)
**Scope:** Application側のObservability実装（OTel SDK、業務Span、Metrics、Runtime Metrics、SQLite計装、Browser RUM受信、Redaction）
**Release delta:** 現行Production release `9783463dcdd3f3b10f743ab3e1488978b842b4be` からのアプリケーション差分は**本Commitのみ**（`9783463..10d3e51`はdocs / infraのみでアプリケーションコードの変更なし）
**Design:** [application-instrumentation.md](./application-instrumentation.md) / **Local verification:** [application-instrumentation-verification-2026-11-13.md](./application-instrumentation-verification-2026-11-13.md) / **As-To-Be:** [architecture-as-to-be-add-observability.md](../../architecture-as-to-be-add-observability.md)

> **Evidence policy:** 本書の結果はすべてローカル（macOS arm64、Node.js v26.9.0、Next.js 16.4.3 Webpack）での実行結果である。AWS、Production EC2、Terraform、実DeepL / OpenAI / Cognitoには接続・変更していない。Production（Linux ARM64 / systemd）での挙動、およびSRE側のProduction Collector（:8443）の設定は本書の範囲外であり、下記のGateで確認する。

> **Historical（2026-11-14追記）：** 本書はRelease前（2026-11-13）の判定と手順の記録であり、検証結果（§2〜§5）は変更していない。本書が対象としたApplication変更（Commit `75d5b17`）は、Release `364d0b7`として本番で稼働している。
>
> - 冒頭の「現行Production release `9783463`」は記録時点の値である。現在の本番Releaseは`364d0b7`で、`9783463`はRollback用に保持されている。
> - §1の判定、§6.3の設定例、§7のGateの状態は**Superseded**。結果は§10に記す。
> - §8のRollback手順は引き続き参照できる。
>
> 現在の本番状態は[production-state.md](../../infrastructure/production-state.md)を正とする。

---

## 1. 判定

| 対象 | 判定 | 条件 |
|---|---|---|
| **A. Application Releaseの本番Deploy（Telemetry無効＝既定）** | **Go** | 既存のARM64 Release手順（§6）に従う。`GMS_OTEL_ENABLED`未設定時はSDKを読み込まず、OTel APIはNo-op。DB Migrationなし |
| **B. Production Telemetry有効化（Server Traces / Metrics）** | **Conditional Go** | §7のGate G1〜G6をSRE / Platformが満たした後、§6.3の手順で有効化する |
| **C. Browser RUM** | **No-go（今回対象外）** | OD-10未承認。`build-arm64.yml`に`NEXT_PUBLIC_GMS_RUM_ENABLED`が無く、本番ArtifactではRUMは無効のまま |

> **Superseded（2026-11-14）：** AとBは実施済み、CはNo-goのまま変わらない。結果は§10.1を参照する。

## 2. 検証方法

- **Commit内容と同一のClean Copy**（`git ls-files -co --exclude-standard`、`.env*`・`.next`・`test-results`を含まない）を作成し、CI（`build-arm64.yml`）と同じ`typecheck → lint → build`を実行した。
- **比較Baseline：** Telemetry変更を含まないHEAD `10d3e51`（アプリケーションコードは現行Production `9783463`と同一）を同一手順でBuildし、E2Eを比較した。
- **TLS / 認証：** 検証用CA・サーバー証明書（SAN：`localhost`, `127.0.0.1`, `::1`）と、`authorization: Bearer <token>`を必須とするHTTPS OTLP Sink（:8443）を一時的に作成した。Production Collectorの実設定（TLS終端・認証方式）はリポジトリに記録が無いため、Application側の送信挙動のみを検証した。
- **Collector互換：** SREのローカル経路（`gms-otel-local` :4318 → `gms-tempo-local`）へ送信し、Tempoへの保存を確認した（設定変更なし）。

## 3. 検証結果

### 3.1 静的検査・Unit Test・Build

| Check | Result |
|---|---|
| `npm run typecheck` | Pass |
| `npm run lint`（Clean Copy＝CI条件） | **0 errors**、Warning 2件（既存`infra/terraform/v5/deepl-proxy-auth.js`） |
| `node --experimental-strip-types --test test/*.test.ts` | **114 / 114 pass**（既存83件 + Observability 31件） |
| `npm run build`（`output: "standalone"`） | Pass。`.next/standalone/server.js`生成 |
| `npm audit --omit=dev` | **0 vulnerabilities** |

開発作業ツリーでの`npm run lint`は、Git ignore対象だがESLint ignore対象外の`test-results/e2e-report/`（Playwright HTML Report）を走査して失敗する。CIには存在しないディレクトリであり、Application不具合ではない（§4）。

### 3.2 E2E回帰

| Spec | Baseline `10d3e51` | Candidate | 分類 |
|---|---|---|---|
| redirects unauthenticated users away from protected application screens | Pass | Pass | — |
| rejects an unauthenticated glossary API request | Pass | Pass | — |
| does not pretend Cognito is configured | Pass ※ | Pass ※ | **環境依存**（§4 E-2） |
| renders the landing page and navigates to login and registration | **Fail** | **Fail** | **既存の陳腐化したSpec**（§4 E-1） |

※ Clean Copy（`.env.local`無し）では成功。

E-1で失敗後に実行されない手順（LP表示 → ヘッダーの「ログイン」 → `/login` → 「新規登録」 → `/register`）は、陳腐化したSelectorのみ現行に合わせた一時スクリプトで両Buildを確認し、**同一結果（すべて成功）**だった。

### 3.3 既存機能への影響（実ブラウザ Translation E2E）

Candidate Standalone Build（CI成果物と同じくRUM無効）、一時SQLite（用語230件、暗号化済みダミーAPI Key）、ローカルMock DeepL / OpenAI、Playwright Chromiumで、`/translation` → Select All → Translate → 結果表示を実行した。

| Item | Telemetry無効 | Telemetry有効（HTTPS + Bearer + CA） |
|---|---|---|
| 画面操作 | 成功 | 成功 |
| HTTP Status | `GET /api/auth/me` 200、`GET /api/glossaries` 200、`GET …/terms` 200×5、`POST /api/translate` 200 | 同左 |
| Mock呼出し | glossary作成1・翻訳1・glossary削除1・chat/completions 1 | 同左 |
| 外部APIへの`traceparent` / `tracestate` / `baggage` | 0件 | 0件 |
| ブラウザの`traceparent`送信 | 0件（RUM無効） | 0件（RUM無効） |
| Collector受信 | — | `/v1/traces` 1回・`/v1/metrics` 3回、すべてBearer有効 |
| 期待Span / Metric名 | — | 17/17を検出（`POST /api/translate`、`gms.translation.request`、`gms.sqlite.read_translation_terms`、`SELECT terms`、`gms.credentials.load`、`gms.deepl.translate`、`gms.openai.post_edit`、`gms.translation.duration`、`gms.external.request.duration`、`db.client.operation.duration`、`http.server.request.duration`、Runtime / SQLite Metrics等） |
| Payload機密検査 | — | 原文・用語・中間訳・最終訳・用語集名・ダミーAPI Key・メール・Auth Subject・User ID・Session Cookie・Collector Token：**一致0件** |

### 3.4 OTLP HTTPS・認証ヘッダー・CA信頼

各シナリオでStandalone Serverを起動し、`/`、`/translation`、`/api/auth/me`、`/api/search?q=<Canary>`へアクセスした。全シナリオで業務応答は`200 200 401 401`（未認証のため401は想定どおり）、Export失敗後の`GET /`も200だった。

| # | 設定 | 修正前（`@vercel/otel` fetch Exporter） | 修正後 |
|---|---|---|---|
| A | CA未設定 | Traces / Metrics とも TLS失敗（破棄） | 同左 |
| B | `OTEL_EXPORTER_OTLP_CERTIFICATE=<CA>` のみ | **Metricsのみ到達、Traceは全件TLS失敗（無通知）** | **Traces / Metrics とも到達、Bearer有効** |
| C | `NODE_EXTRA_CA_CERTS=<CA>` | 両方到達 | 両方到達 |
| D | B + 誤Token + `OTEL_LOG_LEVEL=debug` | — | Collectorが401、アプリは継続。401は再試行しない |
| E | B + IPv6リテラル`https://[::1]:8443` | — | 両方到達 |
| F | B + SAN不一致の証明書 | — | `ERR_TLS_CERT_ALTNAME_INVALID`で拒否（ホスト名検証有効）、アプリは継続 |
| G | B + 正Token + `OTEL_LOG_LEVEL=debug` | — | 両方到達 |

- アプリのstdout（debugを含む全シナリオ）に、Collector Token・`Authorization`・`Cookie`・Query Canaryは**出力されなかった**。
- Content-Type：Traces=`application/x-protobuf`、Metrics=`application/json`。

### 3.5 Collector / Tempo互換

修正後のExporterでSREローカル経路へ送信し、Tempoに20 Trace（`GET /`、`GET /api/auth/me`、`GET /api/search`等）が保存された。`tempo_receiver_refused_spans`=0、Collectorのエラーログ0件。`GET /api/search?q=<Canary>`のTraceは`http.target=/api/search`（Query無し）、Canary一致0件。

### 3.6 機密情報除外（総括）

| 検証 | 結果 |
|---|---|
| Unit（Redaction、Fail Closed、Canary） | Pass |
| 本書§3.3 Payload検査 | 一致0件 |
| 本書§3.4 アプリログ検査 | 一致0件 |
| 本書§3.5 Tempo保存データ | Query無し・Canary 0件 |
| 前回SRE経路E2E（[検証記録§8](./application-instrumentation-verification-2026-11-13.md)、57 Trace / 288 Span） | 一致0件 |

## 4. 既知のE2E環境問題と実装不具合の切り分け

| ID | 事象 | 原因 | 分類 | 対応 |
|---|---|---|---|---|
| E-1 | `renders the landing page…`が失敗（Title `/GMS v5/`、`Glossary Management System`、`nav[aria-label="公開認証メニュー"]`） | CR-02（`de30d5a`、承認済みLP）でTitle・文言・Navが変更されたが、Specが未更新。Baselineでも同一失敗 | **既存Specの陳腐化**（Observability変更と無関係） | 本Commitでは変更しない。CR-02の確定文言（[Use Cases](../../use-cases.md)）に合わせたSpec更新を別変更で行うこと |
| E-2 | `does not pretend Cognito is configured`が開発ツリーでのみ失敗 | Playwrightの`webServer`（`npm start`）が開発者の`.env.local`（Cognito設定）を読み込む | **テスト環境依存** | Clean環境ではBaseline / Candidateとも成功。Hermeticな`webServer`環境の整備を別変更で検討 |
| E-3 | 開発ツリーで`npm run lint`が239 errors | `test-results/e2e-report/`（生成物）がESLint対象 | **テスト環境依存** | CI条件（Clean Copy）では0 errors。`eslint.config.mjs`の`globalIgnores`へ`test-results/**`追加を別変更で検討 |
| I-1 | `OTEL_EXPORTER_OTLP_CERTIFICATE`設定時にTraceのみ無通知で欠落 | Trace Exporter（`@vercel/otel` `OTLPHttpProtoTraceExporter`）がGlobal `fetch`で送信し、証明書関連の環境変数を読まない | **実装不具合** | **本Commitで修正**（§5） |
| I-2 | `OTEL_EXPORTER_OTLP_TRACES_HEADERS`が`OTEL_EXPORTER_OTLP_HEADERS`で上書きされ得る | アプリが`OTEL_EXPORTER_OTLP_HEADERS`を明示指定していたため、OTel仕様のSignal別優先順位が逆転 | **実装不具合** | **本Commitで修正**（§5） |

ローカル統合試験中に修正済みの不具合（`next dev --webpack`の`node:fs` UnhandledSchemeError、Span時刻比較のFlaky Test）は[検証記録§4・§7](./application-instrumentation-verification-2026-11-13.md)を参照。

## 5. 本Readiness確認で行った修正

| File | Change |
|---|---|
| `src/lib/observability/exporters.ts`（新規） | Traces=`OTLPTraceExporter`（`@opentelemetry/exporter-trace-otlp-proto` 0.223.2）、Metrics=`OTLPMetricExporter`。両者とも`@opentelemetry/otlp-exporter-base`のNode.js HTTP Transportを使用し、ヘッダ・CA・mTLS・圧縮の環境変数を同一仕様で解釈 |
| `src/lib/observability/node-sdk.ts` | 上記Factoryを使用。`registerOTel`・Sampler・Propagator・Redaction・SpanProcessor構成は不変 |
| `src/lib/observability/config.ts` | 未使用となったヘッダ解析を削除（ヘッダはExporterが仕様どおり読む） |
| `package.json` / `package-lock.json` | `@opentelemetry/exporter-trace-otlp-proto@^0.223.2`を追加。依存（`otlp-exporter-base` / `otlp-transformer` 0.223.2、`sdk-trace` 2.12.1）は導入済みと同一版で、Lockfileの追加は本Package 1件のみ |
| `test/observability.test.ts` | Exporter契約テスト2件（Protobuf / JSON、ヘッダ優先順位、401で例外を出さず失敗扱い・再試行なし） |
| `.env.example`、設計書（DD-12、§2、§7、§8、§9） | 設定項目と設計判断を反映 |

## 6. Production Deployment手順

既存の手動Release手順（[Production As-Built 2026-10-26 §6.4](../../infrastructure/production-as-built-2026-10-26.md)、[2026-11-08 §4](../../infrastructure/production-as-built-2026-11-08.md)）に従う。**Telemetryは段階的に有効化し、Deploy（6.2）と有効化（6.3）を別の変更として実施する。**

### 6.1 事前確認

1. 本CommitがPlatform TeamによりPush・Review済みであること（本作業ではPushしていない）。
2. Rollback資産：現行release `/opt/gmsv5/releases/9783463…`（または同等の旧releaseディレクトリ）、その成果物とSHA256、`/etc/gmsv5/gmsv5.env`とsystemd Drop-inのバックアップが揃っていること。
3. 本ReleaseにDB Migrationは無い。通常運用のオンラインバックアップ方針に従う。

### 6.2 Phase A — Release切替（Telemetry無効）

1. GitHub Actions `Build ARM64 Release`を本Commitで実行（`npm ci`、Typecheck、Lint、Build、Standalone梱包、SHA256）。
2. ArtifactをMacへ取得し、SHA256を検証。
3. Release番号別パスへS3アップロード。
4. SSMでEC2 #1へ接続し、S3 Dualstack Endpointから取得、SHA256を再検証。
5. `/opt/gmsv5/releases/<release-sha>`へ展開。
6. `/etc/gmsv5/gmsv5.env`に`GMS_OTEL_ENABLED`が**無い（または`false`）**ことを確認。
7. systemd Drop-inで対象releaseを指定し、`systemctl daemon-reload` → `systemctl restart gmsv5.service`。
8. Readiness（Retry付き）：`curl -fsS http://127.0.0.1:3000/`、公開URLのLP、ログイン後の`/translation`表示・Select All・翻訳を既存手順で確認。
9. `journalctl -u gmsv5.service`に`Telemetry initialisation failed`が**無い**ことを確認（無効時はSDKを読み込まないため出力されない）。

### 6.3 Phase B — Telemetry有効化（§7 Gate充足後）

> **Superseded（2026-11-14）：** 以下は、SRE側の本番設定が決まる前に書いた手順案である。実際の構成は認証Header、CAの配置、TokenのParameter名が異なる（§10.2）。作業の流れ（CA配置、Token、環境変数、再起動、受信確認）は参考として残す。

1. **CA配置：** Collectorの証明書を署名したCA（公開証明書のみ）を`/etc/gmsv5/otel-collector-ca.pem`に配置。`root:gmsv5 0640`（実行ユーザー`gmsv5`が読めること）。
2. **Token：** Collector認証TokenはSSM Parameter Store SecureString（例：`/gmsv5/otel/collector-token`）で管理し、`/etc/gmsv5/gmsv5.env`（`root:root 600`）へ供給する。値をShell履歴・ログ・Ticketに残さない。
3. **`/etc/gmsv5/gmsv5.env`へ追記：**

   ```bash
   GMS_OTEL_ENABLED=true
   OTEL_EXPORTER_OTLP_ENDPOINT=https://<collector-host-or-[ipv6]>:8443
   OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20<token>   # 値はURLエンコード（空白=%20）
   OTEL_EXPORTER_OTLP_CERTIFICATE=/etc/gmsv5/otel-collector-ca.pem
   GMS_RELEASE_VERSION=<release-sha>
   GMS_DEPLOYMENT_ENVIRONMENT=production
   # 任意（OD-07決定後）
   # OTEL_TRACES_SAMPLER=parentbased_traceidratio
   # OTEL_TRACES_SAMPLER_ARG=<ratio>
   ```

   - `OTEL_LOG_LEVEL`は設定しない（診断時のみ一時的に`warn`）。`debug`はSpan内容を大量に出力するため本番では使用しない。
   - `GMS_RUM_ENABLED`は設定しない（判定C）。
   - Endpointのホスト名（またはIPv6アドレス）はCollector証明書のSANに含まれている必要がある（不一致は`ERR_TLS_CERT_ALTNAME_INVALID`で全件破棄）。
4. `systemctl restart gmsv5.service` → 6.2-8と同じReadiness確認。
5. **受信確認（SRE）：** Collectorの`otelcol_receiver_accepted_spans` / `…_metric_points`増加、`otelcol_receiver_refused_*`=0、Trace Backendで`service.name=gms-v5`・`service.version=<release-sha>`のTraceを確認。
6. **機密確認（SRE + Application）：** 翻訳1件分のTraceについて`http.target`にQueryが無いこと、原文・訳文・用語・API Key・Cookieが無いことを確認。
7. **性能・安定性：** 有効化前後で翻訳・Select Allの応答時間、`gmsv5.service`のRSS / CPUに有意な悪化が無いこと（基準はPlatformが定める。本書時点で計測値なし）。

## 7. Telemetry有効化Gate（SRE / Platform）

> **Superseded（2026-11-14）：** 下表の「状態」は2026-11-13時点のものである。現在の結果は§10.1を参照する。

| Gate | 内容 | 状態（本書時点） |
|---|---|---|
| G1 | Production CollectorがOTLP/HTTPSを:8443で受信し、`/v1/traces`（protobuf）・`/v1/metrics`（JSON）を受け付ける | 未確認（Security Group規則のみ`10d3e51`でTerraformに追加。apply状態・受信設定は未記録） |
| G2 | Collectorが認証（Bearer等）を強制し、Tokenが発行・SSMに保管済み | 未確認 |
| G3 | Collector証明書のCAとSAN（Endpointのホスト名 / IPv6）が確定し、CAがEC2 #1へ配布可能 | 未確認 |
| G4 | **Trace Backend（Tempo等）がProduction側に存在すること。** EC2 #2の現行Trace Pipelineは`debug` exporterのみ（[Observability As-Built §4.1](../../infrastructure/observability-as-built-2026-11-13.md)） | **未充足** |
| G5 | EC2 #1 → EC2 #2:8443のIPv6到達性（`curl -v --cacert … https://…:8443/v1/traces`で401/405等の応答） | 未確認 |
| G6 | Collector側の多層Redaction（Query・`db.query.text`・`exception.message`等の除去）を推奨（[検証記録§6.2](./application-instrumentation-verification-2026-11-13.md)） | 未確認（推奨） |

## 8. Rollback手順

### 8.1 判断基準

- Readiness失敗、5xx増加、翻訳 / Select Allの失敗、応答時間の有意な悪化、メモリ増加、機密情報の送信疑い。

### 8.2 R1 — Telemetryのみ無効化（Phase B起因、最優先・数分）

1. `/etc/gmsv5/gmsv5.env`の`GMS_OTEL_ENABLED=true`を削除（または`false`）。
2. `systemctl restart gmsv5.service` → Readiness確認。
3. 効果：SDKを読み込まず、Export・計装は完全に停止する。DB・ユーザーデータへの影響なし。
4. 機密情報の送信が疑われる場合：SREへ連絡し、Collector / BackendのPipeline停止と該当データの削除を依頼する。

### 8.3 R2 — Releaseを旧版へ戻す（Phase A起因）

1. systemd Drop-inを旧release（`/opt/gmsv5/releases/9783463…`）へ戻し、`systemctl daemon-reload` → `systemctl restart gmsv5.service`。
2. Readiness確認（LP、ログイン、Glossary、Select All、翻訳）。
3. **DB復元は不要**：本ReleaseはSchema・Migrationを変更しない。Telemetry有効時もSQLiteへの書込は追加しない（RUM受信は無効）。
4. Telemetry関連の環境変数は旧releaseでは参照されないが、混乱を避けるため削除する。
5. 結果（Command ID、切替先SHA、Readiness出力）をAs-Built記録へ残す。

## 9. 未検証事項・残存リスク

- Linux ARM64 / Amazon Linux 2023 / systemd上での起動・メモリ・CPU（ローカルのみ）。
- 実Production CollectorのTLS終端・認証方式・証明書（G1〜G3）、EC2間のIPv6経路（G5）。
- 実DeepL（CloudFront Proxy）/ OpenAI / Cognito経由のSpan・Metrics（ローカルはMockのみ）。
- 計測オーバーヘッド、高負荷時のQueue溢れ・破棄率、Production Baseline・SLO。
- CloudFront / Nginxでの`traceparent`透過（RUM無効のため今回は影響なし）。
- `@opentelemetry/*` 0.223.2は2026-11-12公開。採用済みの同系列Package（`exporter-metrics-otlp-http`等）と同一Releaseで揃えている。

## 10. Release後の状態（2026-11-14追記）

根拠は[Observability As-Built](../../infrastructure/observability-as-built-2026-11-13.md)の§11〜§12である。この節はその転記であり、Application Teamが本番を再検証した結果ではない。

### 10.1 判定とGateの結果

| 対象 | 2026-11-13の判定 | 2026-11-14の結果 |
|---|---|---|
| A. Application Releaseの本番Deploy | Go | 実施済み。Release `364d0b7`が本番で起動し、HTTP Readinessは200。Telemetry無効の状態での切替を独立した段階として行ったかは記録が無い |
| B. Production Telemetry有効化 | Conditional Go | 実施済み。Server TracesとMetricsを本番で確認 |
| C. Browser RUM | No-go | 変更なし。本番では無効 |

| Gate | 結果 | 根拠（Observability As-Built） |
|---|---|---|
| G1 OTLP/HTTPSの受信 | 充足。Nginx GatewayがIPv6 TCP 8443で`POST /v1/traces`と`POST /v1/metrics`を受け付け、Collectorへ中継する | §11.3、§12 |
| G2 認証とToken | 充足。方式はBearerではなくHeader `X-OTLP-Token`。TokenはSSM SecureStringで、Production EC2からの取得を確認 | §11.3、§12 |
| G3 CAとSAN | 充足。Private CA、SANはIPv6アドレス。CA公開証明書は`/etc/ssl/certs/gmsv5-otel-ca.pem`に配置 | §11.3、§12 |
| G4 Trace Backend | 充足。Tempoを導入し、CollectorからOTLP/gRPCで送る | §11.1、§11.2 |
| G5 IPv6到達性 | 充足 | §11.4、§11.5、§12 |
| G6 Collector側の多層Redaction（推奨） | 記録なし。As-Built §11.2が記載するPipelineは`memory_limiter`と`batch`のみ | §11.2 |

### 10.2 手順案と実際の構成の差

§6.3の設定例と本番の実際の構成の違い（認証Header、TLS終端の場所、CAの配置、TokenのParameter名）は、[production-state.md §8](../../infrastructure/production-state.md)にまとめてある。本番の設定はそちらを正とする。

Application側の実装は変更を要しない。ExporterはHeader名を固定しておらず、`OTEL_EXPORTER_OTLP_HEADERS`などに書かれたHeaderをそのまま送る。

### 10.3 本書の手順・未検証事項のその後

| 項目 | 状況 |
|---|---|
| §9：Linux ARM64 / systemdでの起動 | 本番で確認済み（As-Built §12）。メモリとCPUの記録は無い |
| §9：実Collector、EC2間のIPv6経路 | 本番で確認済み（As-Built §11、§12） |
| §9：実DeepL / OpenAI経由のSpanとMetrics | 本番で確認済み（As-Built §12）。実Cognitoの記録は無い |
| §6.3-6：本番Traceの機密確認 | 実施の記録が無い |
| §6.3-7：有効化前後の性能・RSS・CPUの比較 | 実施の記録が無い |
| §8.2：R1（Telemetryのみ無効化）の本番での実施 | 実施の記録が無い。Rollbackは不要だった（As-Built §12） |
| §9：計測オーバーヘッド、高負荷時の破棄率、Baseline、SLO | 未検証のまま |
| §9：CloudFront / Nginxでの`traceparent`透過 | 未検証のまま（RUM無効のため影響なし） |

### 10.4 Development側に残る課題

§4のE-1（LPのE2E Specの陳腐化）、E-2（E2Eの`webServer`が`.env.local`を読む）、E-3（開発ツリーのLintが`test-results/`を走査する）は未対応である。いずれも本Releaseの対象外としたもので、別の変更で扱う。
