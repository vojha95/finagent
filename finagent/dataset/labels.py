"""Row labels for the document channel, taken from the filer's own tags (blueprint 9.4).

Training and evaluation only: labels come from the tags, so this module reads both the tag
store and the rows. Nothing here is ever a model input; the label concepts are kept for the
label audit (9.5), never as features (CLAUDE.md rule 7).

9.4: collect the concepts tagged on each statement row's cells (non-dimensional contexts);
map them to canonical items with the concept map; unmapped and extension concepts -> OTHER;
exclude rows with two mapped items.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from finagent.config import LineItemSpec
from finagent.ixbrl.tagstore import TagFact
from finagent.records import RowInstance

OTHER = "OTHER"

# Why a row got its label; stable codes so the audit can count them.
MAPPED = "MAPPED"  # exactly one canonical item among the row's non-dimensional concepts
UNMAPPED_CONCEPT = "UNMAPPED_CONCEPT"  # tagged, but no concept is in the concept map
DIMENSIONAL_ONLY = "DIMENSIONAL_ONLY"  # only dimensional facts (segment or component rows)
UNTAGGED = "UNTAGGED"  # no fact at all in the row's cells
AMBIGUOUS = "AMBIGUOUS"  # two or more canonical items: excluded (label None)

RowKey = tuple[int, int]  # (table index, row index)


@dataclass(frozen=True)
class RowLabel:
    label: str | None  # canonical item id, OTHER, or None for an excluded row
    reason: str
    concepts: tuple[str, ...]  # non-dimensional concepts on the row; audit only, never a feature


def concept_owner(concept_map: dict[str, LineItemSpec]) -> dict[str, str]:
    """Concept -> canonical item id. The config loader guarantees each concept has one item."""
    return {concept: spec.item_id for spec in concept_map.values() for concept in spec.concepts}


def facts_by_row(facts: Iterable[TagFact]) -> dict[RowKey, list[TagFact]]:
    rows: dict[RowKey, list[TagFact]] = defaultdict(list)
    for fact in facts:
        if fact.cell_address is not None:
            rows[(fact.cell_address.table_index, fact.cell_address.row_index)].append(fact)
    return rows


def label_row(row_facts: list[TagFact], owner: dict[str, str]) -> RowLabel:
    """Label one row from the facts tagged in its cells."""
    concepts = tuple(sorted({f.concept for f in row_facts if not f.dimensions and not f.is_nil}))
    items = sorted({owner[c] for c in concepts if c in owner})
    if len(items) > 1:
        return RowLabel(None, AMBIGUOUS, concepts)
    if items:
        return RowLabel(items[0], MAPPED, concepts)
    if concepts:
        return RowLabel(OTHER, UNMAPPED_CONCEPT, concepts)
    if row_facts:
        return RowLabel(OTHER, DIMENSIONAL_ONLY, concepts)
    return RowLabel(OTHER, UNTAGGED, concepts)


def label_rows(
    rows: Iterable[RowInstance], facts: Iterable[TagFact], owner: dict[str, str]
) -> list[tuple[RowInstance, RowLabel]]:
    by_row = facts_by_row(facts)
    return [(row, label_row(by_row.get((row.table_index, row.source_row), []), owner))
            for row in rows]
