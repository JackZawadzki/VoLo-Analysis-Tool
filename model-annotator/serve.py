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
import json
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
/* analyst worksheet — collapsible family sections */
.famsec{margin:12px 0;border:1px solid var(--line);border-radius:12px;background:#fbf8f2;overflow:hidden}
.famsec>summary{cursor:pointer;list-style:none;padding:14px 18px;font-family:Georgia,serif;font-size:17px;
  font-weight:600;display:flex;align-items:center;gap:10px;user-select:none}
.famsec>summary::-webkit-details-marker{display:none}
.famsec>summary::before{content:"▸";color:var(--muted);font-size:13px;transition:transform .15s}
.famsec[open]>summary::before{transform:rotate(90deg)}
.famsec[open]>summary{border-bottom:1px solid var(--line)}
.famsec .famname{color:var(--ink)}
.fambadge{font-family:-apple-system,sans-serif;font-size:11px;font-weight:600;color:#fff;border-radius:999px;
  padding:2px 10px;margin-left:auto}
.famsec>.wk:first-of-type{margin-top:12px}
.wk{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:12px 16px;overflow:hidden}
.wk-h{padding:13px 16px 12px;border-bottom:1px solid var(--line)}
.wk-h .t{font-weight:600;font-size:15px}
.wk-h .why{color:var(--muted);font-size:13px;margin-top:3px;line-height:1.5}
.wk-h .comp{color:#9a9080;font-size:11.5px;margin-top:5px}
.wk-h .comp .fx{font-family:"SF Mono",Menlo,Consolas,monospace;color:#7d756a}
.wk-h .chip{display:inline-block;font-size:11px;color:#7a3d12;background:#f3e3d6;border:1px solid #e6cdba;border-radius:999px;padding:1px 8px;margin:6px 6px 0 0;text-decoration:none}
.wk-h .chip.ll{color:#27506b;background:#dceaf3;border-color:#bcd7e6}
.scroll{overflow-x:auto}
table.grid{border-collapse:collapse;font-size:12.5px;min-width:100%}
table.grid th,table.grid td{padding:5px 9px;text-align:right;white-space:nowrap;border-bottom:1px solid #f0ece2}
table.grid th.lab,table.grid td.lab{text-align:left;position:sticky;left:0;background:var(--panel);min-width:210px;max-width:280px;overflow:hidden;text-overflow:ellipsis;font-weight:500}
table.grid thead th{position:sticky;top:0;background:#efe9dd;color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;z-index:1}
table.grid thead th.lab{z-index:2;background:#efe9dd}
table.grid td.lab{vertical-align:top}
tr.subhdr td{background:#f3eee3;border-bottom:1px solid #e4dcc9;padding-top:9px;padding-bottom:7px}
tr.subhdr td.lab{font-family:Georgia,serif;font-size:10.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#9a8f78;background:#f3eee3}
.rl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:262px}
.prov{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:10px;color:#b0a489;margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:262px;cursor:help}
.cf{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:10px;color:#a89f8c;font-weight:400;margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:262px;cursor:help}
tr.src td{color:#5f574b}
tr.src td.lab{color:#3a3128;font-weight:500}
tr.der td{font-weight:600;color:var(--ink);border-top:1px solid #ece4d3}
tr.der td.lab{color:var(--accent)}
tr.flagrow td{padding:0;border-bottom:1px solid #f0ece2}
tr.flagrow .wkflag{margin:0;border-radius:0;border-left-width:3px}
tr.flagrow .chip{margin-left:8px}
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
/* sensitivity */
.torn{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:14px 0;padding:16px 18px}
.torn h3{margin:0 0 2px}
.torn .out{color:var(--muted);font-size:13px;margin-bottom:6px}
.torn .out b{color:var(--ink);font-size:15px}
.torn .duo{display:flex;gap:18px;color:var(--muted);font-size:12.5px;margin-bottom:12px}
.torn .duo .dn{color:#b11226}
.torn .duo .up{color:#1a7f4b}
.subh{font-family:Georgia,serif;font-size:14px;font-weight:600;margin:14px 0 8px;color:#3a3128}
/* shapley bars: zero line in the middle, negative left (red), positive right (green) */
.shap{display:grid;grid-template-columns:200px 1fr 188px;gap:10px;align-items:center;margin:5px 0;font-size:12.5px}
.shap .nm{text-align:right;color:#3a3128;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.shap .tk{position:relative;height:18px;background:#f4efe4;border-radius:4px}
.shap .zero{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:#b3a892;z-index:2}
.shap .bar{position:absolute;top:3px;height:12px}
.shap .bar.dn{background:#c4543a;border-radius:3px 0 0 3px}
.shap .bar.up{background:#5a9e74;border-radius:0 3px 3px 0}
.shap .vv{color:var(--muted);font-variant-numeric:tabular-nums;text-align:left;font-size:11.5px}
/* two-way grid */
.twoway{margin-top:6px}
.twoway .sel{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:12.5px;color:var(--muted)}
.twoway select{font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:6px;padding:3px 6px;background:#fff;max-width:240px}
.grid2{border-collapse:collapse;font-size:11px}
.grid2 td,.grid2 th{border:1px solid #fff;padding:5px 7px;text-align:center;font-variant-numeric:tabular-nums;min-width:52px}
.grid2 th{background:#efe9dd;color:var(--muted);font-weight:600}
.grid2 .axl{background:#efe9dd;color:var(--muted);font-weight:600;white-space:nowrap}
.grid2 .cap{font-size:11px;color:var(--muted);margin:6px 0 2px}
.ranges{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.ranges table{width:100%;font-size:12.5px}
.ranges input[type=number]{width:64px;font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:5px;padding:2px 5px;text-align:right}
.ranges .rcite{font-family:"SF Mono",Menlo,monospace;font-size:10.5px;color:#8a7f6f}
.ranges button{background:transparent;border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12.5px;margin-top:8px}
.ranges details summary{cursor:pointer;color:var(--muted);font-size:12.5px;margin-top:8px}
/* progress bar */
.prog{display:none;margin-top:22px}
.prog .bartrack{height:12px;background:#e7e0d2;border-radius:8px;overflow:hidden}
.prog .barfill{height:100%;width:0;background:linear-gradient(90deg,#c98a5e,#9c4221);border-radius:8px;transition:width .35s ease}
.prog .plabel{margin-top:10px;color:#3a3128;font-size:14px}
.prog .ppct{color:var(--muted);font-variant-numeric:tabular-nums}
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
    <button class=btn type=submit id=go>Annotate model</button>
    <label class=muted style="font-size:13.5px"><input type=checkbox name=llm {llm_checked} {llm_dis}> LLM polish {llm_note}</label>
  </div>
  <div class=prog id=prog>
    <div class=bartrack><div class=barfill id=barfill></div></div>
    <div class=plabel><span id=plabel>Starting…</span> <span class=ppct id=ppct></span></div>
  </div>
</form>
{recent}
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file'),fn=document.getElementById('fname');
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.add('hover')}}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.remove('hover')}}));
drop.addEventListener('drop',e=>{{file.files=e.dataTransfer.files;fn.textContent=file.files[0]?.name||''}});
file.addEventListener('change',()=>fn.textContent=file.files[0]?.name||'or click to choose a file');
const fill=document.getElementById('barfill'),plabel=document.getElementById('plabel'),ppct=document.getElementById('ppct');
function setProg(frac,label){{fill.style.width=Math.round(frac*100)+'%';ppct.textContent=Math.round(frac*100)+'%';if(label)plabel.textContent=label;}}
document.getElementById('f').addEventListener('submit',async (e)=>{{
  e.preventDefault();
  if(!file.files[0])return;
  document.getElementById('go').disabled=true;
  document.getElementById('prog').style.display='block';
  setProg(0.02,'Uploading…');
  const fd=new FormData(); fd.append('model',file.files[0]);
  if(document.querySelector('input[name=llm]')?.checked) fd.append('llm','on');
  let job;
  try{{ const r=await fetch('/start',{{method:'POST',body:fd}}); job=(await r.json()).job; }}
  catch(err){{ plabel.textContent='Upload failed: '+err; return; }}
  const poll=setInterval(async ()=>{{
    let s; try{{ s=await (await fetch('/progress?job='+job)).json(); }}catch(e){{return;}}
    setProg(s.frac||0, s.phase||'Working…');
    if(s.status==='done'){{ clearInterval(poll); setProg(1,'Done'); window.location.href='/result?job='+job; }}
    else if(s.status==='error'){{ clearInterval(poll); plabel.textContent='Error: '+(s.error||'unknown'); plabel.style.color='#b11226'; }}
  }}, 400);
}});
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


def _prov(sr) -> tuple[str, str]:
    """(short provenance shown on the row, full ref for the hover title)."""
    if sr.sheet and sr.row_index:
        return f"{sr.sheet} · r{sr.row_index}", f"{sr.sheet}!row {sr.row_index}"
    for c in sr.cells:
        if c.ref:
            return c.ref, c.ref
    return "model", "from the workbook"


def render_family(fam: str, tables: list, findings_by_id=None) -> str:
    """One section per family: the model's own cited rows ONCE at the top, then
    every calculation built on them. A row used by several calcs is shown once,
    so the section stays compact. Source provenance is on a hover."""
    findings_by_id = findings_by_id or {}
    o: list[str] = []
    a = o.append
    periods = tables[0].periods if tables else []
    ncol = len(periods) + 1

    # dedupe source rows across all calcs in this family, keep workbook order
    seen: dict[tuple, object] = {}
    for t in tables:
        for sr in t.source_rows:
            key = (sr.sheet, sr.row_index, sr.label)
            if key not in seen:
                seen[key] = sr
    sources = sorted(seen.values(), key=lambda s: (s.sheet or "", s.row_index or 0))

    a("<div class=scroll><table class=grid><thead><tr>")
    a("<th class=lab>row</th>")
    for p in periods:
        a(f"<th>{e(p)}</th>")
    a("</tr></thead><tbody>")

    # ---- the model's own rows, once ----
    if sources:
        a(f"<tr class=subhdr><td class=lab>From the workbook</td><td colspan={len(periods)}></td></tr>")
        for sr in sources:
            short, full = _prov(sr)
            a(f"<tr class=src><td class=lab><div class=rl title='{e(sr.label)}'>{e(sr.label)}</div>"
              f"<div class=prov title='{e(full)}'>{e(short)}</div></td>")
            by_p = {c.period: c for c in sr.cells}
            for p in periods:
                c = by_p.get(p)
                cls = " inp" if (c and c.is_input) else ""
                tip = f" title='{e(c.ref)}'" if (c and c.ref) else ""
                a(f"<td class='v{cls}'{tip}>{_cell_text(c.value if c else None, sr.is_percent)}</td>")
            a("</tr>")

    # ---- calculations built on those rows ----
    a(f"<tr class=subhdr><td class=lab>Calculations</td><td colspan={len(periods)}></td></tr>")
    seen_flags: set = set()          # a finding can flag several calcs — show it once
    any_clean_shown = False
    for t in tables:
        dr = t.derived_row
        a("<tr class=der><td class=lab>"
          f"<div class=rl title='{e(t.title)}'>{e(t.title)}</div>"
          f"<div class=cf title='{e(t.computation)}'>{e(t.computation)}</div></td>")
        by_p = {c.period: c for c in dr.cells} if dr else {}
        for p in periods:
            c = by_p.get(p)
            if c is None or c.value is None:
                a("<td>·</td>")
                continue
            txt = _cell_text(c.value, dr.is_percent)
            if c.highlighted and c.comment:
                sev = (c.severity.value if c.severity else "medium")
                a(f"<td class='hl s-{sev}'>{txt}<span class=tip>{e(c.comment)}</span></td>")
            else:
                a(f"<td>{txt}</td>")
        a("</tr>")
        # one-line flag, full width under its row — deduped within the family
        flag_text, flag_sev = _table_flag(t, findings_by_id)
        key = (flag_text, flag_sev)
        if flag_sev:
            if key in seen_flags:
                continue
            seen_flags.add(key)
            color = _SEV_COLOR_STR.get(flag_sev, "#7a5a13")
            chips = "<span class='chip ll'>LLM-directed</span>" if t.llm_directed else ""
            a(f"<tr class=flagrow><td colspan={ncol}><div class=wkflag style='border-left-color:{color}'>"
              f"<b style='color:{color}'>⚑ {flag_sev.upper()}</b> {e(flag_text)}{chips}</div></td></tr>")
        elif not any_clean_shown:
            any_clean_shown = True
            a(f"<tr class=flagrow><td colspan={ncol}><div class='wkflag clean' style='border-left-color:#1a7f4b'>"
              f"<b style='color:#1a7f4b'>✓</b> <span class=muted>{e(flag_text)}</span></div></td></tr>")
    a("</tbody></table></div>")
    return "\n".join(o)


def render_annotation_table(t, findings_by_id=None) -> str:
    o: list[str] = []
    a = o.append
    a("<div class=wk>")
    a("<div class=wk-h>")
    a(f"<div class=t>{e(t.title)}</div>")
    if t.rationale:
        a(f"<div class=why>{e(t.rationale)}</div>")
    a(f"<div class=comp>computed as <span class=fx>{e(t.computation)}</span></div>")
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
    """Shapley contribution bars + an interactive two-way grid, both driven live
    by JS that mirrors the server-side closed form. Ranges are editable."""
    o: list[str] = []
    a = o.append
    tid = f"torn{idx}"
    a(f"<div class=torn id={tid}>")
    a(f"<h3>{e(t.output_label)}</h3>")
    a(f"<div class=out>{e(t.formula_note)} — base <b class=base>{_cell_text(t.output_base, False)}</b> "
      f"<span class=muted>{e(t.output_unit)}</span></div>")
    a(f"<div class=duo><span class=dn>▼ all-adverse: <b class=dnv>{_cell_text(t.downside, False)}</b></span>"
      f"<span class=up>▲ all-favorable: <b class=upv>{_cell_text(t.upside, False)}</b></span></div>")

    # Two-sided tornado bars: red = output at the adverse end, green = favorable
    a("<div class=subh>How far each input moves the output "
      "<span class=muted style='font-weight:400'>(▼ adverse · ▲ favorable, sorted by swing)</span></div>")
    a("<div class=shapbars>")
    for d in t.drivers:
        a(f"<div class=shap data-key='{e(d.key)}'>"
          f"<div class=nm title='model cell {e(', '.join(d.input_refs))}'>{e(d.label)}</div>"
          f"<div class=tk><div class=zero></div><div class='bar dn'></div><div class='bar up'></div></div>"
          f"<div class=vv></div></div>")
    a("</div>")

    # Two-way grid
    a("<div class=twoway><div class=subh>Two-way sensitivity — pick any two inputs</div>")
    opts = "".join(f"<option value='{e(d.key)}'>{e(d.label)}</option>" for d in t.drivers)
    keyA = t.drivers[0].key if t.drivers else ""
    keyB = t.drivers[1].key if len(t.drivers) > 1 else keyA
    a(f"<div class=sel>rows: <select class=selA>{opts}</select> "
      f"columns: <select class=selB>{opts}</select> "
      f"<span class=muted>green = higher {e(t.output_label.lower())}, red = lower</span></div>")
    a("<div class=gridbox></div></div>")

    # editable ranges (drives both views)
    a("<div class=ranges><details><summary>Edit input ranges (defaults: ±20% / benchmark)</summary>"
      "<table><tr><th style='text-align:left'>input</th><th>cell</th>"
      "<th>base</th><th>low</th><th>high</th></tr>")
    for d in t.drivers:
        a(f"<tr data-key='{e(d.key)}'>"
          f"<td style='text-align:left'>{e(d.label)}</td>"
          f"<td class=rcite>{e(', '.join(d.input_refs))}</td>"
          f"<td style='text-align:right'>{_cell_text(d.base, d.unit=='fraction')}</td>"
          f"<td><input type=number class=lo step=any value='{round(d.low,6)}'></td>"
          f"<td><input type=number class=hi step=any value='{round(d.high,6)}'></td></tr>")
    a("</table><button class=reset>Reset ranges</button></details></div>")

    spec = {
        "formula": t.formula, "horizon": t.horizon, "unit": t.output_unit,
        "drivers": [{"key": d.key, "label": d.label, "base": d.base, "low": d.low, "high": d.high,
                     "coef": d.coef,
                     "adverse": ("high" if (t.formula == "valuation_pv" and d.key == "rate")
                                 or (t.formula == "linear_sum" and d.coef < 0) else "low")}
                    for d in t.drivers],
        "defaults": {d.key: {"low": d.low, "high": d.high} for d in t.drivers},
        "selA": keyA, "selB": keyB,
    }
    a(f"<script type=application/json class=spec>{json.dumps(spec)}</script>")
    a("</div>")
    return "\n".join(o)


TORNADO_JS = r"""
function maFmt(v){const a=Math.abs(v);if(a>=1e9)return (v/1e9).toFixed(1)+'B';if(a>=1e6)return (v/1e6).toFixed(1)+'M';if(a>=1000)return v.toLocaleString(undefined,{maximumFractionDigits:0});if(a>=1)return v.toFixed(2).replace(/\.?0+$/,'');return v.toFixed(3);}
function maGet(spec,vals,k){if(k in vals)return vals[k];const d=spec.drivers.find(x=>x.key===k);return d?d.base:0;}
function maEval(spec,vals){
  if(spec.formula==='valuation_pv'){
    const rate=spec.drivers.find(d=>d.key==='rate')?maGet(spec,vals,'rate'):0;
    return maGet(spec,vals,'multiple')*maGet(spec,vals,'metric')/Math.pow(1+rate,spec.horizon||0);
  }
  let s=0;spec.drivers.forEach(d=>{s+=d.coef*maGet(spec,vals,d.key);});return s;
}
function maRanges(torn){const cur={};torn.querySelectorAll('.ranges tr[data-key]').forEach(r=>{cur[r.dataset.key]={lo:parseFloat(r.querySelector('.lo').value),hi:parseFloat(r.querySelector('.hi').value)};});return cur;}
function maAdv(d,cur){return d.adverse==='high'?cur[d.key].hi:cur[d.key].lo;}
function maFav(d,cur){return d.adverse==='high'?cur[d.key].lo:cur[d.key].hi;}
function maShapley(spec,cur){
  // linear: contribution = coef*(adverse-base), exact. nonlinear & small: exact Shapley over subsets.
  const ds=spec.drivers;
  if(spec.formula==='linear_sum'||ds.length>12){
    const out={};ds.forEach(d=>{out[d.key]=d.coef*(maAdv(d,cur)-d.base);});return out;
  }
  const base={},adv={};ds.forEach(d=>{base[d.key]=d.base;adv[d.key]=maAdv(d,cur);});
  const fact=n=>{let f=1;for(let i=2;i<=n;i++)f*=i;return f;};
  const cache={};const f=(S)=>{const key=[...S].sort().join(',');if(key in cache)return cache[key];const ov=Object.assign({},base);S.forEach(k=>ov[k]=adv[k]);const v=maEval(spec,ov);cache[key]=v;return v;};
  const n=ds.length,phi={};const keys=ds.map(d=>d.key);
  keys.forEach(k=>{phi[k]=0;const rest=keys.filter(x=>x!==k);
    const subs=(arr)=>{const res=[[]];for(const x of arr){const cp=res.map(s=>s.concat(x));res.push(...cp);}return res;};
    subs(rest).forEach(S=>{const w=fact(S.length)*fact(n-S.length-1)/fact(n);phi[k]+=w*(f(S.concat(k))-f(S));});});
  return phi;
}
function maDownUp(spec,cur){const dn={},up={};spec.drivers.forEach(d=>{dn[d.key]=maAdv(d,cur);up[d.key]=maFav(d,cur);});return {dn:maEval(spec,dn),up:maEval(spec,up)};}
function maColor(t){ // 0=red .. 1=green
  const r=t<0.5?210:Math.round(210-(t-0.5)*2*180), g=t<0.5?Math.round(60+t*2*130):190, b=70;
  return `rgb(${r},${g},${b})`;
}
function maRender(torn){
  const spec=JSON.parse(torn.querySelector('script.spec').textContent);
  const cur=maRanges(torn);
  const base=maEval(spec,{});
  torn.querySelector('.base').textContent=maFmt(base);
  const du=maDownUp(spec,cur);
  torn.querySelector('.dnv').textContent=maFmt(du.dn);
  torn.querySelector('.upv').textContent=maFmt(du.up);
  // two-sided tornado bars: each input's output at its adverse end (red, left
  // of base) and favorable end (green, right of base), centered on the base.
  const eff=spec.drivers.map(d=>{
    const lo=maEval(spec,{[d.key]:cur[d.key].lo});
    const hi=maEval(spec,{[d.key]:cur[d.key].hi});
    const adv=d.adverse==='high'?hi:lo, fav=d.adverse==='high'?lo:hi;
    return {key:d.key,adv,fav,swing:Math.abs(hi-lo)};
  });
  const maxd=Math.max(...eff.map(e=>Math.max(Math.abs(e.adv-base),Math.abs(e.fav-base))),1e-9);
  const box=torn.querySelector('.shapbars');
  eff.sort((a,b)=>b.swing-a.swing);
  eff.forEach(e=>{const row=box.querySelector(`.shap[data-key="${CSS.escape(e.key)}"]`);box.appendChild(row);
    const dn=row.querySelector('.bar.dn'), up=row.querySelector('.bar.up');
    const dpct=Math.abs(e.adv-base)/maxd*50, upct=Math.abs(e.fav-base)/maxd*50;
    dn.style.right='50%';dn.style.width=dpct+'%';
    up.style.left='50%';up.style.width=upct+'%';
    row.querySelector('.vv').textContent='▼ '+maFmt(e.adv)+'  ▲ '+maFmt(e.fav);});
  // two-way grid
  const A=spec.drivers.find(d=>d.key===torn.querySelector('.selA').value)||spec.drivers[0];
  const B=spec.drivers.find(d=>d.key===torn.querySelector('.selB').value)||spec.drivers[0];
  const N=5;const av=[],bv=[];
  for(let i=0;i<N;i++){av.push(cur[A.key].lo+(cur[A.key].hi-cur[A.key].lo)*i/(N-1));bv.push(cur[B.key].lo+(cur[B.key].hi-cur[B.key].lo)*i/(N-1));}
  let cells=[],mn=Infinity,mx=-Infinity;
  for(let i=0;i<N;i++){cells.push([]);for(let j=0;j<N;j++){const ov={};ov[A.key]=av[i];ov[B.key]=bv[j];const v=maEval(spec,ov);cells[i].push(v);if(v<mn)mn=v;if(v>mx)mx=v;}}
  const fp=(d,x)=>(d.unit==='fraction'?(x*100).toFixed(0)+'%':maFmt(x));
  let h='<div class=cap>columns: '+B.label+' · rows: '+A.label+'</div><table class=grid2><tr><th></th>';
  for(let j=0;j<N;j++)h+='<th>'+fp(B,bv[j])+'</th>';h+='</tr>';
  for(let i=0;i<N;i++){h+='<tr><td class=axl>'+fp(A,av[i])+'</td>';for(let j=0;j<N;j++){const v=cells[i][j];const tnorm=mx>mn?(v-mn)/(mx-mn):0.5;h+='<td style="background:'+maColor(tnorm)+';color:#fff">'+maFmt(v)+'</td>';}h+='</tr>';}
  h+='</table>';
  torn.querySelector('.gridbox').innerHTML=h;
}
document.querySelectorAll('.torn').forEach(t=>{
  t.addEventListener('input',()=>maRender(t));
  t.addEventListener('change',()=>maRender(t));
  t.querySelector('.reset')?.addEventListener('click',()=>{
    const spec=JSON.parse(t.querySelector('script.spec').textContent);
    t.querySelectorAll('.ranges tr[data-key]').forEach(r=>{const d=spec.defaults[r.dataset.key];r.querySelector('.lo').value=d.low;r.querySelector('.hi').value=d.high;});
    maRender(t);
  });
  const spec=JSON.parse(t.querySelector('script.spec').textContent);
  t.querySelector('.selA').value=spec.selA;t.querySelector('.selB').value=spec.selB;
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

    # analyst worksheet — each calculation with its source rows AND its own flag line,
    # grouped into collapsible family sections
    findings_by_id = {f.id: f for f in report.findings}
    tables = report.annotation_tables
    if tables:
        a("<h2>Worksheet <span class=muted style='font-weight:400;font-size:14px'>"
          "— each calculation with the model's own rows, and what it flags</span></h2>")
        a("<p class=legend>Each calculation shows the company's <b style='color:#5f574b'>model rows</b> "
          "(hover any value for its exact <code>Sheet!Cell</code>) and the <b>NEW</b> derived row, then a "
          "one-line flag. Click a section to expand it.</p>")
        # group preserving family order
        fams: list[str] = []
        by_fam: dict[str, list] = {}
        for t in tables:
            if t.family not in by_fam:
                by_fam[t.family] = []
                fams.append(t.family)
            by_fam[t.family].append(t)
        for fam in fams:
            group = by_fam[fam]
            uniq: dict[tuple, str] = {}        # unique (text, sev) -> sev; matches the deduped display
            for t in group:
                ft, fs = _table_flag(t, findings_by_id)
                if fs:
                    uniq[(ft, fs)] = fs
            worst = min((_SEV_RANK.get(s, 9) for s in uniq.values()), default=9)
            n_flag = len(uniq)
            # badge colored by the worst flag in the family; all collapsed by
            # default so the report is a scannable list you click to expand
            if n_flag:
                bcolor = _SEV_COLOR_STR["high"] if worst <= 1 else _SEV_COLOR_STR["medium"]
                badge = f"<span class=fambadge style='background:{bcolor}'>{n_flag} flag{'s' if n_flag != 1 else ''}</span>"
            else:
                badge = "<span class=fambadge style='background:#1a7f4b'>clean</span>"
            a("<details class=famsec>")
            a(f"<summary><span class=famname>{e(fam)}</span> "
              f"<span class=muted>· {len(group)} calc{'s' if len(group) != 1 else ''}</span> {badge}</summary>")
            a(render_family(fam, group, findings_by_id))
            a("</details>")

    # sensitivity — Shapley contribution bars + two-way grid, off the model's own inputs
    if report.sensitivities:
        a("<h2>Sensitivity <span class=muted style='font-weight:400;font-size:14px'>"
          "— how the outputs move when you change the model's inputs</span></h2>")
        a("<p class=legend>Each input below is one of the model's <b>own cells</b> (hover a bar for its "
          "<code>Sheet!Cell</code>). For each output there are two views: the <b>tornado</b> shows how far "
          "that output moves when each input swings across its range — <span style='color:#c4543a'>red = the "
          "adverse end</span>, <span style='color:#5a9e74'>green = the favorable end</span> (so you see both "
          "directions, not just downside); the <b>two-way grid</b> lets you move <i>two</i> inputs at once and "
          "reads the output as a red→green heatmap. Edit any input's range and both views re-run live.</p>")
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
# HTTP handler + job registry (async progress)
# ---------------------------------------------------------------------------

OUT_ROOT = Path(tempfile.gettempdir()) / "model_annotator_web"
import threading
import uuid

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _run_job(job_id: str, tmp_path: str, filename: str, use_llm: bool):
    def prog(label: str, frac: float):
        with _JOBS_LOCK:
            j = _JOBS.get(job_id)
            if j is not None:
                j["phase"], j["frac"] = label, frac
    try:
        report = annotate(tmp_path, out_dir=str(OUT_ROOT / Path(filename).stem),
                          no_llm=not use_llm, write_outputs=True, progress=prog)
        html = render_report(report, filename)
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", frac=1.0, phase="Done", html=html)
    except Exception as exc:
        log.error("annotation failed: %s", exc)
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="error", error=f"{exc}")
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: str, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(render_index())
        elif u.path == "/health":
            self._send("ok")
        elif u.path == "/progress":
            job = parse_qs(u.query).get("job", [""])[0]
            with _JOBS_LOCK:
                j = _JOBS.get(job)
                payload = ({"status": "error", "error": "unknown job"} if j is None
                           else {"status": j["status"], "phase": j.get("phase", ""),
                                 "frac": j.get("frac", 0.0), "error": j.get("error", "")})
            self._send(json.dumps(payload), ctype="application/json")
        elif u.path == "/result":
            job = parse_qs(u.query).get("job", [""])[0]
            with _JOBS_LOCK:
                j = _JOBS.get(job)
                html = j.get("html") if j else None
            if html:
                self._send(html)
            else:
                self._send(render_index("<div class=err>Result not ready or expired.</div>"), 404)
        else:
            self._send("<div class=wrap>Not found. <a href=/>Home</a></div>", 404)

    def do_POST(self):
        if self.path != "/start":
            self._send("Not found", 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        fields = parse_multipart(body, self.headers.get("Content-Type", ""))
        if "model" not in fields or not fields["model"][0]:
            self._send(json.dumps({"error": "no file"}), 400, "application/json")
            return
        filename, data = fields["model"]
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            self._send(json.dumps({"error": "not an .xlsx/.xlsm file"}), 400, "application/json")
            return
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False, dir=OUT_ROOT)
        tmp.write(data)
        tmp.close()
        use_llm = USE_LLM and "llm" in fields
        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "phase": "Starting…", "frac": 0.0,
                             "html": None, "error": ""}
        threading.Thread(target=_run_job, args=(job_id, tmp.name, filename, use_llm),
                         daemon=True).start()
        self._send(json.dumps({"job": job_id}), ctype="application/json")


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
