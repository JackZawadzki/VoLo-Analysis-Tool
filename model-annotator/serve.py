"""Local web UI for model-annotator — drag in an .xlsx/.xlsm model, get the
full diligence report in the browser. Stdlib only (no Flask), so it adds no
dependencies to the package.

    python serve.py                # http://127.0.0.1:8765
    python serve.py --port 9000 --llm
"""
from __future__ import annotations

import argparse
import html
import io
import logging
import re
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_annotator import annotate
from model_annotator.schema import Report, Severity, TieOutStatus

log = logging.getLogger("model_annotator.serve")


def _load_dotenv() -> Path | None:
    """Populate ANTHROPIC_API_KEY from the nearest .env that actually carries
    one — checking this dir, then each parent — unless the shell already set it.
    Dependency-free; only reads the keys we use. Returns the file used (if any)."""
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return None
    here = Path(__file__).resolve()
    seen: set[Path] = set()
    for d in [here.parent, *here.parents]:
        env = d / ".env"
        if env in seen or not env.is_file():
            continue
        seen.add(env)
        found = False
        for raw in env.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") and v:
                os.environ.setdefault(k, v)
                found = True
        if found:
            return env       # this .env had a usable key; stop here
        # otherwise keep walking up — a parent .env may carry the real key
    return None


_DOTENV_USED = _load_dotenv()

USE_LLM = False  # set by --llm

_SEV_COLOR = {
    Severity.critical: "#b11226",
    Severity.high: "#d9730d",
    Severity.medium: "#b8860b",
    Severity.info: "#5a6b7b",
}
_SEV_COLOR_STR = {k.value: v for k, v in _SEV_COLOR.items()}
_STATUS_BADGE = {
    TieOutStatus.passed: ("✓ pass", "#1a7f4b"),
    TieOutStatus.failed: ("✗ FAIL", "#b11226"),
    TieOutStatus.not_applicable: ("– n/a", "#8a94a0"),
    TieOutStatus.unfalsifiable: ("⚠ unfalsifiable", "#b11226"),
}


def e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return e(str(v)[:60])


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

PAGE_CSS = """
:root{--bg:#f4f1ea;--panel:#fffdf8;--ink:#26211b;--muted:#6b6256;--line:#e3ddd0;--accent:#9c4221}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:32px 24px 80px}
h1{font-family:Georgia,"Times New Roman",serif;font-weight:600;letter-spacing:-.01em;margin:0 0 4px}
h2{font-family:Georgia,serif;font-weight:600;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{margin:20px 0 6px;font-size:16px}
a{color:var(--accent)}
.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0;box-shadow:0 1px 0 rgba(0,0,0,.02)}
.drop{display:block;border:2px dashed #c8bfae;border-radius:14px;background:var(--panel);padding:48px 24px;text-align:center;cursor:pointer;transition:.15s}
.drop.hover{border-color:var(--accent);background:#fbf6ef}
.drop input{display:none}
.btn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:15px;cursor:pointer;text-decoration:none}
.btn.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:8px 0}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
code,.mono{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12.5px;background:#f0ebe0;padding:1px 5px;border-radius:4px}
.pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;color:#fff;padding:2px 9px;border-radius:999px}
.trust{font-size:34px;font-weight:700;font-family:Georgia,serif}
.kpis{display:flex;gap:26px;flex-wrap:wrap;margin:6px 0 0}
.kpi b{display:block;font-size:22px;font-family:Georgia,serif}
.kpi span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.q{border-left:3px solid var(--accent);padding:6px 0 6px 14px;margin:8px 0;color:#3a3127;font-style:italic}
.muted{color:var(--muted)}
.ev{margin:6px 0 0;padding:0;list-style:none}
.ev li{padding:2px 0}
details summary{cursor:pointer;font-weight:600;color:var(--muted)}
.err{background:#fdecec;border:1px solid #f3b9b9;color:#7a1414;padding:14px 18px;border-radius:10px;white-space:pre-wrap;font-family:monospace;font-size:12.5px}
.spin{display:none;margin-top:14px;color:var(--muted)}
.fld{scroll-margin-top:14px}
/* analyst worksheet tables */
.fam{font-family:Georgia,serif;font-size:18px;font-weight:600;margin:26px 0 4px}
.wk{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:12px 0;overflow:hidden}
.wk-h{padding:13px 16px 11px;border-bottom:1px solid var(--line)}
.wk-h .t{font-weight:600;font-size:15px}
.wk-h .why{color:var(--muted);font-size:13px;margin-top:3px}
.wk-h .comp{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:11.5px;color:#7d756a;background:#f0ebe0;padding:1px 6px;border-radius:4px;display:inline-block;margin-top:6px}
.wk-h .chip{display:inline-block;font-size:11px;color:#7a3d12;background:#f3e3d6;border:1px solid #e6cdba;border-radius:999px;padding:1px 8px;margin:6px 6px 0 0}
.wk-h .chip.ll{color:#27506b;background:#dceaf3;border-color:#bcd7e6}
.scroll{overflow-x:auto}
table.grid{border-collapse:collapse;font-size:12.5px;min-width:100%}
table.grid th,table.grid td{padding:5px 9px;text-align:right;white-space:nowrap;border-bottom:1px solid #f0ece2}
table.grid th.lab,table.grid td.lab{text-align:left;position:sticky;left:0;background:var(--panel);min-width:210px;max-width:280px;overflow:hidden;text-overflow:ellipsis;font-weight:500}
table.grid thead th{position:sticky;top:0;background:#efe9dd;color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;z-index:1}
table.grid thead th.lab{z-index:2;background:#efe9dd}
tr.src td{color:#5f574b}
tr.src td.lab{color:#3a3128}
tr.src td.lab::before{content:"model";float:right;font-size:9px;color:#a99;background:#f0ece2;border-radius:3px;padding:0 4px;margin-left:6px;letter-spacing:.04em}
tr.der td{font-weight:600;color:var(--ink);border-top:2px solid #d8cfbe}
tr.der td.lab{color:var(--accent)}
tr.der td.lab::before{content:"NEW";float:right;font-size:9px;color:#fff;background:var(--accent);border-radius:3px;padding:0 4px;margin-left:6px;letter-spacing:.04em}
td.inp{color:#1a5fb4}  /* typed input in the model */
td.hl{border-radius:4px;cursor:help;position:relative;font-weight:700}
td.hl.s-critical,td.hl.s-high{background:#fbe3dd;color:#9a2415;box-shadow:inset 0 0 0 1px #e8b3a6}
td.hl.s-medium{background:#fbf0d5;color:#7a5a13;box-shadow:inset 0 0 0 1px #e8d6a6}
td.hl .tip{visibility:hidden;opacity:0;transition:.12s;position:absolute;right:0;bottom:140%;width:260px;background:#26211b;color:#f4f1ea;font-weight:400;text-align:left;border-radius:8px;padding:9px 11px;font-size:12px;line-height:1.45;z-index:20;box-shadow:0 6px 22px rgba(0,0,0,.25);white-space:normal}
td.hl .tip::after{content:"";position:absolute;right:14px;top:100%;border:6px solid transparent;border-top-color:#26211b}
td.hl:hover .tip{visibility:visible;opacity:1}
.legend{color:var(--muted);font-size:12px;margin:4px 0 0}
.legend b{color:var(--accent)}
.wkflag{font-size:13px;padding:9px 14px;margin:0 16px 14px;background:#faf6ef;border-left:3px solid #b8860b;border-radius:0 6px 6px 0;line-height:1.5}
.wkflag b{font-size:11px;letter-spacing:.03em;margin-right:6px}
/* precise citations */
.cite{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;color:#8a7f6f;background:#f0ece2;border-radius:3px;padding:0 5px;margin-left:8px;cursor:copy;white-space:nowrap}
.cite:hover{color:var(--accent);background:#f3e3d6}
/* executive summary */
.exec{background:#fffdf8;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:16px 20px;margin:14px 0;font-size:15px;line-height:1.6}
.exec .lab{font-family:Georgia,serif;font-weight:600;color:var(--accent);font-size:13px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
/* tornado */
.torn{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:14px 0;padding:16px 18px}
.torn h3{margin:0 0 2px}
.torn .out{color:var(--muted);font-size:13px;margin-bottom:12px}
.torn .out b{color:var(--ink);font-size:15px}
.tbar{display:grid;grid-template-columns:200px 1fr 96px;gap:10px;align-items:center;margin:7px 0;font-size:13px}
.tbar .nm{text-align:right;color:#3a3128}
.tbar .track{position:relative;height:26px;background:#f4efe4;border-radius:5px;overflow:visible}
.tbar .fill{position:absolute;top:4px;height:18px;border-radius:4px;background:linear-gradient(90deg,#c98a5e,#9c4221)}
.tbar .mid{position:absolute;top:-3px;bottom:-3px;width:2px;background:#7d756a;z-index:2}
.tbar .sw{color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}
.ranges{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.ranges table{width:100%;font-size:12.5px}
.ranges input[type=number]{width:62px;font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:5px;padding:2px 5px;text-align:right}
.ranges .rcite{font-family:"SF Mono",Menlo,monospace;font-size:10.5px;color:#8a7f6f}
.ranges button{background:transparent;border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12.5px;margin-top:8px}
"""

INDEX_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>model-annotator</title><style>{css}</style></head><body><div class=wrap>
<h1>model-annotator</h1>
<p class=sub>Automated financial-model diligence. Drop a startup's Excel model and get the analysis a senior VC analyst performs by hand — tie-outs, derived metrics, findings with evidence and the questions to ask management.</p>
<form id=f method=post action=/annotate enctype=multipart/form-data>
  <label class=drop id=drop>
    <input type=file name=model id=file accept=".xlsx,.xlsm" required>
    <div style="font-size:18px;font-family:Georgia,serif">Drop an .xlsx / .xlsm model here</div>
    <div class=muted id=fname style="margin-top:8px">or click to choose a file</div>
  </label>
  <div class=row style="margin-top:16px">
    <button class=btn type=submit>Annotate model</button>
    <label class=muted style="font-size:13.5px"><input type=checkbox name=llm {llm_checked} {llm_dis}> LLM polish {llm_note}</label>
  </div>
  <div class=spin id=spin>Running the pipeline — ingest → tie-outs → metrics → findings…</div>
</form>
{recent}
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file'),fn=document.getElementById('fname');
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.add('hover')}}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.remove('hover')}}));
drop.addEventListener('drop',e=>{{file.files=e.dataTransfer.files;fn.textContent=file.files[0]?.name||''}});
file.addEventListener('change',()=>fn.textContent=file.files[0]?.name||'or click to choose a file');
document.getElementById('f').addEventListener('submit',()=>{{document.getElementById('spin').style.display='block'}});
</script>
</div></body></html>"""


def render_index(recent_html: str = "") -> str:
    return INDEX_HTML.format(
        css=PAGE_CSS,
        recent=recent_html,
        llm_checked="checked" if USE_LLM else "",
        llm_dis="" if USE_LLM else "disabled",
        llm_note="(enabled)" if USE_LLM else "(start with --llm + ANTHROPIC_API_KEY)",
    )


def _cell_text(value, is_percent: bool) -> str:
    if value is None:
        return "·"
    if is_percent:
        return f"{value * 100:.0f}%" if abs(value) >= 0.1 else f"{value * 100:.1f}%"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:.3f}"


_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def _table_flag(t, findings_by_id):
    """One-sentence flag summary for a worksheet calculation: (text, severity|None)."""
    related = [findings_by_id[i] for i in t.related_finding_ids if i in findings_by_id]
    related.sort(key=lambda f: _SEV_RANK.get(f.severity.value, 9))
    if related:
        f = related[0]
        q = f" — {f.management_question}" if f.management_question else ""
        return (f"{f.title}{q}", f.severity.value)
    # else summarize the standout cells on the derived row
    if t.derived_row:
        hl = [c for c in t.derived_row.cells if c.highlighted and c.comment]
        if hl:
            order = {"critical": 0, "high": 1, "medium": 2, None: 3}
            hl.sort(key=lambda c: order.get(c.severity.value if c.severity else None, 3))
            sev = hl[0].severity.value if hl[0].severity else "medium"
            extra = f" (+{len(hl) - 1} more period{'s' if len(hl) > 2 else ''})" if len(hl) > 1 else ""
            return (hl[0].comment + extra, sev)
    return ("No flags — this calculation looks clean.", None)


def _model_level_flags(report):
    """Findings not attached to any worksheet calculation (structural ones), >= medium."""
    attached = set()
    for t in report.annotation_tables:
        attached.update(t.related_finding_ids)
    out = [f for f in report.findings
           if f.id not in attached and f.severity.value in ("critical", "high", "medium")]
    out.sort(key=lambda f: _SEV_RANK.get(f.severity.value, 9))
    return out


def render_annotation_table(t, findings_by_id=None) -> str:
    o: list[str] = []
    a = o.append
    a("<div class=wk>")
    a("<div class=wk-h>")
    a(f"<div class=t>{e(t.title)}</div>")
    if t.rationale:
        a(f"<div class=why>{e(t.rationale)}</div>")
    a(f"<div class=comp>{e(t.computation)}</div>")
    if t.llm_directed:
        a("<span class='chip ll'>LLM-directed</span>")
    for fid in t.related_finding_ids:
        a(f"<a class=chip href='#{e(fid)}'>see {e(fid)}</a>")
    a("</div>")
    a("<div class=scroll><table class=grid><thead><tr>")
    a("<th class=lab>row</th>")
    for p in t.periods:
        a(f"<th>{e(p)}</th>")
    a("</tr></thead><tbody>")
    # source rows in full, each with a precise navigable citation ("Sheet · row N")
    for sr in t.source_rows:
        cite = ""
        if sr.sheet and sr.row_index:
            short = sr.sheet if len(sr.sheet) <= 16 else sr.sheet[:15] + "…"
            cite = (f"<span class=cite title='click to copy {e(sr.sheet)}!{sr.row_index}' "
                    f"data-copy='{e(sr.sheet)}!{sr.row_index}'>{e(short)} · r{sr.row_index}</span>")
        a(f"<tr class=src><td class=lab title='{e(sr.sheet)} · row {sr.row_index} · {e(sr.label)}'>"
          f"{e(sr.label)}{cite}</td>")
        by_p = {c.period: c for c in sr.cells}
        for p in t.periods:
            c = by_p.get(p)
            cls = " inp" if (c and c.is_input) else ""
            tip = f" title='{e(c.ref)}'" if (c and c.ref) else ""
            a(f"<td class='v{cls}'{tip}>{_cell_text(c.value if c else None, sr.is_percent)}</td>")
        a("</tr>")
    # the new derived row
    dr = t.derived_row
    if dr is not None:
        a(f"<tr class=der><td class=lab>{e(dr.label)}</td>")
        by_p = {c.period: c for c in dr.cells}
        for p in t.periods:
            c = by_p.get(p)
            if c is None:
                a("<td>·</td>")
                continue
            txt = _cell_text(c.value, dr.is_percent)
            if c.highlighted and c.comment:
                sev = (c.severity.value if c.severity else "medium")
                a(f"<td class='hl s-{sev}'>{txt}<span class=tip>{e(c.comment)}</span></td>")
            else:
                a(f"<td>{txt}</td>")
        a("</tr>")
    a("</tbody></table></div>")
    # one-sentence flag summary for this calculation
    flag_text, flag_sev = _table_flag(t, findings_by_id or {})
    if flag_sev:
        color = _SEV_COLOR_STR.get(flag_sev, "#7a5a13")
        a(f"<div class=wkflag style='border-left-color:{color}'>"
          f"<b style='color:{color}'>⚑ {flag_sev.upper()}</b> {e(flag_text)}</div>")
    else:
        a(f"<div class=wkflag class=clean style='border-left-color:#1a7f4b'>"
          f"<b style='color:#1a7f4b'>✓</b> <span class=muted>{e(flag_text)}</span></div>")
    a("</div>")
    return "\n".join(o)


def render_tornado(t, idx: int) -> str:
    """Static tornado + an editable range table; live recompute via JS (the
    formula mirrors the server-side closed form)."""
    o: list[str] = []
    a = o.append
    tid = f"torn{idx}"
    a(f"<div class=torn id={tid}>")
    a(f"<h3>{e(t.output_label)}</h3>")
    a(f"<div class=out>{e(t.formula_note)} — base <b class=base>{_cell_text(t.output_base, False)}</b> "
      f"<span class=muted>{e(t.output_unit)}</span></div>")
    a("<div class=bars>")
    for d in t.drivers:
        a(f"<div class=tbar data-key='{e(d.key)}'>"
          f"<div class=nm>{e(d.label)}</div>"
          f"<div class=track><div class=fill></div><div class=mid></div></div>"
          f"<div class=sw>±<span class=swv></span></div></div>")
    a("</div>")
    # editable ranges
    a("<div class=ranges><table><tr><th style='text-align:left'>driver</th><th>cell</th>"
      "<th>base</th><th>low</th><th>high</th></tr>")
    for d in t.drivers:
        refs = ", ".join(d.input_refs)
        a(f"<tr data-key='{e(d.key)}'>"
          f"<td style='text-align:left'>{e(d.label)}</td>"
          f"<td class=rcite>{e(refs)}</td>"
          f"<td style='text-align:right'>{_cell_text(d.base, d.unit=='fraction')}</td>"
          f"<td><input type=number class=lo step=any value='{round(d.low,6)}'></td>"
          f"<td><input type=number class=hi step=any value='{round(d.high,6)}'></td></tr>")
    a("</table><button class=reset>Reset ranges to ±20% / benchmark</button>"
      "<div class=muted style='font-size:11.5px;margin-top:6px'>Edit a low/high and the tornado updates live. "
      "Values are the model's own input cells.</div></div>")
    # data for JS
    import json as _json
    spec = {"formula": t.formula, "base": t.output_base,
            "drivers": [{"key": d.key, "base": d.base, "low": d.low, "high": d.high,
                         "coef": 0.0} for d in t.drivers]}
    # linear_sum needs coefficients (sign); recover from output deltas
    if t.formula == "linear_sum":
        for sd, d in zip(spec["drivers"], t.drivers):
            sd["coef"] = 1.0 if d.output_high > d.output_low else -1.0
    if t.formula == "valuation_pv":
        m = re.search(r"\^(\d+(?:\.\d+)?)", t.formula_note)
        spec["horizon"] = float(m.group(1)) if m else 0.0
    a(f"<script type=application/json id={tid}-data>{_json.dumps(spec)}</script>")
    a("</div>")
    return "\n".join(o)


TORNADO_JS = """
function maFmt(v){const a=Math.abs(v);if(a>=1000)return v.toLocaleString(undefined,{maximumFractionDigits:0});if(a>=1)return v.toFixed(2).replace(/\\.?0+$/,'');return v.toFixed(3);}
function maEval(spec, vals){
  const get=(k)=>{ if(k in vals) return vals[k]; const d=spec.drivers.find(x=>x.key===k); return d?d.base:0; };
  if(spec.formula==='valuation_pv'){
    const rateD=spec.drivers.find(d=>d.key==='rate');
    const rate=rateD?get('rate'):0;
    return get('multiple')*get('metric')/Math.pow(1+rate, spec.horizon||0);
  }
  let s=0; spec.drivers.forEach(d=>{ s += d.coef * get(d.key); });
  return s;
}
function maBase(spec){ let v={}; spec.drivers.forEach(d=>v[d.key]=d.base); return maEval(spec, v); }
function maRender(torn){
  const spec=JSON.parse(torn.querySelector('script[type="application/json"]').textContent);
  const rows=[...torn.querySelectorAll('.ranges tr[data-key]')];
  const cur={};
  rows.forEach(r=>{cur[r.dataset.key]={lo:parseFloat(r.querySelector('.lo').value), hi:parseFloat(r.querySelector('.hi').value)};});
  const base=maBase(spec);
  torn.querySelector('.base').textContent=maFmt(base);
  // compute each driver's output at lo/hi (others at base)
  let res=spec.drivers.map(d=>{
    const lo=maEval(spec, Object.assign(maVals(spec), {[d.key]:cur[d.key].lo}));
    const hi=maEval(spec, Object.assign(maVals(spec), {[d.key]:cur[d.key].hi}));
    return {key:d.key, lo, hi, swing:Math.abs(hi-lo)};
  });
  const maxsw=Math.max(...res.map(r=>r.swing),1e-9);
  // sort bars by swing desc by reordering
  res.sort((a,b)=>b.swing-a.swing);
  const barbox=torn.querySelector('.bars');
  res.forEach(r=>{
    const bar=barbox.querySelector(`.tbar[data-key="${r.key}"]`);
    barbox.appendChild(bar);
    const lo=Math.min(r.lo,r.hi), hi=Math.max(r.lo,r.hi);
    const span=hi-lo; const track=bar.querySelector('.track'); const W=track.clientWidth||600;
    // scale: map [base-maxsw, base+maxsw] to track
    const left=(x)=>Math.max(0,Math.min(1,(x-(base-maxsw))/(2*maxsw)))*100;
    const fill=bar.querySelector('.fill');
    fill.style.left=left(lo)+'%'; fill.style.width=Math.max(1,left(hi)-left(lo))+'%';
    bar.querySelector('.mid').style.left=left(base)+'%';
    bar.querySelector('.swv').textContent=maFmt(r.swing);
  });
}
function maVals(spec){ let v={}; spec.drivers.forEach(d=>v[d.key]=d.base); return v; }
document.querySelectorAll('.torn').forEach(t=>{
  t.addEventListener('input', ()=>maRender(t));
  t.querySelector('.reset')?.addEventListener('click', ()=>{
    const spec=JSON.parse(t.querySelector('script[type="application/json"]').textContent);
    t.querySelectorAll('.ranges tr[data-key]').forEach(r=>{
      const d=spec.drivers.find(x=>x.key===r.dataset.key);
      r.querySelector('.lo').value=d.low; r.querySelector('.hi').value=d.high;
    });
    maRender(t);
  });
  maRender(t);
});
"""


def render_report(report: Report, filename: str) -> str:
    from model_annotator.narrate import read_this_first, trust_sentence
    wm = report.workbook_map
    o: list[str] = []
    a = o.append
    a("<!doctype html><html><head><meta charset=utf-8>"
      "<meta name=viewport content='width=device-width,initial-scale=1'>"
      f"<title>{e(filename)} — annotation</title><style>{PAGE_CSS}</style></head><body><div class=wrap>")
    a(f"<a href=/ class='btn ghost' style='float:right'>&larr; new file</a>")
    a(f"<h1>{e(filename)}</h1>")

    sev_counts: dict[str, int] = {}
    for f in report.findings:
        sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1

    units = ""
    if wm.primary_statement_sheet and wm.primary_statement_sheet in wm.units:
        u = wm.units[wm.primary_statement_sheet]
        units = f"{e(u.label)} ({u.confidence:.2f})"
    periods = wm.period_axis.periods if wm.period_axis else []
    span = f"{e(periods[0])} – {e(periods[-1])}" if periods else "none"

    n_flags = sum(1 for f in report.findings if f.severity.value in ("critical", "high", "medium"))
    a("<div class=card><div class=row style='justify-content:flex-start;gap:30px;align-items:flex-start'>")
    a("<div class=kpis>")
    a(f"<div class=kpi><b>{n_flags}</b><span>flags</span></div>")
    a(f"<div class=kpi><b>{len([t for t in report.annotation_tables])}</b><span>calculations</span></div>")
    a(f"<div class=kpi><b>{len(periods)}</b><span>periods</span></div>")
    a("</div></div>")
    a(f"<div class=muted style='margin-top:12px'>Primary statements <b>{e(wm.primary_statement_sheet)}</b> · "
      f"periods {span} ({e(wm.period_axis.granularity.value) if wm.period_axis else '–'}) · "
      f"units {units or 'unknown'} · LLM {'used' if report.llm_used else 'off'}</div>")
    a("</div>")

    # executive summary
    es = report.executive_summary
    if es and es.text:
        a("<div class=exec><div class=lab>Executive summary</div>")
        a(f"<div>{e(es.text)}</div></div>")

    # model-level flags that aren't tied to a single calculation
    model_flags = _model_level_flags(report)
    if model_flags:
        a("<h2>Model-level flags <span class=muted style='font-weight:400;font-size:14px'>"
          "— not tied to one calculation</span></h2>")
        a("<div class=card><ul class=ev>")
        for f in model_flags:
            color = _SEV_COLOR[f.severity]
            q = f" <span class=muted>{e(f.management_question)}</span>" if f.management_question else ""
            a(f"<li><span class=pill style='background:{color};font-size:10px'>{f.severity.value.upper()}</span> "
              f"{e(f.title)}{q}</li>")
        a("</ul></div>")

    # analysis plan — why these calculations
    plan = report.analysis_plan
    if plan is not None:
        a("<h2>Analysis plan <span class=muted style='font-weight:400;font-size:14px'>— why these calculations</span></h2>")
        a("<div class=card>")
        src = ("LLM business read of the workbook's structure (labels only — never values)"
               if plan.source == "llm" else "deterministic structural heuristic (no LLM)")
        a(f"<div class=muted style='margin-bottom:8px'>Source: {e(src)}</div>")
        a(f"<div><b>Archetype:</b> {e(plan.archetype)} <span class=muted>(benchmarks: <code>{e(plan.benchmark_archetype)}</code>)</span></div>")
        if plan.rationale:
            a(f"<div style='margin-top:6px'>{e(plan.rationale)}</div>")
        if plan.risks_to_probe:
            a("<div style='margin-top:8px'><b>Risks worth probing:</b> " + "; ".join(e(r) for r in plan.risks_to_probe) + "</div>")
        if plan.custom_computations:
            a("<ul class=ev style='margin-top:8px'>")
            for c in plan.custom_computations:
                status = "computed" if c.executed else f"skipped — {e(c.skip_reason or '')}"
                a(f"<li><code>{e(c.metric_id)}</code> <span class=muted>({status})</span> — {e(c.rationale)}</li>")
            a("</ul>")
        a("</div>")

    # analyst worksheet — each calculation with its source rows AND its own flag line
    findings_by_id = {f.id: f for f in report.findings}
    tables = report.annotation_tables
    if tables:
        a("<h2>Worksheet <span class=muted style='font-weight:400;font-size:14px'>"
          "— each calculation with the model's own rows, and what it flags</span></h2>")
        a("<p class=legend>Each block shows the company's <b style='color:#5f574b'>model rows</b> "
          "and the <b>NEW</b> derived row beneath them, then a one-line <b>flag</b> for that calculation. "
          "Highlighted cells are values that stand out — <b>hover</b> for why.</p>")
        last_fam = None
        for t in tables:
            if t.family != last_fam:
                a(f"<div class=fam>{e(t.family)}</div>")
                last_fam = t.family
            a(render_annotation_table(t, findings_by_id))

    # sensitivity / tornado — off the model's own input cells
    if report.sensitivities:
        a("<h2>Sensitivity <span class=muted style='font-weight:400;font-size:14px'>"
          "— move the model's own inputs, see the effect</span></h2>")
        a("<p class=legend>Each bar is one of the model's input cells flexed across its range; "
          "the bar length is the swing it creates in the output. <b>Edit the low/high</b> below a chart "
          "and it updates live. The center line is the base case.</p>")
        for i, t in enumerate(report.sensitivities):
            a(render_tornado(t, i))
            for cav in t.caveats:
                a(f"<p class=muted style='font-size:11.5px;margin:2px 0 0'>{e(cav)}</p>")

    # tie-outs
    a("<h2>Tie-outs</h2><div class=card><table><tr><th>Check</th><th>Status</th><th>Max residual</th><th>Detail</th></tr>")
    for t in report.tie_outs:
        label, col = _STATUS_BADGE.get(t.status, (t.status.value, "#555"))
        res = f"{t.max_abs_residual:,.2f}" if t.max_abs_residual is not None else ""
        a(f"<tr><td>{e(t.label or t.id)}</td><td><span style='color:{col};font-weight:600'>{e(label)}</span></td>"
          f"<td class=mono>{res}</td><td class=muted>{e(t.detail[:160])}</td></tr>")
    a("</table></div>")

    # acquittals
    a("<h2>Acquittals <span class=muted style='font-weight:400;font-size:14px'>— looked alarming, dissolved</span></h2>")
    if not report.acquittals:
        a("<div class=card class=muted>None this run.</div>")
    for acq in report.acquittals:
        a(f"<div class=card><b>{e(acq.candidate)}</b> <span class=muted>({e(acq.category)})</span><br>{e(acq.decomposition)}</div>")

    # clean checks
    a("<h2>Clean checks <span class=muted style='font-weight:400;font-size:14px'>— what the model gets right</span></h2>")
    a("<div class=card><ul class=ev>")
    for c in report.clean_checks:
        a(f"<li><b>{e(c.check)}</b> — <span class=muted>{e(c.result)}</span></li>")
    a("</ul></div>")

    # appendices (collapsed)
    a("<h2>Appendices</h2>")
    a("<details class=card><summary>A — Derived metrics ({})</summary><table>"
      "<tr><th>Metric</th><th>Checkpoints</th><th>Computation</th></tr>".format(len(report.derived_metrics)))
    for m in report.derived_metrics:
        chk = fmt(m.scalar) if m.scalar is not None else "; ".join(
            f"{e(p)}: {fmt(v)}" for p, v in list(m.checkpoints.items())[:3])
        ext = " <span class=pill style='background:#5a6b7b'>ext</span>" if m.external_benchmark else ""
        a(f"<tr><td>{e(m.metric_id)}{ext}</td><td>{chk}</td><td><code>{e(m.computation[:80])}</code></td></tr>")
    a("</table></details>")

    a("<details class=card><summary>B — Assumptions census ({})</summary><table>"
      "<tr><th>#</th><th>Cell</th><th>Label</th><th>Value</th><th>Stmt cells</th><th>Flat</th><th>Comment</th></tr>".format(
          len(report.assumptions_census)))
    for x in report.assumptions_census[:40]:
        flat = {True: "flat", False: "", None: "?"}[x.flat_all_periods]
        a(f"<tr><td>{x.impact_rank}</td><td><code>{e(x.cell)}</code></td><td>{e(x.label[:48])}</td>"
          f"<td>{fmt(x.value)}</td><td>{x.dependent_statement_cells:,}</td><td>{flat}</td>"
          f"<td class=muted>{e((x.comment_text or '')[:80])}</td></tr>")
    a("</table></details>")

    if report.limitations:
        a("<details class=card><summary>Limitations ({})</summary><ul>".format(len(report.limitations)))
        for lim in report.limitations:
            warn = " style='color:#b11226'" if lim.startswith("CITATION") else ""
            a(f"<li{warn}>{e(lim)}</li>")
        a("</ul></details>")

    a("</div>")
    a("<script>" + TORNADO_JS + """
document.querySelectorAll('.cite[data-copy]').forEach(c=>c.addEventListener('click',()=>{
  navigator.clipboard&&navigator.clipboard.writeText(c.dataset.copy);
  const o=c.textContent;c.textContent='copied '+c.dataset.copy;setTimeout(()=>c.textContent=o,1100);
}));
</script>""")
    a("</body></html>")
    return "\n".join(o)


# ---------------------------------------------------------------------------
# Minimal multipart parser (cgi was removed in 3.13)
# ---------------------------------------------------------------------------

def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    """Return {field_name: (filename_or_'', value_bytes)} for a multipart body."""
    if "boundary=" not in content_type:
        return {}
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    sep = b"--" + boundary.encode()
    out: dict[str, tuple[str, bytes]] = {}
    for part in body.split(sep):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        data = data[:-2] if data.endswith(b"\r\n") else data
        headers = head.decode("utf-8", "replace")
        name = ""
        filename = ""
        for line in headers.splitlines():
            if line.lower().startswith("content-disposition"):
                for tok in line.split(";"):
                    tok = tok.strip()
                    if tok.startswith("name="):
                        name = tok[5:].strip('"')
                    elif tok.startswith("filename="):
                        filename = tok[9:].strip('"')
        if name:
            out[name] = (filename, data)
    return out


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

OUT_ROOT = Path(tempfile.gettempdir()) / "model_annotator_web"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: str, code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(render_index())
        elif self.path == "/health":
            self._send("ok")
        else:
            self._send("<div class=wrap>Not found. <a href=/>Home</a></div>", 404)

    def do_POST(self):
        if self.path != "/annotate":
            self._send("Not found", 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        fields = parse_multipart(body, self.headers.get("Content-Type", ""))
        if "model" not in fields or not fields["model"][0]:
            self._send(render_index("<div class=err>No file was uploaded.</div>"))
            return
        filename, data = fields["model"]
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            self._send(render_index("<div class=err>Please upload an .xlsx or .xlsm file.</div>"))
            return
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False, dir=OUT_ROOT)
        tmp.write(data)
        tmp.close()
        use_llm = USE_LLM and "llm" in fields
        try:
            report = annotate(tmp.name, out_dir=str(OUT_ROOT / Path(filename).stem),
                              no_llm=not use_llm, write_outputs=True)
            self._send(render_report(report, filename))
        except Exception as exc:
            tb = traceback.format_exc()
            log.error("annotation failed: %s", exc)
            self._send(render_index(
                f"<div class=err><b>Annotation failed:</b> {e(exc)}\n\n{e(tb)}</div>"), 500)
        finally:
            try:
                Path(tmp.name).unlink()
            except OSError:
                pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Local web UI for model-annotator")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--llm", action="store_true", help="enable optional LLM narrative polish")
    args = p.parse_args(argv)

    global USE_LLM
    USE_LLM = args.llm
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"\n  model-annotator web UI → {url}")
    print(f"  LLM polish: {'ON' if USE_LLM else 'off (run with --llm and ANTHROPIC_API_KEY to enable)'}")
    print("  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
