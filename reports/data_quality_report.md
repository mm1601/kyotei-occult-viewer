# Manshu Data Quality Report

このレポートは分析用データセット生成時点の品質確認であり、舟券購入を推奨するものではありません。

- 取得日数: 143 / 143
- レース数: 21576
- 分析対象レース数: 20947
- 6艇情報あり: 21568
- 万舟数: 3452
- 全体万舟率: 16.48%
- 既存スコア結合数: 6476
- Parquet出力: wrote data/analysis/race_dataset.parquet

## 欠損数

- `lane1_class`: 21 (0.1%)
- `lane1_national_win_rate`: 21 (0.1%)
- `lane1_local_win_rate`: 21 (0.1%)
- `lane1_motor_quinella_rate`: 21 (0.1%)
- `lane1_exhibition_time`: 283 (1.3%)
- `wind_speed_m`: 252 (1.2%)
- `wave_cm`: 252 (1.2%)
- `grade`: 0 (0.0%)
- `existing_score`: 15100 (70.0%)

## 注意

- 公式DLのBファイルには3連率、F/L数、平均STが含まれない場合がある。
- グレード、開催タイトル、日次はOpenAPI v3 programsがある場合のみ非公式補助で補完される。
- 展示タイム・展示STは直前版特徴量として扱う。
- `popularity`, `decision`, `result_trifecta`, `payout_yen` はラベル・検証専用。
- `existing_score` は `manshu_days.html` と期間が重なる場合のみ結合される。
