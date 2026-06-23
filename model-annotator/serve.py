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
import os
import re
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_annotator import annotate
from model_annotator.schema import Report, Severity, TieOutStatus

log = logging.getLogger("model_annotator.serve")


_DOTENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "REFIANT_API_KEY", "REFIANT_BASE_URL", "REFIANT_MODEL")


def _load_dotenv() -> Path | None:
    """Populate the keys we use (Anthropic + Refiant) from the nearest .env that
    carries any of them — checking this dir, then each parent. Dependency-free;
    shell-set values win (setdefault). Returns the file used (if any)."""
    import os
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
            if k in _DOTENV_KEYS and v:
                os.environ.setdefault(k, v)
                found = True
        if found:
            return env       # this .env had usable keys; stop here
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
.cf{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;color:#8a7f6f;font-weight:400;margin-top:3px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;cursor:help}
.measures{font-size:11px;color:#7d756a;font-style:italic;margin-top:3px;line-height:1.4;
  white-space:normal;max-width:300px}
table.grid th.num,table.grid td.num{text-align:right;color:#b0a489;font-size:10.5px;width:24px;
  padding:5px 6px 5px 4px;font-variant-numeric:tabular-nums;position:sticky;left:0;background:var(--panel)}
table.grid thead th.num{background:#efe9dd;z-index:2}
table.grid td.lab{left:24px}     /* label column sits right of the # column */
table.grid th.lab{left:24px}
.scalarv{color:var(--ink);font-weight:700}
.hzn{color:var(--muted);font-weight:400;font-size:11px;margin-left:8px}
tr.src td{color:#5f574b}
tr.src td.lab{color:#3a3128;font-weight:500}
tr.der td{font-weight:600;color:var(--ink);border-top:1px solid #ece4d3}
tr.der td.lab{color:var(--accent)}
tr.der td.lab.flagged .rl{color:#b11226}   /* a calc with a standout cell — red title */
td.na{}                                     /* n/a here: intentionally blank, no dot */
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
.torn .duo{display:flex;gap:26px;align-items:baseline;justify-content:center;color:var(--muted);font-size:13px;margin:4px 0 4px}
.torn .duo .dn{color:#b11226}
.torn .duo .up{color:#1a7f4b}
.torn .duo .basew{color:#3a3128;font-weight:600}
.torn .duo .base{font-size:15px}
.torn .fnote{text-align:center;color:var(--muted);font-size:12px;margin-bottom:14px}
.subh{font-family:Georgia,serif;font-size:14px;font-weight:600;margin:14px 0 8px;color:#3a3128}
/* output / year selector (recompute mode) */
.osel{font-size:13px;color:#3a3128;margin:2px 0 12px}
.osel select{font-family:inherit;font-size:13px;border:1px solid var(--line);border-radius:6px;padding:3px 7px;background:#fff}
/* single magnitude bar per input: length = how far it moves the selected output */
.shap{display:grid;grid-template-columns:200px 1fr 150px;gap:10px;align-items:center;margin:5px 0;font-size:12.5px}
.shap.hascube{grid-template-columns:190px 1fr 124px 98px}   /* extra column for the by-year sparkline */
.shap .nm{text-align:right;color:#3a3128;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.shap .tk{position:relative;height:16px;background:#f4efe4;border-radius:4px}
.shap .bar1{position:absolute;left:0;top:2px;height:12px;background:var(--accent);border-radius:3px;min-width:2px;transition:width .15s}
.shap .vv{color:var(--muted);font-variant-numeric:tabular-nums;text-align:left;font-size:11.5px}
.shap .spark{height:24px}
.shap .spark svg{display:block}
.shap.child{display:none}
.shap.child.show{display:grid}
.shap.child .nm{color:#6b6256;font-style:italic}
.bexp,.rexp{cursor:pointer;display:inline-block;color:var(--accent);font-size:10px;width:10px;transition:transform .12s}
.shap.agg.expanded .bexp,.rtbl tr.agg.expanded .rexp{transform:rotate(90deg)}
.rtbl tr.child{display:none}
.rtbl tr.child.show{display:table-row}
.rtbl tr.child .rl{padding-left:16px;color:#6b6256}
.rtbl tr.agg .rl{cursor:pointer}
.rtbl tr.agg.expanded input.lo,.rtbl tr.agg.expanded input.hi{opacity:.35;pointer-events:none}
/* two-way grid */
.twoway{margin-top:6px}
.twoway .sel{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:12.5px;color:var(--muted)}
.twoway select{font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:6px;padding:3px 6px;background:#fff;max-width:240px}
.grid2{border-collapse:collapse;font-size:11px}
.grid2 td,.grid2 th{border:1px solid #fff;padding:5px 7px;text-align:center;font-variant-numeric:tabular-nums;min-width:52px}
.grid2 th{background:#efe9dd;color:var(--muted);font-weight:600}
.grid2 .axl{background:#efe9dd;color:var(--muted);font-weight:600;white-space:nowrap}
.grid2 .cap{font-size:11px;color:var(--muted);margin:6px 0 2px}
.flatmsg{font-size:12.5px;color:#5f574b;background:#f6f1e8;border:1px dashed #d8cfbe;border-radius:8px;padding:14px 16px;line-height:1.5}
.flatmsg b{color:var(--ink)}
.ranges{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.ranges table{width:100%;font-size:12.5px}
.ranges input[type=number]{width:82px;font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:5px;padding:2px 5px;text-align:right}
.ranges .rcite{font-family:"SF Mono",Menlo,monospace;font-size:10.5px;color:#8a7f6f}
.rhint{font-size:12px;color:#5f574b;margin:8px 0 12px;line-height:1.5}
.rhint b{color:var(--ink)}
.rtbl{width:100%;border-collapse:collapse;font-size:12.5px}
.rtbl th,.rtbl td{padding:6px 8px;border-bottom:1px solid #f0ece2;vertical-align:middle}
.rtbl th{color:var(--muted);font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;text-align:right;white-space:nowrap}
.rtbl th.l,.rtbl td.l{text-align:left}
.rtbl th.oimph{text-align:center;color:#3a3128;background:#f6f1e8;border-bottom:2px solid #e4dcc9}
.rtbl td.rbase{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.rtbl .rl{font-weight:500;color:#3a3128;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.oimp{text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums;background:#fcfaf5}
.oimp .iv{font-weight:700}
.oimp .iv.dn{color:#b11226}
.oimp .iv.up{color:#1a7f4b}
.oimp .ivb{font-weight:600;color:#3a3128}
.oimp .iar{color:#c0b59c;margin:0 6px}
.oimp .idl{font-size:10.5px;color:var(--muted);margin-top:2px}
.oimp .nochg{color:#c5bca8;font-size:11px}
.ranges button{background:transparent;border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12.5px;margin-top:8px}
.ranges details summary{cursor:pointer;list-style:none;margin:2px 0 4px}
.ranges details summary::-webkit-details-marker{display:none}
.ranges .rsum-btn{display:inline-flex;align-items:center;gap:7px;background:#f3ede1;border:1px solid var(--accent);
  color:var(--accent);border-radius:8px;padding:7px 14px;font-size:13.5px;font-weight:600}
.ranges details[open] .rsum-btn{background:var(--accent);color:#fff}
.ranges .rsum-btn:hover{background:var(--accent);color:#fff}
.ranges .rsum-chev{display:inline-block;transition:transform .15s;font-size:11px}
.ranges details[open] .rsum-chev{transform:rotate(90deg)}
.ranges .rsum-sub{display:block;color:var(--muted);font-size:12px;margin-top:5px}
/* progress bar */
.prog{display:none;margin-top:22px}
.prog .bartrack{height:12px;background:#e7e0d2;border-radius:8px;overflow:hidden}
.prog .barfill{height:100%;width:0;background:linear-gradient(90deg,#c98a5e,#9c4221);border-radius:8px;transition:width .35s ease}
.prog .plabel{margin-top:10px;color:#3a3128;font-size:14px}
.prog .ppct{color:var(--muted);font-variant-numeric:tabular-nums}
/* landing page */
.land{max-width:680px;margin:0 auto;padding-top:30px}
.hero{text-align:center;margin-bottom:26px}
.hero h1{font-size:33px;line-height:1.1;letter-spacing:-.02em;margin:0 0 10px}
.vbadge{display:inline-block;font-family:-apple-system,sans-serif;font-size:12px;font-weight:700;color:#fff;
  background:linear-gradient(135deg,#c98a5e,#9c4221);border-radius:999px;padding:3px 10px;vertical-align:middle;
  margin-left:10px;letter-spacing:.04em;box-shadow:0 1px 3px rgba(156,66,33,.3)}
.hero .sub{max-width:600px;margin:0 auto;color:var(--muted);font-size:15px;line-height:1.6}
.feats{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;justify-content:center}
.feat{font-size:12px;color:#6b6253;background:#efe9dd;border:1px solid var(--line);border-radius:999px;padding:5px 13px}
.drop{display:block;border:2px dashed #cbbfa8;border-radius:18px;background:linear-gradient(180deg,#fffdf9,#fbf7ef);
  padding:52px 24px;text-align:center;cursor:pointer;transition:.15s;box-shadow:0 1px 2px rgba(60,49,40,.04)}
.drop.hover{border-color:var(--accent);background:#fbf3ea;box-shadow:0 4px 18px rgba(156,66,33,.10)}
.dropicon{width:50px;height:50px;margin:0 auto 14px;border-radius:50%;background:#efe7d8;display:flex;
  align-items:center;justify-content:center;color:#a8895f;font-size:23px}
.dropt{font-size:19px;font-family:Georgia,serif;color:var(--ink)}
.actions{display:flex;flex-direction:column;align-items:center;gap:10px;margin-top:22px}
.btn.big{padding:13px 30px;font-size:15.5px;border-radius:10px;box-shadow:0 2px 8px rgba(156,66,33,.18)}
.btn.big:hover{background:#883a1d}
.provsel{font-size:13px;color:#5f574b}
.provsel select{font-family:inherit;font-size:13px;border:1px solid var(--line);border-radius:7px;padding:5px 9px;background:#fff;margin-left:6px}
.poweredby{font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
.poweredby b{color:#5f574b}
.poweredby .dot{color:#c8bfae;margin:0 6px}
"""

INDEX_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>VoLo Financial Analysis Tool</title><style>{css}</style></head><body><div class="wrap land">
<div class=hero>
  <h1>VoLo Financial Analysis Tool<span class=vbadge>V3</span></h1>
  <p class=sub>Drop a startup's Excel model and get the diligence a senior VC analyst does by hand — tie-outs, derived metrics, sensitivity, and the questions to ask management, with every number traced back to the cell it came from.</p>
  <div class=feats>
    <span class=feat>Arithmetic tie-outs</span>
    <span class=feat>Derived analyst metrics</span>
    <span class=feat>Sensitivity &amp; tornado</span>
    <span class=feat>Flags + questions to ask</span>
  </div>
</div>
<form id=f method=post action=/annotate enctype=multipart/form-data>
  <label class=drop id=drop>
    <input type=file name=model id=file accept=".xlsx,.xlsm" required>
    <div class=dropicon>⬆</div>
    <div class=dropt>Drop an .xlsx / .xlsm model here</div>
    <div class=muted id=fname style="margin-top:6px">or click to choose a file</div>
  </label>
  <div class=actions>
    <div class=provsel>Model provider:
      <select name=provider id=prov>
        <option value=anthropic selected>Anthropic (Claude)</option>
        <option value=refiant>Refiant</option>
      </select>
    </div>
    <button class="btn big" type=submit id=go>Analyze model →</button>
    <div class=poweredby>Reads structure &amp; writes the summary with the selected model<span class=dot>·</span>every figure still comes from your workbook</div>
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
const go=document.getElementById('go');
const fill=document.getElementById('barfill'),plabel=document.getElementById('plabel'),ppct=document.getElementById('ppct');
function setProg(frac,label){{fill.style.width=Math.round(frac*100)+'%';ppct.textContent=Math.round(frac*100)+'%';if(label)plabel.textContent=label;}}
function fail(msg){{ plabel.textContent=msg; plabel.style.color='#b11226'; go.disabled=false; }}
document.getElementById('f').addEventListener('submit',async (e)=>{{
  e.preventDefault();
  if(!file.files[0]){{ fail('Choose an .xlsx / .xlsm file first.'); return; }}
  go.disabled=true; plabel.style.color='';
  document.getElementById('prog').style.display='block';
  setProg(0.02,'Uploading…');
  const fd=new FormData(); fd.append('model',file.files[0]); fd.append('llm','on');
  fd.append('provider', (document.getElementById('prov')||{{}}).value || 'anthropic');
  let job;
  try{{
    const r=await fetch('/start',{{method:'POST',body:fd}});
    let data={{}}; try{{ data=await r.json(); }}catch(_){{}}
    if(!r.ok || data.error || !data.job){{ fail('Could not start: '+(data.error||('server error '+r.status))); return; }}
    job=data.job;
  }} catch(err){{ fail('Upload failed (is the server running?): '+err); return; }}
  let misses=0;
  const poll=setInterval(async ()=>{{
    let s; try{{ s=await (await fetch('/progress?job='+job)).json(); }}catch(e){{ if(++misses>8){{clearInterval(poll);fail('Lost contact with the server. Click Analyze to retry.');}} return; }}
    setProg(s.frac||0, s.phase||'Working…');
    if(s.status==='done'){{ clearInterval(poll); setProg(1,'Done'); window.location.href='/result?job='+job; }}
    else if(s.status==='error'){{ clearInterval(poll);
      fail(s.error==='unknown job'
        ? 'That run was lost — the server was restarted. Click Analyze model to run it again.'
        : 'Error: '+(s.error||'unknown')); }}
  }}, 400);
}});
</script>
</div></body></html>"""


def render_index(recent_html: str = "") -> str:
    return INDEX_HTML.format(css=PAGE_CSS, recent=recent_html)


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
    """One-sentence flag for a worksheet calculation: (text, severity|None).

    Priority is built around the user's rule that a calc's flag must be about
    THAT calc: a high/critical standout in the calc's own row wins first (it is
    intrinsically about this number's behaviour), then a related finding, then a
    medium own standout. Structural/units findings never reach here (filtered in
    tables.py), so a flag is always either the calc's own story or a real,
    calc-specific finding — never the tool's unit confusion."""
    own = []
    if t.derived_row:
        for c in t.derived_row.cells:
            if c.highlighted and c.comment:
                sev = c.severity.value if c.severity else "medium"
                own.append((sev, c.comment))
        own.sort(key=lambda x: _SEV_RANK.get(x[0], 9))

    def own_flag():
        sev, comment = own[0]
        extra = f" (+{len(own) - 1} more period{'s' if len(own) > 2 else ''})" if len(own) > 1 else ""
        return (comment + extra, sev)

    # 1) the calc's own high/critical standout — most specific to this number
    if own and _SEV_RANK.get(own[0][0], 9) <= 1:
        return own_flag()
    # 2) a related, calc-specific finding (has a management question)
    related = [findings_by_id[i] for i in t.related_finding_ids if i in findings_by_id]
    related.sort(key=lambda f: _SEV_RANK.get(f.severity.value, 9))
    if related:
        f = related[0]
        q = f" — {f.management_question}" if f.management_question else ""
        return (f"{f.title}{q}", f.severity.value)
    # 3) a medium own standout
    if own:
        return own_flag()
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


def _renumber_comp(comp: str, idmap: dict) -> str:
    """Rewrite a computation so its canonical inputs read as the numbered rows in
    this section, e.g. 'gross_profit[t] / revenue_total[t]'  ->  '(3) / (1)'."""
    out = comp
    for cid in sorted(idmap, key=len, reverse=True):
        out = re.sub(r"\b" + re.escape(cid) + r"\b", f"({idmap[cid]})", out)
    out = out.replace("[t-1]", "ₜ₋₁").replace("[t]", "")  # tidy period subscripts
    return out


def render_family(fam: str, tables: list, findings_by_id=None) -> str:
    """One section per family: the model's own cited rows ONCE at the top, then
    every calculation built on them. Every row is numbered, so a calculation can
    name the rows it uses (e.g. '(3) / (1)'). Source provenance is on a hover."""
    findings_by_id = findings_by_id or {}
    o: list[str] = []
    a = o.append
    periods = tables[0].periods if tables else []

    # dedupe source rows across all calcs in this family, keep workbook order
    seen: dict[tuple, object] = {}
    for t in tables:
        for sr in t.source_rows:
            key = (sr.sheet, sr.row_index, sr.label)
            if key not in seen:
                seen[key] = sr
    sources = sorted(seen.values(), key=lambda s: (s.sheet or "", s.row_index or 0))

    # number every row (sources first, then calcs) and map canonical id -> number
    idmap: dict[str, int] = {}
    src_no: dict[int, int] = {}
    der_no: dict[int, int] = {}
    n = 0
    for sr in sources:
        n += 1
        src_no[id(sr)] = n
        if sr.canonical_id:
            idmap.setdefault(sr.canonical_id, n)
    for t in tables:
        n += 1
        der_no[id(t)] = n
        if t.metric_id:
            idmap.setdefault(t.metric_id, n)

    a("<div class=scroll><table class=grid><thead><tr>")
    a("<th class=num>#</th><th class=lab>row</th>")
    for p in periods:
        a(f"<th>{e(p)}</th>")
    a("</tr></thead><tbody>")
    sub = f"<td class=num></td><td class=lab>{{}}</td><td colspan={len(periods)}></td>"

    # ---- the model's own rows, once ----
    if sources:
        a(f"<tr class=subhdr>{sub.format('From the workbook')}</tr>")
        for sr in sources:
            short, full = _prov(sr)
            a(f"<tr class=src><td class=num>{src_no[id(sr)]}</td>"
              f"<td class=lab><div class=rl title='{e(sr.label)}'>{e(sr.label)}</div>"
              f"<div class=prov title='{e(full)}'>{e(short)}</div></td>")
            by_p = {c.period: c for c in sr.cells}
            for p in periods:
                c = by_p.get(p)
                cls = " inp" if (c and c.is_input) else ""
                tip = f" title='{e(c.ref)}'" if (c and c.ref) else ""
                a(f"<td class='v{cls}'{tip}>{_cell_text(c.value if c else None, sr.is_percent)}</td>")
            a("</tr>")

    # ---- calculations built on those rows ----
    # No flag rows or badges (they cluttered): a standout cell keeps its hover
    # comment, and a calc that has any standout gets a red title so it's scannable.
    a(f"<tr class=subhdr>{sub.format('Calculations')}</tr>")
    for t in tables:
        dr = t.derived_row
        by_p = {c.period: c for c in dr.cells} if dr else {}
        flagged = bool(dr) and any(c.highlighted and c.comment for c in dr.cells)
        labcls = "lab flagged" if flagged else "lab"
        comp = _renumber_comp(t.computation, idmap) if t.computation else ""
        measures = f"<div class=measures>{e(t.rationale)}</div>" if t.rationale else ""
        a(f"<tr class=der><td class=num>{der_no[id(t)]}</td><td class='{labcls}'>"
          f"<div class=rl title='{e(t.title)}'>{e(t.title)}</div>"
          f"<div class=cf title='{e(t.computation)}'>= {e(comp)}</div>{measures}</td>")
        # scalar metric (one figure for the whole horizon) -> a single spanning cell
        vals = [c for c in dr.cells if c.value is not None] if dr else []
        if len(vals) == 1 and len(periods) > 2:
            c = vals[0]
            txt = _cell_text(c.value, dr.is_percent)
            if c.highlighted and c.comment:
                sev = (c.severity.value if c.severity else "medium")
                a(f"<td class='hl s-{sev}' colspan={len(periods)} style='text-align:left'>{txt}"
                  f"<span class=hzn>· one figure over {e(periods[0])}–{e(periods[-1])}</span>"
                  f"<span class=tip>{e(c.comment)}</span></td>")
            else:
                a(f"<td class=scalarv colspan={len(periods)} style='text-align:left'>{txt}"
                  f"<span class=hzn>· one figure over {e(periods[0])}–{e(periods[-1])}</span></td>")
            a("</tr>")
            continue
        for p in periods:
            c = by_p.get(p)
            if c is None or c.value is None:
                a("<td class=na></td>")          # blank, not a dot — reads as 'n/a here', not broken
                continue
            txt = _cell_text(c.value, dr.is_percent)
            if c.highlighted and c.comment:
                sev = (c.severity.value if c.severity else "medium")
                a(f"<td class='hl s-{sev}'>{txt}<span class=tip>{e(c.comment)}</span></td>")
            else:
                a(f"<td>{txt}</td>")
        a("</tr>")
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
    """One bar per input (length = how far it moves the chosen output) + a
    two-way grid + an editable-ranges/impact table, all driven live by JS. In
    recompute (cube) mode the analyst picks which output and which year to view."""
    o: list[str] = []
    a = o.append
    cube = t.cube
    tid = f"torn{idx}"
    a(f"<div class=torn id={tid}>")
    title = "Sensitivity — how inputs move the outputs" if cube else e(t.output_label)
    a(f"<h3>{title}</h3>")

    if cube:
        oopts = "".join(f"<option value='{e(x['key'])}'{' selected' if x['key']==cube.get('defaultOutput') else ''}>"
                        f"{e(x['label'])}</option>" for x in cube["outputs"])
        popts = "".join(f"<option value='{e(p)}'{' selected' if p==cube.get('defaultPeriod') else ''}>{e(p)}</option>"
                        for p in cube["periods"])
        if cube["periods"]:
            popts += (f"<option value='__cum__'>Cumulative {e(cube['periods'][0])}–{e(cube['periods'][-1])}</option>")
        a(f"<div class=osel>Output: <select class=outsel>{oopts}</select> "
          f"&nbsp; Year: <select class=persel>{popts}</select></div>")

    drv = cube["drivers"] if cube else [{"key": d.key, "label": d.label, "refs": d.input_refs} for d in t.drivers]
    if cube:
        out_cols = 1
    else:
        out_names = ["Revenue", "EBITDA"] if t.formula == "linear_sum" else ["Valuation"]
        out_cols = len(out_names)

    # summary: worst | base | best, with base centered
    a("<div class=duo>"
      "<span class=dn>▼ worst-case <b class=dnv></b></span>"
      f"<span class=basew>base <b class=base>{_cell_text(t.output_base, False)}</b> "
      f"<span class=muted>{e(t.output_unit)}</span></span>"
      "<span class=up>▲ best-case <b class=upv></b></span></div>")
    a(f"<div class=fnote>{e(t.formula_note)}</div>")

    # analyst input ranges FIRST (collapsed by default) — these drive both charts
    a("<div class=ranges><details>"
      "<summary class=rsum><span class=rsum-btn><span class=rsum-chev>▸</span> ✎ Edit input ranges "
      "&amp; what's being tested</span>"
      "<span class=rsum-sub>set each input's low/high — drives the tornado &amp; two-way charts below</span>"
      "</summary>")
    a("<div class=rhint>Set each input's <b>low</b> and <b>high</b>; <b>base</b> is the model's own value. The "
      "output is shown <b>worst · base · best</b>, and the tornado &amp; two-way charts below re-run live as you "
      "edit. <span class=muted>Red = output falls, green = output rises.</span></div>")
    a("<table class=rtbl><thead><tr><th class=l>input (model cell)</th><th>low</th><th>base</th><th>high</th>")
    if cube:
        a("<th class=oimph><span class=outname3>output</span> <span class=muted>(worst · base · best)</span></th>")
    else:
        for nm in out_names:
            a(f"<th class=oimph>{e(nm)} <span class=muted>(worst · base · best)</span></th>")
    a("</tr></thead><tbody>")
    def _range_row(key, label, refs, base, lo, hi, is_frac, kind="", parent=""):
        trcls = f" class='{kind}'" if kind else ""
        pa = f" data-parent='{e(parent)}'" if parent else ""
        mark = "<span class=rexp>▸</span> " if kind == "agg" else ("└ " if kind == "child" else "")
        a(f"<tr data-key='{e(key)}'{pa}{trcls}>"
          f"<td class=l><div class=rl>{mark}{e(label)}</div><div class=rcite>{e(', '.join(refs))}</div></td>"
          f"<td><input type=number class=lo step=any value='{round(lo,6)}'></td>"
          f"<td class=rbase>{_cell_text(base, is_frac)}</td>"
          f"<td><input type=number class=hi step=any value='{round(hi,6)}'></td>")
        for _ in range(out_cols):
            a("<td class=oimp></td>")
        a("</tr>")

    for d in (cube["drivers"] if cube else None) or t.drivers:
        if cube:
            key, label, refs, base = d["key"], d["label"], d["refs"], d["value"]
            lo, hi, is_frac = base * 0.8, base * 1.2, False
            kids = d.get("children", [])
        else:
            key, label, refs, base = d.key, d.label, d.input_refs, d.base
            lo, hi, is_frac = d.low, d.high, d.unit == 'fraction'
            kids = []
        _range_row(key, label, refs, base, lo, hi, is_frac, kind=("agg" if kids else ""))
        for c in kids:
            _range_row(c["key"], c["label"], c.get("refs", []), c["value"],
                       c["value"] * 0.8, c["value"] * 1.2, False, kind="child", parent=key)
    a("</tbody></table><button class=reset>Reset ranges</button></details></div>")

    # single bar per input: length = swing of the selected output
    spark_hdr = (" <span class=muted style='font-weight:400'>· the mini line is that input's effect each year, "
                 "as % of that year's revenue (so every year is on its own scale)</span>" if cube else "")
    a("<div class=subh>How far each input moves the <span class=outname>output</span> "
      f"<span class=muted style='font-weight:400'>(sorted by impact)</span>{spark_hdr}</div>")
    def _bar(key, label, refs, kind="", parent=""):
        spark = "<div class=spark></div>" if cube else ""
        cls = "shap" + (" hascube" if cube else "") + (f" {kind}" if kind else "")
        pa = f" data-parent='{e(parent)}'" if parent else ""
        mark = "<span class=bexp>▸</span> " if kind == "agg" else ""
        a(f"<div class='{cls}' data-key='{e(key)}'{pa}>"
          f"<div class=nm title='model cell {e(', '.join(refs))}'>{mark}{e(label)}</div>"
          f"<div class=tk><div class=bar1></div></div>"
          f"<div class=vv></div>{spark}</div>")

    a("<div class=shapbars>")
    for d in drv:
        refs = d.get("refs") if cube else d["refs"]
        kids = d.get("children", []) if cube else []
        _bar(d["key"], d["label"], refs, kind=("agg" if kids else ""))
        for c in kids:
            _bar(c["key"], c["label"], c.get("refs", []), kind="child", parent=d["key"])
    a("</div>")

    # two-way grid
    a("<div class=twoway><div class=subh>Two-way sensitivity — pick any two inputs</div>")
    opts = "".join(f"<option value='{e(d['key'])}'>{e(d['label'])}</option>" for d in drv)
    keyA = drv[0]["key"] if drv else ""
    keyB = drv[1]["key"] if len(drv) > 1 else keyA
    a(f"<div class=sel>rows: <select class=selA>{opts}</select> "
      f"columns: <select class=selB>{opts}</select> "
      "<span class=muted>green = higher <span class=outname2>output</span>, red = lower</span></div>")
    a("<div class=gridbox></div></div>")

    if cube:
        spec = {"formula": "cube", "cube": cube, "unit": t.output_unit, "selA": keyA, "selB": keyB}
    else:
        def _adverse(d):
            if t.formula == "valuation_pv" and d.key == "rate":
                return "high"
            return "high" if (t.formula == "linear_sum" and d.coef < 0) else "low"
        spec = {
            "formula": t.formula, "horizon": t.horizon, "unit": t.output_unit,
            "offset": t.offset, "outNames": out_names, "revBase": t.out2_base,
            "drivers": [{"key": d.key, "label": d.label, "base": d.base, "low": d.low, "high": d.high,
                         "coef": d.coef, "adverse": _adverse(d),
                         "outLow": d.output_low, "outHigh": d.output_high,
                         "rev2Low": d.out2_low, "rev2High": d.out2_high} for d in t.drivers],
            "defaults": {d.key: {"low": d.low, "high": d.high} for d in t.drivers},
            "selA": keyA, "selB": keyB,
        }
    a(f"<script type=application/json class=spec>{json.dumps(spec)}</script>")
    a("</div>")
    return "\n".join(o)


TORNADO_JS = r"""
function maFmt(v){if(v==null||isNaN(v))return '–';const a=Math.abs(v);if(a>=1e9)return (v/1e9).toFixed(1)+'B';if(a>=1e6)return (v/1e6).toFixed(1)+'M';if(a>=1000)return v.toLocaleString(undefined,{maximumFractionDigits:0});if(a>=1)return v.toFixed(2).replace(/\.?0+$/,'');return v.toFixed(3);}
function maGet(spec,vals,k){if(k in vals)return vals[k];const d=spec.drivers.find(x=>x.key===k);return d?d.base:0;}
function maEval(spec,vals){
  if(spec.formula==='valuation_pv'){
    const rate=spec.drivers.find(d=>d.key==='rate')?maGet(spec,vals,'rate'):0;
    return maGet(spec,vals,'multiple')*maGet(spec,vals,'metric')/Math.pow(1+rate,spec.horizon||0);
  }
  if(spec.formula==='cube_view'){     // base + per-input slope*(value-base); slope from the recompute points
    let s=spec.base||0;
    spec.drivers.forEach(d=>{const sl=(d.outHigh-d.outLow)/((d.high-d.low)||1);s+=sl*(maGet(spec,vals,d.key)-d.base);});
    return s;
  }
  let s=spec.offset||0;spec.drivers.forEach(d=>{s+=d.coef*maGet(spec,vals,d.key);});return s;
}
// resolve the spec to a single-output linear view; for a cube, read the selected output+year
function maSpec(torn){
  const raw=JSON.parse(torn.querySelector('script.spec').textContent);
  if(raw.formula!=='cube') return raw;
  const cube=raw.cube;
  const out=(torn.querySelector('.outsel')||{}).value||cube.defaultOutput||cube.outputs[0].key;
  const per=(torn.querySelector('.persel')||{}).value||cube.defaultPeriod||cube.periods[cube.periods.length-1];
  const olabel=(cube.outputs.find(x=>x.key===out)||{}).label||out;
  const cum=per==='__cum__', PS=cube.periods;
  const base=cum?PS.reduce((s,p)=>s+(((cube.base[out]||{})[p])||0),0):((cube.base[out]||{})[per]);
  // active drivers: an expanded aggregate is replaced by its children (so the
  // group and its segments are never both counted), collapsed shows the aggregate
  const drivers=[];
  cube.drivers.forEach(d=>{
    const kids=d.children||[];
    const bar=torn.querySelector('.shap[data-key="'+CSS.escape(d.key)+'"]');
    const exp=kids.length&&bar&&bar.classList.contains('expanded');
    (exp?kids:[d]).forEach(dd=>{
      let lo,hi;
      if(cum){lo=0;hi=0;PS.forEach(p=>{const c=(dd.out[out]||{})[p];if(c){lo+=c.low;hi+=c.high;}});}
      else{const cell=((dd.out[out]||{})[per])||{low:base,high:base};lo=cell.low;hi=cell.high;}
      drivers.push({key:dd.key,label:dd.label,base:dd.value,low:dd.value*0.8,high:dd.value*1.2,outLow:lo,outHigh:hi});
    });
  });
  const plabel=cum?('Cumulative '+PS[0]+'–'+PS[PS.length-1]):per;
  return {formula:'cube_view',unit:raw.unit,base:base||0,outLabel:olabel,period:plabel,drivers:drivers,selA:raw.selA,selB:raw.selB,
          defaults:Object.fromEntries(drivers.map(d=>[d.key,{low:d.low,high:d.high}]))};
}
function maRanges(torn){const cur={};torn.querySelectorAll('.ranges tr[data-key]').forEach(r=>{cur[r.dataset.key]={lo:parseFloat(r.querySelector('.lo').value),hi:parseFloat(r.querySelector('.hi').value)};});return cur;}
function maOutFns(spec){
  if(spec.formula==='cube_view') return [{name:spec.outLabel||'Output',fn:v=>maEval(spec,v)}];
  const nm=spec.outNames||[];
  if(spec.formula==='valuation_pv') return [{name:nm[0]||'Output',fn:v=>maEval(spec,v)}];
  return [
    {name:nm[0]||'Revenue',fn:v=>{let s=0;spec.drivers.forEach(d=>{if(d.coef>0)s+=d.coef*maGet(spec,v,d.key);});return s;}},
    {name:nm[1]||'EBITDA',fn:v=>maEval(spec,v)},
  ];
}
function maImpacts(torn,spec,cur){
  const fns=maOutFns(spec);
  // keep the impact-table header in sync with the selected output (cube mode)
  const h=torn.querySelector('.outname3'); if(h&&fns.length===1) h.textContent=fns[0].name;
  spec.drivers.forEach(d=>{
    const tr=torn.querySelector('.ranges tr[data-key="'+CSS.escape(d.key)+'"]');if(!tr)return;
    const cells=tr.querySelectorAll('.oimp');
    fns.forEach((o,oi)=>{
      const cell=cells[oi];if(!cell)return;
      // worst | base | best (base centered) — lower output is the worse case
      const b=o.fn({}),lo=o.fn({[d.key]:cur[d.key].lo}),hi=o.fn({[d.key]:cur[d.key].hi});
      const worst=Math.min(lo,hi),best=Math.max(lo,hi),sw=best-worst;
      if(Math.abs(lo-b)<1e-6&&Math.abs(hi-b)<1e-6){cell.innerHTML='<span class=nochg>— no effect</span>';return;}
      cell.innerHTML='<span class="iv dn">'+maFmt(worst)+'</span><span class=iar>·</span>'
        +'<span class=ivb>'+maFmt(b)+'</span><span class=iar>·</span>'
        +'<span class="iv up">'+maFmt(best)+'</span>'
        +'<div class=idl>swing '+maFmt(sw)+'</div>';
    });
  });
}
function maColor(t){const r=t<0.5?210:Math.round(210-(t-0.5)*2*180),g=t<0.5?Math.round(60+t*2*130):190,b=70;return `rgb(${r},${g},${b})`;}
// per-input sparkline (a small line): how much this input moves the selected
// output in EACH year, as a PERCENT of that year's REVENUE — a stable, always-
// positive scale, so early years aren't dwarfed by the big late-year numbers and
// the break-even year (EBITDA ~0) doesn't make every input spike identically.
function maSparks(torn,cube,out,per){
  const PS=cube.periods,W=96,H=24,pad=3,n=PS.length,bw=n>1?(W-2*pad)/(n-1):0;
  const rev=cube.base["revenue"]||cube.base[out]||{};
  const floor=0.05*Math.max(...PS.map(p=>Math.abs(rev[p]||0)),1e-9);
  const olabel=(cube.outputs.find(x=>x.key===out)||{}).label||out;
  cube.drivers.forEach(d=>{
    const row=torn.querySelector('.shap[data-key="'+CSS.escape(d.key)+'"]');if(!row)return;
    const sp=row.querySelector('.spark');if(!sp)return;
    const pct=PS.map(p=>{const c=(d.out[out]||{})[p];if(!c)return 0;
      return Math.abs(c.high-c.low)/Math.max(Math.abs(rev[p]||0),floor);});
    const mx=Math.max(...pct,1e-9);
    const pts=pct.map((v,i)=>[pad+i*bw,(H-pad)-(v/mx)*(H-2*pad)]);
    const line=pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
    let s='<svg width="'+W+'" height="'+H+'" role=img>';
    s+='<polygon points="'+pad+','+(H-pad)+' '+line+' '+(pad+(n-1)*bw).toFixed(1)+','+(H-pad)+'" fill="#efe7d6"/>';
    s+='<polyline points="'+line+'" fill="none" stroke="#b88a4e" stroke-width="1.3"/>';
    pts.forEach((p,i)=>{const sel=(per!=='__cum__'&&PS[i]===per);
      if(sel)s+='<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="2.6" fill="#9c4221"/>';});
    pct.forEach((v,i)=>{const c=(d.out[out]||{})[PS[i]];const abs=c?Math.abs(c.high-c.low):0;
      s+='<rect x="'+(pad+i*bw-bw/2).toFixed(1)+'" y="0" width="'+(bw||W).toFixed(1)+'" height="'+H+'" fill="transparent"><title>'+PS[i]+': moves '+olabel+' by '+(v*100).toFixed(1)+'% of that year\'s revenue (±'+maFmt(abs)+')</title></rect>';});
    sp.innerHTML=s+'</svg>';
  });
}
function maRender(torn){
  const raw=JSON.parse(torn.querySelector('script.spec').textContent);
  const spec=maSpec(torn);
  const cur=maRanges(torn);
  const oname=spec.outLabel||((spec.outNames||['output'])[(spec.outNames||[]).length-1]||'output');
  torn.querySelectorAll('.outname,.outname2').forEach(el=>el.textContent=oname);
  const base=maEval(spec,{});
  torn.querySelector('.base').textContent=maFmt(base);
  // all-adverse / all-favorable: each input set to the end that lowers / raises the output
  const advv={},favv={};
  spec.drivers.forEach(d=>{const lo=maEval(spec,{[d.key]:cur[d.key].lo}),hi=maEval(spec,{[d.key]:cur[d.key].hi});
    const aLo=lo<hi; advv[d.key]=aLo?cur[d.key].lo:cur[d.key].hi; favv[d.key]=aLo?cur[d.key].hi:cur[d.key].lo;});
  torn.querySelector('.dnv').textContent=maFmt(maEval(spec,advv));
  torn.querySelector('.upv').textContent=maFmt(maEval(spec,favv));
  // single magnitude bar per input, sorted by swing
  const eff=spec.drivers.map(d=>{const lo=maEval(spec,{[d.key]:cur[d.key].lo}),hi=maEval(spec,{[d.key]:cur[d.key].hi});return {key:d.key,lo,hi,swing:Math.abs(hi-lo)};});
  const maxs=Math.max(...eff.map(e=>e.swing),1e-9);
  const box=torn.querySelector('.shapbars');
  // only the active drivers get a bar (expanded aggregates show their children)
  const activeKeys=new Set(spec.drivers.map(d=>d.key));
  box.querySelectorAll('.shap').forEach(r=>{r.style.display=activeKeys.has(r.dataset.key)?'grid':'none';});
  eff.sort((a,b)=>b.swing-a.swing);
  eff.forEach(e=>{const row=box.querySelector('.shap[data-key="'+CSS.escape(e.key)+'"]');if(!row)return;box.appendChild(row);
    row.querySelector('.bar1').style.width=(e.swing/maxs*100)+'%';
    row.querySelector('.vv').textContent=maFmt(Math.min(e.lo,e.hi))+' → '+maFmt(Math.max(e.lo,e.hi));});
  if(raw.formula==='cube'){const o=(torn.querySelector('.outsel')||{}).value||raw.cube.defaultOutput;const pr=(torn.querySelector('.persel')||{}).value||raw.cube.defaultPeriod;maSparks(torn,raw.cube,o,pr);}
  maImpacts(torn,spec,cur);
  // two-way grid
  const A=spec.drivers.find(d=>d.key===torn.querySelector('.selA').value)||spec.drivers[0];
  const B=spec.drivers.find(d=>d.key===torn.querySelector('.selB').value)||spec.drivers[0];
  const gb=torn.querySelector('.gridbox');
  if(!A||!B){gb.innerHTML='';return;}
  const yr=spec.period||'this year';
  if(A.key===B.key){gb.innerHTML='<div class=flatmsg>Pick <b>two different</b> inputs to see how they interact.</div>';return;}
  const N=5,av=[],bv=[];
  for(let i=0;i<N;i++){av.push(cur[A.key].lo+(cur[A.key].hi-cur[A.key].lo)*i/(N-1));bv.push(cur[B.key].lo+(cur[B.key].hi-cur[B.key].lo)*i/(N-1));}
  let cells=[],mn=Infinity,mx=-Infinity;
  for(let i=0;i<N;i++){cells.push([]);for(let j=0;j<N;j++){const ov={};ov[A.key]=av[i];ov[B.key]=bv[j];const v=maEval(spec,ov);cells[i].push(v);if(v<mn)mn=v;if(v>mx)mx=v;}}
  if(mx-mn < 1e-6*Math.max(Math.abs(mx),Math.abs(mn),1)){
    gb.innerHTML='<div class=flatmsg>Neither <b>'+A.label+'</b> nor <b>'+B.label+'</b> moves '+oname+' in <b>'+yr+'</b> — they act in other years. Try a later year (or <b>Cumulative</b>), or pick inputs that bite in '+yr+'.</div>';return;}
  const fp=x=>maFmt(x);
  const ctx=spec.outLabel?('<b>'+spec.outLabel+(spec.period?' · '+spec.period:'')+'</b> — '):'';
  let h='<div class=cap>'+ctx+'cells = '+(spec.outLabel||'output')+'; columns: '+B.label+' · rows: '+A.label+'</div><table class=grid2><tr><th></th>';
  for(let j=0;j<N;j++)h+='<th>'+fp(bv[j])+'</th>';h+='</tr>';
  for(let i=0;i<N;i++){h+='<tr><td class=axl>'+fp(av[i])+'</td>';for(let j=0;j<N;j++){const v=cells[i][j];const tn=mx>mn?(v-mn)/(mx-mn):0.5;h+='<td style="background:'+maColor(tn)+';color:#fff">'+maFmt(v)+'</td>';}h+='</tr>';}
  torn.querySelector('.gridbox').innerHTML=h+'</table>';
}
function maToggle(torn,key){
  const bar=torn.querySelector('.shap.agg[data-key="'+CSS.escape(key)+'"]');
  const on=!(bar&&bar.classList.contains('expanded'));
  torn.querySelectorAll('[data-key="'+CSS.escape(key)+'"]').forEach(el=>el.classList.toggle('expanded',on));
  torn.querySelectorAll('tr.child[data-parent="'+CSS.escape(key)+'"]').forEach(el=>el.classList.toggle('show',on));
  maRender(torn);
}
document.querySelectorAll('.torn').forEach(t=>{
  t.addEventListener('input',()=>maRender(t));
  t.addEventListener('change',()=>maRender(t));
  t.querySelectorAll('.bexp,.rexp').forEach(x=>x.addEventListener('click',e=>{
    e.stopPropagation();const o=x.closest('[data-key]');if(o)maToggle(t,o.dataset.key);}));
  t.querySelectorAll('.rtbl tr.agg .rl').forEach(x=>x.addEventListener('click',()=>{
    const o=x.closest('[data-key]');if(o)maToggle(t,o.dataset.key);}));
  t.querySelector('.reset')?.addEventListener('click',()=>{
    const spec=maSpec(t);
    t.querySelectorAll('.ranges tr[data-key]').forEach(r=>{const d=spec.defaults[r.dataset.key];if(d){r.querySelector('.lo').value=d.low;r.querySelector('.hi').value=d.high;}});
    maRender(t);
  });
  const spec=maSpec(t);
  if(t.querySelector('.selA'))t.querySelector('.selA').value=spec.selA;
  if(t.querySelector('.selB'))t.querySelector('.selB').value=spec.selB;
  maRender(t);
});
"""


def _company_name(filename: str) -> str:
    """A clean company name from a workbook filename (drop dates, versions, and
    boilerplate like 'Financial Model' / 'Projections' / 'Data Room')."""
    import os
    stem = os.path.splitext(os.path.basename(filename))[0]
    s = re.sub(r"\(.*?\)", " ", stem)                                  # parentheticals
    s = re.sub(r"\d{4}[-_./]\d{1,2}[-_./]\d{1,2}", " ", s)            # 2026-06-01
    s = re.sub(r"\b\d{6,8}\b", " ", s)                                 # 20260507
    s = re.sub(r"[_\-]+", " ", s)
    noise = re.compile(r"^(financ\w*|model\w*|plan|projection\w*|forecast\w*|analysis|data ?room|"
                       r"non.?nda|nda|cvt|note|new|final|draft|copy|v\d*|rev\d*|q[1-4]|fy\d*)$", re.I)
    out: list[str] = []
    for t in s.split():
        if noise.match(t) or re.fullmatch(r"\d+(\.\d+)?", t):
            break
        out.append(t)
    return " ".join(out).strip() or stem


def render_report(report: Report, filename: str) -> str:
    wm = report.workbook_map
    o: list[str] = []
    a = o.append
    company = _company_name(filename)
    a("<!doctype html><html><head><meta charset=utf-8>"
      "<meta name=viewport content='width=device-width,initial-scale=1'>"
      f"<title>{e(company)} — Analysis</title><style>{PAGE_CSS}</style></head><body><div class=wrap>")
    a("<a href=/ class='btn ghost' style='float:right'>&larr; new file</a>")
    a(f"<h1>{e(company)} <span class=muted style='font-weight:400'>— Analysis</span></h1>")
    periods = wm.period_axis.periods if wm.period_axis else []

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

    # (Analysis-plan section removed — the worksheet + flags speak for themselves.)

    # analyst worksheet — each calculation with its source rows AND its own flag line,
    # grouped into collapsible family sections
    findings_by_id = {f.id: f for f in report.findings}
    tables = report.annotation_tables
    if tables:
        a("<h2>Worksheet <span class=muted style='font-weight:400;font-size:14px'>"
          "— each calculation with the model's own rows, and what it flags</span></h2>")
        a("<p class=legend>Four broad groups. Each shows the company's <b style='color:#5f574b'>model rows</b> "
          "once, then the calculations built on them. A calc with a <b style='color:#b11226'>red title</b> has a "
          "cell that stands out — <b>hover that cell</b> for the reason. Click a group to expand it.</p>")
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
            a("<details class=famsec>")
            a(f"<summary><span class=famname>{e(fam)}</span> "
              f"<span class=muted>· {len(group)} calc{'s' if len(group) != 1 else ''}</span></summary>")
            a(render_family(fam, group, findings_by_id))
            a("</details>")

    # sensitivity — Shapley contribution bars + two-way grid, off the model's own inputs
    if report.sensitivities:
        a("<h2>Sensitivity <span class=muted style='font-weight:400;font-size:14px'>"
          "— how the outputs move when you change the model's inputs</span></h2>")
        a("<p class=legend>Each input below is one of the model's <b>own cells</b> (hover a bar for its "
          "<code>Sheet!Cell</code>). Where the workbook's formulas can be evaluated, flexing an input "
          "<b>recomputes the outputs through the model's own formulas</b> — an exact what-if, and you can "
          "<b>pick which output and year</b> to view; otherwise the model's revenue/cost lines are flexed "
          "directly (the per-tornado note says which). The <b>tornado</b> shows one bar per input — its length "
          "is how far that input moves the selected output (±20% by default; the swing is the same up or down, "
          "so a single bar says it all). The <b>two-way grid</b> moves two inputs at once. Edit any range and "
          "everything re-runs live.</p>")
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


def _run_job(job_id: str, tmp_path: str, filename: str, use_llm: bool, provider: str = "anthropic"):
    def prog(label: str, frac: float):
        with _JOBS_LOCK:
            j = _JOBS.get(job_id)
            if j is not None:
                j["phase"], j["frac"] = label, frac
    try:
        report = annotate(tmp_path, out_dir=str(OUT_ROOT / Path(filename).stem),
                          no_llm=not use_llm, llm_provider=provider, write_outputs=True, progress=prog)
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
        use_llm = USE_LLM        # always on when a key is available
        provider = (fields.get("provider", ["anthropic"])[0] or "anthropic").lower()
        if provider not in ("anthropic", "refiant"):
            provider = "anthropic"
        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "phase": "Starting…", "frac": 0.0,
                             "html": None, "error": ""}
        threading.Thread(target=_run_job, args=(job_id, tmp.name, filename, use_llm, provider),
                         daemon=True).start()
        self._send(json.dumps({"job": job_id}), ctype="application/json")


def main(argv=None):
    p = argparse.ArgumentParser(description="Local web UI for model-annotator")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--llm", action="store_true", help="enable optional LLM narrative polish")
    args = p.parse_args(argv)

    global USE_LLM
    # LLM is on when a key for EITHER provider is available; the per-run provider
    # (Anthropic / Refiant) is chosen from the page toggle. --llm forces it on.
    _has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                    or os.environ.get("REFIANT_API_KEY"))
    USE_LLM = args.llm or _has_key
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"\n  VoLo Financial Analysis Tool V3 → {url}")
    a_on = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    r_on = bool(os.environ.get("REFIANT_API_KEY"))
    print(f"  Providers — Anthropic: {'key found' if a_on else 'no key'} · "
          f"Refiant: {'key found' if r_on else 'no key'}")
    print("  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
