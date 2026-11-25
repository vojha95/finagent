"""Evidence v2 quote acceptance threshold (D-EV, post-hoc; docs/evidence_v2.md section 1).

    python -m finagent.evaluation.quote_threshold [--run-id e3-0002] [--record]

The rule was written before any curve was computed (configs/e3.yaml `quote_threshold`). One E3
query is one unit: its score is the cross-encoder score of the rerank variant's rank-1 chunk
(gold/e3_pool_key.jsonl), its relevance that chunk's label (gold/e3_labels.jsonl; strict >= 2,
lenient >= 1). A threshold t accepts a query when score >= t; below it the answer is
EVIDENCE_GAP.

choose_threshold: on the training queries, the cut (between distinct scores, or accept-all, or
accept-none) that maximises +1 per accepted relevant query and -cost per accepted irrelevant
one; a gap scores 0. Ties: the cut accepting more. The value is midway between the lowest
accepted and the highest rejected score (accept-all -inf, accept-none +inf).

Leave one company out: each company's queries are scored with the threshold chosen on the
other companies; the pooled out-of-fold precision and coverage are the cross-validated result.
The same for every cost in `curve_costs` gives the cross-validated curve (report only). The
shipped threshold is the rule on all queries. Writes runs/<run>/header.json, metrics.json and
docs/e3_threshold_<NNNN>.md; --record appends the run to eval/experiments.csv as an E3 row
(run ids continue the E3 numbering; e3-0001 was the variant scoring).
"""

import argparse
import csv
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import RETRIEVAL_VARIANTS, ConfigError, _load_yaml
from finagent.evaluation.e3 import E3_PATH, EXPERIMENTS, ROOT, RUNS_DIR, file_sha256, read_jsonl

STRICT, LENIENT = "strict", "lenient"


@dataclass(frozen=True)
class ThresholdSpec:
    """configs/e3.yaml `quote_threshold`."""

    variant: str
    reference: str
    relevance: dict[str, int]
    decide_on: str
    cost: float
    curve_costs: tuple[float, ...]


def load_threshold_spec(path: Path | str = E3_PATH) -> ThresholdSpec:
    data = _load_yaml(Path(path))["quote_threshold"]
    relevance = {str(k): int(v) for k, v in data["relevance"].items()}
    spec = ThresholdSpec(str(data["variant"]), str(data["reference"]), relevance,
                         str(data["decide_on"]), float(data["cost"]),
                         tuple(float(c) for c in data["curve_costs"]))
    if {spec.variant, spec.reference} - set(RETRIEVAL_VARIANTS):
        raise ConfigError("quote_threshold: unknown variant or reference")
    if set(relevance) != {STRICT, LENIENT} or spec.decide_on not in relevance:
        raise ConfigError("quote_threshold: relevance needs strict and lenient; decide_on one")
    if spec.cost <= 0 or spec.cost not in spec.curve_costs:
        raise ConfigError("quote_threshold: cost must be positive and on the curve")
    return spec


@dataclass(frozen=True)
class TopChunk:
    """One E3 query: the rank-1 chunk of the variant, its score and label, and the label of
    the reference variant's rank-1 chunk."""

    query_id: str
    issuer: str
    kind: str
    chunk_id: str
    score: float
    label: int
    reference_label: int


def top_chunks(key_rows: Iterable[Mapping[str, Any]], label_records: Iterable[Mapping[str, Any]],
               variant: str, reference: str) -> list[TopChunk]:
    """Each labelled query's rank-1 chunk under `variant` (with its score) and `reference`."""
    first: dict[tuple[str, str], tuple[str, float]] = {}
    for row in key_rows:
        for name, rank, score in row["found"]:
            if name in (variant, reference) and int(rank) == 1:
                if (row["query_id"], name) in first:
                    raise ValueError(f"{row['query_id']} {name}: two rank-1 chunks")
                first[(row["query_id"], name)] = (row["chunk_id"], float(score))
    found = []
    for r in label_records:
        labels = {x["chunk_id"]: int(x["label"]) for x in r["labels"]}
        q = r["query_id"]
        if (q, variant) not in first or (q, reference) not in first:
            raise ValueError(f"{q}: no rank-1 chunk for {variant} or {reference}")
        chunk, score = first[(q, variant)]
        found.append(TopChunk(q, r["issuer"], r["kind"], chunk, score, labels[chunk],
                              labels[first[(q, reference)][0]]))
    return found


def _midway(high: float, low: float) -> float:
    return (high + low) / 2


def cuts(scores: Sequence[float]) -> list[tuple[int, float]]:
    """(number accepted, threshold) for accept-none, every cut between distinct scores (ties
    accepted together) and accept-all, scores sorted highest first."""
    s = sorted(scores, reverse=True)
    found = [(0, math.inf)]
    for j in range(1, len(s)):
        if s[j - 1] > s[j]:
            found.append((j, _midway(s[j - 1], s[j])))
    if s:
        found.append((len(s), -math.inf))
    return found


def choose_threshold(points: Sequence[tuple[float, bool]], cost: float) -> float:
    """The primary rule (module docstring): points are (score, relevant)."""
    ordered = sorted(points, key=lambda p: -p[0])
    best: tuple[float, int, float] | None = None  # (utility, accepted, threshold)
    for accepted, t in cuts([p[0] for p in points]):
        utility = sum(1.0 if rel else -cost for _, rel in ordered[:accepted])
        if best is None or (utility, accepted) > (best[0], best[1]):
            best = (utility, accepted, t)
    return math.inf if best is None else best[2]


def reference_threshold(points: Sequence[tuple[float, bool]], target: float) -> float:
    """Sensitivity (a): the lowest threshold whose precision of accepted points is at least
    `target` (+inf when no cut reaches it)."""
    ordered = sorted(points, key=lambda p: -p[0])
    chosen = math.inf
    for accepted, t in cuts([p[0] for p in points]):
        if accepted and sum(rel for _, rel in ordered[:accepted]) / accepted >= target:
            chosen = t
    return chosen


@dataclass(frozen=True)
class OperatingPoint:
    queries: int
    accepted: int
    strict: int  # accepted queries whose top chunk is strictly relevant
    lenient: int

    @property
    def coverage(self) -> float | None:
        return self.accepted / self.queries if self.queries else None

    def precision(self, definition: str) -> float | None:
        hits = self.strict if definition == STRICT else self.lenient
        return hits / self.accepted if self.accepted else None


def operating_point(chunks: Sequence[TopChunk], threshold: float,
                    relevance: Mapping[str, int]) -> OperatingPoint:
    kept = [c for c in chunks if c.score >= threshold]
    return OperatingPoint(len(chunks), len(kept),
                          sum(c.label >= relevance[STRICT] for c in kept),
                          sum(c.label >= relevance[LENIENT] for c in kept))


def add(a: OperatingPoint, b: OperatingPoint) -> OperatingPoint:
    return OperatingPoint(a.queries + b.queries, a.accepted + b.accepted, a.strict + b.strict,
                          a.lenient + b.lenient)


@dataclass(frozen=True)
class Fold:
    company: str
    threshold: float
    point: OperatingPoint


Rule = Callable[[Sequence[TopChunk]], float]


def utility_rule(cost: float, relevance: Mapping[str, int], definition: str) -> Rule:
    return lambda train: choose_threshold(
        [(c.score, c.label >= relevance[definition]) for c in train], cost)


def example_rule(relevance: Mapping[str, int]) -> Rule:
    """Sensitivity (a): the reference variant's strict rank-1 precision on the training
    queries is the target."""
    def rule(train: Sequence[TopChunk]) -> float:
        target = sum(c.reference_label >= relevance[STRICT] for c in train) / len(train)
        return reference_threshold([(c.score, c.label >= relevance[STRICT]) for c in train],
                                   target)
    return rule


def leave_one_company_out(chunks: Sequence[TopChunk], rule: Rule,
                          relevance: Mapping[str, int]) -> list[Fold]:
    """Each company scored with the threshold the rule picks on the other companies."""
    companies = list(dict.fromkeys(c.issuer for c in chunks))
    folds = []
    for company in companies:
        train = [c for c in chunks if c.issuer != company]
        test = [c for c in chunks if c.issuer == company]
        t = rule(train)
        folds.append(Fold(company, t, operating_point(test, t, relevance)))
    return folds


def pooled(folds: Sequence[Fold]) -> OperatingPoint:
    total = OperatingPoint(0, 0, 0, 0)
    for f in folds:
        total = add(total, f.point)
    return total


def run(spec: ThresholdSpec, key_rows: Iterable[Mapping[str, Any]],
        label_records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The threshold, its leave-one-company-out result, the curves and the sensitivities."""
    chunks = top_chunks(key_rows, label_records, spec.variant, spec.reference)
    rel = spec.relevance
    primary = utility_rule(spec.cost, rel, spec.decide_on)
    folds = leave_one_company_out(chunks, primary, rel)
    curve = []
    for cost in spec.curve_costs:
        rule = utility_rule(cost, rel, spec.decide_on)
        curve.append((cost, pooled(leave_one_company_out(chunks, rule, rel)), rule(chunks)))
    in_sample = [(t, operating_point(chunks, t, rel)) for _, t in cuts([c.score for c in chunks])]
    reference = OperatingPoint(len(chunks), len(chunks),
                               sum(c.reference_label >= rel[STRICT] for c in chunks),
                               sum(c.reference_label >= rel[LENIENT] for c in chunks))
    sensitivity = {}
    lenient_definition = LENIENT if spec.decide_on == STRICT else STRICT
    for name, rule in (("example_rule", example_rule(rel)),
                       (f"utility_{lenient_definition}",
                        utility_rule(spec.cost, rel, lenient_definition))):
        sensitivity[name] = (leave_one_company_out(chunks, rule, rel), rule(chunks))
    threshold = primary(chunks)
    return {"chunks": chunks, "threshold": threshold, "folds": folds, "cv": pooled(folds),
            "curve": curve, "in_sample": in_sample, "reference": reference,
            "sensitivity": sensitivity, "full": operating_point(chunks, threshold, rel)}


# ---------------------------------------------------------------- report

def _t(x: float) -> str:
    return "accept all" if x == -math.inf else "accept none" if x == math.inf else f"{x:.3f}"


def _p(point: OperatingPoint, definition: str) -> str:
    p = point.precision(definition)
    hits = point.strict if definition == STRICT else point.lenient
    return "—" if p is None else f"{p:.3f} ({hits}/{point.accepted})"


def _cov(point: OperatingPoint) -> str:
    return f"{point.coverage:.3f} ({point.accepted}/{point.queries})"


def render(result: Mapping[str, Any], spec: ThresholdSpec, run_id: str,
           header: Mapping[str, Any]) -> str:
    """The threshold report (markdown)."""
    cv, full, ref = result["cv"], result["full"], result["reference"]
    lines = [f"# Evidence v2 quote threshold ({run_id})", "",
             "Generated by `python -m finagent.evaluation.quote_threshold` by the rule written "
             "before any curve was computed (docs/evidence_v2.md section 1). **Post-hoc** (D-EV). "
             f"Commit `{header['git_commit']}`{' (dirty)' if header['git_dirty'] else ''}; "
             f"{len(result['chunks'])} E3 queries; score: the {spec.variant} rank-1 chunk's "
             f"cross-encoder score; cost {spec.cost:g} on {spec.decide_on} relevance.", "",
             "## Choice", "",
             f"**Shipped threshold: {_t(result['threshold'])}** (the rule on all queries).", "",
             "| | coverage | strict precision | lenient precision |", "|---|---|---|---|",
             f"| leave-one-company-out (cross-validated) | {_cov(cv)} | {_p(cv, STRICT)} | "
             f"{_p(cv, LENIENT)} |",
             f"| shipped threshold on all queries (in-sample) | {_cov(full)} | "
             f"{_p(full, STRICT)} | {_p(full, LENIENT)} |",
             f"| {spec.reference} rank 1, no threshold (v1 retrieval) | {_cov(ref)} | "
             f"{_p(ref, STRICT)} | {_p(ref, LENIENT)} |",
             f"| {spec.variant} rank 1, no threshold | "
             f"{_cov(result['in_sample'][-1][1])} | {_p(result['in_sample'][-1][1], STRICT)} | "
             f"{_p(result['in_sample'][-1][1], LENIENT)} |", "",
             "## Folds (threshold chosen on the other companies)", "",
             "| left out | threshold | accepted | strict | lenient |", "|---|---|---|---|---|"]
    lines += [f"| {f.company} | {_t(f.threshold)} | {f.point.accepted}/{f.point.queries} | "
              f"{f.point.strict} | {f.point.lenient} |" for f in result["folds"]]
    lines += ["", "## Cross-validated curve (cost of a misleading quote; report only except the "
              f"choice, {spec.cost:g})", "",
              "| cost | coverage | strict precision | lenient precision | threshold on all |",
              "|---|---|---|---|---|"]
    lines += [f"| {cost:g}{' (choice)' if cost == spec.cost else ''} | {_cov(p)} | "
              f"{_p(p, STRICT)} | {_p(p, LENIENT)} | {_t(t)} |" for cost, p, t in result["curve"]]
    lines += ["", "## In-sample curve (all queries; every cut)", "",
              "| threshold | coverage | strict precision | lenient precision |",
              "|---|---|---|---|"]
    lines += [f"| {_t(t)} | {_cov(p)} | {_p(p, STRICT)} | {_p(p, LENIENT)} |"
              for t, p in result["in_sample"] if p.accepted]
    lines += ["", "## Sensitivity (reported; does not change the choice)", "",
              "| rule | cross-validated coverage | strict precision | lenient precision | "
              "threshold on all |", "|---|---|---|---|---|"]
    names = {"example_rule": f"(a) lowest threshold with strict precision >= {spec.reference} "
                             "rank-1 strict precision (training queries)",
             "utility_lenient": f"(b) the primary rule on lenient relevance (cost {spec.cost:g})",
             "utility_strict": f"(b) the primary rule on strict relevance (cost {spec.cost:g})"}
    for name, (folds, t) in result["sensitivity"].items():
        p = pooled(folds)
        lines.append(f"| {names[name]} | {_cov(p)} | {_p(p, STRICT)} | {_p(p, LENIENT)} | "
                     f"{_t(t)} |")
    lines += ["", "## Queries (score order)", "",
              "| query | company | kind | score | label | accepted |", "|---|---|---|---|---|---|"]
    lines += [f"| {c.query_id} | {c.issuer} | {c.kind} | {c.score:.3f} | {c.label} | "
              f"{'yes' if c.score >= result['threshold'] else 'no'} |"
              for c in sorted(result["chunks"], key=lambda c: -c.score)]
    return "\n".join(lines) + "\n"


def _json_threshold(x: float) -> float | str:
    return "-inf" if x == -math.inf else "inf" if x == math.inf else x


def metrics_record(result: Mapping[str, Any]) -> dict[str, Any]:
    def point(p: OperatingPoint) -> dict[str, Any]:
        return {**asdict(p), "coverage": p.coverage, "precision_strict": p.precision(STRICT),
                "precision_lenient": p.precision(LENIENT)}

    return {"threshold": _json_threshold(result["threshold"]), "cv": point(result["cv"]),
            "in_sample_at_threshold": point(result["full"]),
            "reference_rank1": point(result["reference"]),
            "folds": [{"company": f.company, "threshold": _json_threshold(f.threshold),
                       **point(f.point)} for f in result["folds"]],
            "curve": [{"cost": c, "threshold_all": _json_threshold(t), **point(p)}
                      for c, p, t in result["curve"]],
            "sensitivity": {k: {"threshold_all": _json_threshold(t), **point(pooled(f))}
                            for k, (f, t) in result["sensitivity"].items()}}


def record(run_id: str, header: Mapping[str, Any], metrics: Mapping[str, Any],
           spec: ThresholdSpec, experiments: Path = EXPERIMENTS) -> None:
    """Append the run to eval/experiments.csv (Appendix F.2) as an E3 row."""
    cv = metrics["cv"]
    with experiments.open("a", newline="") as handle:
        csv.writer(handle).writerow([
            run_id, date.today().isoformat(), "E3", "retrieval",
            f"{spec.variant} quote threshold (cost {spec.cost:g}, {spec.decide_on})",
            "post-hoc (D-EV): quote acceptance threshold on the reranker score, chosen by "
            "leave-one-company-out on the E3 labels (docs/evidence_v2.md section 1)",
            "e3_labels", "validation (test companies)", json.dumps(metrics, default=str),
            header["git_commit"] + ("-dirty" if header["git_dirty"] else ""),
            header["evidence_manifest_sha256"][:12],
            f"threshold {metrics['threshold']}; cross-validated coverage {cv['coverage']:.3f}, "
            f"strict precision {cv['precision_strict']}, lenient {cv['precision_lenient']}; "
            "evidence v2 default; reported next to e3-0001, not instead of it"])


def main(argv: Sequence[str] | None = None) -> int:
    from finagent.dataset.build import git_state
    from finagent.evaluation.e3 import _paths, load_e3_spec

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", default="e3-0002")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    spec = load_threshold_spec()
    files = _paths(load_e3_spec())
    result = run(spec, read_jsonl(files["pool_key"]), read_jsonl(files["labels"]))
    commit, dirty = git_state(ROOT, ignore=("datasets", "models"))
    header = {"run_id": args.run_id, "git_commit": commit, "git_dirty": dirty,
              "evidence_manifest_sha256": file_sha256(ROOT / "evidence" / "manifest.json"),
              "labels_sha256": file_sha256(files["labels"]),
              "pool_key_sha256": file_sha256(files["pool_key"]), "spec": asdict(spec)}
    metrics = metrics_record(result)
    directory = RUNS_DIR / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "header.json").write_text(json.dumps(header, indent=2) + "\n")
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str) + "\n")
    doc = ROOT / "docs" / f"e3_threshold_{args.run_id.split('-')[-1]}.md"
    doc.write_text(render(result, spec, args.run_id, header), encoding="utf-8")
    print(f"threshold {_t(result['threshold'])}; cross-validated coverage "
          f"{_cov(result['cv'])}, strict {_p(result['cv'], STRICT)}, "
          f"lenient {_p(result['cv'], LENIENT)}")
    print(f"Wrote {directory} and {doc}")
    if args.record:
        record(args.run_id, header, metrics, spec)
        print(f"Recorded {args.run_id} in {EXPERIMENTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
