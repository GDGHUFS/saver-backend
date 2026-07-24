"""LLM-first query understanding."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from src.search.engine.schema import DomainScore, Entity, QueryAnalysis


ANALYSIS_SCHEMA = {
    "name": "policy_search_analysis", "strict": True,
    "schema": {"type": "object", "additionalProperties": False, "required": [
        "primary_intent", "domains", "requested_support_types", "explicitly_requested_support_types",
        "inferred_relevant_support_types", "user_conditions", "government_scopes",
        "requests_eligibility", "requests_multiple_results", "excluded_domains", "complexity", "confidence",
        "search_queries", "provider_filters", "provider_search_terms", "provider_plans",
        "is_personalized", "required_steps", "provider_targets",
    ], "properties": {
        "primary_intent": {"type": "string"},
        "domains": {"type": "array", "items": {"type": "string"}},
        "requested_support_types": {"type": "array", "items": {"type": "string"}},
        "explicitly_requested_support_types": {"type": "array", "items": {"type": "string"}},
        "inferred_relevant_support_types": {"type": "array", "items": {"type": "string"}},
        "user_conditions": {"type": "object", "additionalProperties": False,
            "required": ["age", "region", "employment_status", "household_type"],
            "properties": {
                "age": {"type": ["integer", "null"]},
                "region": {"type": "object", "additionalProperties": False,
                    "required": ["sido", "sigungu"], "properties": {
                        "sido": {"type": ["string", "null"]},
                        "sigungu": {"type": ["string", "null"]},
                    }},
                "employment_status": {"type": ["string", "null"]},
                "household_type": {"type": ["string", "null"]},
            }},
        "government_scopes": {"type": "array", "items": {"type": "string",
            "enum": ["central", "metropolitan", "municipal"]}},
        "requests_eligibility": {"type": "boolean"},
        "requests_multiple_results": {"type": "boolean"},
        "excluded_domains": {"type": "array", "items": {"type": "string"}},
        "complexity": {"type": "string", "enum": ["simple", "complex"]},
        "confidence": {"type": "number"},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "is_personalized": {"type": "boolean"},
        "required_steps": {"type": "array", "items": {"type": "string", "enum": [
            "query_expansion", "candidate_generation", "hybrid_ranking", "reranking",
            "answer_nugget_extraction", "knowledge_selection", "diversity_selection", "rule_reasoning",
        ]}},
        "provider_targets": {"type": "array", "items": {"type": "string",
            "enum": ["naver_web_search", "kakao_web_search"]}},
        "provider_search_terms": {"type": "array", "items": {"type": "string"}},
        "provider_plans": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["provider_id", "queries", "filters", "reason"],
            "properties": {
                "provider_id": {"type": "string", "enum": ["naver_web_search", "kakao_web_search"]},
                "queries": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "object", "additionalProperties": False,
                    "required": ["life_cycle", "interest_theme", "region"], "properties": {
                        "life_cycle": {"type": "array", "items": {"type": "string"}},
                        "interest_theme": {"type": "array", "items": {"type": "string"}},
                        "region": {"type": "object", "additionalProperties": False,
                            "required": ["sido", "sigungu"], "properties": {
                                "sido": {"type": ["string", "null"]},
                                "sigungu": {"type": ["string", "null"]},
                            }},
                    }},
                "reason": {"type": "string"},
            }}},
        "provider_filters": {"type": "object", "additionalProperties": False,
            "required": ["life_cycle", "interest_theme"], "properties": {
                "life_cycle": {"type": "array", "items": {"type": "string"}},
                "interest_theme": {"type": "array", "items": {"type": "string"}},
            }},
    }},
}


class OpenAIQueryAnalyzer:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self.client, self.model = client, model

    async def analyze(self, query: str) -> QueryAnalysis:
        completion = await self.client.chat.completions.create(
            model=self.model, temperature=0,
            response_format={"type": "json_schema", "json_schema": ANALYSIS_SCHEMA},
            messages=[{"role": "system", "content": (
                "Extract the user's intent into the supplied schema. Infer meaning from context, not isolated words. "
                "A personal condition is not automatically a requested support domain. Generate broad but relevant "
                "official-service search_queries. Decide is_personalized, required_steps, and provider_targets from "
                "the full meaning. Keep explicitly_requested_support_types separate from inferred_relevant_support_types. "
                "When the user asks which services they can receive, set rule_reasoning; when they ask for all or "
                "multiple services, set diversity_selection. Generate provider_plans directly; web search can use full "
                "natural-language queries. For general web retrieval target both naver_web_search and kakao_web_search. "
                "government_scopes contains only central, metropolitan, municipal; never place names."
            )}, {"role": "user", "content": query}],
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("LLM returned no query analysis")
        return self.from_payload(query, json.loads(content))

    @staticmethod
    def from_payload(query: str, payload: dict[str, Any]) -> QueryAnalysis:
        conditions = payload["user_conditions"]
        region = conditions["region"]
        constraints = {key: value for key, value in {
            "age": conditions["age"], "employment_status": conditions["employment_status"],
            "household_type": conditions["household_type"], "region": region["sido"], "district": region["sigungu"],
        }.items() if value is not None}
        domains = list(dict.fromkeys(payload["domains"])) or ["general"]
        scopes = set(payload.get("government_scopes", []))
        required_steps = set(payload["required_steps"])
        flags = {
            "is_simple_lookup": payload["complexity"] == "simple",
            "is_personalized_welfare": payload.get("is_personalized", False),
            "needs_diverse_results": "diversity_selection" in required_steps,
            "needs_rule_reasoning": "rule_reasoning" in required_steps,
            "needs_graph": False, "needs_conflict_resolution": False, "is_follow_up": False,
            "needs_change_detection": False,
            "needs_multi_provider_search": len(payload["provider_targets"]) > 1,
        }
        return QueryAnalysis(
            original_query=query, normalized_query=query,
            domains=[DomainScore(domain, float(payload["confidence"])) for domain in domains],
            query_type=payload["primary_intent"],
            answer_type="ranked_entity_list" if payload["requests_multiple_results"] else "direct_answer",
            entities=[Entity(value, value, name) for name, value in (
                ("sido", region["sido"]), ("sigungu", region["sigungu"])) if value],
            constraints=constraints,
            search_queries=list(dict.fromkeys(payload["search_queries"] or [query])),
            domain_extensions={"analysis": {
                "requested_support_types": payload.get("requested_support_types", []),
                "explicitly_requested_support_types": payload.get("explicitly_requested_support_types", []),
                "inferred_relevant_support_types": payload.get("inferred_relevant_support_types", []),
                "provider_search_terms": payload.get("provider_search_terms", []),
                "provider_plans": payload["provider_plans"],
                "government_scopes": sorted(scopes),
                "provider_filters": payload.get("provider_filters", {}),
                "provider_targets": payload["provider_targets"],
                "required_steps": payload["required_steps"],
            }},
            planner_flags=flags,
        )
