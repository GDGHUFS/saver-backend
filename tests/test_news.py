import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException

from src.news import get_latest_news, get_latest_news_page


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


_DEFAULT_PUB_DATE = object()


def news_row(
    item_id: int = 1,
    publisher: str = "테스트 뉴스",
    pub_date=_DEFAULT_PUB_DATE,
):
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
        "pub_date": datetime.now(UTC) if pub_date is _DEFAULT_PUB_DATE else pub_date,
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

    async def test_returns_paginated_latest_news_with_next_cursor(self):
        first_pub_date = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
        second_pub_date = datetime(2026, 7, 9, 11, 0, tzinfo=UTC)
        third_pub_date = datetime(2026, 7, 9, 10, 0, tzinfo=UTC)

        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [
                    news_row(3, pub_date=first_pub_date),
                    news_row(2, pub_date=second_pub_date),
                    news_row(1, pub_date=third_pub_date),
                ]

        connection = Connection()
        result = await get_latest_news_page(
            request_with(connection),
            page_size=2,
            publisher=None,
            cursor=None,
        )

        self.assertEqual(connection.values, (3, None, None, None))
        self.assertIn("pub_date DESC NULLS LAST", connection.query)
        self.assertEqual([item.id for item in result.items], [3, 2])
        self.assertTrue(result.has_more)
        self.assertIsNotNone(result.next_cursor)
        self.assertEqual(result.page_size, 2)

    async def test_uses_cursor_to_fetch_next_page(self):
        cursor_pub_date = datetime(2026, 7, 9, 11, 0, tzinfo=UTC)

        class FirstConnection:
            async def fetch(self, query, *values):
                return [
                    news_row(3, pub_date=datetime(2026, 7, 9, 12, 0, tzinfo=UTC)),
                    news_row(2, pub_date=cursor_pub_date),
                    news_row(1, pub_date=datetime(2026, 7, 9, 10, 0, tzinfo=UTC)),
                ]

        first_page = await get_latest_news_page(
            request_with(FirstConnection()),
            page_size=2,
            publisher=None,
            cursor=None,
        )

        class NextConnection:
            async def fetch(self, query, *values):
                self.values = values
                return [news_row(1, pub_date=datetime(2026, 7, 9, 10, 0, tzinfo=UTC))]

        connection = NextConnection()
        result = await get_latest_news_page(
            request_with(connection),
            page_size=2,
            publisher="  한국외대 학보  ",
            cursor=first_page.next_cursor,
        )

        self.assertEqual(connection.values, (3, "한국외대 학보", 2, cursor_pub_date))
        self.assertEqual([item.id for item in result.items], [1])
        self.assertFalse(result.has_more)
        self.assertIsNone(result.next_cursor)

    async def test_rejects_invalid_page_cursor(self):
        class Connection:
            async def fetch(self, query, *values):
                raise AssertionError("DB를 조회하면 안 됩니다.")

        with self.assertRaises(HTTPException) as raised:
            await get_latest_news_page(
                request_with(Connection()),
                page_size=10,
                publisher=None,
                cursor="not-a-valid-cursor",
            )

        self.assertEqual(raised.exception.status_code, 422)

    async def test_cursor_supports_news_without_pub_date(self):
        class FirstConnection:
            async def fetch(self, query, *values):
                return [news_row(5, pub_date=None), news_row(4, pub_date=None)]

        first_page = await get_latest_news_page(
            request_with(FirstConnection()),
            page_size=1,
            publisher=None,
            cursor=None,
        )

        class NextConnection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [news_row(4, pub_date=None)]

        connection = NextConnection()
        result = await get_latest_news_page(
            request_with(connection),
            page_size=1,
            publisher=None,
            cursor=first_page.next_cursor,
        )

        self.assertEqual(connection.values, (2, None, 5, None))
        self.assertIn("news_items.pub_date IS NULL", connection.query)
        self.assertEqual([item.id for item in result.items], [4])


if __name__ == "__main__":
    unittest.main()
