# GMS v5 — Baseline 1.2 Production As-Built

> **Historical（2026-11-14追記）：** 本書は2026年11月8日のBaseline 1.2リリース（`9783463`）の記録である。下の「Current release」は記録時点の値であり、現在の本番Releaseは`364d0b7`である。`9783463`はRollback用に保持されている。現在の状態は[production-state.md](production-state.md)を参照する。本文は記録時のまま変更していない。

**記録日:** 2026-11-08（JST）

**Current release:** `9783463dcdd3f3b10f743ab3e1488978b842b4be`

**Previous release:** `5f0c6e7`（`5f0c6e73f47c6c34b6a0b9016645829392072b97`）

**CR-02 LP commit:** `de30d5a`（`de30d5aafbd10e2b06583b8365782e4d00f71b01`）

**公開URL:** [sunlightjetrans.com](https://sunlightjetrans.com)

**状態:** CR-01・CR-02本番リリース報告とProduct Ownerの限定的な本番ブラウザ確認を記録。正式な性能受入は未完了。本番Migration適用ログ・systemd最終状態は要確認。

## 1. 記録の根拠と範囲

本書は2026年11月8日のBaseline 1.2リリースを記録する。証拠を「リリースコード/CIの読み取り確認」「Product Owner/Platformから提示された実績」「実行ログ未提示で要確認」に分ける。今回の文書整理ではAWS、DB、Build、Deploy、ブラウザE2Eの操作を実施していない。

| 根拠 | 確認できる内容 | 限界 |
|---|---|---|
| ローカルGit `9783463…` | 現行コード、migration v2、既存APIの版拡張、CR-02の取り込み | EC2でそのコードが実行されていることをGitだけで証明しない |
| GitHub Actions読み取り | Run `42481781676`のbuild jobとInstall and verify / Package standalone / Upload release artifactがcompleted/success | EC2切替・DB移行・systemd状態の証拠ではない |
| GitHub Artifact metadata | `gmsv5-linux-arm64`、artifact ID `13617787667`、head SHAが現行releaseと一致 | GitHub ZIPのdigestと内部tar.gzのSHA256照合を同一視しない |
| Product Owner/Platformのリリース報告 | Build ARM64 Release #10成功、Artifact SHA256 Verified、S3→SSM→systemd経由のデプロイ、本番ブラウザ観測、下記バックアップ/隔離DB検証 | チェックサム照合値、SSM実行ログ、DB移行ログ、systemd最終出力は未提示 |
| 開発工程の検証記録 | 単体・結合テスト83件成功、型チェック成功、Lintエラー0件 | 本文書更新時に再実行しておらず、本番E2Eや性能比較の実績ではない |

CIの参照先：[Build ARM64 Release #10 / Run 42481781676](https://github.com/1716964609/GMSv5/actions/runs/42481781676)、[build job](https://github.com/1716964609/GMSv5/actions/runs/42481781676/job/128735085600)。workflow定義は[build-arm64.yml](../../.github/workflows/build-arm64.yml)。run番号#10は提示されたリリース情報、job成功とartifactのhead SHAは読み取りで照合した。

10月26日の[As-Built](production-as-built-2026-10-26.md)は当時の記録として保持する。ネットワーク、IPv6 ingress/egress、認証、独立EBSの構成は同記録から継承する情報であり、今回ライブAWS監査によって再検証したものではない。

## 2. Baseline・承認・リリース履歴

| 対象 | 状態 |
|---|---|
| Baseline 1.1 | 2026年11月8日承認・本番リリース済みの履歴を維持。CR-01/CR-02以外の既存仕様を継承 |
| CR-02 / `de30d5a` | 最終HeroをProduct Owner承認、ローカル表示/基本機能確認後に確定。現行releaseへ取り込み、本番LP表示もProduct Owner確認 |
| CR-01 / `9783463…` | 改訂番号による変更検出と制限付き並列取得を実装し本番リリース。10,000 Termの選択成功はProduct Ownerの本番観測として確認 |
| 性能受入 | p95≤20秒・同条件逐次比50%以上短縮は暫定目標。正式な1/2/4比較・p95判定は未実施。リリース済みと性能目標達成済みを区別する |

仕様のSource of Truthは[Requirements](../requirements.md)、[Use Cases](../use-cases.md)、[Architecture](../architecture.md)。詳細は[Baseline 1.2設計記録](../design/baseline-1.2-proposal.md)、[CR-01技術設計](../design/cr-01-parallel-fetch-design.md)を参照する。

## 3. 現行アプリケーションの変更

### 3.1 CR-02 — 紫系LP

- 濃紫からラベンダーのグラデーション、発光ライン、柔らかな波形、ガラス調の浮遊パネルをHTML/CSS/SVGで実装。
- 装飾パネルはコンセプト表現。パネル内の小さなCONCEPT表記を保持し、Hero下の黒い説明ラベルは削除。実製品スクリーンショットではない。
- Header、Hero、Features、How It Works、Privacy、FAQ、Final CTA、Footerで構成。4つのページ内アンカー、既存登録/ログイン導線、mailto問い合わせ、料金と外部送信の説明を維持。
- 静的装飾を基本とし、prefers-reduced-motionへ対応。実画面画像2枚の撮影・合成DB準備は必須工程から撤回済み。
- Glossary/Term管理画面の既存5パネル構成、認証済みナビゲーション、CRUD/import、Settings、Cognito認証フローは今回刷新の対象外で、既存構成を維持。

### 3.2 CR-01 — 取得・選択

- 操作ごとの並列数は1/2/4を設定可能。実装の暫定デフォルト2、単一操作最大4、同一タブ合計最大4。通常のBYO Termページと最終版確認も同じ枠を使う。
- `NEXT_PUBLIC_GMS_TERM_FETCH_CONCURRENCY`はNextのビルド時公開設定。本番に注入された実効値を示すビルド環境/Networkログは未提示であり、デフォルトと実測同時数を区別する。
- 初期表示はGlossaryのみ、展開で最初のページ、後続ページは閲覧時に取得。Select Allは新規取得の最初のページから版を確定し、同じ版のキャッシュのみ再利用する。
- ページ番号で結果を結合し、メタデータ、件数、欠落/重複IDを検証。全ページ取得後の最終版確認が成功した後に限り、最新の選択集合へ原子的に反映。
- 上限は翻訳要求全体の10,000 Term ID和集合。同一IDは二重計上せず、10,001件以上は拒否。失敗・変更検出・Abort・古い世代の応答では、その操作の選択変更は0件。他の成功操作を巻き戻さない。
- 折りたたみ/離脱時は実行中・待機中要求を中止。クライアントの中止が同期SQLiteクエリの即時停止を保証するものではない。

### 3.3 改訂付きページAPI

既存の`GET /api/glossaries/{glossaryId}/terms`を拡張する。既存POSTや翻訳POSTの契約は維持する。

| 要求 | リリースコードの動作 |
|---|---|
| `?page=N` | 既存items/page/pageSize/total/hasNextPageにtermSetVersionを追加 |
| `?page=N&expectedVersion=V` | 版不一致は409 `TERM_SET_CHANGED`で拒否し、そのSelect Allは不成立 |
| `?validateOnly=true&expectedVersion=V` | 全ページ取得後に現在の版を再検証。同版ならtermSetVersionを返す |
| 不正な版/ページ、認証/所有権不成立、版行欠落 | 適切なエラーで失敗。版識別子を認可の代わりに使用しない |

GET応答はprivate/no-store。所有権・版・COUNT・本文は同じ短い読み取りトランザクションで取得する。最後の確認後の変更は凍結せず、後続翻訳で現在のDBを再検証する。COUNT一致・ID重複検査だけで厳密snapshotを保証する方式ではない。

### 3.4 SQLite migration v2

`glossary_term_revisions`にGlossaryごとのepochと単調増加revisionを保持する。既存Glossaryは新epoch/revision 0でbackfillし、既存のUser/Glossary/Term本文・ID・日時を変更しない。

- Glossary作成で改訂行を生成、Glossary名/説明の更新でも改訂を加算。
- Term INSERT/UPDATE/DELETEのトリガーで、更新と改訂を同じ書込トランザクションで反映。移動は旧/新Glossaryの双方を更新。
- JSON importも行トリガーを通り、失敗時は本文と改訂を共にrollback。Glossary/ユーザー削除は存在・所有権検証で後続取得を失敗にする。
- 応答版はサーバー起動世代・Glossary epoch・revisionから成る。再起動/別workerで保守的に失敗し、停止/再起動を伴うDB復元前のキャッシュを再利用しない。

**この節はreleaseに含まれる実装仕様である。本番DBでmigration v2が適用完了したことを示す実行ログは未提示のため要確認。** 10,000件選択のブラウザ成功や以下の隔離DB検証を、そのログの代わりにしない。

## 4. リリース・切替記録

| 項目 | 実績/確認状態 |
|---|---|
| Workflow | Build ARM64 Release #10、Run `42481781676`：Success |
| ARM64 artifact | `gmsv5-linux-arm64`。Package/Uploadステップ成功、head SHA照合済み |
| SHA256 | Verifiedとのリリース報告。実際のtar.gzチェックサム値/照合ログは未提示。本整理では再計算しない |
| 配布・切替 | S3 → EC2（SSM）→ systemdによるデプロイとの報告 |
| 公開確認 | Product Ownerが新LP表示とBYOの10,000 Term選択成功を確認 |
| SSM/切替証跡 | 対象S3 key、Command ID、実行結果、実release pathのログは要確認 |
| systemd最終状態 | `gmsv5.service`の最終active状態、MainPID/ExecStart、切替先SHA、readinessログは要確認 |
| 本番Migration結果 | 適用/skipされたversion、版管理行数、移行後整合性の実行ログは要確認 |

workflowは`workflow_dispatch`でARM64 runnerを起動し、npm ci・TypeScript・ESLint・Production Build、standalone梱包とSHA256生成、artifact uploadを行う。workflow定義に単体/結合テストの実行ステップはないため、開発83件のテスト結果をCIの実行件数として転記しない。

既存の配布手順はartifact取得→SHA256確認→S3配置→SSM経由でEC2取得/再照合→release切替→systemd/readiness確認。これは手動操作を含む手順であり、Git pushだけで完結する自動CDではない。今回の報告を超える切替コマンドの出力は推測で補わない。

## 5. バックアップ・隔離DB移行・ロールバック資産

以下はProduct Owner/Platformから提示された実績。今回の文書整理でAWS/DBへ再照会していない。

| 対象 | 報告された実績 |
|---|---|
| SQLiteオンラインバックアップ | integrity_check OK |
| 対象EBS | `vol-04932ef80e2172dd5` |
| EBS snapshot | `snap-0ec99a1b15c53dc25` |
| snapshot state | completed |
| 隔離DB migration | v1→v2成功 |
| 隔離DB行数 | Users 2、Glossaries 22、Terms 160,320、API Credentials 4 |
| 隔離DB改訂管理 | 22/22 |
| 隔離DB整合性 | integrity_check / foreign_key_check：PASS |

**これらの行数・22/22・整合性結果は隔離DBの値であり、本番移行後の実測値ではない。** バックアップの実ファイル名、保存場所、保存期間、復元試験の実行ログは未提示であり要確認。認証情報の値やDB内容は本書に記載しない。

ロールバック候補は前release `5f0c6e7`、報告されたオンラインバックアップ、completed snapshot。Git上の前releaseは確認できるが、EC2の旧releaseディレクトリ、旧artifact/チェックサム、環境設定の退避が現在揃っているかは実行ログ未提示で要確認。10月26日の旧release保持記録を、11月8日の資産確認に置き換えない。

実際の切戻しはPlatformが資産確認後にアプリ停止・対応するコード/必要ならDBの復元・再起動・readiness/整合性確認を行う。v2は追加schemaだが、旧releaseへ戻す一連の手順の成功までは未検証。稼働中のDB差替は行わず、再起動世代によって復元前のクライアント版を無効化する。

## 6. 実施済み確認と未実施事項

| 区分 | 記録 |
|---|---|
| 開発単体/結合 | 83件成功・失敗0件（取得/選択/改訂関連34件を含む）。合成データ・一時SQLite・モックで実施 |
| 開発静的検査 | 型チェック成功。ESLintエラー0件、既存のTerraform用JS警告2件 |
| 開発CSS | Next.js開発用HTML/CSSコンパイルとHTTP 200を確認。Production Buildとは区別 |
| CI | Runのbuild jobとInstall and verify/Package/Upload成功。今回の文書作業ではBuildを起動していない |
| 移行準備 | 上記の隔離DB v1→v2と整合性検証を報告済み |
| 本番ブラウザ | Product Ownerが新LPと10,000 Term選択成功を確認 |
| 未実施/証跡未提示 | 網羅的なブラウザE2E、同時数1/2/4の正式性能比較、改訂409/通信失敗/Abortの本番ブラウザ試験、全端末幅・キーボード/支援技術・SEOの総合評価、実サービス翻訳の今回の再検証、復元/rollback試験 |

開発テストの通過を、本番の409動作・同時数・Migration・systemd状態を直接実測した証拠として扱わない。追加検証の手順は[CR-01技術設計のPlatform手順](../design/cr-01-parallel-fetch-design.md)を参照する。

## 7. Product Ownerの本番性能観測

| 観測 | 値 |
|---|---|
| 旧版Select All | 約40秒 |
| 新版Select All | 約27秒 |
| 参考短縮率 | `(40 - 27) / 40 × 100 = 32.5%` |

これはProduct Ownerの観測値。同一データ、キャッシュ、RTT、端末、同時負荷、試行数の統制・生データは提示されていない。正式なp95比較ではなく、約32.5%は概数同士からの参考計算である。

観測値自体は20秒以下/50%以上短縮に届いていない。正式なp95受入判定は未実施であり、暫定目標を達成済みとはしない。10,000件選択の機能成功と性能受入完了を分けて扱う。

## 8. 残存リスクと改善候補

- 最優先の証跡補完は、本番Migration適用結果、移行後の版管理/整合性結果、systemd最終状態とrelease切替/readinessログ。
- タブの共有4枠は複数タブ/ユーザーのサーバー総負荷上限ではない。DatabaseSyncの処理が4倍並列になるわけではなく、小型EC2のCPU/I/O・クレジット・DBロックへの影響は未測定。
- 起動世代を含む版は複数worker切替時にも保守的に失敗する。実際のworker構成、再起動時の再試行操作を確認する。
- 最終確認後の変更、ライブDB差替、トリガー無効化は厳密snapshotで保護されない。後続翻訳の再検証と停止/再起動を伴う復元手順を維持する。
- 行トリガーによる大量importの書込コスト、長文Termの転送量、後半OFFSET、ページごとのCOUNT/接続/migration確認、キャッシュ再利用率を計測する。
- 次の改善候補は、同条件の1/2/4比較、RTT/JSON/SQL時間の分解、実行計画に基づく索引・COUNT反復の検討、IDだけの一括取得API等。いずれも測定と別途承認を前提とし、今回実装/実行していない。

## 9. 今回の文書整理の境界

更新は本書とREADME、Architecture、Requirements、Use Cases、Baseline 1.2設計記録、CR-01技術設計のみ。10月26日As-BuiltとBaseline 1.1履歴を保持する。GitHubのjob/artifact metadataの読み取りとローカル文書/Git確認以外に、AWS・DB・Build・Deploy操作を行わない。PushはPlatform Teamが担当する。
