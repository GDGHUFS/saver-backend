import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.search.engine.config import EngineConfig, LLMSettings
from src.search.engine.schema import SearchResponse
from src.search.store import RedisSearchStore
from src.search.worker import (
    MAX_COMMAND_BYTES,
    SearchWorker,
    WorkerSettings,
    _validated_command,
)


MAGIC_CODE = "A" * 43


def command_body(
    query: str = "보안 검색",
    *,
    magic_code: str = MAGIC_CODE,
) -> bytes:
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return json.dumps(
        {
            "schemaVersion": 1,
            "jobId": query_hash,
            "magicCode": magic_code,
            "query": query,
            "queryHash": query_hash,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class EngineSecuritySettingsTest(unittest.TestCase):
    def test_mock_providers_are_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            config = EngineConfig.from_env()

        self.assertFalse(config.use_mock_providers)

    def test_mock_providers_require_explicit_boolean_opt_in(self):
        with patch.dict(
            "os.environ",
            {
                "SEARCH_USE_MOCK_PROVIDERS": "true",
                "SEARCH_MAX_CANDIDATES": "7",
                "SEARCH_MAX_RESULTS": "3",
                "SEARCH_PROVIDER_TIMEOUT": "9",
            },
            clear=True,
        ):
            config = EngineConfig.from_env()

        self.assertTrue(config.use_mock_providers)
        self.assertEqual(config.max_candidates, 7)
        self.assertEqual(config.max_results, 3)
        self.assertEqual(config.provider_timeout_seconds, 9)

    def test_rejects_ambiguous_mock_provider_setting(self):
        with patch.dict(
            "os.environ",
            {"SEARCH_USE_MOCK_PROVIDERS": "enable"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                EngineConfig.from_env()

    def test_remote_llm_endpoint_requires_https(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "secret",
                "LLM_BASE_URL": "http://llm.example.com/v1",
            },
            clear=True,
        ):
            with self.assertRaises(ValueError):
                LLMSettings.from_env()

    def test_loopback_llm_endpoint_may_use_http(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "local-key",
                "LLM_BASE_URL": "http://127.0.0.1:8000/v1",
                "LLM_MODEL": "local-model",
                "LLM_TIMEOUT_SECONDS": "5",
            },
            clear=True,
        ):
            settings = LLMSettings.from_env()

        self.assertEqual(settings.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(settings.timeout_seconds, 5)

    def test_llm_endpoint_rejects_embedded_credentials(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "secret",
                "LLM_BASE_URL": "https://user:password@llm.example.com/v1",
            },
            clear=True,
        ):
            with self.assertRaises(ValueError):
                LLMSettings.from_env()


class SearchCommandSecurityTest(unittest.TestCase):
    def test_accepts_complete_normalized_command(self):
        command = _validated_command(command_body())

        self.assertEqual(command["magic_code"], MAGIC_CODE)
        self.assertEqual(command["query"], "보안 검색")

    def test_rejects_oversized_command_before_json_parsing(self):
        with self.assertRaises(ValueError):
            _validated_command(b"{" + b"x" * MAX_COMMAND_BYTES)

    def test_rejects_invalid_magic_code(self):
        with self.assertRaises(ValueError):
            _validated_command(command_body(magic_code="guessable"))

    def test_rejects_non_normalized_query(self):
        with self.assertRaises(ValueError):
            _validated_command(command_body("  보안   검색  "))


class WorkerStartupSecurityTest(unittest.TestCase):
    def test_external_processing_requires_explicit_opt_in(self):
        with patch.dict(
            "os.environ",
            {
                "RABBITMQ_USER": "worker",
                "RABBITMQ_PASSWORD": "secret",
                "REDIS_PASSWORD": "secret",
            },
            clear=True,
        ):
            with self.assertRaises(ValueError):
                WorkerSettings.from_env()

    def test_enabled_worker_allows_redis_without_password(self):
        with patch.dict(
            "os.environ",
            {"SEARCH_EXTERNAL_PROCESSING_ENABLED": "true"},
            clear=True,
        ):
            settings = WorkerSettings.from_env()

        self.assertIsNone(settings.redis_password)

    def test_enabled_worker_reads_redis_password(self):
        with patch.dict(
            "os.environ",
            {
                "SEARCH_EXTERNAL_PROCESSING_ENABLED": "true",
                "REDIS_PASSWORD": "redis-secret",
            },
            clear=True,
        ):
            settings = WorkerSettings.from_env()

        self.assertEqual(settings.redis_password, "redis-secret")

    def test_enabled_worker_uses_dedicated_intelligent_queue(self):
        with patch.dict(
            "os.environ",
            {
                "SEARCH_EXTERNAL_PROCESSING_ENABLED": "true",
                "SEARCH_INTELLIGENT_QUEUE": "search.intelligent.test",
            },
            clear=True,
        ):
            settings = WorkerSettings.from_env()

        self.assertEqual(settings.queue, "search.intelligent.test")


class FakeRedis:
    def __init__(self, values, *, eval_result=1):
        self.values = values
        self.eval_result = eval_result
        self.eval_calls = []

    def hgetall(self, key):
        return self.values.get(key, {})

    def eval(self, *args):
        self.eval_calls.append(args)
        return self.eval_result


class FakeChannel:
    def __init__(self):
        self.acked = []
        self.rejected = []
        self.nacked = []

    def basic_ack(self, *, delivery_tag):
        self.acked.append(delivery_tag)

    def basic_reject(self, *, delivery_tag, requeue):
        self.rejected.append((delivery_tag, requeue))

    def basic_nack(self, *, delivery_tag, requeue):
        self.nacked.append((delivery_tag, requeue))


class EmptyEngine:
    async def search(self, query):
        return SearchResponse(query_analysis=None, results=[])


class WorkerCommandAuthorizationTest(unittest.TestCase):
    def worker(self, redis_client):
        worker = SearchWorker.__new__(SearchWorker)
        worker.settings = WorkerSettings(
            rabbitmq_host="localhost",
            rabbitmq_port=5672,
            rabbitmq_user="worker",
            rabbitmq_password="secret",
            rabbitmq_vhost="/",
            queue="saver.search.intelligent.requests",
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password="secret",
            result_ttl=180,
        )
        worker.engine = EmptyEngine()
        worker._event_loop = asyncio.new_event_loop()
        self.addCleanup(worker._event_loop.close)
        worker.redis = redis_client
        return worker

    def redis_values(self, query="보안 검색", *, query_status="PENDING"):
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        legacy_query_key = f"saver:search:query:{query_hash}"
        query_key = f"{legacy_query_key}:intelligent"
        query_data = {"status": query_status}
        if query_status == "COMPLETED":
            query_data["result"] = "{}"
        return {
            f"saver:search:ticket:{MAGIC_CODE}": {
                "status": "PENDING",
                "query_key": legacy_query_key,
                "intelligent_query_key": query_key,
            },
            query_key: query_data,
        }

    def test_rejects_command_without_matching_redis_ticket(self):
        worker = self.worker(FakeRedis({}))
        channel = FakeChannel()

        worker._consume(
            channel,
            SimpleNamespace(delivery_tag=7),
            None,
            command_body(),
        )

        self.assertEqual(channel.rejected, [(7, False)])
        self.assertEqual(channel.acked, [])

    def test_acknowledges_already_completed_authorized_command(self):
        worker = self.worker(
            FakeRedis(self.redis_values(query_status="COMPLETED"))
        )
        channel = FakeChannel()

        worker._consume(
            channel,
            SimpleNamespace(delivery_tag=8),
            None,
            command_body(),
        )

        self.assertEqual(channel.acked, [8])

    def test_completes_only_authorized_pending_command(self):
        redis_client = FakeRedis(self.redis_values())
        worker = self.worker(redis_client)
        channel = FakeChannel()

        worker._consume(
            channel,
            SimpleNamespace(delivery_tag=9),
            None,
            command_body(),
        )

        self.assertEqual(channel.acked, [9])
        self.assertEqual(len(redis_client.eval_calls), 1)
        self.assertTrue(
            redis_client.eval_calls[0][2].endswith(":intelligent")
        )


class SearchRateLimitPrivacyTest(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_key_does_not_contain_raw_user_id(self):
        class FakeAsyncRedis:
            def __init__(self):
                self.calls = []

            async def eval(self, *args):
                self.calls.append(args)
                return 1

        redis_client = FakeAsyncRedis()
        store = RedisSearchStore(
            redis_client,
            rate_limit_secret="s" * 32,
        )

        allowed = await store.allow_submission(1234)

        self.assertTrue(allowed)
        key = redis_client.calls[0][2]
        self.assertTrue(key.startswith("saver:search:rate:"))
        self.assertNotIn("1234", key)


if __name__ == "__main__":
    unittest.main()
