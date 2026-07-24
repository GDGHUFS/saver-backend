from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Literal

from src.search.engine.schema import AnswerNugget, EvidenceEdge, SearchCandidate


def extract_nuggets(candidate: SearchCandidate) -> list[AnswerNugget]:
    text = candidate.content or candidate.snippet
    spans = [match for match in re.finditer(r"[^.!?。\n]+[.!?。]?", text) if match.group().strip()]
    nuggets = []
    for index, match in enumerate(spans):
        value = match.group().strip()
        if len(value) < 15:
            continue
        nugget_type = next((
            field for field in candidate.structured_fields
            if field.replace("_", " ") in value.casefold()
        ), "summary")
        nuggets.append(AnswerNugget(
            f"{candidate.id}:n{index}", candidate.id, value, nugget_type,
            source_span=match.span(),
        ))
    return nuggets


def evidence_edges(candidate: SearchCandidate) -> list[EvidenceEdge]:
    fields = candidate.structured_fields
    if {"subject", "predicate", "object"} <= fields.keys():
        return [EvidenceEdge(
            str(fields["subject"]), str(fields["predicate"]), str(fields["object"]), candidate.url)]
    mapping = {
        "region": "LOCATED_IN", "administering_organization": "ADMINISTERED_BY",
        "related_law": "RELATED_TO", "benefit": "HAS_VALUE",
        "application_period": "VALID_DURING",
    }
    return [
        EvidenceEdge(candidate.title, predicate, str(fields[field]), candidate.url)
        for field, predicate in mapping.items() if field in fields
    ]


TruthValue = Literal["true", "false", "unknown", "not_evaluated"]


def evaluate_eligibility(
    candidate: SearchCandidate, constraints: dict[str, Any]
) -> dict[str, Any]:
    checks = []
    unknown = []
    fields = candidate.structured_fields
    if "age" in constraints:
        age_range = fields.get("eligible_age")
        if not isinstance(age_range, dict):
            unknown.append("age")
        else:
            checks.append((
                "age",
                age_range.get("min", 0) <= constraints["age"] <= age_range.get("max", 10_000),
            ))
    for key in ("region", "employment_status", "business_type"):
        if key in constraints:
            if key not in fields:
                unknown.append(key)
            else:
                checks.append((key, fields[key] == constraints[key]))
    for key in ("income", "income_ratio"):
        if key in constraints:
            if key not in fields:
                unknown.append(key)
            else:
                checks.append((key, fields[key] == constraints[key]))
    if "business_age_years" in constraints:
        maximum = fields.get("business_age_years_max")
        if maximum is None:
            unknown.append("business_age_years")
        else:
            checks.append(("business_age_years", constraints["business_age_years"] <= maximum))
    status: TruthValue = (
        "false" if any(not value for _, value in checks)
        else "unknown" if unknown else "true"
    )
    return {
        "status": status,
        "justification": {key: value for key, value in checks},
        "unknown_conditions": unknown,
        "counterfactual_conditions": [key for key, value in checks if not value],
    }


def fuse_claims(
    claims: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        grouped[claim["field"]].append(claim)
    resolved, conflicts = [], []
    for field, values in grouped.items():
        distinct = {str(item["value"]) for item in values}
        winner = max(
            values, key=lambda item: item.get("authority", 0) * item.get("freshness", 0))
        resolved.append(winner)
        if len(distinct) > 1:
            conflicts.append({"field": field, "claims": values, "selected": winner})
    return resolved, conflicts
