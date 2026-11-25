"""The 7.4 variant comparison (retrieval-0003): dev companies only, the same queries for every
variant, runtime measured per process, the report (no evidence read, no model loaded)."""

import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from retrieval_support import EVIDENCE, filing
from variant_support import FINGERPRINT, FakeCrossEncoder, FakeEncoder

from finagent.config import load_retrieval_config, load_signal_config
from finagent.records import Outcome
from finagent.retrieval import compare
from finagent.retrieval.compare import (MARKER, NotDevError, QuerySpec, check_dev,
                                        explain_spec, measure, peak_rss_bytes, query_from_json,
                                        render, run_variant, side_by_side_rows, signal_queries,
                                        write_report)
from finagent.retrieval.dense import Embedder, EmbeddingCache
from finagent.retrieval.index import Retriever, build_index
from finagent.retrieval.quotes import ChangeCheck
from finagent.retrieval.rerank import Reranker
from finagent.retrieval.templates import load_query_templates
from finagent.retrieval.variants import SearchModels

CONFIG = load_retrieval_config()
SPEC = load_signal_config().with_evidence("v1").evidence  # the 7.3 rules
AS_OF = date(2026, 11, 24)
FY2025 = filing(2025, date(2026, 2, 1), "0000000001-26-000001")
DEV = compare.DEV[0]


class FakeStore:
    evidence_dir, manifest = None, {}

    def load_filings(self, issuer, as_of):
        return [FY2025]


def retriever(tmp_path) -> Retriever:
    r = Retriever(FakeStore(), {DEV: "validation", "OTHR": "validation"}, CONFIG)
    r.indexes[FY2025.accession] = build_index(FY2025, EVIDENCE.encode(), CONFIG)
    r.models = SearchModels(CONFIG.search, {}, EmbeddingCache(tmp_path),
                            _embedder=Embedder(FakeEncoder(), FINGERPRINT),
                            _reranker=Reranker(FakeCrossEncoder()))
    return r


def query(issuer: str = DEV, expected=None) -> QuerySpec:
    return QuerySpec(f"{issuer} FY2025 R1", issuer, 2025, "signal", "R1",
                     "accounts receivable increased customer payment terms", ("receivable",),
                     ("ITEM_7", "ITEM_8"), expected)


def test_only_dev_companies_are_compared(tmp_path):
    check_dev([query()])
    with pytest.raises(NotDevError):
        check_dev([query(), query("OTHR")])
    with pytest.raises(NotDevError):
        run_variant([query("OTHR")], "bm25", AS_OF, retriever(tmp_path), SPEC)
    with pytest.raises(NotDevError):
        explain_spec(SimpleNamespace(), "OTHR", "latest", "revenue", AS_OF)


def test_query_specs_round_trip_and_carry_the_change_check():
    q = query(expected=("100", "900", "1000000"))
    assert query_from_json(asdict(q)) == q
    assert q.change_check() == ChangeCheck(Decimal(100), Decimal(900), Decimal(1000000))
    assert query().change_check() is None
    assert query(expected=("1", "2", "")).change_check().unit is None


def test_signal_queries_are_the_frozen_templates_except_model_derived():
    tools = SimpleNamespace(signal_config=load_signal_config(),
                            templates=load_query_templates())
    found = signal_queries(tools, DEV, 2025)
    ids = {q.subject for q in found}
    derived = {s for s, sig in tools.signal_config.signals.items() if sig.data == "scenario"}
    assert derived and not ids & derived  # #180: R10 searches nothing
    assert ids == set(tools.templates.signals) - derived
    for q in found:
        frozen = tools.templates.signals[q.subject]
        assert (q.query, q.anchors) == (frozen.query, tuple(frozen.anchors))
        assert q.sections == SPEC.sections and q.kind == "signal"


def test_explain_spec_follows_explain_inputs():
    expected = ChangeCheck(Decimal(100), Decimal(900), Decimal(10) ** 6)
    ready = SimpleNamespace(year=2025, query="revenue increased", anchors=("revenue",),
                            expected=expected)
    tools = SimpleNamespace(signal_config=load_signal_config(),
                            explain_inputs=lambda *a, **k: ready)
    q = explain_spec(tools, DEV, "latest", "revenue", AS_OF, "Why?")
    assert (q.fiscal_year, q.query, q.sections, q.question) == (
        2025, "revenue increased", SPEC.explain_sections, "Why?")
    assert q.change_check() == expected
    gap = Outcome("GAP", "NO_MOVEMENT", detail="FY2025 revenue equals FY2024")
    tools.explain_inputs = lambda *a, **k: gap
    assert explain_spec(tools, DEV, 2025, "revenue", AS_OF) == \
        "GAP NO_MOVEMENT: FY2025 revenue equals FY2024"


@pytest.mark.parametrize("variant", ["bm25", "dense", "hybrid_rrf", "hybrid_rrf_rerank"])
def test_run_variant_applies_the_7_3_quote_rules(tmp_path, variant):
    r = retriever(tmp_path)
    out = run_variant([query()], variant, AS_OF, r, SPEC)
    result = out["results"][0]
    assert out["variant"] == variant and r.variant == variant
    assert result["outcome"].startswith("QUOTED")
    assert result["candidates"][0]["text"].startswith("Accounts receivable increased 33%")
    assert result["candidates"][0]["quoted"] and len(result["candidates"]) <= compare.TOP
    assert len(result["hits"]) <= (3 if variant == "hybrid_rrf_rerank" else SPEC.hits)
    t = out["timings"]
    assert t["queries"] == 1 and t["query_seconds_total"] >= 0
    assert (t["chunks_embedded"] > 0) == (variant != "bm25")
    late = QuerySpec(f"{DEV} FY2026", DEV, 2026, "signal", "R1", "receivable", ("receivable",),
                     ("ITEM_7",))
    assert run_variant([late], variant, AS_OF, r, SPEC)["results"][0]["outcome"] == "NO_FILING"
    with pytest.raises(ValueError):
        run_variant([query()], "magic", AS_OF, r, SPEC)


def test_peak_memory_and_measure():
    usage = SimpleNamespace(ru_maxrss=1024)
    assert peak_rss_bytes(usage) == (1024 if sys.platform == "darwin" else 1024 * 1024)
    code, seconds, rss = measure([sys.executable, "-c", "pass"])
    assert code == 0 and seconds > 0 and rss > 0
    assert measure([sys.executable, "-c", "raise SystemExit(3)"])[0] == 3


def outputs(tmp_path):
    q = query()
    runs = []
    for variant in ("bm25", "dense"):
        out = run_variant([q], variant, AS_OF, retriever(tmp_path), SPEC)
        out.update(wall_seconds=1.5, peak_rss_bytes=200 * 2 ** 20)
        runs.append(out)
    return [q], {r["variant"]: r for r in runs}, runs


def test_side_by_side_rows_list_each_variant_candidate(tmp_path):
    queries, results, _ = outputs(tmp_path)
    rows = side_by_side_rows(queries, results)
    assert {r[1] for r in rows} == {"bm25", "dense"}
    assert rows[0][0] == queries[0].qid and rows[0][5] == "Q"
    assert rows[0][8].startswith("Accounts receivable increased 33%")


def test_report_renders_and_keeps_notes(tmp_path):
    queries, results, runs = outputs(tmp_path)
    header = {"run_id": "retrieval-test", "git_commit": "abc1234", "git_dirty": True,
              "as_of": AS_OF.isoformat(), "evidence_manifest_sha256": "0" * 64}
    pins = {"embedder": SimpleNamespace(key="embedder", model_id="tiny", repo="org/tiny",
                                        revision="a" * 40, files={"x": 1}, size=1,
                                        fingerprint="f" * 64)}
    text = render(header, queries, ["Q?: GAP NO_MOVEMENT"], results, runs, pins)
    assert "Dev companies only" in text and "| bm25 | 1.5 | 200 |" in text
    assert f"### {queries[0].qid}" in text and "Not searched: Q?: GAP NO_MOVEMENT." in text
    assert "**Q** " in text and "+ uncommitted changes" in text
    path = tmp_path / "report.md"
    write_report(path, text)
    path.write_text(path.read_text() + "Vineet's note.\n")
    write_report(path, text.replace("1.5", "2.5"))
    kept = path.read_text()
    assert kept.count(MARKER) == 1 and kept.endswith("Vineet's note.\n") and "2.5" in kept
