# GMS v5 — Disaster Recovery Runbook

- **Owner:** Infrastructure / SRE
- **Baseline:** 2026-11-20 JST
- **Status:** 隔離Restore Drillに基づく運用手順。Production Cutover、Cold Start全自動化、再起動復旧は未検証。
- **Evidence:** [DR As-Built 2026-11-20](./dr-as-built-2026-11-20.md)
- **Current state:** [Production State](./production-state.md)、[Architecture](../architecture.md)
- **Other procedures:** [CI/CD Runbook](./cicd-operations-runbook.md)、[Spot Runbook](./spot-interruption-runbook.md)、[Availability Runbook](./availability-external-runbook.md)

本書は、同一リージョンで本番EBS Snapshotと既存Release Artifactを用いてApplicationを復旧する際の手順と判定基準を定める。実行前に最新のProduction State、Terraform、実環境および権限を照合する。過去のDrill IDやResource IDをそのまま再利用しない。コマンド例は各Gateを確認しながらOperatorが実行するものであり、単一の自動実行Scriptではない。

## 1. Objectives and scope

| Objective | 暫定目標・定義 |
|---|---|
| RPO | 24h。最新利用可能な復旧点を評価。実際のデータ損失は変更履歴と照合できる場合のみ数値化 |
| Application RTO | T0（Provisioning開始）→T1（DB/Runtime/Artifactの検証と期待SHAのReadiness 3連続成功） |
| Functional RTO | T0→T2（認証と必須UI/データ参照確認）。暫定30分 |
| Production service restoration | 実際の本番トラフィック回復を別途測定。T2は隔離環境の値であり代替しない |

事前準備、障害検知、判断、Cutover、DNS伝播等を含むEnd-to-End復旧時間は別指標として記録する。演習の実測値はAs-Builtを参照。RPO/RTOは目標でありSLAではない。

## 2. Failure classification and decision gate

| 障害 | 優先する対応 |
|---|---|
| Deployment不良、Release切替中断 | CI/CD RunbookのAutomatic Rollback / Boot Recoveryを確認。DB復元を最初の対応にしない |
| Spot中断、Instance停止 | Spot Runbook。EBSとInstanceの再開可否を確認 |
| EC2喪失、Data EBS正常 | 既存EBSを破壊せず保全。別Instanceへの安全な接続/複製を計画 |
| Data EBS喪失、SQLite破損 | 完了済みSnapshot/検証済み独立Backupから新Volumeへ復元。元Volumeを上書きしない |
| 誤更新・誤削除 | 変更時点を特定し、適切な復旧点と復旧後の損失を評価。最新Snapshotが常に正解ではない |
| CloudFront/DNS/Cognito/監視基盤障害 | 依存サービスを個別診断。App復旧だけで全体障害が解決するとは限らない |
| リージョン/アカウント喪失 | 本Runbookの受入範囲外。別リージョン・別アカウントのBackup/Artifact/IdP戦略が必要 |

本番DBを変更する操作、既存Instanceの停止、Origin切替、Cognito変更はIncident Ownerの承認を得る。演習時は一切行わない。障害原因によってはCD停止、Single Writerの確保なども必要（CI/CD Runbook参照）。

## 3. Preflight (read-only)

1. 承認済み最小権限Operator Roleを使用（root CLIは不可を原則とする）。`aws sts get-caller-identity`、Region、Account、対象資源を照合する。
2. Production / ObservabilityのEC2、EBS、SG、SSM Status、公開Readiness、CD有効状態、進行中Deployment/Lockを記録。Secret値を出力しない。
3. Data VolumeのTag/ID、AZ、暗号化/KMS、Volume Type・Size・IOPS・Throughput、DeleteOnTermination、Mount Point、Filesystem UUID、DB Pathを確認。
4. SnapshotのOwner、Source Volume、State、StartTime、Encryption、KMS permissionを確認。最新の正常な候補を選び、IDを固定。破損発生時刻も考慮し、古い正常候補を残す。
5. 対象Release SHA、S3 Artifact、Checksum、Manifest、Schema Fingerprint、Node Version、復旧Utilityの同一Commitを確認。Artifactまたは外部Runtime取得不能の場合は代替経路を用意。
6. Cognito Callback/Logout、必要なParameterの名前・読取権限、既存ユーザーによる演習可能性を確認。Production Clientを演習のために書き換えない。
7. 一時資源の予算、容量、AMI ARM64、AZ、Subnet、IGW/Endpoint、SSM接続、S3/KMS到達性を確認。Snapshotからの初期Read Latencyは復旧時に考慮する。
8. Localに権限制限済みEvidence Directoryを作り、Run ID、T0以前の準備事項、対象Release/Snapshotを保存。SecretやDB内容をログへ入れない。

コマンド例（変数は当日の実環境から安全に確定する。以下の`:?`は未設定時に停止するためのGuard）：

```bash
export AWS_DEFAULT_REGION=ap-northeast-1 AWS_PAGER=""
aws sts get-caller-identity
aws ec2 describe-instances --instance-ids "${PROD_INSTANCE:?}"
aws ec2 describe-volumes --volume-ids "${PROD_DATA_VOLUME:?}"
aws ec2 describe-snapshots --owner-ids self \
  --filters "Name=volume-id,Values=$PROD_DATA_VOLUME" \
  --query 'Snapshots[].{ID:SnapshotId,State:State,Start:StartTime,Encrypted:Encrypted}'
aws s3api head-object --bucket "${RELEASE_BUCKET:?}" \
  --key "releases/${RELEASE_SHA:?}/gmsv5-linux-arm64.tar.gz"
```

公開Readinessは `GET /api/health/ready` でHTTP 200、status=ready、SHA一致を確認する。単にLivenessが通ることだけでは合格にしない。

## 4. Isolation and provisioning

### 4.1 Safety design

- 演習専用 `DrillId=gmsv5-dr-<UTC timestamp>` を新規発行し、EC2/EBS/SG/ENI/IAM Role/Profileに付与する。作成直後に正確なIDをLocalに記録する。
- 本番Terraform Stateで一時DR資源を管理しない。既存Snapshot・Artifactは読み取り専用。DRから本番ReleaseのPublish、Deployment Lock操作、本番SSM Command、DLM変更を禁止。
- DR専用SGはInboundなし、必要なOutboundのみ。SSM経由で管理・Port Forwardingする。実測ではdual-stack Subnet、DR Public IPv4付きOn-Demand ARM64を使用。本番IPv6-only/Spotの完全再現ではない。
- Dedicated IAM Role/Profile。SSM Managed Policyと必要なArtifact PrefixのS3 GetObject、指定Cognito client-secret ParameterのGetParameter、必要なKMS権限のみを付与し、他のSecret取得を制限する。汎用SSM権限の範囲も監査する。
- 既存VolumeをDetachしない。Snapshotから新規Volumeを作成。Root DeleteOnTermination=true、復元Data Volumeはfalseとし後で個別確認する。

### 4.2 Provisioning sequence

T0をUTC epochとISO 8601で記録してから、SG/IAM、復元Volume、EC2起動、Volume Attachment、SSM Onlineの順に実行する。復元VolumeとInstanceは同一AZ。AMIはAL2023 ARM64を選び、IMDSv2をrequireする。User DataにはSecretを埋め込まない。

例：復元Volume作成（対象Snapshot IDとAZを確認した後にのみ実行）。

```bash
aws ec2 create-volume \
  --snapshot-id "${RECOVERY_SNAPSHOT:?}" \
  --availability-zone "${DR_AZ:?}" \
  --volume-type gp3 --size 10 --iops 3000 --throughput 125 \
  --encrypted \
  --tag-specifications "ResourceType=volume,Tags=[{Key=DrillId,Value=${DR_ID:?}}]"
```

Size/IOPS/Throughputは演習時の値。現在のSourceが異なる場合は所要仕様に合わせる（Snapshotより小さいSizeは不可）。KMS Keyは現行設定に合わせる。EC2/IAM作成は当日のNetwork/IAM条件で別途確認する。AMIやSSM Endpointを固定値で流用しない。

```bash
aws ec2 wait volume-available --volume-ids "${DR_VOLUME:?}"
aws ec2 wait instance-running --instance-ids "${DR_INSTANCE:?}"
aws ec2 attach-volume --volume-id "$DR_VOLUME" \
  --instance-id "$DR_INSTANCE" --device /dev/sdf
aws ec2 wait volume-in-use --volume-ids "$DR_VOLUME"
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$DR_INSTANCE"
```

各作成・待機結果をEvidenceに追記。SSM Onlineでない場合はDNS、443 egress、SSM Agent/Instance Role、Endpointを確認し、許可なくSSH inboundを開けない。

## 5. Restore disk and verify SQLite before migration

SSMでDRに接続し、IMDSv2で実際のInstance IDを記録したDRと照合する。Nitroでは指定DeviceとOS上のNVMe Device名が一致しない場合がある。`lsblk -o NAME,SIZE,FSTYPE,UUID,SERIAL,MOUNTPOINTS` 等で対象Volume IDのSerialを照合してからMountする。

- Snapshot復元済みFilesystemを使用し、`mkfs`、`wipefs` は実行しない。
- XFSのUUID重複がないことを確認。重複がある場合は復元用Mount方法を別途判断し、元Volumeへ手を加えない。
- 専用Mount Point `/mnt/gms-data` へMountし、対象Device・容量・権限を確認。演習ではrw,nodev,nosuid。本番DBのPathと復元DBを混同しない。
- DB、journal、WAL/SHM（存在する場合）を一体として保全する。journalを手動削除しない。物理Snapshotはファイルシステム/アプリ整合性を自動保証しない。

DRのDBを使い、`PRAGMA quick_check`、`PRAGMA integrity_check`、`PRAGMA foreign_key_check`、Table存在、匿名の件数、適用Migrationを確認する。読み取り専用Openがhot journal等で失敗する場合は、DRコピー上だけで正常なSQLite Recoveryを行う手順を別途確認する。元Snapshot/元Volumeには書き込まない。

検証例（DR上、対象PathのMount・ID確認後）：

```bash
python3 - <<'PY'
import sqlite3
p = '/mnt/gms-data/gmsv5.sqlite'
with sqlite3.connect('file:' + p + '?mode=ro', uri=True) as db:
    for pragma in ('quick_check', 'integrity_check'):
        result = [r[0] for r in db.execute('PRAGMA ' + pragma)]
        assert result == ['ok'], pragma + ' failed'
        print(pragma, 'ok')
    fk = sum(1 for _ in db.execute('PRAGMA foreign_key_check'))
    print('foreign_key_violations', fk)
    assert fk == 0
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ('users','glossaries','terms','api_credentials','glossary_term_revisions','schema_migrations'):
        print(t, db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] if t in tables else 'MISSING')
PY
```

Migration前に旧SchemaのTableが存在しないことはあり得る。対象ReleaseのMigrationと照合して判断し、必要Tableを黙って無視しない。SQLiteの値・ユーザー名・Term・Credentialは表示しない。事前件数差をそのまま損失件数としない。

## 6. Runtime, artifact and migration

### 6.1 Runtime and artifact

1. 対象ManifestのNode VersionとArchitectureを確認し、公式ARM64 ArchiveとChecksumを検証して専用Pathへ配置する。
2. AL2023で `libatomic.so.1` 不足を検出した実績がある。`ldd <node binary>` のnot foundを確認し、必要ならDR側で `sudo dnf install -y libatomic`。NodeのVersionをManifestと照合する。
3. S3の固定Release PrefixからArtifact、SHA256、Manifestを取得。SHA256・OS/Architecture・Build SHA・Schema Fingerprintを照合する。不一致なら停止。
4. 対象Commitの `infra/deployment/` のartifact/download/stage/safe_extract等と既存Migration Utilityを確認して利用する。Deployment Engineの本番Lockや本番へのPublishを呼び出さない。Releaseの実行形式に応じて導入する。
5. 専用User/Directoryへ安全に展開し、検証済みReleaseへのcurrent symlinkを設定する。既存本番ディレクトリを変更しない。

当日のRecovery Source Bundleには`infra/deployment/`の必要なPython Utility、`scripts/migrate.ts`、DB Migration/SQLite/Domain Error関連Sourceを含めた。実際の呼び出し方は対象CommitのCLI Usage、CI/CD Runbookと照合する。完全な復旧Bootstrap Scriptは本Runbookには同梱していない。次回は再利用可能なIaC/Scriptとして別途管理することを推奨。

### 6.2 Migration

- DRのみで、元SnapshotのSchemaとRelease ManifestのFingerprintを比較する。
- 必要なMigrationを同じReleaseの実装で適用。Migration前の復旧点を保持する。演習はMigration 1→2を成功させたが、任意のSchema間のDown Migrationは保証しない。
- 5章のDB検証を再実施し、必須Table、Migration履歴、件数、FK/Integrityを確認。失敗時はserviceを起動せず、元Snapshotから別Volumeへやり直すか原因を調査。

### 6.3 Environment and service

`/etc/gmsv5-dr/dr.env`（root所有、0600）などDR専用の設定ファイルを作り、対象Releaseの `.env.example` の契約を確認する。必要なSecretは許可されたSSM Parameterから安全に取得し、stdout、履歴、EvidenceやGitへ出さない。Session/Encryption Keyは演習専用に生成する。

| 設定 | 隔離演習時 |
|---|---|
| NODE_ENV / HOSTNAME / PORT | production / 127.0.0.1 / 3000 |
| GMS_DATABASE_PATH | `/mnt/gms-data/gmsv5.sqlite` |
| GMS_BUILD_SHA | Artifactの対象SHAと同一（Buildに埋め込まれた値を確認する） |
| GMS_PUBLIC_ORIGIN | `http://localhost:3100` |
| Cognito | 既存Clientのlocalhost Callback/Logoutを利用。設定変更なし |
| GMS_OTEL_ENABLED / GMS_RUM_ENABLED | false / false |
| Browser RUM | Build時に無効なArtifactを確認する（Runtimeだけでは変更不可） |
| DEEPL_API_URL / OPENAI_API_URL | 利用不能なloopback endpoint（実APIは呼ばない） |

DR専用systemd UnitはDedicated User（例 `gmsdr`）、WorkingDirectory=検証済みcurrent、ExecStart=Manifest準拠Node+Standalone `server.js`、EnvironmentFile=DR設定、RequiresMountsFor=/mnt/gms-data、起動直前Mount Gateを設定。Root DBへの意図しない書き込みを防止する。`systemctl daemon-reload`、DR Unitのみ起動、status/journal確認。演習では自動起動/fstab再起動復旧は検証していない。

## 7. Readiness and functional verification

T1は対象Mount、Migration、Runtime、Artifactの検証後、期待SHAのReadinessが3回連続成功した時刻。例（DRホスト上、`RELEASE_SHA`を設定済み）：

```bash
(
  set -euo pipefail
  for i in 1 2 3; do
    curl --fail --silent --show-error --max-time 10 \
      http://127.0.0.1:3000/api/health/ready | \
      python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ready" and d["release"]==sys.argv[1]; print("ready",d["release"])' "$RELEASE_SHA"
    sleep 1
  done
  date -u '+%Y-%m-%dT%H:%M:%SZ'
)
```

失敗時は3連続成功を最初から取得する。時刻をEvidenceに保存（上記例は最終sleepも含むため厳密な計測では最終成功時点を直接記録する）。

MacからDRにのみPort Forwardingする。

```bash
aws ssm start-session --target "${DR_INSTANCE:?}" \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3100"]}'
```

`http://localhost:3100` で以下を確認する。

1. 期待ReleaseのReadiness。
2. 既存Cognitoユーザーでログイン成功、認証後画面が表示される。
3. 用語集とTermの参照、必要なPage遷移が成功。
4. BrowserのCookie/Sessionは外部へ出さない。新規登録・認証設定変更・外部API利用はしない。

T2は上記必須機能を確認した時点（試験対象の認証/UI/Read-only操作のみ）。T0/T1/T2をUTC epochで保存して秒差を計算する。Source Snapshot StartTimeからT0時点の復旧点経過時間を計算する。承認済み目標に満たない場合はFAILとし、実際のデータ損失は別評価。利用環境・準備範囲を記録する。

## 8. Production cutover — separate change approval required

**本節は設計上の判定Gateであり、本番で実行済みの手順ではない。隔離演習では実行禁止。**

実障害でDRを本番に昇格する必要がある場合、Incident OwnerとInfrastructureが個別計画を作成する。

| Gate | 条件 |
|---|---|
| G1: Incident scope | 旧環境の状態、復旧点、データ欠損・重複の可能性を評価。RPO超過は責任者判断 |
| G2: Single writer | 旧本番への書込み停止/Fencingを確認。復元DBと旧DBの同時書込みを防止 |
| G3: Deployment | CDを必要に応じ安全停止、実行中Deployment/Lockの状態を確認。対象Instance/Role/Stateを新構成に整合 |
| G4: Runtime & secrets | 本番用Secret、Credential復号Key、Cognito、Origin認証/TLS、環境変数を正規経路で復元（演習用Keyを昇格しない） |
| G5: Network | CloudFront→Nginx HTTPS/IPv6、Origin Protection、証明書、SG/ENI、公開DNS、Callback、Monitoring/Alertingを確認 |
| G6: Acceptance | 本番Readiness、認証、代表業務、必要な書込み・翻訳操作、データ整合性、Telemetry、Backup、負荷を承認条件に従い確認 |
| G7: Cutover and rollback | 旧環境保持、切替時刻、切戻し条件、切替後の新規書込みの取り扱いを文書化してから実行 |

Cutover後の書込みがある場合、単純に旧Originへ戻してはならない。データ分岐と損失の扱いを決定する。復旧先をTerraform/CD/DLMの正式な管理対象に移す手順は未検証で、別のChange Planを要する。

## 9. Optional performance verification

DRのRTO測定を完了してから、必要に応じ[CR-01](../design/cr-01-parallel-fetch-design.md)に従う別試験を実施する。復元DBや本番ユーザーCredentialを使う必要はない。合成DB、Dedicated User、隔離service、互いに独立したBuild-time Worker設定、固定Release、Telemetry無効、Provider Call禁止を用いる。

各3 Warm-up後30回以上、p50/p95/max、成功率、全ページ/Revision検証、実最大並列数、CPU/RSS/I/O、SQLiteエラー、CPU Creditを記録する。試験結果は通信経路、DB規模、他負荷、順序、計測定義とセットで扱う。正式な本番受入では別途他APIのp95負荷ガード、複数ユーザー、長時間負荷などを確認する。詳細な実測値はAs-Builtにのみ記録する。

## 10. Evidence preservation and isolated cleanup

Cleanup前にRecovery Log、時系列、RPO/RTO、DB検査、Artifact Checksum、Resource ID/Tag、Benchmark生データ、Resource Summary、CloudWatchをLocalへ退避。スクリーンショットやHARは個人情報とSecretを除去し、元DB/TokenをGitに保存しない。1秒Monitor生データも必要ならInstanceを削除する前に取り出す。Evidence Inventoryと保存先を確認する。

### 10.1 Safety gate

DR Instance、Root/Data Volume、ENI、SG、IAM Role/ProfileのIDとDrillIdを、作成時のLocal Resource Inventoryおよび現在のAWS APIの両方で照合する。AWS Account/Regionを再確認。対象に本番・監視・DLM・復元元Snapshotが含まれないことを確認する。Tagが不一致/欠落、予期せぬAttachment、ID未確定なら停止する。Tag一覧をそのまま削除ループに入力しない。

### 10.2 Deletion sequence (DR only)

1. DR専用SSM Tunnelを閉じる。Evidence保全済みと確認。
2. 正確なDR Instance IDとTagを照合してTerminate、`aws ec2 wait instance-terminated` で完了を待つ。Root EBSとENIの自動削除を確認。
3. DR Data VolumeのID/Tag、State=available、Attachments=0を再確認し、同VolumeのみDelete、`aws ec2 wait volume-deleted`。
4. DR SGのID/TagとENI参照0件を確認してDelete。依存が残る場合は無関係な資源を削除せず原因調査。
5. DR IAM Role/ProfileのName/Tagと構成を確認し、RoleをProfileからRemove、ProfileをDelete、作成時のManaged PolicyをDetach、DR専用Inline PolicyをDelete、RoleをDelete。
6. DR TokenをLocalから削除し、該当DR用Port Listenerが残っていないことを確認。Local Evidenceは保持。

Terraform destroyは使用しない。上記は隔離演習のみ。昇格済みDRや唯一の復旧コピーを削除しない。各操作の結果を独立して確認する。

### 10.3 Residual audit (read-only)

```bash
: "${DR_ID:?}"
aws ec2 describe-instances \
  --filters "Name=tag:DrillId,Values=$DR_ID" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
  --query 'Reservations[].Instances[].InstanceId'
aws ec2 describe-volumes --filters "Name=tag:DrillId,Values=$DR_ID" --query 'Volumes[].VolumeId'
aws ec2 describe-security-groups --filters "Name=tag:DrillId,Values=$DR_ID" --query 'SecurityGroups[].GroupId'
aws ec2 describe-network-interfaces --filters "Name=tag:DrillId,Values=$DR_ID" --query 'NetworkInterfaces[].NetworkInterfaceId'
aws ec2 describe-snapshots --owner-ids self --filters "Name=tag:DrillId,Values=$DR_ID" --query 'Snapshots[].SnapshotId'
aws iam list-roles --query "Roles[?starts_with(RoleName, '$DR_ID')].RoleName"
aws iam list-instance-profiles --query "InstanceProfiles[?starts_with(InstanceProfileName, '$DR_ID')].InstanceProfileName"
```

すべて空配列が期待値。作成時Inventoryの全IDも照合する。APIエラーを空配列と扱わない。終了済みEC2の表示は課金中Instanceの残存とは異なる。

### 10.4 Post-drill production checks

演習前のInventoryと以下を比較し、結果と時刻を保存する。

- Production / Observability EC2のRunning、System/Instance/EBS Status、SSM Online。
- Production Data EBSのID、Attachment、State、DeleteOnTermination。
- 既存手動Snapshot、DLM policy、最新Snapshotの状態。
- 公開Readiness 3回連続HTTP 200・予定SHA一致。必要に応じ認証/UIを確認。
- Observabilityの各Container、Grafana DB Health、Prometheus scrape、Tempo/OTel受信、Alerting/Discord経路。実送信試験の有無を明示。
- CD設定、最新Deployment Run/Lock、Terraform State/Git Working Tree。変更がないことを照合。

本番チェック不足は未確認として記録。成功したチェックの範囲だけで「影響なし」と表現する。今回の演習では監視内部再検証など一部項目を実施していない（As-Built参照）。

## 11. Continuous backup operations — DLM

現在の宣言は `infra/terraform/v5/backup.tf`、適用結果はAs-Built、最新状態はProduction Stateを正とする。

### 11.1 Daily check / first execution acceptance

```bash
aws dlm get-lifecycle-policy --policy-id "${DLM_POLICY_ID:?}" \
  --query 'Policy.{State:State,Details:PolicyDetails}'
aws ec2 describe-snapshots --owner-ids self \
  --filters "Name=volume-id,Values=${PROD_DATA_VOLUME:?}" \
            "Name=tag:GMSBackupPolicy,Values=daily-data" \
  --query 'reverse(sort_by(Snapshots,&StartTime))[].{ID:SnapshotId,State:State,Start:StartTime,Encrypted:Encrypted}'
```

実際のスケジュール開始時刻、完了済みSnapshotの存在、元Volume、Encryption、Tag、復元点の経過時間を確認（Schedule時刻は実行時刻の保証ではない）。Policy ENABLEDだけでBackup成功としない。Snapshot失敗やRPO超過は調査・通知対象。運用時にはDLMの失敗監視を別途実装する。

### 11.2 Retention acceptance

3回目以降の正常作成とRetention動作を確認し、対象Policyの最新2世代が維持されることを確認する。生成中/削除待ちの一時的な件数差を考慮する。手動Snapshotを含む全Snapshotが2件になるという意味ではない。元の手動Snapshotは保持し、DLM専用Delete Guardの対象Tagを不用意に付与しない。IAMのDenyはDLM Roleに対する制約であり、全管理者の削除を禁止する機能ではない。

### 11.3 Restore assurance and limitations

定期的に新規隔離VolumeへのRestoreとSQLite Integrity/Functional Testを実施する。SQLite Online Backupの独立保管、アプリ整合性、KMS/Secret/IdPの復旧性、リージョン障害への対策、Observability Backupは別途設計する。24hのScheduleだけでは24h RPO保証にならない。

## 12. Change management and source of truth

- 実行結果と測定条件は日付付きAs-Builtへ、恒久構成はArchitecture/Production Stateへ、手順変更は本Runbookへ記録する。
- `backup.tf` のApply済みStateと宣言を照合しGit管理する。State、Plan、SecretはCommitしない。
- 現行CDはmainへのPush/Mergeを契機にProductionへ自動Deploymentする。Documentationのみでも起動するため、PR/CIとMerge承認を分け、Merge後のCD/Readinessを確認する。
- DNS移行、Worker 4本番適用、DLM実行検証は独立した変更。今回のDR Cleanupで本番リソースは削除しない。
