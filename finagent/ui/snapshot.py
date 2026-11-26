"""Ask reuses the serving run's statement sets at its as_of (BC37, proposed; speed only).

The first question about a company built its post-agent statement set live. Parsing the
filings' HTML and inline tags takes about 80% of that time (e7-0001: cold p95 16.4 s, max
35.5 s; docs/issues_log.md #360). The serving build (finagent.ui.serving) already builds every
served company's reconciliation at the snapshot date. It now also writes each one, as built,
to runs/<serving run>/ask/<ticker>.pkl, and ask/snapshot.json records:

- fingerprint: everything that decides a reconciliation (`fingerprint`). That is the package
  source (the sha256 of every finagent/**/*.py except finagent/ui/**, which is presentation and
  serving tools that the reused statement sets do not depend on; Vineet's decision, #369),
  every configs/ file except ui.yaml (app wording and run paths, read only by finagent/ui), the
  document model, its meta and dataset meta, the evidence manifest and every evidence file it
  names, the Python version and platform, and every installed package's version. The git
  commit and dirty flag are recorded for information only, never compared: a commit cannot be
  checked on a dirty tree or where there is no .git, and the hashes are the stricter test.
- files: each pickle's sha256.
- changed_during_build: any component that differed between the start and the end of the
  build. A non-empty list makes the snapshot unusable.

Snapshot is the reader. FinAgentTools(snapshot=...) consults it when its reconciliation cache
misses (finagent.tools). It answers only at its own as_of and under the FULL view, only when
the recorded fingerprint equals the current one (checked once, when opened), and only when the
pickle's sha256 matches. In every other case the tools build live, exactly as before. The
held-out guard and the point-in-time check still run on every reconciliation. Either way the
answer is the same: the snapshot holds the object that the same code built from the same
inputs. `prove` checks this object by object and answer by answer. Each issuer's path (reused,
or built live with the reason) is recorded for the app's trace note.

    python -m finagent.ui.snapshot check --serving-run serving-0002
    python -m finagent.ui.snapshot prove --serving-run serving-0002 --run-id ask-proof-0001
"""

import argparse
import hashlib
import importlib.metadata
import json
import pickle
import platform
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
ASK = "ask"
INDEX = "snapshot.json"
APP_ONLY_CONFIGS = frozenset({"ui.yaml"})  # read by finagent/ui only, never by the library
APP_ONLY_CODE = ("ui/",)  # finagent/ui/**: presentation and serving tools, not hashed (#369)
COMPONENTS = ("code", "configs", "models", "evidence", "runtime", "packages")  # compared
REUSED = "reused"


# ---------------------------------------------------------------- fingerprint

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hashes(directory: Path, pattern: str, skip: frozenset[str] = frozenset()
                ) -> dict[str, str]:
    """sha256 of every file under directory matching pattern, by relative path (hidden files,
    __pycache__ and the skipped names left out)."""
    directory = Path(directory)
    return {p.relative_to(directory).as_posix(): sha256_file(p)
            for p in sorted(directory.rglob(pattern))
            if p.is_file() and p.name not in skip and not p.name.startswith(".")
            and "__pycache__" not in p.parts}


def answer_code(hashes: Mapping[str, str]) -> dict[str, str]:
    """The code hashes that decide a reconciliation: finagent/ui/** left out (#369)."""
    return {k: v for k, v in hashes.items() if not k.startswith(APP_ONLY_CODE)}


def model_files(dataset_version: str) -> tuple[str, ...]:
    """The document model, its meta and the dataset meta it is checked against (relative)."""
    return (f"models/m1_{dataset_version}.joblib", f"models/m1_{dataset_version}.meta.json",
            f"datasets/rows_{dataset_version}.meta.json")


def evidence_hashes(evidence_dir: Path) -> dict[str, str]:
    """The manifest's sha256 and the sha256 of every file it names, as stored."""
    evidence_dir = Path(evidence_dir)
    manifest = evidence_dir / "manifest.json"
    found = {"manifest.json": sha256_file(manifest)}
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    for name in sorted({str(e["file"]) for e in entries.values()}):
        path = evidence_dir / name
        found[name] = sha256_file(path) if path.is_file() else "<missing>"
    return found


def packages() -> dict[str, str]:
    """Every installed distribution's version, by lower-case name."""
    found: dict[str, set[str]] = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if name:
            found.setdefault(name, set()).add(dist.version)
    return {k: ",".join(sorted(v)) for k, v in sorted(found.items())}


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=root,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def fingerprint(root: Path = ROOT, dataset_version: str = "v3") -> dict[str, Any]:
    """What decides a reconciliation (module docstring), plus git for information."""
    root = Path(root)
    return {
        "code": answer_code(tree_hashes(root / "finagent", "*.py")),
        "configs": tree_hashes(root / "configs", "*", APP_ONLY_CONFIGS),
        "models": {name: sha256_file(root / name) if (root / name).is_file() else "<missing>"
                   for name in model_files(dataset_version)},
        "evidence": evidence_hashes(root / "evidence"),
        "runtime": {"python": sys.version, "platform": platform.platform(),
                    "machine": platform.machine()},
        "packages": packages(),
        "git": {"commit": _git(root, "rev-parse", "--short", "HEAD"),
                "dirty": bool(_git(root, "status", "--porcelain"))},
    }


def mismatches(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Each compared component that differs, naming up to three of its changed entries. The
    code is compared without finagent/ui/** on both sides, so an index recorded before #369
    (which listed it) is read by the same rule."""
    found = []
    for component in COMPONENTS:
        a, b = recorded.get(component), current.get(component)
        if component == "code" and isinstance(a, Mapping) and isinstance(b, Mapping):
            a, b = answer_code(a), answer_code(b)
        if a == b:
            continue
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            more = f" and {len(changed) - 3} more" if len(changed) > 3 else ""
            found.append(f"{component} differ ({', '.join(changed[:3])}{more})")
        else:
            found.append(f"{component} differ")
    return found


# ---------------------------------------------------------------- writer (the serving build)

def write_reconciliation(ask_dir: Path, rec: Any) -> str:
    """ask/<issuer>.pkl, the reconciliation as built; returns the file's sha256."""
    data = pickle.dumps(rec, protocol=pickle.HIGHEST_PROTOCOL)
    (Path(ask_dir) / f"{rec.issuer}.pkl").write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_index(ask_dir: Path, run_id: str, as_of: date, files: Mapping[str, str],
                start: Mapping[str, Any], end: Mapping[str, Any]) -> dict[str, Any]:
    """ask/snapshot.json: the fingerprint at the start of the build, each pickle's sha256 and
    whatever changed during the build (which makes the snapshot unusable)."""
    document = {"run_id": run_id, "as_of": as_of.isoformat(), "policy": "BC37 (proposed)",
                "fingerprint": dict(start), "files": dict(sorted(files.items())),
                "changed_during_build": mismatches(start, end)}
    (Path(ask_dir) / INDEX).write_text(json.dumps(document, indent=1), encoding="utf-8")
    return document


# ---------------------------------------------------------------- reader (the app, E7)

@dataclass
class Snapshot:
    """The serving run's reconciliations, reused only where the module docstring allows."""

    run: str
    ask_dir: Path
    as_of: date | None
    usable: bool
    reason: str  # why it is not usable ("" when usable)
    files: dict[str, str] = field(default_factory=dict)
    sources: dict[tuple[str, date], str] = field(default_factory=dict)  # (issuer, as_of) -> path

    @classmethod
    def open(cls, run_dir: Path, current: Mapping[str, Any] | None = None,
             dataset_version: str = "v3") -> "Snapshot":
        """Read ask/snapshot.json and compare its fingerprint with the current one (computed
        here unless given)."""
        run_dir = Path(run_dir)
        ask_dir = run_dir / ASK
        index = ask_dir / INDEX
        if not index.is_file():
            return cls(run_dir.name, ask_dir, None, False, f"{run_dir.name} has no {ASK}/{INDEX}")
        doc = json.loads(index.read_text(encoding="utf-8"))
        current = current if current is not None else fingerprint(ROOT, dataset_version)
        problems = [f"changed during the build: {x}" for x in doc.get("changed_during_build", [])]
        problems += mismatches(doc["fingerprint"], current)
        return cls(run_dir.name, ask_dir, date.fromisoformat(doc["as_of"]), not problems,
                   "; ".join(problems), dict(doc["files"]))

    def _live(self, issuer: str, as_of: date, why: str) -> None:
        self.sources[(issuer, as_of)] = f"built live: {why}"

    def reconciliation(self, issuer: str, as_of: date, view: Any) -> Any:
        """The issuer's reconciliation from the snapshot, or None (the caller builds live).
        The outcome is recorded in `sources`."""
        from finagent.statements.replay import FULL

        if as_of != self.as_of:
            self._live(issuer, as_of, f"as of {as_of} is not {self.run}'s as of {self.as_of}")
            return None
        if view != FULL:
            self._live(issuer, as_of, "the evidence view is not FULL")
            return None
        if not self.usable:
            self._live(issuer, as_of, f"{self.run} does not match the current build "
                                      f"({self.reason})")
            return None
        expected = self.files.get(issuer)
        path = self.ask_dir / f"{issuer}.pkl"
        if expected is None or not path.is_file():
            self._live(issuer, as_of, f"{issuer} is not in {self.run}")
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            self._live(issuer, as_of, f"{path.name} differs from its recorded sha256")
            return None
        rec = pickle.loads(data)  # noqa: S301 (written by the serving build, sha256-checked)
        if rec.issuer != issuer or rec.as_of != as_of:
            self._live(issuer, as_of, f"{path.name} holds {rec.issuer} as of {rec.as_of}")
            return None
        self.sources[(issuer, as_of)] = f"{REUSED} from {self.run}"
        return rec

    def source(self, issuer: str, as_of: date) -> str | None:
        return self.sources.get((issuer, as_of))


# ---------------------------------------------------------------- the Ask session

@dataclass
class AskSession:
    """What the app loads once per process: planner, tools (M1), search models, snapshot."""

    resources: Any
    parser: Any
    tools: Any
    snapshot: Snapshot | None
    seconds: dict[str, float]


def load_search_models(tools: Any) -> None:
    """Load the evidence search's embedder and reranker now (otherwise on first use)."""
    from finagent.retrieval.variants import SearchModels

    retriever = tools.search()
    if retriever.models is None:
        retriever.models = SearchModels.from_config(retriever.config.search)
    retriever.models.embedder()
    retriever.models.reranker()


def load_session(serving_run: Path | None, *, include_held_out: bool = False,
                 dataset_version: str = "v3", search_models: bool = True,
                 current: Mapping[str, Any] | None = None) -> AskSession:
    """The planner, the tools over the pinned store and M1, the search models (optional) and
    the serving run's snapshot (None: always live), with the seconds each took."""
    from finagent.planner.compile import CompilerResources, load_trained_parser
    from finagent.tools import FinAgentTools

    seconds: dict[str, float] = {}
    start = time.perf_counter()
    resources = CompilerResources.load()
    parser = load_trained_parser(resources)
    seconds["planner"] = time.perf_counter() - start
    start = time.perf_counter()
    tools = FinAgentTools.default(dataset_version=dataset_version,
                                  include_held_out=include_held_out)
    seconds["tools"] = time.perf_counter() - start
    if search_models:
        start = time.perf_counter()
        load_search_models(tools)
        seconds["search_models"] = time.perf_counter() - start
    snapshot = None
    if serving_run is not None:
        start = time.perf_counter()
        snapshot = Snapshot.open(Path(serving_run), current, dataset_version)
        tools.snapshot = snapshot
        seconds["snapshot_check"] = time.perf_counter() - start
    return AskSession(resources, parser, tools, snapshot,
                      {k: round(v, 3) for k, v in seconds.items()})


def issuers_used(tools: Any, inv: Any) -> list[str]:
    """The issuers whose statement set the answer's own tool calls used, in call order (none
    for a clarification, which calls no tool)."""
    called = [s.arguments.get("issuer") for s in inv.trace if isinstance(s.arguments, Mapping)]
    cached = {issuer for issuer, as_of, _ in tools.reconciliations if as_of == inv.as_of}
    return [t for t in dict.fromkeys(called) if t in cached]


def statement_paths(tools: Any, inv: Any) -> list[str]:
    """One line per issuer: which path built the statement set the answer used (the trace
    note). Empty when no statement set was needed (e.g. a clarification)."""
    snapshot = getattr(tools, "snapshot", None)
    lines = []
    for issuer in issuers_used(tools, inv):
        source = snapshot.source(issuer, inv.as_of) if snapshot is not None else None
        lines.append(f"{issuer}: {source or 'built live: no serving run snapshot configured'}")
    return lines


# ---------------------------------------------------------------- proof (validation only)

def without_latency(inv: Any) -> Any:
    """The investigation with each step's measured latency set to 0 (never reproducible)."""
    return replace(inv, trace=tuple(replace(s, latency_ms=0.0) for s in inv.trace))


def comparable(rec: Any) -> Any:
    """The reconciliation with the Correlation Agent trace's measured latency set to 0: two
    live builds of the same company differ only there (#363; BC36's compare names it timing)."""
    return replace(rec, results=tuple(replace(r, trace=tuple(replace(t, latency_ms=0.0)
                                                             for t in r.trace))
                                      for r in rec.results))


def record_differences(a: Any, b: Any) -> tuple[list[str], int]:
    """(paths that differ, measured latencies that differ) between two reconciliations'
    records: the statement set as a statements run writes it, and the agent record."""
    from finagent.statements.report import set_record
    from finagent.ui.serving import differences
    from finagent.verify.reconcile import reconciliation_record

    def plain(rec: Any) -> Any:
        return json.loads(json.dumps({"set": set_record(rec.statement_set, "-"),
                                      "agent": reconciliation_record(rec, "-")}, default=str))

    found = differences(plain(a), plain(b))
    timing = [d for d in found if d[0].endswith(".latency_ms")]
    return [d[0] for d in found if d not in timing], len(timing)


def answer_differences(a: Any, b: Any) -> list[str]:
    """Paths where two investigations' records differ (latency excluded)."""
    from finagent.planner.trace import run_record, step_records
    from finagent.ui.serving import differences

    left = {"run": run_record(a), "steps": step_records(without_latency(a))}
    right = {"run": run_record(b), "steps": step_records(without_latency(b))}
    return [d[0] for d in differences(json.loads(json.dumps(left, default=str)),
                                      json.loads(json.dumps(right, default=str)))]


def prove(serving_run: str, run_id: str) -> int:
    """Validation companies only (BC36): (1) each reconciliation from the snapshot equals a
    fresh live build (the agent trace's measured latency aside, `comparable`); (2) every
    ui-present audit question (the 6 examples for each validation
    company and the 20 E5 canonical questions) gets the identical investigation (latency
    excluded) and plain view on the reuse path and the live path."""
    from finagent.config import load_universe
    from finagent.planner.investigate import investigate
    from finagent.ui.data import load_ui_config
    from finagent.ui.present import Labels, audit_questions, plain_text, present

    out = RUNS / run_id
    if out.exists():
        raise SystemExit(f"{out} exists; pick a new run id")
    ui, universe, labels = load_ui_config(), load_universe(), Labels.load()
    reuse = load_session(RUNS / serving_run, search_models=False)
    if reuse.snapshot is None or not reuse.snapshot.usable:
        raise SystemExit(f"{serving_run}: snapshot not usable: "
                         f"{reuse.snapshot.reason if reuse.snapshot else 'none'}")
    live = load_session(None, search_models=False)
    validation = sorted(t for t, s in universe.items() if s == "validation")
    lines, problems = [], 0

    lines.append("== (1) reconciliations: snapshot vs a fresh live build ==")
    for ticker in validation:
        started = time.perf_counter()
        a = reuse.tools.reconciliation(ticker, reuse.snapshot.as_of)
        reused_s = time.perf_counter() - started
        started = time.perf_counter()
        b = live.tools.reconciliation(ticker, reuse.snapshot.as_of)
        live_s = time.perf_counter() - started
        path = reuse.snapshot.source(ticker, reuse.snapshot.as_of) or ""
        same = comparable(a) == comparable(b)
        diffs, timing = record_differences(a, b)
        ok = same and not diffs and path.startswith(REUSED)
        problems += not ok
        lines.append(f"{ticker:<5} {'EQUAL' if ok else 'DIFFERENT'}  objects {same}  records "
                     f"{not diffs} (measured agent latencies differ: {timing})  path '{path}'  "
                     f"reuse {reused_s:.2f}s  live {live_s:.2f}s  observations "
                     f"{len(a.statement_set.observations)}")
        lines.extend(f"    DIFF {d}" for d in diffs[:10])

    lines.append("")
    lines.append("== (2) answers: reuse path vs live path (the ui-present audit questions) ==")
    questions = audit_questions(ui, universe)
    for q in questions:
        rid = f"{run_id}:{q['id']}"
        runs = [investigate(q["question"], q["as_of"], tools=s.tools, parser=s.parser,
                            resources=s.resources, selected_issuer=q["selected"], run_id=rid)
                for s in (reuse, live)]
        a, b = runs
        diffs = answer_differences(a, b)
        same_object = without_latency(a) == without_latency(b)
        pa, pb = present(a, labels), present(b, labels)
        same_plain = pa == pb and plain_text(pa) == plain_text(pb)
        paths = statement_paths(reuse.tools, a)
        live_paths = [p for p in paths if REUSED not in p]
        ok = not diffs and same_object and same_plain and not live_paths
        problems += not ok
        lines.append(f"{q['id']:<16} {'IDENTICAL' if ok else 'DIFFERENT'}  {a.status:<13} "
                     f"text {a.answer == b.answer}  observations+statuses+gate "
                     f"{not diffs and same_object}  plain {same_plain}  "
                     f"paths {paths or ['none needed']}")
        lines.extend(f"    DIFF {d}" for d in diffs[:10])
    lines.append("")
    lines.append(f"{len(validation)} reconciliations, {len(questions)} answers; "
                 f"{problems} not identical")
    lines.append("IDENTICAL" if problems == 0 else "DIFFERENT")
    out.mkdir(parents=True)
    header = {"run_id": run_id, "serving_run": serving_run, "as_of": str(reuse.snapshot.as_of),
              "git": fingerprint()["git"], "policy": "validation companies only (BC36)"}
    (out / "header.json").write_text(json.dumps(header, indent=1) + "\n", encoding="utf-8")
    (out / "proof.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if problems == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="is the serving run's snapshot usable here?")
    c.add_argument("--serving-run", required=True)
    p = sub.add_parser("prove", help="reuse path == live path (validation companies)")
    p.add_argument("--serving-run", required=True)
    p.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        snap = Snapshot.open(RUNS / args.serving_run)
        print(f"{args.serving_run}: as of {snap.as_of}; "
              f"{'usable' if snap.usable else 'not usable: ' + snap.reason}; "
              f"{len(snap.files)} companies")
        return 0 if snap.usable else 1
    return prove(args.serving_run, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
