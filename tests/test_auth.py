import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
from fastapi import HTTPException

from src.auth import (
    KAKAO_UNLINK_URL,
    _delete_local_user_after_unlink,
    _frontend_redirect_url,
    _unlink_kakao_user,
    _user_values,
    get_current_user_id,
)
from src.auth.session import create_session_cookie


class FrontendRedirectTest(unittest.TestCase):
    def setUp(self):
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(frontend_url="https://example.com"),
            )
        )

    def test_builds_login_completion_redirect(self):
        self.assertEqual(_frontend_redirect_url(self.request), "https://example.com/")

    def test_builds_withdrawal_completion_redirect(self):
        self.assertEqual(
            _frontend_redirect_url(self.request, withdrawn="true"),
            "https://example.com/?withdrawn=true",
        )


class UserValuesTest(unittest.TestCase):
    def test_reads_kakao_account_profile(self):
        profile = {
            "id": 1234,
            "kakao_account": {
                "profile": {
                    "nickname": "Saver 사용자",
                    "profile_image_url": "https://example.com/profile.png",
                }
            },
        }

        self.assertEqual(
            _user_values(profile, "https://example.com/default.svg"),
            (1234, "Saver 사용자", "https://example.com/profile.png"),
        )

    def test_uses_defaults_when_optional_profile_is_not_provided(self):
        self.assertEqual(
            _user_values({"id": 1234}, "https://example.com/default.svg"),
            (1234, "사용자-1234", "https://example.com/default.svg"),
        )

    def test_rejects_profile_without_user_id(self):
        with self.assertRaises(HTTPException) as raised:
            _user_values({}, "https://example.com/default.svg")

        self.assertEqual(raised.exception.status_code, 502)


class KakaoUnlinkTest(unittest.IsolatedAsyncioTestCase):
    async def test_unlinks_user_with_bearer_access_token(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), KAKAO_UNLINK_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer kakao-token")
            return httpx.Response(200, json={"id": 1234})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            user_id = await _unlink_kakao_user(client, "kakao-token")

        self.assertEqual(user_id, 1234)

    async def test_maps_kakao_unlink_failure_to_bad_gateway(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"msg": "invalid token"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(HTTPException) as raised:
                await _unlink_kakao_user(client, "invalid-token")

        self.assertEqual(raised.exception.status_code, 502)


class SessionUserTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_signed_cookie_after_local_user_deletion(self):
        class Connection:
            async def fetchval(self, query, user_id):
                return False

        class AcquireContext:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class Pool:
            def acquire(self):
                return AcquireContext()

        secret = "session-secret"
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(session_secret=secret, pool=Pool()),
            )
        )
        cookie = create_session_cookie(1234, secret, 3600)

        with self.assertRaises(HTTPException) as raised:
            await get_current_user_id(request, cookie)

        self.assertEqual(raised.exception.status_code, 401)

    async def test_maps_database_failure_to_service_unavailable(self):
        class Connection:
            async def fetchval(self, query, user_id):
                raise asyncpg.InterfaceError("connection is closed")

        class AcquireContext:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class Pool:
            def acquire(self):
                return AcquireContext()

        secret = "session-secret"
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(session_secret=secret, pool=Pool()),
            )
        )
        cookie = create_session_cookie(1234, secret, 3600)

        with self.assertRaises(HTTPException) as raised:
            await get_current_user_id(request, cookie)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("connection is closed", raised.exception.detail)


class WithdrawalLocalDeleteTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_idempotent_cascade_delete_after_unlink(self):
        class Connection:
            def __init__(self):
                self.queries = []
                self.attempts = 0

            async def execute(self, query, user_id):
                self.queries.append((query, user_id))
                self.attempts += 1
                if self.attempts < 3:
                    raise asyncpg.InterfaceError("connection is closed")

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

        connection = Connection()
        with patch("src.auth.asyncio.sleep", new=AsyncMock()) as sleep:
            await _delete_local_user_after_unlink(Pool(connection), 1234)

        self.assertEqual(connection.attempts, 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertTrue(all("DELETE FROM users" in query for query, _ in connection.queries))
        self.assertTrue(all("DELETE FROM blogs" not in query for query, _ in connection.queries))

    async def test_logs_critical_after_local_delete_retries_are_exhausted(self):
        class AcquireContext:
            async def __aenter__(self):
                raise asyncpg.InterfaceError("postgres://user:secret@internal")

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class Pool:
            def acquire(self):
                return AcquireContext()

        with (
            patch("src.auth.asyncio.sleep", new=AsyncMock()),
            patch("src.auth.logger.critical") as critical,
            self.assertRaises(HTTPException) as raised,
        ):
            await _delete_local_user_after_unlink(Pool(), 1234)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret", raised.exception.detail)
        critical.assert_called_once_with(
            "Auth reconciliation required: withdraw-local-delete-after-provider-unlink "
            "user_id={} ({})",
            1234,
            "InterfaceError",
        )


if __name__ == "__main__":
    unittest.main()
