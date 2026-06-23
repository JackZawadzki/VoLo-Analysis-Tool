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


# --- 2.1 captions conditional on the actual pattern -------------------------
def test_rising_only_when_data_rises():
    rising = {"2026": 0.1, "2027": 0.2, "2028": 0.3, "2029": 0.4, "2030": 0.5, "2031": 0.6}
    flat = {p: 0.1 for p in P}
    assert M._rising(rising, P)
    assert not M._rising(flat, P)
