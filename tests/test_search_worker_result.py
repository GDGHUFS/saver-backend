import json
import unittest

from src.search.engine.answer import generate_answer
from src.search.engine.schema import SearchCandidate, SearchResponse, SourceType
from src.search.model import IntelligentSearchResponse
from src.search.worker import _search_result


class FakeEngine:
    def __init__(self, response):
        self.response = response
        self._owned_llm_client = None

    async def search(self, query):
        return self.response


class SearchAnswerTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_stable_message_when_provider_failed(self):
        response = SearchResponse(
            query_analysis=None,
            results=[],
            execution_metadata={"failed_providers": ["naver_web_search"]},
        )

        answer = await generate_answer(FakeEngine(response), "질문", response)

        self.assertEqual(
            answer,
            "외부 검색 서비스 호출에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        )

    async def test_worker_result_contains_cli_answer_and_search_evidence(self):
        response = SearchResponse(
            query_analysis=None,
            results=[
                SearchCandidate(
                    id="result-1",
                    source_type=SourceType.WEB,
                    provider_id="naver_web_search",
                    title="검색 결과",
                    snippet="CLI와 웹에서 공유할 답변",
                    url="https://example.com/result",
                )
            ],
        )

        raw_result = await _search_result(FakeEngine(response), "질문")
        payload = json.loads(raw_result)
        validated = IntelligentSearchResponse.model_validate(payload)

        self.assertEqual(validated.answer, "CLI와 웹에서 공유할 답변")
        self.assertEqual(
            validated.data.search[0].url,
            "https://example.com/result",
        )


if __name__ == "__main__":
    unittest.main()
