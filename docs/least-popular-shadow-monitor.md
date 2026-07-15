# 人気最下位艇2戦略・前向きシャドー監視

## 目的

ルールは2026年7月15日に固定し、7月16日以降の結果を見ていないデータだけで、次の2戦略を仮想購入します。過去成績は参考表示だけに使い、採用判定には混ぜません。

### C1：最下位艇1着固定2点

対象艇を `T` とします。

- BOATERS市場人気プロキシが6艇中単独最下位の5・6号艇
- AI予測率4%以上12%未満
- 展示＋1周タイム合計が6艇平均より0.10秒以上速い
- 展示または1周タイムが2位以内（上の条件と合わせてexact +14）
- 市場人気プロキシ1位艇が40%以下

各100円で `T-1-2` と `T-1-3` を仮想購入します。

### C4：最下位艇2着固定・他艇2艇以上弱化3点

C1と同じ `exact +14` の対象条件（ただし市場1位40%以下の条件は使わない）のうち、さらに次を満たします。

- TのAI予測率6%以上12%未満
- TのAI3連率45%未満
- T以外に複合弱化艇が2艇以上

複合弱化艇は、展示順位5・6位、1周順位5・6位、展示ST 0.20以上、一般3連率順位5・6位の4軸中2軸以上に該当する艇です。

他5艇を展示＋1周タイム合計の速い順にA〜Eとし、各100円で `A-T-B`、`A-T-C`、`A-T-D` を仮想購入します。

## 結果前固定

モニターは5分間隔で動き、締切22分前から5分前までの固定窓で6艇分の入力が最初に揃った時点を判定値にします。通信開始時刻ではなく2ページの取得完了時刻で5分前を判定します。候補になったレースは、入力値、買い目、取得完了時刻、締切までの残り時間、元ページ2件のSHA-256、ルール版、戦略別予測通番を保存し、結果判明後もcapture部分を更新しません。

朝の全場・全12RをBOATERSから独立に2回取得し、さらにBOATRACE公式日程の開催場集合と照合します。会場・レース・締切が一致した時だけ日次manifestとして固定します。締切前に判定できなかったレース、ジョブ停止日、部分取得、結果未確定は欠損として残し、完全率が99%未満なら採用ゲートを通しません。中止はvoid、返還は買い目単位で処理し、返還を含まない買い目の投資と結果は残します。

`protocol_lock.json` は開始日、取得窓、ルールに加え、監視本体・BOATERS取得処理・日程DB生成処理・workflowのbundle SHA-256を固定します。本番はPython 3.12.4と2つの所定日程DBパスも固定し、`--offline`、時刻上書き、対象レース限定、開始日・取得窓・bootstrap回数の変更を禁止します。ルールや依存コードを変える場合は同じ成績に継ぎ足さず、新しいprotocolとして再開します。

## 採用ステータス

- `WATCH`：最低サンプル未到達
- `PROMISING`：最初の固定チェックポイントを全項目通過
- `ADOPT_SMALL`：次の固定区間でも再現し、少額採用可
- `200_CONFIRMED`：開催日bootstrap片側95%下限も200%以上
- `HOLD`：採用ゲートまたはリスク停止基準を未通過

固定チェックポイントはC1が500レース、その後250レースごと。C4が120レース、その後60レースごとです。毎日の累積値が都合よく上がった瞬間には採用判定しません。

共通の主な採用ゲートは以下です。

- 累積ROI 200%以上
- 開催日単位bootstrap片側95%下限100%以上
- 最大払戻1本を除いてROI 150%以上
- 最大1本の払戻寄与40%以下、上位3本70%以下
- 時系列前半・後半ともROI 100%以上
- 的中が4暦四半期以上に分散
- 締切前監視・日次manifest・結果確定・スナップショット完全率99%以上
- 過去日のworkflow実行完全率99%以上（初回・最終時刻と最大15分間隔を監査）
- 予測通番が1から連続し、期限超過openが0件

C1は500レース・10的中以上、C4は120レース・12的中以上が最初の最低条件です。先頭から未確定の予測があれば、後発レースが確定していてもチェックポイントを進めません。当日分はworkflow完全性が確定する翌日まで採用判定に入れず、次の固定区間も通過した時だけ `ADOPT_SMALL` になります。

## 出力

- `data/output/least_popular_shadow/candidates_YYYYMMDD.json`：日別の結果前候補、判定監査、結果
- `data/output/least_popular_shadow/status.json`：累積成績、採用ゲート、次のチェックポイント
- `data/output/least_popular_shadow/notification_state.json`：採用可能通知の重複防止
- `data/output/least_popular_shadow/protocol_lock.json`：監視契約と実行コードの固定

GitHub Actionsの `least-popular-shadow-monitor.yml` が朝の早い締切にも間に合うようJST 07:02〜21:57に5分間隔で両方を監視し、過去日を含む変更済み台帳をすべて保存します。push失敗は成功扱いにせずジョブを失敗させます。スマホ通知は候補発生時ではなく、`ADOPT_SMALL` または `200_CONFIRMED` へ初めて到達した時だけです。採用通知は判定台帳のpush成功後に別段階で送り、未保存の標本を根拠に通知しません。

## 手動実行

本番は当日のスケジュールDBが作成された後に固定条件で実行します。

```bash
python3 scripts/monitor_least_popular_shadow.py \
  --date 2026-07-16 \
  --db /tmp/least-popular-schedule-primary.sqlite \
  --verification-db /tmp/least-popular-schedule-verification.sqlite \
  --official-index-html /tmp/least-popular-official/20260716/official_index.html \
  --workflow-run-id "$GITHUB_RUN_ID" \
  --workflow-run-attempt "$GITHUB_RUN_ATTEMPT"
```

保存済みDBでロジックだけを確認する場合は、本番台帳と混ざらない一時出力先を必ず指定します。

```bash
python3 scripts/monitor_least_popular_shadow.py \
  --date 2026-06-19 \
  --now 2026-06-19T17:40:00+09:00 \
  --start-date 2026-06-19 \
  --db ../price_action_analysis/outputs/boaters_all_races.sqlite \
  --output-dir /tmp/least-popular-shadow-test \
  --offline --no-result-fetch
```

「人気最下位」は公式最終オッズ順位ではなく、BOATERS `odds_prediction_pct` の単独最小です。
