"""E3 scoring (blueprint 10.3; Phase 7.6): Recall@5 and MRR@5 per retrieval variant.

The rule was written before any score was computed (docs/e3_v1.md section 4; configs/e3.yaml
`scoring`):

- each variant's ranked list is the chunks the pool key gives it at ranks 1..depth; every
  variant is scored at the same depth (5), the rerank variant included (#243);
- relevance: primary (lenient) label >= 1, secondary (strict) label = 2;
- a "better passage" is a relevant passage no variant found (a pool miss) under every
  definition: it adds 1 to the Recall denominator and never to MRR;
- means over the queries with at least one relevant passage; the others are counted apart;
- 95% percentile bootstrap over queries; each variant against the baseline (bm25) by a paired
  bootstrap of the MRR@5 difference;
- the default moves only if a variant's primary MRR@5 beats the baseline with a paired interval
  above zero; several qualify -> the lowest ms per query (retrieval-0003); else it stays.

Pure functions; finagent.evaluation.e3 `score` reads the gold files and writes the run.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from finagent.config import RETRIEVAL_VARIANTS, ConfigError, _load_yaml

PRIMARY, SECONDARY = "primary", "secondary"
DEFINITION_NAMES = {PRIMARY: "lenient (label >= 1)", SECONDARY: "strict (label = 2)"}


@dataclass(frozen=True)
class ScoringSpec:
    depth: int
    relevance: dict[str, int]  # definition -> lowest label that counts as relevant
    decide_on: str
    baseline: str
    draws: int
    seed: int
    level: float
    ms_per_query: dict[str, float]


def load_scoring(path: Path) -> ScoringSpec:
    """configs/e3.yaml `scoring`."""
    data = _load_yaml(Path(path))["scoring"]
    relevance = {str(k): int(v) for k, v in data["relevance"].items()}
    if set(relevance) != {PRIMARY, SECONDARY}:
        raise ConfigError("e3 scoring: relevance needs primary and secondary")
    ms = {str(k): float(v) for k, v in data["ms_per_query"].items()}
    if set(ms) - set(RETRIEVAL_VARIANTS) or data["baseline"] not in ms:
        raise ConfigError("e3 scoring: ms_per_query must name known variants and the baseline")
    if data["decide_on"] not in relevance:
        raise ConfigError("e3 scoring: decide_on must be a relevance definition")
    boot = data["bootstrap"]
    return ScoringSpec(int(data["depth"]), relevance, str(data["decide_on"]),
                       str(data["baseline"]), int(boot["draws"]), int(boot["seed"]),
                       float(boot["level"]), ms)


@dataclass(frozen=True)
class QueryLabels:
    query_id: str
    issuer: str
    fiscal_year: int
    kind: str  # signal | explain
    subject: str
    labels: dict[str, int]  # chunk_id -> 0, 1 or 2
    pool_misses: int  # better passages: relevant, found by no variant


def query_labels(records: Iterable[Mapping[str, Any]]) -> list[QueryLabels]:
    """gold/e3_labels.jsonl records -> QueryLabels (a better passage is one pool miss)."""
    return [QueryLabels(r["query_id"], r["issuer"], int(r["fiscal_year"]), r["kind"],
                        r["subject"], {x["chunk_id"]: int(x["label"]) for x in r["labels"]},
                        1 if str(r.get("better_passage") or "").strip() else 0)
            for r in records]


def ranked_lists(key_rows: Iterable[Mapping[str, Any]], variants: Sequence[str], depth: int
                 ) -> dict[tuple[str, str], list[str]]:
    """(query_id, variant) -> chunk ids at ranks 1..depth, from the pool key. A list with a
    missing or repeated rank is an error (every variant returned `depth` chunks in 7.5)."""
    ranks: dict[tuple[str, str], dict[int, str]] = {}
    for row in key_rows:
        for variant, rank, _score in row["found"]:
            if variant in variants and int(rank) <= depth:
                slot = ranks.setdefault((row["query_id"], variant), {})
                if int(rank) in slot:
                    raise ValueError(f"{row['query_id']} {variant}: rank {rank} twice")
                slot[int(rank)] = row["chunk_id"]
    lists = {}
    for key, slot in ranks.items():
        if sorted(slot) != list(range(1, depth + 1)):
            raise ValueError(f"{key[0]} {key[1]}: ranks {sorted(slot)} are not 1..{depth}")
        lists[key] = [slot[r] for r in range(1, depth + 1)]
    return lists


def relevant(q: QueryLabels, threshold: int) -> set[str]:
    return {c for c, label in q.labels.items() if label >= threshold}


def recall_at(ranked: Sequence[str], relevant_chunks: set[str], misses: int, depth: int
              ) -> float | None:
    """Relevant chunks in the top `depth` / (relevant chunks + pool misses); None if no relevant
    passage exists."""
    total = len(relevant_chunks) + misses
    if total == 0:
        return None
    return len(set(ranked[:depth]) & relevant_chunks) / total


def reciprocal_rank(ranked: Sequence[str], relevant_chunks: set[str], depth: int) -> float:
    """1 / rank of the first relevant chunk in the top `depth`; 0 if none."""
    return next((1.0 / n for n, c in enumerate(ranked[:depth], 1) if c in relevant_chunks), 0.0)


@dataclass(frozen=True)
class QueryScore:
    query_id: str
    issuer: str
    kind: str
    variant: str
    definition: str
    relevant: int  # pooled relevant chunks + pool misses
    recall: float | None  # None: the query has no relevant passage (left out of the means)
    rr: float | None
    first_rank: int | None  # rank of the first relevant chunk, None if not in the top depth


def score_queries(queries: Sequence[QueryLabels], lists: Mapping[tuple[str, str], Sequence[str]],
                  variants: Sequence[str], spec: ScoringSpec) -> list[QueryScore]:
    found = []
    for q in queries:
        for definition, threshold in spec.relevance.items():
            rel = relevant(q, threshold)
            for v in variants:
                ranked = lists[(q.query_id, v)]
                recall = recall_at(ranked, rel, q.pool_misses, spec.depth)
                rr = None if recall is None else reciprocal_rank(ranked, rel, spec.depth)
                first = next((n for n, c in enumerate(ranked[:spec.depth], 1) if c in rel), None)
                found.append(QueryScore(q.query_id, q.issuer, q.kind, v, definition,
                                        len(rel) + q.pool_misses, recall, rr, first))
    return found


def _quantiles(values: np.ndarray, level: float) -> tuple[float, float]:
    tail = (1 - level) / 2
    return float(np.quantile(values, tail)), float(np.quantile(values, 1 - tail))


def bootstrap_mean(values: Sequence[float], draws: int, seed: int, level: float
                   ) -> tuple[float, tuple[float, float]] | None:
    """Mean and percentile interval over `draws` resamples of the values (None if empty)."""
    if not values:
        return None
    x = np.asarray(values, dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(x), size=(draws, len(x)))
    return float(x.mean()), _quantiles(x[idx].mean(axis=1), level)


def paired_difference(a: Sequence[float], b: Sequence[float], draws: int, seed: int,
                      level: float) -> tuple[float, tuple[float, float]] | None:
    """Mean of b - a and its interval, resampling the same queries for both (None if empty)."""
    if len(a) != len(b):
        raise ValueError("paired samples differ in length")
    if not a:
        return None
    d = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(d), size=(draws, len(d)))
    return float(d.mean()), _quantiles(d[idx].mean(axis=1), level)


@dataclass(frozen=True)
class Summary:
    variant: str
    definition: str
    n: int  # queries scored (with a relevant passage)
    left_out: int  # queries without a relevant passage under the definition
    recall: float | None
    recall_ci: tuple[float, float] | None
    mrr: float | None
    mrr_ci: tuple[float, float] | None
    mrr_diff: float | None  # this variant minus the baseline (None for the baseline)
    mrr_diff_ci: tuple[float, float] | None


def summarise(scores: Sequence[QueryScore], variants: Sequence[str], definition: str,
              spec: ScoringSpec, query_ids: set[str] | None = None) -> list[Summary]:
    """Means, intervals and the paired MRR difference against the baseline, per variant, over
    the scored queries (optionally a subset: a company or a query kind)."""
    mine = [s for s in scores if s.definition == definition
            and (query_ids is None or s.query_id in query_ids)]
    ids = sorted({s.query_id for s in mine})
    by = {(s.query_id, s.variant): s for s in mine}
    scored = [q for q in ids if by[(q, spec.baseline)].recall is not None]
    base = [by[(q, spec.baseline)].rr for q in scored]
    found = []
    for v in variants:
        recall = [by[(q, v)].recall for q in scored]
        rr = [by[(q, v)].rr for q in scored]
        r = bootstrap_mean(recall, spec.draws, spec.seed, spec.level)
        m = bootstrap_mean(rr, spec.draws, spec.seed, spec.level)
        d = None if v == spec.baseline else paired_difference(base, rr, spec.draws, spec.seed,
                                                              spec.level)
        found.append(Summary(v, definition, len(scored), len(ids) - len(scored),
                             None if r is None else r[0], None if r is None else r[1],
                             None if m is None else m[0], None if m is None else m[1],
                             None if d is None else d[0], None if d is None else d[1]))
    return found


@dataclass(frozen=True)
class Decision:
    default: str
    qualifying: tuple[str, ...]  # variants whose decisive MRR@5 beats the baseline (CI > 0)
    changed: bool
    reason: str


def decide(summaries: Sequence[Summary], spec: ScoringSpec) -> Decision:
    """Apply the default switch rule to the decisive definition's summaries."""
    decisive = [s for s in summaries if s.definition == spec.decide_on]
    qualifying = tuple(s.variant for s in decisive if s.variant != spec.baseline
                       and s.mrr_diff_ci is not None and s.mrr_diff_ci[0] > 0)
    if not qualifying:
        return Decision(spec.baseline, (), False,
                        f"no variant's {spec.decide_on} MRR@{spec.depth} beats "
                        f"{spec.baseline} with a paired 95% interval above zero; "
                        f"{spec.baseline} stays")
    cheapest = min(qualifying, key=lambda v: (spec.ms_per_query[v], v))
    return Decision(cheapest, qualifying, True,
                    f"{', '.join(qualifying)} beat {spec.baseline}; the cheapest by runtime is "
                    f"{cheapest} ({spec.ms_per_query[cheapest]:g} ms per query)")


# ---------------------------------------------------------------- report

def _f(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def _ci(c: tuple[float, float] | None, signed: bool = False) -> str:
    if c is None:
        return ""
    return f"[{c[0]:+.3f}, {c[1]:+.3f}]" if signed else f"[{c[0]:.3f}, {c[1]:.3f}]"


def summary_table(summaries: Sequence[Summary], baseline: str) -> list[str]:
    lines = [f"| variant | queries scored (left out) | Recall@5 [95% CI] | MRR@5 [95% CI] | "
             f"MRR@5 minus {baseline} [paired 95% CI] |", "|---|---|---|---|---|"]
    for s in summaries:
        diff = "—" if s.mrr_diff is None else f"{s.mrr_diff:+.3f} {_ci(s.mrr_diff_ci, True)}"
        lines.append(f"| {s.variant} | {s.n} ({s.left_out}) | {_f(s.recall)} {_ci(s.recall_ci)} "
                     f"| {_f(s.mrr)} {_ci(s.mrr_ci)} | {diff} |")
    return lines


def breakdown_table(scores: Sequence[QueryScore], variants: Sequence[str], spec: ScoringSpec,
                    groups: Mapping[str, set[str]], definition: str) -> list[str]:
    """One row per group (company or kind): n and each variant's MRR@5 [CI] and Recall@5."""
    lines = ["| group | scored (left out) | " + " | ".join(f"{v} MRR@5 [CI] / R@5"
                                                         for v in variants) + " |",
             "|---|---|" + "---|" * len(variants)]
    for name, ids in groups.items():
        s = summarise(scores, variants, definition, spec, ids)
        cells = [f"{_f(x.mrr)} {_ci(x.mrr_ci)} / {_f(x.recall)}" for x in s]
        lines.append(f"| {name} | {s[0].n} ({s[0].left_out}) | " + " | ".join(cells) + " |")
    return lines


def query_table(scores: Sequence[QueryScore], queries: Sequence[QueryLabels],
                variants: Sequence[str]) -> list[str]:
    """Per query: relevant counts (lenient / strict, pool misses) and each variant's first
    relevant rank under the lenient definition (— none in the top 5; n/a no relevant)."""
    by = {(s.query_id, s.variant, s.definition): s for s in scores}
    lines = ["| query | company | kind | relevant lenient / strict (pool misses) | "
             + " | ".join(variants) + " |", "|---|---|---|---|" + "---|" * len(variants)]
    for q in queries:
        lenient, strict = by[(q.query_id, variants[0], PRIMARY)], by[(q.query_id, variants[0],
                                                                         SECONDARY)]
        cells = []
        for v in variants:
            s = by[(q.query_id, v, PRIMARY)]
            cells.append("n/a" if s.recall is None else ("—" if s.first_rank is None
                                                         else str(s.first_rank)))
        lines.append(f"| {q.query_id} | {q.issuer} | {q.kind} {q.subject} | {lenient.relevant} "
                     f"/ {strict.relevant} ({q.pool_misses}) | " + " | ".join(cells) + " |")
    return lines
