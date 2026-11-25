"""Training data for the intent classifier (blueprint 8.15 step 3; Phase 6).

    python -m finagent.planner.intent_data --version v1

Fills the families of configs/intent_templates.yaml with slot values drawn, with a fixed seed,
from configs/intents.yaml (metric, scenario, period phrases) and configs/universe.yaml (names of
train-split issuers only, and their tickers). Every sample carries its expected goals (or its
clarification category), so the compiler can be scored on it. The split is by family: a share of
each intent's families goes to validation, so no validation phrasing is seen in training. Two-
clause composites (configs/intent_templates.yaml composites) are built from validation families
only and used to score the compiler, never to train the classifier.

Code only: no LLM, no external service. The E5 gold set (gold/) is never read here.

Writes datasets/intents_{version}.parquet (gitignored) and datasets/intents_{version}.meta.json
(tracked).
"""

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from finagent.config import (CONFIG_DIR, UNIVERSE_PATH, INTENTS_PATH, IntentVocabulary,
                             IssuerNames, PlannerConfig, load_intent_vocabulary,
                             load_issuer_names, load_planner_config, load_universe)
from finagent.planner.intents import INTENT_LABELS, OUT_OF_SCOPE
from finagent.records import Goal, Target

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PATH = CONFIG_DIR / "intent_templates.yaml"
DATASETS_DIR = ROOT / "datasets"
SLOTS = ("issuer", "issuer2", "metric", "metric2", "year", "target", "scenario", "latest",
         "condition")
_SLOT = re.compile(r"\{(\w+)\}")
YEARS = range(2019, 2027)  # reported fiscal years named in questions
TARGET_YEARS = range(2026, 2030)  # projected fiscal years
YEAR_FORMATS = ("FY{y}", "fiscal {y}", "fiscal year {y}", "{y}", "FY{yy}", "FY {y}")
METRIC_TABLE = {"fact": "fact", "explain": "fact", "forecast": "forecast", "credit": "credit",
                "liquidity": "liquidity"}
TRAIN, VALIDATION = "train", "validation"


@dataclass(frozen=True)
class Family:
    family_id: str
    intent: str
    style: str
    text: str
    metrics: tuple[str, ...] = ()
    split: str | None = None  # pinned split of a family added later (#245); None: seeded

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(_SLOT.findall(self.text))


@dataclass(frozen=True)
class Composite:
    composite_id: str
    first: str
    second: str
    join: str
    conditional: bool = False


@dataclass(frozen=True)
class TemplateSet:
    seed: int
    samples_per_family: int
    validation_share: float
    verb_phrases: frozenset[str]
    ticker_share: float
    families: tuple[Family, ...]
    composites: tuple[Composite, ...]


@dataclass(frozen=True)
class Sample:
    """One generated question with what the compiler should return for it."""

    question: str
    intent: str | None  # None for a composite
    family: str
    style: str
    split: str
    goals: tuple[Goal, ...] = ()
    clarification: str | None = None  # expected clarification category
    composite: bool = False


def load_templates(path: Path | str = TEMPLATES_PATH) -> TemplateSet:
    """The template set; slots, intents and family ids are checked."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    families = []
    for intent, listed in data["families"].items():
        if intent not in INTENT_LABELS:
            raise ValueError(f"templates: unknown intent {intent}")
        for raw in listed:
            family = Family(str(raw["id"]), str(intent), str(raw["style"]), str(raw["text"]),
                            tuple(raw.get("metrics") or ()),
                            str(raw["split"]) if raw.get("split") else None)
            unknown = set(family.slots) - set(SLOTS)
            if unknown or family.style not in ("template", "paraphrase"):
                raise ValueError(f"templates {family.family_id}: slots {sorted(unknown)} or "
                                 f"style {family.style}")
            if family.split not in (None, TRAIN, VALIDATION):
                raise ValueError(f"templates {family.family_id}: split {family.split}")
            families.append(family)
    ids = [f.family_id for f in families]
    if len(set(ids)) != len(ids):
        raise ValueError("templates: family ids must be unique")
    composites = tuple(Composite(str(c["id"]), str(c["first"]), str(c["second"]), str(c["join"]),
                                 bool(c.get("conditional", False)))
                       for c in data.get("composites") or ())
    return TemplateSet(int(data["seed"]), int(data["samples_per_family"]),
                       float(data["validation_share"]),
                       frozenset(str(v) for v in data.get("verb_phrases") or ()),
                       float(data.get("ticker_share", 0.0)), tuple(families), composites)


def family_splits(families: Sequence[Family], share: float, seed: int) -> dict[str, str]:
    """family id -> train / validation: round(share x n) of each intent's families (at least
    one) go to validation, chosen with the seed. A family with a pinned split keeps it and is
    left out of the draw, so adding one does not move any other family (#245)."""
    rng = random.Random(seed)
    split: dict[str, str] = {f.family_id: f.split for f in families if f.split}
    for intent in INTENT_LABELS:
        ids = sorted(f.family_id for f in families if f.intent == intent and not f.split)
        rng.shuffle(ids)
        n = max(1, round(share * len(ids))) if ids else 0
        split.update({i: VALIDATION if k < n else TRAIN for k, i in enumerate(ids)})
    return split


class SlotSampler:
    """Draws slot surfaces and their meaning with one random generator."""

    def __init__(self, rng: random.Random, vocabulary: IntentVocabulary, names: IssuerNames,
                 universe: dict[str, str], templates: TemplateSet) -> None:
        self.rng, self.v, self.t = rng, vocabulary, templates
        self.issuers = sorted(t for t, s in universe.items() if s == TRAIN)
        self.names = names

    def issuer(self, exclude: Sequence[str] = ()) -> tuple[str, str]:
        ticker = self.rng.choice([t for t in self.issuers if t not in exclude])
        if self.rng.random() < self.t.ticker_share:
            return ticker, ticker
        return self.rng.choice(self.names.names[ticker]), ticker

    def year(self, years: range = YEARS) -> tuple[str, int]:
        y = self.rng.choice(list(years))
        return self.rng.choice(YEAR_FORMATS).format(y=y, yy=f"{y % 100:02d}"), y

    def target(self) -> tuple[str, Target]:
        if self.rng.random() < 0.5:
            surface, y = self.year(TARGET_YEARS)
            return surface, y
        offset = self.rng.choice(sorted(self.v.targets))
        return self.rng.choice(self.v.targets[offset]), (None if offset == "+1" else offset)

    def scenario(self) -> tuple[str, str]:
        name = self.rng.choice(sorted(self.v.scenarios))
        return self.rng.choice(self.v.scenarios[name]), name

    def metric(self, kind: str, exclude: Sequence[str] = ()) -> tuple[str, str]:
        table = self.v.metrics[METRIC_TABLE[kind]]
        metric = self.rng.choice([m for m in sorted(table) if m not in exclude])
        phrases = [p for p in table[metric] if p not in self.t.verb_phrases]
        return self.rng.choice(phrases), metric

    def pick(self, phrases: Sequence[str]) -> str:
        return self.rng.choice(list(phrases))


@dataclass(frozen=True)
class Rendered:
    question: str
    goals: tuple[Goal, ...]
    scenario_named: bool
    target_named: bool


DOUBLE_POSSESSIVE = re.compile(r"(['’]s)['’]s\b")
DOUBLED_WORD = re.compile(r"\b(in|of|for|the|case)\s+\1\b", re.IGNORECASE)


def tidy(question: str) -> str:
    """Remove slot-filling artefacts (#132): a possessive added to a name that already ends in 's
    ("Lowe's's" → "Lowe's") and a word doubled where a slot starts or ends with the template's
    next or previous word ("in in", "base case case")."""
    return DOUBLED_WORD.sub(r"\1", DOUBLE_POSSESSIVE.sub(r"\1", question))


def render(family: Family, sampler: SlotSampler, planner: PlannerConfig,
           issuer: tuple[str, str] | None = None) -> Rendered:
    """One question from a family, with its expected goals (none for advice / unsupported)."""
    surfaces: dict[str, str] = {}
    first = issuer or sampler.issuer()
    surfaces["issuer"], tickers = first[0], [first[1]]
    metrics: list[str] = list(family.metrics)
    year: int | None = None
    target: Target = None
    scenario = ""
    for slot in family.slots:
        if slot in surfaces:
            continue
        if slot == "issuer2":
            surfaces[slot], ticker = sampler.issuer(exclude=tickers)
            tickers.append(ticker)
        elif slot in ("metric", "metric2"):
            surfaces[slot], metric = sampler.metric(family.intent, exclude=metrics)
            metrics.append(metric)
        elif slot == "year":
            surfaces[slot], year = sampler.year()
        elif slot == "target":
            surfaces[slot], target = sampler.target()
        elif slot == "scenario":
            surfaces[slot], scenario = sampler.scenario()
        elif slot == "latest":
            surfaces[slot] = sampler.pick(sampler.v.latest_phrases)
    question = tidy(family.text.format(**surfaces))
    if family.intent in OUT_OF_SCOPE:
        return Rendered(question, (), False, False)
    spec = planner.kinds[family.intent]
    if spec.target:
        period: Any = "latest"
        target = target if "target" in family.slots else year
        scenario_value = scenario or planner.default_scenario
    else:
        period, target, scenario_value = (year if year is not None else "latest"), None, ""
    goals = tuple(Goal(t, period, family.intent, m, scenario_value, target)  # type: ignore[arg-type]
                  for t in tickers for m in (metrics or [""]))
    return Rendered(question, goals, bool(scenario), "target" in family.slots or (
        spec.target and year is not None))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:1].isupper() and text[1:2].islower() else text


def render_composite(spec: Composite, first: Family, second: Family, sampler: SlotSampler,
                     planner: PlannerConfig) -> Rendered:
    """Two clauses for one issuer; a conditional second goal takes the first goal's scenario and
    target unless it names its own."""
    issuer = sampler.issuer()
    a = render(first, sampler, planner, issuer)
    b = render(second, sampler, planner, issuer)
    join = spec.join.replace("{condition}", sampler.pick(sampler.v.condition_subjects))
    question = a.question.rstrip("?.!") + join + _lower_first(b.question)
    goals = list(b.goals)
    if spec.conditional and a.goals:
        lead = a.goals[0]
        goals = [Goal(g.issuer, g.period, g.kind, g.metric,
                      g.scenario if b.scenario_named else lead.scenario,
                      g.target if b.target_named else lead.target, True) for g in b.goals]
    return Rendered(question, tuple(dict.fromkeys(a.goals + tuple(goals))), False, False)


def generate(templates: TemplateSet, vocabulary: IntentVocabulary, names: IssuerNames,
             universe: dict[str, str], planner: PlannerConfig) -> list[Sample]:
    """Every sample: samples_per_family distinct questions per family, then the composites, then
    the families with a pinned split (added later; drawn last and kept out of the composites, so
    the earlier samples are generated unchanged, #245)."""
    rng = random.Random(templates.seed)
    sampler = SlotSampler(rng, vocabulary, names, universe, templates)
    splits = family_splits(templates.families, templates.validation_share, templates.seed)
    seeded = [f for f in templates.families if not f.split]
    pinned = [f for f in templates.families if f.split]

    def family_samples(family: Family) -> list[Sample]:
        found: list[Sample] = []
        seen: set[str] = set()
        for _ in range(templates.samples_per_family * 20):
            if len(seen) >= templates.samples_per_family:
                break
            r = render(family, sampler, planner)
            if r.question in seen:
                continue
            seen.add(r.question)
            clarification = family.intent if family.intent in OUT_OF_SCOPE else None
            found.append(Sample(r.question, family.intent, family.family_id, family.style,
                                splits[family.family_id], r.goals, clarification))
        return found

    samples = [s for family in seeded for s in family_samples(family)]
    validation = [f for f in seeded if splits[f.family_id] == VALIDATION
                  and "issuer2" not in f.slots]
    for spec in templates.composites:
        firsts = [f for f in validation if f.intent == spec.first]
        seconds = [f for f in validation if f.intent == spec.second]
        for _ in range(templates.samples_per_family // 2 if firsts and seconds else 0):
            r = render_composite(spec, rng.choice(firsts), rng.choice(seconds), sampler, planner)
            samples.append(Sample(r.question, None, spec.composite_id, "composite", VALIDATION,
                                  r.goals, None, True))
    return samples + [s for family in pinned for s in family_samples(family)]


def goals_json(goals: Sequence[Goal]) -> str:
    return json.dumps([asdict(g) for g in goals], sort_keys=True)


def goals_from_json(text: str) -> tuple[Goal, ...]:
    return tuple(Goal(**g) for g in json.loads(text))


def to_frame(samples: Sequence[Sample]):
    import pandas as pd

    return pd.DataFrame([{"question": s.question, "intent": s.intent, "family": s.family,
                          "style": s.style, "split": s.split, "composite": s.composite,
                          "goals": goals_json(s.goals),
                          "clarification": s.clarification or ""}
                         for s in samples])


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dataset_meta(samples: Sequence[Sample], version: str, templates_path: Path) -> dict[str, Any]:
    from finagent.dataset.build import git_state

    commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
    plain = [s for s in samples if not s.composite]
    families = Counter((s.split, s.intent) for s in {(s.family, s.split, s.intent): s
                                                     for s in plain}.values())
    return {
        "version": version, "generator": "finagent.planner.intent_data",
        "templates_sha256": _sha256(templates_path), "intents_sha256": _sha256(INTENTS_PATH),
        "universe_sha256": _sha256(UNIVERSE_PATH), "git_commit": commit, "git_dirty": dirty,
        "questions": len(plain), "composites": len(samples) - len(plain),
        "by_split": dict(Counter(s.split for s in plain)),
        "by_intent": {f"{k[0]}/{k[1]}": v for k, v in
                      sorted(Counter((s.split, s.intent) for s in plain).items())},
        "families": {f"{k[0]}/{k[1]}": v for k, v in sorted(families.items())},
        "issuer_split": TRAIN,
    }


def write_dataset(samples: Sequence[Sample], version: str, datasets_dir: Path = DATASETS_DIR,
                  templates_path: Path = TEMPLATES_PATH) -> Path:
    datasets_dir.mkdir(parents=True, exist_ok=True)
    path = datasets_dir / f"intents_{version}.parquet"
    to_frame(samples).to_parquet(path, index=False)
    meta = dataset_meta(samples, version, templates_path)
    (datasets_dir / f"intents_{version}.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build(version: str, datasets_dir: Path = DATASETS_DIR,
          templates_path: Path = TEMPLATES_PATH) -> Path:
    templates = load_templates(templates_path)
    samples = generate(templates, load_intent_vocabulary(), load_issuer_names(), load_universe(),
                       load_planner_config())
    return write_dataset(samples, version, datasets_dir, templates_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--datasets-dir", default=str(DATASETS_DIR))
    args = parser.parse_args(argv)
    path = build(args.version, Path(args.datasets_dir))
    meta = json.loads(path.with_name(path.name.replace(".parquet", ".meta.json")).read_text())
    print(f"Wrote {path}: {meta['questions']} questions, {meta['composites']} composites, "
          f"{meta['by_split']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
