import asyncio
import json
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pika


class SearchPublishError(RuntimeError):
    SAFE_REASON_CODES = frozenset({
        "connection_failed",
        "publish_nacked",
        "publish_unroutable",
        "publish_transport_failed",
        "unknown",
    })

    def __init__(self, reason_code: str = "unknown") -> None:
        self.reason_code = (
            reason_code if reason_code in self.SAFE_REASON_CODES else "unknown"
        )
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class RabbitMQSettings:
    host: str
    port: int
    username: str
    password: str
    virtual_host: str
    exchange: str
    legacy_queue: str
    intelligent_queue: str

    def __post_init__(self) -> None:
        if not all((
            self.host.strip(),
            self.username.strip(),
            self.virtual_host.strip(),
            self.exchange.strip(),
            self.legacy_queue.strip(),
            self.intelligent_queue.strip(),
        )):
            raise ValueError("RabbitMQ connection and topology names must not be empty")
        if self.legacy_queue == self.intelligent_queue:
            raise ValueError("legacy and intelligent search queues must be different")


class RabbitMQSearchPublisher:
    """pika의 blocking I/O를 전용 단일 스레드에서만 실행하는 비동기 어댑터."""

    def __init__(self, settings: RabbitMQSettings) -> None:
        self._settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search-rabbitmq")
        self._connection: pika.BlockingConnection | None = None
        self._channel = None
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._connect)
        except (pika.exceptions.AMQPError, OSError, TimeoutError) as exc:
            raise SearchPublishError("connection_failed") from exc
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="search-rabbitmq-heartbeat",
        )

    def _connect(self) -> None:
        credentials = pika.PlainCredentials(
            self._settings.username,
            self._settings.password,
            erase_on_connect=True,
        )
        parameters = pika.ConnectionParameters(
            host=self._settings.host,
            port=self._settings.port,
            virtual_host=self._settings.virtual_host,
            credentials=credentials,
            heartbeat=30,
            blocked_connection_timeout=5,
            socket_timeout=5,
            connection_attempts=3,
            retry_delay=1,
        )
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=self._settings.exchange,
            exchange_type="fanout",
            durable=True,
        )
        # legacy_queue는 기존 브로커 리소스와의 호환성을 위해 설정에 남겨두지만,
        # 현재 검색 요청은 intelligent worker 한 곳으로만 전달한다.
        for queue in (self._settings.legacy_queue, self._settings.intelligent_queue):
            self._channel.queue_declare(queue=queue, durable=True)
        # 이전 버전이 만들어 둔 fanout binding이 남아 있으면 legacy 큐로도
        # 전달되므로, 연결 시 명시적으로 제거한다.
        self._channel.queue_unbind(
            exchange=self._settings.exchange,
            queue=self._settings.legacy_queue,
        )
        self._channel.queue_bind(
            exchange=self._settings.exchange,
            queue=self._settings.intelligent_queue,
        )
        self._channel.confirm_delivery()

    async def _heartbeat_loop(self) -> None:
        """BlockingConnection이 heartbeat와 broker frame을 처리하도록 주기적으로 깨운다."""
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(10)
            await loop.run_in_executor(self._executor, self._process_data_events)

    def _process_data_events(self) -> None:
        if self._connection is None or self._connection.is_closed:
            return
        try:
            self._connection.process_data_events(time_limit=0.1)
        except (pika.exceptions.AMQPError, OSError, TimeoutError):
            # 다음 publish가 새 연결을 만들 수 있도록 끊어진 객체를 폐기한다.
            self._discard_connection()

    async def publish(self, message: dict[str, str | int]) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._publish, message)
        except pika.exceptions.NackError as exc:
            raise SearchPublishError("publish_nacked") from exc
        except pika.exceptions.UnroutableError as exc:
            raise SearchPublishError("publish_unroutable") from exc
        except (pika.exceptions.AMQPError, OSError, TimeoutError) as exc:
            raise SearchPublishError("publish_transport_failed") from exc

    def _publish(self, message: dict[str, str | int]) -> None:
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        properties = pika.BasicProperties(
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=str(message["jobId"]),
            type="search.requested.v1",
        )
        for attempt in range(2):
            try:
                if self._connection is None or self._connection.is_closed:
                    self._connect()
                self._connection.process_data_events(time_limit=0)
                # confirm_delivery 모드에서는 ACK가 오면 None을 반환하고 NACK 또는
                # unroutable 결과는 예외로 전달된다. 반환값의 truthiness를 검사하면 안 된다.
                self._channel.basic_publish(
                    exchange=self._settings.exchange,
                    routing_key="",
                    body=body,
                    properties=properties,
                    mandatory=True,
                )
                return
            except (pika.exceptions.AMQPError, OSError, TimeoutError):
                self._discard_connection()
                if attempt == 1:
                    raise

    def _discard_connection(self) -> None:
        connection = self._connection
        self._connection = None
        self._channel = None
        if connection is not None and connection.is_open:
            with suppress(pika.exceptions.AMQPError, OSError, TimeoutError):
                connection.close()

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close)
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)

    def _close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
