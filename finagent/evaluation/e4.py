"""E4 faithfulness labelling (blueprint 10.3; Phase 7.6; D9): sheet, judge, import and score.

    python -m finagent.evaluation.e4 cards      # gold/e4_cards.jsonl (the selection rule)
    python -m finagent.evaluation.e4 sheet      # eval/e4_labels.xlsx
    python -m finagent.evaluation.e4 judge      # gold/e4_judge_key.jsonl (hidden; never printed)
    python -m finagent.evaluation.e4 doc        # docs/e4_v1.md (text below the marker)
    python -m finagent.evaluation.e4 validate [--sheet PATH] [--partial]
    python -m finagent.evaluation.e4 import   [--sheet PATH]   # -> gold/e4_labels.jsonl
    python -m finagent.evaluation.e4 score  --run-id e4-0001   # after Vineet labels

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
from dataclasses import asdict, dataclass, replace
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
        spec = E4Spec(date.fromisoformat(str(data["as_of"])), str(data["insights_run"]),
                      {k: Path(v) for k, v in data["files"].items()},
                      int(sel["earlier_years"]), bool(sel["earlier_from_dev"]),
                      tuple(str(x) for x in data["labels"]), int(data["seconds_per_card"]),
                      str(judge["model"]), str(judge["entailment"]),
                      {str(k): str(v) for k, v in judge["hypotheses"].items()},
                      int(boot["draws"]), int(boot["seed"]), float(boot["level"]), examples)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"e4: {exc}") from exc
    if spec.labels != LABELS:
        raise ConfigError(f"e4: labels must be exactly {list(LABELS)}")
    if any(e.label not in LABELS for e in spec.examples):
        raise ConfigError("e4: an example label is not one of the labels")
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


def score_e4(labels: Sequence[Mapping[str, Any]], judged: Sequence[Mapping[str, Any]],
             cards: Sequence[E4Card], cards_sha256: str, spec: E4Spec) -> dict[str, Any]:
    """Vineet's labels (the reference) against the judge's, by the rule of configs/e4.yaml."""
    from finagent.evaluation.e3_score import bootstrap_mean

    for name, records in (("labels", labels), ("judge key", judged)):
        if any(r["cards_sha256"] != cards_sha256 for r in records):
            raise ValueError(f"the {name} were made on another cards file")
    ids = [k.card_id for k in on_sheet(cards)]
    human = {r["card_id"]: r["label"] for r in labels}
    judge = {r["card_id"]: r["label"] for r in judged}
    missing = [i for i in ids if human.get(i) not in LABELS or judge.get(i) not in LABELS]
    if missing:
        raise ValueError(f"cards without both labels: {missing[:3]}")
    a, b = [human[i] for i in ids], [judge[i] for i in ids]
    boot = (spec.draws, spec.seed, spec.level)

    def rate(xs: Sequence[str], label: str) -> Any:
        return bootstrap_mean([1.0 if x == label else 0.0 for x in xs], *boot)
    counts = census(cards)
    gap = {}
    for when in ("all_years", "latest_year"):
        c = counts[when]
        denominator = c[QUOTED] + c[EVIDENCE_GAP]
        gap[when] = {"evidence_gap": c[EVIDENCE_GAP], "of": denominator,
                     "share": c[EVIDENCE_GAP] / denominator if denominator else None,
                     "model_derived": c[MODEL]}
    return {"cards": len(ids), "human": dict(Counter(a)), "judge": dict(Counter(b)),
            "unsupported_rate": rate(a, UNSUPPORTED), "partial_rate": rate(a, PARTIAL),
            "judge_unsupported_rate": rate(b, UNSUPPORTED),
            "kappa": bootstrap_kappa(a, b, LABELS, *boot),
            "kappa_binary": bootstrap_kappa(binary(a), binary(b),
                                            (SUPPORTED, "not supported"), *boot),
            "confusion": {h: {j: sum(1 for x, y in zip(a, b) if (x, y) == (h, j))
                              for j in LABELS} for h in LABELS},
            "evidence_gap": gap}


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
             f"| Cohen's kappa, supported vs not | {_kappa(result['kappa_binary'])} |"]
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


def write_generated(path: Path, text: str) -> None:
    """Replace everything after the GENERATED marker (the hand-written part above is kept)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    head = old.split(GENERATED, 1)[0].rstrip("\n")
    path.write_text(head + "\n\n" + GENERATED + "\n\n" + text, encoding="utf-8")


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


def main(argv: Sequence[str] | None = None) -> int:
    from finagent.retrieval.report import DEV

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("cards", "sheet", "judge", "doc", "validate",
                                            "import", "score"))
    parser.add_argument("--sheet", type=Path, default=None)
    parser.add_argument("--partial", action="store_true", help="allow unlabelled rows")
    parser.add_argument("--run-id", default="e4-0001")
    args = parser.parse_args(argv)
    spec = load_e4_spec()
    files = _paths(spec)
    if args.command == "cards":
        from finagent.config import load_universe

        cards = build_cards(spec, load_universe(), DEV)
        write_jsonl(files["cards"], cards)
        counts = census(cards)
        print(" ".join(f"{w}: {c}" for w, c in counts.items()))
        print(f"Wrote {files['cards']}: {len(cards)} cards, {len(on_sheet(cards))} on the sheet")
        return 0
    cards = load_cards(files["cards"])
    cards_sha = file_sha256(files["cards"])
    if args.command == "sheet":
        path = write_sheet(args.sheet or files["sheet"], cards, spec,
                           example_cards(cards, spec, DEV))
        print(f"Wrote {path}")
        return 0
    if args.command == "doc":
        write_generated(files["doc"], render_generated(cards, spec))
        print(f"Wrote {files['doc']}")
        return 0
    if args.command == "judge":
        from finagent.config import load_model_pins
        from finagent.retrieval.models import load_nli_judge

        pin = load_model_pins()[spec.judge_model]
        model, names = load_nli_judge(pin)
        if spec.entailment not in names.values():
            raise ConfigError(f"e4: the judge has no {spec.entailment!r} label: {names}")
        judge = {"model": pin.model_id, "repo": pin.repo, "revision": pin.revision,
                 "fingerprint": pin.fingerprint}
        records = judge_records(cards, nli_predict(model, names), spec, judge, cards_sha)
        write_jsonl(files["judge_key"], records)
        print(f"Wrote {len(records)} judge records to {files['judge_key']} (hidden; the labels "
              "are not printed)")
        return 0
    if args.command == "score":
        from finagent.dataset.build import git_state

        labels = read_jsonl(files["labels"])
        judged = read_jsonl(files["judge_key"])
        result = score_e4(labels, judged, cards, cards_sha, spec)
        commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
        j = judged[0]["judge"] if judged else {}
        header = {"run_id": args.run_id, "git_commit": commit, "git_dirty": dirty,
                  "evidence_manifest_sha256": file_sha256(ROOT / "evidence" / "manifest.json"),
                  "cards_sha256": cards_sha, "labels_sha256": file_sha256(files["labels"]),
                  "judge": f"{j.get('model')} @ {str(j.get('revision'))[:12]}"}
        directory = RUNS_DIR / args.run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "header.json").write_text(json.dumps(header, indent=2) + "\n")
        (directory / "metrics.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        report = render_score(result, args.run_id, header)
        doc = ROOT / "docs" / f"e4_scores_{args.run_id.split('-')[-1]}.md"
        doc.write_text(report, encoding="utf-8")
        print(report)
        return 0
    sheet = args.sheet or files["sheet"]
    if args.command == "validate":
        header, rows = read_sheet(sheet)
        errors = validate_sheet(header, rows, cards, args.partial)
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
