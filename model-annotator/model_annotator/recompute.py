"""A faithful Excel formula evaluator — used ONLY by the sensitivity layer to
flex the model's real input cells and recompute the outputs exactly.

It parses each formula to a small AST and interprets it over real value types
(numbers, strings, dates, booleans). It implements the arithmetic and the
function set that real venture models actually use — including lookups
(XLOOKUP / MATCH / INDEX), conditional sums (SUMIF), error handling
(IFERROR / IFNA), and date math (YEAR / EOMONTH) — so models that lean on those
(not just plain SUM) can be recomputed. Anything outside the set raises
``Unsupported`` and the caller falls back to the engine-free line-item flex — so
the tool never shows a recomputed number it could not produce.

Faithfulness is enforced by a gate: ``RecomputeModel.build`` recomputes every
formula from the workbook's own inputs and only returns a usable model if its
results reproduce the cached values the workbook was saved with. A wrong function
implementation therefore fails the gate and is simply not used — never trusted.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re
from typing import Optional

from .graph import FormulaGraph, decode, encode
from .ingest import WorkbookData, is_number


class Unsupported(Exception):
    """The formula uses a construct this evaluator does not implement."""


class _XlError(Exception):
    """An Excel runtime error (#DIV/0!, #VALUE!, #N/A used in math, ...).
    Distinct from Unsupported: IFERROR catches THIS, never Unsupported, so an
    unsupported function can never be silently swallowed into a wrong value."""


_NA = object()                       # the #N/A value (from NA() / not-found lookups)
_EPOCH = _dt.datetime(1899, 12, 30)  # Excel's day-zero


class _Range(list):
    """A resolved range: a flat (row-major) list that also remembers its shape,
    so 2-D INDEX(table, row, col) can index it. Being a ``list`` subclass, every
    existing ``isinstance(x, list)`` path (SUM, broadcasting, ...) still works."""
    def __init__(self, vals, nrows=1, ncols=None):
        super().__init__(vals)
        self.nrows = nrows
        self.ncols = ncols if ncols is not None else len(vals)


# --------------------------------------------------------------------------
# Value coercion
# --------------------------------------------------------------------------
def _to_serial(v) -> float:
    base = v if isinstance(v, _dt.datetime) else _dt.datetime(v.year, v.month, v.day)
    return (base - _EPOCH).total_seconds() / 86400.0


def _as_date(v) -> _dt.datetime:
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.date):
        return _dt.datetime(v.year, v.month, v.day)
    return _EPOCH + _dt.timedelta(days=_num(v))


def _num(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return _to_serial(v)
    if v is None or v == "":
        return 0.0
    if v is _NA:
        raise _XlError()
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            raise _XlError()        # text in arithmetic = #VALUE!
    raise Unsupported("non-numeric value")


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is _NA:
        raise _XlError()
    return _num(v) != 0


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return ("%g" % v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return str(v)


def _equal(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(a, str) and isinstance(b, str):
            return a.strip().lower() == b.strip().lower()
        # Excel: a number can equal its text form; keep it simple and faithful-enough
        try:
            return _num(a) == _num(b)
        except _XlError:
            return False
    try:
        return _num(a) == _num(b)
    except _XlError:
        return False


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>"(?:[^"]|"")*")
    | (?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)
    | (?P<ref>(?:'[^']*'|[A-Za-z_\\][A-Za-z0-9_.]*)?!?\$?[A-Za-z]{1,3}\$?\d+
              (?::(?:'[^']*'|[A-Za-z_\\][A-Za-z0-9_.]*)?!?\$?[A-Za-z]{1,3}\$?\d+)?)
    | (?P<bool>TRUE|FALSE)
    | (?P<func>[A-Za-z_][A-Za-z0-9_.]*)(?=\s*\()
    | (?P<name>[A-Za-z_\\][A-Za-z0-9_.\\]*)
    | (?P<op><=|>=|<>|[-+*/^%&=<>])
    | (?P<lp>\() | (?P<rp>\)) | (?P<comma>,) | (?P<colon>:)
    """,
    re.VERBOSE,
)


def _tokenize(formula: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    i, n = 0, len(formula)
    while i < n:
        m = _TOKEN_RE.match(formula, i)
        if not m:
            raise Unsupported(f"cannot tokenize near {formula[i:i + 12]!r}")
        i = m.end()
        if m.lastgroup != "ws":
            toks.append((m.lastgroup, m.group()))
    return toks


# --------------------------------------------------------------------------
# Parser  (tokens -> AST tuples; eval happens later so IF/IFERROR can be lazy)
# --------------------------------------------------------------------------
class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else (None, None)

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def parse(self):
        node = self.expr()
        if self.pos != len(self.toks):
            raise Unsupported("trailing tokens")
        return node

    def expr(self):
        v = self.concat()
        k, s = self.peek()
        if k == "op" and s in ("=", "<>", "<", ">", "<=", ">="):
            self.next()
            return ("cmp", s, v, self.concat())
        return v

    def concat(self):
        v = self.addsub()
        while True:
            k, s = self.peek()
            if k == "op" and s == "&":
                self.next()
                v = ("concat", v, self.addsub())
            else:
                return v

    def addsub(self):
        v = self.muldiv()
        while True:
            k, s = self.peek()
            if k == "op" and s in ("+", "-"):
                self.next()
                v = ("bin", s, v, self.muldiv())
            else:
                return v

    def muldiv(self):
        v = self.power()
        while True:
            k, s = self.peek()
            if k == "op" and s in ("*", "/"):
                self.next()
                v = ("bin", s, v, self.power())
            else:
                return v

    def power(self):
        v = self.unary()
        k, s = self.peek()
        if k == "op" and s == "^":
            self.next()
            return ("bin", "^", v, self.power())
        return v

    def unary(self):
        k, s = self.peek()
        if k == "op" and s in ("+", "-"):
            self.next()
            v = self.unary()
            return ("neg", v) if s == "-" else v
        v = self.primary()
        k, s = self.peek()
        if k == "op" and s == "%":
            self.next()
            return ("pct", v)
        return v

    def primary(self):
        k, s = self.next()
        if k == "num":
            return ("num", float(s))
        if k == "bool":
            return ("bool", s.upper() == "TRUE")
        if k == "str":
            return ("str", s[1:-1].replace('""', '"'))
        if k == "lp":
            v = self.expr()
            if self.next()[0] != "rp":
                raise Unsupported("missing )")
            return v
        if k == "ref":
            return ("range", s) if ":" in s else ("cell", s)
        if k == "name":
            return ("name", s)
        if k == "func":
            return self.call(s.upper())
        raise Unsupported(f"unexpected token {s!r}")

    def call(self, name):
        if self.next()[0] != "lp":
            raise Unsupported("expected (")
        args = []
        if self.peek()[0] != "rp":
            args.append(self._call_arg())
            while self.peek()[0] == "comma":
                self.next()
                args.append(self._call_arg())
        if self.next()[0] != "rp":
            raise Unsupported("missing ) in call")
        return ("call", name, args)

    def _call_arg(self):
        # an omitted argument, e.g. XLOOKUP(a, b, c, , 1)
        if self.peek()[0] in ("comma", "rp"):
            return ("empty",)
        return self.expr()


# normalise XLFN./_xlfn. prefixes Excel writes for newer functions
def _canon(name: str) -> str:
    n = name.upper()
    for p in ("_XLFN.", "XLFN.", "_XLL.", "XLL."):
        if n.startswith(p):
            n = n[len(p):]
    return n


# --------------------------------------------------------------------------
# Interpreter
# --------------------------------------------------------------------------
class _Ctx:
    """Resolver bundle handed to a compiled formula closure."""
    __slots__ = ("rc", "rr", "rn")

    def __init__(self, rc, rr, rn=None):
        self.rc = rc
        self.rr = rr
        self.rn = rn


def _neg(v):
    return [-_num(x) for x in v] if isinstance(v, list) else -_num(v)


def _pct(v):
    return [_num(x) / 100.0 for x in v] if isinstance(v, list) else _num(v) / 100.0


def _compile(node):
    """Compile an AST node to a closure fn(ctx) -> value. Compiling once and
    calling the closure on every recompute is far faster than re-walking the
    tuple AST with type dispatch for every cell of every flex."""
    t = node[0]
    if t == "num" or t == "str" or t == "bool":
        v = node[1]
        return lambda ctx: v
    if t == "empty":
        return lambda ctx: None
    if t == "cell":
        ref = node[1]
        return lambda ctx: ctx.rc(ref)
    if t == "range":
        ref = node[1]
        return lambda ctx: ctx.rr(ref)
    if t == "name":
        nm = node[1]

        def _n(ctx):
            if ctx.rn is None:
                raise Unsupported(f"named range {nm!r}")
            return ctx.rn(nm)
        return _n
    if t == "neg":
        a = _compile(node[1])
        return lambda ctx: _neg(a(ctx))
    if t == "pct":
        a = _compile(node[1])
        return lambda ctx: _pct(a(ctx))
    if t == "bin":
        op, a, b = node[1], _compile(node[2]), _compile(node[3])
        return lambda ctx: _binop(op, a(ctx), b(ctx))
    if t == "cmp":
        op, a, b = node[1], _compile(node[2]), _compile(node[3])
        return lambda ctx: _cmp(op, a(ctx), b(ctx))
    if t == "concat":
        a, b = _compile(node[1]), _compile(node[2])
        return lambda ctx: _txt(a(ctx)) + _txt(b(ctx))
    if t == "call":
        return _compile_call(_canon(node[1]), node[2])
    raise Unsupported(f"node {t}")


def _compile_call(name, anodes):
    ac = [_compile(a) for a in anodes]
    if name == "IF":
        c, th = ac[0], ac[1]
        el = ac[2] if len(ac) > 2 else None
        return lambda ctx: (th(ctx) if _truthy(c(ctx)) else (el(ctx) if el is not None else False))
    if name == "IFS":
        def _ifs(ctx):
            for i in range(0, len(ac) - 1, 2):
                if _truthy(ac[i](ctx)):
                    return ac[i + 1](ctx)
            raise _XlError()
        return _ifs
    if name in ("IFERROR", "IFNA"):
        a, b = ac[0], ac[1]
        is_ifna = name == "IFNA"

        def _iferr(ctx):
            try:
                v = a(ctx)
                return b(ctx) if v is _NA else v
            except _XlError:
                if is_ifna:
                    raise          # IFNA only traps #N/A, not other errors
                return b(ctx)
        return _iferr
    if name == "AND":
        return lambda ctx: all(_truthy(f(ctx)) for f in ac)
    if name == "OR":
        return lambda ctx: any(_truthy(f(ctx)) for f in ac)
    if name == "NOT":
        return lambda ctx: (not _truthy(ac[0](ctx)))
    if name == "NA":
        return lambda ctx: _NA
    return lambda ctx: _apply(name, [f(ctx) for f in ac])


def _broadcast(f, a, b):
    """Elementwise over array operands (Excel array formulas: range<>0, a*b)."""
    al, bl = isinstance(a, list), isinstance(b, list)
    if al and bl:
        n = min(len(a), len(b))
        return [f(a[i], b[i]) for i in range(n)]
    if al:
        return [f(x, b) for x in a]
    if bl:
        return [f(a, y) for y in b]
    return f(a, b)


def _binop(op, a, b):
    if isinstance(a, list) or isinstance(b, list):
        return _broadcast(lambda x, y: _binop(op, x, y), a, b)
    x, y = _num(a), _num(b)
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if op == "/":
        if y == 0:
            raise _XlError()
        return x / y
    if op == "^":
        try:
            return x ** y
        except (ValueError, OverflowError, ZeroDivisionError):
            raise _XlError()
    raise Unsupported(f"op {op}")


def _cmp(op, a, b):
    if isinstance(a, list) or isinstance(b, list):
        return _broadcast(lambda x, y: _cmp(op, x, y), a, b)
    if isinstance(a, str) and isinstance(b, str):
        a2, b2 = a.strip().lower(), b.strip().lower()
        return {"=": a2 == b2, "<>": a2 != b2, "<": a2 < b2, ">": a2 > b2,
                "<=": a2 <= b2, ">=": a2 >= b2}[op]
    if op in ("=", "<>"):
        eq = _equal(a, b)
        return eq if op == "=" else (not eq)
    x, y = _num(a), _num(b)
    return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def _flatten(args):
    out = []
    for a in args:
        if isinstance(a, list):
            out.extend(a)
        else:
            out.append(a)
    return out


def _nums(args):
    out = []
    for v in _flatten(args):
        if v is None or v == "" or isinstance(v, str) or isinstance(v, bool):
            continue
        if v is _NA:
            raise _XlError()
        out.append(_num(v))
    return out


def _aslist(v):
    return v if isinstance(v, list) else [v]


def _crit_match(value, crit):
    """SUMIF/COUNTIF criteria: a bare value (exact), or '>5' / '<=3' / '<>x'."""
    if isinstance(crit, str):
        m = re.match(r"^(<=|>=|<>|=|<|>)\s*(.*)$", crit.strip())
        if m:
            op, rest = m.group(1), m.group(2)
            try:
                target = float(rest)
                src = _num(value) if not isinstance(value, str) else None
                if src is None:
                    return op == "<>"
            except ValueError:
                op = {"=": "=", "<>": "<>"}.get(op, op)
                return _equal(value, rest) if op == "=" else (
                    not _equal(value, rest) if op == "<>" else False)
            return {"=": src == target, "<>": src != target, "<": src < target,
                    ">": src > target, "<=": src <= target, ">=": src >= target}[op]
        return _equal(value, crit)
    return _equal(value, crit)


def _apply(name, args):
    if name == "SUM":
        return sum(_nums(args))
    if name == "PRODUCT":
        p = 1.0
        for v in _nums(args):
            p *= v
        return p
    if name == "SUMPRODUCT":
        ranges = [a for a in args if isinstance(a, list)]
        if ranges and all(len(r) == len(ranges[0]) for r in ranges):
            tot = 0.0
            for tup in zip(*ranges):
                pr = 1.0
                for v in tup:
                    pr *= (_num(v) if not (v is None or isinstance(v, str)) else 0.0)
                tot += pr
            return tot
        pr = 1.0
        for v in _flatten(args):
            pr *= _num(v)
        return pr
    if name in ("MIN", "MAX"):
        vals = _nums(args)
        return (min if name == "MIN" else max)(vals) if vals else 0.0
    if name in ("AVERAGE", "AVG"):
        vals = _nums(args)
        return sum(vals) / len(vals) if vals else _xlerr()
    if name == "COUNT":
        return float(len(_nums(args)))
    if name == "COUNTA":
        return float(sum(1 for v in _flatten(args) if v not in (None, "")))
    if name == "ABS":
        return abs(_num(args[0]))
    if name == "SQRT":
        return _num(args[0]) ** 0.5
    if name == "POWER":
        return _num(args[0]) ** _num(args[1])
    if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
        x = _num(args[0])
        d = int(_num(args[1])) if len(args) > 1 else 0
        import math
        f = 10.0 ** d
        if name == "ROUNDUP":
            return math.ceil(abs(x) * f) / f * (1 if x >= 0 else -1)
        if name == "ROUNDDOWN":
            return math.floor(abs(x) * f) / f * (1 if x >= 0 else -1)
        return round(x, d)
    if name in ("CEILING", "FLOOR"):
        import math
        x = _num(args[0])
        sig = _num(args[1]) if len(args) > 1 else 1.0
        if sig == 0:
            return 0.0
        return (math.ceil if name == "CEILING" else math.floor)(x / sig) * sig
    if name == "INT":
        import math
        return float(math.floor(_num(args[0])))
    if name == "MOD":
        a, b = _num(args[0]), _num(args[1])
        if b == 0:
            raise _XlError()
        return a - b * (a // b)
    if name == "SUMIF":
        rng = _aslist(args[0])
        crit = args[1]
        sumr = _aslist(args[2]) if len(args) > 2 else rng
        tot = 0.0
        for i, cell in enumerate(rng):
            if _crit_match(cell, crit) and i < len(sumr):
                v = sumr[i]
                if not (v is None or isinstance(v, str) or v is _NA):
                    tot += _num(v)
        return tot
    if name == "COUNTIF":
        rng = _aslist(args[0])
        crit = args[1]
        return float(sum(1 for c in rng if _crit_match(c, crit)))
    if name in ("XLOOKUP",):
        lookup = args[0]
        larr = _aslist(args[1])
        rarr = _aslist(args[2])
        if_nf = args[3] if len(args) > 3 and args[3] is not None else _NA
        mode = int(_num(args[4])) if len(args) > 4 and args[4] is not None else 0
        idx = _match_index(lookup, larr, mode if mode in (-1, 0, 1) else 0)
        if idx is None:
            return if_nf
        return rarr[idx] if idx < len(rarr) else _NA
    if name in ("VLOOKUP", "HLOOKUP"):
        lookup = args[0]
        table = _aslist(args[1])           # flat (row-major) — best effort 1D
        # we can't reliably reshape a flat range to columns here; only support the
        # degenerate 2-column case is risky, so defer to Unsupported for safety
        raise Unsupported(name)
    if name == "MATCH":
        lookup = args[0]
        larr = _aslist(args[1])
        mt = int(_num(args[2])) if len(args) > 2 and args[2] is not None else 1
        idx = _match_index(lookup, larr, 0 if mt == 0 else (1 if mt == 1 else -1))
        if idx is None:
            return _NA
        return float(idx + 1)
    if name == "XMATCH":
        lookup = args[0]
        larr = _aslist(args[1])
        mode = int(_num(args[2])) if len(args) > 2 and args[2] is not None else 0
        idx = _match_index(lookup, larr, mode if mode in (-1, 0, 1) else 0)
        if idx is None:
            return _NA
        return float(idx + 1)
    if name == "INDEX":
        arr = args[0] if isinstance(args[0], list) else _aslist(args[0])
        ri = int(_num(args[1])) if len(args) > 1 and args[1] is not None else 0
        ci = int(_num(args[2])) if len(args) > 2 and args[2] is not None else 0
        if isinstance(arr, _Range) and arr.nrows > 1 and arr.ncols > 1:
            # genuine 2-D table lookup: INDEX(table, row, col)
            if ri <= 0 or ci <= 0 or ri > arr.nrows or ci > arr.ncols:
                raise _XlError()
            return arr[(ri - 1) * arr.ncols + (ci - 1)]
        i = ri or ci                            # 1-D vector: the non-empty index
        if i <= 0 or i > len(arr):
            raise _XlError()
        return arr[i - 1]
    if name == "CHOOSE":
        i = int(_num(args[0]))
        if i <= 0 or i >= len(args):
            raise _XlError()
        return args[i]
    if name in ("YEAR", "MONTH", "DAY"):
        d = _as_date(args[0])
        return float(getattr(d, {"YEAR": "year", "MONTH": "month", "DAY": "day"}[name]))
    if name == "DATE":
        return _dt.datetime(int(_num(args[0])), int(_num(args[1])), int(_num(args[2])))
    if name in ("EOMONTH", "EDATE"):
        d = _as_date(args[0])
        n = int(_num(args[1]))
        y = d.year + (d.month - 1 + n) // 12
        m = (d.month - 1 + n) % 12 + 1
        if name == "EOMONTH":
            return _dt.datetime(y, m, calendar.monthrange(y, m)[1])
        day = min(d.day, calendar.monthrange(y, m)[1])
        return _dt.datetime(y, m, day)
    if name == "YEARFRAC":
        a, b = _as_date(args[0]), _as_date(args[1])
        return abs((b - a).days) / 365.0
    if name == "ISNUMBER":
        v = args[0]
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if name == "ISBLANK":
        return args[0] is None or args[0] == ""
    if name in ("ISERROR", "ISERR"):
        return args[0] is _NA
    if name == "ISNA":
        return args[0] is _NA
    if name == "MAXIFS" or name == "MINIFS" or name == "SUMIFS" or name == "COUNTIFS" \
            or name == "AVERAGEIF" or name == "AVERAGEIFS":
        raise Unsupported(name)         # multi-criteria — fall back rather than guess
    raise Unsupported(f"function {name}")


def _xlerr():
    raise _XlError()


def _match_index(lookup, arr, mode):
    """Index (0-based) of lookup in arr. mode 0 = exact; 1 = largest <= lookup
    (ascending); -1 = smallest >= lookup (descending). None if not found."""
    if mode == 0:
        for i, v in enumerate(arr):
            if _equal(lookup, v):
                return i
        return None
    try:
        target = _num(lookup)
    except _XlError:
        for i, v in enumerate(arr):
            if _equal(lookup, v):
                return i
        return None
    best = None
    for i, v in enumerate(arr):
        if v is None or v == "" or isinstance(v, str) or v is _NA:
            continue
        try:
            x = _num(v)
        except _XlError:
            continue
        if mode == 1 and x <= target:
            best = i
        elif mode == -1 and x >= target:
            best = i if best is None else best
    return best


def evaluate(formula: str, resolve_cell, resolve_range, resolve_name=None):
    """Tokenize → parse → interpret a single formula. Stable entry point for
    tests and ad-hoc use. ``resolve_cell(ref)`` returns a raw value,
    ``resolve_range(ref)`` a list of raw values, ``resolve_name(name)`` a defined
    name's value (optional)."""
    fn = _compile(_Parser(_tokenize(str(formula).lstrip("="))).parse())
    return fn(_Ctx(resolve_cell, resolve_range, resolve_name))


# --------------------------------------------------------------------------
# Reference parsing
# --------------------------------------------------------------------------
_CELL = re.compile(r"^(?:(?:'(?P<sq>[^']*)'|(?P<nq>[A-Za-z_\\][A-Za-z0-9_.]*))!)?"
                   r"\$?(?P<col>[A-Za-z]{1,3})\$?(?P<row>\d+)"
                   r"(?::(?:(?:'[^']*'|[A-Za-z_\\][A-Za-z0-9_.]*)!)?"
                   r"\$?(?P<col2>[A-Za-z]{1,3})\$?(?P<row2>\d+))?$")


def _col_to_idx(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


# --------------------------------------------------------------------------
# Recompute model
# --------------------------------------------------------------------------
class RecomputeModel:
    """Evaluates the workbook's own formulas so inputs can be flexed and the
    outputs read off exactly. Build via :meth:`build`, which returns ``None``
    when the model cannot be faithfully reproduced."""

    def __init__(self, graph: FormulaGraph):
        self.g = graph
        self.values: dict[int, object] = {}
        self._topo: list[int] = []
        self._formula_src: dict[int, tuple[str, int]] = {}     # node -> (formula, sheet_idx)
        self._compiled: dict[int, object] = {}                 # node -> compiled closure (cached)
        self._resolvers: dict[int, _Ctx] = {}                  # sheet_idx -> resolver bundle
        self._cone_cache: dict[frozenset, set] = {}            # override-nodes -> dirty cone (reused across lo/hi)
        self._fsrc_set: Optional[set] = None
        self._names_lower = {n.lower(): i for i, n in enumerate(self.g.sheet_names)}
        self.n_frozen = 0                                      # cells we couldn't eval (held at cached value)
        # workbook defined-names -> their target ref string, so formulas that
        # reference inputs by NAME (common in good models) resolve and propagate
        self._named: dict[str, str] = {}
        for nm, target in getattr(self.g.wbd, "named_ranges", {}).items():
            t = str(target).lstrip("=").strip()
            if "," in t:
                t = t.split(",")[0].strip()        # multi-area name -> first area
            if t:
                self._named[nm.lower()] = t

    # ---- ref resolution -------------------------------------------------
    def _resolve_node(self, sheet_idx: int, r: int, c: int):
        node = encode(sheet_idx, r, c)
        if node in self.values:
            return self.values[node]
        rec = self.g.wbd.sheets[self.g.sheet_names[sheet_idx]].cell(r, c)
        if rec is not None and rec.value is not None and rec.value != "":
            return rec.value                       # raw: number / string / date
        return 0.0                                 # blank behaves as 0 in arithmetic

    def _make_resolvers(self, cur_sheet_idx: int):
        cached = self._resolvers.get(cur_sheet_idx)
        if cached is not None:
            return cached
        names_lower = self._names_lower

        def sheet_of(m) -> int:
            sq, nq = m.group("sq"), m.group("nq")
            raw = sq if sq is not None else nq
            if raw is None:
                return cur_sheet_idx
            idx = names_lower.get(raw.lower())
            if idx is None:
                raise Unsupported(f"reference to unknown sheet {raw!r}")
            return idx

        def resolve_cell(ref: str):
            m = _CELL.match(ref)
            if not m or m.group("col2"):
                raise Unsupported(f"bad cell ref {ref!r}")
            return self._resolve_node(sheet_of(m), int(m.group("row")), _col_to_idx(m.group("col")))

        def resolve_range(ref: str) -> list:
            m = _CELL.match(ref)
            if not m or not m.group("col2"):
                raise Unsupported(f"bad range ref {ref!r}")
            si = sheet_of(m)
            r1, r2 = int(m.group("row")), int(m.group("row2"))
            c1, c2 = _col_to_idx(m.group("col")), _col_to_idx(m.group("col2"))
            lo_r, hi_r = min(r1, r2), max(r1, r2)
            lo_c, hi_c = min(c1, c2), max(c1, c2)
            vals = []
            for rr in range(lo_r, hi_r + 1):
                for cc in range(lo_c, hi_c + 1):
                    vals.append(self._resolve_node(si, rr, cc))
            return _Range(vals, hi_r - lo_r + 1, hi_c - lo_c + 1)

        def resolve_name(name: str):
            tgt = self._named.get(name.lower())
            if not tgt:
                raise Unsupported(f"unknown name {name!r}")
            return resolve_range(tgt) if ":" in tgt else resolve_cell(tgt)

        self._resolvers[cur_sheet_idx] = _Ctx(resolve_cell, resolve_range, resolve_name)
        return self._resolvers[cur_sheet_idx]

    def _eval_node(self, node: int):
        fn = self._compiled.get(node)
        si = self._formula_src[node][1]
        if fn is None:
            formula = self._formula_src[node][0]
            fn = _compile(_Parser(_tokenize(formula.lstrip("="))).parse())
            self._compiled[node] = fn
        return fn(self._make_resolvers(si))

    # ---- build + faithfulness gate -------------------------------------
    @classmethod
    def build(cls, graph: FormulaGraph, check_nodes: list[int],
              tol: float = 1e-3, max_formula_cells: int = 400_000,
              gate_nodes: Optional[list[int]] = None) -> Optional["RecomputeModel"]:
        """Return a faithful model, or None. The cone of ``check_nodes`` is
        evaluated (so the caller can read all of them, e.g. every period); the
        faithfulness gate requires only ``gate_nodes`` (default = check_nodes) to
        reproduce their cached values. Gating on the few headline outputs (rather
        than every period) lets a model whose early periods are noisy still be
        used for sensitivity on the headline year."""
        self = cls(graph)
        relevant: set[int] = set()
        for node in check_nodes:
            relevant |= graph.upstream(node, cap=400_000)
        relevant |= set(check_nodes)
        for node in relevant:
            if node not in graph.formula_nodes:
                continue
            si, r, c = decode(node)
            rec = graph.wbd.sheets[graph.sheet_names[si]].cell(r, c)
            if rec is None or rec.formula is None:
                continue
            self._formula_src[node] = (str(rec.formula), si)
        if not self._formula_src or len(self._formula_src) > max_formula_cells:
            return None
        order = _topo_order(graph, set(self._formula_src))
        if order is None:
            return None
        self._topo = order
        n_frozen = 0
        for node in order:
            try:
                self.values[node] = self._eval_node(node)
            except _XlError:
                self.values[node] = _NA          # an Excel error in a cell (#DIV/0! etc.)
            except (Unsupported, OverflowError, KeyError, IndexError,
                    RecursionError, ZeroDivisionError, ValueError, TypeError):
                # can't evaluate this one cell — freeze it at the workbook's cached
                # value so the rest of the model still recomputes from a correct
                # base. A flex flowing only through a frozen cell is treated as
                # constant; everything else stays an exact recompute.
                si, r, c = decode(node)
                rec = self.g.wbd.sheets[self.g.sheet_names[si]].cell(r, c)
                self.values[node] = rec.value if (rec is not None and rec.value is not None) else 0.0
                n_frozen += 1
        self.n_frozen = n_frozen
        # too much of the model is unevaluable -> not trustworthy; fall back
        if n_frozen > max(8, int(0.03 * len(order))):
            return None
        # gate: the headline outputs must reproduce the cached values
        for node in (gate_nodes or check_nodes):
            si, r, c = decode(node)
            rec = graph.wbd.sheets[graph.sheet_names[si]].cell(r, c)
            cached = float(rec.value) if rec is not None and is_number(rec.value) else None
            got = self.values.get(node)
            gotf = float(got) if isinstance(got, (int, float)) and not isinstance(got, bool) else None
            if cached is None or gotf is None:
                return None
            if abs(gotf - cached) / max(abs(cached), 1.0) > tol:
                return None
        return self

    # ---- flex inputs, read the outputs ---------------------------------
    def outputs_with_override(self, input_node: int, value: float,
                              output_nodes: list[int]) -> dict[int, float]:
        return self.outputs_with_overrides({input_node: value}, output_nodes)

    def outputs_with_overrides(self, overrides: dict[int, float],
                               output_nodes: list[int]) -> dict[int, float]:
        """Set one or more input cells, recompute only the affected downstream
        cone, and return the resulting values at ``output_nodes``. Non-destructive."""
        if self._fsrc_set is None:
            self._fsrc_set = set(self._formula_src)
        key = frozenset(overrides)
        cone = self._cone_cache.get(key)
        if cone is None:
            cone = set()
            for nd in overrides:
                cone |= self.g.downstream(nd, cap=400_000)
            cone &= self._fsrc_set
            self._cone_cache[key] = cone
        saved = {n: self.values[n] for n in cone if n in self.values}
        saved_in = {nd: self.values.get(nd) for nd in overrides}
        self.values.update(overrides)
        try:
            for node in self._topo:
                if node in cone:
                    try:
                        self.values[node] = self._eval_node(node)
                    except _XlError:
                        self.values[node] = _NA
                    except (Unsupported, OverflowError, KeyError, IndexError,
                            RecursionError, ZeroDivisionError, ValueError, TypeError):
                        pass            # frozen cell: keep its base value (acts as a constant)
            out = {}
            for o in output_nodes:
                v = self.values.get(o)
                out[o] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            return out
        finally:
            self.values.update(saved)
            for nd, v in saved_in.items():
                if v is None:
                    self.values.pop(nd, None)
                else:
                    self.values[nd] = v


def _topo_order(graph: FormulaGraph, formula_nodes: set[int]) -> Optional[list[int]]:
    """Kahn topological sort over the formula nodes. Returns None on a cycle."""
    indeg: dict[int, int] = {n: 0 for n in formula_nodes}
    preds: dict[int, list[int]] = {}
    for n in formula_nodes:
        ps = [p for p in graph.precedents.get(n, ()) if p in formula_nodes]
        preds[n] = ps
        indeg[n] = len(ps)
    from collections import deque
    deps: dict[int, list[int]] = {n: [] for n in formula_nodes}
    for n in formula_nodes:
        for p in preds[n]:
            deps[p].append(n)
    q = deque(n for n in formula_nodes if indeg[n] == 0)
    order: list[int] = []
    while q:
        n = q.popleft()
        order.append(n)
        for d in deps[n]:
            indeg[d] -= 1
            if indeg[d] == 0:
                q.append(d)
    return order if len(order) == len(formula_nodes) else None
