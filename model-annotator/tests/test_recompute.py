"""The minimal Excel evaluator must compute the arithmetic the models use, and
must reject anything it can't handle (so the sensitivity falls back rather than
showing a wrong number)."""
import pytest

from model_annotator.recompute import _CELL, Unsupported, _Parser, _tokenize


def ev(formula, cells=None, ranges=None):
    cells = cells or {}
    ranges = ranges or {}
    return _Parser(_tokenize(formula),
                   lambda r: cells.get(r, 0.0),
                   lambda r: ranges.get(r, [])).parse()


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


def test_unsupported_function_raises():
    with pytest.raises(Unsupported):
        ev("XLOOKUP(A1,B:B,C:C)")
    with pytest.raises(Unsupported):
        ev('"text"&A1', cells={"A1": 1})


def test_two_letter_column_range_parses():
    # regression: AR7:AR11 must NOT be read as "sheet A, cell R7:AR11"
    m = _CELL.match("AR7:AR11")
    assert m and m.group("nq") is None
    assert m.group("col") == "AR" and m.group("row") == "7"
    assert m.group("col2") == "AR" and m.group("row2") == "11"
    # sheet names still parse, and only when followed by '!'
    m2 = _CELL.match("'Sheet 1'!B12")
    assert m2 and m2.group("sq") == "Sheet 1" and m2.group("col") == "B"
    m3 = _CELL.match("DASHBOARD!H17")
    assert m3 and m3.group("nq") == "DASHBOARD" and m3.group("col") == "H"
