import json

import pytest

from finagent.config import ConfigError
from finagent.evaluation import e3_score as s

SPEC = s.ScoringSpec(depth=3, relevance={"primary": 1, "secondary": 2}, decide_on="primary",
                     baseline="bm25", draws=500, seed=0, level=0.95,
                     ms_per_query={"bm25": 45, "dense": 58, "hybrid_rrf": 59})
VARIANTS = ("bm25", "dense", "hybrid_rrf")


def q(qid, labels, misses=0, issuer="AAA", kind="signal"):
    return s.QueryLabels(qid, issuer, 2025, kind, "R1", labels, misses)


def test_load_scoring_reads_the_config_and_checks_it(tmp_path):
    spec = s.load_scoring(s.Path(__file__).resolve().parents[1] / "configs" / "e3.yaml")
    assert spec.depth == 5 and spec.relevance == {"primary": 1, "secondary": 2}
    assert spec.baseline == "bm25" and spec.decide_on == "primary"
    assert spec.ms_per_query["bm25"] < spec.ms_per_query["hybrid_rrf_rerank"]
    bad = tmp_path / "e3.yaml"
    bad.write_text("scoring: {depth: 5, relevance: {primary: 1}, decide_on: primary, "
                   "baseline: bm25, bootstrap: {draws: 10, seed: 0, level: 0.95}, "
                   "ms_per_query: {bm25: 1}}")
    with pytest.raises(ConfigError):
        s.load_scoring(bad)


def test_query_labels_count_a_better_passage_as_a_pool_miss():
    records = [{"query_id": "Q1", "issuer": "AAA", "fiscal_year": 2025, "kind": "explain",
                "subject": "revenue", "labels": [{"chunk_id": "c1", "label": 2, "notes": ""}],
                "better_passage": " A sentence. ", "pool_sha256": "x"},
               {"query_id": "Q2", "issuer": "AAA", "fiscal_year": 2025, "kind": "signal",
                "subject": "R1", "labels": [], "better_passage": "", "pool_sha256": "x"}]
    found = s.query_labels(records)
    assert found[0].labels == {"c1": 2} and found[0].pool_misses == 1
    assert found[1].pool_misses == 0


def test_ranked_lists_need_every_rank_once():
    key = [{"query_id": "Q1", "chunk_id": "a", "found": [["bm25", 1, 9.0], ["dense", 2, 0.5]]},
           {"query_id": "Q1", "chunk_id": "b", "found": [["bm25", 2, 8.0], ["dense", 1, 0.6]]},
           {"query_id": "Q1", "chunk_id": "c", "found": [["bm25", 3, 7.0], ["dense", 3, 0.4],
                                                         ["bm25", 6, 1.0]]}]
    lists = s.ranked_lists(key, ("bm25", "dense"), 3)
    assert lists[("Q1", "bm25")] == ["a", "b", "c"] and lists[("Q1", "dense")] == ["b", "a", "c"]
    with pytest.raises(ValueError):
        s.ranked_lists(key[:2], ("bm25",), 3)
    with pytest.raises(ValueError):
        s.ranked_lists(key + [{"query_id": "Q1", "chunk_id": "d", "found": [["bm25", 1, 1.0]]}],
                       ("bm25",), 3)


def test_recall_and_reciprocal_rank_by_hand():
    assert s.recall_at(["a", "b", "c"], {"b", "x"}, 0, 3) == 0.5
    assert s.recall_at(["a", "b", "c"], {"b"}, 1, 3) == 0.5  # the pool miss adds to the total
    assert s.recall_at(["a", "b", "c"], set(), 0, 3) is None
    assert s.recall_at(["a"], set(), 1, 3) == 0.0  # only a pool miss: scored, never found
    assert s.reciprocal_rank(["a", "b", "c"], {"c"}, 3) == pytest.approx(1 / 3)
    assert s.reciprocal_rank(["a", "b", "c", "d"], {"d"}, 3) == 0.0


def test_score_queries_lenient_strict_and_left_out():
    queries = [q("Q1", {"a": 2, "b": 1, "c": 0}), q("Q2", {"d": 0, "e": 0}),
               q("Q3", {"f": 1}, misses=1)]
    lists = {("Q1", "bm25"): ["c", "b", "a"], ("Q2", "bm25"): ["d", "e", "z"],
             ("Q3", "bm25"): ["f", "y", "z"]}
    found = {(x.query_id, x.definition): x for x in s.score_queries(queries, lists, ("bm25",),
                                                                     SPEC)}
    assert found[("Q1", "primary")].rr == 0.5 and found[("Q1", "primary")].recall == 1.0
    assert found[("Q1", "secondary")].rr == pytest.approx(1 / 3)
    assert found[("Q1", "secondary")].first_rank == 3
    assert found[("Q2", "primary")].recall is None and found[("Q2", "primary")].rr is None
    assert found[("Q3", "primary")].recall == 0.5 and found[("Q3", "primary")].relevant == 2
    # strict: Q3 has no label 2 but its pool miss is relevant: scored, recall 0, rr 0
    assert found[("Q3", "secondary")].recall == 0.0 and found[("Q3", "secondary")].rr == 0.0


def test_bootstrap_mean_and_paired_difference():
    mean, (lo, hi) = s.bootstrap_mean([0.0, 1.0, 1.0, 0.5], 1000, 0, 0.95)
    assert mean == 0.625 and 0.0 <= lo <= mean <= hi <= 1.0
    assert s.bootstrap_mean([], 10, 0, 0.95) is None
    assert s.bootstrap_mean([0.4, 0.4], 50, 0, 0.95) == (0.4, (0.4, 0.4))
    diff, (lo, hi) = s.paired_difference([0.0, 0.5, 0.0], [1.0, 1.0, 0.5], 1000, 0, 0.95)
    assert diff == pytest.approx(2 / 3) and lo > 0
    assert s.paired_difference([0.2], [0.2], 10, 0, 0.95) == (0.0, (0.0, 0.0))
    with pytest.raises(ValueError):
        s.paired_difference([0.1], [0.1, 0.2], 10, 0, 0.95)


def _scores(better="dense", other_helps=True):
    """Ten queries; `better` finds the relevant chunk at rank 1, bm25 at rank 3, the third
    variant at rank 2 in half the queries (other_helps) or always at rank 3 like bm25."""
    queries, lists = [], {}
    for n in range(10):
        qid = f"Q{n}"
        queries.append(q(qid, {"r": 1, "x": 0, "y": 0}, issuer="AAA" if n < 5 else "BBB",
                         kind="signal" if n % 2 else "explain"))
        lists[(qid, "bm25")] = ["x", "y", "r"]
        lists[(qid, better)] = ["r", "x", "y"]
        other = [v for v in VARIANTS if v not in ("bm25", better)][0]
        lists[(qid, other)] = ["x", "r", "y"] if n % 2 and other_helps else ["x", "y", "r"]
    queries.append(q("Q-none", {"x": 0}))
    for v in VARIANTS:
        lists[("Q-none", v)] = ["x", "y", "z"]
    return queries, s.score_queries(queries, lists, VARIANTS, SPEC)


def test_summarise_counts_left_out_and_compares_with_bm25():
    _, scores = _scores()
    by = {x.variant: x for x in s.summarise(scores, VARIANTS, "primary", SPEC)}
    assert by["bm25"].n == 10 and by["bm25"].left_out == 1
    assert by["bm25"].mrr == pytest.approx(1 / 3) and by["bm25"].mrr_diff is None
    assert by["dense"].mrr == 1.0 and by["dense"].mrr_diff == pytest.approx(2 / 3)
    assert by["dense"].mrr_diff_ci[0] > 0
    sub = s.summarise(scores, VARIANTS, "primary", SPEC, {"Q0", "Q1", "Q-none"})
    assert sub[0].n == 2 and sub[0].left_out == 1


def test_decide_switches_only_with_an_interval_above_zero_and_takes_the_cheapest():
    _, scores = _scores("hybrid_rrf", other_helps=False)
    decision = s.decide(s.summarise(scores, VARIANTS, "primary", SPEC), SPEC)
    assert decision.changed and decision.default == "hybrid_rrf"
    assert decision.qualifying == ("hybrid_rrf",)  # dense ties bm25: interval [0, 0]
    # dense helps in half the queries: it qualifies too and is cheaper
    _, scores = _scores("hybrid_rrf")
    assert s.decide(s.summarise(scores, VARIANTS, "primary", SPEC), SPEC).default == "dense"
    # both dense and hybrid_rrf beat bm25 everywhere -> the cheaper (dense) wins
    queries, scores = _scores("hybrid_rrf")
    both = [x for x in scores if x.variant != "dense"] + [
        s.QueryScore(x.query_id, x.issuer, x.kind, "dense", x.definition, x.relevant, x.recall,
                     x.rr, x.first_rank) for x in scores if x.variant == "hybrid_rrf"]
    decision = s.decide(s.summarise(both, VARIANTS, "primary", SPEC), SPEC)
    assert decision.qualifying == ("dense", "hybrid_rrf") and decision.default == "dense"
    # nothing better than bm25 -> bm25 stays
    same = [s.QueryScore(x.query_id, x.issuer, x.kind, x.variant, x.definition, x.relevant,
                         x.recall, None if x.rr is None else 1 / 3, x.first_rank)
            for x in scores]
    decision = s.decide(s.summarise(same, VARIANTS, "primary", SPEC), SPEC)
    assert not decision.changed and decision.default == "bm25" and decision.qualifying == ()


def test_report_tables_render():
    queries, scores = _scores()
    summaries = s.summarise(scores, VARIANTS, "primary", SPEC)
    table = s.summary_table(summaries, "bm25")
    assert table[0].startswith("| variant |") and len(table) == 2 + len(VARIANTS)
    groups = {"AAA": {f"Q{n}" for n in range(5)}, "BBB": {f"Q{n}" for n in range(5, 10)}}
    assert len(s.breakdown_table(scores, VARIANTS, SPEC, groups, "primary")) == 4
    rows = s.query_table(scores, queries, VARIANTS)
    assert rows[2].startswith("| Q0 | AAA | explain R1 | 1 / 0 (0) | 3 | 1 | 3 |")
    assert rows[-1].endswith("| n/a | n/a | n/a |")
    assert json.dumps([s.DEFINITION_NAMES[s.PRIMARY]])
