"""Regression tests for the methodology fixes (Part 1 correctness + judgment gates).

Each test embeds one pathology from the spec and asserts the generic rule fires.
"""
from model_annotator.mapping import LineItem
from model_annotator import metrics as M

P = ["2026", "2027", "2028", "2029", "2030", "2031"]


def _item(label, vals):
    return LineItem(
        sheet="S", index=1, label=label, depth=0, section="", periods=P,
        coords=[(1, i) for i in range(len(P))], values=list(vals),
        percentish=False, n_numeric=len(P), n_formula=0)


# --- 1.1 never sum a subtotal together with its components -------------------
def test_resolve_hierarchy_drops_subtotal():
    total = _item("Total salaries", [830, 1092, 1517, 2887, 4683, 5110])
    a = _item("Salaries SG&A", [229, 391, 582, 955, 1241, 1303])
    b = _item("Salaries R&D", [601, 701, 935, 922, 1468, 1541])
    c = _item("Salaries other", [0, 0, 0, 1010, 1974, 2266])
    # total ~= a+b+c each period -> total is the subtotal and must be dropped
    leaves, parents = M.resolve_hierarchy([total, a, b, c], P)
    assert [it.label for it in parents] == ["Total salaries"]
    assert sorted(it.label for it in leaves) == ["Salaries R&D", "Salaries SG&A", "Salaries other"]
    # summing leaves must NOT double-count
    s2026 = sum(it.value_for("2026") for it in leaves)
    assert abs(s2026 - 830) < 1.0


def test_resolve_hierarchy_keeps_independent_rows():
    a = _item("Product A", [10, 20, 30, 40, 50, 60])
    b = _item("Product B", [5, 6, 7, 8, 9, 10])
    leaves, parents = M.resolve_hierarchy([a, b], P)
    assert parents == [] and sorted(it.label for it in leaves) == ["Product A", "Product B"]


# --- 1.2 / 2.1 distinguish blank/zero; don't interpret empty rows -----------
def test_is_supported_rejects_all_zero():
    allzero = {p: 0.0 for p in P}
    assert not M.is_supported(allzero, P)
    partial = {**{p: 0.0 for p in P}, "2027": 34_000.0, "2028": 252_000.0}
    assert M.is_supported(partial, P)          # nonzero-then-zero is a real story, keep it


def test_coverage_note_flags_partial():
    s = {"2026": None, "2027": None, "2028": None, "2029": 500.0, "2030": 1000.0, "2031": 0.0}
    assert "3 of 6" in (M.coverage_note(s, P) or "")
    full = {p: 1.0 for p in P}
    assert M.coverage_note(full, P) is None


# --- 1.2 working capital switched off (non-zero historically, zero in plan) --
class _Cell:
    def __init__(self, v):
        self.value = v


class _Sheet:
    def __init__(self, grid):
        self.grid = grid

    def cell(self, r, c):
        return _Cell(self.grid.get((r, c)))


class _Wbd:
    def __init__(self, sheets):
        self.sheets = sheets


def test_pre_horizon_nonzero_detects_switched_off_wc():
    it = _item("Accounts receivable", [0, 0, 0, 0, 0, 0])
    it.sheet = "BALANCE"
    it.coords = [(10, c) for c in range(7, 13)]      # forecast cols 7..12 (all 0)
    wbd = _Wbd({"BALANCE": _Sheet({(10, 5): 34263.0, (10, 6): 252125.0, (10, 7): 0.0})})
    assert M.pre_horizon_nonzero(wbd, it) == 252125.0   # nearest-left historical nonzero
    wbd0 = _Wbd({"BALANCE": _Sheet({(10, 7): 0.0})})    # no history -> not switched off
    assert M.pre_horizon_nonzero(wbd0, it) is None


# --- 3.3 near-zero-denominator artifact -> n/m (pre-scale) -------------------
def test_prescale_masks_tiny_denominator_years():
    rev = {"2026": 100_000.0, "2027": 280_000.0, "2028": 560_000.0,
           "2029": 3_745_000.0, "2030": 16_600_000.0, "2031": 50_579_999.0}
    pre = M.prescale_periods(rev, P)            # < 5% of peak (~2.5M)
    assert pre == {"2026", "2027", "2028"}
    margin = {p: -5.0 for p in P}               # absurd -500% everywhere
    masked = M.mask(margin, pre)
    assert masked["2026"] is None and masked["2031"] == -5.0


# --- 2.3 revenue mix by nature ---------------------------------------------
def test_revenue_nature_classification():
    assert M._revenue_nature("Royalty income") == "recurring"
    assert M._revenue_nature("ARR — subscriptions") == "recurring"
    assert M._revenue_nature("Software license & maintenance") == "recurring"
    assert M._revenue_nature("Turnkey sale") == "one-time"
    assert M._revenue_nature("R&D grant") == "grant"
    assert M._revenue_nature("Consulting services") == "services"
    assert M._revenue_nature("Consumer electronics") == "product"   # default commercial


# --- revenue-share rows: suppress all-zero / non-revenue sources (Rule B) -----
def test_revenue_share_rejects_empty_and_nonrevenue_sources():
    import types
    from model_annotator import metrics as M
    P6 = ["2026", "2027", "2028", "2029", "2030", "2031"]
    seg = types.SimpleNamespace(instance="Consumer electronics", label="Consumer electronics")
    ok = dict(zip(P6, [1.0, 0.71, 0.54, 0.50, 0.38, 0.36]))      # a real, plausible revenue share
    assert M._is_real_revenue_share(seg, ok, P6) is True
    assert M._is_real_revenue_share(seg, {p: 0.0 for p in P6}, P6) is False      # all-zero -> noise
    assert M._is_real_revenue_share(seg, {p: 12.0 for p in P6}, P6) is False     # out of [0,1] -> unit/price basis
    price = types.SimpleNamespace(instance="Consumer electronics", label="Consumer electronics €/unit")
    assert M._is_real_revenue_share(price, ok, P6) is False      # price line label cue -> not a revenue share


# --- sensitivity: section-header detection + label cleaning -------------------
def test_section_label_finds_title_row():
    from model_annotator import sensitivity as S
    grid = {(1, 2): "Licensing (royalty per unit)"}     # title row: text, no band numbers
    for j, v in enumerate([2.5, 12, 6]):                 # 3 data rows below, band cols 3-8
        grid[(2 + j, 2)] = ["Consumer", "Edge", "Data"][j]
        grid[(2 + j, 8)] = v
    sd = _Sheet(grid)
    assert S._section_label(sd, 3, 8, 3) == "Licensing (royalty per unit)"  # data row 3 -> its title
    # a data row with numbers in the band is NOT a section header
    assert S._section_label(sd, 3, 8, 2) is None or "Licensing" in (S._section_label(sd, 3, 8, 2) or "")


def test_clean_label_strips_unit_noise():
    from model_annotator import sensitivity as S
    assert S._clean_label("Licensing (royalty per unit) €/unit") == "Licensing (royalty per unit)"
    assert S._clean_label("New clients x each distributor AVERAGE") == "New clients x each distributor"
    assert S._clean_label("Average ARR (€/Client)") == "Average ARR"


# --- sensitivity: roll segment leaves up to their model total (sum-based) ----
def test_aggregate_groups_rolls_segments_into_total():
    from model_annotator import sensitivity as S
    grid = {}
    seg = {1: [1, 2, 2, 3, 4, 6], 2: [0, 0, 1, 2, 3, 4], 3: [0, 1, 1, 2, 3, 4]}
    for rr, vals in seg.items():
        for j, v in enumerate(vals):
            grid[(rr, 11 + j)] = v
    for j, v in enumerate([1, 3, 4, 7, 10, 14]):       # row 5 = sum of 1+2+3 (row 4 blank)
        grid[(5, 11 + j)] = v
    sd = _Sheet(grid)
    assert S._aggregate_groups(sd, 11, 16, [1, 2, 3]) == [(5, [1, 2, 3])]
    # rows that do NOT sum to a sibling total are left alone (e.g. per-unit prices)
    prices = _Sheet({(1, 11): 2.5, (2, 11): 12, (3, 11): 6, (5, 11): 99})
    assert S._aggregate_groups(prices, 11, 11, [1, 2, 3]) == []


# --- ex-X ratio: suppress when the excluded item exceeds the base -----------
def test_ex_ratio_suppresses_signflip_when_excluded_exceeds_base():
    P4 = ["2026", "2027", "2028", "2029"]
    # gross_profit - grants  (numerator); revenue - grants  (commercial base)
    num = {"2026": 90.0, "2027": -1376.0, "2028": -1000.0, "2029": 500.0}
    base = {"2026": 100.0, "2027": -1106.0, "2028": -440.0, "2029": 3370.0}
    series, undef = M.ex_ratio(num, base, P4)
    # 2027/28: grants exceed revenue -> base negative -> undefined, NOT +124%/+227%
    assert undef == ["2027", "2028"]
    assert series["2027"] is None and series["2028"] is None
    # the neg/neg sign-flip would otherwise produce a healthy-looking positive:
    assert (-1376.0) / (-1106.0) > 1.0
    # valid (positive-base) periods still compute normally
    assert abs(series["2026"] - 0.9) < 1e-9
    assert abs(series["2029"] - 500.0 / 3370.0) < 1e-9


# --- sensitivity input discovery: types, ranges, guards, artifact kills ------
def test_classify_type_context_ranges():
    from model_annotator.input_discovery import classify_type
    # rates move in percentage POINTS, not +-20% of themselves
    assert classify_type("Tax Rate", "0.0%", 0.27).mode == "pp"
    assert classify_type("Fixed Cost Inflation Rate", "0.00%", 0.03).name == "inflation_rate"
    # level inputs move by a type-appropriate +-%
    assert classify_type("CellCo Price per KWh", "#,##0", 45.0).name == "unit_price"
    assert classify_type("Contracted Volume (KWh)", "#,##0", 2850.0).name == "unit_volume"
    assert classify_type("Equity Issuance #21", "#,##0", 81.0).name == "fundraise"
    assert classify_type("Useful Life", "0", 10.0).integer is True
    # unlabelled %-formatted small value -> a generic rate, still pp mode
    assert classify_type("", "0.0%", 0.08).mode == "pp"


def test_ranged_vals_guards_clamp():
    from model_annotator.input_discovery import classify_type, ranged_vals
    # tax 27% flexes +-5pp inside [0, 60%]
    lo, hi = ranged_vals(classify_type("Tax Rate", "0.0%", 0.27), 0.27)
    assert abs(lo - 0.22) < 1e-9 and abs(hi - 0.32) < 1e-9
    # inflation 3% flexes +-1.5pp (0.20 pct-flex would be invisible), floored at 0
    lo, hi = ranged_vals(classify_type("Inflation rate", "0.0%", 0.03), 0.03)
    assert abs(lo - 0.015) < 1e-9 and abs(hi - 0.045) < 1e-9
    lo, _ = ranged_vals(classify_type("Inflation rate", "0.0%", 0.01), 0.01)
    assert lo >= 0.0                                        # guard: no negative rate
    # price never negative; volume +-25%
    lo, hi = ranged_vals(classify_type("Unit price", "", 45.0), 45.0)
    assert lo >= 0 and abs(lo - 45 * 0.85) < 1e-6 and abs(hi - 45 * 1.15) < 1e-6
    # negative line (capex outflow) keeps its sign, magnitude flexes
    lo, hi = ranged_vals(classify_type("Capex", "", -2.5), -2.5)
    assert lo < hi and lo < 0 and hi < 0


def test_bracket_range_invariant():
    from model_annotator.sensitivity import _bracket_range
    # normal pct/pp ranges pass through unchanged
    assert _bracket_range(0.85 * 45, 1.15 * 45, 45.0) == (0.85 * 45, 1.15 * 45)
    assert _bracket_range(0.22, 0.32, 0.27) == (0.22, 0.32)
    # a range that does NOT bracket the base (clamp collapsed to one side) -> ±20%
    lo, hi = _bracket_range(1.0, 1.0, 3.758e6)          # degenerate clamp
    assert lo < 3.758e6 < hi
    lo, hi = _bracket_range(0.9, 1.0, 5.0)              # base 5 outside [0.9,1.0]
    assert lo < 5.0 < hi
    # negative base still brackets
    lo, hi = _bracket_range(0.0, 0.0, -2.5)
    assert lo < -2.5 < hi


def test_scale_factor_and_enum_killed():
    from model_annotator.input_discovery import is_scale_factor, genuineness_score
    # the D19 bug: a 1,000,000 'Factor' cell must never be a driver
    assert is_scale_factor("Factor", 1_000_000.0) is True
    assert is_scale_factor("Units in thousands", 1000.0) is True
    assert is_scale_factor("CellCo Price per KWh", 45.0) is False
    s_factor = genuineness_score("Factor", "#,##0", 1e6, True, True, False, True, True)
    s_price = genuineness_score("CellCo Price per KWh", "#,##0", 45.0, True, True, False, True, True)
    assert s_price > s_factor and s_factor < 3.0            # factor sinks below real inputs


# --- driver naming: placeholder / banner / date / denomination are not labels --
def test_junk_label_filter():
    from model_annotator.sensitivity import _is_junk_label
    for junk in ["N/A", "n/a", "-", "—", "TBD", "0", "12", "45%",
                 "Carbice Confidential - For Internal Use Only", "Proprietary & Confidential",
                 "06/16/2026", "16-Jun-2026", "$000", "$MM", "in thousands",
                 "2026-2030 Operating Plan", "Projected P&L for Carbice", "Business Plan"]:
        assert _is_junk_label(junk), f"{junk!r} should be junk"
    for real in ["Space & Defense", "Revenue Growth %", "Data & Power", "Gross Margin %",
                 "Machinery and equipment", "SAFE", "Income Statement", "R&D"]:
        assert not _is_junk_label(real), f"{real!r} should be a real label"


# --- units: a bare "$000" denomination line below the title = thousands --------
def test_denomination_token_detected_below_title():
    import types
    from model_annotator import structure as S
    FMT = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'
    grid = {(2, 3): "Projected P&L for Carbice 2025-2030",
            (4, 2): "2026-2030 Operating Plan",
            (5, 2): "$000 ",              # the denomination line, one cell, row 5
            (7, 2): "Carbice Confidential - For Internal Use Only"}

    class _C:
        def __init__(self, v, f=FMT):
            self.value = v
            self.number_format = f

    class _SD:
        name = "Income Statement"
        max_row = 24
        max_col = 30

        def cell(self, r, c):
            return _C(grid[(r, c)]) if (r, c) in grid else None
        def iter_cells(self):
            for (r, c), v in grid.items():
                yield r, c, _C(v)
    u = S.detect_units(_SD())
    assert u.scale == 1000.0 and "thousand" in u.label.lower()
    # a genuinely whole-units sheet (no denomination token) stays scale 1
    grid.pop((5, 2))
    assert S.detect_units(_SD()).scale == 1.0


# --- period axis: recover an interleaved annual series (annual totals among Qs) -
def test_interleaved_annual_axis_recovered():
    import types
    from model_annotator import structure as S
    class _C:
        def __init__(self, v, f="General"):
            self.value = v
            self.number_format = f
    sd = types.SimpleNamespace(name="Income Statement")
    # a real layout: 2025 | Q1'26 Q2'26 Q3'26 Q4'26 | 2026 | 2027 | 2028 | 2029 | 2030
    line = [(9, 6, _C(2025)), (9, 7, None),
            (9, 8, _C("Q1 2026")), (9, 10, _C("Q2 2026")), (9, 12, _C("Q3 2026")),
            (9, 14, _C("Q4 2026")), (9, 16, _C(2026)), (9, 26, _C(2027)),
            (9, 28, _C(2028)), (9, 30, _C(2029)), (9, 32, _C(2030))]
    axes = S._interleaved_axes(sd, line, S.Orientation.periods_in_columns)
    annual = [ax for _s, ax in axes
              if [p for p in ax.periods] == ["2025", "2026", "2027", "2028", "2029", "2030"]]
    assert annual, f"expected the 2025-2030 annual series, got {[list(a.periods) for _s, a in axes]}"
    # the annual columns must be the real total columns (6,16,26,28,30,32), not the quarters
    cols = [ref.split("!")[-1] for ref in annual[0].header_cells]
    assert cols[0].startswith("F") and cols[1].startswith("P")   # col 6 = F, col 16 = P


# --- sensitivity cube: driver keys MUST be unique (JS maGet/maEval invariant) -
def test_cube_driver_keys_unique():
    """Every cube driver needs a unique key. Colliding keys make the front-end
    maGet() read the wrong driver's base, which drifts the base eval and
    compounds slopes into nonsense (the -59.7M base / billions tornado bug).
    Runs against the real YPlasma model when present (recompute cube)."""
    import os, pytest
    F = os.path.expanduser("~/Downloads/YPlasma_Financial Plan_20260507.xlsx")
    if not os.path.exists(F):
        pytest.skip("YPlasma model not present")
    from model_annotator import annotate
    r = annotate(F, no_llm=True, write_outputs=False)
    cubes = [t.cube for t in r.sensitivities if getattr(t, "cube", None)]
    assert cubes, "expected a recompute cube for YPlasma"
    for cube in cubes:
        keys = [d["key"] for d in cube["drivers"]]
        assert len(keys) == len(set(keys)), f"duplicate cube driver keys: {keys}"
        for d in cube["drivers"]:
            kk = [c["key"] for c in d.get("children", [])]
            assert len(kk) == len(set(kk)), f"duplicate child keys under {d['key']}"


# --- 2.1 captions conditional on the actual pattern -------------------------
def test_rising_only_when_data_rises():
    rising = {"2026": 0.1, "2027": 0.2, "2028": 0.3, "2029": 0.4, "2030": 0.5, "2031": 0.6}
    flat = {p: 0.1 for p in P}
    assert M._rising(rising, P)
    assert not M._rising(flat, P)
