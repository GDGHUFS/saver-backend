"""RabbitMQ consumer that runs the intelligent search engine outside FastAPI."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

import pika
import redis
from dotenv import load_dotenv

from src.search.engine import IntelligentSearchEngine
from src.search.engine.answer import generate_answer
from src.search.engine.config import LLMSettings, boolean_from_env
from src.search.model import IntelligentSearchResponse


MAX_COMMAND_BYTES = 4_096
MAGIC_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
QUERY_PREFIX = "saver:search:query:"
INTELLIGENT_QUERY_SUFFIX = ":intelligent"
TICKET_PREFIX = "saver:search:ticket:"

COMPLETE_QUERY_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'COMPLETED' and redis.call('HEXISTS', KEYS[1], 'result') == 1 then
    return 0
end
if status ~= 'PENDING' then
    return -1
end
redis.call('HSET', KEYS[1], 'status', 'COMPLETED', 'result', ARGV[1])
redis.call('HDEL', KEYS[1], 'error_code')
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

FAIL_QUERY_SCRIPT = """
if redis.call('HGET', KEYS[1], 'status') ~= 'PENDING' then
    return 0
end
redis.call('HSET', KEYS[1], 'status', 'FAILED', 'error_code', 'WORKER_FAILED')
redis.call('HDEL', KEYS[1], 'result')
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""


@dataclass(frozen=True)
class WorkerSettings:
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str
    queue: str
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str | None
    result_ttl: int

    @classmethod
    def from_env(cls) -> WorkerSettings:
        if not boolean_from_env(
            "SEARCH_EXTERNAL_PROCESSING_ENABLED",
            default=False,
        ):
            raise ValueError(
                "SEARCH_EXTERNAL_PROCESSING_ENABLED must be true to start the worker"
            )
        return cls(
            rabbitmq_host=os.getenv("RABBITMQ_HOST", "localhost"),
            rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
            rabbitmq_user=os.getenv("RABBITMQ_USER", "guest"),
            rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            rabbitmq_vhost=os.getenv("RABBITMQ_VHOST", "/"),
            queue=os.getenv(
                "SEARCH_INTELLIGENT_QUEUE",
                "saver.search.intelligent.requests",
            ),
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            redis_password=os.getenv("REDIS_PASSWORD") or None,
            result_ttl=int(os.getenv("SEARCH_QUERY_TTL", "600")),
        )


def _validated_command(body: bytes) -> dict[str, str]:
    if not body or len(body) > MAX_COMMAND_BYTES:
        raise ValueError("invalid search command size")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("unsupported search command")
    query = payload.get("query")
    query_hash = payload.get("queryHash")
    job_id = payload.get("jobId")
    magic_code = payload.get("magicCode")
    if not isinstance(query, str) or not query or len(query) > 200:
        raise ValueError("invalid search query")
    if any(ord(character) < 32 for character in query):
        raise ValueError("invalid search query")
    normalized_query = " ".join(
        unicodedata.normalize("NFKC", query).split()
    ).casefold()
    if query != normalized_query:
        raise ValueError("search query is not normalized")
    if (
        not isinstance(magic_code, str)
        or MAGIC_CODE_PATTERN.fullmatch(magic_code) is None
    ):
        raise ValueError("invalid magic code")
    expected_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    if query_hash != expected_hash or job_id != expected_hash:
        raise ValueError("invalid search command hash")
    return {
        "query": query,
        "query_hash": query_hash,
        "magic_code": magic_code,
    }


async def _search_result(engine: IntelligentSearchEngine, query: str) -> str:
    started = perf_counter()
    response = await engine.search(query)
    answer = await generate_answer(engine, query, response)
    elapsed_ms = max(0, round((perf_counter() - started) * 1000))
    payload: dict[str, Any] = {
        "answer": answer,
        "data": {
            "related_search": [],
            "search": [
                {
                    "url": item.url,
                    "title": item.title,
                    **({"snippet": item.snippet} if item.snippet else {}),
                }
                for item in response.results
                if item.url and item.title
            ],
        },
        "meta": {"ms": elapsed_ms},
    }
    return IntelligentSearchResponse.model_validate(payload).to_result_json()


class SearchWorker:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        LLMSettings.from_env()
        self.engine = IntelligentSearchEngine()
        real_providers = {
            "naver_web_search",
            "kakao_web_search",
        }
        if not any(
            descriptor.provider_id in real_providers
            for descriptor in self.engine.registry.descriptors()
        ):
            raise ValueError("at least one external search provider is required")
        self._event_loop = asyncio.new_event_loop()
        self.redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

    def _command_state(
        self,
        command: dict[str, str],
    ) -> Literal["PENDING", "COMPLETED"]:
        legacy_query_key = f"{QUERY_PREFIX}{command['query_hash']}"
        query_key = f"{legacy_query_key}{INTELLIGENT_QUERY_SUFFIX}"
        ticket = self.redis.hgetall(f"{TICKET_PREFIX}{command['magic_code']}")
        query = self.redis.hgetall(query_key)
        if (
            ticket.get("status") != "PENDING"
            or ticket.get("query_key") != legacy_query_key
            or ticket.get("intelligent_query_key") != query_key
        ):
            raise ValueError("search command has no matching ticket")
        query_status = query.get("status")
        if query_status == "COMPLETED" and isinstance(query.get("result"), str):
            return "COMPLETED"
        if query_status != "PENDING":
            raise ValueError("search command has no pending query")
        return "PENDING"

    def _complete_query(self, query_key: str, result: str) -> None:
        outcome = self.redis.eval(
            COMPLETE_QUERY_SCRIPT,
            1,
            query_key,
            result,
            self.settings.result_ttl,
        )
        if outcome not in (0, 1):
            raise redis.exceptions.ResponseError(
                "query is not pending or completed"
            )

    def _fail_query(self, query_key: str) -> None:
        self.redis.eval(
            FAIL_QUERY_SCRIPT,
            1,
            query_key,
            self.settings.result_ttl,
        )

    def run(self) -> None:
        credentials = pika.PlainCredentials(
            self.settings.rabbitmq_user,
            self.settings.rabbitmq_password,
        )
        connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=self.settings.rabbitmq_host,
            port=self.settings.rabbitmq_port,
            virtual_host=self.settings.rabbitmq_vhost,
            credentials=credentials,
            heartbeat=30,
        ))
        channel = connection.channel()
        channel.queue_declare(queue=self.settings.queue, durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(
            queue=self.settings.queue,
            on_message_callback=self._consume,
            auto_ack=False,
        )
        print(f"Search worker is consuming queue {self.settings.queue}")
        try:
            channel.start_consuming()
        finally:
            if connection.is_open:
                connection.close()
            try:
                self._event_loop.run_until_complete(self.engine.aclose())
            finally:
                self._event_loop.close()

    def _consume(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        _properties: pika.BasicProperties,
        body: bytes,
    ) -> None:
        try:
            command = _validated_command(body)
        except (UnicodeError, ValueError):
            channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
            return

        query_key = (
            f"{QUERY_PREFIX}{command['query_hash']}"
            f"{INTELLIGENT_QUERY_SUFFIX}"
        )
        try:
            command_state = self._command_state(command)
        except ValueError:
            channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
            return
        except redis.exceptions.RedisError:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

        if command_state == "COMPLETED":
            print(
                "Search intelligent cache hit "
                f"job={command['query_hash'][:12]}"
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        print(f"Search intelligent job received job={command['query_hash'][:12]}")
        try:
            result = self._event_loop.run_until_complete(
                _search_result(self.engine, command["query"])
            )
            self._complete_query(query_key, result)
        except redis.exceptions.RedisError:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        except Exception:
            try:
                self._fail_query(query_key)
            except redis.exceptions.RedisError:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return
            print(f"Search intelligent job failed job={command['query_hash'][:12]}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        print(f"Search intelligent job completed job={command['query_hash'][:12]}")
        channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    load_dotenv()
    SearchWorker(WorkerSettings.from_env()).run()


if __name__ == "__main__":
    main()
