import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException

from src.news import get_latest_news


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return AcquireContext(self.connection)


def request_with(connection):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=Pool(connection))))


def news_row(item_id: int = 1, publisher: str = "테스트 뉴스"):
    return {
        "id": item_id,
        "publisher": publisher,
        "feed_title": "테스트 뉴스 RSS",
        "title": "새 소식",
        "link": "https://example.com/news/1",
        "description": "기사 요약",
        "author": "기자",
        "comments": None,
        "enclosure_url": None,
        "enclosure_length": None,
        "enclosure_type": None,
        "guid": "article-1",
        "guid_is_permalink": False,
        "pub_date": datetime.now(UTC),
        "source_name": None,
        "source_url": None,
        "categories": ["사회", "교육"],
    }


class NewsEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_latest_news_without_publisher_filter(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [news_row(2), news_row(1)]

        connection = Connection()
        result = await get_latest_news(request_with(connection), count=2, publisher=None)

        self.assertEqual(connection.values, (2, None))
        self.assertIn("pub_date DESC NULLS LAST", connection.query)
        self.assertEqual([item.id for item in result], [2, 1])
        self.assertEqual(result[0].categories, ["사회", "교육"])

    async def test_filters_by_trimmed_exact_publisher_name(self):
        class Connection:
            async def fetch(self, query, *values):
                self.values = values
                return [news_row(publisher="한국외대 학보")]

        connection = Connection()
        result = await get_latest_news(
            request_with(connection),
            count=5,
            publisher="  한국외대 학보  ",
        )

        self.assertEqual(connection.values, (5, "한국외대 학보"))
        self.assertEqual(result[0].publisher, "한국외대 학보")

    async def test_rejects_whitespace_only_publisher(self):
        class Connection:
            async def fetch(self, query, *values):
                raise AssertionError("DB를 조회하면 안 됩니다.")

        with self.assertRaises(HTTPException) as raised:
            await get_latest_news(request_with(Connection()), count=5, publisher="   ")

        self.assertEqual(raised.exception.status_code, 422)

    async def test_returns_empty_list_when_no_news_matches(self):
        class Connection:
            async def fetch(self, query, *values):
                return []

        result = await get_latest_news(request_with(Connection()), count=10, publisher="없는 언론사")

        self.assertEqual(result, [])

    async def test_maps_database_failure_to_service_unavailable(self):
        class Connection:
            async def fetch(self, query, *values):
                raise asyncpg.InterfaceError("secret connection detail")

        with self.assertRaises(HTTPException) as raised:
            await get_latest_news(request_with(Connection()), count=10, publisher=None)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret connection detail", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
