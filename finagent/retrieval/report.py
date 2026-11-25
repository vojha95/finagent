"""Retrieval run (blueprint 8.14; Phase 7.2): section detection and chunks per filing.

    python -m finagent.retrieval.report --run-id retrieval-0001 [--as-of 2026-11-24]
        [--tickers AMZN,MSFT]

Validation companies by default (held-out companies are refused). For every 10-K filed by
as_of: the Item sections found, the expected Items missing, the Item 8 notes, the UNKNOWN text
and why, the share of the visible words captured in blocks, and chunk counts and sizes. A
search smoke test runs the frozen signal queries on the dev companies' latest filings only
(D10: AMZN and MSFT are dev; the other six are test, so no query is run on them before E3).
Writes runs/<run>/filings.csv, sections.csv, chunks.csv and docs/retrieval_v1.md.
"""

import argparse
import csv
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finagent.config import load_retrieval_config
from finagent.evidence.integrity import read_snapshot
from finagent.evidence.store import FilingRecord
from finagent.ixbrl import clean_document
from finagent.ixbrl.tagstore import parse_document
from finagent.model.report import ROOT, _git, _table, write_report
from finagent.retrieval.index import FilingIndex, Retriever, build_index
from finagent.retrieval.sections import SKIPPED_ELEMENTS, UNKNOWN, local_name, normalise
from finagent.retrieval.templates import load_query_templates

DEV = ("AMZN", "MSFT")  # D10
SMOKE_SECTIONS = ("ITEM_7", "ITEM_8")  # 8.13: Item 7 and the Item 8 notes


@dataclass
class FilingResult:
    ticker: str
    filing: FilingRecord
    index: FilingIndex | None = None
    visible_words: int = 0
    error: str | None = None
    smoke: list[tuple[str, str, float]] = field(default_factory=list)  # signal, chunk, score

    @property
    def captured_words(self) -> int:
        assert self.index is not None
        return sum(len([w for w in b.text.split() if w != "|"]) for b in self.index.blocks)


def visible_words(document: bytes) -> int:
    """Words of the clean document's visible text (head, style and script left out)."""
    root = parse_document(clean_document(document))
    words = 0
    for e in root.iter():
        if local_name(e) in SKIPPED_ELEMENTS:
            continue
        if any(local_name(a) in SKIPPED_ELEMENTS for a in e.iterancestors()):
            continue
        words += len(normalise(e.text or "").split()) + len(normalise(e.tail or "").split())
    return words


def filing_result(retriever: Retriever, ticker: str, f: FilingRecord) -> FilingResult:
    r = FilingResult(ticker, f)
    try:
        document = read_snapshot(retriever.store.evidence_dir, f.primary_document,
                                 retriever.store.manifest)
        r.index = build_index(f, document, retriever.config)
        retriever.indexes[f.accession] = r.index
        r.visible_words = visible_words(document)
    except Exception as exc:  # reported, never hidden
        r.error = f"{type(exc).__name__}: {exc}"
    return r


def summary_row(r: FilingResult) -> list[object]:
    if r.index is None:
        return [r.ticker, f"FY{r.filing.fiscal_year}", r.filing.accession, "ERROR", r.error,
                "", "", "", "", ""]
    s = r.index.sections
    items = [x.section_id[5:] for x in s.sections if x.section_id.startswith("ITEM_")
             and "/" not in x.section_id]
    notes = sum(1 for x in s.sections if "/NOTE_" in x.section_id)
    unknown = sum(x.words for x in s.sections if x.section_id == UNKNOWN)
    text = [c for c in r.index.chunks if c.kind == "text"]
    coverage = r.captured_words / r.visible_words if r.visible_words else 0.0
    return [r.ticker, f"FY{r.filing.fiscal_year}", r.filing.accession, len(items),
            ", ".join(s.missing) or "none", notes,
            f"{unknown:,} ({s.unknown_reason})" if unknown else "0",
            f"{len(text)} text / {len(r.index.chunks) - len(text)} table",
            max((c.words for c in text), default=0),
            "; ".join(x.split(" (")[0] for x in s.relocated) or "none",
            ", ".join(s.pointers) or "none", f"{coverage:.1%}"]


def section_words(r: FilingResult) -> dict[str, int]:
    """Words per top-level section (an Item with its notes; pointers apart)."""
    found: dict[str, int] = {}
    if r.index is not None:
        for s in r.index.sections.sections:
            key = s.section_id if s.section_id.endswith("/POINTER") else s.section_id.split("/")[0]
            found[key] = found.get(key, 0) + s.words
    return found


def load_previous(run_dir: Path) -> dict[str, dict[str, int]]:
    """A previous run's notes and Item 7 / Item 8 words (with notes) per accession."""
    found: dict[str, dict[str, int]] = {}
    with (run_dir / "sections.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            mine = found.setdefault(row["accession"], {"notes": 0, "ITEM_7": 0, "ITEM_8": 0})
            top = row["section_id"].split("/")[0]
            if "/NOTE_" in row["section_id"]:
                mine["notes"] += 1
            if top in ("ITEM_7", "ITEM_8") and not row["section_id"].endswith("/POINTER"):
                mine[top] += int(row["words"])
    return found


def comparison_rows(results: Sequence[FilingResult],
                    previous: dict[str, dict[str, int]]) -> list[list[object]]:
    """Filings whose notes or Item 7 / Item 8 words differ from the previous run."""
    rows = []
    for r in results:
        before = previous.get(r.filing.accession)
        if r.index is None or before is None:
            continue
        words = section_words(r)
        now = {"notes": sum(1 for s in r.index.sections.sections if "/NOTE_" in s.section_id),
               "ITEM_7": words.get("ITEM_7", 0), "ITEM_8": words.get("ITEM_8", 0)}
        if now != before:
            rows.append([r.ticker, f"FY{r.filing.fiscal_year}"]
                        + [f"{before[k]:,} → {now[k]:,}" for k in ("ITEM_7", "ITEM_8", "notes")]
                        + ["; ".join(r.index.sections.relocated) or "none"])
    return rows


def write_outputs(out: Path, results: Sequence[FilingResult]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    head = ["ticker", "fiscal_year", "accession", "items_found", "missing", "notes",
            "unknown_words", "chunks", "max_text_chunk_words", "relocated",
            "unresolved_pointers", "coverage"]
    tables = {"filings.csv": (head, [summary_row(r) for r in results]),
              "sections.csv": (["ticker", "fiscal_year", "accession", "section_id", "heading",
                                "words", "text_chunks", "table_chunks"], []),
              "chunks.csv": (["chunk_id", "section_id", "kind", "words", "sha256"], [])}
    for r in results:
        if r.index is None:
            continue
        for s in r.index.sections.sections:
            mine = [c for c in r.index.chunks if c.section_id == s.section_id]
            tables["sections.csv"][1].append(
                [r.ticker, r.filing.fiscal_year, r.filing.accession, s.section_id, s.heading,
                 s.words, sum(c.kind == "text" for c in mine),
                 sum(c.kind == "table" for c in mine)])
        tables["chunks.csv"][1].extend([c.chunk_id, c.section_id, c.kind, c.words, c.sha256]
                                       for c in r.index.chunks)
    for name, (header, rows) in tables.items():
        with (out / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)


def smoke_test(retriever: Retriever, results: Sequence[FilingResult], as_of: date) -> None:
    """Top hit of every frozen signal query, dev companies' latest filing only (D10)."""
    templates = load_query_templates()
    for ticker in DEV:
        mine = [r for r in results if r.ticker == ticker and r.index is not None]
        if not mine:
            continue
        latest = max(mine, key=lambda r: r.filing.fiscal_year)
        for signal in templates.signals.values():
            hits = retriever.search(ticker, latest.filing.fiscal_year, signal.query,
                                    as_of=as_of, sections=SMOKE_SECTIONS, k=1)
            latest.smoke.append((signal.signal_id, hits[0].chunk.chunk_id if hits else "none",
                                 hits[0].score if hits else 0.0))


def render_report(results: Sequence[FilingResult], header: dict[str, object],
                  previous: dict[str, dict[str, int]] | None = None) -> str:
    config = load_retrieval_config()
    templates = load_query_templates()
    lines = [
        "# Evidence retrieval v1 (blueprint 8.14; Phase 7.2)", "",
        f"Generated by `python -m finagent.retrieval.report` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' + uncommitted changes' if header['git_dirty'] else ''}, "
        f"evidence manifest `{str(header['evidence_manifest_sha256'])[:12]}`, as of "
        f"{header['as_of']}). Outputs: `runs/{header['run_id']}/filings.csv`, `sections.csv`, "
        "`chunks.csv`.", "",
        "## Query templates (frozen)", "",
        f"`configs/query_templates.yaml` version {templates.version}, sha256 `{templates.sha256}`"
        ", frozen 2026-11-25T00:23:25Z before the chunker ran on any filing "
        "(`configs/query_templates.sha256`; `tests/test_retrieval_templates.py` fails on any "
        "change). E3 scores these exact queries.", "",
        "## Method", "",
        "Clean document (no tags) read as blocks; Item headings found by "
        "`configs/retrieval.yaml` (a contents table is one block and never yields a heading; a "
        "contents list written as text is left out; the longest run in Form 10-K order is kept); "
        "Item 8 notes numbered in sequence from 1; text before the first Item heading is "
        f"UNKNOWN, never guessed. Chunks of about {config.target_words} words (max "
        f"{config.max_words}) at sentence boundaries, never across a section; each data table "
        f"is its own chunk. BM25 (k1 {config.k1}, b {config.b}) per filing over its text "
        "chunks. Dense retrieval, fusion and the reranker wait for D8.", "",
        "**Rule history.** retrieval-0001: section rules written on the dev filings (AMZN, "
        "MSFT; D10) before any run on the six test companies. retrieval-0002: three general "
        "rules added after retrieval-0001 (#171, #172), developed and checked on train-split "
        "filings only, never tuned on a validation company: pointer Items 7 and 8 take their "
        "content from a later financial section (the pointer becomes ITEM_x/POINTER); a bare "
        "note number (\"NOTE 1\", \"1.\") with its title in the next block is a note heading; an "
        "abbreviation (\"Co.\") in a numbered note title is not a full stop; zero-width filler "
        "characters are dropped. **The problems behind them were found on test companies in "
        "retrieval-0001, so section detection is not blind for those companies** (the "
        "diagnosis below names them).", "",
        "## Section detection per filing", "",
        "Items found = Item sections detected; missing = expected Items "
        f"({', '.join(config.expected_items)}) without a heading; relocated = pointer Items whose "
        "content was found later in the document (#171); pointers not found = pointer Items "
        "left as they are (content not in the document); coverage = words captured in blocks / "
        "visible words of the clean document (page furniture is dropped).", ""]
    lines += _table(["Company", "Year", "Accession", "Items found", "Missing", "Notes",
                     "UNKNOWN words (why)", "Chunks", "Max text chunk words", "Relocated",
                     "Pointers not found", "Coverage"],
                    [summary_row(r) for r in results]) + [""]
    if previous is not None:
        changed = comparison_rows(results, previous)
        lines += [f"### Changes against `{header.get('compare_with', '')}`", "",
                  f"Filings whose Item 7 or Item 8 words (with notes; pointers apart) or note "
                  f"count changed: {len(changed)} of {len(results)}.", ""]
        if changed:
            lines += _table(["Company", "Year", "Item 7 words", "Item 8 words", "Notes",
                             "Relocated"], changed) + [""]
    ignored = [(r.ticker, r.filing.fiscal_year, h) for r in results if r.index
               for h in r.index.sections.ignored_headings]
    toc = sum(r.index.sections.toc_headings for r in results if r.index)
    lines += [f"Headings left out as a text contents list: {toc}. Headings out of Form 10-K "
              f"order (ignored): {len(ignored)}"
              + (": " + "; ".join(f"{t} FY{y} {h}" for t, y, h in ignored[:20]) if ignored
                 else "."), ""]
    smoke = [(r.ticker, r.filing.fiscal_year, s) for r in results for s in r.smoke]
    if smoke:
        lines += ["## Search smoke test (dev filings only)", "",
                  "Top hit of each frozen signal query, restricted to Item 7 and Item 8 (with "
                  "its notes), on the latest dev filings. Section and score only; relevance is "
                  "E3's job.", ""]
        lines += _table(["Company", "Year", "Signal", "Top chunk", "BM25"],
                        [[t, f"FY{y}", sid, cid.split(":", 1)[1], f"{score:.2f}"]
                         for t, y, (sid, cid, score) in smoke]) + [""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from finagent.config import load_universe
    from finagent.evaluation.e1 import pinned_tickers
    from finagent.evidence import EvidenceStore

    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="retrieval-0001")
    parser.add_argument("--tickers", default=None, help="a subset (e.g. the dev companies)")
    parser.add_argument("--report", default=str(ROOT / "docs" / "retrieval_v1.md"))
    parser.add_argument("--compare-with", default=None,
                        help="a previous retrieval run to compare section detection with")
    args = parser.parse_args(argv)
    store, universe = EvidenceStore(ROOT / "evidence"), load_universe()
    retriever = Retriever(store, universe)
    tickers = pinned_tickers(store, universe, "validation", args.as_of)  # refuses held_out
    if args.tickers:
        tickers = [t for t in tickers if t in args.tickers.split(",")]
    results = [filing_result(retriever, t, f) for t in tickers
               for f in retriever.store.load_filings(t, args.as_of) if f.filed <= args.as_of]
    smoke_test(retriever, results, args.as_of)
    header = {"run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
              "git_dirty": bool(_git("status", "--porcelain")),
              "evidence_manifest_sha256": hashlib.sha256(
                  (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest(),
              "as_of": args.as_of.isoformat(), "compare_with": args.compare_with or ""}
    write_outputs(ROOT / "runs" / args.run_id, results)
    previous = load_previous(ROOT / "runs" / args.compare_with) if args.compare_with else None
    write_report(Path(args.report), render_report(results, header, previous))
    for r in results:
        print(summary_row(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
