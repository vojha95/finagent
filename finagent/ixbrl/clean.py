"""Clean document and leakage check (blueprint 8.2).

The clean document is what an analyst sees: every ix:* wrapper is removed with its text kept,
ix:header and ix:hidden are deleted, and no XBRL vocabulary survives. find_leaks is the check.
"""

import re
from collections.abc import Iterable

from lxml import etree

from finagent.ixbrl.tagstore import IX, TagFact, parse_document

# Inline-style declarations the SEC uses to point visible elements at tags, e.g. -sec-ix-hidden:f-480.
_SEC_IX_STYLE = re.compile(r"-sec-ix-[\w-]+\s*:[^;]*;?\s*")
_XMLNS_PREFIX = re.compile(rb"\bxmlns:([A-Za-z_][\w.-]*)\s*=")
# Fixed markers that only tagging produces.
_MARKERS = ("contextRef", "unitRef", "-sec-ix-", "xbrli")
# CamelCase parts: acronym runs ("EMEA" in EMEAMember), capitalised words, lowercase/digit runs.
_CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")


def clean_document(document: bytes) -> bytes:
    """Visible document: delete ix:header and ix:hidden, unwrap every other ix:* element."""
    root = parse_document(document)
    # with_tail=False keeps the text that follows each deleted element.
    etree.strip_elements(root, f"{{{IX}}}header", f"{{{IX}}}hidden", with_tail=False)
    etree.strip_tags(root, f"{{{IX}}}*")
    for element in root.iter():
        style = element.get("style") if isinstance(element.tag, str) else None
        if style and "-sec-ix-" in style:
            cleaned = _SEC_IX_STYLE.sub("", style).strip()
            if cleaned:
                element.set("style", cleaned)
            else:
                del element.attrib["style"]
    etree.cleanup_namespaces(root)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def declared_prefixes(document: bytes) -> set[str]:
    """Every namespace prefix declared in the original document (us-gaap, dei, amzn, ix, ...)."""
    return {m.decode() for m in _XMLNS_PREFIX.findall(document)}


def leak_terms(facts: Iterable[TagFact]) -> set[str]:
    """Concept, axis and member names from the tag store, as qualified names."""
    terms: set[str] = set()
    for fact in facts:
        terms.add(fact.concept)
        for axis, member in fact.dimensions.items():
            terms.update((axis, member))
    return terms


def ix_concept_names(document: bytes) -> set[str]:
    """Concept names on every ix:nonNumeric and ix:nonFraction (text blocks, dei facts, ...).

    The tag store holds only numeric facts; this covers the rest of the tagged vocabulary.
    """
    root = parse_document(document)
    return {
        name
        for tag in ("nonNumeric", "nonFraction")
        for element in root.iter(f"{{{IX}}}{tag}")
        if (name := element.get("name"))
    }


def is_camel_compound(name: str) -> bool:
    """True for multi-part CamelCase names (NetIncomeLoss, EMEAMember); False for 'Revenues'."""
    return bool(re.fullmatch(r"[A-Za-z0-9]+", name)) and len(_CAMEL_PART.findall(name)) >= 2


def _alternation(words: Iterable[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


def find_leaks(clean_html: bytes, terms: Iterable[str], prefixes: Iterable[str]) -> list[str]:
    """Every tagging artefact found in the clean document, as sorted 'rule: text' strings.

    Rules: a declared prefix followed by a name (ix:, us-gaap:, amzn:, ...); a qualified term;
    a CamelCase local name as a whole, case-sensitive token; contextRef, unitRef, -sec-ix-, xbrli.
    Single-word local names (Revenues, Goodwill) are not checked: they are ordinary visible text.
    """
    text = clean_html.decode("utf-8")
    qualified = set(terms)
    locals_ = {t.rsplit(":", 1)[-1] for t in qualified}
    rules: dict[str, str] = {
        "marker": _alternation(_MARKERS),
        "prefix": rf"(?<![\w.-])(?:{_alternation({*prefixes, 'ix'})}):(?=[A-Za-z_])",
    }
    if qualified:
        rules["qualified name"] = _alternation(qualified)
    camel = {n for n in locals_ if is_camel_compound(n)}
    if camel:
        rules["local name"] = rf"(?<![A-Za-z0-9])(?:{_alternation(camel)})(?![A-Za-z0-9])"
    leaks = {
        f"{rule}: {match.group(0)}"
        for rule, pattern in rules.items()
        for match in re.finditer(pattern, text)
    }
    return sorted(leaks)
