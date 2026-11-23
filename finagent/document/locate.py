"""Statement location in the clean document (blueprint 8.3 step 1; BC6).

Tables are scored by anchor phrases, position after the Item 8 heading and numeric density. A
caller may also pass a per-table count of tagged cells ({table_index: count}, computed outside
this package). That count is the only tag-derived input allowed here (rule 7): no concept
names, values or facts. With it, untagged tables are never candidates; without it, location
uses the visible wording alone.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from lxml import etree

from finagent.records import Statement

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"

# Anchor phrase groups per statement. A table scores one point per group it contains; the
# groups are general 10-K wording found across filers, not tied to any company (rule 3).
ANCHORS: dict[Statement, tuple[str, ...]] = {
    "IS": (
        r"total (net )?(sales|revenues?)|net sales|\brevenues?\b|\bsales\b",
        r"operating income|income from operations|operating (loss|profit|earnings)",
        r"income tax|taxes on (income|earnings)|tax (expense|provision)"
        r"|provision for (income )?taxes",
        r"net (income|loss|earnings)",
        r"diluted|assuming dilution",
    ),
    "BS": (
        r"current assets",
        r"total assets",
        r"current liabilities",
        r"(stock|share)(holders|owners)['’] (equity|deficit|investment)|total equity",
        r"total liabilities and|liabilities and (stock|share)\w*['’]? (equity|deficit|investment)"
        r"|liabilities and equity|total liabilities[a-z ,’']{0,80}(equity|investment|deficit)",
    ),
    "CF": (
        r"operating activities|\boperations\b",
        r"investing",
        r"financing",
        r"net cash|cash (generated|provided|used)",
        rf"(end|beginning) of (the )?(fiscal )?(period|year)|(ending|beginning) balances?"
        rf"|cash[a-z ,’'-]{{0,80}} at ({_MONTHS}) \d",
    ),
}

# The last anchor group of each statement closes it; a table that lacks it may continue in
# the next table (a statement split across pages).
MIN_ANCHOR_GROUPS = 4
# With tagged-cell counts, untagged tables are excluded first, so fewer wording groups suffice
# (e.g. an unclassified balance sheet has no current subtotals).
MIN_ANCHOR_GROUPS_TAGGED = 3
# Most tables one split statement may span when no single table reaches the threshold.
MAX_SPLIT_TABLES = 4

# Gap reason codes for a statement that is not located (the status is GAP, blueprint 6.1).
NO_TABLES = "NO_TABLES"
STATEMENTS_INCORPORATED_BY_REFERENCE = "STATEMENTS_INCORPORATED_BY_REFERENCE"
STATEMENT_NOT_LOCATED = "STATEMENT_NOT_LOCATED"

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_ITEM_8 = re.compile(r"^item\s*8\b", re.IGNORECASE)
_ITEM_8_ANYWHERE = re.compile(r"\bitem\s*8\b", re.IGNORECASE)
_BY_REFERENCE = re.compile(r"incorporated\s+(herein\s+)?by\s+reference", re.IGNORECASE)
_REFERENCE_WINDOW = 400  # characters after an "Item 8" mention searched for the wording
_NEXT_ITEM = re.compile(r"\bitem\s*(9|1[0-5])[a-c]?\b", re.IGNORECASE)


@dataclass(frozen=True)
class StatementTables:
    """Where one statement sits: its table indices in document order, and how it scored."""

    statement: Statement
    table_indices: tuple[int, ...]
    anchor_groups: int
    numbers: int


def local_name(element: etree._Element) -> str:
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def document_tables(root: etree._Element) -> list[etree._Element]:
    """Every table in document order (the same order the tag store indexes tables in)."""
    return [e for e in root.iter() if local_name(e) == "table"]


def table_text(table: etree._Element) -> str:
    return " ".join(" ".join(table.itertext()).split()).lower()


def anchor_groups(text: str, statement: Statement) -> int:
    return sum(bool(re.search(pattern, text)) for pattern in ANCHORS[statement])


def closes(text: str, statement: Statement) -> bool:
    return bool(re.search(ANCHORS[statement][-1], text))


def _outside_tables(element: etree._Element) -> bool:
    return not any(local_name(a) == "table" for a in element.iterancestors())


def item_8_table_start(root: etree._Element) -> int | None:
    """Index of the first table after the Item 8 heading, or None when no heading is found.

    The heading is a text block outside any table whose text begins with "Item 8"; the table
    of contents, which also names Item 8, sits inside a table and is ignored.
    """
    tables_seen = 0
    for element in root.iter():
        name = local_name(element)
        if name == "table":
            tables_seen += 1
            continue
        if name not in ("p", "div", "span", "b", "font"):
            continue
        if not _outside_tables(element):
            continue
        text = " ".join("".join(element.itertext()).split())
        if _ITEM_8.match(text) and len(text) < 200:
            return tables_seen
    return None


def _check_counts(counts: Mapping[int, int], n_tables: int) -> None:
    bad = [i for i in counts if not 0 <= i < n_tables]
    if bad:
        raise ValueError(
            f"tagged-cell counts name tables {bad[:5]} outside the document's {n_tables} tables"
        )


def _ranked_single(
    texts: list[str], statement: Statement, start: int | None
) -> list[tuple[int, bool, int, int]]:
    """Wording-only ranking (no counts): anchor groups, after Item 8, numeric density, earliest."""
    return sorted(
        (
            (anchor_groups(text, statement), start is not None and index >= start,
             len(_NUMBER.findall(text)), -index)
            for index, text in enumerate(texts)
        ),
        reverse=True,
    )


def _split_candidate(
    first: int, statement: Statement, texts: list[str], counts: Mapping[int, int]
) -> tuple[int, ...]:
    """A table plus the following tables of the same split statement, up to its closing line.

    Empty spacer tables are skipped. A following table must hold figures, at least one anchor
    group of the statement and tagged cells.
    """
    indices = [first]
    following = first
    while len(indices) < MAX_SPLIT_TABLES and not closes(
        " ".join(texts[i] for i in indices), statement
    ):
        following += 1
        if following >= len(texts):
            break
        if not texts[following]:
            continue
        if (
            not _NUMBER.search(texts[following])
            or anchor_groups(texts[following], statement) == 0
            or counts.get(following, 0) == 0
        ):
            break
        indices.append(following)
    return tuple(indices)


def _best_tagged(
    texts: list[str], statement: Statement, start: int | None, counts: Mapping[int, int]
) -> tuple[tuple[int, ...], int, int] | None:
    """(tables, anchor groups, numbers) of the best tagged candidate reaching the threshold.

    Untagged tables are excluded; candidates rank by anchor groups, then position after the
    Item 8 heading, then the earliest table. Tagged-cell counts filter, they do not rank: a
    quarterly table can hold more tagged cells than the statement itself. Single tables are
    tried first; a split statement only when no single table reaches the threshold.
    """
    tagged = [i for i in range(len(texts)) if counts.get(i, 0) > 0]
    for split in (False, True):
        best: tuple[tuple[int, bool, int], tuple[int, ...]] | None = None
        for index in tagged:
            indices = _split_candidate(index, statement, texts, counts) if split else (index,)
            text = " ".join(texts[i] for i in indices)
            key = (anchor_groups(text, statement), start is not None and index >= start, -index)
            if best is None or key > best[0]:
                best = (key, indices)
        if best is not None and best[0][0] >= MIN_ANCHOR_GROUPS_TAGGED:
            indices = best[1]
            numbers = len(_NUMBER.findall(" ".join(texts[i] for i in indices)))
            return indices, best[0][0], numbers
    return None


def _continue(
    located: StatementTables,
    texts: list[str],
    chosen: set[int],
    counts: Mapping[int, int] | None,
) -> StatementTables:
    """Append continuation tables until the closing anchor (8.3 step 3 merges them later).

    With counts, empty spacer tables are skipped and a continuation table must hold tagged
    cells; without them, the rule is the wording-only one.
    """
    indices = list(located.table_indices)
    following = indices[-1]
    while not closes(texts[indices[-1]], located.statement):
        following += 1
        if following >= len(texts) or following in chosen:
            break
        if counts is not None and not texts[following]:
            continue
        if not _NUMBER.search(texts[following]):
            break
        if counts is not None and counts.get(following, 0) == 0:
            break
        indices.append(following)
        chosen.add(following)
    return StatementTables(
        located.statement, tuple(indices), located.anchor_groups, located.numbers
    )


def locate_statements(
    root: etree._Element, tagged_cells: Mapping[int, int] | None = None
) -> dict[Statement, StatementTables]:
    """The best-scoring table for each statement, plus continuation tables.

    tagged_cells, when given, is {table index in this document: number of tagged cells}; it is
    checked against the document's tables (ValueError for an index out of range). A statement
    is absent from the result when no candidate reaches the threshold (MIN_ANCHOR_GROUPS
    without counts, MIN_ANCHOR_GROUPS_TAGGED with them); the caller treats that as a gap. A
    document with no tables locates nothing.
    """
    tables = document_tables(root)
    if not tables:
        return {}
    if tagged_cells is not None:
        _check_counts(tagged_cells, len(tables))
    texts = [table_text(t) for t in tables]
    start = item_8_table_start(root)
    found: dict[Statement, StatementTables] = {}
    for statement in ANCHORS:
        if tagged_cells is None:
            groups, _, numbers, negative_index = _ranked_single(texts, statement, start)[0]
            if groups >= MIN_ANCHOR_GROUPS:
                found[statement] = StatementTables(statement, (-negative_index,), groups, numbers)
            continue
        best = _best_tagged(texts, statement, start, tagged_cells)
        if best is not None:
            found[statement] = StatementTables(statement, *best)

    chosen = {i for s in found.values() for i in s.table_indices}
    for statement, located in list(found.items()):
        if len(located.table_indices) == 1:
            found[statement] = _continue(located, texts, chosen, tagged_cells)
    return found


def text_outside_tables(root: etree._Element) -> str:
    """The document's visible text outside every table, in order, whitespace-normalised.

    Read as one stream, so a heading and the sentence after it count as adjacent even when the
    filer splits them across elements.
    """
    parts: list[str] = []
    depth = 0
    for event, element in etree.iterwalk(root, events=("start", "end")):
        name = local_name(element)
        if event == "start":
            if name == "table":
                depth += 1
            elif not depth and element.text:
                parts.append(element.text)
        else:
            if name == "table":
                depth -= 1
            if not depth and element.tail:
                parts.append(element.tail)
    return " ".join(" ".join(parts).split())


def incorporated_by_reference(root: etree._Element) -> bool:
    """True when the text after an "Item 8" mention (outside tables, up to the next Item)
    says the financial statements are incorporated by reference (e.g. from an exhibit)."""
    text = text_outside_tables(root)
    for match in _ITEM_8_ANYWHERE.finditer(text):
        window = text[match.end(): match.end() + _REFERENCE_WINDOW]
        next_item = _NEXT_ITEM.search(window)
        if _BY_REFERENCE.search(window[: next_item.start()] if next_item else window):
            return True
    return False


def gap_reason(root: etree._Element, located: Mapping[Statement, object]) -> str | None:
    """Why statements are missing (a GAP reason code), or None when all three are located.

    Read from the document's own wording, never from the issuer.
    """
    if all(s in located for s in ANCHORS):
        return None
    if not document_tables(root):
        return NO_TABLES
    if not located and incorporated_by_reference(root):
        return STATEMENTS_INCORPORATED_BY_REFERENCE
    return STATEMENT_NOT_LOCATED
