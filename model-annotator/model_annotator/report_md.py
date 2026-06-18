"""Markdown report renderer.

Mirrors the human methodology, in order: header → "Read this first" →
Tie-outs → Findings (severity-ordered diligence notes) → Acquittals →
Clean checks → Derived-metrics appendix → Assumptions census appendix →
Limitations. Tone: precise, neutral, question-forward.
"""
from __future__ import annotations

from typing import Optional

from .narrate import read_this_first, trust_sentence
from .schema import Report, Severity, TieOutStatus

_STATUS_ICON = {
    TieOutStatus.passed: "✓ pass",
    TieOutStatus.failed: "✗ FAIL",
    TieOutStatus.not_applicable: "– n/a",
    TieOutStatus.unfalsifiable: "⚠ unfalsifiable",
}

_SEV_LABEL = {
    Severity.critical: "CRITICAL",
    Severity.high: "HIGH",
    Severity.medium: "MEDIUM",
    Severity.info: "INFO",
}


def _fmt_v(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:  # NaN
            return "NaN"
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return str(v)[:40]


_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def _table_flag_md(t, findings_by_id):
    related = [findings_by_id[i] for i in t.related_finding_ids if i in findings_by_id]
    related.sort(key=lambda f: _SEV_RANK.get(f.severity.value, 9))
    if related:
        f = related[0]
        q = f" — {f.management_question}" if f.management_question else ""
        return (f"{f.title}{q}", f.severity.value)
    if t.derived_row:
        hl = [c for c in t.derived_row.cells if c.highlighted and c.comment]
        hl.sort(key=lambda c: _SEV_RANK.get(c.severity.value if c.severity else "info", 9))
        if hl:
            return (hl[0].comment, hl[0].severity.value if hl[0].severity else "medium")
    return ("No flags — this calculation looks clean.", None)


def render_markdown(report: Report) -> str:
    wm = report.workbook_map
    _fbi = {f.id: f for f in report.findings}
    out: list[str] = []
    a = out.append

    # ---- header ----
    a(f"# Model annotation: {report.source_file.rsplit('/', 1)[-1]}")
    a("")
    units_label = ""
    if wm.primary_statement_sheet and wm.primary_statement_sheet in wm.units:
        u = wm.units[wm.primary_statement_sheet]
        units_label = f"{u.label} (confidence {u.confidence:.2f})"
    periods = wm.period_axis.periods if wm.period_axis else []
    a(f"| | |")
    a(f"|---|---|")
    a(f"| Primary statements | {wm.primary_statement_sheet or 'not identified'} |")
    a(f"| Periods | {periods[0] + ' – ' + periods[-1] if periods else 'none detected'}"
      f" ({wm.period_axis.granularity.value if wm.period_axis else '–'}, {len(periods)} periods) |")
    a(f"| Units | {units_label or 'unknown'} |")
    a(f"| Analyzed | {report.analyzed_at} · sha256 {report.sha256[:12]}… · "
      f"tool {report.tool_version} · schema {report.schema_version} · "
      f"LLM {'used' if report.llm_used else 'not used'} |")
    a("")

    # ---- executive summary ----
    if report.executive_summary and report.executive_summary.text:
        a("## Executive summary")
        a("")
        a(report.executive_summary.text)
        a("")

    # ---- model-level flags (not tied to one calculation) ----
    _attached = {fid for t in report.annotation_tables for fid in t.related_finding_ids}
    _ml = [f for f in report.sorted_findings()
           if f.id not in _attached and f.severity.value in ("critical", "high", "medium")]
    if _ml:
        a("## Model-level flags")
        a("")
        for f in _ml:
            q = f" — {f.management_question}" if f.management_question else ""
            a(f"- **[{f.severity.value.upper()}]** {f.title}{q}")
        a("")

    # ---- analysis plan (the WHY layer) ----
    plan = report.analysis_plan
    if plan is not None:
        a("## Analysis plan — why these calculations")
        a("")
        src = ("LLM business read of the workbook's structure (labels only, never values)"
               if plan.source == "llm" else "deterministic structural heuristic (--no-llm)")
        a(f"*Source: {src}.*")
        a("")
        a(f"- **Archetype:** {plan.archetype} (benchmarks: `{plan.benchmark_archetype}`)")
        if plan.rationale:
            a(f"- **Rationale:** {plan.rationale}")
        if plan.risks_to_probe:
            a(f"- **Risks worth probing for this archetype:** {'; '.join(plan.risks_to_probe)}")
        if plan.priorities:
            a(f"- **Metric families prioritized:** {', '.join(plan.priorities)}")
        for c in plan.custom_computations:
            status = "computed" if c.executed else f"skipped — {c.skip_reason}"
            a(f"- **LLM-directed computation** `{c.metric_id}` ({status}): {c.rationale}")
        a("")

    # ---- analyst worksheet ----
    if report.annotation_tables:
        a("## Worksheet — each new calculation with the model's own rows")
        a("")
        a("Each block shows the company's source rows in full, then the NEW derived row. "
          "`*` marks a cell that stands out; the reason follows the table. Inputs are typed model cells.")
        a("")
        last_fam = None
        for t in report.annotation_tables:
            if t.family != last_fam:
                a(f"### {t.family}")
                a("")
                last_fam = t.family
            a(f"**{t.title}** — `{t.computation}`")
            if t.rationale:
                a(f"*{t.rationale}*")
            a("")
            per = t.periods
            a("| row | " + " | ".join(per) + " |")
            a("|" + "---|" * (len(per) + 1))
            for sr in t.source_rows:
                cells = {c.period: c for c in sr.cells}

                def _sv(p):
                    if p not in cells or cells[p].value is None:
                        return ""
                    v = cells[p].value
                    return f"{v * 100:.0f}%" if sr.is_percent else _fmt_v(v)
                vals = " | ".join(_sv(p) for p in per)
                cite = f"{sr.sheet} r{sr.row_index}" if sr.sheet and sr.row_index else ""
                a(f"| {sr.label[:40].replace('|', '/')} ({cite}) | {vals} |")
            dr = t.derived_row
            notes = []
            if dr is not None:
                cells = {c.period: c for c in dr.cells}
                row = []
                for p in per:
                    c = cells.get(p)
                    if c is None or c.value is None:
                        row.append("")
                        continue
                    txt = (f"{c.value * 100:.0f}%" if dr.is_percent else _fmt_v(c.value))
                    if c.highlighted:
                        txt = f"**{txt}***"
                        notes.append(f"{p}: {c.comment}")
                    row.append(txt)
                a(f"| **{dr.label[:40].replace('|', '/')} (NEW)** | " + " | ".join(row) + " |")
            a("")
            # one-line flag for this calculation
            ftext, fsev = _table_flag_md(t, _fbi)
            a(f"> **⚑ {fsev.upper()}:** {ftext}" if fsev else f"> ✓ {ftext}")
            a("")
            for n in notes:
                a(f"  - *{n}*")
            if notes:
                a("")

    # ---- sensitivity / tornado ----
    if report.sensitivities:
        a("## Sensitivity — move the model's own inputs, see the effect")
        a("")
        for t in report.sensitivities:
            a(f"**{t.title if hasattr(t, 'title') else t.output_label}** — base "
              f"{_fmt_v(t.output_base)} {t.output_unit}. `{t.formula_note}`")
            a("")
            a("| Driver | Model cell(s) | Base | Low | High | Output swing |")
            a("|---|---|---|---|---|---|")
            for d in t.drivers:
                a(f"| {d.label} | {', '.join(d.input_refs)} | {_fmt_v(d.base)} | "
                  f"{_fmt_v(d.low)} | {_fmt_v(d.high)} | {_fmt_v(d.swing)} |")
            a("")
            for cav in t.caveats:
                a(f"- *{cav}*")
            a("")

    # ---- tie-outs ----
    a("## Tie-outs (the model's own arithmetic)")
    a("")
    a("| Check | Status | Max residual | Periods | Detail |")
    a("|---|---|---|---|---|")
    for t in report.tie_outs:
        res = f"{t.max_abs_residual:,.2f}" if t.max_abs_residual is not None else ""
        a(f"| {t.label or t.id} | {_STATUS_ICON.get(t.status, t.status.value)} | {res} | "
          f"{t.periods_checked or ''} | {t.detail[:160].replace('|', '/')} |")
    a("")

    # (Findings are now shown inline as a one-line flag under each worksheet
    #  calculation, plus the Model-level flags section above.)

    # ---- acquittals ----
    a("## Acquittals — looked alarming, dissolved under investigation")
    a("")
    if not report.acquittals:
        a("None this run.")
        a("")
    for acq in report.acquittals:
        a(f"- **{acq.candidate}** *({acq.category})* — {acq.decomposition}")
        if acq.resolution_cells:
            a(f"  Resolution cells: {', '.join(f'`{c}`' for c in acq.resolution_cells[:6])}")
    a("")

    # ---- clean checks ----
    a("## Clean checks — what the model gets right")
    a("")
    if not report.clean_checks:
        a("None recorded.")
    for c in report.clean_checks:
        ev = (" (" + ", ".join(f"`{e}`" for e in c.evidence[:4]) + ")") if c.evidence else ""
        a(f"- **{c.check}** — {c.result}{ev}")
    a("")

    # ---- derived metrics appendix ----
    a("## Appendix A — Derived metrics")
    a("")
    a("Every value computed in Python from cells read out of the workbook; "
      "`inputs` cites the source rows. Checkpoints are spot-verification values.")
    a("")
    a("| Metric | Checkpoints | Computation | Units |")
    a("|---|---|---|---|")
    for m in report.derived_metrics:
        if m.scalar is not None:
            chk = _fmt_v(m.scalar)
        else:
            chk = "; ".join(f"{p}: {_fmt_v(v)}" for p, v in list(m.checkpoints.items())[:3])
        name = m.metric_id + (" ⚠ext" if m.external_benchmark else "")
        a(f"| {name} | {chk} | `{m.computation[:90].replace('|', '/')}` | {m.units or ''} |")
    a("")

    # ---- assumptions census appendix ----
    a("## Appendix B — Assumptions census (typed inputs that drive the statements)")
    a("")
    a("| # | Cell | Label | Value | Stmt cells fed | Flat? | Comment |")
    a("|---|---|---|---|---|---|---|")
    for e in report.assumptions_census[:40]:
        comment = (e.comment_text or "").replace("\n", " ").replace("|", "/")[:90]
        flat = {True: "flat", False: "", None: "?"}[e.flat_all_periods]
        a(f"| {e.impact_rank} | `{e.cell}` | {e.label[:50].replace('|', '/')} | {_fmt_v(e.value)} | "
          f"{e.dependent_statement_cells:,} | {flat} | {comment} |")
    a("")

    # ---- limitations ----
    a("## Limitations")
    a("")
    if not report.limitations:
        a("None recorded.")
    for lim in report.limitations:
        a(f"- {lim}")
    a("")
    return "\n".join(out)
