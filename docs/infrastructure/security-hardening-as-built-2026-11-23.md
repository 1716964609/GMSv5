# GMS v5 — Security Baseline / IAM Migration As-Built

- **Document type:** Infrastructure As-Built / Security & FinOps Operating Evidence
- **Evidence window:** 2026-11-22〜23 JST（11月23日未明の最終認証確認を含む）
- **Recorded:** 2026-11-23 JST
- **Owner:** Infrastructure / SRE
- **Scope:** GitHub repository controls、AWS Budget、GitHub Actions OIDC、CI security scan、Operator CLI認証、CloudFront→OriginのIngress公開範囲
- **Current-state index:** [Production State](production-state.md)
- **Source of truth for declarations:** [CI Workflow](../../.github/workflows/ci.yml)、[CD Workflow](../../.github/workflows/cd.yml)、[OIDC Verification Workflow](../../.github/workflows/verify-aws-oidc.yml)、`infra/terraform/`

> **Evidence policy:** 本書は、Operatorが提示したAWS CLI / Terraform / GitHub CLIの実行結果、GitHub設定画面に関するOperatorの確認報告、GitHub `main` のWorkflow定義を分けて記録する。AWS・IAM Identity Center・Budgetの実環境を、本書の執筆時に別途ライブ監査したものではない。「宣言」「Operator実測」「報告」「未確認」「決定」を混同しない。
>
> **Security:** 本書にAccess Key Secret、SSO Token、Password、MFA Seed、Webhook、Origin Custom HeaderのSecret、Terraform State、Credential Fileの内容は記録しない。ローカルのAWS認証設定・BackupはGit管理しない。
>
> **Operational warning:** `main` へのMergeは、ドキュメントだけの変更でも本番CDを起動する（`.github/workflows/cd.yml`にPath Filterなし）。本書の追加自体はAWS構成を変更しないが、Merge時は[CI/CD Runbook](cicd-operations-runbook.md)に従ってDeployment結果を確認する。

## 1. 結論 / Scope

2026-11-23 JST時点で、GitHubの機密情報保護、月額予算アラート、OIDC / IAMの監査、およびTrivyのレポート専用Scanを導入・確認した。Operatorの普段のAWS CLI / Terraform操作は、root / 長期IAM Access KeyからIAM Identity Center（SSO、MFA）へ移行した。

ただし、**旧Access KeyはInactiveであり未削除**、旧IAMユーザーも残存する。**Trivy検出事項のうちIMDSv2のTerraform宣言不足は対応済み**。その他は対応方針を記録し、未完了項目を継続管理する。**CloudFront→OriginのCustom Header Secret Rotationは未実装・延期**。この3点を「完了」としない。

| Mission | 達成状況 | 根拠の種類 |
|---|---|---|
| ① GitHub Secret Protection / Push Protection | 設定済み | OperatorによるGitHub設定確認 |
| ② AWS Budget | 月額USD 20のBudgetと通知閾値を設定 | OperatorによるConsole設定・通知先検証 |
| ③ GitHub OIDC / IAM Audit | 監査3項目PASS、CDはOIDCを使用 | Operatorによる監査結果 + Workflow宣言 |
| ④ Trivy | CIにReport-only Job導入、PR #11をMerge | GitHub `main` 宣言 + OperatorのScan結果 |
| ⑤ Identity Center SSO | 日常認証の移行と無効化試験PASS | OperatorのAWS CLI / Terraform出力 |
| ⑥ CloudFront Origin Header Rotation | **延期、変更なし**。Ingressを検証し、残存リスクを受容（§6.1〜6.3） | Product Owner判断 + OperatorのAWS CLI / curl実測 |

## 2. GitHub Secret Protection / Budget（①、②）

### 2.1 GitHub

- Secret Protection / Push Protectionを設定済み（Operator確認）。
- `gh secret list` はRepository Secretsについて `no secrets found` と応答（Operator実測）。
- **境界:** これはEnvironment Secrets / Organization Secretsが存在しない証拠ではない。また、Secret Protectionは全種類のCredential流出を完全に防ぐ保証ではない。
- `main` へのPRに既存のQuality Gateが必要な構成は維持。

### 2.2 AWS Budget

- 月額の予算閾値：**USD 20**。
- 通知条件：Actual **80%**、Forecasted **100%**、Actual **150%**。
- 通知先の設定・メール確認まで実施（Operator報告）。
- **未確認:** 各閾値が実際に到達した際の通知着信。Budgetは費用を強制停止するQuotaやSpend Capではない。
- **IaC管理外:** 今回のBudget設定は既存Terraform Rootに追加していない。AWS Console側の管理を前提とする。

## 3. GitHub Actions OIDC / IAM（③）

- デプロイ経路は `main` → Build Artifact検証 → GitHub OIDC / AWS STS → S3 → SSM → Production EC2。長期AWS KeyをGitHub Actionsへ配置する構成ではない。
- OperatorのTrust Policy / IAM権限監査は `aud`、固定された `sub` の条件、および対象を絞った `ssm:SendCommand` を確認し、**3項目PASS**。
- Workflowの現在の宣言は[cd.yml](../../.github/workflows/cd.yml)と[verify-aws-oidc.yml](../../.github/workflows/verify-aws-oidc.yml)を正とする。CD Jobには `id-token: write` が付与され、AWSへのAssumeRoleはOIDCを使う。
- **注意:** Workflow中のInline Session PolicyのResource `"*"` という記述だけで実効権限が全Resourceへの無制限アクセスになるわけではない。RoleのIAM Policy、信頼条件、Session Policyの交差で評価する。詳細はAWS側の実効Policy監査結果を優先する。
- GitHub Actionsの実行成功とAWS内の最小権限監査は別の証跡である。

## 4. Trivy CI Security Scan（④）

**Merge証跡:** [PR #11 — ci: add report-only Trivy security scan](https://github.com/1716964609/GMSv5/pull/11)、Merge Commit `7692ef7e35b2b227e63721225bbd32b4df698266`（2026-11-23 01:48 JST頃）。

[CI Workflow](../../.github/workflows/ci.yml)に独立した `trivy-scan` Jobがある。宣言内容：

| 設定 | 値 |
|---|---|
| Job | `Trivy Security Scan` |
| Runner | `ubuntu-24.04`（x86_64） |
| CLI | Trivy `v0.76.2` |
| Supply-chain確認 | DownloadしたCLI tarballを固定SHA-256で検証 |
| 対象 | `trivy fs --scanners vuln,misconfig,secret .` |
| 出力 | JSON `trivy-reports/trivy.json` |
| Artifact | `trivy-security-report`、14日間保持 |
| IAM | `contents: read`、AWS OIDC不要 |
| Gate | `--exit-code 0`：**Report-only。FindingによってCIを失敗させない** |

**導入時の実測（Operator提示の初回Scan結果）:**

| Finding | 件数 | 扱い |
|---|---:|---|
| Dependency vulnerability | MEDIUM 7 | Triage未完了 |
| Terraform IaC misconfiguration | CRITICAL 3 / HIGH 4 / MEDIUM 4 / LOW 7（計18） | Triage未完了 |
| Secret detection | 0 | このScan時点の検査範囲だけでの結果 |

主な検出は、Security Groupの広いEgress（CRITICAL 3）、CloudFrontのWAF未関連付け（HIGH 2）、S3のCustomer-managed KMS未使用（HIGH 1）、Terraform上のEC2 IMDSv2設定未明示（HIGH 1）。**ScanのSeverityは検出ツールの評価**であり、本番脆弱性の成立や即時修正の必要性を個別に実証したものではない。

### 検出事項への対応方針（2026-11-23 JST）

| 検出項目 | 判断・対応方針 |
|---|---|
| IMDSv2（HIGH 1） | **対応済み。** 本番EC2の `HttpTokens=required` に合わせ、`infra/terraform/v5/main.tf` の `aws_instance.v5` に `metadata_options` を追加。`http_endpoint = "enabled"`、`http_tokens = "required"` を明示した。 |
| Security Group Egress（CRITICAL 3） | **暫定リスク受容。** OpenAI、DeepL、AWS API等の外部サービスとの通信が必要であり、宛先IPによる固定的な制限は難しい。ただし、対象Security Groupごとに必要なプロトコル・ポートへの制限余地は継続評価する。 |
| CloudFront WAF（HIGH 2） | **導入保留。** 追加費用を考慮し、現在利用中のCloudFrontプランにWAF機能が含まれるかを確認してから再判断する。 |
| S3 Customer-managed KMS（HIGH 1） | **追加導入しない方針。** 現時点の暗号化要件にはSSE-S3で十分と判断する。対象バケットの暗号化設定は別途確認する。 |
| 依存パッケージの脆弱性（MEDIUM 7） | **個別調査・修正予定。** 修正版が存在するものは互換性とテスト結果を確認して更新する。修正版がない場合は影響と残存リスクを記録する。 |

**IMDSv2変更の検証結果：**

- 変更対象：`infra/terraform/v5/main.tf` の `aws_instance.v5`
- `terraform fmt -check`：PASS
- `terraform validate`：PASS
- AWS SSO（`terraform` Profile）によるPlan：**No changes**
- `terraform plan -detailed-exitcode`：**終了コード 0**
- 本番AWSリソースへの変更：**なし**
- `terraform apply`：**未実施・不要**

この対応は本番EC2の設定変更ではなく、Terraform宣言と既存実態の整合化である。

その他のMEDIUM / LOWのIaC検出11件は、個別のTriageが未完了である。初回Scanの結果は履歴として保持し、修正後の最新状況は再Scanで確認する。JSON Artifactの保持期間は14日間。

**未実施:** 依存パッケージの更新、WAFプラン確認、S3暗号化設定の個別確認、残るIaC FindingのTriage、Blocking Gateへの昇格。これらはScan導入およびIMDSv2宣言修正とは別の作業として扱う。

## 5. IAM Identity CenterへのOperator認証移行（⑤）

### 5.1 新しい運用経路

- AWS Account：既存のGMS v5運用アカウント。Region：`ap-northeast-1`。
- IAM Identity CenterのOrganization Instanceを東京Regionに有効化。
- Identity Center User：`gms-admin`。Google AuthenticatorによるMFAを設定。MFA必須のサインイン設定。
- Permission Set：`AdministratorAccess`。Session Duration：**8時間**。
- AWS CLI SSO Session：`gms-admin`。
- STSでは `assumed-role/AWSReservedSSO_AdministratorAccess_*/gms-admin` のIdentityを確認。

この移行は、**日常操作から長期Keyとroot Credentialを外す**ためのもので、AdministratorAccess自体を最小権限化したものではない。rootユーザーはBreak-glass / 緊急管理用に残す。

### 5.2 既存プロファイルの変更と検証

| CLI Profile | 移行前 | 移行後 | AWS CLI検証 |
|---|---|---|---|
| `default` | rootの `aws login` / `login_session` | SSO | PASS |
| `terraform` | rootの `credential_process` | SSO | PASS |
| `gms-admin` | 新規SSO | SSO | PASS |
| `tvp-terraform` | IAM Userの長期Key | Keyを `Inactive` | 期待どおり `InvalidClientTokenId` |

- Operatorは `~/.aws/config` の変更前Backupを作り、`default` / `terraform` の旧root認証設定を外してSSO設定へ変更した。
- `AWS_PROFILE=gms-admin` 指定時と、`AWS_PROFILE` / `AWS_DEFAULT_PROFILE` を外したサブシェルでのDefault認証、双方で `sso` と同じSSO RoleのARNを確認した。
- 既存Terraform 4 Root Module（`v5`、`observability`、`availability`、`discord-alert-egress`）をSSO認証で `terraform plan -input=false -lock=false -no-color -detailed-exitcode` し、すべて **No changes / PASS**。**Applyは実施していない**。
- SSOでSTS Identityを確認し、本番EC2をDescribeして `running` を確認した。これは管理APIの到達性の確認であり、本番アプリケーション機能の受入試験ではない。

### 5.3 旧アクセスキーの無効化（削除ではない）

- IAM User：`tvp-terraform`。長期Access Keyは1本、作成日は2026-09-20。
- `get-access-key-last-used` は2026-11-23 02:00 JST頃のAWS Organizations使用を返した。直前のOperator調査と整合するが、他の利用が存在しない証明ではない。
- `update-access-key --status Inactive` をSSO管理者で実行。`list-access-keys` で `Inactive` を確認。
- 同Keyを使う `sts get-caller-identity --profile tvp-terraform` は `InvalidClientTokenId` で拒否され、想定どおり。
- GMS v5リポジトリの `git grep` で旧IAM User/Profile・静的AWS Credentialへの明示的参照は0件。`gh secret list` のRepository Secretも0件。
- **残存:** AWS側の旧IAM UserとInactive Key、Mac上の `~/.aws/credentials` の旧Key、以前のroot認証に関連するCache / Config Backup。**これらは削除・失効確認していない**。
- **未確認:** GMS v5以外のTVP関連プロジェクト、Git管理外スクリプト、外部連携、定期実行処理による旧Key依存。全面監査完了とは扱わない。

**IAMリソースの所有権:** 通常、旧IAM Userが作成したEC2等のAWSリソースは、そのUserのKey無効化・削除によって消えない。一方、KeyやUserのARNを直接参照するResource Policy / KMS Key Policy / 外部連携ではアクセス不能になる場合がある。rootを含め「どのPrincipalでも必ず復旧できる」とは扱わない。

## 6. 延期した改善：CloudFront→Origin Custom Header Rotation（⑥）

**決定（2026-11-23 JST）:** 現段階ではGMS v5を含むOrigin Custom Header Secretの導入・自動Rotationを**実装しない**。セキュリティ演習として将来再評価する。今回CloudFront / Nginx / Security Group / Observability EC2の設定は変更しない。

将来実施する場合の候補：

1. 既存Originへの直接到達性とIngress / TLS / Nginxの現在状態を先に監査する。
2. CloudFrontが付加する専用Origin HeaderをNginxで検証する設計の要否を判断する。ユーザーのCognito認証とは別のOrigin Access Controlである。
3. Rotationは旧Secret Aと新Secret Bを一時的にNginxが両方受け入れ、CloudFront変更の伝播を確認してからAを失効する手順を検討する。
4. SecretをGit・Terraform Plan・Logに露出させない方法、Terraform Stateへの露出、配布手順、Rollback / 失敗試験を設計する。
5. 対象はまずGMS v5のみ。Observability EC2のSSM Port Forwarding等は今回変更しない。全面自動Rotationは優先しない。

**状態:** 上記は設計候補であり、実装済み・本番検証済みの機能ではない。CloudFront Custom Headerを加えるだけではネットワーク上のOrigin直接到達を遮断したことにはならない。

### 6.1 Ingress検証結果（2026-11-23 JST）

上記候補1のうち、Security Group、CloudFrontのOrigin設定、OriginへのHTTP直接Requestを確認した。AWS側の値はAWS CLIによる実環境の確認結果（Operator実測）である。

**Security Group / Ingress：**

| 項目 | 確認結果 |
|---|---|
| 本番EC2のネットワークインターフェース | `gmsv5-app-sg`が適用されている |
| TCP 443 Inbound | AWS管理のプレフィックスリスト`com.amazonaws.global.ipv6.cloudfront.origin-facing`に限定 |
| TCP 80 Inbound | IPv6全体（`::/0`）に許可 |
| TCP 80の用途 | Let's EncryptのHTTP-01認証のため、意図的に公開 |
| Terraform State | `aws_security_group.v5`を確認。Ingressルールを独立して管理するリソースは確認されなかった |

**CloudFront（本番Distribution）：**

| 項目 | 確認結果 |
|---|---|
| Default Cache BehaviorのOrigin | `gmsv5-https-probe` |
| 同Originのドメイン | `origin-v5.sunlightjetrans.com` |
| 現行Originへの接続方式 | `https-only`、TCP 443 |
| 旧Origin | `origin.sunlightjetrans.com`が`http-only`としてDistributionに残っている |
| 参照先 | 確認したDefault Cache Behaviorと追加のCache Behaviorは、いずれも現行のHTTPS Originを参照 |

**HTTP直接アクセス試験：** CloudFrontを経由せず、IPv6でOriginへHTTP Requestを送信した。

```bash
curl -6 -sS -I \
  --connect-timeout 5 \
  --max-time 10 \
  -H 'Host: sunlightjetrans.com' \
  http://origin-v5.sunlightjetrans.com/api/health/live
```

```text
HTTP/1.1 404 Not Found
Server: nginx/1.30.5
```

まとめ：

- TCP 443はCloudFront Origin-facingのIPv6プレフィックスリストに制限されている。
- TCP 80はLet's Encrypt HTTP-01のためにIPv6全体へ公開している。HTTP-01の更新のDry Runは[DNS As-Built §13.1](dns-migration-as-built-2026-11-20.md#131-follow-up--lets-encrypt-renewal-dry-run2026-11-23)に記録した。
- CloudFrontを経由しないHTTP 80への到達と、上記のRequestに対するNginxの404応答を実測した。
- CloudFrontの現行Origin接続はHTTPS 443を使用する。
- HTTP 80を閉じる変更は行っていない。
- 今回の検証では、CloudFrontを迂回してアプリケーション本体に到達するアクセスは確認されなかった。

**境界:** この試験で確認したのは、当該Requestへの404応答である。すべてのHTTPパスの応答や、あらゆる迂回経路が存在しないことを証明したものではない。

### 6.2 残存リスク

次の2点は、今回の試験で不正アクセスが成功したことを示すものではない。構成上残るリスクとして記録する。

1. AWS管理のCloudFront Origin-facingプレフィックスリストは、CloudFront全体からの通信を許可する。自分のCloudFront Distributionだけに接続元を限定する仕組みではない。
2. Originのホスト名は、公開DNSおよび証明書透明性ログ（Certificate Transparency）から第三者が知り得る。Originのホスト名を秘密情報として扱うことはできない。

### 6.3 リスク受容判断（2026-11-23 JST）

**決定:** CloudFront Origin Custom Header Secretは現時点では導入しない。§6.2の残存リスクを受容する。

判断の前提：

- 本サービスは単独運用の小規模サービスである。
- アプリケーション側の認証は維持されている。
- 今回の検証では、アプリケーション本体へのCloudFront迂回アクセスは確認されなかった。
- CloudFront全体を許可するプレフィックスリストの制約、およびOriginホスト名の公開可能性を認識している。

これはリスクを受容する判断であり、技術的にリスクが存在しないことを意味しない。Custom Header Secretは未導入で、設定・検証もしていない。

## 7. 残課題 / Operator Checkpoints

| Priority | 課題 | 完了条件 |
|---|---|---|
| 次回整理 | Trivy Findingsの残課題 | 依存7件の修正版確認、残るIaC FindingのTriage、WAFプランとS3暗号化設定の確認 |
| 次回整理 | 旧IAM Key依存の確認 | GMS外の手動/自動クライアントを調査し、Inactive期間の影響なしを確認 |
| 次回整理 | 旧Key・IAM UserとローカルCredentialsの撤去 | 依存排除後にKey削除、User削除、Local CredentialのSecure Cleanupを確認 |
| 任意 | 管理者と日常用Permission Setの分離 | 最小権限の業務用Roleを別途設計・検証 |
| 将来 | Origin Secret Header / Rotation | 2026-11-23のIngress検証後、未導入のまま残存リスクを受容（§6.3）。再評価する場合は脅威モデルを見直し、別設計で判断 |

**Read-onlyな再検証の入口（実行時は `gms-admin` のSSO権限を利用）：**

```bash
aws sts get-caller-identity --profile gms-admin
aws iam list-access-keys --user-name tvp-terraform --profile gms-admin
aws configure list --profile default
gh workflow list
gh secret list
```

過去の検証結果は将来の状態を保証しない。再検証した日時・結果は新しい日付付きAs-Builtに追記する。

## 8. Source-of-truthと変更境界

- **実装宣言:** `.github/workflows/ci.yml` / `cd.yml` / `verify-aws-oidc.yml` / `infra/terraform/`。
- **2026-11-22〜23の手動操作・導入結果:** 本書（Operator提示の証跡に基づく）。
- **現在状態の要約:** [Production State](production-state.md)。
- **Release / Rollbackの検証:** [CI/CD As-Built](cicd-as-built-2026-11-19.md)。
- **将来のOrigin Rotation:** まだAs-Isではない。設計・運用記録が作られるまで本書の§6を「延期決定」として扱う。

本書は新規の安全性認証や完全なペネトレーションテストの合格を宣言するものではない。
