import unittest

from src.search.engine.query_adapter import build_provider_query_plan
from tests.query_analysis_fixture import analysis


class ProviderQueryAdapterTest(unittest.TestCase):
    def test_uses_llm_provider_queries_and_filters(self):
        parsed = analysis(
            "서울시 월세 지원",
            provider_plans=[{
                "provider_id": "naver_web_search",
                "queries": ["서울시 1인 가구 월세 지원", "서울시 주거 복지"],
                "filters": {
                    "life_cycle": ["청년"],
                    "interest_theme": ["주거"],
                    "region": {"sido": "서울특별시", "sigungu": None},
                },
                "reason": "fixture",
            }],
        )
        plan = build_provider_query_plan(parsed, "naver_web_search")
        self.assertEqual(plan.queries, ("서울시 1인 가구 월세 지원", "서울시 주거 복지"))
        self.assertEqual(plan.filters["region"]["sido"], "서울특별시")

    def test_falls_back_to_analysis_queries(self):
        parsed = analysis("원본 질문", search_queries=["검색어 A", "검색어 B"])
        plan = build_provider_query_plan(parsed, "naver_web_search")
        self.assertEqual(plan.queries, ("검색어 A", "검색어 B"))


if __name__ == "__main__":
    unittest.main()
