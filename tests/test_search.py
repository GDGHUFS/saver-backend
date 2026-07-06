import hashlib
import unittest
from unittest.mock import patch

import httpx
import pika
from redis.exceptions import ConnectionError as RedisConnectionError

from src.app import app
from src.auth import get_current_user_id
from src.search.publisher import (
    RabbitMQSearchPublisher,
    RabbitMQSettings,
    SearchPublishError,
)
from src.search.routes import hash_query, normalize_query
from src.search.store import InvalidSearchData, RedisSearchStore, SearchState


MAGIC_CODE = "A" * 43
SECOND_MAGIC_CODE = "B" * 43


class FakeStore:
    def __init__(self, *, should_publish=True, state=None, error=None):
        self.should_publish = should_publish
        self.state = state
        self.error = error
        self.created = []
        self.failed = []

    async def create_ticket(self, magic_code, query_hash):
        if self.error:
            raise self.error
        self.created.append((magic_code, query_hash))
        return self.should_publish

    async def mark_publish_failed(self, magic_code):
        self.failed.append(magic_code)

    async def read(self, magic_code):
        if self.error:
            raise self.error
        return self.state


class FakePublisher:
    def __init__(self, error=None):
        self.error = error
        self.messages = []

    async def publish(self, message):
        if self.error:
            raise self.error
        self.messages.append(message)


class SearchApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def authenticated_user():
            return 1234

        app.dependency_overrides[get_current_user_id] = authenticated_user
        self.transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.pop(get_current_user_id, None)

    def set_dependencies(self, store, publisher=None):
        app.state.search_store = store
        app.state.search_publisher = publisher or FakePublisher()

    async def test_accepts_search_and_publishes_normalized_command(self):
        store = FakeStore()
        publisher = FakePublisher()
        self.set_dependencies(store, publisher)

        with patch("src.search.routes.secrets.token_urlsafe", return_value=MAGIC_CODE):
            response = await self.client.post("/search", json={"query": "  HUFS   날씨  "})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"magicCode": MAGIC_CODE, "status": "PENDING"})
        expected_hash = hashlib.sha256("hufs 날씨".encode()).hexdigest()
        self.assertEqual(store.created, [(MAGIC_CODE, expected_hash)])
        self.assertEqual(
            publisher.messages,
            [{
                "schemaVersion": 1,
                "jobId": expected_hash,
                "magicCode": MAGIC_CODE,
                "query": "hufs 날씨",
                "queryHash": expected_hash,
            }],
        )

    async def test_rejects_search_submission_without_login(self):
        store = FakeStore()
        self.set_dependencies(store)
        app.dependency_overrides.pop(get_current_user_id, None)

        response = await self.client.post("/search", json={"query": "검색"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(store.created, [])

    async def test_rejects_result_lookup_without_login(self):
        store = FakeStore(state=SearchState(status="COMPLETED", result=[]))
        self.set_dependencies(store)
        app.dependency_overrides.pop(get_current_user_id, None)

        response = await self.client.get(f"/search/{MAGIC_CODE}")

        self.assertEqual(response.status_code, 401)

    async def test_does_not_publish_when_completed_cache_exists(self):
        store = FakeStore(should_publish=False)
        publisher = FakePublisher()
        self.set_dependencies(store, publisher)

        with patch("src.search.routes.secrets.token_urlsafe", return_value=MAGIC_CODE):
            response = await self.client.post("/search", json={"query": "cached"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(publisher.messages, [])

    async def test_returns_service_unavailable_and_marks_ticket_on_publish_failure(self):
        store = FakeStore()
        publisher = FakePublisher(SearchPublishError("secret broker detail"))
        self.set_dependencies(store, publisher)

        with patch("src.search.routes.secrets.token_urlsafe", return_value=MAGIC_CODE):
            response = await self.client.post("/search", json={"query": "검색"})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret broker detail", response.text)
        self.assertEqual(store.failed, [MAGIC_CODE])

    async def test_maps_redis_failure_without_exposing_internal_message(self):
        store = FakeStore(error=RedisConnectionError("redis://user:secret@internal"))
        self.set_dependencies(store)

        with patch("src.search.routes.secrets.token_urlsafe", return_value=MAGIC_CODE):
            response = await self.client.post("/search", json={"query": "검색"})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)

    async def test_returns_not_found_for_expired_magic_code(self):
        self.set_dependencies(FakeStore(state=None))

        response = await self.client.get(f"/search/{MAGIC_CODE}")

        self.assertEqual(response.status_code, 404)

    async def test_rejects_invalid_magic_code_format(self):
        self.set_dependencies(FakeStore(state=None))

        response = await self.client.get("/search/guessable")

        self.assertEqual(response.status_code, 422)

    async def test_returns_accepted_while_search_is_pending(self):
        self.set_dependencies(FakeStore(state=SearchState(status="PENDING")))

        response = await self.client.get(f"/search/{MAGIC_CODE}")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"magicCode": MAGIC_CODE, "status": "PENDING"})

    async def test_returns_completed_result_from_store(self):
        result = {"items": [{"title": "검색 결과"}]}
        self.set_dependencies(FakeStore(state=SearchState(status="COMPLETED", result=result)))

        response = await self.client.get(f"/search/{MAGIC_CODE}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], result)

    async def test_maps_worker_failure_to_stable_bad_gateway_response(self):
        self.set_dependencies(FakeStore(state=SearchState(status="FAILED")))

        response = await self.client.get(f"/search/{MAGIC_CODE}")

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("error_code", response.text)

    async def test_duplicate_requests_use_same_idempotency_job_id(self):
        store = FakeStore()
        publisher = FakePublisher()
        self.set_dependencies(store, publisher)

        with patch(
            "src.search.routes.secrets.token_urlsafe",
            side_effect=[MAGIC_CODE, SECOND_MAGIC_CODE],
        ):
            first = await self.client.post("/search", json={"query": "같은 검색"})
            second = await self.client.post("/search", json={"query": "  같은   검색 "})

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(len(publisher.messages), 2)
        self.assertEqual(publisher.messages[0]["jobId"], publisher.messages[1]["jobId"])
        self.assertNotEqual(
            publisher.messages[0]["magicCode"],
            publisher.messages[1]["magicCode"],
        )


class RedisSearchStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_invalid_completed_json(self):
        query_hash = "a" * 64
        query_key = f"saver:search:query:{query_hash}"

        class FakeRedis:
            async def hgetall(self, key):
                if key.startswith("saver:search:ticket:"):
                    return {"status": "COMPLETED", "query_key": query_key}
                return {"status": "COMPLETED", "result": "not-json"}

        store = RedisSearchStore(FakeRedis())

        with self.assertRaises(InvalidSearchData):
            await store.read(MAGIC_CODE)


class RabbitMQSearchPublisherTest(unittest.TestCase):
    def test_reconnects_once_when_idle_connection_was_closed_by_broker(self):
        class BrokenConnection:
            is_closed = False
            is_open = True

            def process_data_events(self, time_limit):
                raise pika.exceptions.StreamLostError("heartbeat timeout")

            def close(self):
                self.is_open = False

        class HealthyConnection:
            is_closed = False
            is_open = True

            def process_data_events(self, time_limit):
                return None

        class HealthyChannel:
            def basic_publish(self, **kwargs):
                return True

        settings = RabbitMQSettings("localhost", 5672, "guest", "guest", "/", "queue")
        publisher = RabbitMQSearchPublisher(settings)
        publisher._connection = BrokenConnection()
        publisher._channel = object()
        reconnects = []

        def reconnect():
            reconnects.append(True)
            publisher._connection = HealthyConnection()
            publisher._channel = HealthyChannel()

        publisher._connect = reconnect
        try:
            result = publisher._publish({"jobId": "stable-job-id"})
        finally:
            publisher._executor.shutdown(wait=True, cancel_futures=True)

        self.assertIsNone(result)
        self.assertEqual(reconnects, [True])

    def test_treats_pika_none_return_as_successful_confirmation(self):
        class HealthyConnection:
            is_closed = False
            is_open = True

            def process_data_events(self, time_limit):
                return None

        class PikaChannel:
            def basic_publish(self, **kwargs):
                # pika 1.4.x는 publisher ACK 성공 시 값을 반환하지 않는다.
                return None

        settings = RabbitMQSettings("localhost", 5672, "guest", "guest", "/", "queue")
        publisher = RabbitMQSearchPublisher(settings)
        publisher._connection = HealthyConnection()
        publisher._channel = PikaChannel()
        try:
            result = publisher._publish({"jobId": "stable-job-id"})
        finally:
            publisher._executor.shutdown(wait=True, cancel_futures=True)

        self.assertIsNone(result)


class SearchNormalizationTest(unittest.TestCase):
    def test_normalization_and_hash_are_stable(self):
        first = normalize_query(" ＨＵＦＳ   날씨 ")
        second = normalize_query("hufs 날씨")

        self.assertEqual(first, second)
        self.assertEqual(hash_query(first), hash_query(second))


if __name__ == "__main__":
    unittest.main()
