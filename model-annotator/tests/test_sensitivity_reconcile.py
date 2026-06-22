"""Regression: EBITDA sensitivity must not double-count overlapping line items.

When the mapper (esp. the LLM path) tags both a subtotal row (e.g. "TOTAL
Salaries") and its components as opex line items, naively summing them all
double-counts opex and corrupts the base EBITDA and every tornado bar. The
build reconciles each group (revenue / COGS / opex) against the model's own
total: it decomposes into line items only when they sum to that total,
otherwise it falls back to the single total cell.
"""
from model_annotator.mapping import LineItem
from model_annotator.sensitivity import build_sensitivities

PERIODS = ["2025", "2026", "2027"]


def _item(index, label, vals, canonical, instance=None):
    return LineItem(
        sheet="P&L", index=index, label=label, depth=0, section="",
        periods=PERIODS, coords=[(index, 2), (index, 3), (index, 4)],
        values=list(vals), percentish=False, n_numeric=3, n_formula=3,
        canonical_id=canonical, instance=instance or label,
    )


class _Wbd:
    sheets = {}


class _Axis:
    periods = PERIODS


class _Units:
    label = "$"


class _Structure:
    units = {"P&L": _Units()}

    def primary_axis(self, sheet):
        return _Axis()


class _Mapping:
    primary_sheet = "P&L"

    def __init__(self, items):
        self._items = items

    def get(self, canonical, sheet=None):
        for it in self._items:
            if it.canonical_id == canonical:
                return it
        return None

    def get_all(self, canonical):
        return [it for it in self._items if it.canonical_id == canonical]


def _ebitda(mapping):
    tors = build_sensitivities(_Wbd(), _Structure(), mapping, {}, periods=PERIODS)
    t = [x for x in tors if x.output_key == "terminal_ebitda"]
    return t[0] if t else None


def test_clean_components_are_decomposed():
    # opex line items sum exactly to the opex total -> keep them as drivers
    items = [
        _item(1, "Revenue", [1000, 1200, 1500], "revenue_total"),
        _item(2, "COGS", [300, 360, 450], "cogs_total"),
        _item(3, "Salaries", [120, 140, 160], "opex_component"),
        _item(4, "R&D", [80, 90, 100], "opex_component"),
        _item(5, "Marketing", [50, 60, 70], "opex_component"),
        _item(6, "Total Operating Expenses", [250, 290, 330], "opex_total"),
    ]
    t = _ebitda(_Mapping(items))
    assert t is not None
    # EBITDA(2027) = 1500 - 450 - 330 = 720
    assert abs(t.output_base - 720) < 1e-6
    labels = {d.label.split(" (")[0] for d in t.drivers}
    assert {"Salaries", "R&D", "Marketing"} <= labels        # decomposed
    assert "Total opex" not in labels


def test_overlapping_subtotal_collapses_to_total():
    # a "TOTAL Salaries" subtotal is mapped alongside its parts: Salaries + R&D
    # already equal it, so components (160+100+160+70=490) overshoot the real
    # opex total (330). The build must drop the decomposition and use the total.
    items = [
        _item(1, "Revenue", [1000, 1200, 1500], "revenue_total"),
        _item(2, "COGS", [300, 360, 450], "cogs_total"),
        _item(3, "TOTAL Salaries", [200, 230, 260], "opex_component"),  # subtotal
        _item(4, "Salaries", [120, 140, 160], "opex_component"),
        _item(5, "R&D", [80, 90, 100], "opex_component"),
        _item(6, "Marketing", [50, 60, 70], "opex_component"),
        _item(7, "Total Operating Expenses", [250, 290, 330], "opex_total"),
    ]
    t = _ebitda(_Mapping(items))
    assert t is not None
    # must reconcile to the real EBITDA: 1500 - 450 - 330 = 720, NOT
    # 1500 - 450 - 490 = 560 (the double-counted value)
    assert abs(t.output_base - 720) < 1e-6
    opex_labels = [d.label for d in t.drivers if d.coef < 0 and "COGS" not in d.label]
    assert len(opex_labels) == 1 and "opex" in opex_labels[0].lower()
