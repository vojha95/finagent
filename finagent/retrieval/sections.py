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
order is kept, the earliest occurrence first. Text before the first Item heading, or a whole
document without one, is UNKNOWN: a section is never guessed.

Pointer Items (#171; retrieval-0002, developed on train filings): an Item of
`sections.relocation` whose text is shorter than pointer_max_words words refers elsewhere
("presented in the Financial Table of Contents"). Its content is the span from the first anchor
heading after the pointer (e.g. "Report of Independent Registered Public Accounting Firm"),
confirmed by a statement or notes heading before the next Item heading, to the next Item
heading or the next relocated span. That span takes the Item's id and the pointer becomes
ITEM_8/POINTER; a pointer whose content is not found stays as it is and is reported.

Item 8 notes are numbered headings in sequence from 1 inside Item 8 (relocated or not): "Note
6 — Debt", "6. Debt" (an abbreviation such as "Co." inside the title is not a full stop), or a
bare "NOTE 6" whose title is the next block (#172).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lxml import etree

from finagent.config import RetrievalConfig
from finagent.records import Section

UNKNOWN = "UNKNOWN"
POINTER = "POINTER"
SKIPPED_ELEMENTS = frozenset({"head", "style", "script", "title"})
_ALPHA = re.compile(r"[A-Za-z]")
# Zero-width characters some filers use as empty cell or paragraph fillers (train filings).
_ZERO_WIDTH = {0x200B: " ", 0x200C: " ", 0x200D: " ", 0xFEFF: " "}
_CLOSERS = "\"”’)],;:"


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
    relocated: tuple[str, ...] = ()  # "8 from block 629 in ITEM_15 (Report of ...)" (#171)
    pointers: tuple[str, ...] = ()  # pointer Items whose content was not found (#171)

    def section_at(self, block: int) -> str:
        for s in self.sections:
            if s.start_block <= block < s.end_block:
                return s.section_id
        return UNKNOWN


def local_name(element: etree._Element) -> str:
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def normalise(text: str) -> str:
    return " ".join(text.translate(_ZERO_WIDTH).split())


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

def mask_abbreviations(text: str, abbreviations: frozenset[str]) -> str:
    """The text with the full stops of known abbreviations removed ("Organon & Co." -> "Organon
    & Co"), so they do not read as a sentence's end in a title (#172)."""
    out = []
    for token in text.split(" "):
        core = token.rstrip(_CLOSERS)
        out.append(core.replace(".", "") + token[len(core):]
                   if core.lower() in abbreviations else token)
    return " ".join(out)


def note_title(block: Block, config: RetrievalConfig) -> bool:
    """A short title block (the title of a bare "NOTE 6" heading)."""
    if block.kind != "text" or not 0 < block.words <= config.note_max_words:
        return False
    text = mask_abbreviations(block.text, config.abbreviations)
    return text[0].isalpha() and not text.endswith(".")


def note_number(blocks: Sequence[Block], position: int, config: RetrievalConfig) -> int | None:
    """The note number a block heads: "Note 6 — Debt", "6. Debt", or a bare "NOTE 6" with its
    title in the next block (#172)."""
    block = blocks[position]
    if block.kind != "text" or block.words > config.note_max_words:
        return None
    text = mask_abbreviations(block.text, config.abbreviations)
    for pattern in config.note_patterns:
        m = pattern.match(text)
        if m:
            return int(m.group(1))
    if config.note_bare_pattern is not None and position + 1 < len(blocks):
        m = config.note_bare_pattern.match(block.text)
        if m and note_title(blocks[position + 1], config):
            return int(m.group(1))
    return None


def note_headings(blocks: Sequence[Block], start: int, end: int,
                  config: RetrievalConfig) -> list[tuple[int, int]]:
    """(block position, note number) of the notes in blocks[start:end], numbered from 1."""
    found, expected = [], 1
    for position in range(start, end):
        if note_number(blocks, position, config) == expected:
            found.append((position, expected))
            expected += 1
    return found


def note_heading_text(blocks: Sequence[Block], position: int, config: RetrievalConfig) -> str:
    """The heading shown for a note: its block, plus the title block of a bare "NOTE 6"."""
    block = blocks[position]
    if config.note_bare_pattern is not None and config.note_bare_pattern.match(block.text):
        return f"{block.text} {blocks[position + 1].text}"
    return block.text


# ---------------------------------------------------------------- pointer Items (#171)

def squeeze(text: str) -> str:
    """Lower case, plain apostrophes, no spaces: "CONSOLIDATED B ALANCE SHEETS" and
    "Consolidated Balance Sheets" read the same (#171)."""
    return "".join(text.lower().replace("’", "'").replace("‘", "'").split())


def anchor_match(block: Block, patterns: Sequence[re.Pattern], config: RetrievalConfig) -> bool:
    """A short heading block (no Item heading, no final full stop) whose squeezed text starts
    with one of the patterns."""
    if block.kind != "text" or not 0 < block.words <= config.anchor_max_words:
        return False
    if item_heading(block, config) is not None or \
            mask_abbreviations(block.text, config.abbreviations).endswith("."):
        return False
    text = squeeze(block.text)
    return any(p.match(text) for p in patterns)


def pointer_spans(blocks: Sequence[Block], run: Sequence[tuple[int, str]],
                  config: RetrievalConfig) -> dict[str, int]:
    """item -> heading position of each Item of the run that is a pointer (module docstring)."""
    found = {}
    for n, (position, item) in enumerate(run):
        end = run[n + 1][0] if n + 1 < len(run) else len(blocks)
        if item in config.relocation and \
                alphabetic_words(blocks[position + 1:end]) < config.pointer_max_words:
            found[item] = position
    return found


def find_anchor(blocks: Sequence[Block], start: int, headings: Sequence[int], item: str,
                config: RetrievalConfig) -> int | None:
    """The first confirmed anchor heading of an Item's content at or after `start`."""
    anchors, confirm = config.relocation[item]
    for i in range(start, len(blocks)):
        if not anchor_match(blocks[i], anchors, config):
            continue
        end = next((h for h in headings if h > i), len(blocks))
        if not confirm or any(anchor_match(blocks[j], confirm, config) for j in range(i, end)):
            return i
    return None


def relocations(blocks: Sequence[Block], run: Sequence[tuple[int, str]],
                config: RetrievalConfig) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """(item -> pointer heading position, item -> content start, unresolved pointer items)."""
    if config.pointer_max_words <= 0:
        return {}, {}, []
    pointers = pointer_spans(blocks, run, config)
    headings = [p for p, _ in run]
    starts, unresolved = {}, []
    for item, position in pointers.items():
        end = next((h for h in headings if h > position), len(blocks))
        anchor = find_anchor(blocks, end, headings, item, config)
        if anchor is None:
            unresolved.append(item)
        else:
            starts[item] = anchor
    return pointers, starts, unresolved


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
    pointers, starts, unresolved = relocations(blocks, run, config)
    # Cut points in document order: every Item heading (a relocated pointer as ITEM_x/POINTER)
    # and the start of each relocated Item's content.
    cuts = [(p, f"ITEM_{item}/{POINTER}" if item in starts else f"ITEM_{item}") for p, item
            in run] + [(p, f"ITEM_{item}") for item, p in starts.items()]
    cuts.sort()
    sections: list[Section] = []
    first = cuts[0][0] if cuts else len(blocks)
    if first > 0:
        sections.append(_section(UNKNOWN, blocks, 0, first))
    for n, (position, section_id) in enumerate(cuts):
        end = cuts[n + 1][0] if n + 1 < len(cuts) else len(blocks)
        notes = note_headings(blocks, position + 1, end, config) \
            if section_id == "ITEM_8" else []
        sections.append(_section(section_id, blocks, position, notes[0][0] if notes else end,
                                 blocks[position].text))
        for k, (note_at, number) in enumerate(notes):
            note_end = notes[k + 1][0] if k + 1 < len(notes) else end
            sections.append(_section(f"{section_id}/NOTE_{number:02d}", blocks, note_at,
                                     note_end, note_heading_text(blocks, note_at, config)))
    found = {item for _, item in run}
    reason = ("" if first == 0 else
              "no Item heading outside the table of contents" if not run else
              "text before the first Item heading (cover page, table of contents)")
    moved = tuple(f"{item} from block {p} after {_host(run, p)} ({blocks[p].text[:60]})"
                  for item, p in sorted(starts.items()))
    return SectionMap(tuple(sections), tuple(i for i in config.expected_items if i not in found),
                      len(toc), ignored, reason, moved, tuple(unresolved))


def _host(run: Sequence[tuple[int, str]], position: int) -> str:
    """The Item whose heading last precedes a relocated span (the Item it sat under)."""
    before = [item for p, item in run if p < position]
    return f"ITEM_{before[-1]}" if before else UNKNOWN
