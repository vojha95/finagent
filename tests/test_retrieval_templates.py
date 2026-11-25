"""The frozen evidence query templates (finagent.retrieval.templates; 8.13, 8.14).

test_templates_are_frozen fails whenever configs/query_templates.yaml changes: a change is a
new version, re-frozen deliberately (new hash in configs/query_templates.sha256) and logged in
docs/issues_log.md, never a tweak towards what retrieval returns.
"""

import pytest

from finagent.config import QUERY_TEMPLATES_PATH
from finagent.retrieval.templates import (TEMPLATES_HASH_PATH, TemplateFreezeError,
                                          check_frozen, file_sha256, load_query_templates)

FROZEN = "4420e0ac6f19e98d7859a2a166ac05ac7e549d0de7db3b3bfadd69f56ab29bac"


def test_templates_are_frozen():
    assert file_sha256(QUERY_TEMPLATES_PATH) == FROZEN
    assert TEMPLATES_HASH_PATH.read_text().split()[0] == FROZEN
    assert check_frozen() == FROZEN


def test_a_changed_file_is_refused(tmp_path):
    changed = tmp_path / "t.yaml"
    changed.write_text(QUERY_TEMPLATES_PATH.read_text() + "\n# tweak\n")
    with pytest.raises(TemplateFreezeError):
        load_query_templates(changed)


def test_signals_and_the_composite():
    t = load_query_templates()
    assert t.version == 1 and t.sha256 == FROZEN
    assert set(t.signals) == {f"R{i}" for i in range(1, 12)}
    r11 = t.signals["R11"]
    assert all(t.signals[s].query in r11.query for s in ("R1", "R2", "R3", "R4"))
    assert set(r11.anchors) == {a for s in ("R1", "R2", "R3", "R4")
                                for a in t.signals[s].anchors}


def test_explain_queries():
    t = load_query_templates()
    query, anchors = t.explain("accounts_receivable", "increased")
    assert query == "accounts receivable increased compared with the prior year primarily due to"
    assert anchors == ("receivable",)
    query, anchors = t.explain("some_metric", "decreased")
    assert query.startswith("some metric decreased") and anchors == ("some metric",)
    with pytest.raises(ValueError):
        t.explain("revenue", "rose")
