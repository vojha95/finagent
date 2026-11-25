"""E3 labelling sheet (finagent.evaluation.e3): selection rule, pool, sheet, import, validate.
Synthetic data only: no retrieval, no evidence store, no test-company filing."""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from finagent.evaluation import e3
from finagent.evaluation.e3 import (EXPLAIN, HEADER, SIGNAL, E3Query, E3Spec, Example,
                                    KeyRow, PoolRow, SheetRow)

ORDER = ["R1", "R2", "R3", "R4", "R5"]


def spec(tmp_path=None, **over) -> E3Spec:
    base = tmp_path or __import__("pathlib").Path("/nowhere")
    files = {k: base / f"{k}.x" for k in ("queries", "pool", "pool_key", "labels", "doc")}
    files["sheet"] = base / "sheet.xlsx"
    s = E3Spec(date(2026, 11, 24), "insights-x", files, 2, 1,
               ("revenue", "operating_income", "operating_cash_flow"),
               ("bm25", "dense"), 2, 7, (2, 10),
               {2: "explains the movement", 1: "partly explains it", 0: "does not explain it"},
               30, ())
    return replace(s, **over)


def query(qid="E3-Q01", issuer="AAA", kind=SIGNAL) -> E3Query:
    return E3Query(qid, issuer, 2025, kind, "R1", f"{qid} moved.", "q text", ("ITEM_7",),
                   "acc-1")


def chunk(cid: str, text: str = "", accession: str = "acc-1"):
    return SimpleNamespace(chunk_id=cid, section_id="ITEM_7", words=len(text.split()) or 3,
                           sha256="h" + cid, text=text or f"text of {cid}", accession=accession)


def hit(cid: str, rank: int, score: float = 1.0, accession: str = "acc-1"):
    return SimpleNamespace(chunk=chunk(cid, accession=accession), rank=rank, score=score)


# ---------------------------------------------------------------- spec

def test_repo_spec_is_the_designed_one():
    s = e3.load_e3_spec()
    assert s.variants == ("bm25", "dense", "hybrid_rrf", "hybrid_rrf_rerank")
    assert s.depth == 5 and (s.signal, s.explain) == (3, 2) and s.seconds_per_row == 30
    assert set(s.labels) == {0, 1, 2}
    from finagent.retrieval.report import DEV
    assert len(s.examples) == 2 and all(x.issuer in DEV for x in s.examples)


def test_spec_refuses_unknown_variant_and_labels(tmp_path):
    good = (e3.E3_PATH).read_text()
    bad = tmp_path / "e3.yaml"
    bad.write_text(good.replace("hybrid_rrf_rerank]", "colbert]"))
    with pytest.raises(e3.ConfigError, match="unknown variants"):
        e3.load_e3_spec(bad)
    bad.write_text(good.replace("  0: does not explain it\n", ""))
    with pytest.raises(e3.ConfigError, match="labels"):
        e3.load_e3_spec(bad)


# ---------------------------------------------------------------- selection rule

def test_test_companies_are_validation_without_dev():
    universe = {"A": "train", "B": "validation", "D": "validation", "C": "validation",
                "H": "held_out"}
    assert e3.test_companies(universe, ("D",)) == ["B", "C"]


def test_fired_candidates_keeps_fired_history_signals_of_the_companies():
    rows = [{"ticker": "A", "fiscal_year": "FY2024", "signal": "R1", "state": "FIRED"},
            {"ticker": "A", "fiscal_year": "FY2024", "signal": "R1", "state": "FIRED"},
            {"ticker": "A", "fiscal_year": "FY2023", "signal": "R2", "state": "NOT_FIRED"},
            {"ticker": "A", "fiscal_year": "FY2025", "signal": "R10", "state": "FIRED"},
            {"ticker": "Z", "fiscal_year": "FY2025", "signal": "R1", "state": "FIRED"}]
    assert e3.fired_candidates(rows, ["A", "B"], ["R10"]) == {"A": [(2024, "R1")], "B": []}


def test_select_signals_spreads_signals_then_years_then_latest_then_order():
    candidates = {"A": [(2023, "R1"), (2024, "R1"), (2024, "R2"), (2022, "R3")],
                  "B": [(2024, "R1"), (2024, "R2"), (2023, "R4")]}
    picks = e3.select_signals(candidates, ["A", "B"], {"A": 2, "B": 2}, ORDER)
    # round 1: A takes the latest year, then signal order (2024 R1); B avoids R1 (2024 R2)
    # round 2: A avoids R1/R2 and its used year (2022 R3); B: R4 unused (2023 R4)
    assert picks == [("A", 2024, "R1"), ("B", 2024, "R2"), ("A", 2022, "R3"),
                     ("B", 2023, "R4")]


def test_select_signals_extra_need_and_short_candidates():
    candidates = {"A": [(2024, "R1"), (2023, "R2"), (2022, "R3")], "B": [(2024, "R1")]}
    picks = e3.select_signals(candidates, ["A", "B"], {"A": 3, "B": 2}, ORDER)
    assert [p for p in picks if p[0] == "A"] == [("A", 2024, "R1"), ("A", 2023, "R2"),
                                                ("A", 2022, "R3")]
    assert [p for p in picks if p[0] == "B"] == [("B", 2024, "R1")]  # only one candidate


def test_select_explains_rotates_and_replaces_unsearchable():
    metrics = ("revenue", "operating_income", "operating_cash_flow")
    gaps = {("B", "revenue"), ("C", "revenue"), ("C", "operating_income"),
            ("C", "operating_cash_flow")}
    pairs, short = e3.select_explains(["A", "B", "C", "D"], metrics, 2,
                                      lambda c, m: (c, m) not in gaps)
    assert pairs == [("A", "operating_income"), ("A", "operating_cash_flow"),
                     ("B", "operating_cash_flow"), ("B", "operating_income"),
                     ("D", "operating_income"), ("D", "operating_cash_flow")]
    assert short == {"A": 0, "B": 0, "C": 2, "D": 0}


# ---------------------------------------------------------------- statements

def test_signal_statement_strips_the_evidence_line(monkeypatch):
    import finagent.risk.insights as insights

    monkeypatch.setattr(insights, "card_text", lambda r, s, label="": "R1 X (FY2025): a."
                        + e3.EVIDENCE_TAIL)
    assert e3.signal_statement(object()) == "R1 X (FY2025): a."
    monkeypatch.setattr(insights, "card_text", lambda r, s, label="": "R1 X: a. Other.")
    with pytest.raises(ValueError, match="unexpected card text"):
        e3.signal_statement(object())


def test_explain_statement_numbers_year_and_change():
    assert e3.explain_statement("operating_cash_flow", 2025, Decimal("900000000"),
                                Decimal("1000000000")) == (
        "Operating cash flow decreased from USD 1,000.0m in FY2024 to USD 900.0m in FY2025 "
        "(-10.0%).")
    up = e3.explain_statement("revenue", 2025, Decimal("5000000"), Decimal("0"))
    assert up == "Revenue increased from USD 0.0m in FY2024 to USD 5.0m in FY2025."


# ---------------------------------------------------------------- pool

def test_merge_pool_dedupes_keeps_every_finding_and_shuffles_by_seed():
    ranked = {"bm25": [hit("c1", 1, 9.0), hit("c2", 2, 8.0)],
              "dense": [hit("c2", 1, 0.9), hit("c3", 2, 0.8)]}
    rows, keys = e3.merge_pool("Q1", ranked, 7)
    assert sorted(r.chunk_id for r in rows) == ["c1", "c2", "c3"]
    assert [r.position for r in rows] == [1, 2, 3]
    assert {k.chunk_id: k.found for k in keys}["c2"] == (("bm25", 2, 8.0), ("dense", 1, 0.9))
    again, _ = e3.merge_pool("Q1", dict(reversed(list(ranked.items()))), 7)
    assert [r.chunk_id for r in again] == [r.chunk_id for r in rows]  # seed only
    assert not hasattr(rows[0], "found") and "bm25" not in json.dumps(rows[0].__dict__)


def test_build_pool_cuts_at_depth_and_checks_the_filing():
    class Fake:
        def __init__(self, accession="acc-1"):
            self.calls, self.accession = [], accession

        def search(self, issuer, year, q, *, as_of, sections, k, variant):
            self.calls.append((variant, k, sections, as_of))
            return tuple(hit(f"{variant}{n}", n, accession=self.accession)
                         for n in range(1, 4))

    s = spec()
    fake = Fake()
    rows, keys = e3.build_pool(fake, [query()], s)
    assert len(rows) == 4 and len(keys) == 4  # 2 variants x depth 2
    assert fake.calls == [("bm25", 2, ("ITEM_7",), s.as_of), ("dense", 2, ("ITEM_7",), s.as_of)]
    with pytest.raises(ValueError, match="not from acc-1"):
        e3.build_pool(Fake("acc-2"), [query()], s)


def test_pool_retriever_reads_the_rerank_list_to_depth():
    from finagent.config import load_retrieval_config

    r = e3.pool_retriever(None, {}, 5)
    assert r.config.search.rerank_keep == 5
    assert load_retrieval_config().search.rerank_keep == 3  # the product keeps 3


def test_jsonl_round_trip_and_sizes(tmp_path):
    q = query()
    e3.write_jsonl(tmp_path / "q.jsonl", [q])
    assert e3.load_queries(tmp_path / "q.jsonl") == [q]
    rows = [PoolRow("Q1", 1, "c1", "ITEM_7", 3, "h", "t"), PoolRow("Q1", 2, "c2", "ITEM_7", 3,
                                                                    "h", "t")]
    e3.write_jsonl(tmp_path / "p.jsonl", rows)
    assert e3.load_pool(tmp_path / "p.jsonl") == rows
    assert e3.pool_sizes(rows) == {"Q1": 2}
    assert e3.read_jsonl(tmp_path / "p.jsonl")[0]["chunk_id"] == "c1"
    assert len(e3.file_sha256(tmp_path / "p.jsonl")) == 64
    e3.write_jsonl(tmp_path / "k.jsonl", [KeyRow("Q1", "c1", (("bm25", 1, 2.0),))])
    assert e3.read_jsonl(tmp_path / "k.jsonl")[0]["found"] == [["bm25", 1, 2.0]]


# ---------------------------------------------------------------- sheet, validate, import

def synthetic(tmp_path):
    queries = [query("E3-Q01"), query("E3-Q02", "BBB", EXPLAIN)]
    pool = [PoolRow(q.query_id, n, f"{q.query_id}:c{n}", "ITEM_7", 5, "h",
                    f"=passage {n} of {q.query_id}" if n == 1 else f"passage {n}")
            for q in queries for n in (1, 2, 3)]
    ex = Example("AMZN", 2025, SIGNAL, "R3", "c", 2, "because")
    path = e3.write_sheet(tmp_path / "s.xlsx", queries, pool, spec(),
                          [(ex, "AMZN R3 moved.", "the AMZN passage")])
    return queries, pool, path


def fill(path, labels: dict[int, object], better: dict[int, str] | None = None):
    wb = load_workbook(path)
    ws = wb[e3.LABEL_SHEET]
    for row, value in labels.items():
        ws.cell(row, HEADER.index("label") + 1, value)
    for row, text in (better or {}).items():
        ws.cell(row, HEADER.index("better_passage") + 1, text)
    wb.save(path)


def test_write_sheet_layout(tmp_path):
    _, pool, path = synthetic(tmp_path)
    wb = load_workbook(path)
    assert wb.sheetnames == [e3.INSTRUCTIONS_SHEET, e3.LABEL_SHEET]
    ws = wb[e3.LABEL_SHEET]
    assert tuple(c.value for c in ws[1]) == HEADER
    assert ws.freeze_panes == "E2" and ws.max_row == 1 + len(pool)
    assert sorted(str(m) for m in ws.merged_cells.ranges) == ["J2:J4", "J5:J7"]
    assert ws.data_validations.dataValidation[0].formula1 == '"0,1,2"'
    assert ws.cell(2, HEADER.index("passage") + 1).value.startswith("=passage")
    assert ws.cell(2, HEADER.index("passage") + 1).data_type == "s"
    assert ws.cell(2, HEADER.index("passage") + 1).alignment.wrap_text
    guide = [r[1] for r in wb[e3.INSTRUCTIONS_SHEET].iter_rows(values_only=True)]
    assert "the AMZN passage" in guide and "2 (explains the movement)" in guide


def test_validate_and_import_a_filled_sheet(tmp_path):
    queries, pool, path = synthetic(tmp_path)
    header, rows = e3.read_sheet(path)
    assert e3.validate_sheet(header, rows, pool, partial=True) == []
    assert len(e3.validate_sheet(header, rows, pool)) == 6  # no labels yet
    fill(path, {2: 2, 3: "1", 4: 0.0, 5: 0, 6: 2, 7: 1}, {5: "The better sentence."})
    header, rows = e3.read_sheet(path)
    assert e3.validate_sheet(header, rows, pool) == []
    records = e3.label_records(rows, queries, "sha")
    assert [r["query_id"] for r in records] == ["E3-Q01", "E3-Q02"]
    assert [x["label"] for x in records[0]["labels"]] == [2, 1, 0]
    assert records[1]["better_passage"] == "The better sentence."
    assert records[1]["kind"] == EXPLAIN and records[0]["pool_sha256"] == "sha"


def test_validate_finds_every_kind_of_error(tmp_path):
    _, pool, path = synthetic(tmp_path)
    header, rows = e3.read_sheet(path)
    assert e3.validate_sheet(("x",) + tuple(header[1:]), rows, pool)[0].startswith("the labels")
    bad = [replace(rows[0], label=3), replace(rows[1], label="2", passage="edited"),
           replace(rows[2], label=1, better_passage="not here"), rows[3], rows[3],
           replace(rows[4], chunk_id="unknown", label=1)]
    errors = "\n".join(e3.validate_sheet(header, bad, pool, partial=True))
    for expected in ("is not 0, 1 or 2", "passage text was changed", "outside the query's",
                     "duplicate row", "not in the pool", "2 pool rows are missing"):
        assert expected in errors
    spaced = replace(rows[0], label=2, passage="  " + rows[0].passage.replace(" ", "\n"))
    assert e3.validate_sheet(header, [spaced] + rows[1:], pool, partial=True) == []


def test_parse_label():
    assert [e3.parse_label(v) for v in (2, 2.0, " 1 ", "0", None, "")] == [2, 2, 1, 0, None,
                                                                           None]
    for bad in ("x", 3, 1.5, True):
        with pytest.raises(ValueError):
            e3.parse_label(bad)


def test_main_validate_and_import(tmp_path, monkeypatch, capsys):
    queries, pool, path = synthetic(tmp_path)
    s = spec(tmp_path)
    e3.write_jsonl(s.files["queries"], queries)
    e3.write_jsonl(s.files["pool"], pool)
    monkeypatch.setattr(e3, "load_e3_spec", lambda: s)
    assert e3.main(["validate", "--partial", "--sheet", str(path)]) == 0
    assert "valid (0 of 6 rows labelled)" in capsys.readouterr().out
    assert e3.main(["import", "--sheet", str(path)]) == 1
    assert not s.files["labels"].exists()
    fill(path, {n: 1 for n in range(2, 8)})
    assert e3.main(["import", "--sheet", str(path)]) == 0
    records = e3.read_jsonl(s.files["labels"])
    assert len(records) == 2 and records[0]["pool_sha256"] == e3.file_sha256(s.files["pool"])


# ---------------------------------------------------------------- report and examples

def test_render_and_write_generated_keeps_the_hand_written_part(tmp_path):
    queries = [query("E3-Q01"), query("E3-Q02", kind=EXPLAIN)]
    pool = [PoolRow("E3-Q01", n, f"c{n}", "ITEM_7", 3, "h", "t") for n in (1, 2, 3)]
    text = e3.render_generated(queries, pool, spec(), e3.Path("eval/s.xlsx"))
    assert "| E3-Q02 | AAA | 2025 | explain R1 | E3-Q02 moved. | 0 |" in text
    assert "**3 rows**" in text and "about 2 minutes" in text
    doc = tmp_path / "d.md"
    doc.write_text("# Hand\n\nkept\n")
    e3.write_generated(doc, "first")
    e3.write_generated(doc, "second")
    body = doc.read_text()
    assert body.startswith("# Hand\n\nkept") and "second" in body and "first" not in body
    big = e3.render_generated(queries, pool * 5, spec(), e3.Path("s.xlsx"))
    assert "larger than the aim" in big


def test_dev_example_refuses_a_test_company():
    with pytest.raises(ValueError, match="dev companies only"):
        e3.dev_example(None, Example("ZZZ", 2025, SIGNAL, "R1", "c", 2, "x"), date(2026, 1, 1))


# ---------------------------------------------------------------- scoring (7.6)

SCORING = """scoring:
  depth: 2
  relevance: {primary: 1, secondary: 2}
  decide_on: primary
  baseline: bm25
  bootstrap: {draws: 200, seed: 0, level: 0.95}
  ms_per_query: {bm25: 45, dense: 58}
"""


def _scored_files(tmp_path):
    """Two queries, two variants at depth 2; dense finds the label-2 chunk first in both."""
    s = spec(tmp_path)
    pool = [PoolRow(q, n, f"{q}-c{n}", "ITEM_7", 3, "h", "t") for q in ("E3-Q01", "E3-Q02")
            for n in (1, 2, 3)]
    e3.write_jsonl(s.files["pool"], pool)
    keys = []
    for q in ("E3-Q01", "E3-Q02"):
        keys += [{"query_id": q, "chunk_id": f"{q}-c1", "found": [["bm25", 2, 1.0],
                                                                 ["dense", 1, 0.9]]},
                 {"query_id": q, "chunk_id": f"{q}-c2", "found": [["bm25", 1, 2.0]]},
                 {"query_id": q, "chunk_id": f"{q}-c3", "found": [["dense", 2, 0.5]]}]
    e3.write_jsonl(s.files["pool_key"], keys)
    sha = e3.file_sha256(s.files["pool"])
    labels = [{"query_id": q, "issuer": issuer, "fiscal_year": 2025, "kind": kind,
               "subject": "R1", "labels": [{"chunk_id": f"{q}-c1", "label": 2, "notes": ""},
                                           {"chunk_id": f"{q}-c2", "label": 0, "notes": ""},
                                           {"chunk_id": f"{q}-c3", "label": 1, "notes": ""}],
               "better_passage": better, "pool_sha256": sha}
              for q, issuer, kind, better in (("E3-Q01", "AAA", SIGNAL, ""),
                                              ("E3-Q02", "BBB", EXPLAIN, "A better one."))]
    e3.write_jsonl(s.files["labels"], labels)
    config = tmp_path / "e3.yaml"
    config.write_text(SCORING)
    return s, config


def test_score_run_render_and_record(tmp_path):
    import csv

    s, config = _scored_files(tmp_path)
    result = e3.score_run(s, dict(s.files), config)
    by = {(x.variant, x.definition): x for xs in result["summaries"].values() for x in xs}
    assert by[("bm25", "primary")].mrr == 0.5 and by[("dense", "primary")].mrr == 1.0
    assert by[("dense", "primary")].mrr_diff == 0.5
    # E3-Q02's better passage: strict relevant = c1 + the pool miss -> dense recall 1/2
    assert by[("dense", "secondary")].recall == pytest.approx((1.0 + 0.5) / 2)
    assert result["decision"].default == "dense"  # every query improves: interval above 0
    assert [m["query_id"] for m in result["pool_misses"]] == ["E3-Q02"]
    header = {"git_commit": "abc1234", "git_dirty": True, "variants": ["bm25", "dense"],
              "evidence_manifest_sha256": "f" * 64}
    report = e3.render_scores(result, "e3-0009", header, "bm25")
    assert report.startswith("# E3 retrieval scores (e3-0009)")
    assert "## Pool misses (better passages)" in report and "A better one." in report
    assert "## By company (primary)" in report and "## By query kind (secondary)" in report
    assert "**Decision: `dense`**" in report
    metrics = e3.metrics_record(result)
    assert metrics["pool_misses"] == ["E3-Q02"] and metrics["depth"] == 2
    runs = tmp_path / "runs" / "e3-0009"
    runs.mkdir(parents=True)
    (runs / "header.json").write_text(json.dumps(header))
    (runs / "metrics.json").write_text(json.dumps(metrics, default=str))
    experiments = tmp_path / "experiments.csv"
    experiments.write_text("run_id,date,eval_id,component,version,single_change,dataset_version,"
                           "split,metrics,git_commit,evidence_manifest_hash,conclusion\n")
    e3.record_score("e3-0009", "four variants", "dense", tmp_path / "runs", experiments)
    row = list(csv.DictReader(experiments.open()))[0]
    assert row["eval_id"] == "E3" and row["git_commit"] == "abc1234-dirty"
    assert row["version"] == "bm25/dense @ depth 2"


def test_score_run_refuses_labels_from_another_pool(tmp_path):
    s, config = _scored_files(tmp_path)
    e3.write_jsonl(s.files["pool"], [PoolRow("E3-Q01", 1, "other", "ITEM_7", 3, "h", "t")])
    with pytest.raises(ValueError, match="another pool"):
        e3.score_run(s, dict(s.files), config)
