# research_v2 Final Report

## 結論

- 採用判定: `HOLD_FOR_FORWARD_TEST`
- 既存本番ツールに変更: なし
- `manshu.html` 変更: なし（baseline hash test OK）
- 既存ランキングJSON変更: なし
- 回収率100%以上: 独立検証では未達
- 本番採用: まだしない。前向き検証継続。

## 現行ランキングと候補ランキング

| 指標 | 現行 | 候補 | 差 |
| --- | ---: | ---: | ---: |
| TOP10万舟率 | 28.16% | 28.16% | 0.0pt |
| TOP3万舟率 | 30.5% | 32.62% | 2.12pt |

候補はTOP3/TOP5では小幅改善しましたが、TOP10は同率、TOP1は悪化しているため採用しません。

## 頭候補2艇

| 指標 | 既存role直前 | 候補logit直前 | 差 |
| --- | ---: | ---: | ---: |
| strict TOP10 head2捕捉率 | 36.67% | 55.33% | 18.66pt |
| Brier | 0.900805 | 0.78014 | -0.120665 |
| log loss | 2.077944 | 1.634063 | -0.443881 |

頭候補2艇は候補モデルで改善。ただし本番採用には前向き検証が必要です。

## 朝版と直前版

| 指標 | 朝版 | 直前版 | 差 |
| --- | ---: | ---: | ---: |
| 候補logit strict TOP10 head2捕捉率 | 54.67% | 55.33% | 0.66pt |
| Brier | 0.781078 | 0.78014 | -0.000938 |

直前版は頭候補捕捉率で小幅改善、Brierも小幅改善。ただしROIでは直前版Bが朝版Bより悪化しました。

## 18点フォーメーションB ROI

| モード | 購入R | 購入額 | 払戻 | 回収率 | 最高配当除外 | 最大連敗 | 最大DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| morning | 451 | 811800 | 766960 | 94.48% | 87.03% | 19 | 94450 |
| preview | 451 | 811800 | 625470 | 77.05% | 69.6% | 20 | 201780 |

回収率100%以上は独立検証で未達。高配当1件を除くとさらに下がるため、無理に100%超えとは判定しません。

## データとクラスタ

- 取得率: 展示タイム97.82%、展示ST97.82%、風速/波高98.83%、1周/まわり足/直線/潮/時刻別オッズは未収集。
- 最高リフトクラスタ: C0 / 混戦型 / 万舟率 18.35% / lift 1.1136
- クラスタはKMeans k=3を採用候補にしたが、ランキング改善確認までは本番利用しません。

## データリーク確認

- 頭モデル特徴量から結果列・払戻・人気・決まり手を除外。
- クラスタ特徴量から結果列・払戻・万舟フラグを除外。
- 保存済みランキングJSONを予測ログとして使用し、過去結果を見て現行ランキングを再実行していません。

## 実行コマンド

```bash
python3 scripts/research_v2/phase0_audit_baseline.py
python3 scripts/research_v2/backtest_saved_rankings.py
python3 scripts/research_v2/head_exhibition_validation.py
python3 scripts/research_v2/data_coverage_cluster.py
python3 scripts/research_v2/candidate_ranking_lab.py
python3 -m unittest discover -s tests -v
```

## 成果物

- `docs/research_v2/current_system_audit.md`
- `reports/research_v2/baseline_manifest.json`
- `reports/research_v2/backtest_roi.md`
- `reports/research_v2/head_model_validation.md`
- `reports/research_v2/exhibition_incremental_validation.md`
- `reports/research_v2/data_coverage_report.md`
- `reports/research_v2/manshu_cluster_validation.md`
- `reports/research_v2/candidate_ranking_comparison.md`
- `manshu_accuracy_lab.html`

## 本番非変更確認

- 本番ファイル差分: 0件
- 対象外差分があれば以下に表示:

```text
なし
```
