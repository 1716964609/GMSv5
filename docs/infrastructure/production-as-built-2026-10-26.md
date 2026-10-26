# GMS v5 — Production Infrastructure As-Built

**記録日:** 2026-10-25（JST）  
**対象:** GMS v5 初回本番公開時の稼働構成  
**Application Release:** `f5fb681`  
**文書種別:** As-Built / Implementation Record  
**状態:** 本番公開・認証・主要データ操作の実機検証済み

## 1. Purpose & Scope

本書は、GMS v5の初回本番公開において実際に構築したインフラ、セキュリティ対策、デプロイ方式、データ移行、および検証結果を記録する。

本書は既存の`docs/architecture.md`（実装前のArchitecture Draft）を置き換えない。設計当初の想定と、実際に構築されたシステムを区別するための実装記録である。

将来の改善計画、未着手タスク、バックログは本書の対象外とする。

## 2. Architecture Overview

本番通信経路：

1. Client Browser → `https://sunlightjetrans.com`
2. DreamHost DNS → Amazon CloudFront
3. CloudFront → HTTPS over IPv6 → `origin-v5.sunlightjetrans.com`
4. EC2 Nginx `:443` → Next.js `127.0.0.1:3000`
5. Next.js Route Handlers → SQLite → 独立EBS

主要構成：

| Component | Production Implementation |
|---|---|
| DNS | DreamHost |
| CDN / Public HTTPS | Amazon CloudFront + AWS ACM |
| Compute | EC2 Spot `t4g.small` / ARM64 |
| OS | Amazon Linux 2023 |
| Origin HTTPS | Nginx + Let's Encrypt |
| Application | Next.js 16 / Node.js 26 / TypeScript |
| Authentication | Amazon Cognito / OAuth Authorization Code + PKCE S256 |
| Database | SQLite |
| Persistent Storage | 暗号化された独立EBS gp3 |
| Process Management | systemd |
| Release Build | GitHub Actions ARM64 |
| Release Distribution | GitHub Artifact → S3 → EC2 via SSM |
| Infrastructure Provisioning | Terraformと手動設定の併用 |

単一EC2上でフロントエンド、バックエンド、データベースを動かすModular Monolithを採用した。Kubernetesやコンテナオーケストレーションは使用していない。

## 3. Networking & DNS

### 3.1 IPv6-first Architecture

パブリックIPv4の継続課金を回避するため、EC2にはパブリックIPv4を割り当てなかった。

- EC2 Instance: `i-0decf6bc205e273d9`
- Primary ENI: `eni-0ae5290a376d30579`
- ENI DeviceIndex: `0`
- DeleteOnTermination: `false`
- Public IPv4: None
- Origin IPv6: `2406:da14:190f:d655:3b6:c3da:aeb4:17c7`

プライマリENIをEC2のライフサイクルから残せる設定とした。ただし、稼働中のインスタンスからプライマリENIをデタッチすることはできない。

旧GMS v3.1のENIは再利用せず、v5専用ENIを使用した。

### 3.2 DreamHost DNS

公開ドメイン`sunlightjetrans.com`は継続利用した。

新たに追加したDNSレコード：

- Host: `origin-v5`
- Type: `AAAA`
- FQDN: `origin-v5.sunlightjetrans.com`
- Value: GMS v5 EC2のパブリックIPv6
- 登録時TTL: 5分

`dig`で正しいIPv6アドレスへ名前解決できることを確認した。

旧`origin.sunlightjetrans.com`は削除せず、CloudFrontに旧v3.1 Originを残した。

### 3.3 Security Group

GMS v5用Security Group：`sg-077aec0aabe8b2e4a`

| Inbound | Source | Purpose |
|---|---|---|
| TCP 80 / IPv6 | `::/0` | Let's Encrypt HTTP-01 |
| TCP 443 / IPv6 | CloudFront Origin-Facing IPv6 Prefix List | CloudFrontからのHTTPS |

CloudFront IPv6 Prefix List：

`pl-0f28cd4a128e7b13a`

TCP 3000と8080はSecurity Groupで公開せず、アプリケーションもループバックで待ち受けている。

TCP 80のNginxサーバーはACME検証パス以外に404を返すよう設定した。

## 4. CloudFront & TLS Architecture

### 4.1 Existing Distribution Reuse

既存CloudFront Distributionを再利用した。

- Distribution ID: `E1EF0DWDYA2XH8`
- Alias: `sunlightjetrans.com`
- Viewer Protocol Policy: HTTPからHTTPSへリダイレクト
- Default Origin: `gmsv5-https-probe`（現在のv5 Origin ID）
- Origin Domain: `origin-v5.sunlightjetrans.com`
- Origin Protocol Policy: `https-only`
- Origin IP Address Type: `ipv6`

旧GMS v3.1のOrigin定義は残した。

移行時にはまず追加Originと専用Cache Behaviorを作成し、`/__gmsv5_cf_probe_status`だけをv5へ振り分けて検証した。その後、Default Originをv5へ切り替えた。

### 4.2 Two Independent TLS Connections

**Viewer → CloudFront**

- HTTPS
- AWS Certificate Manager
- CloudFront用ACM証明書は`us-east-1`で管理
- 公開ドメイン：`sunlightjetrans.com`

**CloudFront → EC2**

- HTTPS over IPv6
- NginxでTLS終端
- Let's Encrypt ECDSA証明書
- 証明書対象：`origin-v5.sunlightjetrans.com`

CloudFrontでTLSを終端した後、EC2に向けて別のTLS接続を確立する。

### 4.3 Origin Request / Caching

本番Default Behavior：

- Cache Policy: `CachingDisabled`
- Origin Request Policy: `AllViewerExceptHostHeader`
- Allowed Methods: GET、HEAD、OPTIONS、PUT、POST、PATCH、DELETE

動的レスポンスのCloudFrontキャッシュを無効化した。

`AllViewerExceptHostHeader`により、Cookie、クエリ文字列、必要なViewerヘッダーを転送しながら、OriginのHostヘッダーをOrigin Domainに合わせる。

これによりCognito Callback、PKCE State、Session Cookie、CSRF Origin検証を処理できる。

### 4.4 Origin Authentication

CloudFrontのv5 Originに専用のカスタムヘッダーを設定した。

Header Name：`X-GMSV5-Origin-Auth`

Nginxは443番へのリクエストでこの値を照合し、不一致の場合にHTTP 403を返す。

ヘッダー値はランダム生成した秘密情報であり、本書には保存しない。

実機確認：

- CloudFront経由・正しい専用ヘッダー：HTTP 200
- 直接アクセス・ヘッダーなし：HTTP 403

Security GroupのCloudFront接続元制限と、Nginx側の秘密ヘッダー認証を組み合わせた。

## 5. Nginx & Certificate Lifecycle

### 5.1 Nginx Configuration

Amazon Linux 2023にNginx 1.30.5をインストールした。

主要設定ファイル：

| File | Purpose |
|---|---|
| `/etc/nginx/conf.d/gmsv5-local.conf` | 127.0.0.1:8080 → Next.js 3000 |
| `/etc/nginx/conf.d/gmsv5-acme-http.conf` | IPv6 TCP 80 / ACME HTTP-01 |
| `/etc/nginx/conf.d/gmsv5-https.conf` | IPv6 TCP 443 / TLS / Origin Authentication |

既定の80番Webサーバーを無効化し、専用設定へ分離した。

Nginxはsystemdで`enabled`、`active`となっていることを確認した。

### 5.2 Let's Encrypt

当初、DreamHost DNS APIを利用したDNS-01認証を検討した。

しかし、GMS v5のIPv6-only EC2から`api.dreamhost.com`と`github.com`のAAAAレコードを取得できず、IPv6による直接接続が成立しなかった。

一方、Let's EncryptのACME APIはIPv6で接続できた。

このため、DreamHost APIに依存しない**HTTP-01方式**を採用した。

- ACME Client: Certbot 2.6.0
- Validation: HTTP-01 / IPv6 / TCP 80
- Webroot: `/var/lib/letsencrypt`
- Certificate: `/etc/letsencrypt/live/origin-v5.sunlightjetrans.com/`
- 発行時に確認した期限：2027-01-22

ステージング検証と本番証明書発行の両方に成功した。

### 5.3 Certificate Renewal

証明書更新はcronではなく、Amazon Linuxのsystemd timerを使用した。

- Timer: `certbot-renew.timer`
- Service: `certbot-renew.service`
- Deploy Hook: `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`

更新時のdeploy hookは、Nginx設定の構文を検証してからNginxをリロードする。

実機確認：

- Timer：`enabled` / `active`
- `certbot renew --dry-run`：成功
- Nginx deploy hookの単独実行：成功

## 6. EC2 Runtime & Release Management

### 6.1 Runtime

- OS: Amazon Linux 2023
- Architecture: ARM64 / Graviton
- Node.js: 26
- Next.js: Standalone
- Runtime User: `gmsv5`
- Service: `gmsv5.service`
- Application Bind: `127.0.0.1:3000`

公開時のアプリケーションリリース：

`/opt/gmsv5/releases/f5fb681`

systemdのDrop-inによって作業ディレクトリとExecStartを指定している。

実際にリリースされた順にsystemd Drop-inを追加してきたため、設定には複数のリリース指定が存在する。公開時点で有効なリリースは`f5fb681`である。

### 6.2 Application Environment

環境変数ファイル：

`/etc/gmsv5/gmsv5.env`

本番公開時に設定した認証URL：

- `COGNITO_REDIRECT_URI=https://sunlightjetrans.com/api/auth/callback`
- `COGNITO_LOGOUT_URI=https://sunlightjetrans.com/`
- `GMS_PUBLIC_ORIGIN=https://sunlightjetrans.com`

CSRF用の公開Originはsystemd Drop-inから供給している。

Session Secret、Cognito Client Secret、API Credential Encryption Keyの実値は本書に記載しない。

### 6.3 CI / Release Artifact

GitHub Actions：

`.github/workflows/build-arm64.yml`

使用したBuild Environment：

- `ubuntu-24.04-arm`
- Node.js 26
- `npm ci`
- TypeScript Type Check
- ESLint
- Next.js Production Build

成果物：

- `gmsv5-linux-arm64.tar.gz`
- `gmsv5-linux-arm64.tar.gz.sha256`

GitHub Actionsは`workflow_dispatch`で起動した。

### 6.4 Deployment Procedure Used

本番構築時に実際に使用したデプロイ経路：

1. MacからGitHub Actionsを起動
2. ARM64成果物をMacへダウンロード
3. SHA-256チェックサムを検証
4. リリース番号別パスへS3アップロード
5. AWS SSMでEC2へ接続
6. S3 Dualstack Endpointを通じて成果物を取得
7. EC2上でSHA-256を再検証
8. `/opt/gmsv5/releases/<commit>`へ展開
9. systemd Drop-inで対象リリースを指定
10. サービス再起動とHTTP動作確認

EC2への成果物取得には次のIPv6対応エンドポイントを使用した。

`https://s3.dualstack.ap-northeast-1.amazonaws.com`

**GitHub ActionsのBuildは自動化済みだが、今回の本番デプロイ全体は手動操作を含む。** Git pushだけで本番EC2が自動更新される構成ではない。

## 7. Authentication & Application Security

### 7.1 Amazon Cognito

認証基盤としてAmazon Cognitoを使用した。

- OAuth 2.0 Authorization Code Flow
- PKCE S256
- OAuth State Validation
- Cognito Hosted Login
- JWT署名・Issuer・Audience等の検証
- Secure / HttpOnly / SameSite=Lax Cookie
- アプリケーションによるユーザー所有権検証

Cognito App Clientでは、localhostテスト用URLと本番HTTPS URLを両方許可した。

### 7.2 PKCE

ログインと新規登録でPKCE Verifierを生成し、SHA-256によるChallengeをCognitoへ送信する。

Verifierは有効期限付きCookieに保持し、Callback時のAuthorization Code交換に使用する。

OAuth Stateも別Cookieで照合する。

RFC 7636のテストベクトル、Verifier生成、Authorization URL、Token Exchangeを含む自動テストが成功している。

### 7.3 CSRF Protection

更新系HTTPリクエストに対し、許可された`Origin`との一致を確認する。

`Sec-Fetch-Site`が存在する場合は`same-origin`を要求する。

ログアウトはGETではなく、CSRF検証を伴うPOSTへ変更した。

実機確認：

- GET `/api/auth/logout` → 405
- 不正OriginからのPOST → 403
- 正しいOriginからのPOST → 303

### 7.4 HTTP Security Headers

Next.jsの設定で以下を付加している。

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy`
- `Content-Security-Policy`

認証関連の画面とAPIでは`Cache-Control: private, no-store`を設定した。

Nginx経由でもヘッダーが転送されることを検証した。

## 8. SQLite Persistence & Legacy Migration

### 8.1 Data Storage

SQLiteはEC2のルートボリュームではなく、独立した暗号化EBS上に保存した。

Production Database：

`/mnt/gms-data/gmsv5.sqlite`

データ用EBSは10 GiBで、アプリケーションコードとは独立している。

### 8.2 GMS v3.1 Migration

旧GMS v3.1のMySQLデータをGMS v5のSQLiteへ移行した。

移行時に記録した結果：

- 移行対象用語集：18件
- 元の用語レコード：160,634件
- 重複として整理されたレコード：317件
- 移行済みの一意な用語ペア：160,317件

この件数は**移行実行時点の記録**であり、公開後の現在件数を表すものではない。

### 8.3 Data Protection Verification

次の作業を実施した。

- SQLite整合性チェック
- Foreign Key整合性チェック
- SQLite Backup APIによるオンラインバックアップ試験
- バックアップコピーの読み込み検証
- 暗号化EBSスナップショットの取得と完了確認

以上は実施済みのバックアップ検証記録であり、継続的なバックアップ運用や災害復旧の完全性を保証するものではない。

## 9. Production Verification Evidence

### 9.1 Infrastructure

| Test | Result |
|---|---|
| DNS AAAA Resolution | PASS |
| CloudFront → HTTPS IPv6 Origin | HTTP 200 |
| CloudFront Origin Secret | 200 / Direct 403 |
| Nginx → Next.js | HTTP 200 |
| ACME HTTP-01 | PASS |
| TLS証明書検証 | PASS |
| Certbot Renewal Dry Run | PASS |
| Nginx Reload Hook | PASS |
| Unauthenticated API | HTTP 401 |

CloudFront専用テストEndpoint：

`/__gmsv5_cf_probe_status`

実際の応答：

`GMSV5-CLOUDFRONT-HTTPS-OK`

### 9.2 Authentication

本番ブラウザを使い、Cognitoへのリダイレクト、Callback、認証済み画面への遷移を確認した。

`/api/auth/me`および用語集APIで、認証済みHTTP 200を確認した。

### 9.3 Application Persistence

公開サイト上で次の操作を確認した。

- 用語集の新規作成：201
- 用語の新規作成：201
- 用語集・用語の読み取り：200
- 用語削除：200
- 用語集削除：200
- テストデータのクリーンアップ

検証には独立したテスト用用語集を使用した。

### 9.4 Automated Tests

公開リリース`f5fb681`の作成前に、以下のテスト群が成功した。

- SQLite Migration：7件
- Use Case Services：6件
- Browserless Acceptance：5件
- CSRF / PKCE：13件

合計31件。

加えてTypeScript Type CheckとESLintが成功した。

これらは実行済みの検証結果であり、全業務ユースケースの本番E2E検証完了を意味するものではない。

## 10. Production Cutover & Recovery Assets

### 10.1 Cutover Method

旧GMS v3.1を公開した状態で、CloudFrontにv5を追加Originとして登録した。

専用テストパスからv5のHTTPS接続とOrigin認証を検証した後、CloudFrontのDefault Originをv5へ変更した。

DreamHostのルートドメイン、既存CloudFront Distribution、旧v3.1 Origin定義は維持した。

### 10.2 Saved Configuration

Mac側ではCloudFrontの旧設定と切り替え設定を保存した。

EC2側ではCognito環境変数とCSRF systemd Drop-inの切り替え前バックアップを取得した。

また、旧アプリケーションリリースは番号別ディレクトリに残している。

これらは切り戻しのための実際の保存物であり、GitHubに格納する公開用アセットではない。

特にCloudFrontの設定JSONにはOrigin Authentication Secretが含まれる可能性があるため、リポジトリには追加しない。

## 11. Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| Single EC2 Modular Monolith | MVPの複雑性と費用を抑える |
| Spot t4g.small | ARM64の低コストCompute |
| Public IPv6 / No Public IPv4 | IPv4の継続課金を回避 |
| Existing CloudFront Reuse | 既存ドメイン・証明書・CDNを活用 |
| Separate EBS for SQLite | アプリケーション実行環境と永続データを分離 |
| Nginx Origin TLS | CloudFrontからEC2までHTTPSを適用 |
| HTTP-01 instead of DNS-01 | DreamHost APIのIPv6到達制約を回避 |
| systemd Certbot Timer | 証明書更新を定期実行 |
| CloudFront Prefix List + Secret Header | ネットワーク制限とアプリケーション層のOrigin認証を併用 |
| PKCE S256 + State | Authorization Code Flowの保護 |
| Cache Disabled | ユーザー固有の動的データをCDNにキャッシュしない |
| Versioned Release Directories | リリースの識別と切り戻しを容易にする |
| Staged Origin Cutover | 旧v3.1を維持したままv5接続を検証する |

## 12. Record Boundaries

本書は実際に完了した構築・検証内容を記録する。

特に、既存Architecture Draftに記載されている自動デプロイ、監視専用EC2、Prometheus/Grafana、Spot復旧自動化などは、本書で実装済みと認定していない。

また、Gitリポジトリに保持されるアプリケーションコードと、AWSおよびEC2上の手動構成は完全には一致していない。

したがって、本書は**本番構築の履歴と技術的判断を固定するAs-Built Record**であり、現時点で完全なTerraform再現性を証明するものではない。

---

**Recorded Production Application Release:** `f5fb681`  
**Public Endpoint:** `https://sunlightjetrans.com`  
**Documentation Baseline:** 2026-10-25 JST
