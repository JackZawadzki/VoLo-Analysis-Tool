"""The Excel evaluator must compute the arithmetic AND the lookup/date/error
functions real models use, and must reject anything it can't handle (so the
sensitivity falls back rather than showing a wrong number)."""
import datetime

import pytest

from model_annotator.recompute import _CELL, Unsupported, evaluate


def ev(formula, cells=None, ranges=None):
    cells = cells or {}
    ranges = ranges or {}
    return evaluate(formula, lambda r: cells.get(r, 0.0), lambda r: ranges.get(r, []))


def test_arithmetic_and_precedence():
    assert ev("1+2*3") == 7
    assert ev("(1+2)*3") == 9
    assert ev("2^3") == 8
    assert ev("-5+3") == -2
    assert ev("10%") == pytest.approx(0.1)
    assert ev("7/2") == pytest.approx(3.5)


def test_cell_and_range_refs():
    assert ev("A1*B1", cells={"A1": 4, "B1": 5}) == 20
    assert ev("SUM(A1:A3)", ranges={"A1:A3": [1, 2, 3]}) == 6
    assert ev("AVERAGE(A1:A2)", ranges={"A1:A2": [2, 4]}) == 3
    assert ev("SUM(X1:X3)+10", ranges={"X1:X3": [1, 1, 1]}) == 13


def test_functions():
    assert ev("ROUND(3.14159,2)") == pytest.approx(3.14)
    assert ev("MIN(3,1,2)") == 1
    assert ev("MAX(3,1,2)") == 3
    assert ev("ABS(-4)") == 4
    assert ev("IF(1,10,20)") == 10
    assert ev("IF(0,10,20)") == 20
    assert ev("IF(A1>5,1,0)", cells={"A1": 7}) == 1
    assert ev("PRODUCT(2,3,4)") == 24
    assert ev('SUMIF(A1:A3,">2",B1:B3)', ranges={"A1:A3": [1, 3, 5], "B1:B3": [10, 20, 30]}) == 50


def test_lookup_functions():
    # XLOOKUP exact match returns the parallel cell; not-found yields the default
    assert ev('XLOOKUP("b",A1:A3,B1:B3)',
              ranges={"A1:A3": ["a", "b", "c"], "B1:B3": [10, 20, 30]}) == 20
    assert ev('XLOOKUP("z",A1:A2,B1:B2,-1)',
              ranges={"A1:A2": ["a", "b"], "B1:B2": [1, 2]}) == -1
    assert ev("MATCH(30,A1:A3,0)", ranges={"A1:A3": [10, 20, 30]}) == 3
    assert ev("INDEX(A1:A3,2)", ranges={"A1:A3": [7, 8, 9]}) == 8


def test_iferror_traps_excel_errors_not_unsupported():
    assert ev("IFERROR(1/0, 99)") == 99           # #DIV/0! is trapped
    assert ev("IFERROR(A1, 99)", cells={"A1": 5}) == 5
    # IFERROR must NOT swallow a genuinely unsupported construct (else a wrong
    # value would pass the faithfulness gate)
    with pytest.raises(Unsupported):
        ev("IFERROR(VLOOKUP(1,A:B,2), 0)")


def test_date_functions():
    d = datetime.datetime(2028, 6, 15)
    assert ev("YEAR(A1)", cells={"A1": d}) == 2028
    eom = ev("EOMONTH(A1,0)", cells={"A1": d})
    assert isinstance(eom, datetime.datetime) and eom.day == 30


def test_unsupported_function_raises():
    with pytest.raises(Unsupported):
        ev("VLOOKUP(A1,B:C,2)")
    with pytest.raises(Unsupported):
        ev("SUMIFS(A:A,B:B,1)")
    with pytest.raises(Unsupported):
        ev("TOTALLYMADEUP(A1)", cells={"A1": 1})


def test_text_concat_supported():
    assert ev('"x"&A1', cells={"A1": 1}) == "x1"


def test_two_letter_column_range_parses():
    m = _CELL.match("AR7:AR11")
    assert m and m.group("nq") is None
    assert m.group("col") == "AR" and m.group("row") == "7"
    assert m.group("col2") == "AR" and m.group("row2") == "11"
    m2 = _CELL.match("'Sheet 1'!B12")
    assert m2 and m2.group("sq") == "Sheet 1" and m2.group("col") == "B"
    m3 = _CELL.match("DASHBOARD!H17")
    assert m3 and m3.group("nq") == "DASHBOARD" and m3.group("col") == "H"


def test_sumifs_datedif_column():
    # SUMIFS(sum_range, crit_range, crit): sum where criteria match
    assert ev('SUMIFS(B1:B3,A1:A3,">2")',
              ranges={"A1:A3": [1, 3, 5], "B1:B3": [10, 20, 30]}) == 50
    # single-cell ranges (the degenerate form real models use)
    assert ev("SUMIFS(A1,B1,C1)", cells={"A1": 7, "B1": 2, "C1": 2}) == 7
    assert ev("SUMIFS(A1,B1,C1)", cells={"A1": 7, "B1": 2, "C1": 3}) == 0
    # DATEDIF month arithmetic (the form that froze financing spines)
    assert ev('DATEDIF(DATE(2026,1,15),DATE(2026,7,15),"m")') == 6
    assert ev('DATEDIF(DATE(2026,1,1),DATE(2028,1,1),"y")') == 2
    assert ev('IFERROR(DATEDIF(DATE(2026,6,1),DATE(2026,1,1),"m"),0)') == 0
    # COLUMN(ref)/ROW(ref) resolve at compile time from the ref text
    assert ev("COLUMN(C33)") == 3
    assert ev("ROW(C33)") == 33
    assert ev("COLUMN(AB7)-COLUMN(C7)") == 28 - 3
