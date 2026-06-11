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
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_annotator import annotate
from model_annotator.schema import Report, Severity, TieOutStatus

log = logging.getLogger("model_annotator.serve")


def _load_dotenv() -> None:
    """Populate ANTHROPIC_API_KEY from a .env in this dir or any parent, if the
    shell hasn't already set it. Dependency-free; only reads the keys we use."""
    import os
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return
    here = Path(__file__).resolve()
    for d in [here.parent, *here.parents]:
        env = d / ".env"
        if not env.is_file():
            continue
        for raw in env.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") and v:
                os.environ.setdefault(k, v)
        return


_load_dotenv()

USE_LLM = False  # set by --llm

_SEV_COLOR = {
    Severity.critical: "#b11226",
    Severity.high: "#d9730d",
    Severity.medium: "#b8860b",
    Severity.info: "#5a6b7b",
}
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

    a("<div class=card><div class=row style='justify-content:space-between;align-items:flex-start'>")
    a(f"<div><span class=muted>Trust score</span><div class=trust style='color:{'#1a7f4b' if wm.trust_score>=0.95 else ('#d9730d' if wm.trust_score>=0.7 else '#b11226')}'>{wm.trust_score:.2f}</div></div>")
    a("<div class=kpis>")
    a(f"<div class=kpi><b>{len(report.findings)}</b><span>findings</span></div>")
    a(f"<div class=kpi><b>{len(report.acquittals)}</b><span>acquittals</span></div>")
    a(f"<div class=kpi><b>{len(report.clean_checks)}</b><span>clean checks</span></div>")
    a(f"<div class=kpi><b>{len(report.derived_metrics)}</b><span>metrics</span></div>")
    a("</div></div>")
    a(f"<div class=muted style='margin-top:12px'>Primary statements <b>{e(wm.primary_statement_sheet)}</b> · "
      f"periods {span} ({e(wm.period_axis.granularity.value) if wm.period_axis else '–'}, {len(periods)}) · "
      f"units {units or 'unknown'} · LLM {'used' if report.llm_used else 'off'}</div>")
    a("</div>")

    # read this first
    a("<h2>Read this first</h2>")
    a(f"<div class=card>{e(read_this_first(report))}<p class=muted style='margin:10px 0 0'>{e(trust_sentence(report))}</p></div>")

    # findings
    a("<h2>Findings</h2>")
    if not report.findings:
        a("<div class=card class=muted>No findings above the reporting threshold.</div>")
    for f in report.sorted_findings():
        color = _SEV_COLOR[f.severity]
        a(f"<div class='card fld' id={e(f.id)}>")
        a(f"<div class=row style='justify-content:space-between'><h3 style='margin:0'>{e(f.title)}</h3>"
          f"<span class=pill style='background:{color}'>{f.severity.value.upper()}</span></div>")
        a(f"<div class=muted style='margin:2px 0 8px'>{e(f.id)} · {e(f.category)} · confidence {f.confidence:.2f}"
          + (" · <span style='color:#b11226'>trust degraded</span>" if f.trust_degraded else "") + "</div>")
        a(f"<div>{e(f.narrative)}</div>")
        if f.evidence_values:
            a("<ul class=ev>")
            for ev in f.evidence_values[:8]:
                val = f" = {fmt(ev.value)}" if ev.value is not None else ""
                lbl = f" <span class=muted>— {e(ev.label)}</span>" if ev.label else ""
                a(f"<li><code>{e(ev.ref)}</code>{val}{lbl}</li>")
            a("</ul>")
        elif f.evidence_cells:
            a("<div style='margin-top:6px'>" + " ".join(f"<code>{e(c)}</code>" for c in f.evidence_cells[:10]) + "</div>")
        qi = f.quantified_impact
        if qi and (qi.as_modeled is not None or qi.as_corrected is not None):
            a(f"<div style='margin-top:8px'><b>Quantified impact</b> ({e(qi.metric)}): "
              f"as modeled <b>{fmt(qi.as_modeled)}</b> → as corrected <b>{fmt(qi.as_corrected)}</b>. "
              f"<span class=muted>{e(qi.basis)}</span></div>")
        if f.management_question:
            a(f"<div class=q>Q for management: {e(f.management_question)}</div>")
        a("</div>")

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

    a("</div></body></html>")
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
