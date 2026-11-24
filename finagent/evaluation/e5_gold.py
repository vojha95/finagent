"""E5 gold set: coverage grid, brief, format check and freeze (blueprint 10.4; Phase 6).

    python -m finagent.evaluation.e5_gold grid       # gold/e5_coverage_grid.csv (120 cells)
    python -m finagent.evaluation.e5_gold brief      # docs/e5_question_brief.md (Vineet's cells)
    python -m finagent.evaluation.e5_gold import     # filled brief -> gold/intents_test.jsonl
    python -m finagent.evaluation.e5_gold validate [--stage labelled]
    python -m finagent.evaluation.e5_gold freeze     # gold/intents_test.sha256
    python -m finagent.evaluation.e5_gold check      # the frozen hashes still match

The grid (configs/e5.yaml) gives every cell a request kind or clarification category, a
validation company, its slots (metric, fiscal year, scenario, projected year) and an author.
Vineet writes the paraphrase, trap and most clarification questions from the brief, which
describes each cell in plain words and never shows training template text; the canonical
questions and every gold-goal label are added afterwards. The set is blind because its author has
not seen the templates, even though it is written after training.

The gold file is JSONL, one question per cell (blueprint 9.6 names a .csv; a question can carry
several goals). Freezing writes the sha256 of the gold file and of the grid; E5 runs refuse a set
that is not frozen or whose hashes changed. Nothing here reads a held-out company: the fiscal
years come from the validation companies' pinned 10-Ks.
"""

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import (CLARIFICATION_CATEGORIES, CONFIG_DIR, GOAL_KINDS, ConfigError,
                             IntentVocabulary, _load_yaml)
from finagent.records import Goal

ROOT = Path(__file__).resolve().parents[2]
E5_PATH = CONFIG_DIR / "e5.yaml"
VALIDATION = "validation"
AUTHORS = ("vineet", "canonical")
STYLES = ("canonical", "paraphrase")
EXPECTED = ("GOALS", "CLARIFICATION")
OPTIONS = ("comparisons", "latest", "selected", "unnamed_metric", "unnamed_target", "conditional")
UNNAMED = "unnamed"
RECORD_KEYS = ("id", "cell", "question", "selected_issuer", "author", "style", "trap_type",
               "expected", "clarification_category", "goals", "end_to_end", "notes")
STAGES = ("questions", "labelled")
TWO_ISSUER_VARIANTS = ("comparison_without_cue", "two_companies_no_comparison",
                       "selected_mismatch")
NO_ISSUER_VARIANTS = ("outside_universe", "no_company")
CELL_ROW = re.compile(r"^\|\s*C\d{3}\s*\|")


@dataclass(frozen=True)
class StyleSpec:
    count: int
    options: dict[str, int]


@dataclass(frozen=True)
class GoalGroup:
    kind: str
    canonical: StyleSpec
    paraphrase: StyleSpec
    traps: tuple[str, ...]


@dataclass(frozen=True)
class ClarificationGroup:
    category: str
    canonical: tuple[str, ...]
    vineet: tuple[str, ...]


@dataclass(frozen=True)
class E5Spec:
    as_of: date
    files: dict[str, Path]
    metrics: dict[str, tuple[str, ...]]
    words: dict[str, str]
    scenarios: tuple[str, ...]
    goal_groups: tuple[GoalGroup, ...]
    clarification_groups: tuple[ClarificationGroup, ...]
    kinds: dict[str, str]
    variants: dict[str, dict[str, str]]

    def size(self) -> int:
        return (sum(g.canonical.count + g.paraphrase.count + len(g.traps)
                    for g in self.goal_groups)
                + sum(len(g.canonical) + len(g.vineet) for g in self.clarification_groups))


def _style(raw: Any, where: str) -> StyleSpec:
    if not isinstance(raw, dict) or not isinstance(raw.get("count"), int) or raw["count"] < 0:
        raise ConfigError(f"e5: {where} needs a count")
    options = {k: v for k, v in raw.items() if k != "count"}
    for name, n in options.items():
        if name not in OPTIONS:
            raise ConfigError(f"e5: {where}: unknown option {name}")
        if not isinstance(n, int) or not 0 <= n <= raw["count"]:
            raise ConfigError(f"e5: {where}: {name} must be between 0 and count")
    return StyleSpec(raw["count"], options)


def load_e5_spec(path: Path | str = E5_PATH, vocabulary: IntentVocabulary | None = None,
                 scenario_names: Sequence[str] | None = None) -> E5Spec:
    """configs/e5.yaml, checked against configs/intents.yaml and configs/scenarios.yaml."""
    from finagent.config import load_intent_vocabulary, load_scenarios

    data = _load_yaml(path)
    vocabulary = vocabulary or load_intent_vocabulary()
    names = tuple(scenario_names or (s.name for s in load_scenarios()))
    metrics = {k: tuple(v) for k, v in (data.get("metrics") or {}).items()}
    words = {str(k): str(v) for k, v in (data.get("words") or {}).items()}
    for kind, ids in metrics.items():
        table = vocabulary.metrics.get(kind)
        if table is None:
            raise ConfigError(f"e5: metrics.{kind} is not a metric table of intents.yaml")
        for metric in ids:
            if metric not in table:
                raise ConfigError(f"e5: metrics.{kind}: {metric} is not in intents.yaml")
            if metric not in words:
                raise ConfigError(f"e5: words has no entry for {metric}")
    scenarios = tuple(str(s) for s in data.get("scenarios") or ())
    if not scenarios or set(scenarios) - set(names) - {UNNAMED}:
        raise ConfigError(f"e5: scenarios must be among {list(names)} and {UNNAMED}")
    variants = {str(k): dict(v) for k, v in (data.get("variants") or {}).items()}
    for name, spec in variants.items():
        if not spec.get("ask") or not spec.get("expected"):
            raise ConfigError(f"e5: variant {name} needs ask and expected")
        if spec.get("category", "") not in ("",) + CLARIFICATION_CATEGORIES:
            raise ConfigError(f"e5: variant {name}: unknown category {spec['category']}")
    goal_groups = []
    for raw in data.get("goal_groups") or ():
        kind = raw.get("kind")
        if kind not in GOAL_KINDS:
            raise ConfigError(f"e5: unknown goal kind {kind}")
        traps = tuple(raw.get("traps") or ())
        goal_groups.append(GoalGroup(kind, _style(raw.get("canonical"), f"{kind}.canonical"),
                                     _style(raw.get("paraphrase"), f"{kind}.paraphrase"), traps))
    clar_groups = []
    for raw in data.get("clarification_groups") or ():
        category = raw.get("category")
        if category not in CLARIFICATION_CATEGORIES:
            raise ConfigError(f"e5: unknown clarification category {category}")
        clar_groups.append(ClarificationGroup(category, tuple(raw.get("canonical") or ()),
                                              tuple(raw.get("vineet") or ())))
    used = {t for g in goal_groups for t in g.traps} | {
        v for g in clar_groups for v in g.canonical + g.vineet}
    missing = sorted(used - set(variants))
    if missing:
        raise ConfigError(f"e5: variants without a description: {missing}")
    kinds = {str(k): str(v) for k, v in (data.get("kinds") or {}).items()}
    if set(kinds) != set(GOAL_KINDS):
        raise ConfigError("e5: kinds must describe every goal kind")
    files = {str(k): ROOT / str(v) for k, v in (data.get("files") or {}).items()}
    if set(files) != {"grid", "gold", "hash", "brief"}:
        raise ConfigError("e5: files needs grid, gold, hash and brief")
    as_of = data.get("as_of")
    as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    return E5Spec(as_of, files, metrics, words, scenarios, tuple(goal_groups),
                  tuple(clar_groups), kinds, variants)


@dataclass(frozen=True)
class GridCell:
    """One E5 question to write: its kind (goal groups) or category (clarifications and
    clarifying traps), author, slots and variant. period: a fiscal year, "latest", or for a
    two-period variant the years it spans ("2023;2025"); target: a projected fiscal year or ""
    (none named: the first projected year); scenario: a scenario name, "unnamed" or ""."""

    cell: str
    group: str
    kind: str
    category: str
    author: str
    style: str
    variant: str
    issuer: str
    issuer2: str
    selected_issuer: str
    metric: str
    period: str
    scenario: str
    target: str
    conditional: bool

    @property
    def expected(self) -> str:
        return "CLARIFICATION" if self.category else "GOALS"


GRID_COLUMNS = tuple(f.name for f in fields(GridCell))


def fiscal_years(tickers: Sequence[str], as_of: date, evidence_dir: Path | str | None = None
                 ) -> dict[str, tuple[int, ...]]:
    """Fiscal years of each company's pinned 10-Ks visible at as_of (validation only)."""
    from finagent.config import check_not_held_out, load_universe
    from finagent.evidence.store import EvidenceStore

    universe = load_universe()
    check_not_held_out(tickers, universe, include_held_out=False)
    store = EvidenceStore(evidence_dir or ROOT / "evidence")
    return {t: tuple(r.fiscal_year for r in store.load_filings(t, as_of)) for t in tickers}


class _Rotation:
    """Deterministic round-robin draws, one counter per key."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def take(self, key: str, items: Sequence) -> Any:
        if not items:
            return ""
        i = self.counts.get(key, 0)
        self.counts[key] = i + 1
        return items[i % len(items)]


def _flags(spec: StyleSpec) -> list[set[str]]:
    """Option flags per cell: each option takes the next n cells, wrapping round."""
    cells: list[set[str]] = [set() for _ in range(spec.count)]
    start = 0
    for name in OPTIONS:
        for k in range(spec.options.get(name, 0)):
            cells[(start + k) % spec.count].add(name)
        start += spec.options.get(name, 0)
    return cells


def build_grid(spec: E5Spec, years: Mapping[str, Sequence[int]], horizon: int) -> list[GridCell]:
    """Every cell of the coverage grid, in group order; companies rotate over all cells."""
    companies = sorted(years)
    if not companies or any(len(years[c]) < 2 for c in companies):
        raise ValueError("every company needs at least two fiscal years")
    rot = _Rotation()
    cells: list[GridCell] = []

    def add(group: str, kind: str, category: str, author: str, style: str, variant: str,
            flags: set[str]) -> None:
        issuer = rot.take("company", companies)
        other = companies[(companies.index(issuer) + 1) % len(companies)]
        fys = list(years[issuer])
        latest = fys[-1]
        issuer2 = other if "comparisons" in flags or variant in TWO_ISSUER_VARIANTS else ""
        selected = issuer if "selected" in flags else ""
        if variant == "selected_mismatch":
            selected, issuer, issuer2 = issuer, other, ""
        if variant in NO_ISSUER_VARIANTS:
            issuer = ""
        metric_kind = kind or "fact"
        metric = "" if "unnamed_metric" in flags or variant in ("advice", "outside_scope") \
            else rot.take(f"metric:{metric_kind}", spec.metrics.get(metric_kind, ()))
        period = target = scenario = ""
        if kind in ("forecast", "liquidity", "debt"):
            scenario = rot.take(f"scenario:{kind}", spec.scenarios)
            if "unnamed_target" not in flags:
                target = str(latest + 1 + rot.take(f"target:{kind}", range(horizon)))
            if variant == "past_target":
                target = str(latest)
            elif variant == "beyond_horizon":
                target = str(latest + horizon + 1)
            if variant in ("ambiguous_scenario", "no_scenario", "condition_without_liquidity"):
                scenario = UNNAMED
        elif variant in ("two_years", "year_span"):
            first = rot.take("first_year", fys[:-1])
            last = fys[-1] if variant == "year_span" else fys[fys.index(first) + 1]
            period = f"{first};{last}"
        elif variant not in ("advice", "outside_scope", "several_years", "part_year"):
            pool = fys[1:] if kind == "explain" else fys
            period = "latest" if "latest" in flags or variant == "relative_latest" \
                else str(rot.take(f"year:{kind or 'other'}", pool))
        if variant == "scenario_on_history":
            scenario = "Severe"
        conditional = "conditional" in flags or variant == "condition_without_liquidity"
        cells.append(GridCell(f"C{len(cells) + 1:03d}", group, kind, category, author, style,
                              variant, issuer, issuer2, selected, metric, period, scenario,
                              target, conditional))

    for g in spec.goal_groups:
        for style, author, style_spec in (("canonical", "canonical", g.canonical),
                                          ("paraphrase", "vineet", g.paraphrase)):
            for flags in _flags(style_spec):
                add(g.kind, g.kind, "", author, style, "", flags)
        for trap in g.traps:
            add(g.kind, g.kind, spec.variants[trap].get("category", ""), "vineet", "paraphrase",
                trap, set())
    for c in spec.clarification_groups:
        for author, variants in (("canonical", c.canonical), ("vineet", c.vineet)):
            for variant in variants:
                add(c.category, "", c.category, author,
                    "canonical" if author == "canonical" else "paraphrase", variant, set())
    return cells


def write_grid(cells: Sequence[GridCell], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GRID_COLUMNS)
        writer.writeheader()
        for cell in cells:
            writer.writerow(asdict(cell))
    return path


def load_grid(path: Path) -> list[GridCell]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [GridCell(**{**row, "conditional": row["conditional"] == "True"}) for row in rows]


# --- the brief -------------------------------------------------------------------------------

BRIEF_HEADER = """# E5 question brief

Vineet: this file is your template. Write one question per row in the last column, in your own
words, as you would type it to FinAgent. Then run
`python -m finagent.evaluation.e5_gold import` to copy your questions into
`gold/intents_test.jsonl`. Labels (the expected goals) are added afterwards, not by you.

## Why this set is blind

The E5 set is written after the intent classifier was trained, but it stays blind because you
write it without seeing the training templates. **Before and while writing, do not open**:
`configs/intents.yaml`, `configs/intent_templates.yaml`, `datasets/intents_*`,
`finagent/planner/`, `tests/test_planner_*`, the planner sections of `docs/issues_log.md`, and
the Phase 6 reports and compiled examples. `docs/model_cards/intent_v*.md` show metrics only and
are safe. This brief describes each cell by what it asks for, never by training wording.

## Rules

- One question per cell. Do not use the `|` character in a question.
- Use only the company given in the row (its name or its ticker, as you prefer). Every company
  here is a validation company. Never use any other company in the universe
  (`configs/universe.yaml`) except where a row asks for a company outside it.
- Fiscal years are the company's own (e.g. a June year-end company's fiscal 2025 ends in June
  2025). Name the fiscal year the row gives, unless the row says not to.
- "Selected in the app" means the user has already picked that company on screen, so the
  question does not need to name it (or, for a mismatch row, names a different one).
- Projected years: name the fiscal year, or refer to it relative to now in your own words.
- Write naturally: vary the wording, length and tone across rows. Typos you would really make
  are fine.
- Traps and clarification rows say what the question should do and what FinAgent is expected to
  do with it. Write a question a real user might ask that fits the description.
"""


def _company(ticker: str, display: Mapping[str, str]) -> str:
    return f"{display.get(ticker, ticker)} ({ticker})" if ticker else ""


def describe(cell: GridCell, spec: E5Spec, display: Mapping[str, str]) -> tuple[str, str]:
    """(what to ask, the details) for one cell, in plain words."""
    if cell.variant:
        ask = f"{spec.variants[cell.variant]['ask']}. Expected: " \
              f"{spec.variants[cell.variant]['expected']}"
    else:
        ask = spec.kinds[cell.kind]
    details = []
    if cell.metric:
        details.append(f"figure: {spec.words[cell.metric]}")
    elif cell.kind in ("forecast", "credit", "liquidity") and not cell.variant:
        details.append("figure: none named (let FinAgent choose)")
    if cell.issuer2 and cell.variant not in TWO_ISSUER_VARIANTS:
        details.append(f"compare with: {_company(cell.issuer2, display)}")
    elif cell.issuer2:
        details.append(f"second company: {_company(cell.issuer2, display)}")
    if cell.period == "latest":
        details.append("year: the latest reported year (do not name the year)")
    elif ";" in cell.period:
        first, last = cell.period.split(";")
        joint = "to" if cell.variant == "year_span" else "and"
        details.append(f"years: FY{first} {joint} FY{last}")
    elif cell.period:
        details.append(f"fiscal year: FY{cell.period}")
    if cell.variant in ("past_target", "beyond_horizon"):
        details.append(f"year to forecast: FY{cell.target}")
    elif cell.kind in ("forecast", "liquidity", "debt"):
        details.append(f"projected year: FY{cell.target}" if cell.target
                       else "projected year: none named (the first projected year)")
    if cell.scenario == UNNAMED:
        details.append("scenario: do not name one")
    elif cell.scenario:
        details.append(f"scenario: {cell.scenario}")
    if cell.conditional and cell.variant != "condition_without_liquidity":
        details.append("ask for liquidity, and for debt service only if there is a shortfall")
    if cell.selected_issuer:
        details.append(f"selected in the app: {_company(cell.selected_issuer, display)}"
                       + ("" if cell.variant else " (the question need not name it)"))
    return ask, "; ".join(details)


def render_brief(cells: Sequence[GridCell], spec: E5Spec, display: Mapping[str, str]) -> str:
    """The brief: Vineet's cells only, by group, each with an empty question column."""
    mine = [c for c in cells if c.author == "vineet"]
    lines = [BRIEF_HEADER, f"## Your {len(mine)} questions", ""]
    for group in dict.fromkeys(c.group for c in mine):
        rows = [c for c in mine if c.group == group]
        lines += [f"### {group}", "", "| Cell | What to ask | Company | Details | Your question |",
                  "|---|---|---|---|---|"]
        for c in rows:
            ask, details = describe(c, spec, display)
            company = _company(c.issuer, display) or (
                "one outside the universe (your choice)" if c.variant == "outside_universe"
                else "none")
            lines.append(f"| {c.cell} | {ask} | {company} | {details} |  |")
        lines.append("")
    return "\n".join(lines)


def parse_brief(text: str) -> dict[str, str]:
    """cell -> question for every filled row of the brief."""
    found: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not CELL_ROW.match(stripped):
            continue
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) != 5:
            raise ValueError(f"brief line {number}: expected 5 columns (is there a '|' in the "
                             "question?)")
        if parts[4]:
            found[parts[0]] = parts[4]
    return found


# --- the gold file ---------------------------------------------------------------------------

def blank_record(cell: GridCell, question: str) -> dict[str, Any]:
    """A gold record for a cell, question written, labels empty."""
    return {"id": cell.cell.replace("C", "Q", 1), "cell": cell.cell, "question": question,
            "selected_issuer": cell.selected_issuer, "author": cell.author,
            "style": cell.style, "trap_type": cell.variant, "expected": "",
            "clarification_category": "", "goals": [], "end_to_end": None, "notes": ""}


def load_gold(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def write_gold(records: Sequence[Mapping[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: r["cell"])
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in ordered),
                    encoding="utf-8")
    return path


def import_brief(brief: Path, cells: Sequence[GridCell], gold: Path, hash_file: Path) -> int:
    """Copy the brief's filled questions into the gold file (Vineet's cells only); existing
    labels of a record whose question is unchanged are kept. Refused once frozen."""
    if hash_file.exists():
        raise ValueError(f"{gold} is frozen ({hash_file}); it cannot change")
    by_cell = {c.cell: c for c in cells}
    questions = parse_brief(brief.read_text(encoding="utf-8"))
    records = {r["cell"]: r for r in load_gold(gold)}
    for cell_id, question in questions.items():
        cell = by_cell.get(cell_id)
        if cell is None or cell.author != "vineet":
            raise ValueError(f"brief: {cell_id} is not one of Vineet's grid cells")
        old = records.get(cell_id)
        if old is None or old["question"] != question:
            records[cell_id] = blank_record(cell, question)
    write_gold(list(records.values()), gold)
    return len(questions)


def _goal_errors(raw: Any, where: str, validation: set[str], scenario_names: set[str],
                 vocabulary: IntentVocabulary) -> list[str]:
    names = {f.name for f in fields(Goal)}
    if not isinstance(raw, dict) or set(raw) != names:
        return [f"{where}: a goal needs exactly the fields {sorted(names)}"]
    errors = []
    if raw["kind"] not in GOAL_KINDS:
        errors.append(f"{where}: unknown kind {raw['kind']}")
    if raw["issuer"] not in validation:
        errors.append(f"{where}: issuer {raw['issuer']} is not a validation company")
    period, target = raw["period"], raw["target"]
    if not (period == "latest" or (isinstance(period, int) and not isinstance(period, bool))):
        errors.append(f"{where}: period must be a fiscal year or 'latest'")
    if not (target is None or target in ("+2", "+3")
            or (isinstance(target, int) and not isinstance(target, bool))):
        errors.append(f"{where}: target must be null, a fiscal year, '+2' or '+3'")
    if raw["scenario"] not in scenario_names | {""}:
        errors.append(f"{where}: unknown scenario {raw['scenario']}")
    if not isinstance(raw["conditional"], bool):
        errors.append(f"{where}: conditional must be true or false")
    table = vocabulary.metrics.get(raw["kind"], {})
    if raw["metric"] and raw["metric"] not in table:
        errors.append(f"{where}: {raw['metric']} is not a {raw['kind']} metric")
    return errors


def validate_gold(records: Sequence[Mapping[str, Any]], cells: Sequence[GridCell],
                  stage: str = "questions", index=None, vocabulary: IntentVocabulary | None = None,
                  scenario_names: Sequence[str] | None = None,
                  require_complete: bool = False) -> list[str]:
    """Format errors of the gold file (empty: valid). "questions": every record matches its
    grid cell and names only the companies it should; "labelled": labels are complete and
    consistent too. require_complete: every grid cell has a record."""
    from finagent.config import load_intent_vocabulary, load_scenarios
    from finagent.planner.issuers import IssuerIndex, find_mentions

    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    vocabulary = vocabulary or load_intent_vocabulary()
    index = index or IssuerIndex.load(vocabulary=vocabulary)
    names = set(scenario_names or (s.name for s in load_scenarios()))
    validation = {t for t, s in index.universe.items() if s == VALIDATION}
    by_cell = {c.cell: c for c in cells}
    errors: list[str] = []
    seen_ids, seen_cells, seen_questions = set(), set(), set()
    for n, r in enumerate(records, 1):
        where = f"record {n} ({r.get('id', '?')})"
        if set(r) != set(RECORD_KEYS):
            errors.append(f"{where}: keys must be exactly {list(RECORD_KEYS)}")
            continue
        cell = by_cell.get(r["cell"])
        if cell is None:
            errors.append(f"{where}: cell {r['cell']} is not in the grid")
            continue
        for key, seen in (("id", seen_ids), ("cell", seen_cells)):
            if r[key] in seen:
                errors.append(f"{where}: duplicate {key} {r[key]}")
            seen.add(r[key])
        question = r["question"].strip() if isinstance(r["question"], str) else ""
        if not question:
            errors.append(f"{where}: empty question")
        elif question.lower() in seen_questions:
            errors.append(f"{where}: duplicate question")
        seen_questions.add(question.lower())
        for key, want in (("author", cell.author), ("style", cell.style),
                          ("trap_type", cell.variant), ("selected_issuer", cell.selected_issuer)):
            if r[key] != want:
                errors.append(f"{where}: {key} {r[key]!r} differs from the grid ({want!r})")
        mentions = find_mentions(question, index) if question else []
        named = {m.ticker for m in mentions if m.ticker in index.universe}
        for ticker in sorted(named):
            if index.universe[ticker] != VALIDATION:
                errors.append(f"{where}: names {ticker}, which is not a validation company")
        if cell.variant == "outside_universe":
            if named or not mentions:
                errors.append(f"{where}: must name one company outside the universe")
        elif cell.issuer and cell.issuer not in named and not cell.selected_issuer:
            errors.append(f"{where}: does not name {cell.issuer}")
        if stage == "labelled":
            errors += _label_errors(r, cell, where, validation, names, vocabulary)
    if require_complete:
        missing = sorted(set(by_cell) - seen_cells)
        if missing:
            errors.append(f"{len(missing)} grid cells have no record: {', '.join(missing)}")
    return errors


def _label_errors(r: Mapping[str, Any], cell: GridCell, where: str, validation: set[str],
                  scenario_names: set[str], vocabulary: IntentVocabulary) -> list[str]:
    errors = []
    if r["expected"] not in EXPECTED:
        return [f"{where}: expected must be one of {EXPECTED}"]
    if r["expected"] != cell.expected:
        errors.append(f"{where}: expected {r['expected']} but the grid cell is {cell.expected}")
    if not isinstance(r["end_to_end"], bool):
        errors.append(f"{where}: end_to_end must be true or false")
    if r["expected"] == "GOALS":
        if r["clarification_category"] or not r["goals"]:
            errors.append(f"{where}: GOALS needs goals and no clarification category")
        for k, g in enumerate(r["goals"] or [], 1):
            errors += _goal_errors(g, f"{where} goal {k}", validation, scenario_names, vocabulary)
    else:
        if r["goals"]:
            errors.append(f"{where}: CLARIFICATION takes no goals")
        if r["clarification_category"] != cell.category:
            errors.append(f"{where}: clarification category {r['clarification_category']!r} "
                          f"differs from the grid ({cell.category!r})")
    return errors


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(gold: Path, grid: Path, hash_file: Path, errors: Sequence[str]) -> Path:
    """Write the sha256 of the gold file and the grid. Refused when already frozen or when the
    labelled, complete validation found errors."""
    if hash_file.exists():
        raise ValueError(f"already frozen: {hash_file}")
    if errors:
        raise ValueError(f"not frozen: {len(errors)} validation errors, first: {errors[0]}")
    lines = [f"{file_sha256(p)}  {p.name}\n" for p in (gold, grid)]
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text("".join(lines), encoding="utf-8")
    return hash_file


def check_frozen(hash_file: Path) -> bool:
    """True when every file listed in the hash file (beside it) still has its hash."""
    if not hash_file.exists():
        return False
    for line in hash_file.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        path = hash_file.parent / name
        if not path.exists() or file_sha256(path) != digest:
            return False
    return True


def require_frozen(hash_file: Path) -> None:
    """For the E5 runs: raise unless the gold set is frozen and unchanged."""
    if not check_frozen(hash_file):
        raise ValueError(f"E5 gold set is not frozen or has changed ({hash_file})")


def display_names() -> dict[str, str]:
    """Ticker -> the first name configs/universe.yaml lists for it."""
    from finagent.config import load_issuer_names

    return {t: names[0] for t, names in load_issuer_names().names.items()}


def main(argv: Sequence[str] | None = None) -> int:
    from finagent.config import load_forecast_config, load_universe

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("grid", "brief", "import", "validate", "freeze",
                                            "check"))
    parser.add_argument("--stage", choices=STAGES, default="questions")
    parser.add_argument("--force", action="store_true", help="overwrite a filled brief")
    args = parser.parse_args(argv)
    spec = load_e5_spec()
    files = spec.files
    if args.command == "grid":
        tickers = sorted(t for t, s in load_universe().items() if s == VALIDATION)
        cells = build_grid(spec, fiscal_years(tickers, spec.as_of),
                           load_forecast_config().horizon_years)
        write_grid(cells, files["grid"])
        print(f"Wrote {files['grid']}: {len(cells)} cells")
        return 0
    if args.command == "check":
        ok = check_frozen(files["hash"])
        print("frozen and unchanged" if ok else "NOT frozen or changed")
        return 0 if ok else 1
    cells = load_grid(files["grid"])
    if args.command == "brief":
        if files["brief"].exists() and parse_brief(files["brief"].read_text("utf-8")) \
                and not args.force:
            print(f"{files['brief']} already has questions; use --force to overwrite")
            return 1
        files["brief"].write_text(render_brief(cells, spec, display_names()), encoding="utf-8")
        print(f"Wrote {files['brief']}")
        return 0
    if args.command == "import":
        n = import_brief(files["brief"], cells, files["gold"], files["hash"])
        print(f"Imported {n} questions into {files['gold']}")
        return 0
    complete = args.command == "freeze"
    stage = "labelled" if complete else args.stage
    errors = validate_gold(load_gold(files["gold"]), cells, stage, require_complete=complete)
    if args.command == "validate":
        print("\n".join(errors) or "valid")
        return 1 if errors else 0
    freeze(files["gold"], files["grid"], files["hash"], errors)
    print(f"Frozen: {files['hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
