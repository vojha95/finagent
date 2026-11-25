"""Sentence splitting and chunks (finagent.retrieval.chunk; 8.14)."""

import hashlib
from dataclasses import replace

from retrieval_support import DOC

from finagent.config import load_retrieval_config
from finagent.ixbrl.tagstore import parse_document
from finagent.retrieval.chunk import chunk_document, chunk_id, chunk_section, split_sentences
from finagent.retrieval.sections import Block, detect_sections, document_blocks

CONFIG = load_retrieval_config()
ACC = "0000000001-26-000001"


def test_sentences_respect_abbreviations_initials_and_decimals():
    text = ("Acme Inc. sold 3.5 million units in the U.S. market. Sales rose, e.g. in Europe. "
            "J. Smith joined. \"Prices fell.\" Next sentence? Yes!")
    assert split_sentences(text, CONFIG.abbreviations) == [
        "Acme Inc. sold 3.5 million units in the U.S. market.", "Sales rose, e.g. in Europe.",
        "J. Smith joined.", "\"Prices fell.\"", "Next sentence?", "Yes!"]
    assert split_sentences("No stop at the end", CONFIG.abbreviations) == ["No stop at the end"]


def sentence(words: int) -> str:
    return " ".join(["Word"] + ["word"] * (words - 1)) + "."


def test_chunks_close_near_the_target_at_sentence_boundaries():
    blocks = [Block("text", " ".join([sentence(40)] * 5))]  # 200 words
    chunks = chunk_section(ACC, "ITEM_7", blocks, CONFIG)
    assert [c.words for c in chunks] == [160, 40]  # closed once >= 150 words
    assert all(c.text.endswith(".") for c in chunks)


def test_max_words_and_long_sentences():
    config = replace(CONFIG, target_words=1000, max_words=100)
    text = f"{sentence(60)} {sentence(60)}"
    chunks = chunk_section(ACC, "ITEM_7", [Block("text", text)], config)
    assert [c.words for c in chunks] == [60, 60]  # 120 would pass max_words
    long = [Block("text", sentence(300))]
    assert [c.words for c in chunk_section(ACC, "ITEM_7", long, config)] == \
        [300]  # one sentence is never cut


def test_tables_are_their_own_chunks_and_ids_are_deterministic():
    blocks = [Block("text", "First."), Block("table", "Revenue | 1,234"), Block("text", "Then.")]
    chunks = chunk_section(ACC, "ITEM_7", blocks, CONFIG)
    assert [(c.kind, c.text) for c in chunks] == [("text", "First."),
                                                  ("table", "Revenue | 1,234"),
                                                  ("text", "Then.")]
    assert [c.chunk_id for c in chunks] == [chunk_id(ACC, "ITEM_7", n) for n in range(3)]
    assert chunks[0].chunk_id == f"{ACC}:ITEM_7:0000"
    assert chunks[1].sha256 == hashlib.sha256(b"Revenue | 1,234").hexdigest()
    assert chunk_section(ACC, "ITEM_7", blocks, CONFIG) == chunks


def test_document_chunks_never_cross_a_section():
    blocks = document_blocks(parse_document(DOC.encode()), CONFIG)
    sections = detect_sections(blocks, CONFIG)
    chunks = chunk_document(ACC, blocks, sections, CONFIG)
    assert {c.section_id for c in chunks} == {s.section_id for s in sections.sections}
    note = [c for c in chunks if c.section_id == "ITEM_8/NOTE_02"]
    assert note and note[0].text.startswith("Note 2")
    assert all("Item 9" not in c.text for c in note)
