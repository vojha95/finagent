"""Structure and magnitude features of statement rows for M2 (blueprint 8.4, model ladder).

Every feature comes from the visible row and the visible rows of the same statement: layout
(indent, total styling, position), the size of the row's displayed figure against the largest
figure on the statement, and a subtotal detector. No tags, concepts or tagged values (rule 7).

Subtotal detector, on the displayed figures of the rows above the row (statement order):
- sum: the row equals the sum of the k rows just above it (k >= 2);
- difference: the row equals the topmost of those k rows minus the others;
- pair: the row equals an earlier row plus or minus the row just above it (gross profit =
  revenue - cost of revenue with the cost components in between; total assets = current +
  non-current);
- of_subtotals: the row equals the sum of the last m >= 2 rows already detected as subtotals
  plus any rows after the last of them (net change in cash = operating + investing + financing
  [+ exchange-rate effect]).
A match must hold in every amount column where all the figures involved are shown, within half a
displayed unit per figure.

Figures are read as displayed (unscaled, parentheses negative, a dash is 0). A row without a
readable figure, or a per-share or share-count row (not in the table's unit, 8.5), has no
magnitude: its magnitude features are NaN, never 0 (rule 4); LightGBM treats NaN as missing.
Floats are used because these are model inputs (rule 5).
"""

import math
from collections.abc import Sequence
from decimal import Decimal

from finagent.document.normalize import is_scale_exempt, read_display
from finagent.records import RowInstance

MAX_SPAN = 15  # rows above a row that the subtotal detector may combine
MAX_SUBTOTALS = 4  # earlier subtotals a total of subtotals may add
FEATURES = (
    "indent", "is_total", "is_bold", "position", "relative_position",
    "statement_is", "statement_bs", "statement_cf",
    "has_figure", "scale_exempt", "negative", "log_share_of_max",
    "subtotal_sum", "subtotal_difference", "subtotal_pair", "subtotal_of_subtotals",
    "subtotal_span",
)

Figures = Sequence[Decimal | None]


def displayed_figures(row: RowInstance) -> list[Decimal | None]:
    """Signed displayed figure per amount column (percentage sub-columns skipped, BC11);
    None for a blank or unreadable cell."""
    figures: list[Decimal | None] = []
    for column, text in enumerate(row.cell_texts):
        if not row.is_amount_column(column):
            continue
        figure = read_display(text)
        if figure is None or figure.number is None:
            figures.append(None)
        else:
            figures.append(-figure.number if figure.negative else figure.number)
    return figures


def _decimals(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _close(total: Decimal, combined: Decimal, figures: Sequence[Decimal]) -> bool:
    """Equal within the rounding of the displayed figures: half a unit per figure."""
    unit = Decimal(1).scaleb(-max(_decimals(v) for v in figures))
    return abs(total - combined) <= unit * Decimal(len(figures)) / 2


def _matches(row: Figures, parts: Sequence[Figures], signs: Sequence[int]) -> bool:
    """row = sum(sign x part) in every column where the row and every part have figures (at
    least one such column); the row is not 0 there and at least two parts are non-zero."""
    columns = [c for c in range(len(row))
               if row[c] is not None and all(c < len(p) and p[c] is not None for p in parts)]
    if not columns or row[columns[0]] == 0:
        return False
    if sum(p[columns[0]] != 0 for p in parts) < 2:
        return False
    for c in columns:
        values = [p[c] for p in parts]
        combined = sum((s * v for s, v in zip(signs, values)), Decimal(0))
        if not _close(row[c], combined, [row[c], *values]):
            return False
    return True


def subtotal_span(figures: Sequence[Figures], index: int, difference: bool = False,
                  max_span: int = MAX_SPAN) -> int:
    """Smallest k >= 2 such that row `index` equals the sum of the k rows just above it
    (difference: the topmost of them minus the others); 0 when there is none."""
    for k in range(2, min(max_span, index) + 1):
        signs = [1] + [-1 if difference else 1] * (k - 1)
        if _matches(figures[index], figures[index - k:index], signs):
            return k
    return 0


def pair_span(figures: Sequence[Figures], index: int, max_span: int = MAX_SPAN) -> int:
    """Smallest distance d >= 2 such that row `index` equals row index-d plus or minus the row
    just above it; 0 when there is none."""
    if index < 2:
        return 0
    above = figures[index - 1]
    for d in range(2, min(max_span, index) + 1):
        earlier = figures[index - d]
        if _matches(figures[index], [earlier, above], [1, 1]) \
                or _matches(figures[index], [earlier, above], [1, -1]):
            return d
    return 0


def total_of_subtotals(figures: Sequence[Figures], index: int, subtotals: Sequence[int],
                       max_count: int = MAX_SUBTOTALS) -> int:
    """Number m >= 2 of the last earlier subtotal rows that, with the rows after the last of
    them, add up to row `index`; 0 when there is none. `subtotals` are earlier row indices."""
    earlier = [s for s in subtotals if s < index]
    if len(earlier) < 2:
        return 0
    trailing = list(figures[earlier[-1] + 1:index])
    for m in range(2, min(max_count, len(earlier)) + 1):
        parts = [figures[s] for s in earlier[-m:]] + trailing
        if _matches(figures[index], parts, [1] * len(parts)):
            return m
    return 0


def statement_features(rows: Sequence[RowInstance]) -> list[dict[str, float]]:
    """Features for the rows of one statement of one filing, in the order given."""
    order = sorted(range(len(rows)), key=lambda i: rows[i].position)
    exempt = [is_scale_exempt(rows[i]) for i in order]
    figures = [displayed_figures(rows[i]) for i in order]
    first = [None if x else next((f for f in fs if f is not None), None)
             for fs, x in zip(figures, exempt)]
    largest = max((abs(f) for f in first if f is not None and f != 0), default=None)
    last_position = max((r.position for r in rows), default=0)
    subtotals: list[int] = []
    features: list[dict[str, float]] = [{} for _ in rows]
    for rank, i in enumerate(order):
        row, value = rows[i], first[rank]
        if exempt[rank]:  # per-share and share-count rows are not in the table's unit
            spans = (0, 0, 0, 0)
        else:
            spans = (subtotal_span(figures, rank), subtotal_span(figures, rank, difference=True),
                     pair_span(figures, rank), total_of_subtotals(figures, rank, subtotals))
        if any(spans):
            subtotals.append(rank)
        share = (math.log10(float(abs(value) / largest))
                 if value is not None and value != 0 and largest else math.nan)
        features[i] = {
            "indent": float(row.indent), "is_total": float(row.is_total),
            "is_bold": float(row.is_bold), "position": float(row.position),
            "relative_position": row.position / last_position if last_position else 0.0,
            "statement_is": float(row.statement == "IS"),
            "statement_bs": float(row.statement == "BS"),
            "statement_cf": float(row.statement == "CF"),
            "has_figure": float(any(f is not None for f in figures[rank])),
            "scale_exempt": float(exempt[rank]),
            "negative": math.nan if value is None else float(value < 0),
            "log_share_of_max": share,
            "subtotal_sum": float(spans[0] > 0), "subtotal_difference": float(spans[1] > 0),
            "subtotal_pair": float(spans[2] > 0), "subtotal_of_subtotals": float(spans[3] > 0),
            "subtotal_span": float(min((s for s in spans[:3] if s), default=0)),
        }
    return features


def row_features(rows: Sequence[RowInstance]) -> list[list[float]]:
    """One feature vector (FEATURES order) per row; rows are grouped by filing and statement,
    so a batch may hold several filings."""
    groups: dict[tuple[str, str], list[int]] = {}
    for i, row in enumerate(rows):
        groups.setdefault((row.accession, row.statement), []).append(i)
    vectors: list[list[float]] = [[] for _ in rows]
    for indices in groups.values():
        for i, values in zip(indices, statement_features([rows[i] for i in indices])):
            vectors[i] = [values[name] for name in FEATURES]
    return vectors
