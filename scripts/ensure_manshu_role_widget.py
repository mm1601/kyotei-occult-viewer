#!/usr/bin/env python3
"""Ensure generated manshu HTML pages include the role-ranking widget.

The daily manshu generator rewrites manshu.html and date archive pages. This
script is intentionally idempotent so it can run after generated commits and
restore the role widget without changing prediction logic.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROLE_CSS = """.card.role{border-left-color:#0d9488} .card.role h2{color:#0f766e}
.rolelist{display:grid;gap:10px;margin-top:8px}
.roleitem{border:1px solid #dbeafe;background:#f8fbff;border-radius:9px;padding:10px}
.roletop{display:flex;gap:8px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;font-size:13px;color:#33405a}
.rolemetric{font-size:12px;font-weight:800;color:#0f766e;background:#ccfbf1;border:1px solid #99f6e4;border-radius:999px;padding:2px 8px;white-space:nowrap}
.rolegrid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px}
.rolebox{background:#fff;border:1px solid #eef1f5;border-radius:8px;padding:6px 8px;font-size:12.5px;line-height:1.45}
.rolebox b{color:#33405a}.rolebox .tag{display:block;color:#0f766e;font-weight:800;font-size:11.5px;margin-bottom:2px}
.rolebox.toss .tag{color:#b45309}.rolebox.toss{background:#fff7ed;border-color:#fed7aa}
.rolebox.head .tag{color:#b91c1c}.rolebox.head{background:#fff7ed;border-color:#fecaca}
.rolebox.axis .tag{color:#1d4ed8}.rolebox.axis{background:#eff6ff;border-color:#bfdbfe}
.rolefoot{font-size:11.5px;color:#64748b;margin-top:7px}
.role-result{font-weight:800;color:#33405a}
.role-strategy{margin-top:7px;border:1px solid #e5e7eb;background:#fafafa;border-radius:8px;padding:7px 8px;font-size:12px;line-height:1.5;color:#374151}
.role-strategy b{color:#111827}.role-strategy .good{color:#047857;font-weight:900}.role-strategy .hold{color:#b45309;font-weight:900}.role-strategy .avoid{color:#b91c1c;font-weight:900}.role-strategy .outside_rule{color:#64748b;font-weight:900}
.rate-scroll{overflow-x:auto;margin-top:8px}
.rate-table{min-width:980px}
.rate-table th,.rate-table td{vertical-align:top}
.rate-table .race{font-weight:800;color:#33405a;white-space:nowrap}
.rate-table .rate{font-weight:900;color:#7c3aed;white-space:nowrap}
.rate-table .result{white-space:nowrap}
.rate-table .roi{font-weight:900;color:#047857;white-space:nowrap}
.rate-table .miss{color:#64748b;font-weight:800}
.rate-roles{font-size:11.8px;line-height:1.55;color:#475569;min-width:220px;max-width:320px}
.rate-roles b{color:#33405a}
.rate-summary{font-size:12.5px;color:#33405a;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px 10px;margin:8px 0 0}
.post-card textarea{min-height:150px}"""


RATE_CARD = """<div class="card rank" id="rate-card">
  <h2>📊 万舟率トップ10（結果・的中・回収率つき）</h2>
  <p class="lead" id="rate-status">本日の万舟率トップ10を読み込み中…</p>
  <div class="bar"><button id="rbtn" onclick="loadResults()">🔄 結果を更新</button><span id="rstat" class="rstat"></span></div>
  <div class="rate-scroll">
    <table class="rate-table">
      <thead><tr><th>順位</th><th>場R</th><th>万舟率</th><th>結果</th><th>買い方1</th><th>頭・軸・消し</th><th>単発ROI</th></tr></thead>
      <tbody id="rate-list"></tbody>
    </table>
  </div>
  <p class="rate-summary" id="rate-summary">集計待ち</p>
  <p class="muted">的中・回収率は「買い方1（検証用9点）」を100円ずつ買った場合の単発換算です。予想・購入推奨・利益保証ではありません。</p>
</div>
<div class="card xpost post-card" id="top5-x-card">
  <h2>✍️ X投稿用｜万舟率トップ5</h2>
  <p class="lead">トップ5の観察メモを短文で投稿できます。買い目や回収率は載せません。</p>
  <textarea class="xta" id="top5-x-post" rows="8" readonly>読み込み中…</textarea>
  <button class="xcopy" onclick="copyX('top5-x-post',this)">📋 X文をコピー</button><span id="top5-x-post-len" class="xlen"></span>
</div>
<div class="card notepost post-card" id="top10-note-card">
  <h2>📝 note投稿用｜万舟率トップ10</h2>
  <p class="lead">トップ10の観察メモと答え合わせをnoteへ貼れる形式です。買い目や回収率は載せません。</p>
  <textarea class="xta" id="top10-note-post" rows="18" readonly>読み込み中…</textarea>
  <button class="xcopy" onclick="copyX('top10-note-post',this)">📋 note文をコピー</button>
</div>"""


ROLE_CARD = """<div class="card role" id="role-card">
  <h2>🧪 ロール分析（試験表示）</h2>
  <p class="lead" id="role-status">頭2艇・軸2艇・消し候補を読み込み中…</p>
  <div class="rolelist" id="role-list"></div>
  <p class="muted">過去20,947Rで検証した補助表示です。買い目確定や利益保証ではありません。直前版は展示・気象込みのため、朝の判断とは分けて見てください。</p>
</div>"""


ROLE_JS = """var ROLE_RANKING_DATA=null;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function roleDataUrl(){
  var prefix=location.pathname.indexOf('/manshu/')>=0?'../':'';
  return prefix+'data/output/manshu_role_ranking_'+RDATE.replace(/-/g,'')+'.json';
}
function boatLine(r,lane,role){
  var b=(r.boats||[]).find(function(x){return Number(x.lane)===Number(lane);})||{};
  var reason=b.role_reason?'<br><span class="muted">'+esc(b.role_reason).replace(/\\|/g,' / ')+'</span>':'';
  return '<b>'+lane+'号艇</b>'+(b.name?' '+esc(b.name):'')+(b.class?' <span class="muted">'+esc(b.class)+'</span>':'')+reason;
}
function roleBox(r,title,role,lanes){
  var body=(lanes||[]).map(function(lane){return boatLine(r,lane,role);}).join('<br>');
  if(!body) body='<span class="muted">未判定</span>';
  return '<div class="rolebox '+role+'"><span class="tag">'+title+'</span>'+body+'</div>';
}
function roleResultKey(r){return parseInt(r.jcd,10)+'-'+parseInt(r.race_no,10);}
function roleResultHtml(r){
  var live=ROLE_RESULT_MAP[roleResultKey(r)],res=r.result||{},combo=null,payout=null;
  if(live){combo=live.combination; payout=live.payout;}
  else {combo=res.trifecta; payout=res.payout_yen;}
  if(!combo) return '結果: <span class="muted">未確定</span>';
  if(payout==null) return '結果: 3連単 <b>'+esc(combo)+'</b> <span class="muted">配当待ち</span>';
  var man=payout>=MAN;
  return '結果: 3連単 <b>'+esc(combo)+'</b> '+Number(payout).toLocaleString()+'円 '+(man?'<span class="man">万舟決着</span>':'<span class="muted">1万円未満</span>');
}
function pctText(v){return v==null?'—':(Number(v)*100).toFixed(1)+'%';}
function pctPoint(v){return v==null?'—':(Number(v)*100).toFixed(1)+'%';}
function yenText(v){return v==null?'—':Number(v).toLocaleString()+'円';}
function rateResult(r){
  var live=ROLE_RESULT_MAP[roleResultKey(r)],res=r.result||{},combo=null,payout=null,status=null;
  if(live){combo=live.combination; payout=live.payout; status='live';}
  else {combo=res.trifecta; payout=res.payout_yen; status=res.status||null;}
  return {combination:combo,payout:payout,manshu:payout!=null&&payout>=MAN,status:status};
}
function strategyEval(r){
  var s=r.strategy&&r.strategy.buy_style_1||{},form=s.formation||{},tickets=form.tickets||[];
  var points=Number(form.points||tickets.length||9),cost=points*100,result=rateResult(r),check=s.result_check||{};
  var hit=check.hit,ret=check.return_yen_per_100;
  if(hit==null&&result.combination&&result.payout!=null&&tickets.length){
    hit=tickets.indexOf(result.combination)>=0;
    ret=hit?result.payout:0;
  }
  if(result.combination&&!result.payout&&hit==null) return {points:points,cost:cost,done:false,hit:null,returnYen:null,roi:null,label:'配当待ち',short:'待'};
  if(!result.combination&&hit==null) return {points:points,cost:cost,done:false,hit:null,returnYen:null,roi:null,label:'結果待ち',short:'待'};
  if(ret==null) ret=hit?result.payout:0;
  var roi=cost?ret/cost*100:null;
  return {points:points,cost:cost,done:true,hit:!!hit,returnYen:ret,roi:roi,label:hit?'的中':'不的中',short:hit?'◎':'×'};
}
function roleLaneName(r,lane){
  var b=(r.boats||[]).find(function(x){return Number(x.lane)===Number(lane);})||{};
  return lane+'号艇'+(b.name?' '+esc(b.name):'');
}
function roleLanesText(r,lanes){
  if(!lanes||!lanes.length) return '未判定';
  return lanes.map(function(lane){return roleLaneName(r,lane);}).join(' / ');
}
function rateRolesHtml(r){
  var roles=r.role_summary||{};
  return '<div><b>頭</b>: '+esc(roleLanesText(r,roles.head))+'</div>'+
    '<div><b>軸</b>: '+esc(roleLanesText(r,roles.axis))+'</div>'+
    '<div><b>消し</b>: '+esc(roleLanesText(r,roles.toss))+'</div>';
}
function topRateRaces(data){
  return (data.races||[]).slice().sort(function(a,b){
    return (b.scores&&b.scores.manshu_probability_proxy||0)-(a.scores&&a.scores.manshu_probability_proxy||0);
  }).slice(0,10);
}
function summarizeTop(top){
  var done=0,manshu=0,hit=0,cost=0,ret=0,pending=0;
  top.forEach(function(r){
    var res=rateResult(r),ev=strategyEval(r);
    if(res.combination&&res.payout!=null){
      done++;
      if(res.manshu) manshu++;
    } else pending++;
    if(ev.done){
      cost+=ev.cost;
      ret+=ev.returnYen||0;
      if(ev.hit) hit++;
    }
  });
  return {done:done,pending:pending,manshu:manshu,hit:hit,cost:cost,ret:ret,roi:cost?ret/cost*100:null};
}
function resultCellText(r){
  var res=rateResult(r);
  if(!res.combination) return '結果待ち';
  if(res.payout==null) return res.combination+' 配当待ち';
  return res.combination+' '+yenText(res.payout)+' '+(res.manshu?'万舟決着':'1万円未満');
}
function compactRaceLabel(r){return esc(r.venue_name||'')+esc(r.race_no)+'R';}
function updateRateResults(){
  if(!ROLE_RANKING_DATA) return;
  renderRateTop10(ROLE_RANKING_DATA);
}
function renderRateTop10(data){
  ROLE_RANKING_DATA=data;
  var body=document.getElementById('rate-list'),status=document.getElementById('rate-status'),summaryEl=document.getElementById('rate-summary');
  if(!body||!status) return;
  var top=topRateRaces(data),summary=summarizeTop(top);
  if(!top.length){status.textContent='万舟率トップ10データが空です。'; return;}
  status.textContent='万舟率が高い順にトップ10を表示中。結果・的中・回収率は確定分から順次反映します。';
  body.innerHTML=top.map(function(r,i){
    var res=rateResult(r),ev=strategyEval(r),rate=pctPoint(r.scores&&r.scores.manshu_probability_proxy);
    var resultHtml=!res.combination?'<span class="muted">結果待ち</span>':
      esc(res.combination)+'<br><span class="'+(res.manshu?'man':'muted')+'">'+(res.payout==null?'配当待ち':yenText(res.payout)+(res.manshu?' 万舟決着':' 1万円未満'))+'</span>';
    var hitHtml=ev.hit==null?'<span class="muted">'+esc(ev.label)+'</span>':
      '<span class="'+(ev.hit?'man':'miss')+'">'+esc(ev.label)+'</span><br><span class="muted">'+ev.points+'点</span>';
    var roiHtml=ev.roi==null?'<span class="muted">—</span>':'<span class="roi">'+Math.round(ev.roi)+'%</span><br><span class="muted">返 '+yenText(ev.returnYen)+'</span>';
    return '<tr data-rate-row="'+esc(roleResultKey(r))+'">'+
      '<td class="rkn">'+(i+1)+'</td>'+
      '<td><span class="race">'+compactRaceLabel(r)+'</span><br><span class="muted">'+esc(r.deadline||'')+'</span></td>'+
      '<td class="rate">'+rate+'</td>'+
      '<td class="result">'+resultHtml+'</td>'+
      '<td>'+hitHtml+'</td>'+
      '<td class="rate-roles">'+rateRolesHtml(r)+'</td>'+
      '<td>'+roiHtml+'</td>'+
    '</tr>';
  }).join('');
  if(summaryEl){
    summaryEl.innerHTML='トップ10確定 '+summary.done+'/'+top.length+'R / 万舟決着 '+summary.manshu+'件 / 検証用9点 '+summary.hit+'件 / 単発回収率 <b>'+(summary.roi==null?'—':Math.round(summary.roi)+'%')+'</b>（返戻 '+yenText(summary.ret)+' / 想定購入 '+yenText(summary.cost)+'）';
  }
  buildTopPosts(top,summary);
}
function buildTopPosts(top,summary){
  var x=document.getElementById('top5-x-post'),note=document.getElementById('top10-note-post');
  if(x){
    var xlines=[(RDATE.slice(5).replace('-','/')+' 万舟率TOP5 観察メモ')];
    top.slice(0,5).forEach(function(r,i){
      var res=rateResult(r),rate=Math.round((r.scores&&r.scores.manshu_probability_proxy||0)*100);
      var result=res.payout==null?(res.combination?'結果 '+res.combination+' 配当待ち':'結果待ち'):('結果 '+res.combination+' '+Number(res.payout).toLocaleString()+'円'+(res.manshu?' 万舟':''));
      xlines.push((i+1)+'. '+compactRaceLabel(r).replace(/<[^>]*>/g,'')+' 万舟率'+rate+'% / '+result);
    });
    xlines.push('荒れやすさの研究用メモ。買い目・購入推奨・利益保証なし。');
    x.value=xlines.join('\\\\n');
    showLen('top5-x-post');
  }
  if(note){
    var L=['# '+RDATE.slice(5).replace('-','/')+' 万舟率TOP10 観察メモ・答え合わせ','','過去データから作った「荒れやすさ」のランキングです。買い目、購入推奨、利益保証ではありません。','','## 集計','- 確定: '+summary.done+'/'+top.length+'R','- 万舟決着: '+summary.manshu+'件','- 対象: 万舟率TOP10の答え合わせ','','## TOP10'];
    top.forEach(function(r,i){
      var res=rateResult(r),rate=pctPoint(r.scores&&r.scores.manshu_probability_proxy);
      L.push('');
      L.push('### '+(i+1)+'位 '+(r.venue_name||'')+r.race_no+'R（'+(r.deadline||'締切未取得')+'）');
      L.push('- 万舟率: '+rate);
      L.push('- 結果: '+resultCellText(r));
    });
    L.push('');
    L.push('## 注意');
    L.push('- 万舟率は荒れやすさの目安で、的中や利益を示す数字ではありません。');
    L.push('- 結果・払戻は答え合わせ用で、購入判断を促すものではありません。');
    L.push('- 娯楽・研究用です。舟券購入を推奨しません。');
    note.value=L.join('\\\\n');
  }
}
function roleStrategyHtml(r){
  var s=r.strategy&&r.strategy.buy_style_1;
  if(!s) return '';
  var hist=s.historical&&s.historical.good_venues||{}, overall=s.historical&&s.historical.overall||{};
  var cls=s.status||'outside_rule';
  var condition=s.condition_matched?'条件一致':'条件外';
  var result=s.result_check||{};
  var hitText=result.hit==null?'':(' / 照合 '+(result.hit?'的中':'不的中'));
  return '<div class="role-strategy">'+
    '<b>買い方1（検証用9点）</b>: <span class="'+cls+'">'+esc(s.status_label||condition)+'</span> / 場相性 <span class="'+(s.venue_tier||'neutral')+'">'+esc(s.venue_label||'—')+'</span>'+hitText+
    '<br><span class="muted">条件: 1号艇弱め・外枠最強が上・勝率差1.5以内・非進入固定・外枠展示上位</span>'+
    '<br><span class="muted">過去全体 '+esc(overall.races||'—')+'R ROI '+pctText(overall.return_rate)+' / 相性良場 '+esc(hist.races||'—')+'R ROI '+pctText(hist.return_rate)+'（後半 '+pctText(hist.validation_return_rate)+'）</span>'+
    '<br><span class="muted">'+esc(s.venue_reason||'')+'</span>'+
  '</div>';
}
function updateRoleResults(){
  document.querySelectorAll('[data-role-result]').forEach(function(el){
    var raw=el.getAttribute('data-role-json');
    if(!raw) return;
    try{el.innerHTML=roleResultHtml(JSON.parse(raw));}catch(e){}
  });
}
function renderRoleRanking(data){
  ROLE_RANKING_DATA=data;
  var status=document.getElementById('role-status'),list=document.getElementById('role-list');
  renderRateTop10(data);
  if(!status||!list) return;
  if(!data||!Array.isArray(data.races)||!data.races.length){
    status.textContent='ロール分析データが空です。';
    return;
  }
  if(data.date&&data.date!==RDATE){
    status.textContent='ロール分析データの日付が違うため表示を止めています（'+data.date+'）。';
    return;
  }
  var modeLabel=data.mode==='morning'?'朝版':'直前版';
  var strategyMatches=data.races.filter(function(r){var s=r.strategy&&r.strategy.buy_style_1; return s&&s.condition_matched;});
  var goodMatches=strategyMatches.filter(function(r){return (r.strategy.buy_style_1.venue_tier)==='good';});
  status.textContent=modeLabel+'ロール候補を表示中。買い方1条件一致 '+strategyMatches.length+'件 / 相性良 '+goodMatches.length+'件。条件一致がある場合は優先表示します。';
  var seen={}, display=[];
  strategyMatches.sort(function(a,b){
    var order={good:3,hold:2,neutral:1,avoid:0};
    var av=order[(a.strategy.buy_style_1.venue_tier)||'neutral']||0;
    var bv=order[(b.strategy.buy_style_1.venue_tier)||'neutral']||0;
    return bv-av;
  }).concat(data.races).forEach(function(r){
    if(!seen[r.race_id]){seen[r.race_id]=1; display.push(r);}
  });
  list.innerHTML=display.slice(0,8).map(function(r,i){
    var roles=r.role_summary||{}, scores=r.scores||{};
    var score=scores.manshu_score==null?'—':Math.round(scores.manshu_score);
    var manshu=scores.manshu_probability_proxy==null?'—':(scores.manshu_probability_proxy*100).toFixed(1)+'%';
    var target=scores.target_arare_probability_proxy==null?'—':Math.round(scores.target_arare_probability_proxy*100)+'%';
    var skip=r.skip_recommendation&&r.skip_recommendation.skip;
    return '<div class="roleitem">'+
      '<div class="roletop"><div><b>'+(i+1)+'位 '+esc(r.venue_name)+esc(r.race_no)+'R</b> <span class="muted">'+esc(r.deadline||'')+'</span></div><span class="rolemetric">推定万舟率 '+manshu+' / 荒れ度 '+score+' / 中荒れ '+target+'</span></div>'+
      '<div class="rolegrid">'+
        roleBox(r,'頭候補2艇','head',roles.head)+
        roleBox(r,'軸候補2艇','axis',roles.axis)+
        roleBox(r,'消し候補','toss',roles.toss)+
        roleBox(r,'残り相手','opponent',roles.opponent)+
      '</div>'+
      '<div class="rolefoot role-result" data-role-result="'+esc(roleResultKey(r))+'" data-role-json="'+esc(JSON.stringify({jcd:r.jcd,race_no:r.race_no,result:r.result||{}}))+'">'+roleResultHtml(r)+'</div>'+
      roleStrategyHtml(r)+
      '<div class="rolefoot">'+(skip?'見送り候補: '+esc((r.skip_recommendation.reasons||[]).join(' / ')):'見送り判定なし')+'</div>'+
    '</div>';
  }).join('');
}
async function loadRoleRanking(){
  var status=document.getElementById('role-status');
  if(!status) return;
  try{
    var r=await fetch(roleDataUrl(),{cache:'no-store'});
    if(!r.ok) throw 0;
    renderRoleRanking(await r.json());
  }catch(e){
    status.textContent='本日のロール分析JSONはまだ未生成です。生成されると、頭2艇・軸2艇・消し候補がここに表示されます。';
  }
}"""


def default_targets(root: Path) -> list[Path]:
    paths = [root / "manshu.html"]
    paths.extend(sorted((root / "manshu").glob("*.html")))
    return [path for path in paths if path.exists()]


def patch_html(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    if 'var RDATE="' not in text:
        print(f"skip unsupported manshu page: {path}")
        return False
    if ".card.role" not in text and not any(
        marker in text
        for marker in [
            ".card.rank{border-left-color:#7c3aed} .card.rank h2{color:#6d28d9}\n",
            "footer{color:#8a93a6;font-size:11.5px;text-align:center;margin-top:20px}\n",
        ]
    ):
        print(f"skip unsupported manshu page: {path} (CSS marker not found)")
        return False
    if 'id="role-card"' not in text and not any(
        [
            re.search(r'(<div class="bar">.*?</div>\n)(<div class="card rank">)', text, flags=re.S),
            re.search(r'(<p class="sub">📅 .*?</p>\n)', text, flags=re.S),
            re.search(r'(<details class="card">)', text),
        ]
    ):
        print(f"skip unsupported manshu page: {path} (role card marker not found)")
        return False
    if "var ROLE_RESULT_MAP={};" not in text and not re.search(r'(var RDATE="[^"]+", MAN=10000;\n)', text):
        print(f"skip unsupported manshu page: {path} (RDATE marker not found)")
        return False
    if "function roleDataUrl()" not in text and "function acc(tr){" not in text:
        print(f"skip unsupported manshu page: {path} (JS marker not found)")
        return False
    if "ROLE_RESULT_MAP=map;" not in text and "  var done=0,man=0,total=0,pend=0;" not in text:
        print(f"skip unsupported manshu page: {path} (loadResults marker not found)")
        return False
    if (
        "loadRoleRanking()" not in text.split("document.addEventListener('DOMContentLoaded'", 1)[-1]
        and "showLen('xrank'); loadTimes();" not in text
        and "showLen('xrank');" not in text
    ):
        print(f"skip unsupported manshu page: {path} (DOMContentLoaded marker not found)")
        return False

    if ".card.role" not in text:
        marker = ".card.rank{border-left-color:#7c3aed} .card.rank h2{color:#6d28d9}\n"
        if marker in text:
            text = text.replace(marker, marker + ROLE_CSS + "\n", 1)
        else:
            marker = "footer{color:#8a93a6;font-size:11.5px;text-align:center;margin-top:20px}\n"
            text = text.replace(marker, ROLE_CSS + "\n" + marker, 1)
    elif ".role-strategy" not in text:
        text = text.replace(
            ".role-result{font-weight:800;color:#33405a}",
            """.role-result{font-weight:800;color:#33405a}
.role-strategy{margin-top:7px;border:1px solid #e5e7eb;background:#fafafa;border-radius:8px;padding:7px 8px;font-size:12px;line-height:1.5;color:#374151}
.role-strategy b{color:#111827}.role-strategy .good{color:#047857;font-weight:900}.role-strategy .hold{color:#b45309;font-weight:900}.role-strategy .avoid{color:#b91c1c;font-weight:900}.role-strategy .outside_rule{color:#64748b;font-weight:900}""",
            1,
        )
    elif ".rate-table" not in text:
        marker = ".role-strategy b{color:#111827}.role-strategy .good{color:#047857;font-weight:900}.role-strategy .hold{color:#b45309;font-weight:900}.role-strategy .avoid{color:#b91c1c;font-weight:900}.role-strategy .outside_rule{color:#64748b;font-weight:900}"
        text = text.replace(
            marker,
            marker
            + """
.rate-scroll{overflow-x:auto;margin-top:8px}
.rate-table{min-width:980px}
.rate-table th,.rate-table td{vertical-align:top}
.rate-table .race{font-weight:800;color:#33405a;white-space:nowrap}
.rate-table .rate{font-weight:900;color:#7c3aed;white-space:nowrap}
.rate-table .result{white-space:nowrap}
.rate-table .roi{font-weight:900;color:#047857;white-space:nowrap}
.rate-table .miss{color:#64748b;font-weight:800}
.rate-roles{font-size:11.8px;line-height:1.55;color:#475569;min-width:220px;max-width:320px}
.rate-roles b{color:#33405a}
.rate-summary{font-size:12.5px;color:#33405a;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px 10px;margin:8px 0 0}
.post-card textarea{min-height:150px}""",
            1,
        )
    elif ".rate-roles" not in text:
        text = text.replace(".rate-table{min-width:720px}", ".rate-table{min-width:980px}", 1)
        text, count = re.subn(
            r"\.rate-tickets\{[^}]+\}",
            """.rate-roles{font-size:11.8px;line-height:1.55;color:#475569;min-width:220px;max-width:320px}
.rate-roles b{color:#33405a}""",
            text,
            count=1,
        )
        if count != 1:
            text = text.replace(
                ".rate-table .miss{color:#64748b;font-weight:800}",
                """.rate-table .miss{color:#64748b;font-weight:800}
.rate-roles{font-size:11.8px;line-height:1.55;color:#475569;min-width:220px;max-width:320px}
.rate-roles b{color:#33405a}""",
                1,
            )

    if 'id="role-card"' not in text:
        count = 0
        if '<div class="card rank">' in text:
            text, count = re.subn(
                r'(<div class="bar">.*?</div>\n)(<div class="card rank">)',
                r"\1" + ROLE_CARD + "\n" + r"\2",
                text,
                count=1,
                flags=re.S,
            )
        if count != 1:
            text, count = re.subn(
                r'(<p class="sub">📅 .*?</p>\n)',
                r"\1" + ROLE_CARD + "\n",
                text,
                count=1,
                flags=re.S,
            )
        if count != 1:
            text, count = re.subn(
                r'(<details class="card">)',
                ROLE_CARD + "\n" + r"\1",
                text,
                count=1,
            )
        if count != 1:
            raise RuntimeError(f"{path}: role card insertion marker not found")

    if 'id="rate-card"' not in text:
        count = 0
        if 'id="role-card"' in text:
            text, count = re.subn(
                r'(<div class="card role" id="role-card">)',
                RATE_CARD + "\n" + r"\1",
                text,
                count=1,
            )
        if count != 1:
            text, count = re.subn(
                r'(<p class="sub">📅 .*?</p>\n)',
                r"\1" + RATE_CARD + "\n",
                text,
                count=1,
                flags=re.S,
            )
        if count != 1:
            raise RuntimeError(f"{path}: rate card insertion marker not found")
    elif "頭・軸・消し" not in text:
        text = text.replace(
            "<thead><tr><th>順位</th><th>場R</th><th>万舟率</th><th>結果</th><th>買い方1</th><th>買い目（買い方1）</th><th>単発ROI</th></tr></thead>",
            "<thead><tr><th>順位</th><th>場R</th><th>万舟率</th><th>結果</th><th>買い方1</th><th>頭・軸・消し</th><th>単発ROI</th></tr></thead>",
            1,
        )
        text = text.replace(
            "<thead><tr><th>順位</th><th>場R</th><th>万舟率</th><th>結果</th><th>買い方1</th><th>単発ROI</th></tr></thead>",
            "<thead><tr><th>順位</th><th>場R</th><th>万舟率</th><th>結果</th><th>買い方1</th><th>頭・軸・消し</th><th>単発ROI</th></tr></thead>",
            1,
        )

    if "var ROLE_RESULT_MAP={};" not in text:
        text, count = re.subn(
            r'(var RDATE="[^"]+", MAN=10000;\n)',
            r"\1var ROLE_RESULT_MAP={};\n",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"{path}: RDATE marker not found")

    text = text.replace(
        "トップ5の結果・的中・回収率を短文で投稿できます。",
        "トップ5の観察メモを短文で投稿できます。買い目や回収率は載せません。",
    )
    text = text.replace(
        "トップ10の結果・根拠・回収率をそのままnoteへ貼れる形式です。",
        "トップ10の観察メモと答え合わせをnoteへ貼れる形式です。買い目や回収率は載せません。",
    )

    if "function roleDataUrl()" not in text:
        marker = "function acc(tr){"
        if marker not in text:
            raise RuntimeError(f"{path}: JS insertion marker not found")
        text = text.replace(marker, ROLE_JS + "\n" + marker, 1)
    elif (
        "function roleStrategyHtml(" not in text
        or "strategyMatches=data.races.filter" not in text
        or "function renderRateTop10(" not in text
        or "var ROLE_RANKING_DATA=null;" not in text
        or "x.value=xlines.join('\\\\n');" not in text
        or "note.value=L.join('\\\\n');" not in text
        or "rateRolesHtml" not in text
        or "ticketsHtml" in text
        or "role-tickets" in text
        or "検証用フォーメーション" in text
        or "買い方1=9点検証" in text
        or "確定分ROI" in text
        or "回収率をそのままnote" in text
        or "L.push('- 買い目:" in text
    ):
        text, count = re.subn(
            r"(?:var ROLE_RANKING_DATA=null;\n)?function esc\(s\)\{.*?\nfunction acc\(tr\)\{",
            ROLE_JS + "\nfunction acc(tr){",
            text,
            count=1,
            flags=re.S,
        )
        if count != 1:
            text, count = re.subn(
                r"function esc\(s\)\{.*?\nasync function loadResults\(\)\{",
                ROLE_JS + "\nasync function loadResults(){",
                text,
                count=1,
                flags=re.S,
            )
        if count != 1:
            raise RuntimeError(f"{path}: existing role JS replacement marker not found")

    text = re.sub(r"\n?\.role-tickets\{[^}]+\}", "", text)

    if "ROLE_RESULT_MAP=map;" not in text:
        marker = "  var done=0,man=0,total=0,pend=0;"
        text = text.replace(marker, "  ROLE_RESULT_MAP=map;\n  updateRoleResults();\n  updateRateResults();\n" + marker, 1)
    elif "updateRateResults();" not in text:
        text = text.replace("  updateRoleResults();\n", "  updateRoleResults();\n  updateRateResults();\n", 1)

    if "loadRoleRanking()" not in text.split("document.addEventListener('DOMContentLoaded'", 1)[-1]:
        if "showLen('xrank'); loadTimes();" in text:
            marker = "showLen('xrank'); loadTimes();"
            text = text.replace(marker, "showLen('xrank'); loadRoleRanking(); loadTimes();", 1)
        else:
            marker = "showLen('xrank');"
            text = text.replace(marker, "showLen('xrank'); loadRoleRanking();", 1)
    if "loadResults()" not in text.split("document.addEventListener('DOMContentLoaded'", 1)[-1]:
        text = text.replace("loadRoleRanking();", "loadRoleRanking(); loadResults();", 1)

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="HTML files to patch. Defaults to manshu.html and manshu/*.html")
    return parser


def run(args: argparse.Namespace) -> int:
    root = Path.cwd()
    targets = [Path(path) for path in args.paths] if args.paths else default_targets(root)
    changed = []
    for target in targets:
        if patch_html(target):
            changed.append(str(target))
    if changed:
        print("patched role widget:")
        for path in changed:
            print(f"- {path}")
    else:
        print("role widget already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
