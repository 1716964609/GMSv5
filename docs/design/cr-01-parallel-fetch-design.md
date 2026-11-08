# CR-01 — 改訂番号による変更検出と制限付き並列取得

**状態：CR-01本番反映済み。正式な性能受入は未完了。本番Migration適用ログ・systemd最終状態は要確認。**

CR-02の確定点は`de30d5aafbd10e2b06583b8365782e4d00f71b01`。本変更でCR-02の実装ファイルは変更しない。CR-01は今回の指示によりV方式（変更検出・操作全体の失敗）へ確定し、厳密な開始時点snapshotは必須としない。

## 1. トレーサビリティと調査結果

| 要件 | 実装箇所 | 確認対象 |
|---|---|---|
| FR-733 | `term-fetch-scheduler.ts`、`term-page-loader.ts`、`bulk-term-selection.ts`、BYOページ | 1/2/4 worker、タブ合計4、原子的和集合、中止・世代 |
| FR-734 | 8章の測定計画 | 20秒/50%は暫定目標。今回のローカルテスト時間で達成と判定しない |
| FR-735 | migration v2、`db/term-pages.ts`、`services/term-pages.ts`、既存Term GET | 更新の網羅、版と本文の整合、最終検証、キャッシュ |
| UC-011 | 追加基本フロー、A12/A13、追加受入条件 | 完全取得まで未確定、変更/失敗/中止時の選択維持 |

基準コミットの`fetchAllPages()`は逐次await、APIは100件単位のLIMIT/OFFSETだった。Glossaryのupdated_atはTerm CRUD時に更新されず、改訂識別子として流用できない。APIの各要求でDB接続を開閉し、COUNTとSELECTも明示的な読取トランザクションで揃っていなかった。

ローカル約97ページが高速、旧版旧版本番10,000件規模で約40秒という値はProduct Owner観測であり、今回独立測定はしていない。100件/ページなら10,000件は100ページ、最初の1ページだけがキャッシュ済みなら残り99要求となる。97ページという観測のキャッシュ/データ条件は未確定である。

## 2. 保証する時点と限界

T0はSelect Allの最初のページをサーバーが同じ読取トランザクションで読む時点。Tvは全ページ取得後の最終改訂確認。Tcはクライアントの選択一括反映時点とする。

- すべてのページがT0の版に一致し、完全性検証とTvでの改訂確認に成功してからTcで反映する。
- 追加・削除だけでなく、同件数の削除/追加、同IDの本文更新、移動、JSON importも改訂を変える。検出した場合は全操作を失敗とし、当該操作の追加/解除は0件。
- COUNT一致・ID一意性だけでsnapshotを証明しない。変更中でもT0の履歴を返し続ける方式ではない。
- Tv後～Tcの変更と、その後の翻訳までの変更は防げない。既存翻訳APIは現在のDBで所有権・削除・方向・衝突・外部API上限を再検証する。
- 全選択の表示は最後に検証した集合に対する状態であり、DBの最新状態を永久保証するものではない。版変更が分かったキャッシュを全件完了表示に使わず、選択済みID自体は勝手に解除しない。
- 稼働中のDBファイル差替、トリガー削除、改訂メタデータの手動巻戻しは通常更新経路に含めない。DB復元はサーバー停止・再起動が前提。

## 3. 改訂番号とDB移行

migration v2 `glossary_term_revisions`は技術メタデータ表を追加する。`glossary_id`は既存GlossaryへのFK（削除cascade）、`epoch`はランダム32桁hex、`revision`は非負SQLite INTEGER。v1の既存Glossaryはepoch新規/revision 0で初期化する。既存User/Glossary/Term本文、ID、所有者、日時は変更しない。

| 更新経路 | 同一書込トランザクション内の処理 |
|---|---|
| Glossary作成 | AFTER INSERTでepoch/revision行を作成 |
| Glossary更新 | AFTER UPDATEでrevision +1。名前/説明の変更も保守的に検出 |
| Term作成 | AFTER INSERTで対象Glossaryのrevision +1 |
| Term更新 | AFTER UPDATEで旧/新Glossaryを更新。同じGlossaryなら1回。説明・言語などの更新も保守的に無効化 |
| Term削除 | AFTER DELETEで元Glossaryのrevision +1 |
| JSON import | 既存`importTermsTransaction()`の各INSERTが同じトリガーを通る。import失敗時はTermと改訂の両方rollback |
| Glossary/ユーザー削除 | FK cascadeでデータと版行を削除。後続のページ/最終検証は存在・所有権確認で失敗 |
| 直接SQLの通常Term INSERT/UPDATE/DELETE | 同じトリガーを通る。REPLACEやスキーマ管理・トリガー無効化を新しい更新経路として導入する場合は別レビューが必要 |

改訂値はSQLで文字列化して応答版に含め、JSの整数精度で丸めない。SQLite整数のoverflowが実数へ変化した場合はCHECK制約で書込自体を失敗させ、版を再利用しない。版行が欠落した場合は503で失敗し、改訂を推測して成功させない。

応答の`termSetVersion`は**サーバー起動世代 : Glossary epoch : revision**。起動世代はサーバーモジュール起動時に生成する。再起動/HMRや別workerに切り替わると、保守的に不一致として失敗する。復元前のキャッシュと復元後DBが同じ改訂値でも、停止/再起動により区別できる。複数worker間で操作を継続させる可用性は別設計であり、Platformは単一worker構成/挙動を確認する。

[SQLite CREATE TRIGGER](https://www.sqlite.org/lang_createtrigger.html)の行トリガーを利用するため、10,000件importでは行ごとの改訂更新コストが発生する。書込性能もPlatform測定対象とする。

移行は既存`runMigrations()`へ追加しており、既存`npm run db:migrate`と要求時のmigration確認で適用される。Platformは停止・バックアップ・**隔離コピーでv1→v2を検証した後**に承認された対象へ適用する。今回のエージェントは本番DBへ接続/適用していない。破壊的down migrationは用意せず、復元時はアプリ停止・DB復元・再起動を行う。

## 4. 既存Term GET APIの拡張

パス：`GET /api/glossaries/{glossaryId}/terms`

| 要求 | 応答/エラー |
|---|---|
| `?page=N` | 従来のitems/page/pageSize/total/hasNextPageを維持し、`termSetVersion`を追加 |
| `?page=N&expectedVersion=V` | 同じ版ならページを返し、不一致は409 `TERM_SET_CHANGED` |
| `?validateOnly=true&expectedVersion=V` | 同じ版なら`{termSetVersion: V}`。Term本文は取得しない |
| validateOnlyで版なし、不正な版形式、不正なpage | 400。validateOnlyは省略またはtrueのみ |
| 未認証、所有権不成立、Glossary削除 | 既存`protectedRequest()`と所有権検証に従って拒否。versionは認可の代替にしない |
| 版メタデータ欠落 | 503 `TERM_REVISION_UNAVAILABLE` |

すべてのGET応答を`Cache-Control: private, no-store`とする。POSTによるTerm登録や翻訳POSTの契約は変えない。

`db/term-pages.ts`はBEGINからCOMMITまで、Glossary所有権・版・COUNT・itemsを同期読取する。別接続が途中で更新しても、この応答内の本文と版を別時点にしない。最終検証は別の短い読取で現在の版を確認する。ネットワーク待ちをDBトランザクション内に入れない。[SQLite read transactions](https://www.sqlite.org/lang_transaction.html)を参照。

## 5. ワーカープールとタブ共有枠

- `term-page-loader.ts`は最初のページを新規取得して総ページ数と版を確定し、残りを1/2/4 workerで取得する。
- 結果をページ番号のMapへ格納し、1…最終ページの順に結合する。番号、版、総数、pageSize、hasNextPage、各ページ件数、IDの欠落/重複を確認する。重複除去だけで不整合を隠さない。
- 同じ版・総数・pageSizeで検証済みのキャッシュだけ再利用する。完全キャッシュでも最初のページと最後の版はサーバーへ要求する。
- `TermFetchScheduler`はタブのWindow上のSymbolで共有し、ルート再マウント/HMRでも古い取得枠を捨てない。SSRユーザー間のグローバル共有はしない。
- 通常のBYO Termページ、Select All、最終確認が同じ最大4枠を使う。Glossary一覧・認証・翻訳APIへの総合レート制限を追加するものではない。
- FIFO待機キューと少数workerで大量fetchの一括開始を避ける。待機中にAbortされた要求は取り除いて開始しない。実行枠はfetchと本文処理がsettleするまで保持し、finallyで1回だけ返却する。
- 操作内で1件でも失敗したら同操作の残りworkerを中止し、部分結果を返さない。他Glossaryの独立操作まで中止しない。
- `NEXT_PUBLIC_GMS_TERM_FETCH_CONCURRENCY=1|2|4`。暫定デフォルト2、操作最大4、タブ最大4。Platformは比較環境のdev serverを再起動して設定を反映する。ProductionではNextの公開環境変数のビルド時反映に注意する（BuildはPlatform担当）。

この上限はクライアントの未完了Term要求数であり、別タブ/ユーザーの総数やサーバーSQLの実行残りを制限するものではない。Abortは同期SQLiteクエリの即時停止を保証しない。

## 6. 選択の原子的反映とUI状態

`bulk-term-selection.ts:executeSelectAll()`は、全ページと最終改訂確認が成功した後に現行世代を再検証し、その時点の`selectedRef.current`へ既存のSelect All/Clear Allルールを適用する。非同期処理を挟まず和集合を判定し、10,000件以内なら1回だけcommitする。

- 10,000件は翻訳要求全体のTerm ID和集合。既存選択との重なりは二重計上しない。
- 対象Glossaryが10,001件以上なら最初のページの総数から拒否でき、部分取得を選択に追加しない。
- 失敗/Abort/世代失効時に古い選択集合を復元しない。他操作の成功選択や個別選択を保持する。
- 同一Glossaryの新要求・折りたたみ・画面離脱は既存`TermRequestCoordinator`で中止/世代無効化する。Abortを無視する遅い応答もcommitしない。
- 成功ページのキャッシュは版で揃えて保存する。取得失敗時は対象の全件キャッシュを無効化し、再試行を最初のページへ案内する。選択IDは不変とする。
- 初期表示はGlossaryのみ、展開は最初のページ、後続は閲覧要求で取得する。用語なし翻訳、所有権・方向・衝突解決・外部API制限は維持する。

## 7. 厳密snapshot方式との比較（今回採用しない）

| 方式 | 特性/必要変更 |
|---|---|
| 単一読取トランザクションで全ID取得 | T0のID集合を固定して返せる。全ID取得APIとUIのID集合保持が必要。永続スキーマ変更は必須ではないが、現在のページ取得方式から契約を変更する |
| 不変snapshotをページ配信 | 更新中もT0の集合を返せる。snapshot生成・token/所有権・失効/解放、メモリ上限または保存用テーブルが必要。小型EC2のメモリ/保存コストが増える |
| 今回の改訂検出 | 更新検出時は全失敗。短いDB読取で既存ページAPIを拡張できるが、頻繁な変更では再試行が増える。厳密な過去snapshotとは呼ばない |

COUNTだけが同じケースやID重複がないケースでも集合は入れ替わり得るため、改訂を省く代替にはならない。`PRAGMA data_version`は接続ごとの比較用であり、HTTPページ間の共有版に流用しない。[SQLite data_version](https://www.sqlite.org/pragma.html#pragma_data_version)を参照。

## 8. Platform測定計画と残る制約

p95≤20秒・同条件の逐次比50%以上短縮は暫定目標。未測定であり達成保証はしない。

1. 同一版・同一改訂方式・同一100件/ページ・同一ID/文字量の10,000件合成データで1/2/4を比較する。外部翻訳は呼ばない。
2. 空/部分/全件キャッシュ、DB/HTTPの冷/温、RTT/TLS/帯域/接続再利用を区別する。現在の無改訂の旧逐次版は診断参考として別記し、等価条件と混同しない。
3. 条件ごとにウォームアップ後最低30回、可能なら50回以上。p95は昇順のceil(0.95*n)番目とし、生データ・n・p50/p95/maxを記録する。
4. Select All開始→最終検証→選択件数反映までを測る。待機、TTFB/転送、JSON処理、検証、commitを分解し、要求数・cache hit・最大同時数も記録する。
5. 複数Glossary、1/2/4ユーザー相当、後半OFFSET、長文Term、10,000件importを別ケースにする。DatabaseSync処理自体が4倍並列になると見込まない。
6. 小型EC2相当の隔離環境でCPU/RSS、DB busy/lock、I/O、CPU credit、他の軽量APIのp95を記録する。負荷ガード案は他API p95が逐次基準2倍以内。長い読取のWAL/checkpointへの影響も確認する。journal modeは今回変更していない。[SQLite WAL](https://www.sqlite.org/wal.html)を参照。
7. 目標未達や負荷増加なら同時数2/1への変更を検討し、無制限並列化しない。別タブ/複数workerの総サーバー負荷制限が必要なら別設計へ戻す。

## 9. Platform向け手動E2E手順（網羅的検証は未完了）

Product Ownerによる本番新LP表示・10,000 Term選択成功の限定的確認は実施済み。以下の全ケースや正式性能比較の完了を意味しない。

- 専用の隔離SQLiteを新規作成し、次に合成データのv1 DBコピーもv2へ移行する。既存ユーザー/Glossary/Termとrevision backfillを確認する。本番DBを使用しない。
- APIの通常ページと最終確認の版・private/no-storeヘッダーを確認し、未ログイン時の拒否・別所有者の拒否を両モードで確認する。
- 承認済みの非本番認証環境でBYOを開く。未展開Termへの通信がなく、展開1ページ・ページ送り・選択保持が維持されることを確認する。
- 設定1/2/4を順に使い、10,000件の選択ID、同時数、複数Glossaryの共有4上限、10,001件拒否を確認する。
- DevToolsの遅延等を使い、別のローカル操作から対象Termを追加/更新/削除/importする。Select All全体が失敗し、既存の選択件数が不変で、再試行可能であることを確認する。
- 一部通信失敗、待機中/実行中の折りたたみ、画面離脱・再表示を試し、部分選択や古い応答の表示上書きがないことを確認する。
- 別Glossaryの成功操作と失敗操作を重ね、成功した選択を失わないことを確認する。用語なし翻訳の導線と、既存認証/所有権制御も別途確認する。
- 実サービス翻訳、Production Build、AWS操作、Production相当性能測定、DeployはPlatformの承認工程で実施する。今回の開発チームは実施しない。

## 10. 開発検証記録（9783463実装時点・今回再実行なし）

- `node --experimental-strip-types --test test/*.test.ts`：83件成功、失敗0件。新取得/選択/改訂関連34件（loader 12、bulk selection 6、revision integration 16）を含む。
- `npm run typecheck`：成功。`next typegen`と`tsc --noEmit`のみでProduction Buildは実行していない。
- `npm run lint`：エラー0件。未変更の`infra/terraform/v5/deepl-proxy-auth.js`に既存警告2件。
- データは合成データ・OS一時ディレクトリのSQLite・ローカルHTTPモックのみ。v1→v2移行の既存行保持と、2接続の制御した競合を検証した。既存ローカルDB/本番SQLiteへエージェントが移行を適用していない。
- CR-02ソース/CSS/LPテスト/デモ素材は`de30d5a`から差分なし。HTTPルート全体/Cognito往復を含むブラウザE2E、1/2/4の性能比較、本番目標達成、Build/Deployは未実施。
- 当時の開発完了時点ではCommit/PushせずPlatformへ引き渡した。その後の実装確定コミットは`9783463`。本番反映状況は12章、残る確認は9章・測定は8章を参照する。

## 11. 実装コミット9783463の変更ファイル一覧

| 区分 | ファイル |
|---|---|
| 設定例 | `.env.example` |
| BYO/API | `src/app/translation/page.tsx`、`src/app/api/glossaries/[glossaryId]/terms/route.ts` |
| 取得/選択 | `src/lib/translation/term-page-loader.ts`、`src/lib/translation/term-fetch-scheduler.ts`、`src/lib/translation/bulk-term-selection.ts` |
| DB/サービス/型 | `src/lib/db/migrations.ts`、`src/lib/db/term-pages.ts`、`src/lib/services/term-pages.ts`、`src/lib/domain/types.ts` |
| テスト | `test/term-page-loader.test.ts`、`test/term-revision.test.ts`、`test/bulk-term-selection.test.ts`、`test/db-migration.test.ts` |
| 文書 | `docs/requirements.md`、`docs/use-cases.md`、`docs/design/baseline-1.2-proposal.md`、`docs/design/cr-01-parallel-fetch-design.md` |

## 12. 本番反映・検証状態（2026年11月8日）

2026年11月8日にCR-01・CR-02を本番反映（release `9783463dcdd3f3b10f743ab3e1488978b842b4be`）。Product Ownerは新LP表示と10,000 Term選択成功を本番ブラウザで確認した。旧版約40秒→新版約27秒（参考短縮率約32.5%）は同条件の正式なp95比較ではない。FR-734のp95≤20秒・逐次比50%以上短縮は達成済みとせず、正式な性能受入は未完了。本番Migration適用結果・systemd最終状態の実行ログは未提示で要確認。

[Production As-Built](../infrastructure/production-as-built-2026-11-08.md)にActions Run `42481781676`、バックアップ・隔離DB移行・未確認事項を集約する。暫定デフォルト2、単一操作/同一タブ最大4の実装を維持する。隔離DBの件数とPASSを本番移行後の実測値として扱わない。過去の開発記録のBuild/Deploy未実施は当時の開発担当の範囲を指し、今回報告されたCI/Platformの本番リリースとは区別する。
