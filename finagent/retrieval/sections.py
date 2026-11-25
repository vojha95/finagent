"""Sections of a 10-K's clean document (blueprint 8.14; Phase 7.2).

The clean document (finagent.ixbrl.clean_document: what an analyst sees, no tags) is read as a
list of blocks in document order (configs/retrieval.yaml `blocks`):
- text: a text block (a leaf block element, or a run of inline text between block elements)
  outside tables, or a layout table (no numeric cell: bullets, footnotes, heading tables),
  its cells joined in reading order;
- table: a data table (at least one numeric cell), kept apart from the text;
page furniture (page numbers, "Table of contents") is dropped.

Item headings are short text blocks matching `sections.item_pattern` ("Item 7. Management's
Discussion ...", "ITEM 7.", a one-row heading table); a bare "Item 7" is a running page header.
A table of contents is one table block, so it never yields a heading; a contents list written
as text is a run of at least toc_min_items headings each followed by fewer than toc_max_words
alphabetic words, and is left out. Of the remaining headings the longest run in Form 10-K
order is kept, the earliest occurrence first. Item 8 notes are numbered headings in sequence
from 1 inside Item 8. Text before the first Item heading, or a whole document without one, is
UNKNOWN: a section is never guessed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from finagent.config import RetrievalConfig
from finagent.records import Section

UNKNOWN = "UNKNOWN"
SKIPPED_ELEMENTS = frozenset({"head", "style", "script", "title"})
_ALPHA = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class Block:
    kind: Literal["text", "table"]
    text: str  # text: one line; table: rows joined by newlines, cells by " | "

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class SectionMap:
    """The sections of one document and what section detection found or missed."""

    sections: tuple[Section, ...]
    missing: tuple[str, ...]  # expected Items without a heading
    toc_headings: int  # headings left out as a contents list
    ignored_headings: tuple[str, ...]  # headings out of Form 10-K order
    unknown_reason: str  # why the UNKNOWN section exists ("" when there is none)

    def section_at(self, block: int) -> str:
        for s in self.sections:
            if s.start_block <= block < s.end_block:
                return s.section_id
        return UNKNOWN


def local_name(element: etree._Element) -> str:
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def normalise(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- blocks

def _containers(root: etree._Element, config: RetrievalConfig) -> set[etree._Element]:
    """Elements with a block element or table below them (one pass over the tree)."""
    found: set[etree._Element] = set()
    for e in root.iter():
        if local_name(e) in config.block_tags or local_name(e) == "table":
            for a in e.iterancestors():
                if a in found:
                    break
                found.add(a)
    return found


def table_block(table: etree._Element, config: RetrievalConfig) -> Block:
    """A data table (a numeric cell), or a layout table read as one text block."""
    rows = []
    for tr in (e for e in table.iter() if local_name(e) == "tr"):
        cells = [normalise(" ".join(c.itertext())) for c in tr
                 if local_name(c) in ("td", "th")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    if any(config.numeric_cell.match(c) for row in rows for c in row):
        return Block("table", "\n".join(" | ".join(row) for row in rows))
    return Block("text", " ".join(c for row in rows for c in row))


def document_blocks(root: etree._Element, config: RetrievalConfig) -> list[Block]:
    """Every block of the document in reading order (module docstring)."""
    containers = _containers(root, config)
    out: list[Block] = []

    def add(text: str) -> None:
        text = normalise(text)
        if text and not any(p.match(text) for p in config.furniture):
            out.append(Block("text", text))

    def walk(element: etree._Element) -> None:
        buffer: list[str] = [element.text or ""]
        for child in element:
            name = local_name(child)
            if name in SKIPPED_ELEMENTS:
                pass
            elif name == "table":
                add(" ".join(buffer))
                buffer = []
                block = table_block(child, config)
                if block.kind == "table":
                    out.append(block)
                else:
                    add(block.text)
            elif child in containers:
                add(" ".join(buffer))
                buffer = []
                walk(child)
            elif name in config.block_tags:
                add(" ".join(buffer))
                buffer = []
                add(" ".join(child.itertext()))
            elif name:  # inline element: part of the current run of text
                buffer.append(" ".join(child.itertext()))
            buffer.append(child.tail or "")
        add(" ".join(buffer))

    walk(root)
    return out


# ---------------------------------------------------------------- Item headings

def item_heading(block: Block, config: RetrievalConfig) -> str | None:
    """The Item id a text block heads ("7A"), or None."""
    if block.kind != "text" or len(block.text) > config.heading_max_chars:
        return None
    m = config.item_pattern.match(block.text)
    if not m or m.group(1).upper() not in config.items:
        return None
    return m.group(1).upper()


def alphabetic_words(blocks: Sequence[Block]) -> int:
    return sum(1 for b in blocks if b.kind == "text" for w in b.text.split() if _ALPHA.search(w))


def toc_runs(blocks: Sequence[Block], candidates: Sequence[tuple[int, str]],
             config: RetrievalConfig) -> set[int]:
    """Block positions of headings in a contents list written as text (module docstring)."""
    short = []
    for i, (position, _) in enumerate(candidates):
        end = candidates[i + 1][0] if i + 1 < len(candidates) else len(blocks)
        short.append(alphabetic_words(blocks[position + 1:end]) < config.toc_max_words)
    found: set[int] = set()
    i = 0
    while i < len(candidates):
        j = i
        while j < len(candidates) and short[j]:
            j += 1
        if j - i >= config.toc_min_items:
            found.update(position for position, _ in candidates[i:j])
        i = max(j, i + 1)
    return found


def ordered_run(candidates: Sequence[tuple[int, str]],
                items: Sequence[str]) -> list[tuple[int, str]]:
    """The longest subsequence in Form 10-K Item order; ties go to the earliest headings."""
    rank = {item: i for i, item in enumerate(items)}
    best: list[int] = []
    previous: list[int | None] = []
    for i, (_, item) in enumerate(candidates):
        length, before = 1, None
        for j in range(i):
            if rank[candidates[j][1]] < rank[item] and best[j] + 1 > length:
                length, before = best[j] + 1, j
        best.append(length)
        previous.append(before)
    if not best:
        return []
    end: int | None = best.index(max(best))
    run = []
    while end is not None:
        run.append(candidates[end])
        end = previous[end]
    return run[::-1]


# ---------------------------------------------------------------- notes

def note_number(block: Block, config: RetrievalConfig) -> int | None:
    if block.kind != "text" or block.words > config.note_max_words:
        return None
    for pattern in config.note_patterns:
        m = pattern.match(block.text)
        if m:
            return int(m.group(1))
    return None


def note_headings(blocks: Sequence[Block], start: int, end: int,
                  config: RetrievalConfig) -> list[tuple[int, int]]:
    """(block position, note number) of the notes in blocks[start:end], numbered from 1."""
    found, expected = [], 1
    for position in range(start, end):
        if note_number(blocks[position], config) == expected:
            found.append((position, expected))
            expected += 1
    return found


# ---------------------------------------------------------------- sections

def _section(section_id: str, blocks: Sequence[Block], start: int, end: int,
             heading: str = "") -> Section:
    words = sum(b.words for b in blocks[start:end] if b.kind == "text")
    return Section(section_id, heading[:200], start, end, words)


def detect_sections(blocks: Sequence[Block], config: RetrievalConfig) -> SectionMap:
    """The sections of a document's blocks (module docstring)."""
    candidates = [(i, item) for i, b in enumerate(blocks)
                  if (item := item_heading(b, config)) is not None]
    toc = toc_runs(blocks, candidates, config)
    kept = [c for c in candidates if c[0] not in toc]
    run = ordered_run(kept, config.items)
    ignored = tuple(f"{blocks[p].text[:60]} (block {p})" for p, _ in kept if (p, _) not in run)
    sections: list[Section] = []
    first = run[0][0] if run else len(blocks)
    if first > 0:
        sections.append(_section(UNKNOWN, blocks, 0, first))
    for n, (position, item) in enumerate(run):
        end = run[n + 1][0] if n + 1 < len(run) else len(blocks)
        section_id = f"ITEM_{item}"
        notes = note_headings(blocks, position + 1, end, config) if item == "8" else []
        sections.append(_section(section_id, blocks, position, notes[0][0] if notes else end,
                                 blocks[position].text))
        for k, (note_at, number) in enumerate(notes):
            note_end = notes[k + 1][0] if k + 1 < len(notes) else end
            sections.append(_section(f"{section_id}/NOTE_{number:02d}", blocks, note_at,
                                     note_end, blocks[note_at].text))
    found = {item for _, item in run}
    reason = ("" if first == 0 else
              "no Item heading outside the table of contents" if not run else
              "text before the first Item heading (cover page, table of contents)")
    return SectionMap(tuple(sections), tuple(i for i in config.expected_items if i not in found),
                      len(toc), ignored, reason)
