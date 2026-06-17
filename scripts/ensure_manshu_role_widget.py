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
.role-result{font-weight:800;color:#33405a}"""


ROLE_CARD = """<div class="card role" id="role-card">
  <h2>🧪 ロール分析（試験表示）</h2>
  <p class="lead" id="role-status">頭2艇・軸2艇・消し候補を読み込み中…</p>
  <div class="rolelist" id="role-list"></div>
  <p class="muted">過去20,947Rで検証した補助表示です。買い目確定や利益保証ではありません。直前版は展示・気象込みのため、朝の判断とは分けて見てください。</p>
</div>"""


ROLE_JS = """function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
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
  return '結果: 3連単 <b>'+esc(combo)+'</b> '+Number(payout).toLocaleString()+'円 '+(man?'<span class="man">◎万舟</span>':'<span class="muted">— 堅め</span>');
}
function updateRoleResults(){
  document.querySelectorAll('[data-role-result]').forEach(function(el){
    var raw=el.getAttribute('data-role-json');
    if(!raw) return;
    try{el.innerHTML=roleResultHtml(JSON.parse(raw));}catch(e){}
  });
}
function renderRoleRanking(data){
  var status=document.getElementById('role-status'),list=document.getElementById('role-list');
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
  status.textContent=modeLabel+'ロール候補を表示中。頭2・軸2・消し1・残り相手1の重複なし分類です。';
  list.innerHTML=data.races.slice(0,5).map(function(r,i){
    var roles=r.role_summary||{}, scores=r.scores||{}, forms=r.formations||{};
    var score=scores.manshu_score==null?'—':Math.round(scores.manshu_score);
    var manshu=scores.manshu_probability_proxy==null?'—':(scores.manshu_probability_proxy*100).toFixed(1)+'%';
    var target=scores.target_arare_probability_proxy==null?'—':Math.round(scores.target_arare_probability_proxy*100)+'%';
    var skip=r.skip_recommendation&&r.skip_recommendation.skip;
    var formText=['A','B','C','D'].map(function(k){return k+':'+(forms[k]&&forms[k].points!=null?forms[k].points:'—')+'点';}).join(' / ');
    return '<div class="roleitem">'+
      '<div class="roletop"><div><b>'+(i+1)+'位 '+esc(r.venue_name)+esc(r.race_no)+'R</b> <span class="muted">'+esc(r.deadline||'')+'</span></div><span class="rolemetric">推定万舟率 '+manshu+' / 荒れ度 '+score+' / 中荒れ '+target+'</span></div>'+
      '<div class="rolegrid">'+
        roleBox(r,'頭候補2艇','head',roles.head)+
        roleBox(r,'軸候補2艇','axis',roles.axis)+
        roleBox(r,'消し候補','toss',roles.toss)+
        roleBox(r,'残り相手','opponent',roles.opponent)+
      '</div>'+
      '<div class="rolefoot role-result" data-role-result="'+esc(roleResultKey(r))+'" data-role-json="'+esc(JSON.stringify({jcd:r.jcd,race_no:r.race_no,result:r.result||{}}))+'">'+roleResultHtml(r)+'</div>'+
      '<div class="rolefoot">'+(skip?'見送り候補: '+esc((r.skip_recommendation.reasons||[]).join(' / ')):'見送り判定なし')+'　検証用フォーメーション '+formText+'</div>'+
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
    if 'var RDATE="' not in text or '<div class="card rank">' not in text:
        print(f"skip unsupported manshu page: {path}")
        return False
    if ".card.role" not in text and ".card.rank{border-left-color:#7c3aed} .card.rank h2{color:#6d28d9}\n" not in text:
        print(f"skip unsupported manshu page: {path} (CSS marker not found)")
        return False
    if 'id="role-card"' not in text and not re.search(r'(<div class="bar">.*?</div>\n)(<div class="card rank">)', text, flags=re.S):
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
    if "loadRoleRanking()" not in text.split("document.addEventListener('DOMContentLoaded'", 1)[-1] and "showLen('xrank'); loadTimes();" not in text:
        print(f"skip unsupported manshu page: {path} (DOMContentLoaded marker not found)")
        return False

    if ".card.role" not in text:
        marker = ".card.rank{border-left-color:#7c3aed} .card.rank h2{color:#6d28d9}\n"
        text = text.replace(marker, marker + ROLE_CSS + "\n", 1)

    if 'id="role-card"' not in text:
        text, count = re.subn(
            r'(<div class="bar">.*?</div>\n)(<div class="card rank">)',
            r"\1" + ROLE_CARD + "\n" + r"\2",
            text,
            count=1,
            flags=re.S,
        )
        if count != 1:
            raise RuntimeError(f"{path}: role card insertion marker not found")

    if "var ROLE_RESULT_MAP={};" not in text:
        text, count = re.subn(
            r'(var RDATE="[^"]+", MAN=10000;\n)',
            r"\1var ROLE_RESULT_MAP={};\n",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"{path}: RDATE marker not found")

    if "function roleDataUrl()" not in text:
        marker = "function acc(tr){"
        if marker not in text:
            raise RuntimeError(f"{path}: JS insertion marker not found")
        text = text.replace(marker, ROLE_JS + "\n" + marker, 1)

    if "ROLE_RESULT_MAP=map;" not in text:
        marker = "  var done=0,man=0,total=0,pend=0;"
        text = text.replace(marker, "  ROLE_RESULT_MAP=map;\n  updateRoleResults();\n" + marker, 1)

    if "loadRoleRanking()" not in text.split("document.addEventListener('DOMContentLoaded'", 1)[-1]:
        marker = "showLen('xrank'); loadTimes();"
        text = text.replace(marker, "showLen('xrank'); loadRoleRanking(); loadTimes();", 1)

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
