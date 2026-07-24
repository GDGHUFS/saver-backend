import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis

from src.search.model import IntelligentSearchResponse, KagiSearchResponse


TICKET_PREFIX = "saver:search:ticket:"
QUERY_PREFIX = "saver:search:query:"
INTELLIGENT_QUERY_SUFFIX = ":intelligent"
QUERY_KEY_PATTERN = re.compile(r"^saver:search:query:[0-9a-f]{64}$")
INTELLIGENT_QUERY_KEY_PATTERN = re.compile(
    r"^saver:search:query:[0-9a-f]{64}:intelligent$"
)
VALID_STATUSES = frozenset({"PENDING", "COMPLETED", "FAILED"})
RATE_LIMIT_PREFIX = "saver:search:rate:"


CREATE_TICKET_SCRIPT = """
local legacy_status = redis.call('HGET', KEYS[2], 'status')
local legacy_has_result = redis.call('HEXISTS', KEYS[2], 'result')
local intelligent_status = redis.call('HGET', KEYS[3], 'status')
local intelligent_has_result = redis.call('HEXISTS', KEYS[3], 'result')

local legacy_completed = legacy_status == 'COMPLETED' and legacy_has_result == 1
local intelligent_completed =
    intelligent_status == 'COMPLETED' and intelligent_has_result == 1
local should_publish = (legacy_completed and intelligent_completed) and 0 or 1

if not legacy_completed then
    redis.call('HSET', KEYS[2], 'status', 'PENDING')
    redis.call('HDEL', KEYS[2], 'result', 'error_code')
end
if not intelligent_completed then
    redis.call('HSET', KEYS[3], 'status', 'PENDING')
    redis.call('HDEL', KEYS[3], 'result', 'error_code')
end

redis.call('EXPIRE', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[3], ARGV[2])
redis.call('HSET', KEYS[1],
    'status', should_publish == 0 and 'COMPLETED' or 'PENDING',
    'query_key', KEYS[2],
    'intelligent_query_key', KEYS[3])
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

RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class InvalidSearchData(ValueError):
    """Redis에 외부 계약과 맞지 않는 검색 데이터가 저장된 경우."""


@dataclass(frozen=True)
class LegacySearchState:
    status: str
    result: KagiSearchResponse | None = None


@dataclass(frozen=True)
class IntelligentSearchState:
    status: str
    result: IntelligentSearchResponse | None = None


@dataclass(frozen=True)
class SearchState:
    legacy: LegacySearchState
    intelligent: IntelligentSearchState


class RedisSearchStore:
    def __init__(
        self,
        redis: Redis,
        *,
        ticket_ttl: int = 300,
        query_ttl: int = 600,
        max_result_bytes: int = 2_000_000,
        rate_limit_secret: str | None = None,
        submission_limit: int = 10,
        rate_limit_window: int = 60,
    ) -> None:
        if (
            ticket_ttl <= 0
            or query_ttl <= 0
            or max_result_bytes <= 0
            or submission_limit <= 0
            or rate_limit_window <= 0
        ):
            raise ValueError("Search limits, TTLs, and result size must be positive")
        self._redis = redis
        self.ticket_ttl = ticket_ttl
        self.query_ttl = query_ttl
        self.max_result_bytes = max_result_bytes
        self._rate_limit_secret = (
            rate_limit_secret.encode("utf-8")
            if isinstance(rate_limit_secret, str) and rate_limit_secret
            else None
        )
        self.submission_limit = submission_limit
        self.rate_limit_window = rate_limit_window

    @staticmethod
    def ticket_key(magic_code: str) -> str:
        return f"{TICKET_PREFIX}{magic_code}"

    @staticmethod
    def query_key(query_hash: str) -> str:
        return f"{QUERY_PREFIX}{query_hash}"

    @staticmethod
    def intelligent_query_key(query_hash: str) -> str:
        return f"{QUERY_PREFIX}{query_hash}{INTELLIGENT_QUERY_SUFFIX}"

    def rate_limit_key(self, user_id: int) -> str:
        if self._rate_limit_secret is None:
            raise RuntimeError("search rate limit secret is not configured")
        digest = hmac.new(
            self._rate_limit_secret,
            f"saver-search-rate-limit:v1:{user_id}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{RATE_LIMIT_PREFIX}{digest}"

    async def allow_submission(self, user_id: int) -> bool:
        count = await self._redis.eval(
            RATE_LIMIT_SCRIPT,
            1,
            self.rate_limit_key(user_id),
            self.rate_limit_window,
        )
        if not isinstance(count, int) or count <= 0:
            raise InvalidSearchData("unexpected search rate limit response")
        return count <= self.submission_limit

    async def create_ticket(self, magic_code: str, query_hash: str) -> bool:
        result = await self._redis.eval(
            CREATE_TICKET_SCRIPT,
            3,
            self.ticket_key(magic_code),
            self.query_key(query_hash),
            self.intelligent_query_key(query_hash),
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
        intelligent_query_key = ticket.get("intelligent_query_key")
        if (
            ticket_status not in VALID_STATUSES
            or not isinstance(query_key, str)
            or not isinstance(intelligent_query_key, str)
        ):
            raise InvalidSearchData("invalid search ticket")
        if not QUERY_KEY_PATTERN.fullmatch(query_key):
            raise InvalidSearchData("invalid legacy query key")
        if not INTELLIGENT_QUERY_KEY_PATTERN.fullmatch(intelligent_query_key):
            raise InvalidSearchData("invalid intelligent query key")
        if intelligent_query_key != f"{query_key}{INTELLIGENT_QUERY_SUFFIX}":
            raise InvalidSearchData("search query keys do not match")
        if ticket_status == "FAILED":
            return SearchState(
                legacy=LegacySearchState(status="FAILED"),
                intelligent=IntelligentSearchState(status="FAILED"),
            )

        legacy_query = await self._redis.hgetall(query_key)
        intelligent_query = await self._redis.hgetall(intelligent_query_key)
        legacy_status, legacy_result = self._validated_query_state(
            legacy_query,
            KagiSearchResponse,
            "legacy",
        )
        intelligent_status, intelligent_result = self._validated_query_state(
            intelligent_query,
            IntelligentSearchResponse,
            "intelligent",
        )
        return SearchState(
            legacy=LegacySearchState(
                status=legacy_status,
                result=legacy_result,
            ),
            intelligent=IntelligentSearchState(
                status=intelligent_status,
                result=intelligent_result,
            ),
        )

    def _validated_query_state(
        self,
        query: dict[str, str],
        response_model: type[KagiSearchResponse],
        branch: str,
    ) -> tuple[str, Any]:
        if not query:
            return "PENDING", None
        query_status = query.get("status")
        if query_status not in VALID_STATUSES:
            raise InvalidSearchData(f"invalid {branch} query status")
        if query_status != "COMPLETED":
            return query_status, None

        raw_result = query.get("result")
        if not isinstance(raw_result, str):
            raise InvalidSearchData(f"completed {branch} search has no result")
        if len(raw_result.encode("utf-8")) > self.max_result_bytes:
            raise InvalidSearchData(f"{branch} search result is too large")
        try:
            decoded_result = json.loads(
                raw_result,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
            result = response_model.model_validate(decoded_result)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidSearchData(
                f"{branch} search result does not match its contract"
            ) from exc
        return "COMPLETED", result
