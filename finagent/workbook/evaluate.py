"""Independent evaluation of the workbook's formulas (Phase 4d).

A small evaluator for exactly the formula subset the workbook writes, independent of the code
that computed the model: it reads the formula strings from the saved workbook and computes
them. It is strict where Excel is lenient: a blank cell or text used in arithmetic raises
(Excel would read a blank as 0, which would hide a GAP), and an unsupported function raises.

Supported: numbers, "strings", TRUE/FALSE, cell references (optionally sheet-qualified and
absolute) and ranges, + - * / unary minus, comparisons (= <> < <= > >=), parentheses, and the
functions SUM, IF, MAX, MIN, ABS, AND, OR, ISNUMBER, INDEX, MATCH (exact), COUNTIF (equality).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import column_index_from_string, get_column_letter

FUNCTIONS = {"SUM", "IF", "MAX", "MIN", "ABS", "AND", "OR", "ISNUMBER", "INDEX", "MATCH",
             "COUNTIF"}

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Z]{1,3}\$?\d+
            (?::\$?[A-Z]{1,3}\$?\d+)?)
  | (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<func>[A-Z][A-Z0-9.]*)\(
  | (?P<bool>TRUE|FALSE)\b
  | (?P<op><=|>=|<>|[-+*/(),=<>&])
""", re.VERBOSE)


class FormulaError(ValueError):
    """A formula the evaluator refuses: blank or text in arithmetic, unknown function, cycle."""


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


def tokenize(formula: str) -> list[Token]:
    tokens, pos = [], 0
    while pos < len(formula):
        m = _TOKEN.match(formula, pos)
        if not m:
            raise FormulaError(f"cannot read formula at {formula[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        assert kind is not None
        if kind != "ws":
            tokens.append(Token(kind, m.group(kind)))
    return tokens


def _split_ref(text: str, sheet: str) -> tuple[str, str]:
    if "!" in text:
        name, cell = text.rsplit("!", 1)
        if name.startswith("'"):
            name = name[1:-1].replace("''", "'")
        return name, cell.replace("$", "")
    return sheet, text.replace("$", "")


def _cells(a: str, b: str) -> list[str]:
    ca, ra = re.match(r"([A-Z]+)(\d+)", a).groups()  # type: ignore[union-attr]
    cb, rb = re.match(r"([A-Z]+)(\d+)", b).groups()  # type: ignore[union-attr]
    c1, c2 = sorted((column_index_from_string(ca), column_index_from_string(cb)))
    r1, r2 = sorted((int(ra), int(rb)))
    return [f"{get_column_letter(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


class Range(list):
    """Values of a range, row-major."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        raise FormulaError(f"{where}: blank cell used in arithmetic")
    raise FormulaError(f"{where}: text {value!r} used in arithmetic")


class Evaluator:
    """Evaluates every formula of a workbook; `overrides` replace input cell values (e.g. the
    scenario selector)."""

    def __init__(self, workbook: Workbook,
                 overrides: Mapping[tuple[str, str], Any] | None = None) -> None:
        self.wb = workbook
        self.overrides = dict(overrides or {})
        self.cache: dict[tuple[str, str], Any] = {}
        self.active: set[tuple[str, str]] = set()

    def cell(self, sheet: str, coord: str) -> Any:
        key = (sheet, coord)
        if key in self.overrides:
            return self.overrides[key]
        if key in self.cache:
            return self.cache[key]
        c = self.wb[sheet][coord]
        raw = c.value
        if c.data_type == "f" and isinstance(raw, str):
            if key in self.active:
                raise FormulaError(f"circular reference at {sheet}!{coord}")
            self.active.add(key)
            try:
                value = _Parser(self, sheet, raw[1:], f"{sheet}!{coord}").parse()
            finally:
                self.active.discard(key)
            self.cache[key] = value
            return value
        return raw

    def evaluate_all(self) -> dict[tuple[str, str], Any]:
        found = {}
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.data_type == "f" and isinstance(c.value, str):
                        found[(ws.title, c.coordinate)] = self.cell(ws.title, c.coordinate)
        return found


class _Parser:
    def __init__(self, ev: Evaluator, sheet: str, formula: str, where: str) -> None:
        self.ev, self.sheet, self.where = ev, sheet, where
        self.tokens = tokenize(formula)
        self.i = 0

    def parse(self) -> Any:
        value = self.comparison()
        if self.i != len(self.tokens):
            raise FormulaError(f"{self.where}: unexpected {self.tokens[self.i].text!r}")
        if isinstance(value, Range):
            raise FormulaError(f"{self.where}: a range is not a value")
        return value

    # ---- token helpers
    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, text: str | None = None) -> Token:
        token = self.peek()
        if token is None or (text is not None and token.text != text):
            raise FormulaError(f"{self.where}: expected {text!r}")
        self.i += 1
        return token

    # ---- grammar
    def comparison(self) -> Any:
        left = self.additive()
        token = self.peek()
        if token is not None and token.text in ("=", "<>", "<", "<=", ">", ">="):
            self.i += 1
            right = self.additive()
            return _compare(token.text, left, right, self.where)
        return left

    def additive(self) -> Any:
        value = self.term()
        while (token := self.peek()) is not None and token.text in ("+", "-"):
            self.i += 1
            right = self.term()
            a, b = _number(value, self.where), _number(right, self.where)
            value = a + b if token.text == "+" else a - b
        return value

    def term(self) -> Any:
        value = self.unary()
        while (token := self.peek()) is not None and token.text in ("*", "/"):
            self.i += 1
            right = self.unary()
            a, b = _number(value, self.where), _number(right, self.where)
            if token.text == "/" and b == 0:
                raise FormulaError(f"{self.where}: division by zero")
            value = a * b if token.text == "*" else a / b
        return value

    def unary(self) -> Any:
        token = self.peek()
        if token is not None and token.text in ("-", "+"):
            self.i += 1
            value = _number(self.unary(), self.where)
            return -value if token.text == "-" else value
        return self.primary()

    def primary(self) -> Any:
        token = self.take()
        if token.kind == "number":
            return float(token.text)
        if token.kind == "string":
            return token.text[1:-1].replace('""', '"')
        if token.kind == "bool":
            return token.text == "TRUE"
        if token.kind == "ref":
            return self.reference(token.text)
        if token.kind == "func":
            return self.function(token.text)
        if token.text == "(":
            value = self.comparison()
            self.take(")")
            return value
        raise FormulaError(f"{self.where}: unexpected {token.text!r}")

    def reference(self, text: str) -> Any:
        sheet, cell = _split_ref(text, self.sheet)
        if ":" in cell:
            a, b = cell.split(":")
            return Range(self.ev.cell(sheet, c) for c in _cells(a, b))
        return self.ev.cell(sheet, cell)

    def arguments(self) -> list[Any]:
        args: list[Any] = []
        if self.peek() is not None and self.peek().text == ")":  # type: ignore[union-attr]
            self.i += 1
            return args
        while True:
            args.append(self.comparison())
            token = self.take()
            if token.text == ")":
                return args
            if token.text != ",":
                raise FormulaError(f"{self.where}: expected , or )")

    def lazy_if(self) -> Any:
        condition = self.comparison()
        self.take(",")
        start = self.i
        if _truth(condition, self.where):
            value = self.comparison()
            if self.peek() is not None and self.peek().text == ",":  # type: ignore[union-attr]
                self.i += 1
                self.skip_argument()
        else:
            self.skip_argument()
            value = False
            if self.peek() is not None and self.peek().text == ",":  # type: ignore[union-attr]
                self.i += 1
                value = self.comparison()
        del start
        self.take(")")
        return value

    def skip_argument(self) -> None:
        depth = 0
        while (token := self.peek()) is not None:
            if token.text == "(" or token.kind == "func":
                depth += 1
            elif token.text == ")":
                if depth == 0:
                    return
                depth -= 1
            elif token.text == "," and depth == 0:
                return
            self.i += 1
        raise FormulaError(f"{self.where}: unbalanced parentheses")

    def function(self, name: str) -> Any:
        if name not in FUNCTIONS:
            raise FormulaError(f"{self.where}: unsupported function {name}")
        if name == "IF":  # lazy: the branch not taken is never evaluated (as in Excel)
            return self.lazy_if()
        args = self.arguments()
        flat = lambda: [v for a in args for v in (a if isinstance(a, Range) else [a])]  # noqa
        if name == "SUM":
            return sum(_number(v, self.where) for v in flat())
        if name in ("MAX", "MIN"):
            values = [_number(v, self.where) for v in flat()]
            return max(values) if name == "MAX" else min(values)
        if name == "ABS":
            return abs(_number(args[0], self.where))
        if name == "AND":
            return all(_truth(v, self.where) for v in flat())
        if name == "OR":
            return any(_truth(v, self.where) for v in flat())
        if name == "ISNUMBER":
            v = args[0]
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if name == "INDEX":
            values = args[0]
            n = int(_number(args[1], self.where))
            if not isinstance(values, Range) or not 1 <= n <= len(values):
                raise FormulaError(f"{self.where}: INDEX out of range")
            return values[n - 1]
        if name == "MATCH":
            needle, values = args[0], args[1]
            if len(args) != 3 or _number(args[2], self.where) != 0:
                raise FormulaError(f"{self.where}: only exact MATCH(..., 0) is supported")
            for n, v in enumerate(values, start=1):
                if _equal(v, needle):
                    return float(n)
            raise FormulaError(f"{self.where}: MATCH found no {needle!r}")
        if name == "COUNTIF":
            values, needle = args[0], args[1]
            return float(sum(1 for v in values if _equal(v, needle)))
        raise FormulaError(f"{self.where}: unsupported function {name}")


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    return a == b


def _truth(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    return _number(value, where) != 0


def _compare(op: str, a: Any, b: Any, where: str) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        if op == "=":
            return _equal(a, b)
        if op == "<>":
            return not _equal(a, b)
        raise FormulaError(f"{where}: ordering comparison on text")
    x, y = _number(a, where), _number(b, where)
    return {"=": x == y, "<>": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
