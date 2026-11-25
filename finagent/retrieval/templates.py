"""Frozen evidence query templates (configs/query_templates.yaml; blueprint 8.13, 8.14).

The templates were frozen before the chunker ran on any filing (Phase 7.2): the file's sha256
must equal configs/query_templates.sha256, so they cannot drift towards what retrieval returns
(E3 scores these exact queries). A composite signal (R11) unites the queries and anchors of the
signals it composes.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from finagent.config import CONFIG_DIR, QUERY_TEMPLATES_PATH, ConfigError

TEMPLATES_HASH_PATH = CONFIG_DIR / "query_templates.sha256"
DIRECTIONS = ("increased", "decreased")


class TemplateFreezeError(ConfigError):
    """The query templates differ from the frozen version."""


@dataclass(frozen=True)
class SignalQuery:
    signal_id: str
    name: str
    focus: str
    query: str
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class QueryTemplates:
    version: int
    sha256: str
    signals: dict[str, SignalQuery]
    explain_template: str
    metrics: dict[str, tuple[str, tuple[str, ...]]]  # metric -> (phrase, anchors)

    def explain(self, metric: str, direction: str) -> tuple[str, tuple[str, ...]]:
        """(query, anchors) for explaining a reported metric's movement."""
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        default = metric.replace("_", " ")
        phrase, anchors = self.metrics.get(metric, (default, (default,)))
        return self.explain_template.format(phrase=phrase, direction=direction), anchors


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_frozen(path: Path | str = QUERY_TEMPLATES_PATH,
                 hash_path: Path | str = TEMPLATES_HASH_PATH) -> str:
    """The templates' sha256; TemplateFreezeError when it is not the frozen one."""
    actual = file_sha256(path)
    frozen = Path(hash_path).read_text(encoding="utf-8").split()[0]
    if actual != frozen:
        raise TemplateFreezeError(f"{path}: sha256 {actual} is not the frozen {frozen}")
    return actual


def _signal(signal_id: str, raw: dict[str, Any], every: dict[str, Any]) -> SignalQuery:
    parts = [every[s] for s in raw.get("compose") or ()] or [raw]
    query = " ".join(str(p["query"]) for p in parts)
    anchors = tuple(dict.fromkeys(str(a).lower() for p in parts for a in p["anchors"]))
    return SignalQuery(signal_id, str(raw["name"]), str(raw["focus"]), query, anchors)


def load_query_templates(path: Path | str = QUERY_TEMPLATES_PATH,
                         hash_path: Path | str = TEMPLATES_HASH_PATH) -> QueryTemplates:
    """The frozen templates (the hash is checked first)."""
    sha = check_frozen(path, hash_path)
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    try:
        raw = data["signals"]
        signals = {str(k): _signal(str(k), v, raw) for k, v in raw.items()}
        explain = data["explain"]
        metrics = {str(k): (str(v["phrase"]), tuple(str(a).lower() for a in v["anchors"]))
                   for k, v in explain["metrics"].items()}
        return QueryTemplates(int(data["version"]), sha, signals, str(explain["query"]),
                              metrics)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"query templates: {exc}") from exc
