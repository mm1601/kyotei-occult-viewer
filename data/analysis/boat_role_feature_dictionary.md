# Boat Role Dataset Dictionary

このデータセットは艇別ロール候補の検証用です。舟券購入を推奨するものではありません。

- `head_score_morning/preview`: 1着候補スコア。結果列は使わない。
- `axis_score_morning/preview`: 3着以内候補スコア。結果列は使わない。
- `toss_score_morning/preview`: 3着外候補スコア。結果列は使わない。
- `role_morning/preview`: 各レース内で重複なしに割り当てた `head`, `axis`, `toss`, `opponent`。
- `actual_win`, `actual_top3`, `actual_out_top3`: 検証ラベル。
- `mid_arare_flag`: 払戻5,000円以上10,000円未満。
- `target_arare_flag`: 払戻5,000円以上。
- `chaos_score`: レース荒れ判定用の説明可能なルールベーススコア。
- `skip_morning/preview`: 見送り候補。頭候補が割れ気味、消し候補不明瞭、欠損多めなど。

データリーク防止: `payout_yen`, `result_trifecta`, `actual_*` はラベル・検証専用。
