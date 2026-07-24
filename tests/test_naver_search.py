import os
import unittest
from unittest.mock import patch

import httpx

from tests.query_analysis_fixture import analysis
from src.search.engine.naver import NaverSearchError, NaverSearchSettings, NaverWebSearchProvider


class NaverSearchSettingsTest(unittest.TestCase):
    def test_returns_none_when_credentials_are_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(NaverSearchSettings.from_env())

    def test_rejects_partially_configured_credentials(self):
        with patch.dict(os.environ, {"NAVER_SEARCH_CLIENT_ID": "id"}, clear=True):
            with self.assertRaises(ValueError):
                NaverSearchSettings.from_env()

    def test_rejects_non_official_endpoint(self):
        with self.assertRaises(ValueError):
            NaverSearchSettings("id", "secret", endpoint="https://attacker.example/search")


class NaverWebSearchProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_calls_official_api_headers_and_normalizes_result(self):
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["request"] = request
            return httpx.Response(200, json={
                "lastBuildDate": "Mon, 13 Jul 2026 18:00:00 +0900",
                "total": 1,
                "start": 1,
                "display": 1,
                "items": [{
                    "title": "<b>Title</b> &amp; Result",
                    "link": "https://example.com/result",
                    "description": "<b>Snippet</b> text",
                }],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = NaverWebSearchProvider(NaverSearchSettings("client-id", "client-secret"), client=client)
            results = await provider.search(analysis("query-a"), 10)

        request = observed["request"]
        self.assertEqual(request.headers["X-Naver-Client-Id"], "client-id")
        self.assertEqual(request.headers["X-Naver-Client-Secret"], "client-secret")
        self.assertIn("query=query-a", str(request.url))
        self.assertEqual(results[0].title, "Title & Result")
        self.assertEqual(results[0].snippet, "Snippet text")
        self.assertEqual(results[0].provider_id, "naver_web_search")
        self.assertFalse(results[0].demo_data)

    async def test_retries_one_transient_failure(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503 if attempts == 1 else 200, json={"items": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = NaverWebSearchProvider(NaverSearchSettings("id", "secret"), client=client)
            self.assertEqual(await provider.search(analysis("query-a"), 1), [])
        self.assertEqual(attempts, 2)

    async def test_does_not_expose_credentials_in_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"errorCode": "024"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = NaverWebSearchProvider(NaverSearchSettings("secret-id", "secret-key"), client=client)
            with self.assertRaises(NaverSearchError) as caught:
                await provider.search(analysis("query-a"), 1)
        self.assertNotIn("secret-id", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
