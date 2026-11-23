"""Statement location in the clean document (blueprint 8.3 step 1).

Works on the clean document only: tables are scored by anchor phrases and numeric density,
never by tags, so the document channel stays independent of the tag channel (rule 7).
"""

import re
from dataclasses import dataclass

from lxml import etree

from finagent.records import Statement

# Anchor phrase groups per statement. A table scores one point per group it contains; the
# groups are general wording found across filers, not tied to any company (rule 3).
ANCHORS: dict[Statement, tuple[str, ...]] = {
    "IS": (
        r"total (net )?(sales|revenues?)|net sales|\brevenues?\b",
        r"operating income|income from operations|operating (loss|profit)",
        r"income tax",
        r"net (income|loss|earnings)",
        r"diluted",
    ),
    "BS": (
        r"total current assets",
        r"total assets",
        r"total current liabilities",
        r"(stockholders|shareholders)['’] (equity|deficit)",
        r"total liabilities and",
    ),
    "CF": (
        r"operating activities|\boperations\b",
        r"investing",
        r"financing",
        r"net cash",
        r"(end|beginning) of (the )?(period|year)",
    ),
}

# The last anchor group of each statement closes it; a table that lacks it may continue in
# the next table (a statement split across pages).
MIN_ANCHOR_GROUPS = 4

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_ITEM_8 = re.compile(r"^item\s*8\b", re.IGNORECASE)


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
        if any(local_name(a) == "table" for a in element.iterancestors()):
            continue
        text = " ".join("".join(element.itertext()).split())
        if _ITEM_8.match(text) and len(text) < 200:
            return tables_seen
    return None


def locate_statements(root: etree._Element) -> dict[Statement, StatementTables]:
    """The best-scoring table for each statement, plus continuation tables.

    A statement is absent from the result when no table reaches MIN_ANCHOR_GROUPS; the caller
    treats that as a gap. Ranking: anchor groups, then position after the Item 8 heading, then
    numeric density.
    """
    tables = document_tables(root)
    texts = [table_text(t) for t in tables]
    start = item_8_table_start(root)
    found: dict[Statement, StatementTables] = {}
    for statement in ANCHORS:
        ranked = sorted(
            (
                (
                    anchor_groups(text, statement),
                    start is not None and index >= start,
                    len(_NUMBER.findall(text)),
                    -index,
                )
                for index, text in enumerate(texts)
            ),
            reverse=True,
        )
        groups, _, numbers, negative_index = ranked[0]
        if groups < MIN_ANCHOR_GROUPS:
            continue
        index = -negative_index
        found[statement] = StatementTables(statement, (index,), groups, numbers)

    chosen = {i for s in found.values() for i in s.table_indices}
    for statement, located in list(found.items()):
        indices = list(located.table_indices)
        while not closes(texts[indices[-1]], statement):
            following = indices[-1] + 1
            if following >= len(tables) or following in chosen:
                break
            if not _NUMBER.search(texts[following]):
                break
            indices.append(following)
            chosen.add(following)
        found[statement] = StatementTables(
            statement, tuple(indices), located.anchor_groups, located.numbers
        )
    return found
