# GMS v5 — Disaster Recovery Drill, Backup Automation and Performance Evidence

- **Evidence date:** 2026-11-20 JST（AWSログのUTC日付は2026-11-19）
- **Status:** 隔離DR演習・性能試験・DR Cleanup完了。DLM適用済み、初回実行未確認。Production Cutover未実施。
- **Drill ID:** `gmsv5-dr-20261119T163346Z`
- **Tested production release:** `57b914136fcea70247336e7bf65160300e47781c`
- **Region:** `ap-northeast-1`
- **Owner:** Infrastructure / SRE（性能試験はDevelopmentとの共有証跡）
- **Related:** [Production State](./production-state.md)、[DR Runbook](./disaster-recovery-runbook.md)、[CI/CD As-Built](./cicd-as-built-2026-11-19.md)、[CR-01](../design/cr-01-parallel-fetch-design.md)

本書は当日の実測と適用結果の記録であり、現在状態の要約はProduction State、次回の復旧手順はRunbookを参照する。以下のPASSは明記した試験条件に限る。

> **Follow-up（2026-11-20夕方追記）：** 本文は演習時の記録のまま変更していない。その後の事実は次のとおり。（1）DLMの初回の自動Snapshotが成功した（§12）。Status行の「初回実行未確認」と§3・§10の該当箇所は、§12で更新される。（2）§8で調査したDNS / CDNの問題について、権威DNSをRoute 53へ移行した（[DNS Migration As-Built 2026-11-20](./dns-migration-as-built-2026-11-20.md)）。§8.3の「DNS移行は未実施」は演習時点の事実である。（3）Product OwnerはWorker 4の本番適用と追加の並列化をしないと決定した（[CR-01 §14](../design/cr-01-parallel-fetch-design.md#14-追加最適化のclose2026-11-20)）。§7の結果と§5のRPO / RTOの判定は、当時の観測としてそのまま有効である。

## 1. Acceptance summary

| 項目 | 実測・確認結果 | 判定 |
|---|---|---|
| Snapshot recovery point age（RPO評価用） | 約267.5h（目標24h） | FAIL |
| Application recovery（T0→T1） | 308s / 5m08s | PASS：30分以内 |
| Functional recovery（T0→T2） | 478s / 7m58s | PASS：30分以内 |
| SQLite | 復元前後の整合性検査、Migration、件数確認 | PASS |
| Cognito / UI | 既存ユーザーでログインし用語集を参照 | PASS |
| DLM | 毎日、最新2世代保持のPolicyを適用 | Configured：実行・Rotation未検証 |
| CR-01（1万件、4 Worker） | p95 4.4573s、逐次比61.25%短縮、各条件30/30成功 | PASS：隔離環境での性能目標のみ |
| CPU Credit | 5分観測値で枯渇なし | PASS：今回の短時間負荷のみ |
| CDN調査 | HIO経由→NRT経由でHealth API median TTFB 297.809→35.822ms | Measured：恒久修正なし |
| DR Cleanup | DR対象の残存監査7分類すべて空、ローカルToken削除・Tunnelなし | PASS |
| Production / Observability | EC2/EBS Status OK、SSM Online、本番Readiness 3/3 HTTP 200、SHA一致 | PASS：確認範囲内 |

## 2. Initial state and safety boundaries

演習前に本番・監視EC2、Data EBS、Snapshot、S3 Release Artifact、Cognito Client、既存バックアップ、ReadinessをRead-onlyで調査した。

- 本番と監視基盤は東京のARM64 `t4g.small` Spot。双方Running、EC2 System/Instance/EBS Status OK、SSM Online。
- 本番のSQLiteは暗号化済み10 GiB gp3独立EBS（3000 IOPS / 125 MiB/s）、XFS、`/mnt/gms-data/gmsv5.sqlite`。Data EBSはDeleteOnTermination=false。journal modeはdelete。
- 本番はIPv6-onlyの公開Origin、CloudFront→HTTPS→Nginx→Next.js loopback :3000。Cognitoの既存localhost Callback/Logoutを演習で再利用できることを確認。
- 本番Releaseは冒頭のSHA。S3にLinux ARM64 Standalone Artifact、SHA256、Manifestが存在。Artifact SHA256は `d8a5aab73e2b251e24bf30b521d335a98ca9ee9a2416fa889532cf884dd15c7a`、DB Schema Fingerprintは `5fea7657dc23fa13161d3d5af848420e9db790a62a18f0771b52172b9f267d32`。
- 本番実行Nodeはv26.12.1、Artifact Manifest指定はv26.13.1。DRではManifest指定を使用した。
- 2026-11-19 01:17:54 JSTに作成されたSQLiteオンラインバックアップ（約56 MiB、SHA256確認済み、作成時quick_check成功）が本番Root EBSに存在した。ただし独立した障害ドメインに保管されていないため、今回のEBS Snapshot復旧点の代替とはしない。
- 監視基盤はGrafana、Tempo、Prometheus、OTel Collector、OTLP Gateway、node exporter、Discord Relayが稼働。演習前のGrafana APIはDB OK。演習後はEC2/SSMレベルのみ最終確認（全Containerや通知経路の再受入は未実施）。

DRは本番のTerraform Stateから独立させ、既存SnapshotとArtifactを読み取り専用で利用した。DR専用のInstance、EBS、SG、IAM Role/Profile、ENIへDrillIdを付与。元のEBS、Snapshot、Cognito設定、本番Deployment Lock、Release Prefix、本番DNSを復旧操作で変更しない。DRから本番Telemetryを送信せず、外部翻訳APIを呼ばない。DLMの恒久追加と、後述の検証用CNAME追加は明示的に別扱いとした。

当日のCLIはアカウントroot権限で実行された。これは今後の運用上の改善点であり、Runbookは最小権限のOperator Roleを前提とする。

## 3. Permanent backup automation — Amazon Data Lifecycle Manager

RPOの不足を受け、DR用の一時資源作成に先立って、恒久的なSnapshot PolicyをProduction Terraform Root Moduleへ追加した。

| 項目 | 設定 |
|---|---|
| IaC | `infra/terraform/v5/backup.tf` |
| Policy | `policy-04811170b69e903b4`、ENABLED |
| Type | EBS_SNAPSHOT_MANAGEMENT / VOLUME |
| Target | Volume Tag `Name=gmsv5-sqlite-data` |
| Schedule | `DailyBackup`、24h、19:00 UTC（04:00 JST） |
| Retention | Count=2 |
| CopyTags | false |
| Snapshot Tags | `Name=gmsv5-daily-data-backup`、`ManagedBy=DLM`、`GMSBackupPolicy=daily-data` |
| IAM Role | `gmsv5-dlm-role`、AWSDataLifecycleManagerServiceRole |
| Delete guard | `gmsv5-dlm-delete-guard`：`GMSBackupPolicy=daily-data` でないSnapshotのDeleteSnapshotを明示Deny |

Plan: **4 to add, 0 to change, 0 to destroy**。Apply: **4 added, 0 changed, 0 destroyed**。2026-11-20 01:25 JST頃にENABLEDと設定内容を確認し、既存手動Snapshotが維持され、本番Readinessが正常であることを確認した。

本PolicyはDR Cleanup対象外。演習終了時点で `backup.tf` は `feat/gmsv5-backup-lifecycle` 上の未追跡ファイルだった（AWS Apply済み、Git反映とは別）。初回作成は11月20日04:00 JSTを基準とするSchedule。実際の開始・完了時刻、3回目以降のRotation、Delete Guardの実動作は未確認。24h周期はRPO 24h達成そのものを保証しない。Snapshot失敗、実行遅延、完了までの時間、アプリ整合性を別途評価する。

## 4. Isolated DR environment

| 項目 | 実際の構成 |
|---|---|
| Instance | AL2023 ARM64、t4g.small On-Demand、standard CPU credits、東京1a |
| Root EBS | 16 GiB、DeleteOnTermination=true |
| Data EBS | 元Snapshotから暗号化10 GiBを復元、DeleteOnTermination=false |
| Network | 既存dual-stack Subnet、DRにPublic IPv4とIPv6を割当、SG inboundなし、outbound TCP443 |
| Management | SSM Session Manager、IMDSv2 |
| IAM | AmazonSSMManagedInstanceCore、指定Release PrefixのGetObject、既存Cognito client secretの指定Parameterのみ取得可、他Parameter取得を制限 |
| Runtime | Node.js v26.13.1、`/opt/gmsv5-dr/runtime/` |
| Application | `gmsv5-dr.service`、専用ユーザー `gmsdr`、loopback :3000 |
| Data | 新規復元EBSを`/mnt/gms-data`へrw,nodev,nosuidでMount |
| Browser | Mac localhost:3100→SSM→DR :3000 |
| Telemetry / Providers | OTel・RUM無効、翻訳Provider URLを使えないloopback endpointへ変更 |

本番と異なるPublic IPv4、SpotではないInstance、DR専用のSession/Encryption Keyを利用した。SSM Managed Policyは汎用管理権限を含むため、DR専用Roleでも「完全にRead-only」とは呼ばない。復元されたCredential行は参照・件数確認のみ。元のEncryption Keyを復元していないため、既存翻訳Credentialの復号やProvider Callは試験外。DRサービスの再起動時の自動Mount/Boot Recoveryも対象外。

## 5. RPO and RTO measurements

### 5.1 Recovery point

復元元は `snap-0ec99a1b15c53dc25`、StartTime 2026-11-08 22:03:15.827 JST、状態completed。旧手動Snapshot `snap-05e7147085a3966b4` も保持。評価基準はT0と復元元SnapshotのStartTimeの差で、約11日3時間30分（267.5h）。暫定目標24hに未達。

これは復旧点の経過時間であり、実際のデータ損失時間・件数を測定したものではない。Snapshot後の変更履歴全体を比較していないため、後述のTerm件数差を損失件数として扱わない。

### 5.2 Recovery timeline

全時刻JST（UTCへ換算する場合9時間減算）。準備、Artifact確認、DLM ApplyはT0より前でRTOに含まない。

| Event | 時刻 | T0から |
|---|---|---:|
| T0：DR Provisioning開始 | 01:33:46 | 0s |
| SSM Online | 01:34:32頃 | 46s |
| Mount・復元DBの事前検査完了 | 01:35:35 | 109s |
| Node実行時にlibatomic不足を検出 | 01:36:56 | 190s |
| DRのみlibatomic導入、Node再確認 | 01:37:49頃 | 243s |
| Artifact検証・展開、Migration、service起動 | 01:38:27–43 | 281–297s |
| T1：期待SHAのReadiness 3回連続成功 | 01:38:54 | **308s** |
| T2：SSM経由Cognitoログイン・用語集参照完了 | 01:41:44 | **478s** |

T1/T2は実測。30分の暫定RTOに対してFunctional Recoveryは7m58s。T0以前に用意したUser Data、AMI選定、復旧用Source Bundle、Artifactが利用可能な条件下の値。障害検知、意思決定、DNS切替、本番トラフィック復旧、旧本番のFencingは含まない。単発演習であり、実災害時のRTO保証ではない。

## 6. Application and database verification

DRで復元EBSのNVMe Serialを対象Volume IDと照合し、XFS Mountを確認。Migration前と後にSQLite検査を実施した。Nodeの公式ARM64 ArchiveはSHA256一致（`89e1853138db51e8b82d3e98ba9c0d3f2956a28e5e55ea1b7e12d25aae92d5dd`）。AL2023上で`libatomic.so.1`不足が判明したため、DR側だけに`libatomic`を導入し、`ldd`とNode Versionで再確認した。これはRTO内の作業である。

S3のArtifactを取得し、ManifestとSHA256を確認したうえで既存Deployment Utilityを利用して安全に展開。復元元はMigration 1適用済み、現行ReleaseはMigration 2を必要とした。DR DBでのみMigration 2を適用し、その結果を確認した。

| Table | 本番事前観測 | DR Migration前 | DR Migration後 |
|---|---:|---:|---:|
| users | 2 | 2 | 2 |
| glossaries | 22 | 22 | 22 |
| terms | 160321 | 160320 | 160320 |
| api_credentials | 4 | 4 | 4 |
| glossary_term_revisions | 22 | 未存在 | 22 |
| schema_migrations | 2 | 1 | 2 |

Migration 1=`initial_schema`、2=`glossary_term_revisions`。`quick_check`/`integrity_check`は前後ともok、`foreign_key_check`は0件。件数はCognitoログイン前の観測。本番との差はTermの純件数差1であり、失われたTermの数や変更内容を証明しない。

本番と同じBuild SHAでReadinessが3回連続成功。既存Cognito Clientのlocalhost Callback/Logoutに合わせ、公開Origin相当を`http://localhost:3100`に設定。SSM Port Forwarding経由で既存ユーザーの認証と用語集・Term参照を実施した。新規登録、パスワード変更、実翻訳、外部Provider Credentialの利用は実施しなかった。本番Ingressを切替えていない。

## 7. CR-01 — Controlled Select All benchmark

### 7.1 Method

同一Commit SHAから `NEXT_PUBLIC_GMS_TERM_FETCH_CONCURRENCY=1/2/4` の3種類をMacのLinux ARM64 Docker（Node v26.13.1 bookworm）でそれぞれBuildした。既存Production S3 Prefixは変更せず、SSM経由でDRへ転送して各SHA256を検証。Build処理はDR CPU Creditを消費させないためMacで実行した。

- 同じDR Instance・Node Runtime、各Variantを一度に1つだけ起動。復旧用serviceは停止し、専用`gmsbench` userと`gmsv5-bench@.service`で隔離。
- 合成DBはRoot EBS上の約3.4 MiB、1ユーザー、1用語集、10,000 Term、Credential 0、Migration 1/2、Integrity/FK検査成功。復元した本番DBはBenchmarkに使わない。Benchmark serviceは`/mnt/gms-data`へアクセス不可。
- BrowserはPlaywright 1.64.1 Chromium、Mac localhost:3102→SSM→DR :3001、Headless、新規Browser Contextごとに試験用Sessionを設定。
- 実際のBYO Translation UI `/translation` で合成用語集を展開し、最初のPage取得後にSelect Allをクリック。Click eventからHeadingが`10,000 terms selected`へ更新されるまでを`performance.now()`で測定。DOM更新までで実際のPaint完了とは異なる。
- 100件/page×100ページ、最終Revision Validation 1回、計101 Term GET。Network in-flight最大数と一意Page数、Validation、失敗・未完了Requestを記録。
- Worker 1→2→4の固定順、各3 Warm-upを除外、各30正式Trial。事前Smoke 1件も除外。全体の実行は02:00–02:14 JST頃。
- p50は標本中央値（偶数nは中央2値の平均）、p95はCR-01指定Nearest Rank（30回の29番目）、短縮率=1−(対象p95/Worker 1 p95)。

### 7.2 Results

| 指標 | Worker 1 | Worker 2 | Worker 4 |
|---|---:|---:|---:|
| 正式試験数 | 30 | 30 | 30 |
| 成功数 | 30 | 30 | 30 |
| min (s) | 10.5552 | 5.6286 | 4.1528 |
| p50 (s) | 10.98945 | 5.76520 | 4.20665 |
| p95 (s) | 11.5035 | 5.9201 | 4.4573 |
| max (s) | 12.0334 | 6.0432 | 4.4662 |
| mean (s) | 11.04367 | 5.77835 | 4.24033 |
| p95短縮率 | baseline | 48.54% | 61.25% |
| p95 ≤20s | PASS | PASS | PASS |
| 逐次比 ≥50%短縮 | — | FAIL | PASS |

90回すべてで選択10,000件、Term Request 101、Unique Pages 100、Validation 1、期待最大並列数と一致、Failed/Unfinished Requests 0、pass=true。標本ログから再集計した結果である。Worker 4は隔離試験における採用候補。本番Defaultの変更・再Deployは行っていない。

### 7.3 Resource observations

| 指標 | Worker 1 | Worker 2 | Worker 4 |
|---|---:|---:|---:|
| Sample count | 361 | 203 | 156 |
| Monitor elapsed (s) | 360.52 | 202.25 | 155.19 |
| Service CPU seconds | 72.083 | 72.394 | 71.452 |
| Average CPU (% of 2 vCPU capacity) | 9.997 | 17.897 | 23.021 |
| Peak MainPID RSS (MiB) | 146.24 | 156.24 | 157.46 |
| Peak cgroup memory.current (MiB) | 95.64 | 105.91 | 107.21 |
| cgroup disk read (bytes) | 0 | 0 | 0 |
| cgroup disk write (bytes) | 9584640 | 9551872 | 1572864 |
| SQLite busy / locked / database is locked log matches | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

1s間隔のService cgroup/MainPID Monitor。Monitor区間にはTrial間の準備/待機も含む。RSSとmemory.currentは異なる指標であり直接比較しない。Disk read 0は測定cgroupのBlock I/O計上値でありSQLiteが読み取っていないという意味ではない。Warm DBの条件。ログの一致0は内部競合が存在しないことの証明ではない。

CloudWatch（5分集計、Instance全体）:

| JST | CPUCreditBalance | CPUCreditUsage (Sum) | CPUUtilization (%) |
|---|---:|---:|---:|
| 01:55 | 6.958 | 0.284 | 2.837 |
| 02:00 | 8.692 | 0.266 | 12.938 |
| 02:05 | 9.157 | 1.538 | 19.157 |
| 02:10 | 8.992 | 2.161 | 26.228 |
| 02:15 | 8.865 | 2.130 | 0.865 |

全観測点でCredit正値。各Workerの正確なCredit消費を切り分ける粒度ではなく、瞬間的な枯渇や長時間負荷の制約を保証しない。CPU合計はほぼ同等で、並列化により時間当たりの負荷が集中した。Peak RSSの増加はWorker 1→4で約11.2 MiB。

### 7.4 Acceptance boundaries

本番の公開HTTPS・実医薬Term・約16万件のDB、複数ユーザー、長いTerm、他APIのp95負荷ガード、WAL/checkpointへの影響、長期負荷は未検証。Artifactは同一SourceでもBuild設定が異なる別Variant。合成DBは本番と異なるEBS上にあり、SSM通信も公開CDNとは異なる。順序をランダム化していないため時間変動・Credit・Cacheの交絡を排除できない。正式なProduction FR-734の受入完了とはしない。DB journal modeは変更していない。

## 8. Production latency investigation — DNS / CDN

### 8.1 Findings and measured evidence

Productionの医薬用語集9,999件のSelect Allは手動観測で約30秒。Chrome Networkの表示は102 requests、約3.5 MB転送（ページ全体でのカウンタ）。個別Term APIのTimingはTotal 508.97ms、TTFB 473.47ms、Content Download 33.64ms、Queueing 0.34ms、Stalled 1.26ms、Request Sent 0.27ms。

既存Grafana/TempoのTerm API検索では14件のServer Traceが24–47ms（表示値平均約30.6ms）。Browser RUMは本番無効のため、同一Browser RequestとTrace IDで突合していない。したがってTTFBとの差をそのまま特定区間の遅延にはしない。Applicationだけでは外部観測の遅延を説明しにくいことを示す証拠である。

公開Health API `https://sunlightjetrans.com/api/health/ready` をMacからcurlで測定。毎回新規接続のIPv6試験では初回を除くTCP接続差分が概ね150–175ms、TTFB累積は566–633ms。接続再利用試験はHTTP/2、2回目以降TCP/TLS時間0、全HTTP 200。

| 経路・条件（各10回、初回除外） | Median TTFB (ms) | Min (ms) | Max (ms) |
|---|---:|---:|---:|
| 独自Domain、IPv6 series 1 | 258.424 | 237.102 | 1799.582 |
| 独自Domain、IPv6 series 2 | 297.809 | 244.825 | 691.368 |
| 独自Domain、IPv4 | 303.691 | 253.531 | 634.232 |
| CloudFront native DNSへ接続先変更、IPv6 | 35.822 | 27.426 | 264.807 |

IPv6 ping（10回）はmin/avg/max/stddev=146.443/329.048/727.772/198.007ms、loss 0%。ICMPの値はHTTPのLatencyそのものではない。

最終行は `curl --connect-to sunlightjetrans.com:443:d2yxdtkikux26e.cloudfront.net:443` を用いてURL・Host・TLS検証を維持した比較。独自Domainの通常接続で `HIO52-P2`（Hillsboro、米国オレゴン）、native Domainへの直接解決および接続先変更の確認Requestで `NRT12-P5`（東京）を観測。確認したHeaderは `x-cache: Miss from cloudfront`。各Timed RequestごとのPOP/Cache Headerは保存していない。初回接続を除く各9回で、series 2比Median TTFB約88.0%短縮。東京経由は8/9が27–41ms。少数標本の逐次実施であり、全利用者の性能保証やSelect Allの改善実測ではない。

### 8.2 DNS evidence and root cause hypothesis

- 公開CloudFront Distributionは `PriceClass_All`、IPv6 Enabled、HTTP/2。日本のEdgeを使用可能であり、Price Class 100による地域制限ではない。
- 独自DomainはDreamHostのapex ALIAS。管理画面上Targetは `d2yxdtkikux26e.cloudfront.net` であり、CloudFrontが発行した標準Domainと一致。固定A/AAAAを手入力したという仮説は採用しない。
- DreamHostの3権威NSへ直接問い合わせても、apexはA `3.163.24.x`、AAAA `2600:9000:260f:...` を返した。通常Resolver・Cloudflare・GoogleのAも同じ4アドレス群。一方native Domainは別のA/AAAA群（例 `3.165.39.x`、`3.170.229.x`、`2600:9000:26f2:...`）。IPのprefix自体で地理位置を断定せず、実際のPOP Headerを根拠とする。
- IPv4/IPv6双方の独自DomainでHIOを観測したため、IPv6だけの問題ではない。DNSはHTTPのData Planeに存在せず、名前解決で選ばれた遠方Edgeへの継続的な通信が遅延要因となる。
- **有力仮説:** DreamHostのALIAS flatteningによるCloudFront DNS解決で利用者側の位置情報が反映されず、遠方のEdge向けAddressが返っている。DreamHost内部の実装・ECS・AWS内部経路までは未確認。登録者住所が原因という証拠なし。

追加の比較として `edge-test.sunlightjetrans.com CNAME d2yxdtkikux26e.cloudfront.net.` をDreamHostに登録。02:45 JST頃にns1でCNAME応答を一度確認したが、02:46–47頃にはNXDOMAINとNOERRORが混在。ns1のNXDOMAINにSOA serial `2026111900`、別Queryには`2026102400`が現れ、ns2はCNAME応答、ns3はNXDOMAIN。権威応答の不整合を観測したが、その原因とapexのPOP問題が同一かは未確定。CNAME比較は未完了で、検証用Recordは意図的に残した。

### 8.3 Remediation candidates (not implemented)

DreamHost側のALIAS動作確認、CNAME比較の再実施、必要ならRoute 53 native Alias（apex A/AAAA）へのDNS移行を検討。Domain登録者・EC2・CloudFront・Cognitoは原則維持。Origin用AAAA、ACM検証Recordなどを保全し、移行時は全Zone Record・委任・DNSSEC有無・Rollbackを先に検証する。CloudFront Server-TimingなどのEdge監視は未導入。東京POPへの恒久誘導、DNS移行、Worker 4本番適用は未実施。AWSにおける東京Edge選択は常に保証されるものではない。

> **Follow-up（2026-11-20夕方追記）：** 同日夕方に権威DNSをRoute 53へ移行し、ApexをA / AAAA Aliasへ変更した。移行後の計測では東京Edge（`NRT12-P5`）を観測した。時系列、性能、限界、Terraform管理は[DNS Migration As-Built 2026-11-20](./dns-migration-as-built-2026-11-20.md)にある。上の段落は演習時点の記録として残す。

## 9. Cleanup and post-drill safety audit

| 時刻（JST） | 操作・確認 |
|---|---|
| 02:52頃 | DR Instance、Root/Data EBS、ENI、SG、IAM Role/ProfileのID・DrillId一致、証跡存在を確認 |
| 02:54:06 | DR Instance Termination待機完了、Root EBS・ENIなし、DR Data EBS available |
| 03:05:50 | DR Data EBS、SG、IAM Role/Profile（Managed Policy detach、Inline Policy削除を含む）のCleanup完了 |
| 03:08頃 | 残存監査、本番・監視EC2、既存Snapshot、DLM、公開Readiness確認 |
| 03:09頃 | MacのBenchmark Session Token削除、DR Port 3100/3101/3102のListenerなし、Evidence 14ファイル確認 |

DRの残存監査はActive EC2、Volume、SG、ENI、Snapshot、IAM Role、IAM Instance Profileの7分類で全て空（DrillId Tagまたは専用Name Prefixで照合）。終了済みInstanceの履歴は残り得る。Terraform destroyは未使用。DR用Root/Data EBSは削除済み。本番と監視EC2はRunning、System/Instance/Attached EBS status OK、SSM Online。元のProduction Data EBSは元のInstanceにattached/in-use、DeleteOnTermination=false、既存2手動Snapshotはcompleted、DLM ENABLED。公開Readiness 3/3 HTTP 200、すべて冒頭SHA一致、Total 1.512634/0.595115/0.559217s。

本番への悪影響は確認範囲で観測されなかった。全期間の無停止保証ではない。演習後のGrafana/Tempo/Prometheus内部再検査、Discord実送信、CD変数の再取得、Production DB完全比較は未実施。演習前にCD有効を確認し、演習中にCD設定変更は行っていない。DLMは恒久資産として維持。テストCNAMEも今回のAWS DR Cleanup対象外。

## 10. Engineering findings and follow-up

| 項目 | 状態・次の判断 |
|---|---|
| Backup gap | 24h RPO目標に未達。DLMの初回作成・完了とRetention、失敗監視、定期Restore Drillを検証する |
| Runtime dependency | AL2023上のNode Archiveにlibatomicが必要だった。Manifest Versionと依存をRunbook/Preflightで確認する |
| DR reproducibility | 一部準備ScriptはLocalのみ、完全IaC化・Cold Restore・再起動検証・本番切替は未完了 |
| Credential / Cognito | DRで実翻訳Credential復号未検証、共有Cognitoへのログインは実施済み。完全独立IdP復旧は未実施 |
| CR-01 | Worker 4候補、Production相当条件・他API負荷・多人数・長時間試験は未完了 |
| DNS / CDN | Edge選択の差と性能差は実測、DreamHost内部根因は仮説。CNAME実験と恒久修正は後日 |
| Observability | Edge timing、Nginx timing log、Production host metrics、Browser RUMの運用判断は別タスク |
| IAM | root CLI利用をやめ、権限と監査を整備 |
| Git / State | backup.tfをGit管理し、Apply済みStateと宣言を照合。mainへのMergeはCDを起動するため変更管理が必要 |

## 11. Evidence index and privacy

Local evidence root: `~/gmsv5-dr-evidence/gmsv5-dr-20261119T163346Z/`。演習終了時に以下14ファイルの存在を確認した（原本は公開Repositoryへ入れない）。

| File | 内容 |
|---|---|
| `application-recovery.json` | SSM取得したRecovery Log/T1の証跡 |
| `infrastructure.log` | Provisioning timeline |
| `recovery-metrics.txt` | RPO/RTO記録 |
| `resources.env` | DR専用ID一覧 |
| `benchmark/benchmark-results.jsonl` | 正式90件＋Warm-up 9件の生データ |
| `benchmark/smoke-test.jsonl` | 正式集計から除外したSmoke 1件 |
| `benchmark/benchmark-start.txt`, `benchmark-end.txt` | 実行時刻 |
| `benchmark/worker-{1,2,4}-resource.json` | 各Variantの集計Resource値 |
| `benchmark/CPUCreditBalance.json`, `CPUCreditUsage.json`, `CPUUtilization.json` | CloudWatch時系列 |

追加証跡は当日のTerminal出力、Chrome DevTools/Tempo/DreamHostの画面、Cognitoログイン・用語集画面。DNS調査と最終Cleanupログは会話に記録されているが、上記14ファイルには含まれていない。1秒Resource Monitorの全サンプルはDR上に作成したが、Localへの全サンプル保存は確認していない（DR削除後は再取得不可）。個人情報、Cookie、Secret、Session Token、復元DB、Terraform State、未加工HARはコミットしない。必要な証跡のみ秘匿検査後に保存する。

## 12. Addendum — First automated DLM snapshot (2026-11-20)

> 2026-11-20に追記した。§3のDLM Policyの初回実行の確認であり、§1〜§11の演習時の記録は変更していない。

| 項目 | 実測・確認結果 |
|---|---|
| DLM Policy | `policy-04811170b69e903b4` |
| Snapshot | `snap-010d97887aba25463` |
| StartTime | 2026-11-19T19:32:51.927Z（2026-11-20 04:32:51.927 JST） |
| 確認時刻 | 2026-11-20 11:03 JST頃 |
| State / Progress | completed / 100% |
| Encrypted | true |
| Tag | DLMが付与するTagを確認 |
| 既存の手動Snapshot | 2件とも保持 |

判定：日次の自動化は**1回成功した**。Scheduleの基準時刻（19:00 UTC）に対し、今回の作成開始は約33分後だった。

未確認のまま残るもの：

- 2世代保持の世代交代（3回目のScheduled Backupの後に確認する）
- 継続的なRPO適合。1回の成功は日々の成功を示さない
- このSnapshotからのRestore試験
- Delete Guardの実動作、DLM失敗の検知・通知

§1と§5.1のRPO FAIL（復旧点約267.5h）は、演習で使った復旧点についての観測であり、新しいSnapshotがあることを理由にRPO受入の成功へ書き換えない。§5.2のRTOの結果も、演習時の観測としてそのまま有効である。現在の状態は[Production State §3.9](./production-state.md#39-backup--disaster-recovery2026-11-20)を参照する。
