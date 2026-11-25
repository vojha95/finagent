"""Retrieval variants side by side on the dev filings (Phase 7.4; retrieval-0003).

    python -m finagent.retrieval.compare --run-id retrieval-0003

Dev companies only (D10: AMZN and MSFT). The six test companies are E3's (7.6) and are refused
here; held-out companies are never run. Queries, as of the date, on each dev company's latest
10-K:

- every frozen signal query (configs/query_templates.yaml) over Items 7 and 8 with its notes,
  as the insight cards search them; a signal on model data (R10) searches nothing (#180);
- the dev explain questions (finagent.risk.report.EXPLAIN_QUESTIONS, compiled by the trained
  parser) over Item 7, with the computed change for the AMOUNT_MISMATCH rule (#185).

Each variant (bm25, dense, hybrid_rrf, hybrid_rrf_rerank) runs in its own process, so its wall
time and peak memory (resident set size, from the operating system) are its own; dense runs
twice, so a cold and a warm embedding cache are both measured when the cache starts empty. The
quote rules are the 7.3 rules for every variant (finagent.retrieval.quotes; score = share of
the query's IDF weight, threshold 0.30); only the chunks read differ (hybrid_rrf_rerank reads
the 3 it keeps, the others the configured `hits`). The comparison always uses evidence v1's
rules (sentence order by score, no reranker gate), whatever evidence_version is active (D-EV).

Outputs: runs/<run>/queries.json, <n>_<variant>.json, side_by_side.csv, runtime.csv and the
report (docs/retrieval_variants_v1.md; notes below the marker are kept).
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from finagent.config import RETRIEVAL_VARIANTS, load_model_pins, load_signal_config
from finagent.retrieval.quotes import ChangeCheck, find_quotes
from finagent.retrieval.report import DEV

ROOT = Path(__file__).resolve().parents[2]
RUNS = ("bm25", "dense", "dense", "hybrid_rrf", "hybrid_rrf_rerank")  # dense: cold, then warm
TOP = 3  # candidate sentences shown per variant
MARKER = "<!-- notes: text below this line is kept when the report is regenerated -->"


class NotDevError(ValueError):
    """A comparison query for a company that is not a dev company (D10)."""


@dataclass(frozen=True)
class QuerySpec:
    """One search of the comparison: a signal's frozen query or an explain question's."""

    qid: str
    issuer: str
    fiscal_year: int
    kind: str  # "signal" or "explain"
    subject: str  # the signal id or the metric
    query: str
    anchors: tuple[str, ...]
    sections: tuple[str, ...]
    expected: tuple[str, str, str] | None = None  # #185 change: amount, base, unit ("" = None)
    question: str = ""

    def change_check(self) -> ChangeCheck | None:
        if self.expected is None:
            return None
        amount, base, unit = self.expected
        return ChangeCheck(Decimal(amount), Decimal(base), Decimal(unit) if unit else None)


def check_dev(queries: Sequence[QuerySpec]) -> None:
    """Refuse any query on a company that is not a dev company (D10)."""
    others = sorted({q.issuer for q in queries} - set(DEV))
    if others:
        raise NotDevError(f"variant comparison is for the dev companies only, not {others}")


def query_from_json(data: Mapping[str, Any]) -> QuerySpec:
    expected = data.get("expected")
    return QuerySpec(data["qid"], data["issuer"], int(data["fiscal_year"]), data["kind"],
                     data["subject"], data["query"], tuple(data["anchors"]),
                     tuple(data["sections"]), tuple(expected) if expected else None,
                     data.get("question", ""))


# ---------------------------------------------------------------- queries (parent process)

def signal_queries(tools: Any, issuer: str, year: int) -> list[QuerySpec]:
    """Every signal's frozen query (not a model-derived signal's, #180), as the cards search."""
    spec = tools.signal_config.evidence
    found = []
    for sid, signal in tools.signal_config.signals.items():
        if signal.data == "scenario" or sid not in tools.templates.signals:
            continue
        q = tools.templates.signals[sid]
        found.append(QuerySpec(f"{issuer} FY{year} {sid}", issuer, year, "signal", sid, q.query,
                               tuple(q.anchors), tuple(spec.sections)))
    return found


def explain_spec(tools: Any, issuer: str, period: int | str, metric: str, as_of: date,
                 question: str = "") -> QuerySpec | str:
    """The explain search of one goal, exactly as explain_movement would run it, or why it
    stops before searching (FinAgentTools.explain_inputs)."""
    if issuer not in DEV:
        raise NotDevError(f"explain query on a company that is not dev: {issuer}")
    found = tools.explain_inputs(issuer, period, metric, as_of=as_of)
    if not hasattr(found, "query"):  # an Outcome: GAP before any search
        return f"{found.status} {found.reason}: {found.detail}"
    e = found.expected
    return QuerySpec(f"{issuer} FY{found.year} explain {metric}", issuer, found.year, "explain",
                     metric, found.query, tuple(found.anchors),
                     tuple(tools.signal_config.evidence.explain_sections),
                     (str(e.amount), str(e.base), "" if e.unit is None else str(e.unit)),
                     question)


def explain_queries(tools: Any, as_of: date, questions: Sequence[str]) -> tuple[
        list[QuerySpec], list[str]]:
    """The explain questions' searches (dev companies only), and the ones not searched. The
    questions are compiled by the trained parser, as the planner does (no issuer is named in
    code)."""
    from finagent.planner.compile import (CompilerResources, compile_question,
                                          load_trained_parser)

    resources = CompilerResources.load()
    parser = load_trained_parser(resources)
    found, skipped = [], []
    for question in questions:
        compiled = compile_question(question, parser, resources)
        goals = [g for g in compiled.goals if g.kind == "explain"]
        if compiled.clarification is not None or len(goals) != 1:
            skipped.append(f"{question}: not compiled to one explain goal")
            continue
        g = goals[0]
        spec = explain_spec(tools, g.issuer, g.period, g.metric, as_of, question)
        (skipped.append(f"{question}: {spec}") if isinstance(spec, str)
         else found.append(spec))
    return found, skipped


# ---------------------------------------------------------------- one variant (child process)

def candidate_rows(found: Any, hits: Sequence[Any]) -> list[dict[str, Any]]:
    quoted = {q.text for q in found.quotes}
    rank = {h.chunk.chunk_id: h.rank for h in hits}
    return [{"text": c.quote.text, "score": c.quote.score, "chunk_id": c.quote.chunk_id,
             "section": c.quote.section, "chunk_rank": rank.get(c.quote.chunk_id),
             "quoted": c.quote.text in quoted} for c in found.candidates[:TOP]]


def run_variant(queries: Sequence[QuerySpec], variant: str, as_of: date, retriever: Any,
                spec: Any) -> dict[str, Any]:
    """Every query under one variant, with its timings (module docstring)."""
    check_dev(queries)
    if variant not in RETRIEVAL_VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    retriever.variant = variant
    timings: dict[str, Any] = {}
    filings = {}
    start = time.perf_counter()
    for q in queries:
        f = retriever.filing(q.issuer, q.fiscal_year, as_of)
        if f is not None:
            filings[f.accession] = retriever.index(f)
    timings["index_seconds"] = time.perf_counter() - start
    if variant != "bm25":
        from finagent.retrieval.variants import SearchModels
        if retriever.models is None:
            retriever.models = SearchModels.from_config(retriever.config.search)
        for index in filings.values():
            retriever.models.vectors(index, retriever.vectors)
        if variant == "hybrid_rrf_rerank":
            retriever.models.reranker()
    results, latency = [], []
    for q in queries:
        f = retriever.filing(q.issuer, q.fiscal_year, as_of)
        if f is None:
            results.append({"qid": q.qid, "outcome": "NO_FILING", "hits": [], "candidates": []})
            continue
        start = time.perf_counter()
        hits = retriever.search(q.issuer, q.fiscal_year, q.query, as_of=as_of,
                                sections=q.sections, k=spec.hits)
        found = find_quotes(retriever.index(f), hits, q.query, q.anchors, q.fiscal_year, spec,
                            retriever.config, q.change_check())
        latency.append(time.perf_counter() - start)
        results.append({
            "qid": q.qid, "outcome": f"QUOTED ({len(found.quotes)})" if found.quotes
            else "EVIDENCE_GAP",
            "hits": [{"chunk_id": h.chunk.chunk_id, "score": h.score, "rank": h.rank}
                     for h in hits],
            "candidates": candidate_rows(found, hits), "skipped": found.skipped})
    models = retriever.models
    timings.update({
        "queries": len(latency), "query_seconds_total": sum(latency),
        "query_seconds_mean": sum(latency) / len(latency) if latency else 0.0,
        "query_seconds_max": max(latency, default=0.0),
        "model_load_seconds": dict(models.load_seconds) if models else {},
        "embed_seconds": models.embed_seconds if models else 0.0,
        "chunks_embedded": models.stats.embedded if models else 0,
        "chunks_from_cache": models.stats.cached if models else 0,
        "torch_loaded": "torch" in sys.modules})
    return {"variant": variant, "as_of": as_of.isoformat(), "timings": timings,
            "results": results}


def child(run_dir: Path, variant: str, n: int, as_of: date) -> int:
    from finagent.evidence import EvidenceStore
    from finagent.retrieval.index import Retriever

    queries = [query_from_json(q) for q in
               json.loads((run_dir / "queries.json").read_text(encoding="utf-8"))["queries"]]
    retriever = Retriever(EvidenceStore(ROOT / "evidence"))
    out = run_variant(queries, variant, as_of, retriever,
                      load_signal_config().with_evidence("v1").evidence)
    (run_dir / f"{n}_{variant}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


# ---------------------------------------------------------------- measurement (parent)

def peak_rss_bytes(rusage: Any) -> int:
    """ru_maxrss is in bytes on macOS and in kilobytes on Linux."""
    return int(rusage.ru_maxrss) * (1 if sys.platform == "darwin" else 1024)


def measure(command: Sequence[str]) -> tuple[int, float, int]:
    """Run a command; its exit code, wall seconds and peak resident memory in bytes."""
    start = time.perf_counter()
    process = subprocess.Popen(list(command), cwd=ROOT)
    _, status, rusage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    return process.returncode, time.perf_counter() - start, peak_rss_bytes(rusage)


# ---------------------------------------------------------------- outputs

def _short(text: str, words: int = 40) -> str:
    parts = text.split()
    return text if len(parts) <= words else " ".join(parts[:words]) + " …"


def side_by_side_rows(queries: Sequence[QuerySpec],
                      results: Mapping[str, Mapping[str, Any]]) -> list[list[Any]]:
    """One row per query, variant and candidate (up to TOP)."""
    rows = []
    for q in queries:
        for variant, out in results.items():
            r = next(x for x in out["results"] if x["qid"] == q.qid)
            cands = r["candidates"] or [None]
            for n, c in enumerate(cands, start=1):
                rows.append([q.qid, variant, r["outcome"], n if c else "",
                             f"{c['score']:.3f}" if c else "", "Q" if c and c["quoted"] else "",
                             c["section"] if c else "", c["chunk_rank"] if c else "",
                             c["text"] if c else ""])
    return rows


def runtime_rows(runs: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    rows = []
    for run in runs:
        t = run["timings"]
        load = ", ".join(f"{k} {v:.1f}s" for k, v in t["model_load_seconds"].items()) or "—"
        cache = (f"{t['chunks_embedded']} embedded, {t['chunks_from_cache']} from cache"
                 if run["variant"] != "bm25" else "—")
        rows.append([run["variant"], f"{run['wall_seconds']:.1f}",
                     f"{run['peak_rss_bytes'] / 2 ** 20:,.0f}", f"{t['index_seconds']:.1f}",
                     load, f"{t['embed_seconds']:.1f}", cache, t["queries"],
                     f"{1000 * t['query_seconds_mean']:.0f}",
                     f"{1000 * t['query_seconds_max']:.0f}",
                     "yes" if t["torch_loaded"] else "no"])
    return rows


RUNTIME_HEADER = ["Variant", "Wall s", "Peak RSS MiB", "BM25 index s", "Model load",
                  "Embed s", "Embedding cache", "Queries", "Mean ms/query", "Max ms/query",
                  "torch loaded"]


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    def cell(v: Any) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + [
        "| " + " | ".join(cell(v) for v in row) + " |" for row in rows]


def query_block(q: QuerySpec, results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    title = q.question or f"{q.subject} ({q.kind})"
    lines = [f"### {q.qid}: {title}", "",
             f"Query: `{q.query}`; sections {', '.join(q.sections)}"
             + (f"; computed change {q.expected[0]} (#185 check)" if q.expected else "") + ".",
             ""]
    rows = []
    for variant, out in results.items():
        r = next(x for x in out["results"] if x["qid"] == q.qid)
        cells = [f"{'**Q** ' if c['quoted'] else ''}{c['score']:.2f} · {c['section']} · "
                 f"chunk #{c['chunk_rank']}: “{_short(c['text'])}”" for c in r["candidates"]]
        rows.append([variant, r["outcome"], *(cells + ["—"] * TOP)[:TOP]])
    return lines + _table(["Variant", "Outcome", "Candidate 1", "Candidate 2", "Candidate 3"],
                          rows) + [""]


def summary_rows(queries: Sequence[QuerySpec],
                 results: Mapping[str, Mapping[str, Any]]) -> list[list[Any]]:
    """Per query: each variant's outcome and best candidate score."""
    rows = []
    for q in queries:
        row: list[Any] = [q.qid]
        for out in results.values():
            r = next(x for x in out["results"] if x["qid"] == q.qid)
            best = f"{r['candidates'][0]['score']:.2f}" if r["candidates"] else "—"
            row.append(f"{r['outcome']} · best {best}")
        rows.append(row)
    return rows


def render(header: Mapping[str, Any], queries: Sequence[QuerySpec], skipped: Sequence[str],
           results: Mapping[str, Mapping[str, Any]], runs: Sequence[Mapping[str, Any]],
           pins: Mapping[str, Any]) -> str:
    variants = list(results)
    lines = [
        "# Retrieval variants v1: BM25, dense, hybrid RRF, + reranker (Phase 7.4)", "",
        f"Generated by `python -m finagent.retrieval.compare` (run `{header['run_id']}`, commit "
        f"`{header['git_commit']}`{' + uncommitted changes' if header['git_dirty'] else ''}, "
        f"evidence manifest `{header['evidence_manifest_sha256'][:12]}`, as of "
        f"{header['as_of']}). Outputs: `runs/{header['run_id']}/queries.json`, "
        "`side_by_side.csv`, `runtime.csv`, one JSON per variant run. **Dev companies only** "
        "(AMZN, MSFT; D10): no variant has run on the six test companies (E3, 7.6), and no "
        "held-out company is opened. BM25 stays the default until E3 decides.", "",
        "## Models (pinned, offline)", ""]
    lines += _table(["Key", "Model", "Repo", "Revision", "Files", "Size (bytes)",
                     "Fingerprint"],
                    [[p.key, p.model_id, p.repo, p.revision, len(p.files), f"{p.size:,}",
                      p.fingerprint[:16]] for p in pins.values()])
    lines += ["", "Every file is checked against its sha256 and size before loading "
              "(`python -m finagent.retrieval.models verify`); the models load with "
              "HF_HUB_OFFLINE=1 and local_files_only, on CPU.", "",
              "## Method", "",
              "Each variant ranks the same text chunks of the same 10-K, as of the date, in the "
              "query's sections: **bm25** (k1 1.5, b 0.75); **dense** (bge-small-en-v1.5, query "
              "with the model's retrieval instruction, cosine of unit vectors); **hybrid_rrf** "
              "(RRF, k = 60, of the full BM25 and dense rankings, top 20); "
              "**hybrid_rrf_rerank** (the RRF top 20 re-scored by ms-marco-MiniLM-L-6-v2, keep "
              "3). The quote rules are the 7.3 rules for every variant: whole sentences of 6 to "
              "60 words with an anchor term, not about an earlier year only, no contradicted "
              "change (#185); score = share of the query's IDF weight in the sentence; at most "
              "two quoted at 0.30 or above. Only the chunks read differ: the best 5 (the "
              "reranker: its 3). Candidate cells: **Q** = quoted; score; section; the chunk's "
              "rank under the variant.", "",
              "## Runtime and memory", "",
              "Each variant run is its own process (wall time includes Python start-up and "
              "imports); peak RSS is the process's maximum resident memory from the operating "
              "system. dense runs twice: the first fills the embedding cache when it is empty "
              "(cold), the second reads it (warm).", ""]
    lines += _table(RUNTIME_HEADER, runtime_rows(runs))
    lines += ["", "## Summary (outcome and best candidate score per variant)", ""]
    lines += _table(["Query", *variants], summary_rows(queries, results))
    if skipped:
        lines += ["", "Not searched: " + "; ".join(skipped) + "."]
    lines += ["", "## Side by side: explain questions", ""]
    for q in (q for q in queries if q.kind == "explain"):
        lines += query_block(q, results)
    lines += ["## Side by side: signal queries (latest 10-K)", ""]
    for q in (q for q in queries if q.kind == "signal"):
        lines += query_block(q, results)
    return "\n".join(lines) + "\n"


def write_report(path: Path, text: str) -> None:
    kept = ""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if MARKER in old:
            kept = old.split(MARKER, 1)[1]
    path.write_text(text + "\n" + MARKER + kept if kept else text + "\n" + MARKER + "\n",
                    encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 24))
    parser.add_argument("--run-id", default="retrieval-0003")
    parser.add_argument("--report", default=str(ROOT / "docs" / "retrieval_variants_v1.md"))
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)  # variant
    parser.add_argument("--n", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    run_dir = ROOT / "runs" / args.run_id
    if args.child:
        return child(run_dir, args.child, args.n, args.as_of)

    from finagent.risk.report import EXPLAIN_QUESTIONS
    from finagent.tools import FinAgentTools

    tools = FinAgentTools.default()
    queries = [q for issuer in DEV
               for q in signal_queries(tools, issuer,
                                       tools.resolve_year(issuer, "latest", args.as_of))]
    explained, skipped = explain_queries(tools, args.as_of, EXPLAIN_QUESTIONS)
    queries = explained + queries
    check_dev(queries)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "queries.json").write_text(json.dumps(
        {"as_of": args.as_of.isoformat(), "skipped": skipped,
         "queries": [asdict(q) for q in queries]}, indent=1), encoding="utf-8")
    runs = []
    for n, variant in enumerate(RUNS, start=1):
        code, seconds, rss = measure([sys.executable, "-m", "finagent.retrieval.compare",
                                      "--run-id", args.run_id, "--as-of",
                                      args.as_of.isoformat(), "--child", variant,
                                      "--n", str(n)])
        if code != 0:
            raise RuntimeError(f"variant {variant} (run {n}) failed with exit code {code}")
        out = json.loads((run_dir / f"{n}_{variant}.json").read_text(encoding="utf-8"))
        out.update(wall_seconds=seconds, peak_rss_bytes=rss)
        runs.append(out)
        print(f"{variant}: {seconds:.1f}s, peak RSS {rss / 2 ** 20:,.0f} MiB")
    results = {r["variant"]: r for r in runs}  # the last run of each variant
    with (run_dir / "side_by_side.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["query", "variant", "outcome", "candidate", "score", "quoted", "section",
                    "chunk_rank", "sentence"])
        w.writerows(side_by_side_rows(queries, results))
    with (run_dir / "runtime.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(RUNTIME_HEADER)
        w.writerows(runtime_rows(runs))
    header = {"run_id": args.run_id, "git_commit": _git("rev-parse", "--short", "HEAD"),
              "git_dirty": bool(_git("status", "--porcelain")), "as_of": args.as_of.isoformat(),
              "evidence_manifest_sha256": hashlib.sha256(
                  (ROOT / "evidence" / "manifest.json").read_bytes()).hexdigest()}
    write_report(Path(args.report), render(header, queries, skipped, results, runs,
                                           load_model_pins()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
