"""RabbitMQ consumer that runs the intelligent search engine outside FastAPI."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import pika
import redis
from dotenv import load_dotenv

from src.search.engine import IntelligentSearchEngine


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
        return cls(
            rabbitmq_host=os.getenv("RABBITMQ_HOST", "localhost"),
            rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
            rabbitmq_user=os.getenv("RABBITMQ_USER", "guest"),
            rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            rabbitmq_vhost=os.getenv("RABBITMQ_VHOST", "/"),
            queue=os.getenv("SEARCH_QUEUE", "saver.search.requests"),
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            redis_password=os.getenv("REDIS_PASSWORD") or None,
            result_ttl=int(os.getenv("SEARCH_QUERY_TTL", "180")),
        )


def _validated_command(body: bytes) -> dict[str, str]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("unsupported search command")
    query = payload.get("query")
    query_hash = payload.get("queryHash")
    job_id = payload.get("jobId")
    if not isinstance(query, str) or not query or len(query) > 200:
        raise ValueError("invalid search query")
    expected_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    if query_hash != expected_hash or job_id != expected_hash:
        raise ValueError("invalid search command hash")
    return {"query": query, "query_hash": query_hash}


async def _search_result(engine: IntelligentSearchEngine, query: str) -> str:
    started = perf_counter()
    response = await engine.search(query)
    elapsed_ms = max(0, round((perf_counter() - started) * 1000))
    payload: dict[str, Any] = {
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
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SearchWorker:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.engine = IntelligentSearchEngine()
        self.redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True,
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

    def _consume(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        _properties: pika.BasicProperties,
        body: bytes,
    ) -> None:
        query_key: str | None = None
        try:
            command = _validated_command(body)
            query_key = f"saver:search:query:{command['query_hash']}"
            result = asyncio.run(_search_result(self.engine, command["query"]))
            pipe = self.redis.pipeline(transaction=True)
            pipe.hset(query_key, mapping={"status": "COMPLETED", "result": result})
            pipe.hdel(query_key, "error_code")
            pipe.expire(query_key, self.settings.result_ttl)
            pipe.execute()
        except ValueError:
            channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
            return
        except Exception:
            if query_key is not None:
                pipe = self.redis.pipeline(transaction=True)
                pipe.hset(query_key, mapping={"status": "FAILED", "error_code": "WORKER_FAILED"})
                pipe.expire(query_key, self.settings.result_ttl)
                pipe.execute()
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    load_dotenv()
    SearchWorker(WorkerSettings.from_env()).run()


if __name__ == "__main__":
    main()
