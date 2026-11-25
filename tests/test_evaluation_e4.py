"""E4 faithfulness sheet, judge, import and score on synthetic cards (7.6; D9)."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest
import yaml

from finagent.config import ConfigError
from finagent.evaluation import e4

HYP = {"c1": "Alpha rose.", "c2": "Beta fell."}
SPEC = e4.E4Spec(date(2026, 11, 24), "insights-x",
                 {"cards": "gold/c.jsonl", "sheet": "eval/s.xlsx", "judge_key": "gold/k.jsonl",
                  "labels": "gold/l.jsonl", "doc": "docs/e4.md"},
                 1, False, e4.LABELS, 75, "nli_judge", "entailment", HYP, 300, 0, 0.95,
                 (e4.Example("DEVA", 2024, "R3", "supported", "it says why"),))
UNIVERSE = {"DEVA": "validation", "AAA": "validation", "BBB": "validation", "HHH": "held_out"}
DEV = ("DEVA",)
HEADER = {"run_id": "insights-x", "as_of": "2026-11-24", "signals_sha256": "s",
          "templates_sha256": "t", "evidence_manifest_sha256": "m"}


def raw_card(sid, status, quotes=(), fired=("c1",)):
    tail = " Evidence: quoted below." if quotes else (
        " EVIDENCE_GAP: the filing does not explain the movement." if status == "EVIDENCE_GAP"
        else " Evidence: model-derived; no filing evidence expected.")
    return {"signal_id": sid, "name": f"name {sid}", "status": status, "accession": "0001-x",
            "metrics": [{"condition_id": c, "state": "FIRED"} for c in fired]
            + [{"condition_id": "c9", "state": "NOT_FIRED"}],
            "evidence": [{"chunk_id": f"0001-x:ITEM_7:{n:04d}", "section": "ITEM_7", "text": t}
                         for n, t in enumerate(quotes)],
            "text": f"{sid} {sid} name (FY, severity medium): up +1.0 pp (test > +0.5 pp).{tail}"}


def run_data():
    q = ("Alpha rose because of x.",)
    return {"header": HEADER, "companies": [
        {"ticker": "DEVA", "error": "", "cards": {
            "2023": [raw_card("R2", "VERIFIED", q)], "2024": [raw_card("R3", "VERIFIED", q)],
            "2025": [raw_card("R1", "VERIFIED", q)]}},
        {"ticker": "AAA", "error": "", "cards": {
            "2023": [raw_card("R5", "VERIFIED", q)],
            "2024": [raw_card("R3", "VERIFIED", q + ("Beta fell.",), ("c1", "c2")),
                     raw_card("R4", "EVIDENCE_GAP")],
            "2025": [raw_card("R1", "VERIFIED", q), raw_card("R7", "EVIDENCE_GAP"),
                     raw_card("R10", "VERIFIED", fired=("m1",))]}},
        {"ticker": "BBB", "error": "", "cards": {
            "2023": [raw_card("R2", "VERIFIED", q), raw_card("R4", "VERIFIED", q)],
            "2024": [raw_card("R8", "EVIDENCE_GAP")], "2025": []}}]}


def cards():
    return e4.select_cards(e4.run_cards(run_data(), ["DEVA", "AAA", "BBB"]), DEV, SPEC)


def test_load_e4_spec_reads_the_config_and_needs_every_hypothesis(tmp_path):
    spec = e4.load_e4_spec()
    assert spec.labels == e4.LABELS and spec.judge_model == "nli_judge"
    assert spec.earlier_years == 1 and not spec.earlier_from_dev
    assert {e.issuer for e in spec.examples} <= {"AMZN", "MSFT"}
    data = yaml.safe_load(e4.E4_PATH.read_text())
    data["judge"]["hypotheses"].pop("current_ratio")
    bad = tmp_path / "e4.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="current_ratio"):
        e4.load_e4_spec(bad)
    data = yaml.safe_load(e4.E4_PATH.read_text())
    data["labels"] = ["yes", "no"]
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError):
        e4.load_e4_spec(bad)


def test_card_kind_and_claim_text():
    assert e4.card_kind("VERIFIED", 2) == e4.QUOTED and e4.card_kind("VERIFIED", 0) == e4.MODEL
    assert e4.card_kind("EVIDENCE_GAP", 0) == e4.EVIDENCE_GAP
    with pytest.raises(ValueError):
        e4.card_kind("GAP", 0)
    assert e4.claim_text("R1 x (FY2025): up 2.0 pp. Evidence: quoted below.") == \
        "R1 x (FY2025): up 2.0 pp."
    assert e4.claim_text("R1 x: up. EVIDENCE_GAP: none above the threshold.") == "R1 x: up."


def test_run_cards_reads_every_card_and_refuses_other_companies():
    found = e4.run_cards(run_data(), ["DEVA", "AAA", "BBB"])
    assert len(found) == 12 and [k.issuer for k in found][:3] == ["DEVA"] * 3
    assert [k.fiscal_year for k in found if k.issuer == "DEVA"] == [2025, 2024, 2023]
    aaa = [k for k in found if k.issuer == "AAA"]
    assert [k.kind for k in aaa if k.latest] == [e4.QUOTED, e4.EVIDENCE_GAP, e4.MODEL]
    r3 = next(k for k in aaa if k.signal_id == "R3")
    assert r3.fired == ("c1", "c2") and len(r3.quotes) == 2 and r3.claim.endswith("pp).")
    with pytest.raises(ValueError, match="not validation"):
        e4.run_cards(run_data(), ["DEVA", "AAA"])
    broken = run_data()
    broken["companies"][1]["error"] = "KeyError: x\ntrace"
    with pytest.raises(ValueError, match="failed"):
        e4.run_cards(broken, ["DEVA", "AAA", "BBB"])


def test_check_run_refuses_a_stale_run():
    e4.check_run(HEADER, HEADER)
    with pytest.raises(ValueError, match="signals_sha256"):
        e4.check_run(HEADER, {**HEADER, "signals_sha256": "other"})


def test_select_cards_latest_plus_test_companies_most_recent_earlier_year():
    picked = [(k.card_id, k.issuer, k.fiscal_year, k.signal_id) for k in e4.on_sheet(cards())]
    assert picked == [("E4-C01", "DEVA", 2025, "R1"), ("E4-C02", "AAA", 2025, "R1"),
                      ("E4-C03", "AAA", 2024, "R3"), ("E4-C04", "BBB", 2023, "R2"),
                      ("E4-C05", "BBB", 2023, "R4")]
    more = e4.select_cards(e4.run_cards(run_data(), ["DEVA", "AAA", "BBB"]), DEV,
                           replace(SPEC, earlier_years=2, earlier_from_dev=True))
    assert len(e4.on_sheet(more)) == 8


def test_census_counts_every_kind():
    counts = e4.census(cards())
    assert counts["all_years"] == {"quoted": 8, "EVIDENCE_GAP": 3, "model": 1}
    assert counts["latest_year"] == {"quoted": 2, "EVIDENCE_GAP": 1, "model": 1}
    assert counts["on_sheet"] == {"quoted": 5, "EVIDENCE_GAP": 0, "model": 0}


def test_build_cards_from_a_run_directory(tmp_path):
    (tmp_path / "insights-x").mkdir()
    (tmp_path / "insights-x" / "cards.json").write_text(json.dumps(run_data()))
    found = e4.build_cards(SPEC, UNIVERSE, DEV, tmp_path, HEADER)
    assert len(e4.on_sheet(found)) == 5
    with pytest.raises(ValueError):
        e4.build_cards(SPEC, UNIVERSE, DEV, tmp_path, {**HEADER, "as_of": "2026-01-01"})


def test_example_cards_come_from_dev_cards_off_the_sheet():
    (example, card), = e4.example_cards(cards(), SPEC, DEV)
    assert (card.issuer, card.fiscal_year, card.signal_id, card.card_id) == ("DEVA", 2024, "R3",
                                                                              "")
    with pytest.raises(ValueError, match="dev companies"):
        e4.example_cards(cards(), replace(SPEC, examples=(e4.Example("AAA", 2023, "R5", "partial",
                                                                     "x"),)), DEV)
    with pytest.raises(ValueError, match="off the sheet"):
        e4.example_cards(cards(), replace(SPEC, examples=(e4.Example("DEVA", 2025, "R1",
                                                                     "partial", "x"),)), DEV)


def _filled(tmp_path, labels):
    from openpyxl import load_workbook

    found = cards()
    path = e4.write_sheet(tmp_path / "s.xlsx", found, SPEC, e4.example_cards(found, SPEC, DEV))
    wb = load_workbook(path)
    ws = wb[e4.LABEL_SHEET]
    col = e4.HEADER.index("label") + 1
    for r, label in enumerate(labels, 2):
        ws.cell(r, col, label)
    wb.save(path)
    return path, found


def test_sheet_round_trip_validate_and_import(tmp_path):
    path, found = _filled(tmp_path, ["Supported", "partial", "unsupported", "supported", None])
    header, rows = e4.read_sheet(path)
    assert header == e4.HEADER and [r.card_id for r in rows] == [f"E4-C0{n}" for n in range(1, 6)]
    assert rows[2].quote_2 == "Beta fell." and rows[0].quote_2 == ""
    assert e4.validate_sheet(header, rows, found, partial=True) == []
    assert e4.validate_sheet(header, rows, found) == ["row 6 (E4-C05): no label"]
    from openpyxl import load_workbook
    guide = [c.value for c in load_workbook(path)[e4.INSTRUCTIONS_SHEET]["A"]]
    assert "example 1" in guide and "supported" in guide
    cards_path = tmp_path / "c.jsonl"
    e4.write_jsonl(cards_path, found)
    assert e4.load_cards(cards_path) == found
    labels_path = tmp_path / "l.jsonl"
    assert e4.import_labels(path, cards_path, labels_path)  # one card unlabelled: refused
    assert not labels_path.exists()
    path, _ = _filled(tmp_path, ["supported", "partial", "unsupported", "supported", "partial"])
    assert e4.import_labels(path, cards_path, labels_path) == []
    records = e4.read_jsonl(labels_path)
    assert [r["label"] for r in records] == ["supported", "partial", "unsupported", "supported",
                                             "partial"]
    assert records[0]["cards_sha256"] == e4.file_sha256(cards_path)


def test_validate_finds_edits_bad_labels_duplicates_and_missing_rows():
    found = cards()
    rows = [e4.SheetRow(k.card_id, k.claim, k.quotes[0].text,
                        k.quotes[1].text if len(k.quotes) > 1 else "", "supported", "", n)
            for n, k in enumerate(e4.on_sheet(found), 2)]
    assert e4.validate_sheet(e4.HEADER, rows, found) == []
    bad = [replace(rows[0], quote_1="edited"), replace(rows[1], label="maybe"), rows[2], rows[2],
           replace(rows[3], card_id="E4-C99")]
    errors = e4.validate_sheet(e4.HEADER, bad, found)
    assert any("changed" in e for e in errors) and any("maybe" in e for e in errors)
    assert any("duplicate" in e for e in errors) and any("not a card" in e for e in errors)
    assert any("missing" in e for e in errors)
    assert e4.validate_sheet(e4.HEADER[:-1], rows, found)[0].startswith("the labels tab header")
    assert e4.parse_label(" Partial ") == "partial" and e4.parse_label("") is None


def fake_predict(pairs):
    """Entailment when the premise contains the hypothesis (without its full stop), else
    neutral."""
    return [{"contradiction": 0.1, "entailment": 0.8, "neutral": 0.1} if h.rstrip(".") in p else
            {"contradiction": 0.2, "entailment": 0.1, "neutral": 0.7} for p, h in pairs]


def test_judge_card_rule_supported_partial_unsupported():
    r3 = next(k for k in cards() if k.signal_id == "R3" and k.issuer == "AAA")
    assert e4.hypotheses_for(r3, SPEC) == ("Alpha rose.", "Beta fell.")
    assert e4.judge_card(r3, fake_predict, SPEC)["label"] == e4.SUPPORTED
    one = replace(r3, quotes=r3.quotes[:1])
    judged = e4.judge_card(one, fake_predict, SPEC)
    assert judged["label"] == e4.PARTIAL
    assert [c["entailed"] for c in judged["claims"]] == [True, False]
    assert judged["claims"][1]["quotes"][0]["class"] == "neutral"
    none = replace(r3, quotes=(e4.CardQuote("x", "ITEM_7", "Something else."),))
    assert e4.judge_card(none, fake_predict, SPEC)["label"] == e4.UNSUPPORTED
    with pytest.raises(ValueError, match="no hypothesis"):
        e4.hypotheses_for(replace(r3, fired=("c7",)), SPEC)
    with pytest.raises(ValueError):
        e4.judge_card(r3, lambda pairs: fake_predict(pairs)[:1], SPEC)


def test_nli_predict_maps_probabilities_to_label_names():
    class Fake:
        def predict(self, pairs, batch_size, apply_softmax, show_progress_bar):
            assert apply_softmax and not show_progress_bar
            return np.array([[0.1, 0.7, 0.2]] * len(pairs))
    predict = e4.nli_predict(Fake(), {0: "contradiction", 1: "entailment", 2: "neutral"})
    out = predict([("p", "h"), ("p2", "h2")])
    assert len(out) == 2 and out[0]["entailment"] == pytest.approx(0.7)


def test_judge_records_cover_the_sheet_with_the_pin():
    found = cards()
    records = e4.judge_records(found, fake_predict, SPEC, {"model": "m", "revision": "r"}, "sha")
    assert [r["card_id"] for r in records] == [k.card_id for k in e4.on_sheet(found)]
    assert records[0]["judge"] == {"model": "m", "revision": "r"}
    assert records[0]["cards_sha256"] == "sha"


def test_cohen_kappa_by_hand_and_undefined():
    s, p, u = e4.LABELS
    assert e4.cohen_kappa([s, s, p, u], [s, p, p, u], e4.LABELS) == pytest.approx(0.4375 / 0.6875)
    assert e4.cohen_kappa([s, p], [s, p], e4.LABELS) == 1.0
    assert e4.cohen_kappa([s, s], [s, s], e4.LABELS) is None
    with pytest.raises(ValueError):
        e4.cohen_kappa([s], [s, p], e4.LABELS)
    kappa, interval, skipped = e4.bootstrap_kappa([s, s, p, u] * 3, [s, p, p, u] * 3, e4.LABELS,
                                                  200, 0, 0.95)
    assert kappa == pytest.approx(0.4375 / 0.6875) and interval[0] <= kappa <= interval[1]
    assert skipped >= 0


def test_score_e4_and_render():
    found = cards()
    ids = [k.card_id for k in e4.on_sheet(found)]
    human = ["supported", "partial", "unsupported", "supported", "unsupported"]
    judge = ["supported", "supported", "unsupported", "supported", "partial"]
    labels = [{"card_id": i, "label": x, "cards_sha256": "sha"} for i, x in zip(ids, human)]
    judged = [{"card_id": i, "label": x, "cards_sha256": "sha"} for i, x in zip(ids, judge)]
    result = e4.score_e4(labels, judged, found, "sha", SPEC)
    assert result["cards"] == 5 and result["human"]["unsupported"] == 2
    assert result["unsupported_rate"][0] == pytest.approx(0.4)
    assert result["kappa"][0] == pytest.approx(e4.cohen_kappa(human, judge, e4.LABELS))
    assert result["confusion"]["partial"]["supported"] == 1
    assert e4.some_support(human) == ["supported or partial"] * 2 + ["unsupported",
                                                                      "supported or partial",
                                                                      "unsupported"]
    assert result["kappa_some_support"][0] == pytest.approx(e4.cohen_kappa(
        e4.some_support(human), e4.some_support(judge), (e4.SOME_SUPPORT, e4.UNSUPPORTED)))
    assert result["evidence_gap"]["all_years"] == {"evidence_gap": 3, "of": 11,
                                                   "share": 3 / 11, "model_derived": 1}
    text = e4.render_score(result, "e4-0001", {"git_commit": "abc", "git_dirty": False,
                                               "judge": "nli @ a1"})
    assert "unsupported-claim rate (Vineet) | 0.400" in text and "EVIDENCE_GAP share" in text
    assert "supported or partial vs unsupported (report only" in text
    with pytest.raises(ValueError, match="another cards file"):
        e4.score_e4(labels, judged, found, "other", SPEC)
    with pytest.raises(ValueError, match="both labels"):
        e4.score_e4(labels[:-1], judged, found, "sha", SPEC)


def test_render_generated_lists_cards_and_time_without_judge_labels(tmp_path):
    text = e4.render_generated(cards(), SPEC)
    assert "## 5. The cards" in text and "E4-C05" in text and "about 6 minutes" in text
    assert "judge key (hidden)" in text.lower() and "supported" not in text
    doc = tmp_path / "e4.md"
    doc.write_text("# head\n\nkept\n")
    e4.write_generated(doc, text)
    e4.write_generated(doc, text)
    assert doc.read_text().count(e4.GENERATED) == 1 and "kept" in doc.read_text()


# ---------------------------------------------------------------- evidence v2 (D-EV)

def v2_run_data():
    """The v2 run: C01 and C04 keep their quotes, C02 and C05 change, C03 becomes a gap, and
    AAA FY2024 R4 (a v1 gap) is now quoted."""
    data = json.loads(json.dumps(run_data()))
    companies = {c["ticker"]: c["cards"] for c in data["companies"]}
    other = ("Alpha rose on a new contract.",)
    companies["AAA"]["2025"][0] = raw_card("R1", "VERIFIED", other)
    companies["AAA"]["2024"] = [raw_card("R3", "EVIDENCE_GAP", fired=("c1", "c2")),
                                raw_card("R4", "VERIFIED", other)]
    companies["BBB"]["2023"][1] = raw_card("R4", "VERIFIED", other)
    return data


def v2_cards():
    return e4.v2_slots(cards(), e4.run_cards(v2_run_data(), ["DEVA", "AAA", "BBB"]))


def test_load_e4_spec_reads_the_v2_section(tmp_path):
    spec = e4.load_e4_spec()
    assert spec.v2_insights_run == "insights-0003" and set(spec.v2_files) == set(e4.V2_FILES)
    assert spec.v2_files["sheet"].name == "e4_labels_v2.xlsx"
    data = yaml.safe_load(e4.E4_PATH.read_text())
    data["v2"]["files"].pop("slots")
    bad = tmp_path / "e4.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="v2 files"):
        e4.load_e4_spec(bad)


def test_v2_slots_sort_the_27_slots_and_number_only_the_changed_ones():
    slots, new = v2_cards()
    assert [(s.card_id, s.outcome) for s in slots] == [
        ("E4-C01", "unchanged"), ("E4-C02", "changed"), ("E4-C03", "EVIDENCE_GAP"),
        ("E4-C04", "unchanged"), ("E4-C05", "changed")]
    assert [k.card_id for k in e4.on_sheet(new)] == ["E4-C02", "E4-C05"]
    assert e4.on_sheet(new)[0].quotes[0].text == "Alpha rose on a new contract."
    assert e4.same_quotes(cards()[0], cards()[0])
    short = e4.run_cards(v2_run_data(), ["DEVA", "AAA", "BBB"])[1:]
    with pytest.raises(ValueError, match="no card in the v2 run"):
        e4.v2_slots(cards(), short)
    renamed = [replace(k, claim="other") if k.signal_id == "R1" else k
               for k in e4.run_cards(v2_run_data(), ["DEVA", "AAA", "BBB"])]
    with pytest.raises(ValueError, match="claim or kind"):
        e4.v2_slots(cards(), renamed)


def test_compare_runs_counts_kinds_and_changes_per_company():
    _, new = v2_cards()
    rows = {r["company"]: r for r in e4.compare_runs(cards(), new)}
    aaa = rows["AAA"]
    assert (aaa["v1_quoted"], aaa["v2_quoted"], aaa["v1_EVIDENCE_GAP"],
            aaa["v2_EVIDENCE_GAP"]) == (3, 3, 2, 2)
    assert (aaa["same_quotes"], aaa["changed_quotes"], aaa["quoted_to_gap"],
            aaa["gap_to_quoted"]) == (1, 1, 1, 1)
    total = rows["total"]
    assert total["changed_quotes"] == 2 and total["same_quotes"] == 5 and total["unmatched"] == 0


def _v2_sides():
    slots, new = v2_cards()
    v1_ids = [k.card_id for k in e4.on_sheet(cards())]
    v1_human = dict(zip(v1_ids, ["supported", "unsupported", "unsupported", "partial",
                                 "unsupported"]))
    v1 = {"labels": [{"card_id": i, "label": x, "cards_sha256": "a"}
                     for i, x in v1_human.items()],
          "judged": [{"card_id": i, "label": "unsupported", "cards_sha256": "a"}
                     for i in v1_ids],
          "cards_sha256": "a", "cards": cards()}
    v2 = {"labels": [{"card_id": "E4-C02", "label": "supported", "cards_sha256": "b"},
                     {"card_id": "E4-C05", "label": "partial", "cards_sha256": "b"}],
          "judged": [{"card_id": "E4-C02", "label": "supported", "cards_sha256": "b"},
                     {"card_id": "E4-C05", "label": "supported", "cards_sha256": "b"}],
          "cards_sha256": "b", "cards": new}
    return slots, v1_human, v1, v2


def test_score_e4_v2_combines_unchanged_v1_labels_with_v2_labels():
    slots, v1_human, v1, v2 = _v2_sides()
    new_labels = {"E4-C02": "supported", "E4-C05": "partial"}
    assert e4.combined_labels(slots, v1_human, new_labels) == {
        "E4-C01": "supported", "E4-C02": "supported", "E4-C04": "partial",
        "E4-C05": "partial"}  # C03 is a gap: no label
    result = e4.score_e4_v2(slots, v1, v2, SPEC)
    assert result["cards"] == 4 and result["slots"] == 5
    assert result["human"] == {"supported": 2, "partial": 2}
    assert result["unsupported_rate"][0] == 0.0
    assert result["outcomes"] == {"unchanged": 2, "changed": 2, "EVIDENCE_GAP": 1}
    assert result["slot_shares"]["EVIDENCE_GAP"][0] == pytest.approx(0.2)
    assert result["slot_shares"]["supported"][0] == pytest.approx(0.4)
    assert result["evidence_gap"]["all_years"]["evidence_gap"] == 3
    with pytest.raises(ValueError, match="another cards file"):
        e4.score_e4_v2(slots, v1, dict(v2, cards_sha256="c"), SPEC)
    with pytest.raises(ValueError, match="no label"):
        e4.score_e4_v2(slots, v1, dict(v2, labels=v2["labels"][:1]), SPEC)
    header = {"git_commit": "abc", "git_dirty": True, "judge": "nli @ a1"}
    text = e4.render_score_v2(result, "e4-0002", header, None, "e4-0001")
    assert "post-hoc" in text.lower() and "never instead of it" in text
    assert "| EVIDENCE_GAP | 0.200" in text and "unchanged 2, changed 2, EVIDENCE_GAP 1" in text


def test_render_v2_generated_and_slots_round_trip(tmp_path):
    slots, new = v2_cards()
    spec = replace(SPEC, v2_insights_run="insights-y",
                   v2_files={"sheet": "eval/s2.xlsx", "judge_key": "gold/k2.jsonl"})
    text = e4.render_v2_generated(cards(), new, slots, spec)
    assert "## 3. insights-y (v2) against insights-x (v1)" in text
    assert "| **total** | 8 → 8 | 3 → 3 |" in text
    assert "Changed quotes (on the v2 sheet): **2**" in text and "about 2 minutes" in text
    assert "| E4-C03 | AAA | 2024 | R3 | EVIDENCE_GAP |" in text
    path = tmp_path / "slots.jsonl"
    e4.write_jsonl(path, slots)
    assert e4.load_slots(path) == slots
    doc = tmp_path / "ev.md"
    doc.write_text("# head\n\nkept\n")
    e4.write_generated(doc, text, e4.GENERATED_V2)
    e4.write_generated(doc, text, e4.GENERATED_V2)
    assert doc.read_text().count(e4.GENERATED_V2) == 1 and "kept" in doc.read_text()


def test_build_v2_reads_the_v2_run_checked_against_today(tmp_path):
    spec = replace(SPEC, v2_insights_run="insights-y")
    (tmp_path / "insights-y").mkdir()
    (tmp_path / "insights-y" / "cards.json").write_text(json.dumps(v2_run_data()))
    slots, new = e4.build_v2(spec, cards(), UNIVERSE, tmp_path, HEADER)
    assert len(slots) == 5 and len(e4.on_sheet(new)) == 2
    with pytest.raises(ValueError):
        e4.build_v2(spec, cards(), UNIVERSE, tmp_path, dict(HEADER, signals_sha256="z"))
