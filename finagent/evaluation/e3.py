"""E3 retrieval labelling sheet (blueprint 10.3; Phase 7.5) and its scoring (7.6).

    python -m finagent.evaluation.e3 queries    # gold/e3_queries.jsonl (the selection rule)
    python -m finagent.evaluation.e3 pool       # gold/e3_pool.jsonl + gold/e3_pool_key.jsonl
    python -m finagent.evaluation.e3 sheet      # eval/e3_labels.xlsx
    python -m finagent.evaluation.e3 validate [--sheet PATH] [--partial]
    python -m finagent.evaluation.e3 import   [--sheet PATH]   # -> gold/e3_labels.jsonl
    python -m finagent.evaluation.e3 score  --run-id e3-0001   # runs/e3-0001, docs/e3_scores_0001.md
    python -m finagent.evaluation.e3 record --run-id e3-0001 --single-change ... --conclusion ...

Scoring follows the rule written before any score (docs/e3_v1.md section 4; configs/e3.yaml
`scoring`; finagent.evaluation.e3_score): Recall@5 and MRR@5 per variant at depth 5, lenient and
strict relevance, query bootstrap, a paired bootstrap against bm25, and the default switch rule.

configs/e3.yaml fixes the query selection rule (written before any retrieval ran on the test
companies; docs/e3_v1.md section 1), the pool (each variant's top `depth` chunks of the query's
10-K, same as_of and sections as the product; union shuffled per query with a fixed seed) and
the label scale (2 explains the movement, 1 partly, 0 does not).

Blinding: gold/e3_pool.jsonl holds what the sheet shows (query, chunk, text) and nothing about
the variants. gold/e3_pool_key.jsonl maps each pooled chunk to the variants, ranks and scores
that found it; the sheet never shows it and nothing here prints it. The two dev-company worked
examples on the instructions tab come from configs/e3.yaml `examples`.

Test companies only (the validation split without the dev companies); held-out companies are
refused by the tools and the retriever. No external AI API.
"""

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.config import CONFIG_DIR, RETRIEVAL_VARIANTS, ConfigError, _load_yaml

ROOT = Path(__file__).resolve().parents[2]
E3_PATH = CONFIG_DIR / "e3.yaml"
VALIDATION = "validation"
SIGNAL, EXPLAIN = "signal", "explain"
EVIDENCE_TAIL = " Evidence: quoted below."
LABELS = (0, 1, 2)
HEADER = ("query_id", "company", "fiscal_year", "query", "row", "section", "passage", "label",
          "notes", "better_passage", "chunk_id")
LABEL_SHEET, INSTRUCTIONS_SHEET = "labels", "instructions"


# ---------------------------------------------------------------- spec

@dataclass(frozen=True)
class Example:
    """A worked example for the instructions tab (a dev-company chunk with its label)."""

    issuer: str
    fiscal_year: int
    kind: str
    subject: str
    chunk_id: str
    label: int
    why: str


@dataclass(frozen=True)
class E3Spec:
    as_of: date
    insights_run: str
    files: dict[str, Path]
    signal: int
    explain: int
    explain_metrics: tuple[str, ...]
    variants: tuple[str, ...]
    depth: int
    seed: int
    rows_aim: tuple[int, int]
    labels: dict[int, str]
    seconds_per_row: int
    examples: tuple[Example, ...] = ()


def load_e3_spec(path: Path | str = E3_PATH) -> E3Spec:
    data = _load_yaml(Path(path))
    sel, pool = data["selection"], data["pool"]
    variants = tuple(pool["variants"])
    if set(variants) - set(RETRIEVAL_VARIANTS) or not variants:
        raise ConfigError(f"e3: unknown variants {sorted(set(variants) - set(RETRIEVAL_VARIANTS))}")
    labels = {int(k): str(v) for k, v in data["labels"].items()}
    if tuple(sorted(labels)) != LABELS:
        raise ConfigError(f"e3: labels must be exactly {LABELS}")
    metrics = tuple(sel["explain_metrics"])
    if len(metrics) != 3:
        raise ConfigError("e3: explain_metrics needs three metrics (k mod 3 rotation)")
    examples = tuple(Example(str(e["issuer"]), int(e["fiscal_year"]), str(e["kind"]),
                             str(e["subject"]), str(e["chunk_id"]), int(e["label"]),
                             str(e["why"])) for e in data.get("examples") or ())
    return E3Spec(date.fromisoformat(str(data["as_of"])), str(data["insights_run"]),
                  {k: Path(v) for k, v in data["files"].items()}, int(sel["signal"]),
                  int(sel["explain"]), metrics, variants, int(pool["depth"]), int(pool["seed"]),
                  tuple(pool["rows_aim"]), labels, int(data["seconds_per_row"]), examples)


# ---------------------------------------------------------------- queries

@dataclass(frozen=True)
class E3Query:
    query_id: str
    issuer: str
    fiscal_year: int
    kind: str  # signal | explain
    subject: str  # signal id or metric
    statement: str  # plain words, shown on the sheet
    query: str  # the product's query, searched by every variant (not shown)
    sections: tuple[str, ...]
    accession: str


def test_companies(universe: Mapping[str, str], dev: Sequence[str]) -> list[str]:
    """The validation split without the dev companies, in configs/universe.yaml order."""
    return [t for t, s in universe.items() if s == VALIDATION and t not in dev]


def fired_candidates(rows: Iterable[Mapping[str, str]], companies: Sequence[str],
                     excluded: Iterable[str]) -> dict[str, list[tuple[int, str]]]:
    """(fiscal year, signal) of every FIRED signal of the insights run, per company."""
    skip = set(excluded)
    found: dict[str, list[tuple[int, str]]] = {c: [] for c in companies}
    for r in rows:
        if r["ticker"] in found and r["state"] == "FIRED" and r["signal"] not in skip:
            found[r["ticker"]].append((int(r["fiscal_year"].removeprefix("FY")), r["signal"]))
    return {c: sorted(set(v)) for c, v in found.items()}


def select_signals(candidates: Mapping[str, Sequence[tuple[int, str]]],
                   companies: Sequence[str], need: Mapping[str, int],
                   order: Sequence[str]) -> list[tuple[str, int, str]]:
    """The rule of configs/e3.yaml: in rounds, each company takes the signal chosen the fewest
    times so far, then its least-used fiscal year, then the latest year, then signal order."""
    chosen: list[tuple[str, int, str]] = []
    counts: Counter = Counter()
    years = {c: Counter() for c in companies}
    left = {c: list(candidates.get(c, ())) for c in companies}
    for _ in range(max(need.values(), default=0)):
        for c in companies:
            if sum(1 for x in chosen if x[0] == c) >= need.get(c, 0) or not left[c]:
                continue
            year, sid = min(left[c], key=lambda ys: (counts[ys[1]], years[c][ys[0]], -ys[0],
                                                     order.index(ys[1])))
            left[c].remove((year, sid))
            counts[sid] += 1
            years[c][year] += 1
            chosen.append((c, year, sid))
    return chosen


def select_explains(companies: Sequence[str], metrics: Sequence[str], per_company: int,
                    searchable: Callable[[str, str], bool]
                    ) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Company k takes the metrics other than metrics[k mod 3]; an unsearchable one is replaced
    by the remaining metric. Returns the (company, metric) pairs and each company's shortfall."""
    chosen, short = [], {}
    for k, c in enumerate(companies):
        left_out = metrics[k % len(metrics)]
        order = [m for m in metrics if m != left_out] + [left_out]
        taken = [m for m in order if searchable(c, m)][:per_company]
        chosen += [(c, m) for m in taken]
        short[c] = per_company - len(taken)
    return chosen, short


def signal_statement(result: Any) -> str:
    """The card's own text without its evidence line (finagent.risk.insights.card_text)."""
    from finagent.risk.insights import VERIFIED, card_text

    text = card_text(result, VERIFIED)
    if not text.endswith(EVIDENCE_TAIL):
        raise ValueError(f"unexpected card text: {text!r}")
    return text[:-len(EVIDENCE_TAIL)]


def explain_statement(metric: str, year: int, now: Decimal, before: Decimal) -> str:
    """"Revenue increased from USD 1,000.0m in FY2024 to USD 1,100.0m in FY2025 (+10.0%).\""""
    from finagent.risk.insights import number

    change = now - before
    direction = "increased" if change > 0 else "decreased"
    share = f" ({float(change / abs(before) * 100):+.1f}%)" if before else ""
    return (f"{metric.replace('_', ' ').capitalize()} {direction} from "
            f"{number(float(before), 'USD')} in FY{year - 1} to {number(float(now), 'USD')} "
            f"in FY{year}{share}.")


def build_queries(tools: Any, spec: E3Spec, universe: Mapping[str, str],
                  dev: Sequence[str], insights_rows: Sequence[Mapping[str, str]]
                  ) -> list[E3Query]:
    """The 30 queries by the rule (no retrieval is run)."""
    as_of = spec.as_of
    companies = test_companies(universe, dev)
    order = list(tools.signal_config.signals)
    model = [s for s, x in tools.signal_config.signals.items() if x.data == "scenario"]
    explains: dict[tuple[str, str], Any] = {}

    def searchable(c: str, m: str) -> bool:
        found = tools.explain_inputs(c, "latest", m, as_of=as_of)
        if hasattr(found, "query"):
            explains[(c, m)] = found
            return True
        return False

    pairs, short = select_explains(companies, spec.explain_metrics, spec.explain, searchable)
    need = {c: spec.signal + short[c] for c in companies}
    picks = select_signals(fired_candidates(insights_rows, companies, model), companies, need,
                           order)
    retriever = tools.search()
    evidence = tools.signal_config.evidence
    found: list[E3Query] = []
    for c in companies:
        for _, year, sid in [p for p in picks if p[0] == c]:
            result = next(r for r in tools.signal_results(c, year, as_of) if r.signal_id == sid)
            if result.state != "FIRED":
                raise ValueError(f"{c} FY{year} {sid} is {result.state}, not FIRED as in "
                                 f"{spec.insights_run}")
            f = retriever.filing(c, year, as_of)
            found.append(E3Query("", c, year, SIGNAL, sid, signal_statement(result),
                                 tools.templates.signals[sid].query, tuple(evidence.sections),
                                 f.accession))
        for _, metric in [p for p in pairs if p[0] == c]:
            e = explains[(c, metric)]
            f = retriever.filing(c, e.year, as_of)
            found.append(E3Query("", c, e.year, EXPLAIN, metric,
                                 explain_statement(metric, e.year, e.now.value, e.before.value),
                                 e.query, tuple(evidence.explain_sections), f.accession))
    return [replace(q, query_id=f"E3-Q{n:02d}") for n, q in enumerate(found, 1)]


# ---------------------------------------------------------------- pool

@dataclass(frozen=True)
class PoolRow:
    """One (query, chunk) row of the sheet; nothing about the variants."""

    query_id: str
    position: int
    chunk_id: str
    section: str
    words: int
    sha256: str
    text: str


@dataclass(frozen=True)
class KeyRow:
    """Which variants found a pooled chunk, at which rank and score (never on the sheet)."""

    query_id: str
    chunk_id: str
    found: tuple[tuple[str, int, float], ...]  # (variant, rank, score)


def merge_pool(query_id: str, ranked: Mapping[str, Sequence[Any]], seed: int
               ) -> tuple[list[PoolRow], list[KeyRow]]:
    """The union of the variants' hits (Hit-like: chunk, rank, score), shuffled with the seed."""
    chunks: dict[str, Any] = {}
    found: dict[str, list[tuple[str, int, float]]] = {}
    for variant, hits in ranked.items():
        for h in hits:
            chunks.setdefault(h.chunk.chunk_id, h.chunk)
            found.setdefault(h.chunk.chunk_id, []).append((variant, h.rank, float(h.score)))
    ids = sorted(chunks)
    random.Random(f"{seed}:{query_id}").shuffle(ids)
    rows = [PoolRow(query_id, n, cid, chunks[cid].section_id, chunks[cid].words,
                    chunks[cid].sha256, chunks[cid].text) for n, cid in enumerate(ids, 1)]
    return rows, [KeyRow(query_id, cid, tuple(found[cid])) for cid in ids]


def pool_retriever(store: Any, universe: Mapping[str, str], depth: int) -> Any:
    """A retriever whose reranked list is read to `depth` (the product keeps 3; #236)."""
    from finagent.config import load_retrieval_config
    from finagent.retrieval.index import Retriever

    config = load_retrieval_config()
    search = replace(config.search, rerank_keep=max(depth, config.search.rerank_keep))
    return Retriever(store, universe, replace(config, search=search))


def build_pool(retriever: Any, queries: Sequence[E3Query], spec: E3Spec
               ) -> tuple[list[PoolRow], list[KeyRow]]:
    rows, keys = [], []
    for q in queries:
        ranked = {}
        for variant in spec.variants:
            hits = retriever.search(q.issuer, q.fiscal_year, q.query, as_of=spec.as_of,
                                    sections=q.sections, k=spec.depth, variant=variant)
            if any(h.chunk.accession != q.accession for h in hits):
                raise ValueError(f"{q.query_id}: a hit is not from {q.accession}")
            ranked[variant] = hits[:spec.depth]
        r, k = merge_pool(q.query_id, ranked, spec.seed)
        rows += r
        keys += k
    return rows, keys


# ---------------------------------------------------------------- files

def write_jsonl(path: Path, records: Iterable[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(asdict(r) if hasattr(r, "__dataclass_fields__") else r,
                                    default=str) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_queries(path: Path) -> list[E3Query]:
    return [E3Query(**{**r, "sections": tuple(r["sections"])}) for r in read_jsonl(path)]


def load_pool(path: Path) -> list[PoolRow]:
    return [PoolRow(**r) for r in read_jsonl(path)]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pool_sizes(rows: Sequence[PoolRow]) -> dict[str, int]:
    return dict(Counter(r.query_id for r in rows))


# ---------------------------------------------------------------- sheet

def _height(text: str, width: int) -> float:
    lines = sum(max(1, math.ceil(len(part) / max(1, width - 2))) for part in text.split("\n"))
    return min(409.0, 15.0 * lines + 4)


def write_sheet(path: Path, queries: Sequence[E3Query], rows: Sequence[PoolRow], spec: E3Spec,
                examples: Sequence[tuple[Example, str, str]] = ()) -> Path:
    """eval/e3_labels.xlsx: the labels tab (one row per query and chunk, grouped by query) and
    the instructions tab with the worked examples ((example, statement, chunk text))."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    widths = {"query_id": 9, "company": 9, "fiscal_year": 8, "query": 42, "row": 5,
              "section": 12, "passage": 95, "label": 7, "notes": 28, "better_passage": 40,
              "chunk_id": 30}
    by_query = {q.query_id: q for q in queries}
    wb = Workbook()
    guide = wb.active
    guide.title = INSTRUCTIONS_SHEET
    sheet = wb.create_sheet(LABEL_SHEET)
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    head_fill = PatternFill("solid", fgColor="DDE4EE")
    band = (PatternFill("solid", fgColor="FFFFFF"), PatternFill("solid", fgColor="F3F3F3"))
    top = Border(top=Side(style="medium", color="808080"))
    for col, name in enumerate(HEADER, 1):
        cell = sheet.cell(1, col, name)
        cell.font, cell.fill, cell.alignment = bold, head_fill, wrap
        sheet.column_dimensions[cell.column_letter].width = widths[name]
    choice = DataValidation(type="list", formula1='"0,1,2"', allow_blank=True,
                            showErrorMessage=True, errorTitle="Label",
                            error="Enter 2 (explains), 1 (partly) or 0 (does not).")
    sheet.add_data_validation(choice)
    col = {name: n for n, name in enumerate(HEADER, 1)}
    r = 2
    for b, qid in enumerate(dict.fromkeys(x.query_id for x in rows)):
        q = by_query[qid]
        mine = sorted((x for x in rows if x.query_id == qid), key=lambda x: x.position)
        first = r
        for x in mine:
            values = {"query_id": qid, "company": q.issuer, "fiscal_year": q.fiscal_year,
                      "query": q.statement, "row": x.position, "section": x.section,
                      "passage": x.text, "chunk_id": x.chunk_id}
            for name, value in values.items():
                cell = sheet.cell(r, col[name], value)
                if isinstance(value, str) and value.startswith("="):
                    cell.data_type = "s"  # text, never a formula
            for c in range(1, len(HEADER) + 1):
                cell = sheet.cell(r, c)
                cell.alignment, cell.fill = wrap, band[b % 2]
                if r == first:
                    cell.border = top
            sheet.row_dimensions[r].height = max(_height(x.text, widths["passage"]),
                                                 _height(q.statement, widths["query"]))
            choice.add(sheet.cell(r, col["label"]))
            r += 1
        if r - 1 > first:
            letter = sheet.cell(first, col["better_passage"]).column_letter
            sheet.merge_cells(f"{letter}{first}:{letter}{r - 1}")
    sheet.freeze_panes = "E2"
    _instructions(guide, spec, examples, bold, wrap)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _instructions(ws: Any, spec: E3Spec, examples: Sequence[tuple[Example, str, str]],
                  bold: Any, wrap: Any) -> None:
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 110
    lines: list[tuple[str, str]] = [
        ("E3 labelling", "For each query, judge every passage on the labels tab: does it explain "
         "the movement the query describes?"),
        ("label 2", spec.labels[2] + ": the passage gives the reason for this movement, in "
         "this company's filing for this year."),
        ("label 1", spec.labels[1] + ": it touches the movement or its cause but leaves out "
         "the main reason, or explains a related figure."),
        ("label 0", spec.labels[0] + ": it is about something else, or only repeats the "
         "number without a reason."),
        ("notes", "Optional, free text."),
        ("better_passage", "One cell per query (merged down the query's rows). If you know a "
         "sentence in this filing that explains the movement and is not among the passages, "
         "paste it there."),
        ("rules", "Label every row (dropdown 0, 1 or 2). Do not add, delete, sort or edit "
         "rows, passages or ids. The passages are in a random order. The sheet does not say "
         "which search method found a passage; do not open gold/e3_pool_key.jsonl."),
        ("when done", "Save as .xlsx, then run: python -m finagent.evaluation.e3 validate, "
         "then python -m finagent.evaluation.e3 import."),
        ("time", f"About {spec.seconds_per_row} seconds per row."),
    ]
    for n, (example, statement, text) in enumerate(examples, 1):
        lines += [(f"example {n}", f"{example.issuer} FY{example.fiscal_year} (a dev company, "
                   "not on the labels tab)"),
                  ("query", statement), ("passage", text),
                  ("label", f"{example.label} ({spec.labels[example.label]})"),
                  ("why", example.why)]
    for r, (key, value) in enumerate(lines, 1):
        ws.cell(r, 1, key).font = bold
        cell = ws.cell(r, 2, value)
        cell.alignment = wrap
        ws.row_dimensions[r].height = _height(value, 110)


# ---------------------------------------------------------------- import and validate

@dataclass(frozen=True)
class SheetRow:
    query_id: str
    chunk_id: str
    passage: str
    label: Any
    notes: str
    better_passage: str
    row: int  # spreadsheet row


def read_sheet(path: Path) -> tuple[tuple[str, ...], list[SheetRow]]:
    from openpyxl import load_workbook

    ws = load_workbook(path, data_only=True)[LABEL_SHEET]
    header = tuple(str(c.value) if c.value is not None else "" for c in ws[1])
    col = {name: n for n, name in enumerate(header)}
    found = []
    for n, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if all(v in (None, "") for v in values):
            continue

        def get(name: str) -> Any:
            return values[col[name]] if name in col and col[name] < len(values) else None
        found.append(SheetRow(str(get("query_id") or ""), str(get("chunk_id") or ""),
                              str(get("passage") or ""), get("label"),
                              str(get("notes") or "").strip(),
                              str(get("better_passage") or "").strip(), n))
    return header, found


def parse_label(value: Any) -> int | None:
    """2, 2.0 or "2" -> 2; blank -> None; anything else -> ValueError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    number = float(str(value).strip())
    if number not in LABELS:
        raise ValueError(value)
    return int(number)


def validate_sheet(header: Sequence[str], rows: Sequence[SheetRow], pool: Sequence[PoolRow],
                   partial: bool = False) -> list[str]:
    """Errors of a filled sheet against the pool (empty: valid). partial: unlabelled rows are
    allowed (progress check); import never allows them."""
    if tuple(header) != HEADER:
        return [f"the labels tab header must be {list(HEADER)}"]
    expected = {(p.query_id, p.chunk_id): p for p in pool}
    first = {}
    for p in sorted(pool, key=lambda p: p.position):
        first.setdefault(p.query_id, p.chunk_id)
    errors, seen = [], set()
    for r in rows:
        key = (r.query_id, r.chunk_id)
        where = f"row {r.row} ({r.query_id} {r.chunk_id})"
        p = expected.get(key)
        if p is None:
            errors.append(f"{where}: not in the pool")
            continue
        if key in seen:
            errors.append(f"{where}: duplicate row")
        seen.add(key)
        if " ".join(r.passage.split()) != " ".join(p.text.split()):
            errors.append(f"{where}: the passage text was changed")
        try:
            label = parse_label(r.label)
        except ValueError:
            errors.append(f"{where}: label {r.label!r} is not 0, 1 or 2")
            continue
        if label is None and not partial:
            errors.append(f"{where}: no label")
        if r.better_passage and first[r.query_id] != r.chunk_id:
            errors.append(f"{where}: better passage outside the query's merged cell")
    missing = sorted(set(expected) - seen)
    if missing:
        errors.append(f"{len(missing)} pool rows are missing from the sheet (first: "
                      f"{missing[0][0]} {missing[0][1]})")
    return errors


def label_records(rows: Sequence[SheetRow], queries: Sequence[E3Query], pool_sha256: str
                  ) -> list[dict[str, Any]]:
    """One record per query: its chunk labels and the better passage (a validated sheet)."""
    found = []
    for q in queries:
        mine = [r for r in rows if r.query_id == q.query_id]
        found.append({"query_id": q.query_id, "issuer": q.issuer, "fiscal_year": q.fiscal_year,
                      "kind": q.kind, "subject": q.subject,
                      "labels": [{"chunk_id": r.chunk_id, "label": parse_label(r.label),
                                  "notes": r.notes} for r in mine],
                      "better_passage": next((r.better_passage for r in mine
                                              if r.better_passage), ""),
                      "pool_sha256": pool_sha256})
    return found


# ---------------------------------------------------------------- report helpers

def query_table(queries: Sequence[E3Query], sizes: Mapping[str, int]) -> list[str]:
    lines = ["| id | company | FY | signal or explain | plain-words query | pool |",
             "|---|---|---|---|---|---|"]
    for q in queries:
        what = q.subject if q.kind == SIGNAL else f"explain {q.subject}"
        lines.append(f"| {q.query_id} | {q.issuer} | {q.fiscal_year} | {what} | "
                     f"{q.statement.replace('|', '/')} | {sizes.get(q.query_id, 0)} |")
    return lines


GENERATED = "<!-- generated by python -m finagent.evaluation.e3 doc; text below is replaced -->"


def write_generated(path: Path, text: str) -> None:
    """Replace everything after the GENERATED marker (the hand-written part above is kept)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    head = old.split(GENERATED, 1)[0].rstrip("\n")
    path.write_text(head + "\n\n" + GENERATED + "\n\n" + text, encoding="utf-8")


def render_generated(queries: Sequence[E3Query], rows: Sequence[PoolRow], spec: E3Spec,
                     sheet: Path) -> str:
    sizes = pool_sizes(rows)
    total = sum(sizes.values())
    low, high = spec.rows_aim
    minutes = total * spec.seconds_per_row / 60
    lines = ["## 5. The 30 queries", "",
             "Pool = the distinct chunks in the union of the four variants' top "
             f"{spec.depth} for the query (between {spec.depth} and "
             f"{spec.depth * len(spec.variants)}).", ""]
    lines += query_table(queries, sizes)
    kinds = Counter(q.kind for q in queries)
    lines += ["", "## 6. Pool and sheet", "",
              f"- Queries: {len(queries)} ({kinds[SIGNAL]} signal, {kinds[EXPLAIN]} explain) on "
              f"{len({q.issuer for q in queries})} companies; as_of {spec.as_of}.",
              f"- Pool: **{total} rows** (per query {min(sizes.values(), default=0)}-"
              f"{max(sizes.values(), default=0)}); aim {low}-{high}"
              + ("; **larger than the aim, reported before any cut**" if total > high else "")
              + ".",
              f"- Labelling time at about {spec.seconds_per_row} s per row: about "
              f"{minutes:.0f} minutes ({minutes / 60:.1f} hours).",
              f"- Sheet: `{sheet}` (labels and instructions tabs). Pool: "
              f"`{spec.files['pool']}`. Key (hidden): `{spec.files['pool_key']}`.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- command

def _paths(spec: E3Spec) -> dict[str, Path]:
    return {k: ROOT / v for k, v in spec.files.items()}


RUNS_DIR = ROOT / "runs"
EXPERIMENTS = ROOT / "eval" / "experiments.csv"


def score_run(spec: E3Spec, files: Mapping[str, Path], scoring_path: Path = E3_PATH
              ) -> dict[str, Any]:
    """Score every variant on the imported labels by the written rule (docs/e3_v1.md section 4).

    Returns {"scoring", "queries", "scores", "summaries", "decision", "pool_misses", ...}.
    """
    from finagent.evaluation import e3_score as s

    scoring = s.load_scoring(scoring_path)
    records = read_jsonl(files["labels"])
    pool_sha = file_sha256(files["pool"])
    stale = sorted({r["query_id"] for r in records if r["pool_sha256"] != pool_sha})
    if stale:
        raise ValueError(f"labels were made on another pool file: {stale[:3]}")
    queries = s.query_labels(records)
    lists = s.ranked_lists(read_jsonl(files["pool_key"]), spec.variants, scoring.depth)
    missing = [(q.query_id, v) for q in queries for v in spec.variants
               if (q.query_id, v) not in lists]
    if missing:
        raise ValueError(f"no ranked list for {missing[:3]}")
    scores = s.score_queries(queries, lists, spec.variants, scoring)
    summaries = {d: s.summarise(scores, spec.variants, d, scoring) for d in scoring.relevance}
    decision = s.decide(summaries[scoring.decide_on], scoring)
    misses = [{"query_id": r["query_id"], "issuer": r["issuer"],
               "better_passage": r["better_passage"]} for r in records if r["better_passage"]]
    return {"scoring": scoring, "queries": queries, "scores": scores, "summaries": summaries,
            "decision": decision, "pool_misses": misses, "pool_sha256": pool_sha}


def render_scores(result: Mapping[str, Any], run_id: str, header: Mapping[str, Any],
                  current_default: str) -> str:
    """The e3 score report (markdown)."""
    from finagent.evaluation import e3_score as s

    scoring, queries, scores = result["scoring"], result["queries"], result["scores"]
    variants = [x.variant for x in result["summaries"][s.PRIMARY]]
    decision = result["decision"]
    companies = {c: {q.query_id for q in queries if q.issuer == c}
                 for c in dict.fromkeys(q.issuer for q in queries)}
    kinds = {k: {q.query_id for q in queries if q.kind == k}
             for k in dict.fromkeys(q.kind for q in queries)}
    lines = [f"# E3 retrieval scores ({run_id})", "",
             f"Generated by `python -m finagent.evaluation.e3 score --run-id {run_id}` by the rule "
             "written before scoring (docs/e3_v1.md section 4). Commit "
             f"`{header['git_commit']}`{' + uncommitted changes' if header['git_dirty'] else ''}"
             f"; labels gold/e3_labels.jsonl (pool sha256 `{result['pool_sha256'][:12]}`); "
             f"{len(queries)} queries on {len(companies)} test companies; every variant at depth "
             f"{scoring.depth}. Recall@5 is relative to the pool (#237).", ""]
    for d in (s.PRIMARY, s.SECONDARY):
        lines += [f"## {d.capitalize()}: {s.DEFINITION_NAMES[d]}", ""]
        lines += s.summary_table(result["summaries"][d], scoring.baseline)
        lines.append("")
    lines += ["## Default decision (primary MRR@5)", "",
              f"- Current default: `{current_default}`. Qualifying: "
              f"{', '.join(decision.qualifying) or 'none'}.",
              f"- **Decision: `{decision.default}`** — {decision.reason}.", ""]
    miss = result["pool_misses"]
    lines += ["## Pool misses (better passages)", ""]
    lines += [f"- {m['query_id']} ({m['issuer']}): \"{m['better_passage']}\"" for m in miss] or [
        "- none"]
    lines.append("")
    for d in (s.PRIMARY, s.SECONDARY):
        lines += [f"## By company ({d})", ""]
        lines += s.breakdown_table(scores, variants, scoring, companies, d)
        lines += ["", f"## By query kind ({d})", ""]
        lines += s.breakdown_table(scores, variants, scoring, kinds, d)
        lines.append("")
    lines += ["## Per query (lenient): rank of the first relevant chunk", "",
              "— = no relevant chunk in the variant's top 5; n/a = the query has no relevant "
              "passage (left out of the means).", ""]
    lines += s.query_table(scores, queries, variants)
    lines.append("")
    return "\n".join(lines)


def metrics_record(result: Mapping[str, Any]) -> dict[str, Any]:
    """The JSON metrics of a score run (summaries, decision, pool misses)."""
    from dataclasses import asdict as _asdict

    return {"summaries": {d: [_asdict(x) for x in xs] for d, xs in result["summaries"].items()},
            "decision": _asdict(result["decision"]),
            "pool_misses": [m["query_id"] for m in result["pool_misses"]],
            "queries": len(result["queries"]), "depth": result["scoring"].depth}


def record_score(run_id: str, single_change: str, conclusion: str,
                 runs_dir: Path = RUNS_DIR, experiments: Path = EXPERIMENTS) -> None:
    """Append the score run to eval/experiments.csv (Appendix F.2)."""
    directory = runs_dir / run_id
    header = json.loads((directory / "header.json").read_text(encoding="utf-8"))
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    with experiments.open("a", newline="") as handle:
        csv.writer(handle).writerow([
            run_id, date.today().isoformat(), "E3", "retrieval",
            "/".join(header["variants"]) + f" @ depth {metrics['depth']}", single_change,
            "e3_labels", "validation (test companies)", json.dumps(metrics, default=str),
            header["git_commit"] + ("-dirty" if header["git_dirty"] else ""),
            header["evidence_manifest_sha256"][:12], conclusion])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("queries", "pool", "sheet", "doc", "validate",
                                            "import", "score", "record"))
    parser.add_argument("--sheet", type=Path, default=None)
    parser.add_argument("--partial", action="store_true", help="allow unlabelled rows")
    parser.add_argument("--run-id", default="e3-0001")
    parser.add_argument("--single-change", default="")
    parser.add_argument("--conclusion", default="")
    args = parser.parse_args(argv)
    spec = load_e3_spec()
    files = _paths(spec)
    if args.command == "score":
        from finagent.config import load_retrieval_config
        from finagent.dataset.build import git_state
        from finagent.evaluation import e3_score as s

        result = score_run(spec, files)
        commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
        header = {"run_id": args.run_id, "git_commit": commit, "git_dirty": dirty,
                  "evidence_manifest_sha256": file_sha256(ROOT / "evidence" / "manifest.json"),
                  "variants": list(spec.variants), "labels_sha256": file_sha256(files["labels"]),
                  "pool_sha256": result["pool_sha256"]}
        directory = RUNS_DIR / args.run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "header.json").write_text(json.dumps(header, indent=2) + "\n")
        (directory / "metrics.json").write_text(
            json.dumps(metrics_record(result), indent=2, default=str) + "\n")
        write_jsonl(directory / "scores.jsonl", result["scores"])
        report = render_scores(result, args.run_id, header,
                               load_retrieval_config().search.default_variant)
        doc = ROOT / "docs" / f"e3_scores_{args.run_id.split('-')[-1]}.md"
        doc.write_text(report, encoding="utf-8")
        for d in (s.PRIMARY, s.SECONDARY):
            print(f"{d}:")
            print("\n".join(s.summary_table(result["summaries"][d], result["scoring"].baseline)))
        print(f"decision: {result['decision'].default} ({result['decision'].reason})")
        print(f"Wrote {directory} and {doc}")
        return 0
    if args.command == "record":
        record_score(args.run_id, args.single_change, args.conclusion)
        print(f"Recorded {args.run_id} in {EXPERIMENTS}")
        return 0
    if args.command in ("queries", "pool", "sheet"):
        from finagent.tools import FinAgentTools

        tools = FinAgentTools.default()
    if args.command == "queries":
        from finagent.retrieval.report import DEV

        with (ROOT / "runs" / spec.insights_run / "signals.csv").open(newline="",
                                                                       encoding="utf-8") as h:
            rows = list(csv.DictReader(h))
        queries = build_queries(tools, spec, tools.universe, DEV, rows)
        write_jsonl(files["queries"], queries)
        print(f"Wrote {files['queries']}: {len(queries)} queries")
        return 0
    queries = load_queries(files["queries"])
    if args.command == "pool":
        retriever = pool_retriever(tools.store, tools.universe, spec.depth)
        rows, keys = build_pool(retriever, queries, spec)
        write_jsonl(files["pool"], rows)
        write_jsonl(files["pool_key"], keys)
        sizes = pool_sizes(rows)
        print(" ".join(f"{q}:{n}" for q, n in sizes.items()))
        print(f"Wrote {files['pool']} ({len(rows)} rows) and the key {files['pool_key']}")
        return 0
    if args.command == "sheet":
        examples = [dev_example(tools, e, spec.as_of) for e in spec.examples]
        path = write_sheet(args.sheet or files["sheet"], queries, load_pool(files["pool"]),
                           spec, examples)
        print(f"Wrote {path}")
        return 0
    if args.command == "doc":
        write_generated(files["doc"], render_generated(queries, load_pool(files["pool"]), spec,
                                                       spec.files["sheet"]))
        print(f"Wrote {files['doc']}")
        return 0
    pool = load_pool(files["pool"])
    header, rows = read_sheet(args.sheet or files["sheet"])
    partial = args.partial and args.command == "validate"
    errors = validate_sheet(header, rows, pool, partial)
    if args.command == "validate":
        labelled = sum(1 for r in rows if r.label not in (None, ""))
        print("\n".join(errors) or f"valid ({labelled} of {len(pool)} rows labelled)")
        return 1 if errors else 0
    if errors:
        print("\n".join(errors))
        print("Not imported: fix the sheet first.")
        return 1
    records = label_records(rows, queries, file_sha256(files["pool"]))
    write_jsonl(files["labels"], records)
    print(f"Imported {len(rows)} labels for {len(records)} queries into {files['labels']}")
    return 0


def dev_example(tools: Any, example: Example, as_of: date) -> tuple[Example, str, str]:
    """A worked example's query statement and chunk text, from a dev company's 10-K."""
    from finagent.retrieval.report import DEV

    if example.issuer not in DEV:
        raise ValueError(f"worked examples come from the dev companies only, not "
                         f"{example.issuer}")
    retriever = tools.search()
    f = retriever.filing(example.issuer, example.fiscal_year, as_of)
    chunk = next(c for c in retriever.index(f).chunks if c.chunk_id == example.chunk_id)
    if example.kind == SIGNAL:
        result = next(r for r in tools.signal_results(example.issuer, example.fiscal_year,
                                                      as_of) if r.signal_id == example.subject)
        statement = signal_statement(result)
    else:
        e = tools.explain_inputs(example.issuer, example.fiscal_year, example.subject,
                                 as_of=as_of)
        statement = explain_statement(example.subject, e.year, e.now.value, e.before.value)
    return example, statement, chunk.text


if __name__ == "__main__":
    raise SystemExit(main())
