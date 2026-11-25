from datetime import date
from pathlib import Path

import numpy as np
import pytest

from finagent.document.classify import OTHER
from finagent.document.encoder import (
    EncoderRowClassifier,
    balanced_weights,
    class_weights,
    linear_schedule,
    pick_device,
    release_memory,
    seed_everything,
    serialise_row,
)
from finagent.records import RowInstance

WORDS = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "[", "]", "st", "##mt", "parent",
         "label", "indent", "total", "income", "statement", "balance", "sheet", "cash", "flow",
         "net", "sales", "revenue", "revenues", "earnings", "marketing", "other", "investing",
         "activities", "0", "1", "2"]


def tiny_base(directory):
    """A one-layer BERT and its tokenizer saved like a pretrained model (no download)."""
    from transformers import BertConfig, BertModel, BertTokenizerFast

    directory.mkdir(parents=True, exist_ok=True)
    BertTokenizerFast(vocab={w: i for i, w in enumerate(WORDS)},
                      do_lower_case=True).save_pretrained(str(directory))
    config = BertConfig(vocab_size=len(WORDS), hidden_size=16, num_hidden_layers=1,
                        num_attention_heads=2, intermediate_size=32, max_position_embeddings=64)
    BertModel(config).save_pretrained(str(directory))
    return directory


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return tiny_base(tmp_path_factory.mktemp("tiny_bert"))


def row(i, label, statement="IS", parent=None, indent=0, total=False):
    return RowInstance(
        row_id=f"r{i}", accession="acc", statement=statement, label=label,
        parent_header=parent, indent=indent, is_total=total, is_bold=False, position=i,
        cell_texts=("10",), column_periods=(date(2025, 12, 31),), unit_text=None,
        table_index=1, source_row=i, column_spans=((1, 1),))


def data():
    labelled = [("Net sales", "revenue"), ("Revenues", "revenue"), ("Total revenue", "revenue"),
                ("Net income", "net_income"), ("Net earnings", "net_income"),
                ("Earnings", "net_income"), ("Marketing", OTHER), ("Other", OTHER),
                ("Investing", OTHER)]
    rows = [row(i, label) for i, (label, _) in enumerate(labelled * 2)]
    return rows, [item for _, item in labelled * 2]


TINY = {"learning_rate": 5e-3, "epochs": 3, "class_weight": "balanced", "batch_size": 4,
        "max_length": 32, "warmup": 0.1, "seed": 0, "device": "cpu"}


def test_serialise_row_follows_the_blueprint_form():
    r = row(0, "Purchases of property and equipment", statement="CF",
            parent="Investing activities:", indent=1)
    assert serialise_row(r) == ("[STMT] cash flow [PARENT] investing activities [LABEL] "
                                "purchases of property and equipment [INDENT] 1 [TOTAL] 0")
    assert serialise_row(row(1, "Total assets", statement="BS", total=True)) == (
        "[STMT] balance sheet [PARENT]  [LABEL] total assets [INDENT] 0 [TOTAL] 1")


def test_pick_device_is_mps_or_cpu():
    assert pick_device() in {"mps", "cpu"}


def test_seed_everything_repeats_draws():
    import torch

    seed_everything(3)
    a = (np.random.rand(), torch.rand(1).item())
    seed_everything(3)
    assert (np.random.rand(), torch.rand(1).item()) == a


def test_balanced_weights_match_sklearn_and_zero_absent_classes():
    w = balanced_weights(["a", "a", "a", "b"], ["a", "b", "c"])
    assert w == pytest.approx([4 / (2 * 3), 4 / (2 * 1), 0.0])


def test_class_weights_balanced_sqrt_or_none():
    items, classes = ["a", "a", "a", "b"], ["a", "b"]
    assert class_weights(items, classes, "balanced") == pytest.approx([2 / 3, 2.0])
    assert class_weights(items, classes, "sqrt") == pytest.approx([(2 / 3) ** 0.5, 2 ** 0.5])
    assert class_weights(items, classes, None) == pytest.approx([1.0, 1.0])
    with pytest.raises(ValueError):
        class_weights(items, classes, "inverse")
    with pytest.raises(ValueError):
        EncoderRowClassifier(Path("unused"), class_weight="inverse")


def test_linear_schedule_warms_up_then_decays_to_zero():
    assert linear_schedule(0, 2, 10) == pytest.approx(0.5)
    assert linear_schedule(1, 2, 10) == pytest.approx(1.0)
    assert linear_schedule(2, 2, 10) == pytest.approx(1.0)
    assert linear_schedule(6, 2, 10) == pytest.approx(0.5)
    assert linear_schedule(10, 2, 10) == 0.0


def test_tiny_tokenizer_reads_the_markers(base):
    model = EncoderRowClassifier(base, **TINY)
    model._build(["a", "b"], pretrained=True)
    tokens = model.tokenizer.convert_ids_to_tokens(model._encode([row(0, "Net sales")])[0])
    assert tokens[:6] == ["[CLS]", "[", "st", "##mt", "]", "income"] and "[UNK]" not in tokens


def test_fit_predict_top3_and_learns_the_tiny_set(base):
    rows, items = data()
    model = EncoderRowClassifier(base, **{**TINY, "epochs": 15}).fit(rows, items)
    assert model.classes == (OTHER, "net_income", "revenue")
    predictions = model.predict(rows)
    assert len(predictions) == len(rows) and all(len(p.candidates) == 3 for p in predictions)
    assert predictions[0].model_version == "m3-bge-small-ft"
    assert sum(p.item == i for p, i in zip(predictions, items)) >= len(items) - 2
    assert np.allclose(model.probabilities(rows).sum(axis=1), 1.0)
    assert model.predict([]) == []


def test_fit_is_repeatable_with_the_same_seed_on_cpu(base):
    rows, items = data()
    a = EncoderRowClassifier(base, **TINY).fit(rows, items).probabilities(rows)
    b = EncoderRowClassifier(base, **TINY).fit(rows, items).probabilities(rows)
    assert np.array_equal(a, b)


def test_stop_after_and_callback_stop_match_and_keep_the_full_schedule(base):
    rows, items = data()
    seen = []

    def on_epoch(epoch, model):
        seen.append(epoch)
        return epoch == 2

    stopped = EncoderRowClassifier(base, **TINY).fit(rows, items, on_epoch=on_epoch)
    short = EncoderRowClassifier(base, **TINY).fit(rows, items, stop_after=2)
    assert seen == [1, 2] and stopped.epochs_trained == short.epochs_trained == 2
    assert np.array_equal(stopped.probabilities(rows), short.probabilities(rows))
    # a 2-epoch schedule decays faster than 2 epochs of a 3-epoch schedule
    two = EncoderRowClassifier(base, **{**TINY, "epochs": 2}).fit(rows, items)
    assert not np.array_equal(two.probabilities(rows), short.probabilities(rows))


def test_fixed_classes_add_columns_for_unseen_classes(base):
    rows, items = data()
    model = EncoderRowClassifier(base, **TINY).fit(
        rows, items, classes=[OTHER, "net_income", "research", "revenue"])
    assert model.classes == (OTHER, "net_income", "research", "revenue")
    assert model.probabilities(rows).shape == (len(rows), 4)


def test_fit_needs_two_classes(base):
    with pytest.raises(ValueError):
        EncoderRowClassifier(base, **TINY).fit([row(0, "Net sales")], ["revenue"])


def test_fit_frees_gradients_and_release_drops_the_model(base):
    rows, items = data()
    model = EncoderRowClassifier(base, **TINY).fit(rows, items)
    assert all(p.grad is None for p in model.encoder.parameters())
    release_memory()  # callable at any time
    model.release()
    assert model.encoder is None and model.head is None


def test_save_and_load_round_trip(base, tmp_path):
    rows, items = data()
    model = EncoderRowClassifier(base, **TINY).fit(rows, items)
    path = model.save(tmp_path / "m3.safetensors")
    params = {k: v for k, v in TINY.items() if k != "device"}
    loaded = EncoderRowClassifier.load(base, path, model.classes, params, device="cpu")
    assert loaded.classes == model.classes
    assert np.allclose(loaded.probabilities(rows), model.probabilities(rows), atol=1e-6)
    assert np.allclose(loaded.logits(rows), model.logits(rows), atol=1e-5)
