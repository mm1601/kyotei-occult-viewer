# Manshu Role Analysis Plan

調査日: 2026-06-17 JST

目的: 既存の `manshu.html` と公開済みロジックを変更せず、万舟/中荒れ以上になりやすいレースをランキング化し、その中で6艇を `head` 2艇、`axis` 2艇、`toss` 1艇、`opponent` 1艇へ分類する検証基盤を作る。

## 現状

- 公開ページ `manshu.html` は静的生成物として扱う。
- 既存の万舟スコアは `manshu_days.html` の埋め込みデータから `existing_score` として分析CSVへ結合される。
- 分析母集団は `data/analysis/race_dataset.csv` の 2026-01-25 から 2026-06-16、20,947有効レース。
- 役割分類は新しい `role_*` 名前空間で作り、既存スコアや既存HTMLを上書きしない。

## モデル分離

### 朝版

出走表時点で分かる情報を使う。

- 級別
- 全国/当地勝率
- モーター/ボート2連率
- 枠番
- レース番号、場、グレード、日次
- 進入固定など事前条件

### 直前版

締切前に取得できる直前情報まで使う。

- 展示タイム/展示順位
- チルト
- 風速、波高、天候
- 直前オッズは取得できる場合のみ別特徴量として扱う

### 検証ラベル専用

以下は予測特徴量に入れない。

- 3連単結果
- 払戻金
- 人気
- 着順
- 決まり手
- 実際の進入/ST
- 返還、事故、F/L結果

## 作成済みファイル

- `scripts/build_boat_role_dataset.py`
- `scripts/analyze_boat_roles.py`
- `scripts/backtest_role_formations.py`
- `scripts/generate_manshu_role_ranking.py`
- `data/analysis/boat_role_dataset.csv`
- `data/analysis/boat_role_dataset.parquet`
- `data/analysis/boat_role_feature_dictionary.md`
- `data/output/manshu_role_ranking_20260616.json`
- `reports/boat_role_validation.md`
- `reports/formation_backtest.md`
- `reports/role_feature_importance.csv`
- `reports/final_recommendations.md`

## 役割定義

- `head`: 1着候補。単なる勝率上位ではなく、荒れレースで頭を作る可能性がある艇。
- `axis`: 3着以内候補。頭固定ではなく、2着/3着に残りやすい艇。
- `toss`: 消し候補。3着外想定の1艇。無理に買い候補へしないための見送り判断にも使う。
- `opponent`: 完全消しではない残り相手1艇。

各艇には `head_score`, `axis_score`, `toss_score` を残し、表示役割は重複なしにする。

## 固定フォーメーション

検証用であり、舟券購入を推奨するものではない。

- A: 1着=head2、2着=消し以外5艇、3着=消し以外5艇、重複除外、24点
- B: 1着=head2、2着=axis2+opponent、3着=消し以外5艇、18点
- C: 1着=head2、2着=axis2、3着=消し以外5艇、12点
- D: 1着=head1、2着=head2+axis2、3着=消し以外5艇、9点

点数や候補の定義を結果を見て後から変える場合は、別バージョンとして前向き検証をやり直す。

## CLI

艇別役割データセット作成:

```bash
python3 scripts/build_boat_role_dataset.py --race-dataset data/analysis/race_dataset.csv
```

艇別役割検証:

```bash
python3 scripts/analyze_boat_roles.py --dataset data/analysis/boat_role_dataset.csv
```

固定フォーメーション検証:

```bash
python3 scripts/backtest_role_formations.py --dataset data/analysis/boat_role_dataset.csv
```

当日表示用JSONプロトタイプ生成:

```bash
python3 scripts/generate_manshu_role_ranking.py --date 2026-06-16 --mode preview --output data/output/manshu_role_ranking_20260616.json
```

## JSON出力方針

`data/output/manshu_role_ranking_YYYYMMDD.json` は GitHub Pages 側へ接続する前のプロトタイプ。

- `version`
- `mode`
- `date`
- `generated_at`
- `source`
- `races[]`
  - `scores.manshu_score`
  - `scores.manshu_probability_proxy`
  - `scores.target_arare_probability_proxy`
  - `risk_flags`
  - `role_summary`
  - `boats[]`
  - `formations`
  - `skip_recommendation`
  - `notes`

確率proxyは校正済み確率ではなく、表示・並び替え用の暫定値。的中や利益は保証しない。

## 検証方針

- 母集団全体、Top3/day、Top5/day、Top10/dayを分ける。
- 万舟だけでなく中荒れ以上も見る。
- 役割評価は `head2_win`, `axis_any_top3`, `toss_out_top3`, `role_core_success` に分解する。
- フォーメーションは hit rate、万舟hit rate、中荒れhit rate、万舟捕捉率、平均/最大払戻を出す。
- 日付順70/30で時系列検証し、同一日を学習/検証に混ぜない。
- 最終判断は結果前にJSONを保存し、結果後に照合する前向きOOSで行う。

## 制約

- 現CSVでは平均STや3連率系に欠損が残るため、ST安定などの説明は過信しない。
- 直前版は展示・気象を使うため、朝版ランキングとは別物として扱う。
- 既存スコアの方が一部範囲で強いが欠損が多い。新スコアは置換ではなく補助として扱う。
- ROI風の数字は固定点数・100円均等仮定の参考値であり、結論に使わない。
