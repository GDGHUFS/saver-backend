import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException
from pydantic import ValidationError

from src.blog import (
    BlogWriteRequest,
    delete_blog,
    get_user_blogs,
    read_blog,
    update_blog,
    write_blog,
)


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


class BlogRequestTest(unittest.TestCase):
    def test_strips_surrounding_whitespace(self):
        blog = BlogWriteRequest(title="  제목  ", content="  본문  ")

        self.assertEqual(blog.title, "제목")
        self.assertEqual(blog.content, "본문")

    def test_rejects_whitespace_only_values(self):
        with self.assertRaises(ValidationError):
            BlogWriteRequest(title="   ", content="본문")


class BlogEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_blog_and_returns_location(self):
        class Connection:
            async def fetchval(self, query, *values):
                self.values = values
                return 7

        connection = Connection()
        response = await write_blog(
            request_with(connection),
            BlogWriteRequest(title="제목", content="본문"),
            1234,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["location"], "/blog/7")
        self.assertEqual(connection.values, (1234, "제목", "본문"))

    async def test_reads_blog_as_documented_response(self):
        now = datetime.now(UTC)

        class Connection:
            async def fetchrow(self, query, blog_id):
                return {
                    "id": blog_id,
                    "title": "제목",
                    "content": "본문",
                    "created_at": now,
                    "updated_at": now,
                    "author_id": 1234,
                    "nickname": "작성자",
                    "profile_image": "https://example.com/profile.png",
                }

        blog = await read_blog(request_with(Connection()), 1)

        self.assertEqual(blog.id, 1)
        self.assertEqual(blog.nickname, "작성자")

    async def test_reads_all_blogs_for_user_in_query_order(self):
        now = datetime.now(UTC)

        class Connection:
            async def fetch(self, query, user_id):
                self.user_id = user_id
                return [
                    {
                        "id": 2,
                        "title": "두 번째 글",
                        "content": "본문 2",
                        "created_at": now,
                        "updated_at": now,
                        "author_id": user_id,
                        "nickname": "작성자",
                        "profile_image": "https://example.com/profile.png",
                    },
                    {
                        "id": 1,
                        "title": "첫 번째 글",
                        "content": "본문 1",
                        "created_at": now,
                        "updated_at": now,
                        "author_id": user_id,
                        "nickname": "작성자",
                        "profile_image": "https://example.com/profile.png",
                    },
                ]

        connection = Connection()
        blogs = await get_user_blogs(request_with(connection), 1234)

        self.assertEqual(connection.user_id, 1234)
        self.assertEqual([blog.id for blog in blogs], [2, 1])
        self.assertEqual([blog.content for blog in blogs], ["본문 2", "본문 1"])

    async def test_returns_empty_list_when_user_has_no_blogs(self):
        class Connection:
            async def fetch(self, query, user_id):
                return [{"id": None}]

        blogs = await get_user_blogs(request_with(Connection()), 1234)

        self.assertEqual(blogs, [])

    async def test_returns_not_found_when_blog_author_does_not_exist(self):
        class Connection:
            async def fetch(self, query, user_id):
                return []

        with self.assertRaises(HTTPException) as raised:
            await get_user_blogs(request_with(Connection()), 1234)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_returns_not_found_when_blog_does_not_exist(self):
        class Connection:
            async def fetchrow(self, query, blog_id):
                return None

        with self.assertRaises(HTTPException) as raised:
            await read_blog(request_with(Connection()), 1)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_returns_not_found_when_delete_does_not_match_owner(self):
        class Connection:
            async def fetchval(self, query, *values):
                return None

        with self.assertRaises(HTTPException) as raised:
            await delete_blog(request_with(Connection()), 1, 1234)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_returns_not_found_when_update_does_not_match_owner(self):
        class Connection:
            async def fetchval(self, query, *values):
                return None

        with self.assertRaises(HTTPException) as raised:
            await update_blog(
                request_with(Connection()),
                1,
                BlogWriteRequest(title="제목", content="본문"),
                1234,
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_maps_database_failure_to_service_unavailable(self):
        class Connection:
            async def fetchrow(self, query, blog_id):
                raise asyncpg.InterfaceError("connection is closed")

        with self.assertRaises(HTTPException) as raised:
            await read_blog(request_with(Connection()), 1)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("connection is closed", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
