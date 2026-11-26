"""Blueprint Version 3 ("as built") checks (Phase 10).

1. Every BC in docs/blueprint_changes.md that is not rejected or superseded is cited in
   docs/blueprint.md.
2. Every run id cited in docs/blueprint.md exists in eval/experiments.csv or under runs/.
3. Every number shared with docs/final_results.md matches: on a line of docs/blueprint.md that
   names an evaluation run id (e1-0001 ... e8-NNNN) or docs/final_results.md, every decimal,
   percentage and fraction appears verbatim in docs/final_results.md (identifiers masked as in
   tests/test_final_results.py).
4. Every Version 2 heading (docs/blueprint_v2.md) is still in Version 3, in the same order, so
   references to a section number keep pointing to the right place.
5. Every "Not built" note (a line starting with **Not built**) names an issue, a BC or
   "no issue logged".

    python tests/test_blueprint_v3.py      # prints each check's result
"""

import csv
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
V3, V2 = DOCS / "blueprint.md", DOCS / "blueprint_v2.md"

_spec = importlib.util.spec_from_file_location("final_results_check",
                                               Path(__file__).with_name("test_final_results.py"))
fr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fr)  # the masks and matching rules of the final_results.md check

RUN_ID = re.compile(r"(?<![\w-])((?:[a-z][a-z0-9]*-)+\d{4})(?:/[a-z]+)?(?![\w-])")
EVAL_RUN = re.compile(r"\be[1-8]-\d{4}\b")
MEASURE = re.compile(r"(?<![\d.])\d+/\d+(?![\d.])|(?<![\w.])\d+\.\d+(?:e-?\d+)?|(?<![\w.])\d+(?=%)")


def bc_ids(changes: Path = DOCS / "blueprint_changes.md") -> list[str]:
    """BC ids in the change table whose status (second-to-last cell) is not Rejected or
    Superseded. Reading stops at the next heading, so the Version 3 map table is not read."""
    ids = []
    for line in changes.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and ids:
            break
        if line.startswith("| BC"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not re.search(r"Rejected|Superseded", cells[-2]):
                ids.append(cells[0])
    return ids


def missing_bcs(text: str, ids: list[str]) -> list[str]:
    return [i for i in ids if not re.search(rf"\b{i}\b", text)]


def known_runs(root: Path = ROOT) -> set[str]:
    with open(root / "eval" / "experiments.csv", encoding="utf-8", newline="") as f:
        runs = {r["run_id"].split("/")[0] for r in csv.DictReader(f)}
    return runs | {p.name.split(".")[0] for p in (root / "runs").iterdir()}


def unknown_runs(text: str, runs: set[str]) -> list[str]:
    return sorted({m for m in RUN_ID.findall(text) if m not in runs})


def measures(line: str) -> list[str]:
    masked = line
    for pattern in fr.MASKS:
        masked = re.sub(pattern, " ", masked)
    return MEASURE.findall(fr.normalise(masked))


def unmatched_numbers(text: str, results: str) -> list[str]:
    results = fr.normalise(results)
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("#") or not (EVAL_RUN.search(line) or "final_results.md" in line):
            continue
        problems += [f"line {n}: {t}" for t in measures(line) if not fr.appears(t, results)]
    return problems


def headings(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if re.match(r"#{1,4} ", l)]


def lost_headings(v2: str, v3: str) -> list[str]:
    """Version 2 headings missing from Version 3 or out of order (Version 3 may add more)."""
    new, i, lost = headings(v3), 0, []
    for h in headings(v2):
        try:
            i = new.index(h, i) + 1
        except ValueError:
            lost.append(h)
    return lost


def bad_not_built(text: str) -> list[str]:
    """Not built notes (lines that start with the marker) that name no issue, BC or 'no issue logged'."""
    return [l[:80] for l in text.splitlines() if l.startswith("**Not built**")
            and not re.search(r"#\d+|BC\d+|no issue logged", l)]


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_bc_is_folded_in():
    assert not missing_bcs(text(V3), bc_ids())


@pytest.mark.skipif(not (ROOT / "runs").is_dir(), reason="runs/ is git-ignored and absent here")
def test_every_cited_run_exists():
    assert not unknown_runs(text(V3), known_runs())


def test_numbers_shared_with_final_results_match():
    assert not unmatched_numbers(text(V3), text(DOCS / "final_results.md"))


def test_version_2_headings_are_kept_in_order():
    assert not lost_headings(text(V2), text(V3))


def test_not_built_notes_name_their_record():
    assert not bad_not_built(text(V3))


def test_the_checks_catch_problems():
    assert missing_bcs("folded (BC1) and BC12.", ["BC1", "BC2", "BC12"]) == ["BC2"]
    assert unknown_runs("e1-0009, statements-0009 and e2-0001/clean; e9-0001", {
        "e1-0009", "statements-0009", "e2-0001"}) == ["e9-0001"]
    results = "M1 v3 0.913 [0.846, 0.961]; 29/52 = 55.8%"
    doc = ("measured 0.913 [0.846, 0.961] (e1-0009; docs/final_results.md)\n"
           "measured 0.914 (e1-0009)\n"
           "threshold 0.875 in configs/planner.yaml, not a result line\n"
           "29/52 = 55.8% and 56% (docs/final_results.md)\n"
           "# 10.1 E1 heading 0.5 (e1-0009)\n")
    assert unmatched_numbers(doc, results) == ["line 2: 0.914", "line 4: 56"]
    v2 = "# 1. A\n## 1.1 B\n# 2. C\n"
    assert lost_headings(v2, "# 1. A\n## 1.1 B\n### new\n# 2. C\n") == []
    assert lost_headings(v2, "# 1. A\n# 2. C\n## 1.1 B\n") == ["# 2. C"]  # out of order
    assert lost_headings(v2, "# 1. A\n# 2. C\n") == ["## 1.1 B"]  # removed
    assert bad_not_built("**Not built** (#316, BC33): x.\n**Not built**: y.\n"
                         "| **Not built** | the glossary's definition |\n") == ["**Not built**: y."]


if __name__ == "__main__":
    v3 = text(V3)
    found = {
        "BCs not cited": missing_bcs(v3, bc_ids()),
        "run ids not found": unknown_runs(v3, known_runs()),
        "numbers not in final_results.md": unmatched_numbers(v3, text(DOCS / "final_results.md")),
        "Version 2 headings lost or reordered": lost_headings(text(V2), v3),
        "Not built notes without a record": bad_not_built(v3),
    }
    shared = sum(len(measures(l)) for l in v3.splitlines()
                 if not l.startswith("#") and (EVAL_RUN.search(l) or "final_results.md" in l))
    print(f"BCs checked: {len(bc_ids())}; run ids cited: {len(set(RUN_ID.findall(v3)))}; "
          f"numbers shared with final_results.md: {shared}; Version 2 headings: "
          f"{len(headings(text(V2)))}")
    for name, items in found.items():
        print(f"{name}: {len(items)}" + "".join(f"\n  {i}" for i in items))
    raise SystemExit(1 if any(found.values()) else 0)
