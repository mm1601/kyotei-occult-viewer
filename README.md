# 競艇オカルトサイン発掘ツール — 閲覧ビューア

公営競技（競艇）の**公開レース結果**から「データでは説明できない選手固有の傾向（サイン）」を統計検証した結果を閲覧する単一HTMLビューア集です。

## ページ

- **🔮 本日のサイン点灯チェッカー（`sign_today.html`）** ← NEW
  全量 **177,564 レース**（2023-01〜2026-06・全24場）を採掘して確定/暫定した**選手固有サイン18件**を、その日の出走表に自動照合します。「この選手がこの号艇に入ると荒れる／このペアが揃って来る／沈む」が**今日どのレースで点灯しているか**を表示。点灯が無い日も、末尾に **登録サイン図鑑（全18件）** を常設しています。
- **サイン検証図鑑（`index.html`・部分データ版スナップショット）**
  検証した全サイン候補（採用/暫定/データ待ち/棄却）の一覧・絞り込み。

**公開URL（GitHub Pages）**: https://boat10000.github.io/kyotei-occult-viewer/sign_today.html

## 検証の規律（なぜ信用できるか）

素朴な採掘は「小標本 × 多重比較」で偽の100%サインを量産します。これを避けるため、全候補に **4関門 + family-wise（多重比較）補正** を課しています:

1. **前半→後半の場外検証(OOS)** — 時系列の前半（2023-24）で選抜し、未知の後半（2025-26）で再現するか
2. **プラセボ** — トリガーを無情報化した帰無分布と比較（偽サインの量産を遮断）
3. **構造補正＆説明可能性** — 場の癖・女子戦混在・選手の実力・番組編成で説明できる分を差し引き、それでも残る「説明不能」だけを採る
4. **敵対的監査** — 別働の監査官が全生存サインをSQLで独立再計算し、潰しにかかる

## 重要（誠実性）

- これは**予想ツールではありません**。利益は一切保証しません。
- 全量ハントで「実力・場・番組で説明し尽くせない＝真のオカルト」として残った**確定サインは4件・実質3選手**（前づけ／チルト全速という"スタイル"がレースを荒らす荒れ予兆）。
- ただし**3連単120点全買いでも収支プラスになるのは西島義則の6号艇（ROI 1.05）の1件のみ**。他は現象は本物でもオッズにほぼ織り込まれ、その買い方では収支マイナスです（各カードに明記）。
- **「必ず3着以内」型のサインは全17.7万レースを走査して 0 件**。「必ず」を名乗る情報は、嘘か数走の偶然のどちらかです。
- 全サインは「**過去（2023-01〜2026-06）にそうだった**」という記述で、将来は保証しません。暫定14件は生き残りの上振れ前提で扱ってください。

閲覧は依存ゼロの自己完結HTML（外部通信なし）。出走表データの取得元は [BoatraceOpenAPI](https://boatraceopenapi.github.io/)（公開情報）です。

## データ取得・正規化基盤

予想ロジックやUIを変更する前段階として、公式サイトの画面系データを低頻度・キャッシュ前提で取得し、正規化JSONへ変換するスクリプトを追加しています。

調査メモ:

- `docs/data-source-investigation.md`
- `docs/manshu-analysis-plan.md`

### 実行例

1日分の開催一覧だけ取得:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16
```

1場だけ、取得予定URLを確認:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16 --jcd 01 --rno 2 --details --results --dry-run
```

1場1Rだけ詳細込みで取得:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16 --jcd 01 --rno 2 --details --odds --preview --results
```

取得済みキャッシュから正規化:

```bash
python3 scripts/normalize_boatrace_data.py --date 2026-06-16
```

サンプル検証:

```bash
python3 scripts/verify_boatrace_data.py --date 2026-06-16 --expect-race-data --expect-result
```

GitHub Pages用データへ変換する場合は、まず `data/normalized/YYYYMMDD.json` を生成し、既存の静的HTML生成フロー側で必要な項目だけを読み込む方針です。今回のスクリプトは既存の `manshu.html` や日別HTMLを直接変更しません。

取得時の安全方針:

- デフォルトは開催一覧1ページのみ。
- レース詳細、オッズ、直前情報、結果は明示オプション時のみ。
- 同一URLは `data/raw/YYYYMMDD/` のキャッシュを再利用。
- `--force` 指定時だけ再取得。
- 並列取得なし、1リクエストごとにデフォルト1秒待機。
- User-Agentを明示。
- 取得範囲は指定日・指定場・指定Rに限定可能。

## 万舟共通条件分析

既存の `manshu.html` や公開HTMLは変更せず、公式ダウンロード系B/Kファイルを主入力として、1レース1行の分析データセットとレポートを作成できます。

公式B/Kダウンロードを1日分取得:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16 --source official --cache
```

公式B/Kダウンロードを期間指定で取得:

```bash
python3 scripts/fetch_boatrace_data.py --start-date 2026-01-01 --end-date 2026-01-31 --source official --cache
```

取得予定URLだけ確認:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16 --source official --dry-run
```

非公式OpenAPI v3を補助データとして取得する場合:

```bash
python3 scripts/fetch_boatrace_data.py --date 2026-06-16 --source openapi --details --results
```

OpenAPIは公式ではなく、正確性・完全性・リアルタイム性は保証されません。分析の比較・補助用途として扱います。

正規化:

```bash
python3 scripts/normalize_boatrace_data.py --start-date 2026-01-01 --end-date 2026-01-31
```

分析データセット作成:

```bash
python3 scripts/build_manshu_dataset.py --start-date 2026-01-01 --end-date 2026-01-31
```

万舟条件分析:

```bash
python3 scripts/analyze_manshu_patterns.py --dataset data/analysis/race_dataset.csv
```

モデル検証:

```bash
python3 scripts/validate_manshu_model.py --dataset data/analysis/race_dataset.csv --time-split
```

主な出力:

- `data/analysis/race_dataset.csv`
- `data/analysis/race_dataset.parquet`（環境により `.unavailable.txt`）
- `data/analysis/feature_dictionary.md`
- `reports/manshu_common_patterns.md`
- `reports/manshu_common_patterns.csv`
- `reports/feature_lift_table.csv`
- `reports/model_validation.md`
- `reports/data_quality_report.md`

分析上の注意:

- `manshu_flag` は3連単払戻金10,000円以上。
- `big_manshu_flag` は3連単払戻金50,000円以上。
- 中止、不成立、返還あり、払戻欠損のレースは分析対象から除外。
- 朝版モデルには出走表時点で分かる情報だけを使う。
- 直前版モデルは展示・気象・オッズなど締切前情報まで使える。
- 結果、払戻金、人気、着順、決まり手は予測特徴量に入れない。
- レポートは娯楽・研究・検証用であり、舟券購入を推奨しません。

## 万舟ロール分析（レース荒れ度 + 頭/軸/消し）

既存の `manshu.html` や公開HTMLは変更せず、20,947レースの分析データから、荒れやすいレースのランキングと6艇の役割分類を検証できます。

設計メモ:

- `docs/manshu-role-analysis-plan.md`
- `reports/boat_role_validation.md`
- `reports/formation_backtest.md`
- `reports/final_recommendations.md`

艇別役割データセット作成:

```bash
python3 scripts/build_boat_role_dataset.py --race-dataset data/analysis/race_dataset.csv
```

艇別役割検証:

```bash
python3 scripts/analyze_boat_roles.py --dataset data/analysis/boat_role_dataset.csv
```

固定フォーメーションA-Dの参考バックテスト:

```bash
python3 scripts/backtest_role_formations.py --dataset data/analysis/boat_role_dataset.csv
```

GitHub Pages連携前のJSONプロトタイプ生成:

```bash
python3 scripts/generate_manshu_role_ranking.py --date 2026-06-16 --mode preview --output data/output/manshu_role_ranking_20260616.json
```

主な出力:

- `data/analysis/boat_role_dataset.csv`
- `data/analysis/boat_role_dataset.parquet`
- `data/analysis/boat_role_feature_dictionary.md`
- `data/output/manshu_role_ranking_20260616.json`
- `reports/boat_role_validation_summary.csv`
- `reports/boat_role_validation_segments.csv`
- `reports/formation_backtest_summary.csv`
- `reports/formation_backtest_time_split.csv`
- `reports/role_feature_importance.csv`

ロール分析上の注意:

- 朝版と直前版を分ける。
- 結果、着順、払戻、人気は候補生成に使わず、検証ラベルとしてのみ扱う。
- A-Dフォーメーションは検証用であり、購入推奨ではありません。
- ROI風の数字は固定点数・100円均等仮定の参考値であり、結論には使いません。
- 最終判断は、結果前にJSONを保存して結果後に照合する前向きOOSで行います。
