"""Shared planner test helpers: the real compiler resources (configs and the pinned SEC ticker
map; no filing is read) and a stub classifier for the trained parser."""

from dataclasses import dataclass
from functools import lru_cache

from finagent.config import HELD_OUT, load_issuer_names, load_universe
from finagent.planner.compile import CompilerResources


@lru_cache(maxsize=1)
def resources() -> CompilerResources:
    return CompilerResources.load()


def held_out_name() -> tuple[str, str]:
    """(ticker, first listed name) of a held-out company, read from the config, so no test file
    names one. Resolution knows the name; no data for the company is read."""
    universe = load_universe()
    ticker = sorted(t for t, s in universe.items() if s == HELD_OUT)[0]
    return ticker, load_issuer_names().names[ticker][0]


@dataclass
class StubClassifier:
    """predict() returns a fixed (label, confidence) for every body."""

    label: str
    confidence: float
    version: str = "stub"

    def predict(self, bodies):
        return [(self.label, self.confidence) for _ in bodies]
