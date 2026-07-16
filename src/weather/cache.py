from pydantic import ValidationError
from redis.asyncio import Redis

from src.weather.model import NationwideCurrentWeatherResponse


CURRENT_WEATHER_CACHE_KEY = "saver:weather:current:v1"


class InvalidWeatherCacheData(ValueError):
    """Redis에 현재 응답 계약과 맞지 않는 날씨 캐시가 저장된 경우."""


class RedisWeatherCache:
    def __init__(
        self,
        redis: Redis,
        *,
        current_ttl: int = 300,
        max_payload_bytes: int = 10_000_000,
    ) -> None:
        if current_ttl <= 0 or max_payload_bytes <= 0:
            raise ValueError("Weather cache TTL and payload size must be positive")
        self._redis = redis
        self.current_ttl = current_ttl
        self.max_payload_bytes = max_payload_bytes

    async def read_current(self) -> NationwideCurrentWeatherResponse | None:
        raw = await self._redis.get(CURRENT_WEATHER_CACHE_KEY)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise InvalidWeatherCacheData("weather cache payload is not text")
        if len(raw.encode("utf-8")) > self.max_payload_bytes:
            raise InvalidWeatherCacheData("weather cache payload is too large")
        try:
            return NationwideCurrentWeatherResponse.model_validate_json(raw)
        except (ValueError, ValidationError) as exc:
            raise InvalidWeatherCacheData(
                "weather cache payload does not match the current response contract"
            ) from exc

    async def write_current(self, response: NationwideCurrentWeatherResponse) -> None:
        payload = response.model_dump_json()
        if len(payload.encode("utf-8")) > self.max_payload_bytes:
            raise InvalidWeatherCacheData("weather response is too large to cache")
        await self._redis.set(
            CURRENT_WEATHER_CACHE_KEY,
            payload,
            ex=self.current_ttl,
        )
