# Codex本命絞り 安定性診断

- 元台帳: `reports/postdata_manshu_backtest/core_focus_all_head2_no1_outer56_20250101_20260629_ledger.csv`
- 判定: `FORWARD_TEST_WITH_CAUTION`

## 全体

```text
segment  buy_races  total_points  stake_yen  payback_yen  profit_yen  roi_pct  hit_rate_pct  manshu_hit_rate_pct  avg_points  max_losing_streak                  decision
    ALL         89           285      28500       115980       87480   406.95          6.74                 4.49         3.2                 33 FORWARD_TEST_WITH_CAUTION
```

## 大当たり依存

```text
         scenario  removed_hits  payback_yen  roi_pct  removed_payback_yen
         all_hits             0       115980   406.95                  NaN
without_top_1_hit             1        64200   225.26              51780.0
without_top_2_hit             2        42900   150.53              73080.0
without_top_3_hit             3        23790    83.47              92190.0
without_top_4_hit             4        10450    36.67             105530.0
without_top_5_hit             5         1000     3.51             114980.0
```

## 的中一覧

```text
      date place_name  round  payout_yen  trifecta         focused_tickets
2025-07-14         戸田      1       51780       425                 425 426
2026-04-08        平和島      3       21300       325         325 345 352 354
2025-07-02        多摩川      2       19110       562         542 543 546 562
2025-02-10         鳴門      2       13340       534 532 534 536 542 543 546
2025-10-18        浜名湖      1        9450       453         435 453 456 465
2026-05-02         戸田      3        1000       452         425 435 452 453
```

## 注意

- 回収率は強いが、的中率は低く最大連敗が長い。
- 本命絞りは少点数の高配当狙い。連敗前提の前向き検証ルールとして扱う。
- 大当たり上位2本を抜いても100%を超えるかを継続監視する。