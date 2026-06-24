"""Sensitivity — built off the model's OWN input cells, presented the way the
DD financials tool presents it: a Shapley contribution bar (how much each input
drives the base→downside move) plus a two-way grid the analyst can drive
interactively (any two inputs at once, red→green).

Faithful and engine-free: each driver is a real model cell, each output is a
quantity the tool owns the closed form for, so the recompute is exact. To
"include every input that could affect the output", the income-statement output
is decomposed all the way to the model's line items — every mapped revenue
segment, COGS component, and opex component becomes its own driver, not just the
three totals.

Outputs:
  - linear_sum  : output = Σ coef·driver  (EBITDA = Σ segments − Σ COGS − Σ opex)
  - valuation_pv: PV = multiple × EBITDA_exit / (1+rate)^horizon
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from math import factorial
from typing import Optional

from .ingest import is_number
from .mapping import LineItem, MappingResult
from .metrics import find_scalar_assumptions
from .schema import CellKind, SensitivityDriver, Tornado
from .structure import StructureResult

DEFAULT_PCT = 0.20
MAX_DRIVERS = 12            # keep the chart legible — show the inputs that move it most
MAX_RECOMPUTE_CONE = 50_000  # above this, per-input recompute is too slow → line-item flex


@dataclass
class _Spec:
    key: str
    label: str
    refs: list[str]
    base: float
    coef: float                 # +1 adds to output, −1 subtracts (linear_sum)
    unit: str = ""
    low_pct: float = -DEFAULT_PCT
    high_pct: float = DEFAULT_PCT
    low_val: Optional[float] = None
    high_val: Optional[float] = None
    # which direction is "adverse" (lowers the output): -1 means low value is adverse
    adverse: str = "low"        # "low" | "high"

    def low(self) -> float:
        return self.low_val if self.low_val is not None else self.base * (1 + self.low_pct)

    def high(self) -> float:
        return self.high_val if self.high_val is not None else self.base * (1 + self.high_pct)

    def adverse_val(self) -> float:
        return self.low() if self.adverse == "low" else self.high()

    def favorable_val(self) -> float:
        return self.high() if self.adverse == "low" else self.low()


def _terminal(it: Optional[LineItem]):
    if it is None:
        return None
    for p in reversed(it.periods):
        v = it.value_for(p)
        if v is not None:
            return p, float(v), it.ref_for(p)
    return None


def _terminal_for(it: LineItem, period: str):
    v = it.value_for(period)
    return (period, float(v), it.ref_for(period)) if v is not None else None


# ---------------------------------------------------------------------------
# Output evaluators
# ---------------------------------------------------------------------------

def _eval_linear(specs: list[_Spec], overrides: dict[str, float], offset: float = 0.0) -> float:
    return offset + sum(s.coef * overrides.get(s.key, s.base) for s in specs)


def _eval_pv(specs_by_key: dict[str, _Spec], overrides: dict[str, float], horizon: float) -> float:
    def g(k):
        return overrides.get(k, specs_by_key[k].base) if k in specs_by_key else 0.0
    rate = g("rate") if "rate" in specs_by_key else 0.0
    return g("multiple") * g("metric") / ((1 + rate) ** horizon)


# ---------------------------------------------------------------------------
# Shapley contribution of each driver to the base→downside move
# ---------------------------------------------------------------------------

def _shapley_linear(specs: list[_Spec]) -> dict[str, float]:
    # linear output has no interactions: contribution = coef·(adverse − base)
    return {s.key: s.coef * (s.adverse_val() - s.base) for s in specs}


def _shapley_exact(specs: list[_Spec], eval_fn) -> dict[str, float]:
    keys = [s.key for s in specs]
    base = {s.key: s.base for s in specs}
    adv = {s.key: s.adverse_val() for s in specs}
    cache: dict[frozenset, float] = {}

    def f(S: frozenset) -> float:
        if S in cache:
            return cache[S]
        ov = dict(base)
        for k in S:
            ov[k] = adv[k]
        val = eval_fn(ov)
        cache[S] = val
        return val

    n = len(keys)
    phi = {k: 0.0 for k in keys}
    for k in keys:
        rest = [x for x in keys if x != k]
        for r in range(len(rest) + 1):
            w = factorial(r) * factorial(n - r - 1) / factorial(n)
            for S in combinations(rest, r):
                phi[k] += w * (f(frozenset(S) | {k}) - f(frozenset(S)))
    return phi


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _mk_driver(s: _Spec, output_low: float, output_high: float, shapley: float) -> SensitivityDriver:
    lo, hi = s.low(), s.high()
    base = s.base
    lp = (lo - base) / base if abs(base) > 1e-9 else s.low_pct
    hp = (hi - base) / base if abs(base) > 1e-9 else s.high_pct
    return SensitivityDriver(
        key=s.key, label=s.label, input_refs=s.refs, base=base, low=lo, high=hi,
        low_pct=round(lp, 4), high_pct=round(hp, 4),
        output_low=output_low, output_high=output_high, swing=abs(output_high - output_low),
        shapley=shapley, coef=s.coef, unit=s.unit)


def _components(ctx_items, sheet, period, coef, key_prefix, unit) -> list[_Spec]:
    specs = []
    for it in ctx_items:
        if it.sheet != sheet:
            continue
        t = _terminal_for(it, period)
        if t is None:
            continue
        _, v, ref = t
        inst = (it.instance or it.label.strip())[:32]
        specs.append(_Spec(key=f"{key_prefix}:{it.index}", label=f"{inst} ({period})",
                           refs=[ref], base=abs(v) if coef < 0 else v, coef=coef, unit=unit,
                           adverse="high" if coef < 0 else "low"))
    return specs


def _node_for_item(graph, item, period):
    """(graph node, value) for an item's terminal-period cell, or (None, None)."""
    t = _terminal_for(item, period)
    if not t:
        return None, None
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    from .graph import encode
    ref = t[2]
    sh = ref.split("!")[0].strip("'")
    if sh not in graph.sheet_index:
        return None, None
    col_l, row = coordinate_from_string(ref.split("!")[1])
    return encode(graph.sheet_index[sh], row, column_index_from_string(col_l)), t[1]


def _row_label(sd, row: int) -> str:
    for c in range(1, min(sd.max_col, 12) + 1):
        rec = sd.cell(row, c)
        if rec is not None and isinstance(rec.value, str) and rec.value.strip():
            return rec.value.strip()[:38]
    return f"row {row}"


def _sheet_bands(structure, sheet: str, max_col: int) -> list[tuple[int, int]]:
    """Horizontal table bands on a sheet, as (col_lo, col_hi) column ranges,
    derived from the detected period axes' spans. Sheets that stack two tables
    side-by-side (a blank-column gap between their period headers) yield TWO
    bands; ordinary sheets yield ONE band spanning the whole row (identical to
    the previous whole-row behaviour). This lets a driver's input cells be kept
    inside the table its label heads, instead of bleeding into a neighbouring
    table that merely shares the physical row."""
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    spans: list[tuple[int, int]] = []
    for ax in (structure.axes.get(sheet) or []):
        cols = []
        for ref in ax.header_cells:
            try:
                cl, _r = coordinate_from_string(ref.split("!")[-1])
                cols.append(column_index_from_string(cl))
            except Exception:
                continue
        if cols:
            spans.append((min(cols), max(cols)))
    if not spans:
        return [(1, max_col)]
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for lo, hi in spans[1:]:
        if lo <= merged[-1][1] + 1:                 # overlapping / adjacent -> one table
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    if len(merged) <= 1:                            # single table -> whole-row scan (unchanged)
        return [(1, max_col)]
    return [(lo, hi) for lo, hi in merged]


def _aggregate_groups(sd, blo: int, bhi: int, leaf_rows: list[int]) -> list[tuple[int, list[int]]]:
    """Roll leaf input rows up to the model's own total. Returns (total_row,
    member_rows) for each row whose per-band-period values equal the SUM of >=2 of
    the given leaf rows sitting just above it (blank rows tolerated) -- e.g. the
    per-segment 'new clients' rows summing into 'TOTAL New clients'. Sum-based, so
    it needs no naming convention; a row is never grouped into more than one total."""
    cols = list(range(blo, bhi + 1))

    def vec(r):
        out = []
        for c in cols:
            rec = sd.cell(r, c)
            v = rec.value if rec is not None else None
            out.append(v if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        return out
    leafset = sorted(set(leaf_rows))
    if len(leafset) < 2:
        return []
    groups: list[tuple[int, list[int]]] = []
    used: set[int] = set()
    for A in range(min(leafset) + 1, max(leafset) + 12):
        if A in leafset:                       # a total is not itself a leaf input
            continue
        av = vec(A)
        if not any(x not in (None, 0) for x in av):
            continue
        members = [r for r in leafset if 0 < A - r <= 12 and r not in used]
        if len(members) < 2:
            continue
        s = [0.0] * len(cols)
        for m in members:
            for i, x in enumerate(vec(m)):
                s[i] += (x or 0)
        present = [i for i in range(len(cols)) if av[i] is not None]
        if present and max(abs((av[i] or 0) - s[i]) for i in present) <= \
                0.02 * max(max((abs(av[i]) for i in present), default=1.0), 1.0):
            groups.append((A, members))
            used.update(members)
    return groups


def _band_label(sd, row: int, band_lo: int) -> str:
    """Label for a driver whose data sits in the band starting at band_lo: the
    nearest non-empty text cell to the LEFT of the band on this row, so a
    second-table driver is named from its OWN table's label (not the leftmost
    label, which belongs to the first table). Falls back to the row label."""
    if band_lo > 1:
        for c in range(band_lo - 1, 0, -1):
            rec = sd.cell(row, c)
            if rec is not None and isinstance(rec.value, str) and rec.value.strip():
                return rec.value.strip()[:38]
    return _row_label(sd, row)


def _section_label(sd, blo: int, bhi: int, row: int) -> Optional[str]:
    """The section header above a data row in band [blo,bhi]: the nearest row
    above with a text label (left of the band) but NO numeric data IN the band —
    i.e. a title row like 'Licensing (royalty per unit)' or 'Units x client'.
    Lets per-segment rows be grouped by the family their modeller put them under."""
    for rr in range(row - 1, max(0, row - 30), -1):
        lab = None
        for c in range(max(1, blo - 1), 0, -1):
            rec = sd.cell(rr, c)
            if rec is not None and isinstance(rec.value, str) and rec.value.strip():
                lab = rec.value.strip()
                break
        if not lab:
            continue
        has_num = False
        for c in range(blo, bhi + 1):
            rec = sd.cell(rr, c)
            if rec is not None and isinstance(rec.value, (int, float)) and not isinstance(rec.value, bool):
                has_num = True
                break
        if not has_num:                 # text + no numbers in the band -> a title row
            return lab
    return None


def _clean_label(s: str) -> str:
    """Tidy a raw row/section label into a readable driver name: drop trailing
    unit notes (€/unit, /client), AVERAGE/TOTAL noise, and surrounding clutter."""
    import re as _r
    out = _r.sub(r"\s*\(?\s*€?\s*/\s*(unit|client|person|day|t|head|month|year)\b.*$", "", s, flags=_r.I)
    out = _r.sub(r"\s*€\s*/\s*\w+\s*$", "", out)
    out = _r.sub(r"\s+(?:AVERAGE|AVG)\s*$", "", out, flags=_r.I)   # trailing 'AVERAGE' noise only
    out = _r.sub(r"\s{2,}", " ", out).strip(" -—:·,")
    return (out or s.strip())[:40]


def _recompute_ebitda_tornado(wbd, structure, mapping, graph, U, periods) -> Optional[Tornado]:
    """Flex the model's REAL input cells and recompute EVERY mapped output at
    EVERY period exactly through the workbook's own formulas, so the UI can let
    the analyst pick which output/year to view. Returns None when the model can't
    be faithfully reproduced (the caller then uses the line-item flex)."""
    if graph is None:
        return None
    from .graph import encode, rank_inputs_by_impact
    from .recompute import RecomputeModel
    sheet = mapping.primary_sheet
    rev_item = mapping.get("revenue_total", sheet)
    eb_item = mapping.get("ebitda_or_ebit", sheet)
    if rev_item is None or eb_item is None:
        return None
    t = _terminal(rev_item)
    tp = t[0] if t else None
    if tp is None:
        return None
    eb_node, _ = _node_for_item(graph, eb_item, tp)
    rev_node, _ = _node_for_item(graph, rev_item, tp)
    if eb_node is None or rev_node is None:
        return None

    # Exact recompute costs ~O(model size) PER flexed input. On a very large model
    # (e.g. a 100k+-cell project-finance workbook) flexing dozens of inputs is
    # impractically slow, so fall back to the line-item flex there rather than
    # hang. Small/medium models recompute exactly.
    up = graph.upstream(eb_node, cap=400_000) | graph.upstream(rev_node, cap=400_000)
    if len(up) > MAX_RECOMPUTE_CONE:
        return None

    # outputs the analyst can view the change in (whichever are mapped), richest
    # first; the per-period cell of each is recomputed exactly.
    want_full = [("ebitda", "EBITDA", eb_item), ("revenue", "Revenue", rev_item)]
    for okey, label, cid in [("gross_profit", "Gross profit", "gross_profit"),
                             ("net_income", "Net income", "net_income")]:
        it = mapping.get(cid, sheet)
        if it is not None:
            want_full.append((okey, label, it))

    def _out_nodes(specs):
        nodes = {}
        for okey, _label, it in specs:
            for p in periods:
                nd, _v = _node_for_item(graph, it, p)
                if nd is not None:
                    nodes[(okey, p)] = nd
        return nodes

    model = None
    out_specs = None
    out_nodes = {}
    for specs in (want_full, want_full[:2]):     # try all outputs, then just EBITDA+revenue
        out_nodes = _out_nodes(specs)
        check = list(set(out_nodes.values())) or [eb_node, rev_node]
        # the cone covers every period (so the cube can read them) but faithfulness
        # is gated only on the headline terminal EBITDA + revenue
        model = RecomputeModel.build(graph, check, gate_nodes=[eb_node, rev_node])
        if model is not None:
            out_specs = [s for s in specs if any((s[0], p) in out_nodes for p in periods)]
            break
    if model is None or not out_specs:
        return None
    all_nodes = list(set(out_nodes.values()))
    eb_base = model.values.get(eb_node)
    rev_base = model.values.get(rev_node)

    # discover EVERY input that actually feeds REVENUE or EBITDA (union of both
    # cones, computed above) — not a fan-out-biased pre-filter — so cost stacks and
    # volume levers are not missed; dead/reference cells are excluded (not upstream).
    from openpyxl.utils import get_column_letter
    import re as _re
    _COST = _re.compile(r"cost|personnel|payroll|opex|sg&a|r&d|salar|capex|overhead|wage", _re.I)

    def _clean_sheet(name):
        s = _re.sub(r"^(COSTS?-|M-)\s*", "", name).strip()
        return (s[:1].upper() + s[1:]) if s else name

    def _leaf(sn, sd, cen, row, blo, bhi):
        cells = []
        for c in range(blo, bhi + 1):
            if cen.kind(row, c) == CellKind.typed_input:
                rec = sd.cell(row, c)
                if rec is not None and is_number(rec.value):
                    cells.append((encode(graph.sheet_index[sn], row, c), c, float(rec.value)))
        nz = [x for x in cells if x[2] != 0]
        if not nz:
            return None
        cells.sort(key=lambda x: x[1])
        v0 = cells[-1][2] if cells[-1][2] != 0 else max(nz, key=lambda x: abs(x[2]))[2]
        return dict(sn=sn, r=row, band=blo, bhi=bhi, cells=cells, v0=v0,
                    ref=graph.node_ref(cells[-1][0]), label=_band_label(sd, row, blo))

    sheet_rows: dict[str, list[int]] = {}
    for sn, sd in wbd.sheets.items():
        cen = structure.censuses.get(sn)
        si = graph.sheet_index.get(sn)
        if cen is None or si is None:
            continue
        rs = set()
        for r in range(1, sd.max_row + 1):
            for c in range(1, sd.max_col + 1):
                if cen.kind(r, c) != CellKind.typed_input:
                    continue
                rec = sd.cell(r, c)
                if rec is None or not is_number(rec.value) or rec.value == 0:
                    continue
                if encode(si, r, c) in up:
                    rs.add(r)
                    break
        if rs:
            sheet_rows[sn] = sorted(rs)

    # Bound the work: exactly recomputing the whole model per input is expensive
    # on large workbooks, so cap how many candidate rows get flexed. Cost stacks
    # collapse to one driver each (kept whole); elsewhere keep the highest-reach
    # rows by a cheap graph proxy. Exact swing then re-ranks the survivors.
    def _is_cost(nm):
        rl = structure.roles.get(nm)
        return (rl and rl[0].value == "cost_buildup") or bool(_COST.search(nm))
    ROW_CAP = 45
    noncost = [(sn, r) for sn, rows in sheet_rows.items() if not _is_cost(sn) for r in rows]
    if len(noncost) > ROW_CAP:
        pri: dict[tuple, int] = {}
        for i, imp in enumerate(rank_inputs_by_impact(graph, [sheet], top_n=5000)):
            pri.setdefault((imp.sheet, imp.row), i)
        noncost.sort(key=lambda sr: pri.get(sr, 10 ** 9))
        keep = set(noncost[:ROW_CAP])
        for sn in list(sheet_rows):
            if not _is_cost(sn):
                kept = [r for r in sheet_rows[sn] if (sn, r) in keep]
                if kept:
                    sheet_rows[sn] = kept
                else:
                    del sheet_rows[sn]

    leaves = []
    aggregates = []
    child_of = {}                          # id(leaf) -> aggregate dict
    for sn, rows in sheet_rows.items():
        sd = wbd.sheets[sn]
        cen = structure.censuses[sn]
        role = structure.roles.get(sn)
        role = role[0].value if role else ""
        bands = _sheet_bands(structure, sn, sd.max_col)
        row_leaves = []
        for r in rows:
            for (blo, bhi) in bands:
                lf = _leaf(sn, sd, cen, r, blo, bhi)
                if lf:
                    row_leaves.append(lf)
        if not row_leaves:
            continue
        is_cost = role == "cost_buildup" or _COST.search(sn) is not None
        if is_cost and len(row_leaves) >= 6:
            # a dense cost stack -> ONE lever (flex the whole stack), rows = children
            agg = dict(sn=sn, r=-1, band=0,
                       cells=[c for lf in row_leaves for c in lf["cells"]],
                       v0=sum(lf["v0"] for lf in row_leaves),
                       ref=f"{sn} · {len(row_leaves)} inputs",
                       label=f"{_clean_sheet(sn)} cost", children=row_leaves)
            aggregates.append(agg)
            for lf in row_leaves:
                child_of[id(lf)] = agg
        elif role == "assumptions":
            for lf in row_leaves:           # each assumption row is its own lever
                lf["label"] = _clean_label(lf["label"])
                leaves.append(lf)
        else:
            # schedule/other: group per-segment rows by the SECTION header their
            # modeller put them under (e.g. 'Licensing (royalty per unit)',
            # 'Units x client'), so families become one lever with segment children
            # instead of N ambiguous bars. Single-row sections stay standalone,
            # named with their section for context.
            leaves.extend(row_leaves)
            by_sec: dict[tuple, list] = {}
            sec_of: dict[int, Optional[str]] = {}
            for lf in row_leaves:
                sec = _section_label(sd, lf["band"], lf["bhi"], lf["r"])
                sec_of[id(lf)] = sec
                by_sec.setdefault((lf["band"], (sec or f"~row{lf['r']}").lower()), []).append(lf)
            for _key, lfs in by_sec.items():
                sec = sec_of[id(lfs[0])]
                if sec and len(lfs) >= 2:
                    agg = dict(sn=sn, r=-1, band=lfs[0]["band"],
                               cells=[c for lf in lfs for c in lf["cells"]],
                               v0=sum(lf["v0"] for lf in lfs),
                               ref=f"{sn} · {sec[:28]}",
                               label=_clean_label(sec), children=lfs)
                    for lf in lfs:
                        lf["label"] = _clean_label(lf["label"])    # child = segment name
                    aggregates.append(agg)
                    for lf in lfs:
                        child_of[id(lf)] = agg
                else:                       # single-row section -> standalone w/ context
                    for lf in lfs:
                        base = _clean_label(lf["label"])
                        lf["label"] = f"{base} ({_clean_label(sec)})" if sec else base

    # merge duplicate aggregates across replica blocks (e.g. three 'TOTAL New
    # clients' from Spain/EU/US become ONE driver flexing all blocks together)
    _bylabel: dict[str, dict] = {}
    _merged: list[dict] = []
    for agg in aggregates:
        k = _re.sub(r"[^a-z0-9]+", "", agg["label"].lower())
        if k and k in _bylabel:
            m = _bylabel[k]
            m["cells"] = m["cells"] + agg["cells"]
            m["children"] = m["children"] + agg["children"]
            m["v0"] = m["v0"] + agg["v0"]
        else:
            _bylabel[k] = agg
            _merged.append(agg)
    aggregates = _merged

    def _flex(cells):
        ov_lo = {nd: val * 0.8 for (nd, _c, val) in cells}
        ov_hi = {nd: val * 1.2 for (nd, _c, val) in cells}
        lo = model.outputs_with_overrides(ov_lo, all_nodes)
        hi = model.outputs_with_overrides(ov_hi, all_nodes)
        # rank by how much the lever moves the OUTCOME — revenue or EBITDA, whichever
        # it hits hardest (volume levers move revenue; cost levers move EBITDA)
        eb_sw = abs((hi.get(eb_node) or 0) - (lo.get(eb_node) or 0))
        rv_sw = abs((hi.get(rev_node) or 0) - (lo.get(rev_node) or 0))
        return lo, hi, max(eb_sw, rv_sw)

    gate = max(1.0, 1e-4 * abs(eb_base or 1))
    cand = []
    claimed: set[int] = set()      # GUARANTEE: each input cell is flexed by at most one driver
    # aggregates first (broader levers claim their cells); then standalone leaves
    for agg in aggregates:
        cells = [x for x in agg["cells"] if x[0] not in claimed]
        if not cells:
            continue
        lo, hi, sw = _flex(cells)
        if sw < gate:
            for lf in agg["children"]:
                child_of.pop(id(lf), None)
            continue
        cellset = {x[0] for x in cells}
        claimed |= cellset
        kids = []
        for lf in agg["children"]:          # children are this aggregate's own decomposition
            if not all(c[0] in cellset for c in lf["cells"]):
                continue
            klo, khi, ksw = _flex(lf["cells"])
            kids.append(dict(lf, lo=klo, hi=khi, swing=ksw))
        kids.sort(key=lambda d: -d["swing"])           # keep the most material rows as children
        cand.append(dict(agg, cells=cells, lo=lo, hi=hi, swing=sw, children=kids[:10]))
    for lf in leaves:                       # ungrouped leaves -> standalone drivers
        if id(lf) in child_of:
            continue
        cells = [x for x in lf["cells"] if x[0] not in claimed]
        if not cells:
            continue
        lo, hi, sw = _flex(cells)
        if sw < gate:
            continue
        claimed |= {x[0] for x in cells}
        cand.append(dict(lf, cells=cells, lo=lo, hi=hi, swing=sw, children=[]))
    if not cand:
        return None
    cand.sort(key=lambda d: -d["swing"])
    cand = cand[:MAX_DRIVERS]
    # disambiguate duplicate labels (e.g. two 'Edge computing' rows) by cell
    lab_counts: dict[str, int] = {}
    for d in cand:
        lab_counts[d["label"]] = lab_counts.get(d["label"], 0) + 1
    for d in cand:
        if lab_counts[d["label"]] > 1:
            d["label"] = f"{d['label']} ({d['ref'].split('!')[-1]})"

    def _outmat(d):
        return {okey: {p: {"low": d["lo"].get(out_nodes[(okey, p)]),
                           "high": d["hi"].get(out_nodes[(okey, p)])}
                       for p in periods if (okey, p) in out_nodes}
                for okey, _l, _it in out_specs}

    # cube: every driver's effect on every output at every period (for the UI)
    cube = {
        "outputs": [{"key": okey, "label": label} for okey, label, _ in out_specs],
        "periods": list(periods),
        "defaultOutput": "ebitda",
        "defaultPeriod": tp,
        "unit": U,
        "base": {okey: {p: model.values.get(out_nodes[(okey, p)])
                        for p in periods if (okey, p) in out_nodes}
                 for okey, _l, _it in out_specs},
        "drivers": [],
    }
    drivers = []
    adv_ov: dict[int, float] = {}
    fav_ov: dict[int, float] = {}
    for d in cand:
        dkey = f"in:{d['sn']}:{d['r']}:{d.get('band', 0)}"
        kids = [{"key": f"{dkey}::{c['r']}", "label": c["label"], "refs": [c["ref"]],
                 "value": c["v0"], "out": _outmat(c)} for c in d.get("children", [])]
        cube["drivers"].append({"key": dkey, "label": d["label"], "refs": [d["ref"]],
                                "value": d["v0"], "out": _outmat(d), "children": kids})
        eb_lo, eb_hi = d["lo"].get(eb_node), d["hi"].get(eb_node)
        rv_lo, rv_hi = d["lo"].get(rev_node), d["hi"].get(rev_node)
        drivers.append(SensitivityDriver(
            key=dkey, label=f"{d['label']} ({tp})",
            input_refs=[d["ref"]], base=d["v0"], low=d["v0"] * 0.8, high=d["v0"] * 1.2,
            low_pct=-0.20, high_pct=0.20,
            output_low=eb_lo, output_high=eb_hi, swing=d["swing"],
            out2_low=rv_lo, out2_high=rv_hi, unit=U))
        adverse_low = (eb_lo or 0) < (eb_hi or 0)
        fa, ff = (0.8, 1.2) if adverse_low else (1.2, 0.8)
        for (nd, _c, val) in d["cells"]:
            adv_ov[nd] = val * fa
            fav_ov[nd] = val * ff
    downside = model.outputs_with_overrides(adv_ov, [eb_node])[eb_node]
    upside = model.outputs_with_overrides(fav_ov, [eb_node])[eb_node]

    return Tornado(
        output_key="terminal_ebitda", output_label=f"Terminal EBITDA ({tp})",
        output_unit=U, output_base=eb_base, formula="recompute_linear",
        out2_base=rev_base, out2_label="Revenue", cube=cube,
        formula_note="every output recomputed through the model's own formulas as each input is flexed",
        drivers=drivers, downside=downside, upside=upside,
        caveats=["Each input is flexed through the model's actual formulas — an exact recompute, "
                 "not a proportional estimate.",
                 "Single-input moves are exact; all-adverse and the two-way grid combine drivers linearly."])


def build_sensitivities(wbd, structure: StructureResult, mapping: MappingResult,
                        benchmarks: dict, archetype: str = "default",
                        periods: Optional[list[str]] = None, graph=None) -> list[Tornado]:
    out: list[Tornado] = []
    sheet = mapping.primary_sheet
    if sheet is None:
        return out
    if periods is None:
        ax = structure.primary_axis(sheet)
        periods = list(ax.periods) if ax else []
    if not periods:
        return out
    units = structure.units.get(sheet)
    U = units.label if units else "model units"

    rev = mapping.get("revenue_total", sheet)
    tp = None
    if rev is not None:
        t = _terminal(rev)
        tp = t[0] if t else None

    # ---- Output 1: terminal EBITDA ----------------------------------------
    # Decompose revenue/COGS/opex into line items ONLY when those items
    # reconcile to the model's own total for that group; otherwise the mapped
    # "components" overlap (a subtotal like "TOTAL Salaries" plus its parts, or
    # an LLM-mapped bucket), which would double-count — so fall back to the one
    # total cell. This keeps every driver a real, non-overlapping model line.
    def _group(comp_items, total_item, coef, key_prefix, total_label):
        comps = _components(comp_items, sheet, tp, coef, key_prefix, U)
        tt = _terminal_for(total_item, tp) if total_item is not None else None
        if comps and tt is not None:
            comp_sum = sum(s.base for s in comps)            # bases are abs for costs
            total_abs = abs(tt[1])
            if total_abs > 1 and abs(comp_sum - total_abs) <= 0.02 * total_abs:
                return comps                                  # clean, non-overlapping decomposition
        if tt is not None:                                    # overlap or gap -> use the single total
            return [_Spec(key_prefix, f"{total_label} ({tp})", [tt[2]],
                          abs(tt[1]) if coef < 0 else tt[1], coef, U,
                          adverse="high" if coef < 0 else "low")]
        return comps

    # Preferred: flex the model's REAL input cells and recompute EBITDA exactly
    # through its own formulas. Falls back to the line-item closed form when the
    # workbook can't be faithfully recomputed (XLOOKUP/VBA/etc.).
    recomputed = None
    try:
        recomputed = _recompute_ebitda_tornado(wbd, structure, mapping, graph, U, periods)
    except Exception:
        recomputed = None
    if recomputed is not None:
        out.append(recomputed)

    if recomputed is None and rev is not None and tp is not None:
        specs: list[_Spec] = []
        specs += _group([it for it in mapping.get_all("revenue_segment") if it.sheet == sheet],
                        rev, +1.0, "rev", "Total revenue")
        specs += _group([it for it in mapping.get_all("cogs_component") if it.sheet == sheet],
                        mapping.get("cogs_total", sheet), -1.0, "cogs", "Total COGS")
        specs += _group([it for it in mapping.get_all("opex_component") if it.sheet == sheet],
                        mapping.get("opex_total", sheet), -1.0, "opex", "Total opex")

        if len(specs) >= 2:
            # Anchor the base to the model's OWN EBITDA/EBIT row when it exists,
            # so the output the analyst flexes equals the number the model
            # actually reports. The decomposed revenue−COGS−opex can differ (a
            # line not mapped, D&A handled differently, other income); the gap
            # becomes a constant offset, leaving every per-input delta exact.
            offset = 0.0
            anchored = False
            own_eb = mapping.get("ebitda_or_ebit", sheet)
            own_t = _terminal_for(own_eb, tp) if own_eb is not None else None
            if own_t is None and own_eb is not None:
                own_t = _terminal(own_eb)
            if own_t is not None:
                decomposed = _eval_linear(specs, {})
                offset = own_t[1] - decomposed
                anchored = abs(offset) > 0.01 * max(abs(own_t[1]), 1)

            base_out = _eval_linear(specs, {}, offset)
            downside = _eval_linear(specs, {s.key: s.adverse_val() for s in specs}, offset)
            upside = _eval_linear(specs, {s.key: s.favorable_val() for s in specs}, offset)
            shap = _shapley_linear(specs)            # contributions are differences; offset cancels
            drivers = []
            for s in specs:
                ol = _eval_linear(specs, {s.key: s.low()}, offset)
                oh = _eval_linear(specs, {s.key: s.high()}, offset)
                drivers.append(_mk_driver(s, ol, oh, shap[s.key]))
            drivers.sort(key=lambda d: -max(abs(d.shapley), d.swing))
            drivers = drivers[:MAX_DRIVERS]
            caveats = ["Every driver is one of the model's own line-item cells; "
                       "±20% default range, editable below."]
            if anchored:
                caveats.append("Base is anchored to the model's own EBITDA row; the part not "
                               "explained by the decomposed lines is held constant, so each "
                               "input's effect is exact while the base matches the model.")
            out.append(Tornado(
                output_key="terminal_ebitda", output_label=f"Terminal EBITDA ({tp})",
                output_unit=U, output_base=base_out, formula="linear_sum", offset=offset,
                formula_note="EBITDA = revenue − COGS − opex, decomposed to every mapped line item",
                drivers=drivers, downside=downside, upside=upside, caveats=caveats))

    # ---- Output 2: present value of exit ----------------------------------
    pv = _valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods, U)
    if pv is not None:
        out.append(pv)
    return out


def _valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods, U) -> Optional[Tornado]:
    sheet = mapping.primary_sheet
    ebitda_mult = find_scalar_assumptions(wbd, structure, r"ebitda\s*multiple|multiple.*ebitda|x\s*ebitda", (0.5, 100))
    dr = find_scalar_assumptions(wbd, structure, r"discount\s*rate|hurdle|wacc", (0.01, 0.99))
    exit_year = find_scalar_assumptions(wbd, structure, r"exit\s*year|valuation\s*year", (1990, 2100))
    ebitda = mapping.get("ebitda_or_ebit", sheet)
    if not ebitda_mult or ebitda is None or not periods:
        return None
    yr = str(int(exit_year[0][1])) if exit_year else None
    exit_p = next((p for p in periods if p.startswith(yr)), None) if yr else None
    if exit_p is None:
        for p in reversed(periods):
            if ebitda.value_for(p) is not None:
                exit_p = p
                break
    if exit_p is None or ebitda.value_for(exit_p) is None or ebitda.value_for(exit_p) <= 0:
        return None
    e_exit = float(ebitda.value_for(exit_p))
    mref, mval, mlabel = ebitda_mult[0]
    rate_ref = rate_val = None
    if dr:
        pref = [t for t in dr if re.search(r"current|to\s*exit|today|present", t[2], re.I)]
        rate_ref, rate_val, _ = (pref[0] if pref else dr[0])
    cur = find_scalar_assumptions(wbd, structure, r"current\s*year|valuation\s*(date|year)|base\s*year", (1990, 2100))
    base_year = int(cur[0][1]) if cur else (int(re.match(r"(\d{4})", periods[0]).group(1)) if re.match(r"(\d{4})", periods[0]) else None)
    me = re.match(r"(\d{4})", exit_p)
    horizon = float(int(me.group(1)) - base_year) if (me and base_year is not None) else 0.0

    rng = (benchmarks or {}).get("ev_ebitda_multiple", {}).get(archetype) or \
          (benchmarks or {}).get("ev_ebitda_multiple", {}).get("default") or {}

    specs: list[_Spec] = [
        _Spec("multiple", f"Exit EBITDA multiple ({mlabel})", [mref], mval, 0.0, "x", adverse="low",
              low_val=float(rng.get("low", mval * 0.8)) if rng else None,
              high_val=float(rng.get("high", mval * 1.2)) if rng else None),
        _Spec("metric", f"Exit-year EBITDA ({exit_p})", [ebitda.ref_for(exit_p)], e_exit, 0.0, U, adverse="low"),
    ]
    if rate_val is not None:
        specs.append(_Spec("rate", "Discount rate", [rate_ref], rate_val, 0.0, "fraction",
                           low_pct=-0.25, high_pct=0.25, adverse="high"))
    sbk = {s.key: s for s in specs}

    def ev(ov):
        return _eval_pv(sbk, ov, horizon)

    base_out = ev({})
    downside = ev({s.key: s.adverse_val() for s in specs})
    upside = ev({s.key: s.favorable_val() for s in specs})
    shap = _shapley_exact(specs, ev)
    drivers = []
    for s in specs:
        ol = ev({s.key: s.low()})
        oh = ev({s.key: s.high()})
        drivers.append(_mk_driver(s, ol, oh, shap[s.key]))
    drivers.sort(key=lambda d: -abs(d.shapley))
    return Tornado(
        output_key="valuation_pv", output_label=f"Present value of {exit_p} exit",
        output_unit=U, output_base=base_out, formula="valuation_pv",
        formula_note=f"PV = multiple × EBITDA[{exit_p}] / (1+discount)^{horizon:g}",
        horizon=horizon, drivers=drivers, downside=downside, upside=upside,
        caveats=[f"Discounts {horizon:g} years at the model's own rate; multiple range is the "
                 f"{archetype.replace('_', ' ')} benchmark band.",
                 "Shapley bars attribute the base→downside move across the inputs, including interactions."])
