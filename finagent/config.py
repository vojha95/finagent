"""Configuration loaders (blueprint Appendix A: concept map; Phase 4a statement-set configs)."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
CONCEPT_MAP_PATH = CONFIG_DIR / "concept_map.yaml"

PeriodType = Literal["duration", "instant"]
SignRule = Literal["positive", "either"]

_PERIOD_TYPES = ("duration", "instant")
_SIGN_RULES = ("positive", "either")


class ConfigError(ValueError):
    """Raised when a configuration file is malformed."""


@dataclass(frozen=True)
class LineItemSpec:
    item_id: str
    label: str
    statement: str
    period_type: PeriodType
    sign: SignRule
    core: bool
    concepts: tuple[str, ...]


def _spec(raw: object) -> LineItemSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"Concept map item must be a mapping: {raw!r}")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise ConfigError(f"Concept map item without an id: {raw!r}")
    for key in ("label", "statement", "period_type", "sign", "core", "concepts"):
        if key not in raw:
            raise ConfigError(f"Item {item_id} is missing {key}")
    if raw["period_type"] not in _PERIOD_TYPES:
        raise ConfigError(f"Item {item_id}: period_type must be one of {_PERIOD_TYPES}")
    if raw["sign"] not in _SIGN_RULES:
        raise ConfigError(f"Item {item_id}: sign must be one of {_SIGN_RULES}")
    if not isinstance(raw["core"], bool):
        raise ConfigError(f"Item {item_id}: core must be true or false")
    concepts = raw["concepts"]
    if (
        not isinstance(concepts, list)
        or not concepts
        or not all(isinstance(c, str) and ":" in c for c in concepts)
    ):
        raise ConfigError(f"Item {item_id}: concepts must be a non-empty list of prefix:Name")
    if len(set(concepts)) != len(concepts):
        raise ConfigError(f"Item {item_id} lists a concept twice")
    return LineItemSpec(
        item_id=item_id,
        label=str(raw["label"]),
        statement=str(raw["statement"]),
        period_type=raw["period_type"],
        sign=raw["sign"],
        core=raw["core"],
        concepts=tuple(concepts),
    )


def load_concept_map(path: Path | str = CONCEPT_MAP_PATH) -> dict[str, LineItemSpec]:
    """Line items keyed by id, in file order. A concept may belong to only one item."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load concept map: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ConfigError("Concept map must have a top-level 'items' list")
    specs: dict[str, LineItemSpec] = {}
    owner: dict[str, str] = {}
    for raw in data["items"]:
        spec = _spec(raw)
        if spec.item_id in specs:
            raise ConfigError(f"Duplicate item id: {spec.item_id}")
        for concept in spec.concepts:
            if concept in owner:
                raise ConfigError(
                    f"Concept {concept} is listed under both {owner[concept]} and {spec.item_id}"
                )
            owner[concept] = spec.item_id
        specs[spec.item_id] = spec
    return specs


def core_items(specs: dict[str, LineItemSpec]) -> list[LineItemSpec]:
    """The items flagged core: true, in file order."""
    return [spec for spec in specs.values() if spec.core]


UNIVERSE_PATH = CONFIG_DIR / "universe.yaml"
SPLITS = ("train", "validation", "held_out")


def load_universe(path: Path = UNIVERSE_PATH) -> dict[str, str]:
    """Ticker -> split (blueprint 9.2). Rejects unknown splits and a ticker in two splits."""
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict) or set(raw) - set(SPLITS):
        raise ConfigError(f"Universe must map {SPLITS} to ticker lists")
    universe: dict[str, str] = {}
    for split in SPLITS:
        tickers = raw.get(split) or []
        if not isinstance(tickers, list) or not all(isinstance(t, str) and t for t in tickers):
            raise ConfigError(f"Universe split {split} must be a list of tickers")
        for ticker in tickers:
            key = ticker.strip().upper()
            if key in universe:
                raise ConfigError(f"{key} is in both {universe[key]} and {split}")
            universe[key] = split
    return universe


HELD_OUT = "held_out"


class HeldOutError(ValueError):
    """A held-out company was requested without the explicit include-held-out flag (9.2).

    Held-out companies are scored once per model version (the final E1 run) and never used
    for debugging or tuning.
    """


def check_not_held_out(
    tickers: Iterable[str], universe: Mapping[str, str], include_held_out: bool
) -> None:
    """Raise HeldOutError if any ticker is held out and include_held_out is False."""
    if include_held_out:
        return
    held = sorted(t for t in (x.strip().upper() for x in tickers) if universe.get(t) == HELD_OUT)
    if held:
        raise HeldOutError(
            f"{', '.join(held)} held out; pass include_held_out (final E1 run only)"
        )


LABEL_RULES_PATH = CONFIG_DIR / "label_rules.yaml"
E1_SCORING_PATH = CONFIG_DIR / "e1_scoring.yaml"
_STATEMENTS = ("IS", "BS", "CF")


@dataclass(frozen=True)
class LabelRule:
    """Relabel source_item -> target_item unless a later row on the statement carries one of
    unless_below (configs/label_rules.yaml; BC14)."""

    rule_id: str
    statement: str
    source_item: str
    target_item: str
    unless_below: tuple[str, ...]


@dataclass(frozen=True)
class Equivalence:
    """Two items E1 accepts for each other when the filing reports no NCI (BC13)."""

    items: tuple[str, str]
    nci_concepts: tuple[str, ...]
    period_type: str


def _yaml_list(path: Path | str, key: str) -> list:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise ConfigError(f"{path} must have a top-level '{key}' list")
    return data[key]


def _known_item(item: object, items: Mapping[str, LineItemSpec], where: str) -> str:
    if not isinstance(item, str) or item not in items:
        raise ConfigError(f"{where}: unknown item {item!r}")
    return item


def load_label_rules(
    path: Path | str = LABEL_RULES_PATH, concept_map: Mapping[str, LineItemSpec] | None = None
) -> list[LabelRule]:
    items = concept_map if concept_map is not None else load_concept_map()
    rules = []
    for raw in _yaml_list(path, "rules"):
        rule_id = raw.get("id")
        if not rule_id or raw.get("statement") not in _STATEMENTS:
            raise ConfigError(f"label rule {raw!r}: needs an id and a statement in {_STATEMENTS}")
        concepts = raw.get("unless_below") or []
        if not isinstance(concepts, list) or not all(isinstance(c, str) for c in concepts):
            raise ConfigError(f"label rule {rule_id}: unless_below must be a list of concepts")
        rules.append(LabelRule(
            rule_id, raw["statement"],
            _known_item(raw.get("source_item"), items, f"label rule {rule_id}"),
            _known_item(raw.get("target_item"), items, f"label rule {rule_id}"),
            tuple(concepts),
        ))
    if len({r.rule_id for r in rules}) != len(rules):
        raise ConfigError("label rule ids must be unique")
    return rules


def load_e1_equivalences(
    path: Path | str = E1_SCORING_PATH, concept_map: Mapping[str, LineItemSpec] | None = None
) -> list[Equivalence]:
    items = concept_map if concept_map is not None else load_concept_map()
    found = []
    for raw in _yaml_list(path, "equivalences"):
        pair = raw.get("items")
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            raise ConfigError(f"equivalence {raw!r}: items must be two different items")
        concepts = raw.get("nci_concepts")
        if not concepts or not all(isinstance(c, str) for c in concepts):
            raise ConfigError(f"equivalence {pair}: nci_concepts must be a non-empty list")
        if raw.get("period_type") not in _PERIOD_TYPES:
            raise ConfigError(f"equivalence {pair}: period_type must be one of {_PERIOD_TYPES}")
        found.append(Equivalence(
            (_known_item(pair[0], items, "equivalence"), _known_item(pair[1], items, "equivalence")),
            tuple(concepts), raw["period_type"],
        ))
    return found


# ---------------------------------------------------------------------------------------------
# Phase 4a: validated statement set (blueprint 8.9, Appendix A.2, Appendix B, 8.10 needs)
# ---------------------------------------------------------------------------------------------

MODEL_INPUTS_PATH = CONFIG_DIR / "model_inputs.yaml"
DERIVATIONS_PATH = CONFIG_DIR / "derivations.yaml"
IDENTITIES_PATH = CONFIG_DIR / "identities.yaml"
FORECAST_INPUTS_PATH = CONFIG_DIR / "forecast_inputs.yaml"

InputPeriod = Literal["duration", "instant", "opening_instant"]
Tier = Literal["required", "optional"]
_INPUT_PERIODS = ("duration", "instant", "opening_instant")
_TIERS = ("required", "optional")

Term = tuple[int, str]  # (+1 or -1, metric id)


def parse_term(raw: object, where: str) -> Term:
    """'+revenue' -> (1, 'revenue'); '-cost_of_revenue' -> (-1, 'cost_of_revenue')."""
    if not isinstance(raw, str) or len(raw) < 2 or raw[0] not in "+-":
        raise ConfigError(f"{where}: a term must be '+id' or '-id', got {raw!r}")
    return (1 if raw[0] == "+" else -1), raw[1:]


def _terms(raw: object, where: str, allow_empty: bool = False) -> tuple[Term, ...]:
    if raw is None and allow_empty:
        return ()
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise ConfigError(f"{where}: terms must be a list of '+id' / '-id'")
    return tuple(parse_term(t, where) for t in raw)


def _load_yaml(path: Path | str) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot load {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    return data


@dataclass(frozen=True)
class ModelInputSpec:
    """One A.2 model-input fact (configs/model_inputs.yaml).

    is_item: the entry references a canonical item (dual-channel value); otherwise the fact is
    read from the tag channel for `period`, with `dimensions` if given.
    """

    input_id: str
    label: str
    is_item: bool
    concepts: tuple[str, ...]
    period: InputPeriod
    dimensions: tuple[tuple[str, str], ...]
    sign: SignRule
    tier: Tier
    substitute_item: str | None = None
    substitute_basis: str | None = None

    @property
    def period_type(self) -> PeriodType:
        return "duration" if self.period == "duration" else "instant"

    def line_item_spec(self) -> LineItemSpec:
        """The entry as a LineItemSpec, so the tag channel can read it."""
        return LineItemSpec(self.input_id, self.label, "NOTE", self.period_type, self.sign,
                            False, self.concepts)


@dataclass(frozen=True)
class ModelInputs:
    inputs: dict[str, ModelInputSpec]
    finance_lease_concepts: frozenset[str]


def load_model_inputs(
    path: Path | str = MODEL_INPUTS_PATH, concept_map: Mapping[str, LineItemSpec] | None = None
) -> ModelInputs:
    items = concept_map if concept_map is not None else load_concept_map()
    data = _load_yaml(path)
    raw_inputs = data.get("inputs")
    if not isinstance(raw_inputs, list):
        raise ConfigError(f"{path} must have an 'inputs' list")
    inputs: dict[str, ModelInputSpec] = {}
    for raw in raw_inputs:
        if not isinstance(raw, dict):
            raise ConfigError(f"model input must be a mapping: {raw!r}")
        tier = raw.get("tier")
        if tier not in _TIERS:
            raise ConfigError(f"model input {raw!r}: tier must be one of {_TIERS}")
        if "item" in raw:
            item = _known_item(raw["item"], items, "model input")
            spec = items[item]
            entry = ModelInputSpec(item, spec.label, True, spec.concepts, spec.period_type, (),
                                   spec.sign, tier)
        else:
            input_id = raw.get("id")
            if not isinstance(input_id, str) or not input_id:
                raise ConfigError(f"model input without an id: {raw!r}")
            if input_id in items:
                raise ConfigError(f"model input {input_id} is a canonical item: use 'item:'")
            concepts = raw.get("concepts")
            if not isinstance(concepts, list) or not concepts or \
                    not all(isinstance(c, str) and ":" in c for c in concepts):
                raise ConfigError(f"model input {input_id}: concepts must be prefix:Name list")
            if raw.get("period") not in _INPUT_PERIODS:
                raise ConfigError(f"model input {input_id}: period must be one of {_INPUT_PERIODS}")
            if raw.get("sign") not in _SIGN_RULES:
                raise ConfigError(f"model input {input_id}: sign must be one of {_SIGN_RULES}")
            dims = raw.get("dimensions") or {}
            if not isinstance(dims, dict) or not all(
                    isinstance(k, str) and ":" in k and isinstance(v, str) and ":" in v
                    for k, v in dims.items()):
                raise ConfigError(f"model input {input_id}: dimensions must map axis -> member")
            substitute = raw.get("substitute")
            sub_item = sub_basis = None
            if substitute is not None:
                if not isinstance(substitute, dict) or not substitute.get("basis"):
                    raise ConfigError(f"model input {input_id}: substitute needs item and basis")
                sub_item = _known_item(substitute.get("item"), items, f"model input {input_id}")
                sub_basis = str(substitute["basis"])
            entry = ModelInputSpec(input_id, str(raw.get("label", input_id)), False,
                                   tuple(concepts), raw["period"], tuple(sorted(dims.items())),
                                   raw["sign"], tier, sub_item, sub_basis)
        if entry.input_id in inputs:
            raise ConfigError(f"duplicate model input {entry.input_id}")
        inputs[entry.input_id] = entry
    lease = data.get("concepts_including_finance_leases") or []
    if not isinstance(lease, list) or not all(isinstance(c, str) and ":" in c for c in lease):
        raise ConfigError("concepts_including_finance_leases must be a list of concepts")
    return ModelInputs(inputs, frozenset(lease))


DerivationKind = Literal["formula", "face_block_sum"]


@dataclass(frozen=True)
class DerivationSpec:
    derivation_id: str
    kind: DerivationKind
    terms: tuple[Term, ...] = ()
    optional: tuple[Term, ...] = ()
    unless_presented: bool = False
    statement: str | None = None
    after_item: str | None = None
    stop_patterns: tuple[re.Pattern, ...] = ()
    flag_finance_leases: bool = False
    include_anchor: bool = False
    # Computed only when none of these metrics is presented (a fallback never overwrites a
    # presented line, e.g. the EBIT proxy and operating income, #68).
    when_not_presented: tuple[str, ...] = ()
    # Flag recorded on the result (and as its basis), and carried by every derivation using it.
    flag: str | None = None


@dataclass(frozen=True)
class Derivations:
    bases: dict[str, tuple[str, ...]]  # basis id -> alternatives, first available wins
    rules: tuple[DerivationSpec, ...]
    # Bases that never fall past an alternative read as DISAGREE (DISAGREE never falls back to
    # another value, 8.7): the basis is then None.
    stop_at_disagree: frozenset[str] = frozenset()


def _check_known(metric: str, known: set[str], where: str) -> None:
    if metric not in known:
        raise ConfigError(f"{where}: unknown metric {metric!r}")


def load_derivations(
    path: Path | str = DERIVATIONS_PATH,
    concept_map: Mapping[str, LineItemSpec] | None = None,
    model_inputs: ModelInputs | None = None,
) -> Derivations:
    items = concept_map if concept_map is not None else load_concept_map()
    inputs = model_inputs if model_inputs is not None else load_model_inputs(concept_map=items)
    data = _load_yaml(path)
    reported = set(items) | set(inputs.inputs)
    rule_ids = {r.get("id") for r in data.get("derivations") or [] if isinstance(r, dict)}
    bases: dict[str, tuple[str, ...]] = {}
    stopping: set[str] = set()
    for basis_id, raw in (data.get("bases") or {}).items():
        alternatives = (raw or {}).get("first_available")
        if not isinstance(alternatives, list) or not alternatives:
            raise ConfigError(f"basis {basis_id}: first_available must be a non-empty list")
        for alternative in alternatives:  # a reported metric or a derivation (e.g. ebit_proxy)
            _check_known(alternative, reported | rule_ids, f"basis {basis_id}")
        if basis_id in reported:
            raise ConfigError(f"basis {basis_id} clashes with a reported metric")
        bases[basis_id] = tuple(alternatives)
        if (raw or {}).get("stop_at_disagree"):
            stopping.add(basis_id)
    known = reported | set(bases)
    rules: list[DerivationSpec] = []
    for raw in data.get("derivations") or []:
        rule_id = raw.get("id")
        kind = raw.get("kind")
        where = f"derivation {rule_id}"
        if not isinstance(rule_id, str) or kind not in ("formula", "face_block_sum"):
            raise ConfigError(f"{where}: needs an id and kind formula or face_block_sum")
        unless = bool(raw.get("unless_presented", False))
        if rule_id in reported and not unless:
            raise ConfigError(f"{where}: a reported metric may only be derived unless_presented")
        if any(r.derivation_id == rule_id for r in rules):
            raise ConfigError(f"{where}: duplicate id")
        if kind == "formula":
            terms = _terms(raw.get("terms"), where)
            optional = _terms(raw.get("optional"), where, allow_empty=True)
            for _, metric in terms + optional:
                if metric == rule_id:
                    raise ConfigError(f"{where}: a derivation cannot use itself")
                _check_known(metric, known, where)
            absent = tuple(raw.get("when_not_presented") or [])
            for metric in absent:
                _check_known(metric, reported, where)
            flag = raw.get("flag")
            if flag is not None and not isinstance(flag, str):
                raise ConfigError(f"{where}: flag must be a string")
            rules.append(DerivationSpec(rule_id, kind, terms, optional, unless,
                                        flag_finance_leases=bool(raw.get("flag_finance_leases")),
                                        when_not_presented=absent, flag=flag))
        else:
            statement = raw.get("statement")
            if statement not in _STATEMENTS:
                raise ConfigError(f"{where}: statement must be one of {_STATEMENTS}")
            after = _known_item(raw.get("after_item"), items, where)
            patterns = raw.get("stop_patterns")
            if not isinstance(patterns, list) or not patterns:
                raise ConfigError(f"{where}: stop_patterns must be a non-empty list")
            try:
                compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
            except re.error as exc:
                raise ConfigError(f"{where}: bad stop pattern: {exc}") from exc
            rules.append(DerivationSpec(rule_id, kind, unless_presented=unless,
                                        statement=statement, after_item=after,
                                        stop_patterns=compiled,
                                        include_anchor=bool(raw.get("include_anchor", False))))
        known.add(rule_id)
    return Derivations(bases, tuple(rules), frozenset(stopping))


IdentityKind = Literal["sum", "cf_top_row", "ordering", "prior_year", "sanity"]
_IDENTITY_KINDS = ("sum", "cf_top_row", "ordering", "prior_year", "sanity")
_SANITY_RULES = {"positive": 1, "non_negative": 1, "less_than": 2}


@dataclass(frozen=True)
class NciCondition:
    """The alternative applies only when the filing reports no NCI for the period (BC13)."""

    concepts: tuple[str, ...]
    period_type: PeriodType


@dataclass(frozen=True)
class OperandAlternative:
    terms: tuple[Term, ...]
    when_no_nci: NciCondition | None = None


@dataclass(frozen=True)
class OptionalTerm:
    term: Term
    when_basis: tuple[tuple[str, str], ...] = ()  # (basis id, chosen metric) that must hold
    on_face: bool = False  # counts only when shown as its own face row (display_check MATCH)


@dataclass(frozen=True)
class SanityCheck:
    rule: str  # positive | non_negative | less_than
    terms: tuple[str, ...]


@dataclass(frozen=True)
class IdentitySpec:
    identity_id: str
    label: str
    kind: IdentityKind
    tolerance: Literal["exact", "default"]
    terms: tuple[Term, ...] = ()
    optional: tuple[OptionalTerm, ...] = ()
    variants: tuple[tuple[Term, ...], ...] = ()
    requires_presented: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()
    statement: str | None = None
    items: tuple[str, ...] = ()
    lower: tuple[str, ...] = ()
    middle: str | None = None
    upper: str | None = None
    checks: tuple[SanityCheck, ...] = ()
    # Status a failed check is reported with: FAIL, or REVIEW (shown with its residual, blocks
    # nothing; Appendix B I11 "or REVIEW with residual").
    on_failure: Literal["FAIL", "REVIEW"] = "FAIL"
    # Per variant (aligned with variants): metrics that must be presented, never derived, for
    # the variant to be computed (#74: I4 variant 2 needs a gross profit line on the face).
    variant_requires: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class IdentityLibrary:
    operands: dict[str, tuple[OperandAlternative, ...]]
    identities: tuple[IdentitySpec, ...]


def load_identities(
    path: Path | str = IDENTITIES_PATH,
    concept_map: Mapping[str, LineItemSpec] | None = None,
    model_inputs: ModelInputs | None = None,
    derivations: Derivations | None = None,
) -> IdentityLibrary:
    items = concept_map if concept_map is not None else load_concept_map()
    inputs = model_inputs if model_inputs is not None else load_model_inputs(concept_map=items)
    derived = derivations if derivations is not None else load_derivations(
        concept_map=items, model_inputs=inputs)
    known = set(items) | set(inputs.inputs) | set(derived.bases) | \
        {r.derivation_id for r in derived.rules}
    data = _load_yaml(path)
    operands: dict[str, tuple[OperandAlternative, ...]] = {}
    for operand_id, raw in (data.get("operands") or {}).items():
        where = f"operand {operand_id}"
        if operand_id in known:
            raise ConfigError(f"{where} clashes with a metric")
        alternatives = []
        for alt in (raw or {}).get("first_of") or []:
            terms = _terms(alt.get("terms"), where)
            for _, metric in terms:
                _check_known(metric, known, where)
            condition = alt.get("when_no_nci")
            nci = None
            if condition is not None:
                concepts = condition.get("concepts")
                if not concepts or condition.get("period") not in _PERIOD_TYPES:
                    raise ConfigError(f"{where}: when_no_nci needs concepts and a period")
                nci = NciCondition(tuple(concepts), condition["period"])
            alternatives.append(OperandAlternative(terms, nci))
        if not alternatives:
            raise ConfigError(f"{where}: first_of must list alternatives")
        operands[operand_id] = tuple(alternatives)
    known |= set(operands)
    identities: list[IdentitySpec] = []
    for raw in data.get("identities") or []:
        identity_id = raw.get("id")
        kind = raw.get("kind")
        where = f"identity {identity_id}"
        if not identity_id or kind not in _IDENTITY_KINDS:
            raise ConfigError(f"{where}: needs an id and a kind in {_IDENTITY_KINDS}")
        tolerance = raw.get("tolerance")
        if tolerance not in ("exact", "default"):
            raise ConfigError(f"{where}: tolerance must be exact or default")
        terms = _terms(raw.get("terms"), where, allow_empty=True)
        raw_variants = raw.get("variants") or []
        variants = tuple(_terms(v["terms"] if isinstance(v, dict) else v, where)
                         for v in raw_variants)
        variant_requires = tuple(tuple(v.get("requires_presented") or []) if isinstance(v, dict)
                                 else () for v in raw_variants)
        optional = []
        for opt in raw.get("optional") or []:
            when = opt.get("when_basis") or {}
            for basis, metric in when.items():
                if basis not in derived.bases or metric not in derived.bases[basis]:
                    raise ConfigError(f"{where}: when_basis {basis}={metric} is not a basis choice")
            optional.append(OptionalTerm(parse_term(opt.get("term"), where),
                                         tuple(sorted(when.items())), bool(opt.get("on_face"))))
        names = [m for _, m in terms] + [m for v in variants for _, m in v] + \
            [o.term[1] for o in optional]
        names += list(raw.get("requires_presented") or []) + list(raw.get("requires_any") or [])
        names += [m for req in variant_requires for m in req]
        names += list(raw.get("items") or []) + list(raw.get("lower") or [])
        names += [m for m in (raw.get("middle"), raw.get("upper")) if m]
        checks = []
        for check in raw.get("checks") or []:
            rule = check.get("rule")
            check_terms = tuple(check.get("terms") or [])
            if rule not in _SANITY_RULES or len(check_terms) != _SANITY_RULES[rule]:
                raise ConfigError(f"{where}: bad sanity check {check!r}")
            names += list(check_terms)
            checks.append(SanityCheck(rule, check_terms))
        for metric in names:
            _check_known(metric, known, where)
        if kind == "sum" and not (terms or variants):
            raise ConfigError(f"{where}: a sum identity needs terms or variants")
        if kind == "ordering" and not (raw.get("middle") and raw.get("upper")):
            raise ConfigError(f"{where}: an ordering needs middle and upper")
        if kind == "cf_top_row" and (raw.get("statement") not in _STATEMENTS or not raw.get("items")):
            raise ConfigError(f"{where}: cf_top_row needs a statement and items")
        on_failure = raw.get("on_failure", "FAIL")
        if on_failure not in ("FAIL", "REVIEW"):
            raise ConfigError(f"{where}: on_failure must be FAIL or REVIEW")
        identities.append(IdentitySpec(
            identity_id, str(raw.get("label", identity_id)), kind, tolerance, terms,
            tuple(optional), variants, tuple(raw.get("requires_presented") or []),
            tuple(raw.get("requires_any") or []), raw.get("statement"),
            tuple(raw.get("items") or []), tuple(raw.get("lower") or []), raw.get("middle"),
            raw.get("upper"), tuple(checks), on_failure,
            variant_requires if any(variant_requires) else ()))
    if len({i.identity_id for i in identities}) != len(identities):
        raise ConfigError("identity ids must be unique")
    return IdentityLibrary(operands, tuple(identities))


@dataclass(frozen=True)
class ForecastNeed:
    need_id: str
    tier: Tier
    years: int
    why: str


def load_forecast_inputs(
    path: Path | str = FORECAST_INPUTS_PATH,
    concept_map: Mapping[str, LineItemSpec] | None = None,
    model_inputs: ModelInputs | None = None,
    derivations: Derivations | None = None,
) -> list[ForecastNeed]:
    items = concept_map if concept_map is not None else load_concept_map()
    inputs = model_inputs if model_inputs is not None else load_model_inputs(concept_map=items)
    derived = derivations if derivations is not None else load_derivations(
        concept_map=items, model_inputs=inputs)
    known = set(items) | set(inputs.inputs) | set(derived.bases) | \
        {r.derivation_id for r in derived.rules}
    needs = []
    for raw in _yaml_list(path, "needs"):
        need_id = raw.get("id")
        _check_known(need_id, known, "forecast need")
        years = raw.get("years")
        if raw.get("tier") not in _TIERS or not isinstance(years, int) or years < 1:
            raise ConfigError(f"forecast need {need_id}: tier in {_TIERS} and years >= 1")
        needs.append(ForecastNeed(need_id, raw["tier"], years, str(raw.get("why", ""))))
    if len({n.need_id for n in needs}) != len(needs):
        raise ConfigError("forecast needs must be unique")
    return needs


FORECAST_PATH = CONFIG_DIR / "forecast.yaml"
WorkingCapitalBasis = Literal["latest", "median_3y"]


@dataclass(frozen=True)
class ForecastConfig:
    """Caps, bounds, default rates and switches of the integrated forecast (configs/forecast.yaml)."""

    horizon_years: int
    growth_years: int
    growth_cap: tuple[float, float]
    tax_bounds: tuple[float, float]
    statutory_tax_rate: float
    default_debt_rate: float
    lease_rate: float
    median_years: int
    min_median_years: int
    tolerance: float
    working_capital_basis: WorkingCapitalBasis = "latest"
    refinance_maturities: bool = False


def _pair(value: Any, where: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2 or float(value[0]) > float(value[1]):
        raise ConfigError(f"forecast {where}: needs [low, high]")
    return float(value[0]), float(value[1])


def load_forecast_config(path: Path | str = FORECAST_PATH, **overrides: Any) -> ForecastConfig:
    """The forecast configuration; overrides replace switches (e.g. refinance_maturities=True)."""
    data = _load_yaml(path)
    try:
        switches = dict(data.get("switches") or {})
        switches.update(overrides)
        basis = switches.get("working_capital_basis", "latest")
        if basis not in ("latest", "median_3y"):
            raise ConfigError("forecast working_capital_basis must be latest or median_3y")
        config = ForecastConfig(
            horizon_years=int(data["horizon_years"]),
            growth_years=int(data["revenue_growth"]["years"]),
            growth_cap=_pair(data["revenue_growth"]["cap"], "revenue_growth.cap"),
            tax_bounds=_pair(data["tax_rate"]["bounds"], "tax_rate.bounds"),
            statutory_tax_rate=float(data["tax_rate"]["statutory_fallback"]),
            default_debt_rate=float(data["interest"]["default_rate"]),
            lease_rate=float(data["interest"]["lease_rate"]),
            median_years=int(data["history"]["median_years"]),
            min_median_years=int(data["history"]["min_years"]),
            tolerance=float(data["tolerance"]),
            working_capital_basis=basis,
            refinance_maturities=bool(switches.get("refinance_maturities", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"forecast config: {exc}") from exc
    if config.horizon_years < 1 or config.growth_years < 2:
        raise ConfigError("forecast: horizon_years >= 1 and revenue_growth.years >= 2")
    if not 1 <= config.min_median_years <= config.median_years:
        raise ConfigError("forecast: 1 <= history.min_years <= history.median_years")
    return config


SCENARIOS_PATH = CONFIG_DIR / "scenarios.yaml"
SCENARIO_FIELDS = ("revenue_growth_shift", "cogs_share_shift", "dso_days", "dio_days",
                   "dpo_days", "funded_debt_rate_shift", "capex_multiplier",
                   "securities_haircut", "minimum_cash_floor")


@dataclass(frozen=True)
class ScenarioSpec:
    """One scenario of blueprint 8.11 (configs/scenarios.yaml); shocks are decimals."""

    name: str
    revenue_growth_shift: float
    cogs_share_shift: float
    dso_days: float
    dio_days: float
    dpo_days: float
    funded_debt_rate_shift: float
    capex_multiplier: float
    securities_haircut: float
    minimum_cash_floor: float
    days_in_year: int = 365

    @property
    def is_neutral(self) -> bool:
        """No shock at all: the scenario is the 4b forecast itself."""
        return (self.revenue_growth_shift == self.cogs_share_shift == self.dso_days
                == self.dio_days == self.dpo_days == self.funded_debt_rate_shift == 0
                and self.capex_multiplier == 1)


BASE_SCENARIO = ScenarioSpec("Base", 0.0, 0.0, 0, 0, 0, 0.0, 1.0, 0.0, 0)


def load_scenarios(path: Path | str = SCENARIOS_PATH) -> tuple[ScenarioSpec, ...]:
    """The scenario set, in file order. Base must exist and carry no driver shock."""
    data = _load_yaml(path)
    try:
        days = int(data.get("days_in_year", 365))
        found = []
        for name, raw in (data["scenarios"] or {}).items():
            missing = [f for f in SCENARIO_FIELDS if f not in raw]
            if missing:
                raise ConfigError(f"scenario {name}: missing {missing}")
            extra = sorted(set(raw) - set(SCENARIO_FIELDS))
            if extra:
                raise ConfigError(f"scenario {name}: unknown fields {extra}")
            found.append(ScenarioSpec(str(name), *(float(raw[f]) for f in SCENARIO_FIELDS),
                                      days_in_year=days))
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"scenarios config: {exc}") from exc
    if days <= 0:
        raise ConfigError("scenarios: days_in_year must be positive")
    for spec in found:
        if not 0 <= spec.securities_haircut <= 1:
            raise ConfigError(f"scenario {spec.name}: securities_haircut must be in [0, 1]")
        if spec.capex_multiplier < 0 or spec.minimum_cash_floor < 0:
            raise ConfigError(f"scenario {spec.name}: capex_multiplier and "
                              "minimum_cash_floor must be >= 0")
    base = [s for s in found if s.name == "Base"]
    if not base or not base[0].is_neutral:
        raise ConfigError("scenarios: a Base scenario with no driver shock is required")
    return tuple(found)
