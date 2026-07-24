"""Explicit LLM-response fixtures; never infer intent from query text."""
from src.search.engine.analysis import OpenAIQueryAnalyzer


def analysis(query: str, **overrides):
    payload = {
        "primary_intent": "web_search", "domains": ["general"],
        "requested_support_types": [], "explicitly_requested_support_types": [],
        "inferred_relevant_support_types": [],
        "user_conditions": {"age": None, "region": {"sido": None, "sigungu": None}, "employment_status": None, "household_type": None},
        "government_scopes": [], "requests_eligibility": False,
        "requests_multiple_results": False, "complexity": "simple", "confidence": .9,
        "excluded_domains": [], "provider_filters": {"life_cycle": [], "interest_theme": []},
        "provider_search_terms": [], "is_personalized": False,
        "search_queries": [query], "provider_plans": [], "required_steps": [], "provider_targets": [],
    }
    payload.update(overrides)
    return OpenAIQueryAnalyzer.from_payload(query, payload)
