"""The prototype's exact-match regex grammar (M0; blueprint 8.15 step 3 baseline, step 4 fallback).

Ported from the October prototype's compile_question (sharp_agentic_planner.py), clause part
only. The clause is cleaned as the prototype did (issuer references and "for/in <year>"
removed, a leading "compare/show/investigate/check" or "check/assess/show" dropped) and must
then match one pattern in full:

    fact        [what was/is | give me] [the] [latest] [reported] <fact metric>
    statements  reconcile|validate [the] [latest] [three] [financial] statements
    forecast    [what was/is | give me] [the] forecast <forecast metric>
    liquidity   liquidity|cash headroom [under [the]] base|moderate|severe [scenario]
                base|moderate|severe [scenario] liquidity
    credit      [lead] [latest] [historical] <ratio> | credit ratios
    debt        [comprehensive] debt-service [coverage|evidence] | dscr

Two patterns in the same style are added for the Version 2 goal kinds the prototype did not
have (explain: "why did [the] <metric> fall|rise|...", risk: "[what are] [the] [main] risk
signals"). Fairness for E5 (#120): issuer resolution, clause splitting, conditions, years and
constraints are shared with the trained compiler, and each metric alternation is the metric's
canonical name in configs/intents.yaml plus the prototype's own phrases, so the grammar is not
penalised for the prototype's two hard-coded companies. No synonyms beyond those.
"""

import re
from collections.abc import Sequence

from finagent.config import IntentVocabulary, PlannerConfig
from finagent.planner.text import ISSUER_TOKEN, YEAR_TOKEN
from finagent.records import ClauseParse

ROUTE = "grammar"
# The prototype's own metric phrases (sharp_agentic_planner.py METRICS, FORECAST_METRICS and the
# credit alternation), mapped to Version 2 metric ids. "" is the kind's default set.
PROTOTYPE_PHRASES: dict[str, dict[str, str]] = {
    "fact": {"revenue": "revenue", "net income": "net_income",
             "operating cash flow": "operating_cash_flow", "operating income": "operating_income",
             "capex": "capital_expenditure", "capital expenditure": "capital_expenditure",
             "capital expenditures": "capital_expenditure"},
    "forecast": {"revenue": "revenue", "free cash flow": "free_cash_flow",
                 "fcf": "free_cash_flow", "closing cash": "cash", "cash": "cash"},
    "credit": {"current ratio": "current_ratio", "quick ratio": "quick_ratio",
               "credit ratios": "", "interest coverage": "ebit_to_gross_interest"},
}
LEAD = r"(?:(?:what (?:was|is)|give me) (?:the )?)?"
MOVES = r"(?:fall|fell|rise|rose|increase|decrease|decline|drop|grow|change)"


def phrases(kind: str, vocabulary: IntentVocabulary) -> dict[str, str]:
    """phrase -> metric id: each metric's canonical name, plus the prototype's phrases."""
    table = {ps[0]: metric for metric, ps in vocabulary.metrics.get(kind, {}).items()}
    table.update(PROTOTYPE_PHRASES.get(kind, {}))
    return table


def _alternation(table: dict[str, str]) -> str:
    return "(" + "|".join(re.escape(p) for p in sorted(table, key=len, reverse=True)) + ")"


def clean(body: str) -> str:
    """The prototype's clause cleaning, on a normalised clause with issuers and years masked."""
    text = re.sub(re.escape(ISSUER_TOKEN) + r"\s*(?:and|versus|vs\.?|with)\s*" +
                  re.escape(ISSUER_TOKEN), " ", body)
    text = re.sub(r"\b(?:for|of)\s+" + re.escape(ISSUER_TOKEN) + "|" + re.escape(ISSUER_TOKEN),
                  " ", text)
    text = re.sub(r"(?:(?:for|in)\s+)?" + re.escape(YEAR_TOKEN), " ", text)
    text = " ".join(text.split())
    text = re.sub(r"^(?:please\s+)?(?:compare|show|investigate|check)\b", "", text).strip()
    text = re.sub(r"^(?:check|assess|show)\s+", "", text)
    return " ".join(text.rstrip("?.!").split())


def match_kind(text: str, vocabulary: IntentVocabulary
               ) -> tuple[str, tuple[str, ...], str] | None:
    """(kind, metric ids, scenario) when the cleaned clause matches a pattern in full."""
    fact, forecast, credit = (phrases(k, vocabulary) for k in ("fact", "forecast", "credit"))
    m = re.fullmatch(LEAD + r"(?:latest )?(?:reported )?" + _alternation(fact), text)
    if m:
        return "fact", (fact[m[1]],), ""
    if re.fullmatch(r"(?:reconcile|validate) (?:the )?(?:latest )?(?:three )?(?:financial )?"
                    r"statements", text):
        return "statements", (), ""
    m = re.fullmatch(LEAD + r"forecast " + _alternation(forecast), text)
    if m:
        return "forecast", (forecast[m[1]],), ""
    m = re.fullmatch(r"(?:liquidity(?: under (?:the )?)?|cash headroom(?: under (?:the )?)?)"
                     r"\s*(base|moderate|severe)(?: scenario)?", text) or \
        re.fullmatch(r"(base|moderate|severe) (?:scenario )?liquidity", text)
    if m:
        return "liquidity", (), m[1].title()
    m = re.fullmatch(LEAD + r"(?:latest )?(?:historical )?" + _alternation(credit), text)
    if m:
        metric = credit[m[1]]
        return "credit", ((metric,) if metric else ()), ""
    if re.fullmatch(r"(?:comprehensive )?(?:debt[- ]service(?: coverage| evidence)?|dscr)", text):
        return "debt", (), ""
    m = re.fullmatch(r"why did (?:the )?" + _alternation(fact) + " " + MOVES, text)
    if m:
        return "explain", (fact[m[1]],), ""
    if re.fullmatch(r"(?:what are )?(?:the )?(?:main )?risk signals", text):
        return "risk", (), ""
    return None


def parse_clause(body: str, years: Sequence[int], conditional: bool,
                 vocabulary: IntentVocabulary, planner: PlannerConfig) -> ClauseParse | None:
    """The clause as the grammar reads it, or None when no pattern matches in full. As in the
    prototype, a year is the period of a fact, statements or credit goal and the target of a
    forecast or liquidity goal."""
    matched = match_kind(clean(body), vocabulary)
    if matched is None:
        return None
    kind, metrics, scenario = matched
    year = years[0] if years else None
    if planner.kinds[kind].target:
        return ClauseParse(body, kind, metrics, "latest", scenario, year, conditional, ROUTE)
    return ClauseParse(body, kind, metrics, year if year is not None else "latest", "", None,
                       conditional, ROUTE)
