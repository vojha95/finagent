import math

import pytest

from finagent.config import ConfigError
from finagent.evaluation import quote_threshold as qt

REL = {"strict": 2, "lenient": 1}


def _key(query, chunk, *found):
    return {"query_id": query, "chunk_id": chunk, "found": [list(f) for f in found]}


def _labels(query, issuer, labels, kind="signal"):
    return {"query_id": query, "issuer": issuer, "kind": kind,
            "labels": [{"chunk_id": c, "label": v, "notes": ""} for c, v in labels.items()]}


def _chunk(query, issuer, score, label, reference_label=0):
    return qt.TopChunk(query, issuer, "signal", f"{query}:c", score, label, reference_label)


def test_load_threshold_spec_reads_the_config_and_checks_it(tmp_path):
    spec = qt.load_threshold_spec()
    assert spec.variant == "hybrid_rrf_rerank" and spec.reference == "bm25"
    assert spec.decide_on == "strict" and spec.cost in spec.curve_costs
    bad = tmp_path / "e3.yaml"
    bad.write_text("quote_threshold: {variant: hybrid_rrf_rerank, reference: bm25, relevance: "
                   "{strict: 2, lenient: 1}, decide_on: strict, cost: 5, curve_costs: [1]}\n")
    with pytest.raises(ConfigError):
        qt.load_threshold_spec(bad)


def test_top_chunks_join_rank_one_scores_to_labels():
    key = [_key("Q1", "a", ("bm25", 1, 9.0), ("hybrid_rrf_rerank", 2, -3.0)),
           _key("Q1", "b", ("bm25", 2, 5.0), ("hybrid_rrf_rerank", 1, 1.5))]
    found = qt.top_chunks(key, [_labels("Q1", "X", {"a": 0, "b": 2})], "hybrid_rrf_rerank",
                          "bm25")
    assert found == [qt.TopChunk("Q1", "X", "signal", "b", 1.5, 2, 0)]
    with pytest.raises(ValueError):
        qt.top_chunks(key, [_labels("Q2", "X", {"a": 0})], "hybrid_rrf_rerank", "bm25")
    with pytest.raises(ValueError):
        qt.top_chunks(key + [_key("Q1", "c", ("bm25", 1, 1.0))], [], "hybrid_rrf_rerank", "bm25")


def test_cuts_group_ties_and_place_thresholds_midway():
    assert qt.cuts([3.0, 1.0, 1.0, -1.0]) == [(0, math.inf), (1, 2.0), (3, 0.0),
                                              (4, -math.inf)]
    assert qt.cuts([]) == [(0, math.inf)]


def test_choose_threshold_maximises_utility_with_ties_to_more_coverage():
    points = [(4.0, True), (3.0, True), (2.0, False), (1.0, False), (0.0, True)]
    assert qt.choose_threshold(points, 1.0) == 2.5  # +2 at two accepted
    # cost 0.5: accepting all scores 3 - 1 = 2, the same as two accepted: more coverage wins
    assert qt.choose_threshold(points, 0.5) == -math.inf
    assert qt.choose_threshold([(1.0, False)], 1.0) == math.inf  # accept none
    assert qt.choose_threshold([], 1.0) == math.inf


def test_reference_threshold_is_the_lowest_cut_reaching_the_target():
    points = [(4.0, True), (3.0, False), (2.0, True), (1.0, False)]
    assert qt.reference_threshold(points, 0.5) == -math.inf  # 2/4 at accept-all
    assert qt.reference_threshold(points, 0.6) == 1.5  # 2/3
    assert qt.reference_threshold(points, 1.01) == math.inf


def test_operating_point_precision_and_coverage():
    chunks = [_chunk("Q1", "A", 2.0, 2), _chunk("Q2", "A", 1.0, 1), _chunk("Q3", "A", 0.0, 0)]
    p = qt.operating_point(chunks, 0.5, REL)
    assert (p.queries, p.accepted, p.strict, p.lenient) == (3, 2, 1, 2)
    assert p.coverage == 2 / 3 and p.precision("strict") == 0.5 and p.precision("lenient") == 1
    none = qt.operating_point(chunks, math.inf, REL)
    assert none.precision("strict") is None and none.coverage == 0


def test_leave_one_company_out_uses_only_the_other_companies():
    chunks = [_chunk("Q1", "A", 3.0, 2), _chunk("Q2", "A", 1.0, 0),
              _chunk("Q3", "B", 2.0, 2), _chunk("Q4", "B", 0.5, 0)]
    seen = []

    def rule(train):
        seen.append({c.issuer for c in train})
        return 1.5

    folds = qt.leave_one_company_out(chunks, rule, REL)
    assert seen == [{"B"}, {"A"}] and [f.company for f in folds] == ["A", "B"]
    total = qt.pooled(folds)
    assert (total.queries, total.accepted, total.strict) == (4, 2, 2)


def test_example_rule_targets_the_reference_precision():
    chunks = [_chunk("Q1", "A", 3.0, 2, 2), _chunk("Q2", "A", 2.0, 0, 0),
              _chunk("Q3", "A", 1.0, 2, 0), _chunk("Q4", "A", 0.0, 0, 0)]
    assert qt.example_rule(REL)(chunks) == -math.inf  # target 1/4; accept-all gives 2/4


def test_run_render_and_metrics_on_a_small_pool(tmp_path):
    key, labels = [], []
    rows = [("A", 3.0, 2), ("A", -1.0, 0), ("B", 2.0, 2), ("B", -2.0, 1), ("C", 1.0, 0),
            ("C", 0.5, 2)]
    for n, (issuer, score, label) in enumerate(rows):
        q = f"Q{n}"
        key.append(_key(q, f"{q}:r", ("hybrid_rrf_rerank", 1, score)))
        key.append(_key(q, f"{q}:b", ("bm25", 1, 1.0)))
        labels.append(_labels(q, issuer, {f"{q}:r": label, f"{q}:b": 1}))
    spec = qt.ThresholdSpec("hybrid_rrf_rerank", "bm25", REL, "strict", 1.0, (0.5, 1.0))
    result = qt.run(spec, key, labels)
    # strict, score order: T T F T F F; +2 after 2 and after 4 accepted: more coverage wins
    assert result["threshold"] == -0.25 and len(result["folds"]) == 3
    assert result["reference"].strict == 0 and result["reference"].lenient == 6
    assert set(result["sensitivity"]) == {"example_rule", "utility_lenient"}
    text = qt.render(result, spec, "e3-9999", {"git_commit": "abc", "git_dirty": False})
    assert "Shipped threshold: -0.250" in text and "| Q0 | A | signal | 3.000 | 2 | yes |" in text
    assert "| Q1 | A | signal | -1.000 | 0 | no |" in text
    metrics = qt.metrics_record(result)
    assert metrics["threshold"] == -0.25 and len(metrics["curve"]) == 2
    path = tmp_path / "experiments.csv"
    path.write_text("run_id,date,eval_id,component,version,single_change,dataset_version,split,"
                    "metrics,git_commit,evidence_manifest_hash,conclusion\n")
    qt.record("e3-9999", {"git_commit": "abc", "git_dirty": True,
                          "evidence_manifest_sha256": "f" * 64}, metrics, spec, path)
    assert "e3-9999" in path.read_text() and "abc-dirty" in path.read_text()
