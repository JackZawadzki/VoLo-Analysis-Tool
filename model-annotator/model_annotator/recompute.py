"""A minimal, faithful Excel formula evaluator — used ONLY by the sensitivity
layer to flex the model's real input cells and recompute the outputs exactly.

It is deliberately small: it evaluates the arithmetic the model actually uses
(`+ - * / ^ %`, parentheses, cell/range references across sheets, and a short
allow-list of functions). Anything outside that allow-list raises ``Unsupported``
and the caller falls back to the engine-free line-item flex — so the tool never
shows a recomputed number it could not produce.

Faithfulness is enforced by a gate: ``RecomputeModel.build`` recomputes every
formula from the workbook's own inputs and only returns a usable model if its
results reproduce the cached values the workbook was saved with. If the evaluator
cannot reproduce the model, it is not used at all.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from .graph import FormulaGraph, decode, encode
from .ingest import WorkbookData, is_number

# functions we can evaluate exactly; anything else => fall back, never guess
_FUNCS = {"SUM", "ROUND", "ROUNDUP", "ROUNDDOWN", "MIN", "MAX", "AVERAGE", "AVG",
          "ABS", "IF", "IFERROR", "AND", "OR", "NOT", "SQRT", "POWER", "SUMPRODUCT"}


class Unsupported(Exception):
    """The formula uses a construct this evaluator does not implement."""


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>"(?:[^"]|"")*")
    | (?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)
    | (?P<ref>(?:'[^']*'|[A-Za-z_][A-Za-z0-9_.]*)?!?\$?[A-Za-z]{1,3}\$?\d+
              (?::(?:'[^']*'|[A-Za-z_][A-Za-z0-9_.]*)?!?\$?[A-Za-z]{1,3}\$?\d+)?)
    | (?P<func>[A-Za-z_][A-Za-z0-9_.]*)(?=\s*\()
    | (?P<bool>TRUE|FALSE)
    | (?P<op><=|>=|<>|[-+*/^%&=<>])
    | (?P<lp>\() | (?P<rp>\)) | (?P<comma>,) | (?P<colon>:)
    """,
    re.VERBOSE,
)


def _tokenize(formula: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    i = 0
    n = len(formula)
    while i < n:
        m = _TOKEN_RE.match(formula, i)
        if not m:
            raise Unsupported(f"cannot tokenize near {formula[i:i+12]!r}")
        i = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        toks.append((kind, m.group()))
    return toks


# --------------------------------------------------------------------------
# Recursive-descent evaluator
# --------------------------------------------------------------------------
class _Parser:
    def __init__(self, toks, resolve_cell, resolve_range):
        self.toks = toks
        self.pos = 0
        self.resolve_cell = resolve_cell      # (ref_str) -> float
        self.resolve_range = resolve_range    # (ref_str) -> list[float]

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else (None, None)

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def parse(self) -> float:
        v = self.expr()
        if self.pos != len(self.toks):
            raise Unsupported("trailing tokens")
        return v

    # comparison (lowest precedence, for IF conditions)
    def expr(self) -> float:
        v = self.addsub()
        k, s = self.peek()
        if k == "op" and s in ("=", "<>", "<", ">", "<=", ">="):
            self.next()
            r = self.addsub()
            return float({"=": v == r, "<>": v != r, "<": v < r, ">": v > r,
                          "<=": v <= r, ">=": v >= r}[s])
        return v

    def addsub(self) -> float:
        v = self.muldiv()
        while True:
            k, s = self.peek()
            if k == "op" and s in ("+", "-"):
                self.next()
                r = self.muldiv()
                v = v + r if s == "+" else v - r
            elif k == "op" and s == "&":          # text concat — numbers only here
                raise Unsupported("string concatenation")
            else:
                return v

    def muldiv(self) -> float:
        v = self.power()
        while True:
            k, s = self.peek()
            if k == "op" and s in ("*", "/"):
                self.next()
                r = self.power()
                if s == "/":
                    v = v / r if r != 0 else 0.0
                else:
                    v = v * r
            else:
                return v

    def power(self) -> float:
        v = self.unary()
        k, s = self.peek()
        if k == "op" and s == "^":
            self.next()
            return v ** self.power()
        return v

    def unary(self) -> float:
        k, s = self.peek()
        if k == "op" and s in ("+", "-"):
            self.next()
            v = self.unary()
            return -v if s == "-" else v
        v = self.primary()
        k, s = self.peek()
        if k == "op" and s == "%":
            self.next()
            return v / 100.0
        return v

    def primary(self) -> float:
        k, s = self.next()
        if k == "num":
            return float(s)
        if k == "bool":
            return 1.0 if s.upper() == "TRUE" else 0.0
        if k == "str":
            raise Unsupported("string literal in arithmetic")
        if k == "lp":
            v = self.expr()
            if self.next()[0] != "rp":
                raise Unsupported("missing )")
            return v
        if k == "ref":
            return float(self.resolve_cell(s))
        if k == "func":
            return self.call(s.upper())
        raise Unsupported(f"unexpected token {s!r}")

    def call(self, name: str) -> float:
        if name not in _FUNCS:
            raise Unsupported(f"function {name}")
        if self.next()[0] != "lp":
            raise Unsupported("expected (")
        args: list = []          # each arg is ("scalar", v) or ("range", [vals])
        if self.peek()[0] != "rp":
            args.append(self._arg())
            while self.peek()[0] == "comma":
                self.next()
                args.append(self._arg())
        if self.next()[0] != "rp":
            raise Unsupported("missing ) in call")
        return self._apply(name, args)

    def _arg(self):
        # a range reference as a whole argument (SUM(A1:B5)), else a scalar expr
        k, s = self.peek()
        if k == "ref" and ":" in s:
            self.next()
            return ("range", self.resolve_range(s))
        return ("scalar", self.expr())

    def _flat(self, args) -> list[float]:
        out: list[float] = []
        for kind, v in args:
            if kind == "range":
                out.extend(v)
            else:
                out.append(v)
        return out

    def _apply(self, name: str, args) -> float:
        if name == "SUM":
            return sum(self._flat(args))
        if name == "SUMPRODUCT":
            ranges = [v for k, v in args if k == "range"]
            if ranges and all(len(r) == len(ranges[0]) for r in ranges):
                return sum(_prod(vals) for vals in zip(*ranges))
            return _prod([v for _, v in args])
        if name in ("MIN", "MAX"):
            vals = self._flat(args)
            return (min if name == "MIN" else max)(vals) if vals else 0.0
        if name in ("AVERAGE", "AVG"):
            vals = self._flat(args)
            return sum(vals) / len(vals) if vals else 0.0
        if name == "ABS":
            return abs(self._flat(args)[0])
        if name == "SQRT":
            return self._flat(args)[0] ** 0.5
        if name == "POWER":
            f = self._flat(args)
            return f[0] ** f[1]
        if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
            f = self._flat(args)
            digits = int(f[1]) if len(f) > 1 else 0
            return round(f[0], digits)
        if name == "IF":
            f = [v for _, v in args]
            cond = f[0]
            return f[1] if cond else (f[2] if len(f) > 2 else 0.0)
        if name == "IFERROR":
            f = [v for _, v in args]
            return f[0]
        if name == "AND":
            return float(all(self._flat(args)))
        if name == "OR":
            return float(any(self._flat(args)))
        if name == "NOT":
            return float(not self._flat(args)[0])
        raise Unsupported(f"function {name}")


def _prod(vals):
    p = 1.0
    for v in vals:
        p *= v
    return p


# --------------------------------------------------------------------------
# Recompute model
# --------------------------------------------------------------------------
# the sheet name, if present, MUST be followed by "!" — otherwise a 2-letter
# column range like AR7:AR11 gets misread as "sheet A, cell R7:AR11". The second
# endpoint of a range may carry its own sheet qualifier ('P&L'!Y7:'P&L'!AA7);
# we assume it's the same sheet as the first and ignore it.
_CELL = re.compile(r"^(?:(?:'(?P<sq>[^']*)'|(?P<nq>[A-Za-z_][A-Za-z0-9_.]*))!)?"
                   r"\$?(?P<col>[A-Za-z]{1,3})\$?(?P<row>\d+)"
                   r"(?::(?:(?:'[^']*'|[A-Za-z_][A-Za-z0-9_.]*)!)?"
                   r"\$?(?P<col2>[A-Za-z]{1,3})\$?(?P<row2>\d+))?$")


def _col_to_idx(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


class RecomputeModel:
    """Evaluates the workbook's own formulas so a single input can be flexed and
    the outputs read off exactly. Build via :meth:`build`, which returns ``None``
    when the model cannot be faithfully reproduced."""

    def __init__(self, graph: FormulaGraph):
        self.g = graph
        self.values: dict[int, float] = {}
        self._topo: list[int] = []
        self._formula_src: dict[int, tuple[str, int]] = {}   # node -> (formula, sheet_idx)

    # ---- ref resolution -------------------------------------------------
    def _resolve_node(self, sheet_idx: int, r: int, c: int) -> float:
        node = encode(sheet_idx, r, c)
        if node in self.values:
            return self.values[node]
        si, rr, cc = sheet_idx, r, c
        name = self.g.sheet_names[si]
        rec = self.g.wbd.sheets[name].cell(rr, cc)
        if rec is not None and is_number(rec.value):
            return float(rec.value)
        return 0.0

    def _make_resolvers(self, cur_sheet_idx: int):
        names_lower = {n.lower(): i for i, n in enumerate(self.g.sheet_names)}

        def sheet_of(m) -> int:
            sq, nq = m.group("sq"), m.group("nq")
            raw = sq if sq is not None else nq
            if raw is None:
                return cur_sheet_idx
            idx = names_lower.get(raw.lower())
            if idx is None:
                raise Unsupported(f"reference to unknown sheet {raw!r}")
            return idx

        def resolve_cell(ref: str) -> float:
            m = _CELL.match(ref)
            if not m or m.group("col2"):
                raise Unsupported(f"bad cell ref {ref!r}")
            si = sheet_of(m)
            return self._resolve_node(si, int(m.group("row")), _col_to_idx(m.group("col")))

        def resolve_range(ref: str) -> list[float]:
            m = _CELL.match(ref)
            if not m or not m.group("col2"):
                raise Unsupported(f"bad range ref {ref!r}")
            si = sheet_of(m)
            r1, r2 = int(m.group("row")), int(m.group("row2"))
            c1, c2 = _col_to_idx(m.group("col")), _col_to_idx(m.group("col2"))
            vals = []
            for rr in range(min(r1, r2), max(r1, r2) + 1):
                for cc in range(min(c1, c2), max(c1, c2) + 1):
                    vals.append(self._resolve_node(si, rr, cc))
            return vals

        return resolve_cell, resolve_range

    def _eval_node(self, node: int) -> float:
        formula, si = self._formula_src[node]
        rc, rr = self._make_resolvers(si)
        toks = _tokenize(formula.lstrip("="))
        return _Parser(toks, rc, rr).parse()

    # ---- build + faithfulness gate -------------------------------------
    @classmethod
    def build(cls, graph: FormulaGraph, check_nodes: list[int],
              tol: float = 1e-3, max_formula_cells: int = 120_000) -> Optional["RecomputeModel"]:
        """Return a faithful model, or None. ``check_nodes`` are the output cells
        whose recomputed value must match the cached value for the model to be
        trusted. Only the formula cells that FEED the check nodes are evaluated —
        other sheets (which may use functions we can't handle) are irrelevant."""
        self = cls(graph)
        # restrict to the cells that actually feed the outputs we care about
        relevant: set[int] = set()
        for node in check_nodes:
            relevant |= graph.upstream(node, cap=200_000)
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
        # topological order over the relevant formula nodes
        order = _topo_order(graph, set(self._formula_src))
        if order is None:
            return None                       # a cycle — can't recompute safely
        self._topo = order
        # recompute everything from the cached inputs
        try:
            for node in order:
                self.values[node] = self._eval_node(node)
        except Unsupported:
            return None
        except (ZeroDivisionError, OverflowError, ValueError, KeyError):
            return None
        # gate: recomputed outputs must reproduce the cached values
        for node in check_nodes:
            si, r, c = decode(node)
            rec = graph.wbd.sheets[graph.sheet_names[si]].cell(r, c)
            cached = float(rec.value) if rec is not None and is_number(rec.value) else None
            got = self.values.get(node)
            if cached is None or got is None:
                return None
            denom = max(abs(cached), 1.0)
            if abs(got - cached) / denom > tol:
                return None
        return self

    # ---- flex one input, read the outputs ------------------------------
    def outputs_with_override(self, input_node: int, value: float,
                              output_nodes: list[int]) -> dict[int, float]:
        return self.outputs_with_overrides({input_node: value}, output_nodes)

    def outputs_with_overrides(self, overrides: dict[int, float],
                               output_nodes: list[int]) -> dict[int, float]:
        """Set one or more input cells, recompute only the affected downstream
        cone, and return the resulting values at ``output_nodes``. Non-destructive."""
        cone: set[int] = set()
        for nd in overrides:
            cone |= self.g.downstream(nd, cap=200_000)
        cone &= set(self._formula_src)
        saved = {n: self.values[n] for n in cone if n in self.values}
        saved_in = {nd: self.values.get(nd) for nd in overrides}
        self.values.update(overrides)
        try:
            for node in self._topo:          # global order keeps deps correct
                if node in cone:
                    self.values[node] = self._eval_node(node)
            return {o: self.values.get(o) for o in output_nodes}
        finally:
            self.values.update(saved)
            for nd, v in saved_in.items():
                if v is None:
                    self.values.pop(nd, None)
                else:
                    self.values[nd] = v


def _topo_order(graph: FormulaGraph, formula_nodes: set[int]) -> Optional[list[int]]:
    """Kahn topological sort of the formula nodes using precedent edges that fall
    inside the formula set. Returns None on a cycle."""
    indeg: dict[int, int] = {n: 0 for n in formula_nodes}
    preds: dict[int, list[int]] = {}
    for n in formula_nodes:
        ps = [p for p in graph.precedents.get(n, ()) if p in formula_nodes]
        preds[n] = ps
        indeg[n] = len(ps)
    from collections import deque
    q = deque(n for n in formula_nodes if indeg[n] == 0)
    order: list[int] = []
    deps: dict[int, list[int]] = {n: [] for n in formula_nodes}
    for n in formula_nodes:
        for p in preds[n]:
            deps[p].append(n)
    while q:
        n = q.popleft()
        order.append(n)
        for d in deps[n]:
            indeg[d] -= 1
            if indeg[d] == 0:
                q.append(d)
    if len(order) != len(formula_nodes):
        return None
    return order
