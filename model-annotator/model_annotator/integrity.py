"""Integrity tie-outs — the trust gate (pipeline Phase 3).

The model's own arithmetic is checked before anything else is believed:
cash roll-forward, statement sums, cross-sheet mirrors, and the model's own
printed check rows — including whether those checks are *falsifiable* (a
"never fails" check wired to an auto-raise solver proves nothing).

A material failure here becomes the lead finding and marks every downstream
finding ``trust_degraded``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .graph import FormulaGraph
from .ingest import WorkbookData, is_number
from .mapping import LineItem, MappingResult
from .schema import CellCitation, TieOut, TieOutStatus

log = logging.getLogger(__name__)

REL_TOL = 0.005          # 0.5% of series scale counts as material
ABS_FLOOR = 1.0          # residuals below one display unit are rounding noise


@dataclass
class IntegrityResult:
    tie_outs: list[TieOut] = field(default_factory=list)
    trust_score: float = 1.0
    trust_degraded: bool = False
    notes: list[str] = field(default_factory=list)


def _series(it: Optional[LineItem]) -> dict[str, float]:
    if it is None:
        return {}
    return {p: v for p, v in zip(it.periods, it.values) if v is not None}


def _scale_of(*series: dict[str, float]) -> float:
    mx = 0.0
    for s in series:
        for v in s.values():
            mx = max(mx, abs(v))
    return mx


def _residual_tieout(
    tid: str,
    label: str,
    lhs: dict[str, float],
    rhs: dict[str, float],
    evidence_items: list[tuple[LineItem, str]],
    detail: str = "",
) -> Optional[TieOut]:
    common = [p for p in lhs if p in rhs]
    if len(common) < 2:
        return None
    scale = _scale_of(lhs, rhs)
    tol = max(scale * REL_TOL, ABS_FLOOR)
    residuals = {p: lhs[p] - rhs[p] for p in common}
    worst_p = max(residuals, key=lambda p: abs(residuals[p]))
    max_abs = abs(residuals[worst_p])
    material = max_abs > tol
    evidence: list[CellCitation] = []
    for item, role in evidence_items:
        ref = item.ref_for(worst_p)
        if ref:
            evidence.append(CellCitation(ref=ref, value=item.value_for(worst_p), label=f"{role}: {item.label.strip()}"))
    n_off = sum(1 for r in residuals.values() if abs(r) > tol)
    return TieOut(
        id=tid,
        label=label,
        status=TieOutStatus.failed if material else TieOutStatus.passed,
        max_abs_residual=round(max_abs, 4),
        periods_checked=len(common),
        material=material,
        evidence=evidence,
        detail=(detail + (" " if detail else "")
                + (f"{n_off}/{len(common)} periods off by more than {tol:,.2f} "
                   f"(worst: {worst_p}, residual {residuals[worst_p]:+,.2f})"
                   if material else f"max residual {max_abs:,.4f} across {len(common)} periods (tolerance {tol:,.2f})")),
    )


_SOLVER_COMMENT = re.compile(
    r"solver|auto.?(raise|inject|equity|fund)|automatic(ally)? raises?"
    r"|triggers? a raise|raise of \d|prevent[^.]{0,50}negative cash"
    r"|negative cash[^.]{0,60}(raise|trigger)|\bplug\b", re.I)
_SOLVER_STRONG = re.compile(r"raise|negative cash|solver", re.I)


def run_tie_outs(
    wbd: WorkbookData,
    mapping: MappingResult,
    graph: Optional[FormulaGraph] = None,
) -> IntegrityResult:
    res = IntegrityResult()
    sheet = mapping.primary_sheet
    if sheet is None:
        res.notes.append("No statement sheet could be mapped; tie-outs not applicable.")
        res.trust_score = 0.3
        res.trust_degraded = True
        res.tie_outs.append(TieOut(id="cash_roll", label="Cash roll-forward",
                                   status=TieOutStatus.not_applicable, detail="no mapped statements"))
        return res

    end = mapping.get("ending_cash", sheet)
    beg = mapping.get("beginning_cash", sheet)
    chg = mapping.get("net_change_cash", sheet)
    cf_op = mapping.get("cf_operating", sheet)
    cf_inv = mapping.get("cf_investing", sheet)
    cf_fin = mapping.get("cf_financing", sheet)

    # ---- 1. Cash roll-forward: ending[t] = ending[t-1] + net_change[t]
    penalties = 0.0
    if end is not None and chg is not None:
        e, n = _series(end), _series(chg)
        lhs = {}
        rhs = {}
        prev_p: Optional[str] = None
        for p in end.periods:
            if prev_p is not None and p in e and prev_p in e and p in n:
                lhs[p] = e[p]
                rhs[p] = e[prev_p] + n[p]
            prev_p = p
        t = _residual_tieout("cash_roll", "Cash roll-forward (ending = prior ending + net change)",
                             lhs, rhs, [(end, "ending_cash"), (chg, "net_change_cash")])
        if t:
            res.tie_outs.append(t)
            if t.material:
                penalties += 0.4
    elif end is not None and beg is not None and chg is not None:
        pass  # covered below
    else:
        res.tie_outs.append(TieOut(id="cash_roll", label="Cash roll-forward",
                                   status=TieOutStatus.not_applicable,
                                   detail="ending_cash and/or net_change_cash not mapped"))

    # ---- 1b. Beginning-cash continuity: beginning[t] = ending[t-1]
    if end is not None and beg is not None:
        e, b = _series(end), _series(beg)
        lhs, rhs = {}, {}
        prev_p = None
        for p in end.periods:
            if prev_p is not None and p in b and prev_p in e:
                lhs[p] = b[p]
                rhs[p] = e[prev_p]
            prev_p = p
        t = _residual_tieout("beginning_cash_continuity", "Beginning cash equals prior ending cash",
                             lhs, rhs, [(beg, "beginning_cash"), (end, "ending_cash")])
        if t:
            res.tie_outs.append(t)
            if t.material:
                penalties += 0.2

    # ---- 2. Net change = operating + investing + financing
    if chg is not None and cf_op is not None and cf_inv is not None and cf_fin is not None:
        n, o, i, f = _series(chg), _series(cf_op), _series(cf_inv), _series(cf_fin)
        common = [p for p in n if p in o and p in i and p in f]
        lhs = {p: n[p] for p in common}
        rhs = {p: o[p] + i[p] + f[p] for p in common}
        t = _residual_tieout("net_change_sum", "Net change in cash = CF ops + investing + financing",
                             lhs, rhs, [(chg, "net_change_cash"), (cf_op, "cf_operating"),
                                        (cf_inv, "cf_investing"), (cf_fin, "cf_financing")])
        if t:
            res.tie_outs.append(t)
            if t.material:
                penalties += 0.2

    # ---- 3. Gross profit identity
    rev = mapping.get("revenue_total", sheet)
    cogs = mapping.get("cogs_total", sheet)
    gp = mapping.get("gross_profit", sheet)
    if rev is not None and cogs is not None and gp is not None:
        r, c, g = _series(rev), _series(cogs), _series(gp)
        common = [p for p in g if p in r and p in c]
        lhs = {p: g[p] for p in common}
        # COGS may be stored signed either way; pick the orientation that fits
        rhs_pos = {p: r[p] - c[p] for p in common}
        rhs_neg = {p: r[p] + c[p] for p in common}
        err_pos = sum(abs(lhs[p] - rhs_pos[p]) for p in common)
        err_neg = sum(abs(lhs[p] - rhs_neg[p]) for p in common)
        rhs = rhs_pos if err_pos <= err_neg else rhs_neg
        t = _residual_tieout("gp_identity", "Gross profit = revenue − COGS",
                             lhs, rhs, [(gp, "gross_profit"), (rev, "revenue_total"), (cogs, "cogs_total")])
        if t:
            res.tie_outs.append(t)
            if t.material:
                penalties += 0.15

    # ---- 4. Component sums (only when the mapped components look complete)
    for total_id, comp_id, tid in (("opex_total", "opex_component", "opex_sum"),
                                   ("cogs_total", "cogs_component", "cogs_sum")):
        total = mapping.get(total_id, sheet)
        comps = [it for it in mapping.get_all(comp_id) if it.sheet == sheet]
        if total is not None:
            # components live above their total; rows below it (balance-sheet
            # echoes like "Payroll liabilities") are not part of this sum
            comps = [c_ for c_ in comps if c_.index < total.index]
        if total is None or len(comps) < 2:
            continue
        if total_id == "opex_total":
            # depreciation living INSIDE the expense block belongs in its sum
            dep = mapping.get("depreciation", sheet)
            if dep is not None and dep.sheet == sheet and comps:
                lo = min(c_.index for c_ in comps)
                if lo <= dep.index <= total.index and dep.section == total.section:
                    comps = comps + [dep]
        tot = _series(total)
        sums: dict[str, float] = {}
        for p in tot:
            vals = [it.value_for(p) for it in comps]
            vals = [v for v in vals if v is not None]
            if vals:
                sums[p] = sum(vals)
        common = [p for p in tot if p in sums]
        if len(common) < 2:
            continue
        coverage = [abs(sums[p]) / abs(tot[p]) for p in common if abs(tot[p]) > ABS_FLOOR]
        if coverage and sorted(coverage)[len(coverage) // 2] < 0.7:
            res.tie_outs.append(TieOut(
                id=tid, label=f"{total_id} = sum of mapped components",
                status=TieOutStatus.not_applicable, periods_checked=len(common),
                detail=f"mapped components cover only ~{sorted(coverage)[len(coverage)//2]:.0%} of the total; "
                       "sum check would be meaningless"))
            continue
        t = _residual_tieout(tid, f"{total_id} = sum of {len(comps)} mapped components",
                             {p: tot[p] for p in common}, sums,
                             [(total, total_id)] + [(c_, comp_id) for c_ in comps[:3]])
        if t:
            res.tie_outs.append(t)
            if t.material:
                penalties += 0.05

    # ---- 5. Revenue = segments + grants
    segs = [it for it in mapping.get_all("revenue_segment") if it.sheet == sheet]
    grants = [it for it in mapping.get_all("grant_or_subsidy_revenue") if it.sheet == sheet]
    if rev is not None and len(segs) >= 2:
        r = _series(rev)
        sums = {}
        for p in r:
            vals = [it.value_for(p) for it in segs + grants]
            vals = [v for v in vals if v is not None]
            if vals:
                sums[p] = sum(vals)
        common = [p for p in r if p in sums]
        if len(common) >= 2:
            coverage = [abs(sums[p]) / abs(r[p]) for p in common if abs(r[p]) > ABS_FLOOR]
            med_cov = sorted(coverage)[len(coverage) // 2] if coverage else 0.0
            if med_cov >= 0.7:
                t = _residual_tieout("revenue_sum", f"revenue_total = sum of {len(segs)} segments"
                                     + (f" + {len(grants)} grant rows" if grants else ""),
                                     {p: r[p] for p in common}, sums,
                                     [(rev, "revenue_total")] + [(s, "revenue_segment") for s in segs[:3]])
                if t:
                    res.tie_outs.append(t)
                    if t.material:
                        penalties += 0.05

    # ---- 6. Cross-sheet mirrors (same canonical id mapped on two sheets)
    res.tie_outs.extend(_cross_sheet_mirrors(mapping, sheet, res))

    # ---- 7. Printed check rows + falsifiability
    res.tie_outs.extend(_printed_checks(wbd, mapping, graph, res))

    # ---- 8. Missing cached values is a trust problem in itself
    if wbd.missing_cached_values:
        penalties += 0.3

    res.trust_score = round(max(0.05, 1.0 - penalties), 2)
    cash_failed = any(t.id in ("cash_roll", "beginning_cash_continuity") and t.material for t in res.tie_outs)
    res.trust_degraded = cash_failed or res.trust_score < 0.7
    return res


def _cross_sheet_mirrors(mapping: MappingResult, primary: str, res: IntegrityResult) -> list[TieOut]:
    """Statement rows mirrored on another sheet must agree — and a clean ~1000x
    ratio is the units-inconsistency signature, worth its own detail."""
    out: list[TieOut] = []
    for cid in ("revenue_total", "ebitda_or_ebit", "ending_cash"):
        prim = mapping.get(cid, primary)
        if prim is None:
            continue
        others = [it for it in mapping.get_all(cid) if it.sheet != primary]
        for other in others[:2]:
            a, b = _series(prim), _series(other)
            common = [p for p in a if p in b and abs(a[p]) > ABS_FLOOR]
            if len(common) < 3:
                continue
            ratios = sorted(b[p] / a[p] for p in common if a[p])
            med_ratio = ratios[len(ratios) // 2] if ratios else 1.0
            detail = ""
            for factor in (1000.0, 1e6, 0.001, 1e-6):
                if abs(med_ratio - factor) / factor < 0.02:
                    detail = (f"values differ by a consistent ~{factor:g}x factor — "
                              "a units inconsistency between sheets, not a calculation difference. ")
            t = _residual_tieout(
                f"mirror_{cid}_{other.sheet}",
                f"{cid} on '{primary}' matches '{other.sheet}'",
                a, b, [(prim, f"{cid}@{primary}"), (other, f"{cid}@{other.sheet}")],
                detail=detail)
            if t:
                if detail and t.status == TieOutStatus.failed:
                    t.detail = detail + t.detail
                out.append(t)
    return out


def _printed_checks(
    wbd: WorkbookData,
    mapping: MappingResult,
    graph: Optional[FormulaGraph],
    res: IntegrityResult,
) -> list[TieOut]:
    out: list[TieOut] = []
    checks = mapping.get_all("balance_check")
    eq_sheet = (mapping.get("equity_proceeds") or LineItem(
        sheet="", index=0, label="", depth=0, section="", periods=[], coords=[],
        values=[], percentish=False, n_numeric=0, n_formula=0)).sheet

    # comment census for solver/auto-raise language, best evidence first:
    # a comment sitting on the financing sheet beats a macro-help blurb
    raw_solver: list[tuple[int, CellCitation]] = []
    for name, sd in wbd.sheets.items():
        for r, c, rec in sd.iter_cells():
            if rec.comment and _SOLVER_COMMENT.search(rec.comment):
                from .ingest import a1
                from .schema import format_ref
                score = (2 if name == eq_sheet else 0) + \
                        (1 if _SOLVER_STRONG.search(rec.comment) else 0)
                raw_solver.append((score, CellCitation(
                    ref=format_ref(name, a1(r, c)),
                    value=None,
                    label=f"comment: {rec.comment.strip()[:200]}")))
    raw_solver.sort(key=lambda t: -t[0])
    solver_evidence: list[CellCitation] = [c for s, c in raw_solver if s >= 1] or \
                                          [c for _, c in raw_solver]

    # does the equity line participate in a circular group / depend on cash?
    solver_structural = False
    eq = mapping.get("equity_proceeds")
    if graph is not None and eq is not None:
        eq_refs = set(eq.refs)
        for group in graph.circular_groups:
            if any(ref in eq_refs for ref in group):
                solver_structural = True
        end_it = mapping.get("ending_cash")
        if not solver_structural and end_it is not None and eq.n_formula > 0:
            # equity formula cells whose upstream includes cash cells
            try:
                from .schema import parse_ref
                from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
                cash_nodes = set()
                for ref in end_it.refs:
                    sname, cell = parse_ref(ref)
                    cl, row = coordinate_from_string(cell)
                    cash_nodes.add(graph.node_of(sname, row, column_index_from_string(cl)))
                for ref in list(eq_refs)[:6]:
                    sname, cell = parse_ref(ref)
                    cl, row = coordinate_from_string(cell)
                    node = graph.node_of(sname, row, column_index_from_string(cl))
                    ups = graph.upstream(node, cap=50_000)
                    if ups & cash_nodes:
                        solver_structural = True
                        break
            except Exception:
                log.debug("solver structural check failed", exc_info=True)

    for chk in checks:
        vals = [v for v in chk.values if v is not None]
        all_zero = bool(vals) and all(abs(v) <= ABS_FLOOR for v in vals)
        label_says_ok = bool(re.search(r"\bok\b|\bpass", chk.label, re.I))
        passes = all_zero or (not vals and label_says_ok)
        unfalsifiable = (solver_structural or bool(solver_evidence)) and "cash" in chk.label.lower() or \
                        (solver_structural and "balance" in chk.label.lower())
        evidence = [CellCitation(ref=r, value=v, label=chk.label.strip())
                    for r, v in zip(chk.refs[:3], chk.values[:3])]
        evidence.extend(solver_evidence[:2] if unfalsifiable else [])
        if unfalsifiable:
            status = TieOutStatus.unfalsifiable
            detail = ("This check cannot fail by construction: the model injects financing on any "
                      "shortfall (solver/auto-raise mechanism), so a passing value carries no information.")
        elif passes:
            status = TieOutStatus.passed
            detail = f"printed check row {chk.label!r} evaluates clean in all {len(vals)} populated periods"
        else:
            status = TieOutStatus.failed
            worst = max(vals, key=abs) if vals else None
            detail = f"printed check row {chk.label!r} is non-zero (worst {worst:,.2f})" if vals else \
                     f"printed check row {chk.label!r} has no evaluable values"
        out.append(TieOut(
            id=f"printed_check_row_{chk.sheet}_{chk.index}",
            label=f"Printed check: {chk.label.strip()}",
            status=status,
            max_abs_residual=max((abs(v) for v in vals), default=None),
            periods_checked=len(vals),
            material=status == TieOutStatus.failed,
            evidence=evidence,
            detail=detail,
        ))

    # solver exists but no check row was branded unfalsifiable: still surface
    # the mechanism (the relevant check may be a typed toggle, not a row)
    if (solver_structural or solver_evidence) and \
            not any(t.status == TieOutStatus.unfalsifiable for t in out):
        out.append(TieOut(
            id="financing_solver", label="Auto-raise financing mechanism",
            status=TieOutStatus.unfalsifiable,
            evidence=solver_evidence[:3],
            detail="Financing appears to auto-inject on shortfall; any 'never runs out of cash' "
                   "property is a mechanism, not a result.",
        ))
    return out
