from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.search.engine.schema import QueryAnalysis


@dataclass(frozen=True)
class ProviderQueryPlan:
    provider_id: str
    queries: tuple[str, ...]
    filters: dict[str, Any]


def build_provider_query_plan(analysis: QueryAnalysis, provider_id: str) -> ProviderQueryPlan:
    data = analysis.domain_extensions.get("analysis", {})
    plan = next((item for item in data.get("provider_plans", []) if item["provider_id"] == provider_id), None)
    if plan is None:
        queries = tuple(dict.fromkeys(q.strip() for q in analysis.search_queries if _valid_query(q)))[:3]
        return ProviderQueryPlan(provider_id, queries or (analysis.normalized_query,), {})
    queries = tuple(dict.fromkeys(query.strip() for query in plan["queries"] if _valid_query(query)))[:3]
    return ProviderQueryPlan(provider_id, queries, dict(plan["filters"]))


def _valid_query(value: str) -> bool:
    query = value.strip()
    return bool(query) and len(query) <= 30 and not re.fullmatch(r"[a-z][a-z0-9_]*", query)
