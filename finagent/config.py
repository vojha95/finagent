"""Configuration loaders (blueprint Appendix A: concept map)."""

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
