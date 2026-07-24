import unittest

from src.search.engine.analysis import OpenAIQueryAnalyzer


class StructuredAnalysisTest(unittest.TestCase):
    def test_preserves_llm_structured_decision_without_keyword_inference(self):
        analysis = OpenAIQueryAnalyzer.from_payload("query-a", {
            "primary_intent": "custom_intent",
            "domains": ["custom_domain"],
            "user_conditions": {"age": 1, "region": {"sido": "region-a", "sigungu": "region-b"}, "employment_status": "condition-a", "household_type": "condition-b"},
            "requests_multiple_results": True, "complexity": "complex", "confidence": .96,
            "search_queries": ["query-a", "query-b"],
            "provider_plans": [],
            "required_steps": ["query_expansion", "candidate_generation", "hybrid_ranking", "reranking", "answer_nugget_extraction", "diversity_selection"],
            "provider_targets": ["naver_web_search"],
        })
        self.assertEqual([item.name for item in analysis.domains], ["custom_domain"])
        self.assertEqual(analysis.constraints["employment_status"], "condition-a")
        self.assertTrue(analysis.planner_flags["needs_diverse_results"])
        self.assertFalse(analysis.planner_flags["needs_multi_provider_search"])
        self.assertEqual(len(analysis.search_queries), 2)


if __name__ == "__main__":
    unittest.main()
