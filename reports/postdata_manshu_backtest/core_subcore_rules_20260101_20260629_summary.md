# Codex本命/準本命 3連単バックテスト

- 期間: 2026-01-01〜2026-06-29
- 朝監視: TOP10
- 買い方: 3連単のみ。2連単は除外。
- 本命: 展示後40%以上 + 12点生成
- 準本命: 展示後38〜39.9% + 1号艇危険 + 外頭2艇(5/6含む) + 内軸残り + 12点生成

```text
         segment  watch_races  buy_races  total_points  avg_points  stake_yen  payback_yen  profit_yen  roi_pct  hit_rate_pct  manshu_hit_rate_pct  over5000_hit_rate_pct  max_losing_streak  max_drawdown_yen
          本命+準本命         1800         56           672        12.0      67200        42990      -24210    63.97         12.50                 1.79                   5.36               13.0           24210.0
        本命 40%以上           42         42           504        12.0      50400        31430      -18970    62.36          9.52                 2.38                   4.76               18.0           21600.0
準本命 38〜39.9%条件成立           14         14           168        12.0      16800        11560       -5240    68.81         21.43                 0.00                   7.14                6.0            7200.0
             見送り         1744          0             0         NaN          0            0           0      NaN           NaN                  NaN                    NaN                NaN               NaN
```