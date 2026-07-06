import json
import re
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis


TICKET_PREFIX = "saver:search:ticket:"
QUERY_PREFIX = "saver:search:query:"
QUERY_KEY_PATTERN = re.compile(r"^saver:search:query:[0-9a-f]{64}$")
VALID_STATUSES = frozenset({"PENDING", "COMPLETED", "FAILED"})


CREATE_TICKET_SCRIPT = """
local query_status = redis.call('HGET', KEYS[2], 'status')
local has_result = redis.call('HEXISTS', KEYS[2], 'result')
local should_publish = 1

if query_status == 'COMPLETED' and has_result == 1 then
    should_publish = 0
else
    redis.call('HSET', KEYS[2], 'status', 'PENDING')
    redis.call('HDEL', KEYS[2], 'result', 'error_code')
end

redis.call('EXPIRE', KEYS[2], ARGV[2])
redis.call('HSET', KEYS[1],
    'status', should_publish == 0 and 'COMPLETED' or 'PENDING',
    'query_key', KEYS[2])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return should_publish
"""


MARK_PUBLISH_FAILED_SCRIPT = """
if redis.call('HGET', KEYS[1], 'status') == 'PENDING' then
    redis.call('HSET', KEYS[1], 'status', 'FAILED', 'error_code', 'DISPATCH_FAILED')
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    return 1
end
return 0
"""


class InvalidSearchData(ValueError):
    """Redis에 외부 계약과 맞지 않는 검색 데이터가 저장된 경우."""


@dataclass(frozen=True)
class SearchState:
    status: str
    result: Any = None


class RedisSearchStore:
    def __init__(
        self,
        redis: Redis,
        *,
        ticket_ttl: int = 60,
        query_ttl: int = 180,
        max_result_bytes: int = 2_000_000,
    ) -> None:
        if ticket_ttl <= 0 or query_ttl <= 0 or max_result_bytes <= 0:
            raise ValueError("Search TTLs and result size must be positive")
        self._redis = redis
        self.ticket_ttl = ticket_ttl
        self.query_ttl = query_ttl
        self.max_result_bytes = max_result_bytes

    @staticmethod
    def ticket_key(magic_code: str) -> str:
        return f"{TICKET_PREFIX}{magic_code}"

    @staticmethod
    def query_key(query_hash: str) -> str:
        return f"{QUERY_PREFIX}{query_hash}"

    async def create_ticket(self, magic_code: str, query_hash: str) -> bool:
        result = await self._redis.eval(
            CREATE_TICKET_SCRIPT,
            2,
            self.ticket_key(magic_code),
            self.query_key(query_hash),
            self.ticket_ttl,
            self.query_ttl,
        )
        if result not in (0, 1):
            raise InvalidSearchData("unexpected Redis script response")
        return bool(result)

    async def mark_publish_failed(self, magic_code: str) -> None:
        await self._redis.eval(
            MARK_PUBLISH_FAILED_SCRIPT,
            1,
            self.ticket_key(magic_code),
            self.ticket_ttl,
        )

    async def delete_ticket(self, magic_code: str) -> bool:
        return bool(await self._redis.delete(self.ticket_key(magic_code)))

    async def read(self, magic_code: str) -> SearchState | None:
        ticket = await self._redis.hgetall(self.ticket_key(magic_code))
        if not ticket:
            return None

        ticket_status = ticket.get("status")
        query_key = ticket.get("query_key")
        if ticket_status not in VALID_STATUSES or not isinstance(query_key, str):
            raise InvalidSearchData("invalid search ticket")
        if not QUERY_KEY_PATTERN.fullmatch(query_key):
            raise InvalidSearchData("invalid query key")
        if ticket_status == "FAILED":
            return SearchState(status="FAILED")

        query = await self._redis.hgetall(query_key)
        if not query:
            return SearchState(status="PENDING")
        query_status = query.get("status")
        if query_status not in VALID_STATUSES:
            raise InvalidSearchData("invalid query status")
        if query_status != "COMPLETED":
            return SearchState(status=query_status)

        raw_result = query.get("result")
        if not isinstance(raw_result, str):
            raise InvalidSearchData("completed search has no result")
        if len(raw_result.encode("utf-8")) > self.max_result_bytes:
            raise InvalidSearchData("search result is too large")
        try:
            result = json.loads(
                raw_result,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidSearchData("search result is not valid JSON") from exc
        return SearchState(status="COMPLETED", result=result)
