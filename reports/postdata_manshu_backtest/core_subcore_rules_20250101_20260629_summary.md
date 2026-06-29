# Codex本命/準本命 3連単バックテスト

- 期間: 2025-01-01〜2026-06-29
- 朝監視: TOP10
- 買い方: 3連単のみ。2連単は除外。
- 本命: 展示後40%以上 + 12点生成
- 準本命: 展示後38〜39.9% + 1号艇危険 + 外頭2艇(5/6含む) + 内軸残り + 12点生成

```text
         segment  watch_races  buy_races  total_points  avg_points  stake_yen  payback_yen  profit_yen  roi_pct  hit_rate_pct  manshu_hit_rate_pct  over5000_hit_rate_pct  max_losing_streak  max_drawdown_yen
          本命+準本命         5450        146          1752        12.0     175200       177350        2150   101.23         17.12                 2.74                   6.85               17.0           43580.0
        本命 40%以上          103        103          1236        12.0     123600       146470       22870   118.50         14.56                 3.88                   7.77               18.0           38800.0
準本命 38〜39.9%条件成立           43         43           516        12.0      51600        30880      -20720    59.84         23.26                 0.00                   4.65                6.0           20720.0
             見送り         5304          0             0         NaN          0            0           0      NaN           NaN                  NaN                    NaN                NaN               NaN
```