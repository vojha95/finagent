"""Tests for finagent/ui/snapshot.py and the FinAgentTools snapshot hook (BC37, proposed): the
fingerprint, the pickles and their index, when the snapshot is used and when the tools build
live, the trace note, and the answer comparison. Synthetic data only: no company is built."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from finagent.config import HeldOutError
from finagent.records import Compilation, TraceStep
from finagent.statements.replay import FULL
from finagent.ui import snapshot as sn

AS_OF = date(2026, 11, 24)


@dataclass(frozen=True)
class FakeSet:
    issuer: str
    as_of: date
    filings: tuple = ()


@dataclass(frozen=True)
class FakeRec:  # pickled at module level, like a Reconciliation
    issuer: str
    as_of: date
    statement_set: FakeSet
    checks: dict = field(default_factory=dict)


def rec(issuer: str = "AAA", as_of: date = AS_OF) -> FakeRec:
    return FakeRec(issuer, as_of, FakeSet(issuer, as_of), {2025: "ok"})


def fake_root(tmp_path: Path) -> Path:
    for name, text in {"finagent/a.py": "x = 1\n", "finagent/sub/b.py": "y = 2\n",
                       "finagent/ui/app.py": "page = 1\n",
                       "finagent/__pycache__/a.cpython-311.pyc": "junk",
                       "configs/a.yaml": "k: 1\n", "configs/ui.yaml": "app: 1\n",
                       "configs/.DS_Store": "finder", "models/m1_v3.joblib": "model",
                       "models/m1_v3.meta.json": "{}", "datasets/rows_v3.meta.json": "{}",
                       "evidence/doc.htm.gz": "doc"}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "evidence" / "manifest.json").write_text(json.dumps(
        {"doc.htm": {"file": "doc.htm.gz", "sha256": "abc"}}))
    return tmp_path


# ---------------------------------------------------------------- fingerprint

def test_fingerprint_covers_code_configs_models_evidence_runtime_packages(tmp_path):
    fp = sn.fingerprint(fake_root(tmp_path))
    assert set(fp) == set(sn.COMPONENTS) | {"git"}
    assert set(fp["code"]) == {"a.py", "sub/b.py"}  # no __pycache__, no ui/ (#369)
    assert set(fp["configs"]) == {"a.yaml"}  # ui.yaml (app only) and hidden files left out
    assert set(fp["models"]) == set(sn.model_files("v3"))
    assert set(fp["evidence"]) == {"manifest.json", "doc.htm.gz"}
    assert {"python", "platform", "machine"} <= set(fp["runtime"])
    assert fp["packages"].get("pytest")


def test_any_change_that_decides_a_reconciliation_changes_the_fingerprint(tmp_path):
    root = fake_root(tmp_path)
    before = sn.fingerprint(root)
    (root / "configs" / "ui.yaml").write_text("app: 2\n")  # app wording: not compared
    (root / "finagent" / "ui" / "app.py").write_text("page = 2\n")  # presentation (#369)
    assert sn.mismatches(before, sn.fingerprint(root)) == []
    for name in ("finagent/sub/b.py", "configs/a.yaml", "models/m1_v3.joblib",
                 "evidence/doc.htm.gz"):
        (root / name).write_text("changed")
        assert sn.mismatches(before, sn.fingerprint(root)), name
        before = sn.fingerprint(root)


def test_mismatches_name_the_changed_entries_and_ignore_git():
    a = {"code": {"x.py": "1", "y.py": "1"}, "runtime": {"python": "3.11"}, "packages": {},
         "git": {"commit": "a"}}
    b = {"code": {"x.py": "2", "y.py": "1", "z.py": "1"}, "runtime": {"python": "3.12"},
         "packages": {}, "git": {"commit": "b"}}
    assert sn.mismatches(a, b) == ["code differ (x.py, z.py)", "runtime differ (python)"]
    # An index recorded before #369 lists finagent/ui/**: it is compared by the same rule.
    old = {"code": {"x.py": "1", "ui/app.py": "1", "ui/snapshot.py": "1"}}
    assert sn.mismatches(old, {"code": {"x.py": "1"}}) == []
    assert sn.mismatches(old, {"code": {"x.py": "2"}}) == ["code differ (x.py)"]
    many = {"code": {f"{i}.py": str(i) for i in range(5)}}
    assert sn.mismatches(many, {"code": {}}) == [
        "code differ (0.py, 1.py, 2.py and 2 more)"]


# ---------------------------------------------------------------- writer and reader

def written(tmp_path: Path, fp: dict, end: dict | None = None) -> Path:
    run = tmp_path / "serving-9999"
    ask = run / sn.ASK
    ask.mkdir(parents=True)
    files = {t: sn.write_reconciliation(ask, rec(t)) for t in ("AAA", "BBB")}
    sn.write_index(ask, "serving-9999", AS_OF, files, fp, end if end is not None else fp)
    return run


FP = {"code": {"a.py": "1"}, "configs": {}, "models": {}, "evidence": {}, "runtime": {},
      "packages": {}, "git": {"commit": "x", "dirty": True}}


def test_snapshot_reused_when_everything_matches(tmp_path):
    run = written(tmp_path, FP)
    snap = sn.Snapshot.open(run, current={**FP, "git": {"commit": "y", "dirty": False}})
    assert snap.usable and snap.reason == "" and snap.as_of == AS_OF
    assert snap.reconciliation("AAA", AS_OF, FULL) == rec("AAA")
    assert snap.source("AAA", AS_OF) == "reused from serving-9999"
    index = json.loads((run / sn.ASK / sn.INDEX).read_text())
    assert index["changed_during_build"] == [] and set(index["files"]) == {"AAA", "BBB"}


def test_another_as_of_builds_live(tmp_path):
    snap = sn.Snapshot.open(written(tmp_path, FP), current=FP)
    assert snap.reconciliation("AAA", date(2026, 11, 23), FULL) is None
    assert snap.source("AAA", date(2026, 11, 23)) == (
        "built live: as of 2026-11-23 is not serving-9999's as of 2026-11-24")


def test_a_hidden_channel_view_builds_live(tmp_path):
    snap = sn.Snapshot.open(written(tmp_path, FP), current=FP)
    assert snap.reconciliation("AAA", AS_OF, object()) is None
    assert snap.source("AAA", AS_OF) == "built live: the evidence view is not FULL"


def test_a_different_build_builds_live_and_says_why(tmp_path):
    snap = sn.Snapshot.open(written(tmp_path, FP), current={**FP, "code": {"a.py": "2"}})
    assert not snap.usable and snap.reason == "code differ (a.py)"
    assert snap.reconciliation("AAA", AS_OF, FULL) is None
    assert "does not match the current build (code differ (a.py))" in snap.source("AAA", AS_OF)


def test_a_change_during_the_build_makes_it_unusable(tmp_path):
    run = written(tmp_path, FP, end={**FP, "configs": {"a.yaml": "2"}})
    snap = sn.Snapshot.open(run, current=FP)
    assert not snap.usable and "changed during the build: configs differ" in snap.reason


def test_a_changed_pickle_or_a_missing_company_builds_live(tmp_path):
    run = written(tmp_path, FP)
    (run / sn.ASK / "BBB.pkl").write_bytes(b"tampered")
    snap = sn.Snapshot.open(run, current=FP)
    assert snap.reconciliation("BBB", AS_OF, FULL) is None
    assert snap.source("BBB", AS_OF) == "built live: BBB.pkl differs from its recorded sha256"
    assert snap.reconciliation("CCC", AS_OF, FULL) is None
    assert snap.source("CCC", AS_OF) == "built live: CCC is not in serving-9999"


def test_no_snapshot_in_the_run(tmp_path):
    (tmp_path / "serving-0001").mkdir()
    snap = sn.Snapshot.open(tmp_path / "serving-0001", current=FP)
    assert not snap.usable and snap.reason == "serving-0001 has no ask/snapshot.json"
    assert snap.reconciliation("AAA", AS_OF, FULL) is None


# ---------------------------------------------------------------- the tools hook

class Recorder:
    def __init__(self, give):
        self.give, self.asked = give, []

    def reconciliation(self, issuer, as_of, view):
        self.asked.append((issuer, as_of, view))
        return self.give


def tools(monkeypatch, snapshot):
    from finagent import tools as t

    built = []
    monkeypatch.setattr(t, "reconcile_company", lambda *a: built.append(a[1]) or rec(a[1], a[2]))
    return t.FinAgentTools(store=SimpleNamespace(), classifier=None,
                           universe={"AAA": "validation", "HHH": "held_out"},
                           snapshot=snapshot), built


def test_tools_use_the_snapshot_on_a_cache_miss_only(monkeypatch):
    snap = Recorder(rec("AAA"))
    t, built = tools(monkeypatch, snap)
    assert t.reconciliation("AAA", AS_OF) == rec("AAA")
    assert t.reconciliation("AAA", AS_OF) == rec("AAA")
    assert snap.asked == [("AAA", AS_OF, FULL)] and built == []


def test_tools_build_live_when_the_snapshot_declines_or_is_absent(monkeypatch):
    t, built = tools(monkeypatch, Recorder(None))
    assert t.reconciliation("AAA", AS_OF) == rec("AAA") and built == ["AAA"]
    t, built = tools(monkeypatch, None)  # the default: the live path, as before
    assert t.snapshot is None
    assert t.reconciliation("AAA", AS_OF) == rec("AAA") and built == ["AAA"]


def test_the_held_out_guard_runs_before_the_snapshot(monkeypatch):
    snap = Recorder(rec("HHH"))
    t, built = tools(monkeypatch, snap)
    with pytest.raises(HeldOutError):
        t.reconciliation("HHH", AS_OF)
    assert snap.asked == [] and built == []


def test_the_point_in_time_check_still_runs_on_a_reused_set(monkeypatch):
    from finagent.model.history import PointInTimeError

    t, _ = tools(monkeypatch, Recorder(rec("AAA", date(2026, 11, 25))))
    with pytest.raises(PointInTimeError):
        t.reconciliation("AAA", AS_OF)


# ---------------------------------------------------------------- trace note and comparison

def inv(*called, as_of=AS_OF):
    trace = tuple(SimpleNamespace(arguments={"issuer": i, "period": 2025}) for i in called)
    return SimpleNamespace(trace=trace, as_of=as_of)


def test_statement_paths_name_each_issuer_used():
    snap = sn.Snapshot("serving-9999", Path("."), AS_OF, True, "",
                       sources={("AAA", AS_OF): "reused from serving-9999",
                                ("BBB", AS_OF): "built live: BBB is not in serving-9999"})
    t = SimpleNamespace(snapshot=snap, reconciliations={("AAA", AS_OF, FULL): 1,
                                                        ("BBB", AS_OF, FULL): 1,
                                                        ("CCC", date(2025, 1, 1), FULL): 1})
    assert sn.statement_paths(t, inv("BBB", "AAA", "BBB", "CCC")) == [
        "BBB: built live: BBB is not in serving-9999", "AAA: reused from serving-9999"]
    assert sn.statement_paths(t, inv()) == []  # a clarification calls no tool, even if cached
    bare = SimpleNamespace(snapshot=None, reconciliations={("AAA", AS_OF, FULL): 1})
    assert sn.statement_paths(bare, inv("AAA")) == [
        "AAA: built live: no serving run snapshot configured"]


def investigation(latency: float, answer: str = "AAA FY2025 revenue: 1"):
    from finagent.planner.investigate import Investigation

    step = TraceStep(1, "reported_verified", {"issuer": "AAA"}, ("reported_verified",),
                     "start", "VERIFIED", latency_ms=latency)
    return Investigation("r1", "q", AS_OF, Compilation("q", "m1"), "COMPLETE",
                         "goals_resolved", (), (step,), answer, (), 8, 0)


def test_answer_comparison_ignores_latency_only():
    a, b = investigation(12.5), investigation(99.0)
    assert sn.without_latency(a) == sn.without_latency(b)
    assert sn.answer_differences(a, b) == []
    diffs = sn.answer_differences(a, investigation(12.5, "AAA FY2025 revenue: 2"))
    assert diffs == [".run.answer"]


def test_comparable_ignores_only_the_agent_trace_latency():
    def step(ms: float, status: str = "FAIL") -> TraceStep:
        return TraceStep(1, "edit", {}, (), "d", status, latency_ms=ms)

    @dataclass(frozen=True)
    class Result:
        trace: tuple

    @dataclass(frozen=True)
    class Rec:
        results: tuple
        value: int = 1

    a = Rec((Result((step(1.5), step(2.0))),))
    b = Rec((Result((step(9.0), step(0.1))),))
    assert a != b and sn.comparable(a) == sn.comparable(b)
    assert sn.comparable(a) != sn.comparable(Rec((Result((step(1.5, "PASS"), step(2.0))),)))
    assert sn.comparable(a) != sn.comparable(Rec(a.results, value=2))


# ---------------------------------------------------------------- the real files, in a temp copy

REPO = Path(__file__).resolve().parents[1]


def repo_copy(tmp_path: Path) -> Path:
    """What the fingerprint reads, from this repository: finagent/ and configs/ copied, the
    model files copied (a placeholder where a git-ignored model is absent), the evidence linked
    read-only (never written)."""
    root = tmp_path / "repo"
    for name in ("finagent", "configs"):
        shutil.copytree(REPO / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    for name in sn.model_files("v3"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        if (REPO / name).is_file():
            shutil.copyfile(REPO / name, root / name)
        else:
            (root / name).write_text("placeholder")
    (root / "evidence").symlink_to(REPO / "evidence", target_is_directory=True)
    return root


@pytest.mark.parametrize("changed, reason", [
    ("configs/derivations.yaml", "configs differ (derivations.yaml)"),
    ("finagent/statements/build.py", "code differ (statements/build.py)"),
])
def test_reuse_at_the_default_as_of_until_a_hashed_file_changes(tmp_path, monkeypatch, changed,
                                                                reason):
    from finagent.ui.data import load_ui_config

    root = repo_copy(tmp_path)
    as_of = load_ui_config().as_of_default
    run = tmp_path / "runs" / "serving-9998"
    (run / sn.ASK).mkdir(parents=True)
    built = sn.fingerprint(root)
    assert "derivations.yaml" in built["configs"] and "tools.py" in built["code"]
    assert "statements/build.py" in built["code"] and not any(
        k.startswith("ui/") for k in built["code"])  # #369
    files = {"AAA": sn.write_reconciliation(run / sn.ASK, rec("AAA", as_of))}
    sn.write_index(run / sn.ASK, "serving-9998", as_of, files, built, built)

    # Nothing that decides a reconciliation changed: a docs-only change, app wording in
    # ui.yaml, presentation code under finagent/ui (#369) and a new commit (git is recorded,
    # never compared). The snapshot is reused.
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("a docs-only change")
    (root / "configs" / "ui.yaml").write_text("tagline: changed app wording\n")
    for name in ("app.py", "data.py", "present.py", "snapshot.py", "serving.py"):
        ui = root / "finagent" / "ui" / name
        ui.write_text(ui.read_text() + "\n# a presentation change\n")
    now = {**sn.fingerprint(root), "git": {"commit": "a-new-commit", "dirty": False}}
    t, live = tools(monkeypatch, sn.Snapshot.open(run, current=now))
    assert t.reconciliation("AAA", as_of) == rec("AAA", as_of) and live == []
    assert t.snapshot.source("AAA", as_of) == "reused from serving-9998"

    # One hashed file changed in the copy: the tools build live and the trace names the file.
    path = root / changed
    path.write_text(path.read_text() + "\n# changed\n")
    t, live = tools(monkeypatch, sn.Snapshot.open(run, current=sn.fingerprint(root)))
    assert t.reconciliation("AAA", as_of) == rec("AAA", as_of) and live == ["AAA"]
    assert t.snapshot.source("AAA", as_of) == (
        f"built live: serving-9998 does not match the current build ({reason})")
