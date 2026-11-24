"""E5 gold set: grid, brief, import, format check and freeze (Phase 6 preparation)."""

import json
import re
from collections import Counter
from dataclasses import replace

import pandas as pd
import pytest
from planner_support import held_out_name, resources

from finagent.config import HELD_OUT, ConfigError, HeldOutError, load_universe
from finagent.evaluation.e5_gold import (E5_PATH, GridCell, blank_record, build_grid,
                                         check_frozen, describe, fiscal_years, freeze,
                                         import_brief, load_e5_spec, load_gold, load_grid,
                                         parse_brief, render_brief, require_frozen,
                                         validate_gold, write_gold, write_grid)
from finagent.planner.intent_data import DATASETS_DIR, load_templates

R = resources()
SPEC = load_e5_spec()
UNIVERSE = load_universe()
VALIDATION = sorted(t for t, s in UNIVERSE.items() if s == "validation")
DEC = (2021, 2022, 2023, 2024, 2025)
YEARS = {t: (DEC if i % 3 else tuple(y + 1 for y in DEC)) for i, t in enumerate(VALIDATION)}
DISPLAY = {"AMZN": "Amazon", "MSFT": "Microsoft"}


@pytest.fixture(scope="module")
def grid():
    return build_grid(SPEC, YEARS, 3)


def _validate(records, cells, stage="questions", complete=False):
    return validate_gold(records, cells, stage, R.index, R.vocabulary, require_complete=complete)


def test_spec_loads_and_sizes():
    assert SPEC.files["gold"].name == "intents_test.jsonl"
    assert SPEC.size() == sum(g.canonical.count + g.paraphrase.count + len(g.traps)
                              for g in SPEC.goal_groups) + \
        sum(len(g.canonical) + len(g.vineet) for g in SPEC.clarification_groups)


def test_spec_rejects_unknown_metric(tmp_path):
    text = E5_PATH.read_text(encoding="utf-8").replace("dso, ebit_margin", "dso, horoscope")
    path = tmp_path / "e5.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_e5_spec(path, R.vocabulary)


def test_grid_matches_the_spec(grid):
    assert len(grid) == SPEC.size() and len({c.cell for c in grid}) == len(grid)
    for g in SPEC.goal_groups:
        cells = [c for c in grid if c.group == g.kind]
        assert Counter(c.author for c in cells) == Counter(
            {"canonical": g.canonical.count, "vineet": g.paraphrase.count + len(g.traps)})
        assert sorted(c.variant for c in cells if c.variant) == sorted(g.traps)
    for g in SPEC.clarification_groups:
        cells = [c for c in grid if c.group == g.category]
        assert all(c.expected == "CLARIFICATION" and c.category == g.category for c in cells)
    named = {c.issuer for c in grid} | {c.issuer2 for c in grid} | {c.selected_issuer for c in grid}
    assert named - {""} <= set(VALIDATION)  # validation companies only
    counts = Counter(c.issuer for c in grid if c.issuer)
    assert max(counts.values()) - min(counts.values()) <= 1  # balanced rotation


def test_grid_slots(grid):
    for c in grid:
        latest = YEARS[c.issuer or c.selected_issuer][-1] if (c.issuer or c.selected_issuer) \
            else None
        if c.kind in ("forecast", "liquidity", "debt") and c.target and not c.variant:
            assert latest < int(c.target) <= latest + 3
        if c.variant == "past_target":
            assert int(c.target) <= latest
        if c.variant == "beyond_horizon":
            assert int(c.target) > latest + 3
        if c.kind == "explain" and c.period not in ("", "latest"):
            assert int(c.period) > YEARS[c.issuer][0]  # a prior year exists
        if c.variant == "selected_mismatch":
            assert c.selected_issuer and c.issuer != c.selected_issuer
    assert {c.expected for c in grid if c.variant == "comparison_without_cue"} == {"CLARIFICATION"}


def test_grid_needs_two_years():
    with pytest.raises(ValueError):
        build_grid(SPEC, {"AMZN": (2025,)}, 3)


def test_grid_round_trip(grid, tmp_path):
    path = write_grid(grid, tmp_path / "grid.csv")
    assert load_grid(path) == grid


def test_brief_shows_only_vineet_cells_and_no_template_text(grid):
    brief = render_brief(grid, SPEC, DISPLAY)
    cells = set(re.findall(r"^\| (C\d{3}) \|", brief, re.MULTILINE))
    assert cells == {c.cell for c in grid if c.author == "vineet"}
    lowered = brief.lower()
    for family in load_templates().families:
        for segment in re.split(r"\{\w+\}", family.text):
            words = segment.strip(" ?.,:'").lower()
            if len(words.split()) >= 4:
                assert words not in lowered, family.family_id
    ticker, name = held_out_name()
    assert name not in brief and f"({ticker})" not in brief


def test_describe_plain_words(grid):
    fc = next(c for c in grid if c.kind == "forecast" and c.target and not c.variant)
    ask, details = describe(fc, SPEC, DISPLAY)
    assert ask == SPEC.kinds["forecast"] and f"FY{fc.target}" in details


def _filled(grid, questions):
    brief = render_brief(grid, SPEC, DISPLAY)
    lines = []
    for line in brief.splitlines():
        m = re.match(r"^\| (C\d{3}) \|", line)
        if m and m.group(1) in questions:
            line = line[:-3] + f" {questions[m.group(1)]} |"
        lines.append(line)
    return "\n".join(lines)


def _vineet_fact(grid):
    return next(c for c in grid if c.author == "vineet" and c.kind == "fact" and not c.variant
                and not c.issuer2 and not c.selected_issuer and c.period not in ("", "latest"))


def test_parse_brief(grid):
    cell = _vineet_fact(grid)
    found = parse_brief(_filled(grid, {cell.cell: "What did they book?"}))
    assert found == {cell.cell: "What did they book?"}
    with pytest.raises(ValueError):
        parse_brief("| C001 | a | b | c | d | e |")


def test_import_brief_keeps_labels_and_refuses_once_frozen(grid, tmp_path):
    cell = _vineet_fact(grid)
    brief, gold, hash_file = tmp_path / "brief.md", tmp_path / "gold.jsonl", tmp_path / "h.sha256"
    brief.write_text(_filled(grid, {cell.cell: "Question one"}), encoding="utf-8")
    assert import_brief(brief, grid, gold, hash_file) == 1
    (record,) = load_gold(gold)
    assert record["cell"] == cell.cell and record["author"] == "vineet"
    write_gold([{**record, "notes": "labelled"}], gold)
    import_brief(brief, grid, gold, hash_file)
    assert load_gold(gold)[0]["notes"] == "labelled"  # unchanged question keeps its labels
    canonical = next(c for c in grid if c.author == "canonical")
    brief.write_text(_filled(grid, {}) + f"\n| {canonical.cell} | a | b | c | q |",
                     encoding="utf-8")
    with pytest.raises(ValueError):
        import_brief(brief, grid, gold, hash_file)
    hash_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        import_brief(brief, grid, gold, hash_file)


def test_validate_questions_stage(grid):
    cell = _vineet_fact(grid)
    name = R.index.names.names[cell.issuer][0]
    good = blank_record(cell, f"{name} figure in FY{cell.period}")
    assert _validate([good], grid) == []
    assert any("does not name" in e for e in _validate([{**good, "question": "a figure"}], grid))
    assert any("author" in e for e in _validate([{**good, "author": "canonical"}], grid))
    train = next(t for t, s in UNIVERSE.items() if s == "train")
    other = R.index.names.names[train][0]
    assert any("not a validation company" in e
               for e in _validate([{**good, "question": f"{name} and {other}"}], grid))
    ticker, held = held_out_name()
    assert any(ticker in e for e in _validate([{**good, "question": f"{name} vs {held}"}], grid))
    assert any("duplicate" in e for e in _validate([good, good], grid))
    assert any("no record" in e for e in _validate([good], grid, complete=True))


def test_validate_labelled_stage(grid):
    cell = _vineet_fact(grid)
    name = R.index.names.names[cell.issuer][0]
    goal = {"issuer": cell.issuer, "period": int(cell.period), "kind": "fact",
            "metric": cell.metric, "scenario": "", "target": None, "conditional": False}
    record = {**blank_record(cell, f"{name} figure in FY{cell.period}"), "expected": "GOALS",
              "goals": [goal], "end_to_end": True}
    assert _validate([record], grid, "labelled") == []
    bad_goal = {**goal, "issuer": "ZZZZ", "metric": "horoscope"}
    errors = _validate([{**record, "goals": [bad_goal]}], grid, "labelled")
    assert any("validation company" in e for e in errors) and any("metric" in e for e in errors)
    assert _validate([{**record, "expected": "CLARIFICATION"}], grid, "labelled")
    assert _validate([{**record, "end_to_end": None}], grid, "labelled")


def test_freeze_and_check(tmp_path):
    gold, grid_file, hash_file = (tmp_path / n for n in ("g.jsonl", "grid.csv", "g.sha256"))
    gold.write_text("{}\n", encoding="utf-8")
    grid_file.write_text("cell\n", encoding="utf-8")
    with pytest.raises(ValueError):
        freeze(gold, grid_file, hash_file, ["an error"])
    freeze(gold, grid_file, hash_file, [])
    assert check_frozen(hash_file)
    require_frozen(hash_file)
    with pytest.raises(ValueError):
        freeze(gold, grid_file, hash_file, [])  # once only
    gold.write_text("{} \n", encoding="utf-8")
    assert not check_frozen(hash_file)
    with pytest.raises(ValueError):
        require_frozen(hash_file)
    assert not check_frozen(tmp_path / "missing.sha256")


def test_fiscal_years_refuses_held_out_before_reading():
    ticker, _ = held_out_name()
    with pytest.raises(HeldOutError):
        fiscal_years([ticker], SPEC.as_of)


def test_fiscal_years_of_a_validation_company():
    (years,) = fiscal_years(["AMZN"], SPEC.as_of).values()
    assert years == tuple(range(years[0], years[0] + len(years))) and len(years) >= 2


def test_gold_questions_are_not_in_the_training_data():
    gold = SPEC.files["gold"]
    dataset = DATASETS_DIR / "intents_v1.parquet"
    if not gold.exists() or not dataset.exists():
        pytest.skip("gold set or training data not present")
    trained = {q.lower() for q in pd.read_parquet(dataset)["question"]}
    assert not [r["question"] for r in load_gold(gold) if r["question"].lower() in trained]


def test_committed_grid_matches_the_spec():
    if not SPEC.files["grid"].exists():
        pytest.skip("grid not built")
    cells = load_grid(SPEC.files["grid"])
    assert len(cells) == SPEC.size()
    assert {c.issuer for c in cells} - {""} <= set(VALIDATION)
    assert not {c.issuer for c in cells} & {t for t, s in UNIVERSE.items() if s == HELD_OUT}


def test_gridcell_expected():
    cell = GridCell("C1", "fact", "fact", "", "vineet", "paraphrase", "", "AMZN", "", "", "revenue",
                    "2024", "", "", False)
    assert cell.expected == "GOALS" and replace(cell, category="advice").expected == \
        "CLARIFICATION"


def test_records_serialise_sorted(tmp_path):
    path = write_gold([{"cell": "C002", "q": 1}, {"cell": "C001", "q": 2}], tmp_path / "g.jsonl")
    assert [json.loads(l)["cell"] for l in path.read_text().splitlines()] == ["C001", "C002"]
