"""E4 faithfulness labelling (blueprint 10.3; Phase 7.6; D9): sheet, judge, import and score.

    python -m finagent.evaluation.e4 cards      # gold/e4_cards.jsonl (the selection rule)
    python -m finagent.evaluation.e4 sheet      # eval/e4_labels.xlsx
    python -m finagent.evaluation.e4 judge      # gold/e4_judge_key.jsonl (hidden; never printed)
    python -m finagent.evaluation.e4 doc        # docs/e4_v1.md (text below the marker)
    python -m finagent.evaluation.e4 validate [--sheet PATH] [--partial]
    python -m finagent.evaluation.e4 import   [--sheet PATH]   # -> gold/e4_labels.jsonl
    python -m finagent.evaluation.e4 score  --run-id e4-0001   # after Vineet labels

Evidence v2 (D-EV, post-hoc; configs/e4.yaml `v2`): the same commands with --evidence v2.
`cards` reads the 27 slots again from the v2 insights run (unchanged quotes, changed quotes,
or EVIDENCE_GAP); `sheet`, `judge`, `validate` and `import` work on the changed slots only
(same instructions and worked examples); `doc` writes docs/evidence_v2.md sections 3-4;
`score --run-id e4-0002` combines Vineet's v1 labels (unchanged slots) with his v2 labels.

configs/e4.yaml fixes the card selection rule (quoted cards of the final system's insights run:
every latest-year card, plus each test company's most recent earlier year), the label scale
(supported / partial / unsupported), the judge rule (the pinned NLI cross-encoder, one
hypothesis per fired condition) and the scoring rule (docs/e4_v1.md section 4). EVIDENCE_GAP
cards (no quote) and R10 model-derived cards (#180) are counted, never labelled.

Blinding: the judge's labels are written only to gold/e4_judge_key.jsonl. The sheet never
shows them and no command prints them; `score` reads them only next to Vineet's imported
labels. The worked examples on the instructions tab are dev-company cards kept off the sheet.

Validation companies only (an insights run never holds a held-out company; any other company
is refused). No external AI API: the judge is a local pinned model, loaded offline.
"""

import argparse
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import CONFIG_DIR, ConfigError, _load_yaml
from finagent.evaluation.e3 import _height, file_sha256, read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[2]
E4_PATH = CONFIG_DIR / "e4.yaml"
RUNS_DIR = ROOT / "runs"
VALIDATION = "validation"
QUOTED, EVIDENCE_GAP, MODEL = "quoted", "EVIDENCE_GAP", "model"
SUPPORTED, PARTIAL, UNSUPPORTED = "supported", "partial", "unsupported"
LABELS = (SUPPORTED, PARTIAL, UNSUPPORTED)
MAX_QUOTES = 2
HEADER = ("card_id", "company", "fiscal_year", "signal", "claim", "quote_1", "quote_2", "label",
          "notes", "quote_sections", "accession")
LABEL_SHEET, INSTRUCTIONS_SHEET = "labels", "instructions"
RUN_CHECKS = ("as_of", "signals_sha256", "templates_sha256", "evidence_manifest_sha256")
# Evidence v2 (D-EV): a sheet slot's outcome in the v2 run, and the v2 files.
UNCHANGED, CHANGED = "unchanged", "changed"
SLOT_OUTCOMES = (UNCHANGED, CHANGED, EVIDENCE_GAP)
V2_FILES = ("cards", "slots", "sheet", "judge_key", "labels", "doc")


# ---------------------------------------------------------------- spec

@dataclass(frozen=True)
class Example:
    """A worked example for the instructions tab (a dev-company card kept off the sheet)."""

    issuer: str
    fiscal_year: int
    signal: str
    label: str
    why: str


@dataclass(frozen=True)
class E4Spec:
    as_of: date
    insights_run: str
    files: dict[str, Path]
    earlier_years: int
    earlier_from_dev: bool
    labels: tuple[str, ...]
    seconds_per_card: int
    judge_model: str
    entailment: str
    hypotheses: dict[str, str]
    draws: int
    seed: int
    level: float
    examples: tuple[Example, ...] = ()
    v2_insights_run: str = ""  # evidence v2 (D-EV): the run whose cards fill the 27 slots
    v2_files: dict[str, Path] = field(default_factory=dict)


def condition_ids(signal_config: Any) -> list[str]:
    """Every condition a filing quote can be asked about (signals on model data excluded)."""
    return [c.condition_id for s in signal_config.signals.values() if s.data != "scenario"
            for c in s.conditions + ((s.while_,) if s.while_ else ())]


def load_e4_spec(path: Path | str = E4_PATH, signal_config: Any = None) -> E4Spec:
    from finagent.config import load_signal_config

    data = _load_yaml(Path(path))
    try:
        sel, judge, boot = data["selection"], data["judge"], data["score"]["bootstrap"]
        examples = tuple(Example(str(e["issuer"]), int(e["fiscal_year"]), str(e["signal"]),
                                 str(e["label"]), str(e["why"]))
                         for e in data.get("examples") or ())
        v2 = data.get("v2") or {}
        spec = E4Spec(date.fromisoformat(str(data["as_of"])), str(data["insights_run"]),
                      {k: Path(v) for k, v in data["files"].items()},
                      int(sel["earlier_years"]), bool(sel["earlier_from_dev"]),
                      tuple(str(x) for x in data["labels"]), int(data["seconds_per_card"]),
                      str(judge["model"]), str(judge["entailment"]),
                      {str(k): str(v) for k, v in judge["hypotheses"].items()},
                      int(boot["draws"]), int(boot["seed"]), float(boot["level"]), examples,
                      str(v2.get("insights_run", "")),
                      {k: Path(p) for k, p in (v2.get("files") or {}).items()})
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"e4: {exc}") from exc
    if spec.labels != LABELS:
        raise ConfigError(f"e4: labels must be exactly {list(LABELS)}")
    if any(e.label not in LABELS for e in spec.examples):
        raise ConfigError("e4: an example label is not one of the labels")
    if spec.v2_insights_run and set(spec.v2_files) != set(V2_FILES):
        raise ConfigError(f"e4: v2 files must be exactly {list(V2_FILES)}")
    missing = [c for c in condition_ids(signal_config or load_signal_config())
               if c not in spec.hypotheses]
    if missing:
        raise ConfigError(f"e4: no judge hypothesis for {missing}")
    return spec


# ---------------------------------------------------------------- cards

@dataclass(frozen=True)
class CardQuote:
    chunk_id: str
    section: str
    text: str


@dataclass(frozen=True)
class E4Card:
    """An insight card as E4 reads it. card_id ("E4-C01") only when it is on the sheet."""

    issuer: str
    fiscal_year: int
    signal_id: str
    name: str
    kind: str  # quoted | EVIDENCE_GAP | model
    latest: bool  # the company's latest 10-K filed by as_of
    claim: str  # the card text without its evidence clause
    fired: tuple[str, ...]  # the fired condition ids (the judge's hypotheses)
    quotes: tuple[CardQuote, ...]
    accession: str
    card_id: str = ""


def card_kind(status: str, quotes: int) -> str:
    """quoted, EVIDENCE_GAP or model (VERIFIED with no quote: model-derived, #180)."""
    if status == "VERIFIED":
        return QUOTED if quotes else MODEL
    if status == "EVIDENCE_GAP":
        return EVIDENCE_GAP
    raise ValueError(f"unexpected card status {status!r}")


def claim_text(text: str) -> str:
    """The signal statement with its numbers: the card text before "Evidence:"/"EVIDENCE_GAP:"."""
    return re.split(r" (?:Evidence|EVIDENCE_GAP): ", text, maxsplit=1)[0]


def run_cards(data: Mapping[str, Any], companies: Sequence[str]) -> list[E4Card]:
    """Every card of an insights run's cards.json: companies in `companies` order (the
    validation split; any other company is refused), latest year first."""
    found = []
    by_ticker = {c["ticker"]: c for c in data["companies"]}
    other = sorted(set(by_ticker) - set(companies))
    if other:
        raise ValueError(f"not validation companies: {other}")
    for ticker in [t for t in companies if t in by_ticker]:
        c = by_ticker[ticker]
        if c.get("error"):
            raise ValueError(f"{ticker}: the insights run failed: {c['error'].splitlines()[0]}")
        years = sorted((int(y) for y in c["cards"]), reverse=True)
        for y in years:
            for k in c["cards"][str(y)]:
                quotes = tuple(CardQuote(q["chunk_id"], q["section"], q["text"])
                               for q in k["evidence"])
                fired = tuple(m["condition_id"] for m in k["metrics"] if m["state"] == "FIRED")
                found.append(E4Card(ticker, y, k["signal_id"], k["name"],
                                    card_kind(k["status"], len(quotes)), y == years[0],
                                    claim_text(k["text"]), fired, quotes, k["accession"]))
    return found


def check_run(header: Mapping[str, Any], current: Mapping[str, str]) -> None:
    """The insights run must match today's as_of, signals, query templates and evidence."""
    stale = [k for k in RUN_CHECKS if str(header.get(k)) != str(current[k])]
    if stale:
        raise ValueError(f"insights run {header.get('run_id')} differs from today on {stale}: "
                         "re-run insights first")


def select_cards(cards: Sequence[E4Card], dev: Sequence[str], spec: E4Spec) -> list[E4Card]:
    """The rule of configs/e4.yaml; every card is returned, the selected ones numbered."""
    chosen: set[int] = set()
    for company in dict.fromkeys(k.issuer for k in cards):
        mine = [n for n, k in enumerate(cards) if k.issuer == company and k.kind == QUOTED]
        chosen |= {n for n in mine if cards[n].latest}
        if company in dev and not spec.earlier_from_dev:
            continue
        years = sorted({cards[n].fiscal_year for n in mine if not cards[n].latest},
                       reverse=True)[:spec.earlier_years]
        chosen |= {n for n in mine if cards[n].fiscal_year in years}
    found, count = [], 0
    for n, k in enumerate(cards):
        if n in chosen:
            count += 1
            k = replace(k, card_id=f"E4-C{count:02d}")
        found.append(k)
    return found


def on_sheet(cards: Iterable[E4Card]) -> list[E4Card]:
    return [k for k in cards if k.card_id]


def census(cards: Sequence[E4Card]) -> dict[str, dict[str, int]]:
    """Cards by kind: every year, the latest year, and on the sheet."""
    def count(xs: Iterable[E4Card]) -> dict[str, int]:
        c = Counter(k.kind for k in xs)
        return {kind: c[kind] for kind in (QUOTED, EVIDENCE_GAP, MODEL)}
    return {"all_years": count(cards), "latest_year": count(k for k in cards if k.latest),
            "on_sheet": count(on_sheet(cards))}


def load_cards(path: Path) -> list[E4Card]:
    return [E4Card(**{**r, "fired": tuple(r["fired"]),
                      "quotes": tuple(CardQuote(**q) for q in r["quotes"])})
            for r in read_jsonl(path)]


def example_cards(cards: Sequence[E4Card], spec: E4Spec, dev: Sequence[str]
                  ) -> list[tuple[Example, E4Card]]:
    """Each worked example's card: a quoted dev-company card that is not on the sheet."""
    found = []
    for e in spec.examples:
        if e.issuer not in dev:
            raise ValueError(f"worked examples come from the dev companies only, not {e.issuer}")
        card = next((k for k in cards if (k.issuer, k.fiscal_year, k.signal_id) ==
                     (e.issuer, e.fiscal_year, e.signal) and k.kind == QUOTED), None)
        if card is None or card.card_id:
            raise ValueError(f"example {e.issuer} FY{e.fiscal_year} {e.signal}: not a quoted "
                             "card off the sheet")
        found.append((e, card))
    return found


# ---------------------------------------------------------------- sheet

def write_sheet(path: Path, cards: Sequence[E4Card], spec: E4Spec,
                examples: Sequence[tuple[Example, E4Card]] = ()) -> Path:
    """eval/e4_labels.xlsx: the labels tab (one row per selected card) and instructions."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    widths = {"card_id": 9, "company": 9, "fiscal_year": 8, "signal": 7, "claim": 48,
              "quote_1": 60, "quote_2": 60, "label": 12, "notes": 28, "quote_sections": 14,
              "accession": 22}
    wb = Workbook()
    guide = wb.active
    guide.title = INSTRUCTIONS_SHEET
    sheet = wb.create_sheet(LABEL_SHEET)
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    head_fill = PatternFill("solid", fgColor="DDE4EE")
    band = (PatternFill("solid", fgColor="FFFFFF"), PatternFill("solid", fgColor="F3F3F3"))
    for col, name in enumerate(HEADER, 1):
        cell = sheet.cell(1, col, name)
        cell.font, cell.fill, cell.alignment = bold, head_fill, wrap
        sheet.column_dimensions[cell.column_letter].width = widths[name]
    choice = DataValidation(type="list", formula1='"' + ",".join(LABELS) + '"',
                            allow_blank=True, showErrorMessage=True, errorTitle="Label",
                            error="Choose supported, partial or unsupported.")
    sheet.add_data_validation(choice)
    col = {name: n for n, name in enumerate(HEADER, 1)}
    for r, k in enumerate(on_sheet(cards), 2):
        if len(k.quotes) > MAX_QUOTES or not k.quotes:
            raise ValueError(f"{k.card_id}: {len(k.quotes)} quotes (the sheet shows 1 or 2)")
        texts = [q.text for q in k.quotes] + [""] * (MAX_QUOTES - len(k.quotes))
        values = {"card_id": k.card_id, "company": k.issuer, "fiscal_year": k.fiscal_year,
                  "signal": k.signal_id, "claim": k.claim, "quote_1": texts[0],
                  "quote_2": texts[1], "quote_sections": "; ".join(q.section for q in k.quotes),
                  "accession": k.accession}
        for name, value in values.items():
            cell = sheet.cell(r, col[name], value)
            if isinstance(value, str) and value.startswith("="):
                cell.data_type = "s"  # text, never a formula
        for c in range(1, len(HEADER) + 1):
            sheet.cell(r, c).alignment, sheet.cell(r, c).fill = wrap, band[r % 2]
        sheet.row_dimensions[r].height = max(_height(t, widths["quote_1"]) for t in
                                             (k.claim, *texts))
        choice.add(sheet.cell(r, col["label"]))
    sheet.freeze_panes = "F2"
    _instructions(guide, spec, examples, bold, wrap)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _instructions(ws: Any, spec: E4Spec, examples: Sequence[tuple[Example, E4Card]],
                  bold: Any, wrap: Any) -> None:
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 110
    lines: list[tuple[str, str]] = [
        ("E4 labelling", "Each row of the labels tab is one insight card: the claim (the signal "
         "that fired, with its numbers) and the one or two sentences the system quoted from "
         "the same 10-K as its evidence. Question: do the quotes support the card's "
         "explanation of the movement?"),
        ("supported", "The quotes, read together, explain the movement the claim reports (give "
         "its reason) for this company and year, and nothing in them contradicts the claim."),
        ("partial", "They touch the movement but do not fully explain it: they restate the "
         "change without a reason, explain only part of the claim (one of two conditions, or "
         "a related figure), or speak of another period (for example a plan for coming years)."),
        ("unsupported", "They are about something else, or contradict the claim (for example "
         "the opposite direction)."),
        ("whole card", "Label the card on what its quotes show together. An extra quote that "
         "adds nothing does not lower the label; say so in notes if you like."),
        ("notes", "Optional, free text."),
        ("rules", "Label every row (dropdown). Do not add, delete, sort or edit rows, claims, "
         "quotes or ids. The sheet does not show any other rater's label; do not open "
         "gold/e4_judge_key.jsonl."),
        ("when done", "Save as .xlsx, then run: python -m finagent.evaluation.e4 validate, "
         "then python -m finagent.evaluation.e4 import."),
        ("time", f"About {spec.seconds_per_card} seconds per card."),
    ]
    for n, (example, card) in enumerate(examples, 1):
        lines += [(f"example {n}", f"{card.issuer} FY{card.fiscal_year} {card.signal_id} (a dev "
                   "company, not on the labels tab)"), ("claim", card.claim)]
        lines += [(f"quote {m}", q.text) for m, q in enumerate(card.quotes, 1)]
        lines += [("label", example.label), ("why", example.why)]
    for r, (key, value) in enumerate(lines, 1):
        ws.cell(r, 1, key).font = bold
        cell = ws.cell(r, 2, value)
        cell.alignment = wrap
        ws.row_dimensions[r].height = _height(value, 110)


# ---------------------------------------------------------------- import and validate

@dataclass(frozen=True)
class SheetRow:
    card_id: str
    claim: str
    quote_1: str
    quote_2: str
    label: Any
    notes: str
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
        found.append(SheetRow(str(get("card_id") or ""), str(get("claim") or ""),
                              str(get("quote_1") or ""), str(get("quote_2") or ""), get("label"),
                              str(get("notes") or "").strip(), n))
    return header, found


def parse_label(value: Any) -> str | None:
    """"Supported " -> "supported"; blank -> None; anything else -> ValueError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    label = str(value).strip().lower()
    if label not in LABELS:
        raise ValueError(value)
    return label


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def validate_sheet(header: Sequence[str], rows: Sequence[SheetRow], cards: Sequence[E4Card],
                   partial: bool = False) -> list[str]:
    """Errors of a filled sheet against the selected cards (empty: valid). partial: unlabelled
    rows are allowed (progress check); import never allows them."""
    if tuple(header) != HEADER:
        return [f"the labels tab header must be {list(HEADER)}"]
    expected = {k.card_id: k for k in on_sheet(cards)}
    errors, seen = [], set()
    for r in rows:
        where = f"row {r.row} ({r.card_id})"
        k = expected.get(r.card_id)
        if k is None:
            errors.append(f"{where}: not a card of the sheet")
            continue
        if r.card_id in seen:
            errors.append(f"{where}: duplicate row")
        seen.add(r.card_id)
        texts = [q.text for q in k.quotes] + [""] * (MAX_QUOTES - len(k.quotes))
        if not (_same(r.claim, k.claim) and _same(r.quote_1, texts[0])
                and _same(r.quote_2, texts[1])):
            errors.append(f"{where}: the claim or a quote was changed")
        try:
            label = parse_label(r.label)
        except ValueError:
            errors.append(f"{where}: label {r.label!r} is not one of {', '.join(LABELS)}")
            continue
        if label is None and not partial:
            errors.append(f"{where}: no label")
    missing = sorted(set(expected) - seen)
    if missing:
        errors.append(f"{len(missing)} cards are missing from the sheet (first: {missing[0]})")
    return errors


def label_records(rows: Sequence[SheetRow], cards: Sequence[E4Card], cards_sha256: str
                  ) -> list[dict[str, Any]]:
    """One record per card of a validated sheet, in card order."""
    by_id = {r.card_id: r for r in rows}
    return [{"card_id": k.card_id, "issuer": k.issuer, "fiscal_year": k.fiscal_year,
             "signal_id": k.signal_id, "label": parse_label(by_id[k.card_id].label),
             "notes": by_id[k.card_id].notes, "cards_sha256": cards_sha256}
            for k in on_sheet(cards)]


# ---------------------------------------------------------------- judge

Predict = Callable[[Sequence[tuple[str, str]]], list[dict[str, float]]]


def hypotheses_for(card: E4Card, spec: E4Spec) -> tuple[str, ...]:
    """One hypothesis per fired condition of the card (configs/e4.yaml judge.hypotheses)."""
    missing = [c for c in card.fired if c not in spec.hypotheses]
    if missing or not card.fired:
        raise ValueError(f"{card.card_id or card.signal_id}: no hypothesis for "
                         f"{missing or 'a card without a fired condition'}")
    return tuple(spec.hypotheses[c] for c in card.fired)


def judge_card(card: E4Card, predict: Predict, spec: E4Spec) -> dict[str, Any]:
    """The judge rule: a condition is entailed when some quote's most probable class is
    entailment; supported = all conditions entailed, partial = some, unsupported = none."""
    hyps = hypotheses_for(card, spec)
    pairs = [(q.text, h) for h in hyps for q in card.quotes]
    probs = predict(pairs)
    if len(probs) != len(pairs):
        raise ValueError(f"{card.card_id}: {len(probs)} predictions for {len(pairs)} pairs")
    claims, n = [], len(card.quotes)
    for i, (cid, h) in enumerate(zip(card.fired, hyps)):
        rows = probs[i * n:(i + 1) * n]
        classes = [max(r, key=lambda name: r[name]) for r in rows]
        claims.append({"condition_id": cid, "hypothesis": h,
                       "entailed": spec.entailment in classes,
                       "quotes": [{"class": c, "probs": {k: round(float(v), 6)
                                                         for k, v in r.items()}}
                                  for c, r in zip(classes, rows)]})
    entailed = sum(c["entailed"] for c in claims)
    label = SUPPORTED if entailed == len(claims) else PARTIAL if entailed else UNSUPPORTED
    return {"card_id": card.card_id, "label": label, "claims": claims}


def nli_predict(model: Any, names: Mapping[int, str], batch_size: int = 16) -> Predict:
    """Softmax probabilities by label name from a sentence-transformers CrossEncoder."""
    def predict(pairs: Sequence[tuple[str, str]]) -> list[dict[str, float]]:
        out = model.predict(list(pairs), batch_size=batch_size, apply_softmax=True,
                            show_progress_bar=False)
        return [{names[j]: float(p) for j, p in enumerate(row)} for row in out]
    return predict


def judge_records(cards: Sequence[E4Card], predict: Predict, spec: E4Spec,
                  judge: Mapping[str, str], cards_sha256: str) -> list[dict[str, Any]]:
    """The judge's label for every card on the sheet, with the judge's pin and the cards file."""
    return [{**judge_card(k, predict, spec), "judge": dict(judge), "cards_sha256": cards_sha256}
            for k in on_sheet(cards)]


# ---------------------------------------------------------------- score (after Vineet labels)

def cohen_kappa(a: Sequence[str], b: Sequence[str], labels: Sequence[str]) -> float | None:
    """Unweighted Cohen's kappa; None when chance agreement is 1 (a single label used)."""
    if len(a) != len(b):
        raise ValueError("paired labels of different lengths")
    if not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    chance = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    if chance >= 1:
        return None
    return (observed - chance) / (1 - chance)


def bootstrap_kappa(a: Sequence[str], b: Sequence[str], labels: Sequence[str], draws: int,
                    seed: int, level: float
                    ) -> tuple[float | None, tuple[float, float] | None, int]:
    """Kappa, its percentile interval over resampled cards, and the draws skipped (undefined)."""
    import numpy as np

    kappa = cohen_kappa(a, b, labels)
    if not a:
        return kappa, None, 0
    rng = np.random.default_rng(seed)
    found, skipped = [], 0
    for idx in rng.integers(0, len(a), size=(draws, len(a))):
        k = cohen_kappa([a[i] for i in idx], [b[i] for i in idx], labels)
        if k is None:
            skipped += 1
        else:
            found.append(k)
    if not found:
        return kappa, None, skipped
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(found, [tail, 100 - tail])
    return kappa, (float(lo), float(hi)), skipped


def binary(labels: Sequence[str]) -> list[str]:
    return [SUPPORTED if x == SUPPORTED else "not supported" for x in labels]


SOME_SUPPORT = "supported or partial"


def some_support(labels: Sequence[str]) -> list[str]:
    """supported or partial vs unsupported: the judge's supported tests only that the quotes
    state the movement, closer to Vineet's supported-or-partial (#291; report only)."""
    return [UNSUPPORTED if x == UNSUPPORTED else SOME_SUPPORT for x in labels]


def score_e4(labels: Sequence[Mapping[str, Any]], judged: Sequence[Mapping[str, Any]],
             cards: Sequence[E4Card], cards_sha256: str, spec: E4Spec) -> dict[str, Any]:
    """Vineet's labels (the reference) against the judge's, by the rule of configs/e4.yaml."""
    for name, records in (("labels", labels), ("judge key", judged)):
        if any(r["cards_sha256"] != cards_sha256 for r in records):
            raise ValueError(f"the {name} were made on another cards file")
    ids = [k.card_id for k in on_sheet(cards)]
    human = {r["card_id"]: r["label"] for r in labels}
    judge = {r["card_id"]: r["label"] for r in judged}
    missing = [i for i in ids if human.get(i) not in LABELS or judge.get(i) not in LABELS]
    if missing:
        raise ValueError(f"cards without both labels: {missing[:3]}")
    return {**pair_scores([human[i] for i in ids], [judge[i] for i in ids], spec),
            "evidence_gap": evidence_gap_share(cards)}


def pair_scores(a: Sequence[str], b: Sequence[str], spec: E4Spec) -> dict[str, Any]:
    """The label measures of configs/e4.yaml for paired labels (a Vineet's, b the judge's)."""
    from finagent.evaluation.e3_score import bootstrap_mean

    boot = (spec.draws, spec.seed, spec.level)

    def rate(xs: Sequence[str], label: str) -> Any:
        return bootstrap_mean([1.0 if x == label else 0.0 for x in xs], *boot)
    return {"cards": len(a), "human": dict(Counter(a)), "judge": dict(Counter(b)),
            "unsupported_rate": rate(a, UNSUPPORTED), "partial_rate": rate(a, PARTIAL),
            "judge_unsupported_rate": rate(b, UNSUPPORTED),
            "kappa": bootstrap_kappa(a, b, LABELS, *boot),
            "kappa_binary": bootstrap_kappa(binary(a), binary(b),
                                            (SUPPORTED, "not supported"), *boot),
            "kappa_some_support": bootstrap_kappa(some_support(a), some_support(b),
                                                  (SOME_SUPPORT, UNSUPPORTED), *boot),
            "confusion": {h: {j: sum(1 for x, y in zip(a, b) if (x, y) == (h, j))
                              for j in LABELS} for h in LABELS}}


def evidence_gap_share(cards: Sequence[E4Card]) -> dict[str, dict[str, Any]]:
    """EVIDENCE_GAP cards / (quoted + EVIDENCE_GAP), every year and the latest year."""
    counts = census(cards)
    gap = {}
    for when in ("all_years", "latest_year"):
        c = counts[when]
        denominator = c[QUOTED] + c[EVIDENCE_GAP]
        gap[when] = {"evidence_gap": c[EVIDENCE_GAP], "of": denominator,
                     "share": c[EVIDENCE_GAP] / denominator if denominator else None,
                     "model_derived": c[MODEL]}
    return gap


# ---------------------------------------------------------------- evidence v2 (D-EV, post-hoc)

@dataclass(frozen=True)
class Slot:
    """One card slot of the v1 sheet and its outcome in the v2 run (configs/e4.yaml v2)."""

    card_id: str
    issuer: str
    fiscal_year: int
    signal_id: str
    outcome: str  # unchanged | changed | EVIDENCE_GAP


def same_quotes(a: E4Card, b: E4Card) -> bool:
    return [(q.chunk_id, q.text) for q in a.quotes] == [(q.chunk_id, q.text) for q in b.quotes]


def v2_slots(v1_cards: Sequence[E4Card], v2_cards: Sequence[E4Card]
             ) -> tuple[list[Slot], list[E4Card]]:
    """Each v1 sheet slot's outcome in the v2 run, and the v2 run's cards with the changed
    slots' ids (the v2 sheet). A slot must keep its claim: the signals did not change."""
    keys = [(k.issuer, k.fiscal_year, k.signal_id) for k in v2_cards]
    if len(set(keys)) != len(keys):
        raise ValueError("the v2 run has two cards for one company, year and signal")
    out = [replace(k, card_id="") for k in v2_cards]
    by_key = {key: n for n, key in enumerate(keys)}
    slots = []
    for old in on_sheet(v1_cards):
        n = by_key.get((old.issuer, old.fiscal_year, old.signal_id))
        if n is None:
            raise ValueError(f"{old.card_id}: no card in the v2 run")
        new = out[n]
        if new.claim != old.claim or new.kind == MODEL:
            raise ValueError(f"{old.card_id}: the v2 card's claim or kind differs")
        if new.kind == EVIDENCE_GAP:
            outcome = EVIDENCE_GAP
        elif same_quotes(old, new):
            outcome = UNCHANGED
        else:
            outcome = CHANGED
            out[n] = replace(new, card_id=old.card_id)
        slots.append(Slot(old.card_id, old.issuer, old.fiscal_year, old.signal_id, outcome))
    return slots, out


def load_slots(path: Path) -> list[Slot]:
    return [Slot(**r) for r in read_jsonl(path)]


def compare_runs(old: Sequence[E4Card], new: Sequence[E4Card]) -> list[dict[str, Any]]:
    """Per company and in total: cards by kind in each run, and how the quoted ones changed
    (same quotes, other quotes, quoted -> EVIDENCE_GAP, EVIDENCE_GAP -> quoted)."""
    key = lambda k: (k.issuer, k.fiscal_year, k.signal_id)  # noqa: E731
    before = {key(k): k for k in old}
    rows = []
    for company in [*dict.fromkeys(k.issuer for k in [*old, *new]), "total"]:
        mine_old = [k for k in old if company in (k.issuer, "total")]
        mine_new = [k for k in new if company in (k.issuer, "total")]
        pairs = [(before[key(k)], k) for k in mine_new if key(k) in before]
        row: dict[str, Any] = {"company": company}
        for name, cards in (("v1", mine_old), ("v2", mine_new)):
            c = Counter(k.kind for k in cards)
            row.update({f"{name}_{kind}": c[kind] for kind in (QUOTED, EVIDENCE_GAP, MODEL)})
        row.update({
            "same_quotes": sum(a.kind == b.kind == QUOTED and same_quotes(a, b)
                               for a, b in pairs),
            "changed_quotes": sum(a.kind == b.kind == QUOTED and not same_quotes(a, b)
                                  for a, b in pairs),
            "quoted_to_gap": sum((a.kind, b.kind) == (QUOTED, EVIDENCE_GAP) for a, b in pairs),
            "gap_to_quoted": sum((a.kind, b.kind) == (EVIDENCE_GAP, QUOTED) for a, b in pairs),
            "unmatched": len(mine_new) - len(pairs)})
        rows.append(row)
    return rows


def combined_labels(slots: Sequence[Slot], v1: Mapping[str, str], v2: Mapping[str, str]
                    ) -> dict[str, str]:
    """card_id -> label for the slots still quoted: v1's for unchanged, v2's for changed."""
    found = {}
    for s in slots:
        if s.outcome == EVIDENCE_GAP:
            continue
        source = v1 if s.outcome == UNCHANGED else v2
        if source.get(s.card_id) not in LABELS:
            raise ValueError(f"{s.card_id} ({s.outcome}): no label")
        found[s.card_id] = source[s.card_id]
    return found


def score_e4_v2(slots: Sequence[Slot], v1: Mapping[str, Any], v2: Mapping[str, Any],
                spec: E4Spec) -> dict[str, Any]:
    """e4-0002 by the rule of configs/e4.yaml v2. v1 and v2 each hold labels, judged (records),
    cards_sha256 and cards; every record must come from its own cards file."""
    from finagent.evaluation.e3_score import bootstrap_mean

    for name, side in (("v1", v1), ("v2", v2)):
        for kind in ("labels", "judged"):
            if any(r["cards_sha256"] != side["cards_sha256"] for r in side[kind]):
                raise ValueError(f"the {name} {kind} were made on another cards file")
    human = combined_labels(slots, *({r["card_id"]: r["label"] for r in side["labels"]}
                                     for side in (v1, v2)))
    judge = combined_labels(slots, *({r["card_id"]: r["label"] for r in side["judged"]}
                                     for side in (v1, v2)))
    ids = list(human)
    boot = (spec.draws, spec.seed, spec.level)
    per_slot = [human.get(s.card_id, EVIDENCE_GAP) for s in slots]
    return {**pair_scores([human[i] for i in ids], [judge[i] for i in ids], spec),
            "slots": len(slots), "outcomes": dict(Counter(s.outcome for s in slots)),
            "slot_shares": {o: bootstrap_mean([1.0 if x == o else 0.0 for x in per_slot],
                                              *boot) for o in (*LABELS, EVIDENCE_GAP)},
            "evidence_gap": evidence_gap_share(v2["cards"])}


def _ci(value: Any) -> str:
    if value is None:
        return "—"
    mean, interval = value
    return f"{mean:.3f}" + (f" [{interval[0]:.3f}, {interval[1]:.3f}]" if interval else "")


def _kappa(value: tuple[Any, Any, int]) -> str:
    kappa, interval, skipped = value
    if kappa is None:
        return "undefined (one label only)"
    text = f"{kappa:.3f}" + (f" [{interval[0]:.3f}, {interval[1]:.3f}]" if interval else "")
    return text + (f" ({skipped} undefined draws skipped)" if skipped else "")


def render_score(result: Mapping[str, Any], run_id: str, header: Mapping[str, Any]) -> str:
    """The E4 score report (markdown)."""
    gap = result["evidence_gap"]
    lines = [f"# E4 faithfulness scores ({run_id})", "",
             f"Generated by `python -m finagent.evaluation.e4 score --run-id {run_id}` by the rule "
             "written before any label (docs/e4_v1.md section 4). Commit "
             f"`{header['git_commit']}`{' + uncommitted changes' if header['git_dirty'] else ''}"
             f"; {result['cards']} cards; Vineet's labels are the reference; judge "
             f"`{header['judge']}`.", "",
             "| measure | value [95% CI] |", "|---|---|",
             f"| unsupported-claim rate (Vineet) | {_ci(result['unsupported_rate'])} |",
             f"| partial rate (Vineet) | {_ci(result['partial_rate'])} |",
             f"| unsupported rate (judge) | {_ci(result['judge_unsupported_rate'])} |",
             f"| Cohen's kappa, three labels | {_kappa(result['kappa'])} |",
             f"| Cohen's kappa, supported vs not | {_kappa(result['kappa_binary'])} |",
             "| Cohen's kappa, supported or partial vs unsupported (report only; rubric "
             f"mismatch, #291) | {_kappa(result['kappa_some_support'])} |"]
    for when, name in (("all_years", "every year"), ("latest_year", "latest year")):
        g = gap[when]
        share = f"{g['share']:.3f}" if g["share"] is not None else "—"
        lines.append(f"| EVIDENCE_GAP share, {name} | {share} ({g['evidence_gap']} of {g['of']}; "
                     f"R10 model-derived {g['model_derived']} counted separately) |")
    lines += ["", "Labels (rows Vineet, columns judge):", "",
              "| Vineet \\ judge | " + " | ".join(LABELS) + " | total |",
              "|---" * (len(LABELS) + 2) + "|"]
    for h in LABELS:
        row = result["confusion"][h]
        lines.append(f"| {h} | " + " | ".join(str(row[j]) for j in LABELS)
                     + f" | {sum(row.values())} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- doc

GENERATED = "<!-- generated by python -m finagent.evaluation.e4 doc; text below is replaced -->"


def render_generated(cards: Sequence[E4Card], spec: E4Spec) -> str:
    """The card list and counts (nothing about the judge's labels)."""
    counts = census(cards)
    shown = on_sheet(cards)
    minutes = len(shown) * spec.seconds_per_card / 60
    lines = ["## 5. The cards", "",
             "| id | company | FY | year | signal | quotes | fired conditions |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {k.card_id} | {k.issuer} | {k.fiscal_year} | "
              f"{'latest' if k.latest else 'earlier'} | {k.signal_id} | {len(k.quotes)} | "
              f"{', '.join(k.fired)} |" for k in shown]
    by_company = Counter(k.issuer for k in shown)
    lines += ["", "## 6. Counts and sheet", "",
              "| cards | quoted | EVIDENCE_GAP | R10 model-derived |", "|---|---|---|---|"]
    for when, name in (("all_years", "every year"), ("latest_year", "latest year"),
                       ("on_sheet", "on the sheet")):
        c = counts[when]
        lines.append(f"| {name} | {c[QUOTED]} | {c[EVIDENCE_GAP]} | {c[MODEL]} |")
    lines += ["", f"- On the sheet: **{len(shown)} cards** ("
              f"{sum(k.latest for k in shown)} latest-year, "
              f"{sum(not k.latest for k in shown)} earlier-year); per company: "
              + ", ".join(f"{c} {n}" for c, n in by_company.items()) + ".",
              f"- Labelling time at about {spec.seconds_per_card} s per card: about "
              f"{minutes:.0f} minutes.",
              f"- Sheet: `{spec.files['sheet']}`. Cards: `{spec.files['cards']}`. Judge key "
              f"(hidden): `{spec.files['judge_key']}`.", ""]
    return "\n".join(lines)


def write_generated(path: Path, text: str, marker: str = GENERATED) -> None:
    """Replace everything after the marker (the hand-written part above is kept)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    head = old.split(marker, 1)[0].rstrip("\n")
    path.write_text(head + "\n\n" + marker + "\n\n" + text, encoding="utf-8")


GENERATED_V2 = ("<!-- generated by python -m finagent.evaluation.e4 doc --evidence v2; text below "
                "is replaced -->")


def render_v2_generated(old: Sequence[E4Card], new: Sequence[E4Card], slots: Sequence[Slot],
                        spec: E4Spec) -> str:
    """docs/evidence_v2.md: the v2 run against the v1 run per company, and the 27 slots."""
    rows = compare_runs(old, new)
    lines = [f"## 3. {spec.v2_insights_run} (v2) against {spec.insights_run} (v1)", "",
             "Every card of each run (all fiscal years). Quoted = VERIFIED with a quote; R10 "
             "model-derived cards are counted apart. Changes are over the cards present in both "
             "runs.", "",
             "| company | quoted v1 → v2 | EVIDENCE_GAP v1 → v2 | R10 model | same quotes | "
             "other quotes | quoted → gap | gap → quoted |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        name = "**total**" if r["company"] == "total" else r["company"]
        lines.append(f"| {name} | {r['v1_quoted']} → {r['v2_quoted']} | "
                     f"{r['v1_EVIDENCE_GAP']} → {r['v2_EVIDENCE_GAP']} | "
                     f"{r['v1_model']} → {r['v2_model']} | {r['same_quotes']} | "
                     f"{r['changed_quotes']} | {r['quoted_to_gap']} | {r['gap_to_quoted']} |")
    unmatched = rows[-1]["unmatched"]
    if unmatched:
        lines += ["", f"{unmatched} v2 card(s) have no v1 card for the same company, year and "
                      "signal."]
    by = Counter(s.outcome for s in slots)
    changed = [s for s in slots if s.outcome == CHANGED]
    minutes = len(changed) * spec.seconds_per_card / 60
    lines += ["", "## 4. The 27 E4 slots under v2", "",
              f"- Unchanged quotes (v1 labels carry over): **{by[UNCHANGED]}**.",
              f"- Changed quotes (on the v2 sheet): **{by[CHANGED]}**.",
              f"- Now EVIDENCE_GAP (listed, not labelled): **{by[EVIDENCE_GAP]}**.",
              f"- Sheet `{spec.v2_files['sheet']}`: {len(changed)} cards, about {minutes:.0f} "
              f"minutes at {spec.seconds_per_card} s per card. Same instructions and worked "
              f"examples as `{spec.files['sheet']}`. Judge key (hidden): "
              f"`{spec.v2_files['judge_key']}`.", "",
              "| slot | company | FY | signal | under v2 |", "|---|---|---|---|---|"]
    lines += [f"| {s.card_id} | {s.issuer} | {s.fiscal_year} | {s.signal_id} | {s.outcome} |"
              for s in slots]
    return "\n".join(lines) + "\n"


def render_score_v2(result: Mapping[str, Any], run_id: str, header: Mapping[str, Any],
                    previous: Mapping[str, Any] | None, previous_id: str) -> str:
    """The e4-0002 report: the v1-and-v2 combined labels, beside e4-0001 (never instead)."""
    def col(r: Mapping[str, Any] | None, key: str, kappa: bool = False) -> str:
        if r is None:
            return "—"
        return _kappa(r[key]) if kappa else _ci(r[key])
    lines = [f"# E4 faithfulness scores, evidence v2 ({run_id}; post-hoc)", "",
             f"Generated by `python -m finagent.evaluation.e4 score --evidence v2 --run-id "
             f"{run_id}` by the rule written before any v2 label (configs/e4.yaml `v2`; "
             f"docs/evidence_v2.md). **Post-hoc** (D-EV); reported beside {previous_id}, never "
             f"instead of it. Commit `{header['git_commit']}`"
             f"{' + uncommitted changes' if header['git_dirty'] else ''}; judge "
             f"`{header['judge']}`.", "",
             f"Slots: {result['slots']} ("
             + ", ".join(f"{o} {result['outcomes'].get(o, 0)}" for o in SLOT_OUTCOMES)
             + f"). Labels: v1 labels for unchanged slots, v2 labels for changed ones; "
             f"{result['cards']} slots still quoted.", "",
             f"| measure | {run_id} (v2) | {previous_id} (v1) |", "|---|---|---|",
             f"| unsupported-claim rate (Vineet), quoted slots | "
             f"{col(result, 'unsupported_rate')} | {col(previous, 'unsupported_rate')} |",
             f"| partial rate (Vineet), quoted slots | {col(result, 'partial_rate')} | "
             f"{col(previous, 'partial_rate')} |",
             f"| unsupported rate (judge), quoted slots | {col(result, 'judge_unsupported_rate')}"
             f" | {col(previous, 'judge_unsupported_rate')} |",
             f"| Cohen's kappa, three labels | {col(result, 'kappa', True)} | "
             f"{col(previous, 'kappa', True)} |",
             f"| Cohen's kappa, supported vs not | {col(result, 'kappa_binary', True)} | "
             f"{col(previous, 'kappa_binary', True)} |",
             f"| Cohen's kappa, supported or partial vs unsupported | "
             f"{col(result, 'kappa_some_support', True)} | "
             f"{col(previous, 'kappa_some_support', True)} |", "",
             f"Over all {result['slots']} slots (Vineet's label, or EVIDENCE_GAP):", "",
             "| outcome | share [95% CI] |", "|---|---|"]
    lines += [f"| {o} | {_ci(result['slot_shares'][o])} |" for o in (*LABELS, EVIDENCE_GAP)]
    for when, name in (("all_years", "every year"), ("latest_year", "latest year")):
        g = result["evidence_gap"][when]
        share = f"{g['share']:.3f}" if g["share"] is not None else "—"
        lines.append(f"\nEVIDENCE_GAP share of the v2 run's cards, {name}: {share} "
                     f"({g['evidence_gap']} of {g['of']}; R10 model-derived {g['model_derived']} "
                     "counted separately).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- command

def current_hashes(as_of: date) -> dict[str, str]:
    """Today's as_of, signals.yaml, query templates and evidence manifest (vs the run's)."""
    from finagent.config import SIGNALS_PATH
    from finagent.retrieval.templates import load_query_templates

    return {"as_of": as_of.isoformat(), "signals_sha256": file_sha256(SIGNALS_PATH),
            "templates_sha256": load_query_templates().sha256,
            "evidence_manifest_sha256": file_sha256(ROOT / "evidence" / "manifest.json")}


def build_cards(spec: E4Spec, universe: Mapping[str, str], dev: Sequence[str],
                runs_dir: Path = RUNS_DIR, current: Mapping[str, str] | None = None
                ) -> list[E4Card]:
    """Every card of the insights run, checked against today, with the selection applied."""
    data = json.loads((runs_dir / spec.insights_run / "cards.json").read_text(encoding="utf-8"))
    check_run(data["header"], current or current_hashes(spec.as_of))
    companies = [t for t, s in universe.items() if s == VALIDATION]
    return select_cards(run_cards(data, companies), dev, spec)


def import_labels(sheet: Path, cards_path: Path, labels_path: Path) -> list[str]:
    """Validate the filled sheet and write the label records (errors: nothing is written)."""
    cards = load_cards(cards_path)
    header, rows = read_sheet(sheet)
    errors = validate_sheet(header, rows, cards)
    if not errors:
        write_jsonl(labels_path, label_records(rows, cards, file_sha256(cards_path)))
    return errors


def _paths(spec: E4Spec) -> dict[str, Path]:
    return {k: ROOT / v for k, v in spec.files.items()}


def build_v2(spec: E4Spec, v1_cards: Sequence[E4Card], universe: Mapping[str, str],
             runs_dir: Path = RUNS_DIR, current: Mapping[str, str] | None = None
             ) -> tuple[list[Slot], list[E4Card]]:
    """The v2 run's cards (checked against today) and the 27 slots' outcomes (D-EV)."""
    data = json.loads((runs_dir / spec.v2_insights_run / "cards.json").read_text(
        encoding="utf-8"))
    check_run(data["header"], current or current_hashes(spec.as_of))
    companies = [t for t, s in universe.items() if s == VALIDATION]
    return v2_slots(v1_cards, run_cards(data, companies))


def _judge_pin(spec: E4Spec) -> tuple[Predict, dict[str, str]]:
    from finagent.config import load_model_pins
    from finagent.retrieval.models import load_nli_judge

    pin = load_model_pins()[spec.judge_model]
    model, names = load_nli_judge(pin)
    if spec.entailment not in names.values():
        raise ConfigError(f"e4: the judge has no {spec.entailment!r} label: {names}")
    return nli_predict(model, names), {"model": pin.model_id, "repo": pin.repo,
                                       "revision": pin.revision, "fingerprint": pin.fingerprint}


def _judge_text(judged: Sequence[Mapping[str, Any]]) -> str:
    j = judged[0]["judge"] if judged else {}
    return f"{j.get('model')} @ {str(j.get('revision'))[:12]}"


def _write_score(run_id: str, header: Mapping[str, Any], result: Mapping[str, Any],
                 report: str) -> Path:
    directory = RUNS_DIR / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "header.json").write_text(json.dumps(header, indent=2) + "\n")
    (directory / "metrics.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    doc = ROOT / "docs" / f"e4_scores_{run_id.split('-')[-1]}.md"
    doc.write_text(report, encoding="utf-8")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    from finagent.retrieval.report import DEV

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("cards", "sheet", "judge", "doc", "validate",
                                            "import", "score"))
    parser.add_argument("--sheet", type=Path, default=None)
    parser.add_argument("--partial", action="store_true", help="allow unlabelled rows")
    parser.add_argument("--run-id", default=None, help="score: e4-0001 (v1), e4-0002 (v2)")
    parser.add_argument("--evidence", choices=("v1", "v2"), default="v1",
                        help="v2: the evidence-v2 slots, sheet, judge key and labels (D-EV)")
    args = parser.parse_args(argv)
    spec = load_e4_spec()
    v1_files = _paths(spec)
    v2 = args.evidence == "v2"
    if v2 and not spec.v2_insights_run:
        raise ConfigError("e4: no v2 section in configs/e4.yaml")
    files = {k: ROOT / p for k, p in spec.v2_files.items()} if v2 else v1_files
    run_id = args.run_id or ("e4-0002" if v2 else "e4-0001")
    if args.command == "cards":
        from finagent.config import load_universe

        if v2:
            slots, cards = build_v2(spec, load_cards(v1_files["cards"]), load_universe())
            write_jsonl(files["cards"], cards)
            write_jsonl(files["slots"], slots)
            print(" ".join(f"{o}: {n}" for o, n in Counter(s.outcome for s in slots).items()))
            print(f"Wrote {files['cards']} ({len(cards)} cards, {len(on_sheet(cards))} on the "
                  f"v2 sheet) and {files['slots']}")
            return 0
        cards = build_cards(spec, load_universe(), DEV)
        write_jsonl(files["cards"], cards)
        counts = census(cards)
        print(" ".join(f"{w}: {c}" for w, c in counts.items()))
        print(f"Wrote {files['cards']}: {len(cards)} cards, {len(on_sheet(cards))} on the sheet")
        return 0
    cards = load_cards(files["cards"])
    cards_sha = file_sha256(files["cards"])
    if args.command == "sheet":  # v2: the same worked examples, from the v1 cards
        path = write_sheet(args.sheet or files["sheet"], cards, spec,
                           example_cards(load_cards(v1_files["cards"]), spec, DEV))
        print(f"Wrote {path}")
        return 0
    if args.command == "doc":
        if v2:
            write_generated(files["doc"], render_v2_generated(
                load_cards(v1_files["cards"]), cards, load_slots(files["slots"]), spec),
                GENERATED_V2)
        else:
            write_generated(files["doc"], render_generated(cards, spec))
        print(f"Wrote {files['doc']}")
        return 0
    if args.command == "judge":
        predict, judge = _judge_pin(spec)
        records = judge_records(cards, predict, spec, judge, cards_sha)
        write_jsonl(files["judge_key"], records)
        print(f"Wrote {len(records)} judge records to {files['judge_key']} (hidden; the labels "
              "are not printed)")
        return 0
    if args.command == "score":
        from finagent.dataset.build import git_state

        commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
        header: dict[str, Any] = {
            "run_id": run_id, "git_commit": commit, "git_dirty": dirty,
            "evidence_manifest_sha256": file_sha256(ROOT / "evidence" / "manifest.json"),
            "cards_sha256": cards_sha,
            "labels_sha256": file_sha256(files["labels"]) if files["labels"].exists() else None}
        if not v2:
            judged = read_jsonl(files["judge_key"])
            result = score_e4(read_jsonl(files["labels"]), judged, cards, cards_sha, spec)
            header["judge"] = _judge_text(judged)
            report = render_score(result, run_id, header)
            _write_score(run_id, header, result, report)
            print(report)
            return 0
        sides = {}
        for name, f in (("v1", v1_files), ("v2", files)):
            # With no changed slot the v2 sheet is empty and its files may be absent; a changed
            # slot without a label is refused by combined_labels.
            sides[name] = {"labels": read_jsonl(f["labels"]) if f["labels"].exists() else [],
                           "judged": read_jsonl(f["judge_key"]) if f["judge_key"].exists()
                           else [],
                           "cards_sha256": file_sha256(f["cards"]), "cards": load_cards(f["cards"])}
        result = score_e4_v2(load_slots(files["slots"]), sides["v1"], sides["v2"], spec)
        header.update({"v1_cards_sha256": sides["v1"]["cards_sha256"],
                       "v1_labels_sha256": file_sha256(v1_files["labels"]),
                       "judge": _judge_text(sides["v2"]["judged"] or sides["v1"]["judged"])})
        before = RUNS_DIR / "e4-0001" / "metrics.json"
        previous = json.loads(before.read_text(encoding="utf-8")) if before.exists() else None
        report = render_score_v2(result, run_id, header, previous, "e4-0001")
        _write_score(run_id, header, result, report)
        print(report)
        return 0
    sheet = args.sheet or files["sheet"]
    if args.command == "validate":
        header_row, rows = read_sheet(sheet)
        errors = validate_sheet(header_row, rows, cards, args.partial)
        labelled = sum(1 for r in rows if r.label not in (None, ""))
        print("\n".join(errors) or f"valid ({labelled} of {len(on_sheet(cards))} cards labelled)")
        return 1 if errors else 0
    errors = import_labels(sheet, files["cards"], files["labels"])
    if errors:
        print("\n".join(errors))
        print("Not imported: fix the sheet first.")
        return 1
    print(f"Imported {len(on_sheet(cards))} labels into {files['labels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
