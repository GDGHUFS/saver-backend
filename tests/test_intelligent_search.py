import unittest

from src.search.engine.config import SearchFeatures
from src.search.engine.pipeline import IntelligentSearchEngine
from src.search.engine.planner import RuleBasedPlanner
from src.search.engine.providers import ProviderRegistry, default_mock_providers
from src.search.engine.schema import ProviderDescriptor, ProviderType, SearchCandidate, SourceType
from tests.query_analysis_fixture import analysis


class FixtureAnalyzer:
    def __init__(self, responses): self.responses = responses
    async def analyze(self, query): return self.responses[query]


class PlannerTest(unittest.TestCase):
    def test_uses_llm_requested_reasoning_steps(self):
        parsed = analysis(
            "fixture-query",
            complexity="complex",
            requests_eligibility=True,
            is_personalized=True,
            required_steps=[
                "candidate_generation", "answer_nugget_extraction",
                "knowledge_selection", "diversity_selection", "rule_reasoning",
            ],
        )
        plan = RuleBasedPlanner(SearchFeatures()).plan(
            parsed, [item.descriptor for item in default_mock_providers()],
        )
        self.assertTrue(plan.use_rules)
        self.assertTrue(plan.needs_nuggets)
        self.assertTrue(plan.needs_knowledge_selection)
        self.assertIn("rule_reasoning", plan.steps)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_unavailable_requested_provider(self):
        query = "fixture-query"
        parsed = analysis(query, provider_targets=["naver_web_search"])
        response = await IntelligentSearchEngine(
            analyzer=FixtureAnalyzer({query: parsed}),
        ).search(query)
        self.assertEqual(response.execution_metadata["selected_provider_targets"], [])
        self.assertEqual(response.execution_metadata["unavailable_provider_targets"], ["naver_web_search"])

    async def test_provider_errors_include_safe_root_cause(self):
        class BrokenWeb:
            descriptor = ProviderDescriptor(
                "naver_web_search", ProviderType.WEB_SEARCH, frozenset(), frozenset(),
            )
            async def search(self, parsed, limit):
                try:
                    raise OSError("secret-value-must-not-be-logged")
                except OSError as exc:
                    raise RuntimeError("provider failed") from exc

        registry = ProviderRegistry()
        registry.register(BrokenWeb())
        query = "query-a"
        parsed = analysis(query, provider_targets=["naver_web_search"])
        response = await IntelligentSearchEngine(
            registry=registry, analyzer=FixtureAnalyzer({query: parsed}),
        ).search(query)
        self.assertEqual(response.execution_metadata["provider_errors"], {
            "naver_web_search": {
                "error_type": "RuntimeError",
                "root_error_type": "OSError",
            },
        })

    async def test_web_provider_runs_as_fallback_for_empty_public_api(self):
        class EmptyPublic:
            descriptor = ProviderDescriptor(
                "public_api_fixture", ProviderType.PUBLIC_API, frozenset(), frozenset(),
            )
            async def search(self, parsed, limit): return []

        class WebFallback:
            descriptor = ProviderDescriptor(
                "naver_web_search", ProviderType.WEB_SEARCH, frozenset(), frozenset(),
            )
            async def search(self, parsed, limit):
                return [SearchCandidate(
                    "web-a", SourceType.WEB, "naver_web_search",
                    "title-a", "snippet-a", url="https://example.invalid/a",
                )]

        registry = ProviderRegistry()
        registry.register(EmptyPublic())
        registry.register(WebFallback())
        query = "query-a"
        parsed = analysis(
            query,
            provider_targets=["public_api_fixture"],
            provider_plans=[
                {
                    "provider_id": "public_api_fixture", "queries": ["항목A"],
                    "filters": {"region": {"sido": None, "sigungu": None}},
                    "reason": "fixture",
                },
                {
                    "provider_id": "naver_web_search", "queries": ["query-a"],
                    "filters": {"region": {"sido": None, "sigungu": None}},
                    "reason": "fallback",
                },
            ],
        )
        response = await IntelligentSearchEngine(
            registry=registry, analyzer=FixtureAnalyzer({query: parsed}),
        ).search(query)
        self.assertEqual([item.provider_id for item in response.results], ["naver_web_search"])
        self.assertIn("web_search_fallback", response.execution_metadata["executed_steps"])


if __name__ == "__main__":
    unittest.main()
