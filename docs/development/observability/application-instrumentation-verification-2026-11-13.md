# GMS v5 — Application Instrumentation: Local Verification and SRE Handoff

**Document Type:** Verification Evidence / Handoff
**Recorded:** 2026-11-13 (JST)
**Scope:** Application-side instrumentation only — local workstation
**Design:** [application-instrumentation.md](./application-instrumentation.md)

> **Evidence policy:** すべてローカル検証の結果である。AWS（EC2 #1 / EC2 #2）、Production、実DeepL / OpenAI APIには一切接続・変更していない。外部APIはローカルMockで代替した。Production適用時の挙動は本書の範囲外であり、SREによる別途検証が必要。

> **Historical（2026-11-14追記）：** 本書は2026-11-13のローカル検証の記録であり、本文は変更していない。その後、Release `364d0b7`が本番で稼働し、Server Traces / Metricsが本番で確認された（[Observability As-Built §12](../../infrastructure/observability-as-built-2026-11-13.md#12-production-phase-1-acceptance)）。
>
> - **§5「Not Verified」：** 本番ARM64 / systemdでの起動、EC2間の経路・認証・TLS・IPv6到達性、実DeepL / 実OpenAIのSpan、Tempo / Prometheus / Grafanaでの表示は、本番で確認済みである。本番での性能とメモリ、実Cognito、`traceparent`のCloudFront / Nginx透過、Prometheus上のMetric名の一覧、高負荷時の挙動、Chromium以外のRUM、Sampling Rate < 1 は、確認の記録が無い。
> - **§6「SRE Handoff」：** 設定例は当初のものである。本番の実際の構成は[production-state.md §8](../../infrastructure/production-state.md)を参照する。
>
> 現在の本番状態は[production-state.md](../../infrastructure/production-state.md)を正とする。

---

## 1. Verification Environment

| Item | Value |
|---|---|
| OS / Arch | macOS (Darwin 27), arm64 |
| Node.js | v26.9.0 |
| Next.js | 16.4.3, `next build --webpack`, `output: "standalone"`（`.next/standalone/server.js`で起動） |
| OTel | `@vercel/otel` 2.3.0, `@opentelemetry/sdk-trace-base` / `sdk-metrics` 2.12.1, `exporter-metrics-otlp-http` 0.223.2 |
| Collector | `otel/opentelemetry-collector:0.165.0`（EC2 #2と同一バージョン）。検証専用コンテナ、`debug` exporter、traces + metrics pipeline、`127.0.0.1:4328` |
| External APIs | ローカルMock HTTPサーバー（`DEEPL_API_URL` / `OPENAI_API_URL`を`http://127.0.0.1:4555/...`に設定）。**課金リクエスト0件** |
| Browser | Playwright Chromium（headless）、検証用Session Cookieを注入 |
| Data | 一時SQLite（検証用ユーザー1名、用語230件、暗号化済みダミーAPI Key） |
| Telemetry env | `GMS_OTEL_ENABLED=true`, `GMS_RUM_ENABLED=true`, build時`NEXT_PUBLIC_GMS_RUM_ENABLED=true`, `GMS_OTEL_METRICS_EXPORT_INTERVAL_MS=5000` |

既存の開発用Collector（`gms-otel-local`, :4318）には変更を加えていない。

## 2. Automated Tests

| Command | Result |
|---|---|
| `node --experimental-strip-types --test test/*.test.ts` | **107 / 107 pass**（既存83件 + 新規24件）。§7の修正後は **112 / 112 pass** |
| `npm run typecheck` | Pass |
| `npm run lint` | 0 errors（既存の`infra/terraform/v5/deepl-proxy-auth.js` Warning 2件のみ。変更前と同一） |
| `npm run build` | Pass（`/api/telemetry/rum`が追加） |
| `npx playwright test`（Telemetry無効） | 2 pass / **2 fail — 既存要因**（下記） |
| `npm audit` | moderate 7件（ESLint系dev依存のminimatch）。**追加依存の導入前後で同一** |

**E2E失敗の要因（本変更と無関係）：**

1. `renders the landing page…` — Specは`/GMS v5/`を期待するが、`src/app/page.tsx`のTitleは2026-11-08のコミット`de30d5a`以降「専門用語の翻訳を、もっと思いどおりに。｜GMS」。
2. `does not pretend Cognito is configured` — `npm start`が`.env.local`（Cognito設定あり）を読み込むため`/api/auth/login`が200。環境依存。

## 3. Runtime Verification (Telemetry Enabled)

ブラウザ操作：`/translation` → Glossary展開 → **Select All（230件, 5ページ並列取得）** → 原文入力・同意 → **Translate** → 結果表示 → ページ離脱（RUM Flush）。続けて`/api/auth/callback?code=…&state=…`と`/api/search?q=<用語>`へアクセス。

### 3.1 Translation — End-to-End Trace（Collector受信データから再構成）

```text
gms.rum.translation            {gms.telemetry.source=browser, gms.action.outcome=success, gms.page.route=/translation}
└─ POST /api/translate         {http.route=/api/translate, http.status_code=200}
   └─ executing api route (app) /api/translate
      ├─ CREATE schema_migrations / SELECT schema_migrations / SELECT users
      └─ gms.translation.request {selected_term_count=230, effective_term_count=230, source_text_length=27, outcome=success}
         ├─ gms.translation.validate
         ├─ gms.sqlite.read_translation_terms
         │  └─ SELECT terms
         ├─ gms.translation.resolve_terms
         ├─ gms.credentials.load
         │  ├─ SELECT api_credentials
         │  └─ SELECT api_credentials
         ├─ gms.deepl.translate {gms.provider=deepl}
         │  ├─ fetch POST …/v2/glossaries      {http.status_code=200}
         │  ├─ fetch POST …/v2/translate       {http.status_code=200}
         │  └─ fetch DELETE …/v2/glossaries/g-1 {http.status_code=204}
         └─ gms.openai.post_edit {gms.provider=openai}
            └─ fetch POST …/v1/chat/completions {http.status_code=200}
orphans: none
```

- ブラウザが送信した`traceparent`（`00-ec1f…082d-c6ad…6c7f-01`）と`gms.rum.translation`のTrace ID / Span IDが一致し、Server Spanがその子になった。
- Mock DeepL / OpenAIへのリクエストに`traceparent`は付与されていない（外部へ伝播しない）。

### 3.2 Select All — Trace

`gms.rum.select_all`（Root, outcome=success）の下に`GET /api/glossaries/[glossaryId]/terms`が並列4件＋検証1件、各々に`SELECT terms`等のSQLite Span。Orphan無し。

### 3.3 Metrics（Collector受信）

| Metric | 受信した属性の例 |
|---|---|
| `http.server.request.duration` | `POST /api/translate 200`, `GET /api/glossaries/[glossaryId]/terms 200`×5, `GET /api/auth/callback 303`, `GET /api/search 200`, `POST /api/telemetry/rum 204`×2 |
| `gms.translation.duration` / `gms.translation.selected_terms` | `outcome=success` |
| `gms.external.request.duration` | `deepl:glossary_create:200`, `deepl:translate:200`, `deepl:glossary_delete:204`, `openai:post_edit:200`（`server.address=127.0.0.1`） |
| `db.client.operation.duration` | `SELECT terms`（10回）ほか |
| `gms.rum.user_action.duration` | `translation/success`, `select_all/success` |
| `gms.rum.web_vital.{lcp,inp,cls,fcp,ttfb}` | `gms.page.route=/translation`, `rating=good` |
| `nodejs.eventloop.delay.*`, `process.memory.usage`, `process.uptime`, `gms.sqlite.file.size{db}`, `gms.sqlite.filesystem.available` | 受信 |

Resource属性：`service.name=gms-v5`, `service.namespace=gms`, `service.version`, `deployment.environment.name`, `gms.telemetry.schema_version=1`。

### 3.4 Sensitive Data Scan

Collectorが受信した全出力（Traces + Metrics）を以下で検索し、**一致0件**：

- 原文・用語230件・ダミーAPI Key・Mockの中間訳/最終訳（すべて`VERIFY-`接頭辞のCanary）
- `code=`, `state=`, `?q=`, `sk-…`, `gms_session`, `DeepL-Auth-Key`, `Bearer …`

`http.target`の全値：`/api/auth/callback`, `/api/auth/me`, `/api/glossaries`, `/api/glossaries/<id>/terms`, `/api/search`, `/api/translate`, `/glossary`, `/translation`（Query無し）。アプリのstdoutにもCanaryは出力されなかった。

### 3.5 Failure Isolation（Collector停止）

| 条件 | `POST /api/translate`（5 terms） |
|---|---|
| Collector稼働 | 200 / 0.26s, 0.26s |
| Collector停止直後 ×5 | 200 / 0.256–0.260s |
| 停止後12秒（Export失敗後） | 200 / 0.259s、`GET /` 200 |

（所要時間の大半はMock側の意図的遅延 約200ms。）`OTEL_LOG_LEVEL`未設定ではExport失敗はログに出ない（確認済み）。`warn`設定時は`ECONNREFUSED`とMetrics Export Timeoutのみ出力され、Payloadは含まれない。

### 3.6 Telemetry Disabled（既定構成）

`GMS_OTEL_ENABLED`未設定・`NEXT_PUBLIC_GMS_RUM_ENABLED`未設定のビルドで同一ブラウザ操作を実施：Select All / Translation正常完了、`traceparent`ヘッダ無し、RUM送信無し。

## 4. Issues Found and Fixed During Verification

| Issue | Cause | Fix |
|---|---|---|
| `gms.rum.*` SpanがブラウザのTrace IDではなくランダムIDで生成された | Next.jsが`instrumentation.ts`（IdGenerator保持）とRoute Handlerを別Webpack Chunkにバンドルし、`tracing.ts`のModule-level `AsyncLocalStorage`が2つ存在 | `globalThis[Symbol.for(...)]`でProcess-wide Singleton化。別Module Instanceで共有されることを回帰テスト化 |
| `GMS_OTEL_ENABLED=true`の`next dev --webpack`で`/instrumentation`のコンパイルが`UnhandledSchemeError: node:fs`で失敗し`GET /` 500（§7） | `instrumentation.ts`が`if (NEXT_RUNTIME !== "nodejs") return;`の**後**で`import("./lib/observability/node-sdk")`していた。Edge向けコンパイルでWebpackはEarly Return後のコードを除去せず、`node-sdk.ts` → `runtime-metrics.ts` → `node:fs`を解決しようとした。Middleware等のEdge Entryが無いProduction Buildでは顕在化しなかった | Next.js公式パターン（`if (process.env.NEXT_RUNTIME === "nodejs") { await import(…) }`）へ変更。AST検査とRuntime別の無効化条件を回帰テスト化 |
| `produces the business span tree…`が約2割の確率で失敗（`DeepL completes before OpenAI starts`） | OTel SDKはSpan開始時刻を`Date.now()`（1ms分解能）、終了時刻を開始＋`performance.now()`差分で算出するため、別Span間の時刻比較はms未満では順序が保証されない（`@opentelemetry/sdk-trace` `Span.js`）。修正前のテストでも10回中2回失敗を確認 | 比較に1ms（`Date.now()`分解能）の許容幅を設定。修正後20回連続pass |
| Idle時のEvent Loop Delayが約21msと報告された | Node 26の`monitorEventLoopDelay`は記録値にサンプリング分解能（20ms）を含む（実測：idle min 19.5ms、500msブロック→515ms） | 分解能を差し引いて報告。単体テスト追加 |

## 5. Not Verified

- Production EC2 #1 / ARM64 / systemd環境での起動・性能・メモリ（ローカルRSS約116MBは参考値に過ぎない）
- EC2 #1 → EC2 #2のネットワーク経路、認証、TLS、IPv6到達性
- 実DeepL（専用CloudFront Proxy経由）/ 実OpenAI / 実Cognitoでの挙動とレイテンシ
- CloudFront / Nginx経由での`traceparent`ヘッダ透過
- Tempo / Prometheus / Grafanaでの表示（今回はdebug exporterのみ）
- PrometheusにおけるMetric名変換後の正確な名称
- 高負荷時のQueue溢れ・Span破棄率、計測オーバーヘッド
- Safari / Firefox / モバイルでのRUM（Chromiumのみ確認）
- Sampling Rate < 1 での挙動（単体テストのみ）

## 6. SRE Handoff

### 6.1 Production有効化に必要な設定（EC2 #1 `gmsv5.service`）

> **Superseded（2026-11-13）：** 本番の有効化手順・環境変数（OTLP/HTTPS :8443、`OTEL_EXPORTER_OTLP_CERTIFICATE`、Rollback）は[Release Readiness](./application-release-readiness-2026-11-13.md) §6〜§8を正とする。以下は当初のHandoff記録として保持する。

```bash
GMS_OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=<private Collector endpoint, e.g. https://…:4318>
OTEL_EXPORTER_OTLP_HEADERS=<auth header; keep in a root-only EnvironmentFile>
GMS_RELEASE_VERSION=<git sha of the release artifact>
GMS_DEPLOYMENT_ENVIRONMENT=production
# Optional
OTEL_TRACES_SAMPLER=parentbased_traceidratio   # OD-07
OTEL_TRACES_SAMPLER_ARG=<ratio>
GMS_RUM_ENABLED=true                            # OD-10 承認後
```

Browser RUMはビルド時変数（`NEXT_PUBLIC_GMS_RUM_ENABLED=true`, `NEXT_PUBLIC_GMS_RUM_SAMPLE_RATE`）が必要。現行`build-arm64.yml`には未設定 → **RUMを有効化する場合はビルドワークフローへの追加が必要**（本作業ではCI/CDを変更していない）。

### 6.2 Collector / Backend

1. **Trace Backend（Tempo等）をProduction接続前に導入すること。** アプリ側でRedaction済みだが、EC2 #2 As-Built §9-5の方針どおり`debug` exporterへの本番送信は避ける。
2. 受信プロトコル：OTLP/HTTP。Traces = `application/x-protobuf`（`/v1/traces`）、Metrics = JSON（`/v1/metrics`）。現行Collectorの`otlp` http receiverで両方受信可能（ローカルで確認済み）。
3. **多層防御として**Collectorに`attributes`/`transform` processorでの除去を推奨：`http.target`/`http.url`/`url.full`のQuery、`db.query.text`、`db.statement`、`http.request.header.*`、`exception.message`。
4. Metrics temporalityはCumulative。Export間隔30秒（scrape 30秒と整合）。
5. Prometheus名称は`prometheus` exporterの設定（suffix付与等）に依存する。期待例：`http_server_request_duration_seconds_bucket`、`gms_translation_duration_seconds_*`、`gms_external_request_duration_seconds_*`、`db_client_operation_duration_seconds_*`、`gms_rum_user_action_duration_seconds_*`。**実データで確認のうえDashboardを作成すること。**
6. Metric Label候補（すべて有界）：§5の属性のみ。`http.route`はRoute Templateまたは`unmatched`。

### 6.3 Network / Edge

1. **CloudFront（公開Ingress）が`traceparent`リクエストヘッダをOriginへ転送するか確認すること。** Origin Request Policyで転送されない場合、Browser→ServerのTrace結合が切れる（機能影響なし）。
2. Nginxは`traceparent`を除去せず、Access Logに`$http_traceparent`を追加するとNginx Timing LogとTraceを結合できる（As-To-Be §5.4）。
3. `/api/telemetry/rum`に対しNginx `limit_req`と`client_max_body_size 16k`相当の制限を推奨（アプリ側も16KB・20イベント/リクエスト上限・認証必須）。
4. Production → ObservabilityのOTLP通信は、As-Built §3のとおり未公開ポート。認証・TLS・Security Group（OD-06）はSRE決定事項。

### 6.4 Sampling / Volume

- 既定は全Trace記録（`parentbased_always_on`）。Production量に応じて`parentbased_traceidratio`を設定。HTTP MetricsはSamplingの影響を受けない設計。
- ブラウザ由来`traceparent`のsampledフラグはクライアントが制御できる。濫用対策はCollectorのTail Sampling / Rate Limitで。
- SQLite Spanは1 Trace当たり100件で打切り（超過時、親Spanに`gms.sqlite.spans_suppressed=true`）。

### 6.5 Alerting / Dashboard候補（As-To-Be §7, §8.1）

| Dashboard | Metric |
|---|---|
| A — User Experience | `gms.rum.user_action.duration`（p50/p95, outcome別件数）、`gms.rum.web_vital.*`、`http.server.request.duration`件数 |
| B — Application Tracing | Tempo（`service.name=gms-v5`）、`gms.external.request.duration`、`db.client.operation.duration`、`gms.translation.duration{outcome}` |
| C — Infrastructure & DB | `nodejs.eventloop.delay.*`、`process.memory.usage`、`process.uptime`（リセット=再起動）、`gms.sqlite.file.size{db,wal}`、`gms.sqlite.filesystem.available` |
| D — Edge & External | `gms.external.request.duration{gms.provider, http.response.status_code, error.type}`（429/5xx/network_error）、`server.address`でDeepL Proxyと直接接続を区別 |

- **Telemetry途絶の検知：** アプリ側はExport失敗を（既定では）ログに出さないため、Collector側で`otelcol_receiver_accepted_spans` / `…_metric_points`の`service.name=gms-v5`由来の途絶、またはPrometheusの`absent()`で検知すること。
- Translation Success Rate（SLI候補）：`gms.rum.user_action.duration_count{gms.action.name="translation"}`をoutcome別に。`conflict`はユーザー操作待ちであり失敗として扱わないことを推奨（§8.1）。サーバー側は`gms.translation.duration{gms.translation.outcome}`で`client_error` / `dependency_error` / `server_error`を区別可能。

### 6.6 アプリ側で観測された事実（参考）

- 全APIリクエストで`runMigrations`（`CREATE schema_migrations` / `SELECT schema_migrations`）が実行されている。性能影響の評価はBaseline取得後に実施予定。
- DeepL翻訳1回につき外部HTTPは3回（Glossary作成・翻訳・Glossary削除）。用語0件時は1回。

---

## 7. Re-verification — `next dev --webpack` instrumentation build fix

**Recorded:** 2026-11-13 (JST)。検証専用Collector（`otel/opentelemetry-collector:0.165.0`, `debug` exporter, `127.0.0.1:4328`）を一時起動して受信を確認し、検証後に停止した。既存の`gms-otel-local` / Tempo / Grafana、AWS、Terraformには変更を加えていない。外部APIへのリクエストは0件。

### 7.1 再現（修正前）

`GMS_OTEL_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4328 npm run dev -- -p 3001` → `Module build failed: UnhandledSchemeError: Reading from "node:fs" is not handled by plugins`（Import trace: `node:fs` ← `runtime-metrics.ts` ← `node-sdk.ts`）、`GET / 500`。

### 7.2 修正

`src/instrumentation.ts`のみ。判定順序と条件は不変（`NEXT_RUNTIME === "nodejs"`かつ`GMS_OTEL_ENABLED === "true"`の場合のみ`node-sdk`を読み込む。初期化失敗はエラー名のみログしてアプリ継続）。`node-sdk.ts`以下（Redaction、Business Spans、Metrics、Runtime Metrics）は変更なし。

### 7.3 結果

| Check | Result |
|---|---|
| `next dev --webpack`（Telemetry有効, port 3001） | コンパイルエラー無し。`GET /` 200、`/translation` 200、`/api/auth/me` 401（未認証、想定どおり） |
| Collector受信（dev） | Trace：`GET /`、`render route (app) /`、`GET /translation`、`executing api route (app) /api/auth/me`、`SELECT schema_migrations`等（`service.name=gms-v5`）。Metrics：`http.server.request.duration`、`db.client.operation.duration`、`nodejs.eventloop.delay.{p50,p99,max}`、`process.memory.usage`、`process.uptime`、`v8js.memory.heap.used`、`gms.sqlite.file.size`、`gms.sqlite.filesystem.available` |
| Redaction（dev） | `/api/search?q=<Canary>`、`/api/auth/callback?code=<Canary>&state=<Canary>`送信後、Collector出力のCanary一致0件。`http.target`は`/`, `/translation`, `/api/auth/me`, `/api/search`, `/api/auth/callback`（Query無し） |
| `npm run build`（Standalone） | Pass |
| `.next/standalone/server.js`（Telemetry有効, 一時SQLite） | 起動成功。`GET /` 200、`/api/auth/me`・`/api/search?q=<Canary>` 401。Trace受信：`register instrumentation`、`GET /api/auth/me`、`GET /api/search`、`CREATE`/`INSERT`/`SELECT schema_migrations`等のSQLite Span。Runtime / SQLite Metrics受信。Canary一致0件 |
| `.next/standalone/server.js`（`GMS_OTEL_ENABLED`未設定） | `GET /` 200、`/translation` 200。15秒間のCollector受信0件 |
| Tests | **112 / 112 pass**（既存107件 + 新規5件：AST検査1件、無効化条件4件） |
| `npm run typecheck` | Pass |
| `npm run lint` | `test-results/e2e-report/`（Playwright HTML Report、git ignore対象だがESLint ignore対象外）を走査して239 errors。**ソースコードは`npx eslint --ignore-pattern "test-results/**"`で0 errors**（既存Warning 2件のみ）。Reportは18:10のE2E実行で生成されたもので本修正とは無関係 |

**補足：**

- `GMS_DATABASE_PATH`未設定のStandalone起動ではAPIが`503 DATABASE_NOT_CONFIGURED`を返す（Productionでは絶対パス必須という既存仕様）。Telemetryとは無関係。
- Next.js 16.4.3の`next dev`はリポジトリ直下に`AGENTS.md`を自動生成する。検証で生成されたものは削除した。

---

## 8. Final Local Integration — SRE Trace Pipeline (Collector → Tempo)

**Recorded:** 2026-11-13 (JST)。§3と同一シナリオを、検証専用Collectorではなく**SRE構築済みの経路**へ送信して再実行した。SREのコンテナ・設定（`.local/otel`, `.local/tempo`）は読み取りのみで変更していない。

### 8.1 Environment

| Item | Value |
|---|---|
| Path | App → OTLP/HTTP `127.0.0.1:4318`（`gms-otel-local`, `batch` → `otlp/tempo`） → `gms-tempo-local:4317`（`gms-tracing-local` network） → Tempo API `127.0.0.1:3200` |
| App | 作業ツリーの隔離コピーを`next build --webpack`（`NEXT_PUBLIC_GMS_RUM_ENABLED=true`）し、Standalone `server.js`を`127.0.0.1:3002`で起動。既存の`next dev`（:3001）は維持 |
| Env | `GMS_OTEL_ENABLED=true`, `GMS_RUM_ENABLED=true`, `GMS_DEPLOYMENT_ENVIRONMENT=local-e2e`, Cognito設定なし, `.env*`不使用 |
| Data | 一時SQLite（検証用ユーザー1名、用語230件、AES-GCM暗号化済みダミーAPI Key）。Session Cookieは一時`SESSION_SECRET`で署名して注入。検証後に一時DB・Secretを削除 |
| External APIs | ローカルMock（`127.0.0.1:4555`, 応答遅延100ms）。実DeepL / OpenAI / Cognitoへの通信0件 |
| Browser | Playwright Chromium headless：`/translation` → Glossary展開 → Select All → 原文入力・同意 → Translate → 結果表示 → ページ離脱 |

### 8.2 Traces in Tempo

| Trace ID | Root | Spans | Orphans |
|---|---|---|---|
| `9b5b37884483f23bad4cc5f864fc4986` | `gms.rum.translation`（browser, outcome=success, 447ms） | 25 | 0 |
| `1b69bd11653349435fe63dbffe5f4462` | `gms.rum.select_all`（browser, outcome=success, 33ms） | 62 | 0 |

TraceQL：`{ name = "gms.rum.translation" && resource.deployment.environment.name = "local-e2e" }`（Select Allは`gms.rum.select_all`）で各1件ヒット。

```text
gms.rum.translation            {gms.telemetry.source=browser, gms.action.outcome=success, http.response.status_code=200}
└─ POST /api/translate         {http.route=/api/translate, http.status_code=200}
   └─ executing api route (app) /api/translate
      ├─ CREATE schema_migrations / SELECT schema_migrations / SELECT users
      └─ gms.translation.request {selected_term_count=230, effective_term_count=230, source_text_length=30, outcome=success}
         ├─ gms.translation.validate
         ├─ gms.sqlite.read_translation_terms
         │  └─ SELECT terms
         ├─ gms.translation.resolve_terms
         ├─ gms.credentials.load
         │  ├─ SELECT api_credentials
         │  └─ SELECT api_credentials
         ├─ gms.deepl.translate {gms.provider=deepl}                  317.6ms
         │  ├─ fetch POST …/v2/glossaries       {http.status_code=200}
         │  ├─ fetch POST …/v2/translate        {http.status_code=200}
         │  └─ fetch DELETE …/v2/glossaries/g-1 {http.status_code=204}
         └─ gms.openai.post_edit {gms.provider=openai}                106.2ms
            └─ fetch POST …/v1/chat/completions {http.status_code=200}
```

Select All：`gms.rum.select_all`の直下に`GET /api/glossaries/[glossaryId]/terms`（200）×4（未取得ページ3件の並列取得＋Revision確認1件。1ページ目は展開時に取得済み）、各々にSQLite Span（`BEGIN` / `SELECT glossaries` / `SELECT glossary_term_revisions` / `SELECT terms` / `COMMIT`）。

### 8.3 Mock API Calls

| Endpoint | Calls | `traceparent` / `tracestate` / `baggage` | Auth |
|---|---|---|---|
| `POST /v2/glossaries` | 1 | 0 / 0 / 0 | `DeepL-Auth-Key`（ダミー） |
| `POST /v2/translate` | 1 | 0 / 0 / 0 | `DeepL-Auth-Key`（ダミー） |
| `DELETE /v2/glossaries/:id` | 1 | 0 / 0 / 0 | `DeepL-Auth-Key`（ダミー） |
| `POST /v1/chat/completions` | 1 | 0 / 0 / 0 | `Bearer`（ダミー） |

### 8.4 Telemetry Delivery and Sensitive Data

- Tempo `tempo_receiver_refused_spans` = 0、Collectorのエラーログ 0件、アプリstdoutにExportエラー無し。Tempoの`BackendScheduler/Next … no jobs found`（warn）はIdle時の定常ログ。
- `local-e2e`の全57 Trace / 288 SpanをTempo APIから取得して検索：原文・用語・中間訳・最終訳・用語集名・ダミーAPI Key・メールアドレス・Auth Subject・User ID・Session Cookie・`SESSION_SECRET`・暗号化Key・`Authorization`・`Cookie`・`q=` / `code=` / `state=`・`db.query.text` / `db.statement` / `exception.message`の**一致0件**。
- `http.target`の値：`/`, `/api/auth/me`, `/api/glossaries`, `/api/glossaries/<id>/terms`, `/api/translate`, `/glossary`, `/login`, `/register`, `/translation`（Query無し）。

### 8.5 Not Verified

- Grafana（`localhost:3300`）上の表示：Grafanaは認証必須（Datasource API 401）。SREの認証情報は使用していない。Datasource `local-tempo`は同一Network上の`gms-tempo-local:3200`を参照しており、Tempo APIで上記Traceの取得を確認済み。Grafana Exploreでの目視確認はSRE / Application Teamで実施すること。
