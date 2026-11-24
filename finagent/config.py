"""Configuration loaders (blueprint Appendix A: concept map)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
CONCEPT_MAP_PATH = CONFIG_DIR / "concept_map.yaml"

PeriodType = Literal["duration", "instant"]
SignRule = Literal["positive", "either"]

_PERIOD_TYPES = ("duration", "instant")
_SIGN_RULES = ("positive", "either")


class ConfigError(ValueError):
    """Raised when a configuration file is malformed."""


@dataclass(frozen=True)
class LineItemSpec:
    item_id: str
    label: str
    statement: str
    period_type: PeriodType
    sign: SignRule
    core: bool
    concepts: tuple[str, ...]


def _spec(raw: object) -> LineItemSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"Concept map item must be a mapping: {raw!r}")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise ConfigError(f"Concept map item without an id: {raw!r}")
    for key in ("label", "statement", "period_type", "sign", "core", "concepts"):
        if key not in raw:
            raise ConfigError(f"Item {item_id} is missing {key}")
    if raw["period_type"] not in _PERIOD_TYPES:
        raise ConfigError(f"Item {item_id}: period_type must be one of {_PERIOD_TYPES}")
    if raw["sign"] not in _SIGN_RULES:
        raise ConfigError(f"Item {item_id}: sign must be one of {_SIGN_RULES}")
    if not isinstance(raw["core"], bool):
        raise ConfigError(f"Item {item_id}: core must be true or false")
    concepts = raw["concepts"]
    if (
        not isinstance(concepts, list)
        or not concepts
        or not all(isinstance(c, str) and ":" in c for c in concepts)
    ):
        raise ConfigError(f"Item {item_id}: concepts must be a non-empty list of prefix:Name")
    if len(set(concepts)) != len(concepts):
        raise ConfigError(f"Item {item_id} lists a concept twice")
    return LineItemSpec(
        item_id=item_id,
        label=str(raw["label"]),
        statement=str(raw["statement"]),
        period_type=raw["period_type"],
        sign=raw["sign"],
        core=raw["core"],
        concepts=tuple(concepts),
    )


def load_concept_map(path: Path | str = CONCEPT_MAP_PATH) -> dict[str, LineItemSpec]:
    """Line items keyed by id, in file order. A concept may belong to only one item."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load concept map: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ConfigError("Concept map must have a top-level 'items' list")
    specs: dict[str, LineItemSpec] = {}
    owner: dict[str, str] = {}
    for raw in data["items"]:
        spec = _spec(raw)
        if spec.item_id in specs:
            raise ConfigError(f"Duplicate item id: {spec.item_id}")
        for concept in spec.concepts:
            if concept in owner:
                raise ConfigError(
                    f"Concept {concept} is listed under both {owner[concept]} and {spec.item_id}"
                )
            owner[concept] = spec.item_id
        specs[spec.item_id] = spec
    return specs


def core_items(specs: dict[str, LineItemSpec]) -> list[LineItemSpec]:
    """The items flagged core: true, in file order."""
    return [spec for spec in specs.values() if spec.core]


UNIVERSE_PATH = CONFIG_DIR / "universe.yaml"
SPLITS = ("train", "validation", "held_out")


def load_universe(path: Path = UNIVERSE_PATH) -> dict[str, str]:
    """Ticker -> split (blueprint 9.2). Rejects unknown splits and a ticker in two splits."""
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict) or set(raw) - set(SPLITS):
        raise ConfigError(f"Universe must map {SPLITS} to ticker lists")
    universe: dict[str, str] = {}
    for split in SPLITS:
        tickers = raw.get(split) or []
        if not isinstance(tickers, list) or not all(isinstance(t, str) and t for t in tickers):
            raise ConfigError(f"Universe split {split} must be a list of tickers")
        for ticker in tickers:
            key = ticker.strip().upper()
            if key in universe:
                raise ConfigError(f"{key} is in both {universe[key]} and {split}")
            universe[key] = split
    return universe


HELD_OUT = "held_out"


class HeldOutError(ValueError):
    """A held-out company was requested without the explicit include-held-out flag (9.2).

    Held-out companies are scored once per model version (the final E1 run) and never used
    for debugging or tuning.
    """


def check_not_held_out(
    tickers: Iterable[str], universe: Mapping[str, str], include_held_out: bool
) -> None:
    """Raise HeldOutError if any ticker is held out and include_held_out is False."""
    if include_held_out:
        return
    held = sorted(t for t in (x.strip().upper() for x in tickers) if universe.get(t) == HELD_OUT)
    if held:
        raise HeldOutError(
            f"{', '.join(held)} held out; pass include_held_out (final E1 run only)"
        )


LABEL_RULES_PATH = CONFIG_DIR / "label_rules.yaml"
E1_SCORING_PATH = CONFIG_DIR / "e1_scoring.yaml"
_STATEMENTS = ("IS", "BS", "CF")


@dataclass(frozen=True)
class LabelRule:
    """Relabel source_item -> target_item unless a later row on the statement carries one of
    unless_below (configs/label_rules.yaml; BC14)."""

    rule_id: str
    statement: str
    source_item: str
    target_item: str
    unless_below: tuple[str, ...]


@dataclass(frozen=True)
class Equivalence:
    """Two items E1 accepts for each other when the filing reports no NCI (BC13)."""

    items: tuple[str, str]
    nci_concepts: tuple[str, ...]
    period_type: str


def _yaml_list(path: Path | str, key: str) -> list:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise ConfigError(f"{path} must have a top-level '{key}' list")
    return data[key]


def _known_item(item: object, items: Mapping[str, LineItemSpec], where: str) -> str:
    if not isinstance(item, str) or item not in items:
        raise ConfigError(f"{where}: unknown item {item!r}")
    return item


def load_label_rules(
    path: Path | str = LABEL_RULES_PATH, concept_map: Mapping[str, LineItemSpec] | None = None
) -> list[LabelRule]:
    items = concept_map if concept_map is not None else load_concept_map()
    rules = []
    for raw in _yaml_list(path, "rules"):
        rule_id = raw.get("id")
        if not rule_id or raw.get("statement") not in _STATEMENTS:
            raise ConfigError(f"label rule {raw!r}: needs an id and a statement in {_STATEMENTS}")
        concepts = raw.get("unless_below") or []
        if not isinstance(concepts, list) or not all(isinstance(c, str) for c in concepts):
            raise ConfigError(f"label rule {rule_id}: unless_below must be a list of concepts")
        rules.append(LabelRule(
            rule_id, raw["statement"],
            _known_item(raw.get("source_item"), items, f"label rule {rule_id}"),
            _known_item(raw.get("target_item"), items, f"label rule {rule_id}"),
            tuple(concepts),
        ))
    if len({r.rule_id for r in rules}) != len(rules):
        raise ConfigError("label rule ids must be unique")
    return rules


def load_e1_equivalences(
    path: Path | str = E1_SCORING_PATH, concept_map: Mapping[str, LineItemSpec] | None = None
) -> list[Equivalence]:
    items = concept_map if concept_map is not None else load_concept_map()
    found = []
    for raw in _yaml_list(path, "equivalences"):
        pair = raw.get("items")
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            raise ConfigError(f"equivalence {raw!r}: items must be two different items")
        concepts = raw.get("nci_concepts")
        if not concepts or not all(isinstance(c, str) for c in concepts):
            raise ConfigError(f"equivalence {pair}: nci_concepts must be a non-empty list")
        if raw.get("period_type") not in _PERIOD_TYPES:
            raise ConfigError(f"equivalence {pair}: period_type must be one of {_PERIOD_TYPES}")
        found.append(Equivalence(
            (_known_item(pair[0], items, "equivalence"), _known_item(pair[1], items, "equivalence")),
            tuple(concepts), raw["period_type"],
        ))
    return found
