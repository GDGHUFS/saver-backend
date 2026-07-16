import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from src.weather.cache import (
    CURRENT_WEATHER_CACHE_KEY,
    InvalidWeatherCacheData,
    RedisWeatherCache,
)
from src.weather.grid import latitude_longitude_to_grid
from src.weather.model import NationwideCurrentWeatherResponse
from src.weather.routes import (
    get_nationwide_current_weather,
    get_weather_forecast,
    get_weather_locations,
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


def request_with_pool(pool):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))


def request_with(connection):
    return request_with_pool(Pool(connection))


def request_with_weather(connection, cache):
    state = SimpleNamespace(pool=Pool(connection), weather_cache=cache)
    return SimpleNamespace(app=SimpleNamespace(state=state))


ISSUED_AT = datetime(2026, 7, 16, 5, 0, tzinfo=UTC)
FORECAST_AT = datetime(2026, 7, 16, 6, 0, tzinfo=UTC)


def location_row(
    *,
    administrative_code="1111051500",
    region_level_1="서울특별시",
    region_level_2="종로구",
    region_level_3="청운효자동",
    nx=60,
    ny=127,
    longitude=126.98935225645432,
    latitude=37.579871128849334,
):
    return {
        "administrative_code": administrative_code,
        "region_level_1": region_level_1,
        "region_level_2": region_level_2,
        "region_level_3": region_level_3,
        "nx": nx,
        "ny": ny,
        "longitude": longitude,
        "latitude": latitude,
    }


def forecast_row(*, forecast_at=FORECAST_AT, nx=60, ny=127):
    return {
        "nx": nx,
        "ny": ny,
        "issued_at": ISSUED_AT,
        "forecast_at": forecast_at,
        "precipitation_probability": "30",
        "precipitation_type": "1",
        "precipitation_amount": "1mm 미만",
        "humidity": "70",
        "snowfall_amount": "적설없음",
        "sky_status": "3",
        "temperature": "25",
        "minimum_temperature": None,
        "maximum_temperature": "29",
        "wind_u_component": "1.2",
        "wind_v_component": "-0.3",
        "wave_height": None,
        "wind_direction": "270",
        "wind_speed": "2.1",
    }


def current_response() -> NationwideCurrentWeatherResponse:
    return NationwideCurrentWeatherResponse.model_validate(
        {
            "generated_at": FORECAST_AT,
            "items": [
                location_row()
                | forecast_row()
                | {
                    "grid": {
                        "nx": 60,
                        "ny": 127,
                        "longitude": 126.98935225645432,
                        "latitude": 37.579871128849334,
                    },
                    "sky_status_label": "구름많음",
                    "precipitation_type_label": "비",
                }
            ],
        }
    )


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.set_calls = []

    async def get(self, key):
        self.get_key = key
        return self.value

    async def set(self, key, value, *, ex):
        self.value = value
        self.set_calls.append((key, value, ex))
        return True


class FakeWeatherCache:
    def __init__(self, response=None, *, read_error=None, write_error=None):
        self.response = response
        self.read_error = read_error
        self.write_error = write_error
        self.read_count = 0
        self.writes = []

    async def read_current(self):
        self.read_count += 1
        if self.read_error is not None:
            raise self.read_error
        return self.response

    async def write_current(self, response):
        if self.write_error is not None:
            raise self.write_error
        self.response = response
        self.writes.append(response)


class GridConversionTest(unittest.TestCase):
    def test_converts_seoul_coordinates_with_collector_formula(self):
        self.assertEqual(latitude_longitude_to_grid(37.5704, 126.9816), (60, 127))

    def test_rejects_coordinates_outside_world_bounds(self):
        for latitude, longitude in ((91.0, 127.0), (37.5, 181.0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValueError):
                    latitude_longitude_to_grid(latitude, longitude)


class RedisWeatherCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_round_trips_current_response_with_configured_ttl(self):
        redis = FakeRedis()
        cache = RedisWeatherCache(redis, current_ttl=123)
        expected = current_response()

        await cache.write_current(expected)
        actual = await cache.read_current()

        self.assertEqual(actual, expected)
        self.assertEqual(redis.get_key, CURRENT_WEATHER_CACHE_KEY)
        self.assertEqual(redis.set_calls[0][0], CURRENT_WEATHER_CACHE_KEY)
        self.assertEqual(redis.set_calls[0][2], 123)

    async def test_rejects_cache_payload_outside_response_contract(self):
        cache = RedisWeatherCache(FakeRedis('{"generated_at":"secret-invalid"}'))

        with self.assertRaises(InvalidWeatherCacheData) as raised:
            await cache.read_current()

        self.assertNotIn("secret-invalid", str(raised.exception))

    def test_rejects_non_positive_cache_settings(self):
        for settings in ({"current_ttl": 0}, {"max_payload_bytes": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RedisWeatherCache(FakeRedis(), **settings)


class NationwideCurrentWeatherTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_nearest_forecast_from_latest_issue_for_each_grid(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [location_row() | forecast_row()]

        connection = Connection()
        result = await get_nationwide_current_weather(request_with(connection))

        self.assertEqual(connection.values, ())
        self.assertIn("DISTINCT ON (issue.nx, issue.ny)", connection.query)
        self.assertIn("JOIN LATERAL", connection.query)
        self.assertIn("ABS(", connection.query)
        self.assertEqual(result.items[0].grid.nx, 60)
        self.assertEqual(result.items[0].forecast_at, FORECAST_AT)
        self.assertEqual(result.items[0].sky_status_label, "구름많음")
        self.assertEqual(result.items[0].precipitation_type_label, "비")

    async def test_caches_database_response_and_reuses_it(self):
        class Connection:
            def __init__(self):
                self.fetch_count = 0

            async def fetch(self, query, *values):
                self.fetch_count += 1
                return [location_row() | forecast_row()]

        connection = Connection()
        cache = FakeWeatherCache()
        request = request_with_weather(connection, cache)

        first = await get_nationwide_current_weather(request)
        second = await get_nationwide_current_weather(request)

        self.assertEqual(connection.fetch_count, 1)
        self.assertEqual(cache.writes, [first])
        self.assertEqual(second, first)

    async def test_coalesces_concurrent_cache_misses_within_process(self):
        class Connection:
            def __init__(self):
                self.fetch_count = 0

            async def fetch(self, query, *values):
                self.fetch_count += 1
                await asyncio.sleep(0)
                return [location_row() | forecast_row()]

        connection = Connection()
        cache = FakeWeatherCache()
        request = request_with_weather(connection, cache)

        results = await asyncio.gather(
            *(get_nationwide_current_weather(request) for _ in range(5))
        )

        self.assertEqual(connection.fetch_count, 1)
        self.assertTrue(all(result == results[0] for result in results))

    async def test_returns_cached_response_without_database_access(self):
        class Connection:
            async def fetch(self, query, *values):
                raise AssertionError("DB를 조회하면 안 됩니다.")

        expected = current_response()
        result = await get_nationwide_current_weather(
            request_with_weather(Connection(), FakeWeatherCache(expected))
        )

        self.assertEqual(result, expected)

    async def test_falls_back_to_database_without_retrying_unavailable_cache(self):
        class Connection:
            async def fetch(self, query, *values):
                return [location_row() | forecast_row()]

        cache = FakeWeatherCache(
            read_error=RedisConnectionError("redis://user:secret@internal"),
            write_error=RedisConnectionError("redis://user:secret@internal"),
        )
        result = await get_nationwide_current_weather(
            request_with_weather(Connection(), cache)
        )

        self.assertEqual(len(result.items), 1)
        self.assertEqual(cache.read_count, 1)
        self.assertEqual(cache.writes, [])

    async def test_replaces_invalid_cache_data_with_database_response(self):
        class Connection:
            async def fetch(self, query, *values):
                return [location_row() | forecast_row()]

        cache = FakeWeatherCache(
            read_error=InvalidWeatherCacheData("secret invalid payload")
        )
        result = await get_nationwide_current_weather(
            request_with_weather(Connection(), cache)
        )

        self.assertEqual(cache.writes, [result])

    async def test_returns_empty_items_when_no_forecasts_are_stored(self):
        class Connection:
            async def fetch(self, query, *values):
                return []

        result = await get_nationwide_current_weather(request_with(Connection()))

        self.assertEqual(result.items, [])

    async def test_maps_pool_acquisition_failure_to_service_unavailable(self):
        class FailingPool:
            def acquire(self):
                raise asyncpg.InterfaceError("postgres://user:secret@internal")

        with self.assertRaises(HTTPException) as raised:
            await get_nationwide_current_weather(request_with_pool(FailingPool()))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret", raised.exception.detail)

    async def test_rejects_invalid_stored_grid_without_exposing_value(self):
        class Connection:
            async def fetch(self, query, *values):
                return [location_row(latitude=99.0) | forecast_row()]

        with self.assertRaises(HTTPException) as raised:
            await get_nationwide_current_weather(request_with(Connection()))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("99", raised.exception.detail)


class WeatherLocationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_distinct_first_level_regions(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [
                    {
                        "name": "서울특별시",
                        "full_name": "서울특별시",
                        "has_children": True,
                    },
                    {
                        "name": "이어도",
                        "full_name": "이어도",
                        "has_children": False,
                    },
                ]

        connection = Connection()
        result = await get_weather_locations(
            request_with(connection),
            region_level_1=None,
            region_level_2=None,
        )

        self.assertEqual(connection.values, ())
        self.assertIn("GROUP BY location.region_level_1", connection.query)
        self.assertIn("ORDER BY location.region_level_1 ASC", connection.query)
        self.assertEqual(result.region_level, 1)
        self.assertEqual(result.parents, [])
        self.assertEqual([item.full_name for item in result.items], ["서울특별시", "이어도"])
        self.assertFalse(result.items[1].has_children)

    async def test_returns_second_level_regions_for_normalized_alias(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [
                    {
                        "name": "전주시 완산구",
                        "full_name": "전북특별자치도 전주시 완산구",
                        "has_children": True,
                    }
                ]

        connection = Connection()
        result = await get_weather_locations(
            request_with(connection),
            region_level_1="  전북  ",
            region_level_2=None,
        )

        self.assertEqual(connection.values, ("전북특별자치도",))
        self.assertIn("location.region_level_2 IS NOT NULL", connection.query)
        self.assertEqual(result.region_level, 2)
        self.assertEqual(result.parents, ["전북특별자치도"])
        self.assertEqual(result.items[0].name, "전주시 완산구")

    async def test_returns_third_level_regions_with_forecast_ready_full_name(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [
                    {
                        "name": "청운효자동",
                        "full_name": "서울특별시 종로구 청운효자동",
                        "has_children": False,
                    },
                    {
                        "name": "사직동",
                        "full_name": "서울특별시 종로구 사직동",
                        "has_children": False,
                    },
                ]

        connection = Connection()
        result = await get_weather_locations(
            request_with(connection),
            region_level_1=" 서울특별시 ",
            region_level_2=" 종로구 ",
        )

        self.assertEqual(connection.values, ("서울특별시", "종로구"))
        self.assertIn("location.region_level_3 IS NOT NULL", connection.query)
        self.assertEqual(result.region_level, 3)
        self.assertEqual(result.parents, ["서울특별시", "종로구"])
        self.assertEqual(result.items[0].full_name, "서울특별시 종로구 청운효자동")

    async def test_supports_current_combined_jeonnam_region_alias(self):
        class Connection:
            async def fetch(self, query, *values):
                self.values = values
                return []

        connection = Connection()
        result = await get_weather_locations(
            request_with(connection),
            region_level_1="전남",
            region_level_2=None,
        )

        self.assertEqual(connection.values, ("전남광주통합특별시",))
        self.assertEqual(result.parents, ["전남광주통합특별시"])
        self.assertEqual(result.items, [])

    async def test_rejects_second_level_without_first_level(self):
        with self.assertRaises(HTTPException) as raised:
            await get_weather_locations(
                request_with(None),
                region_level_1=None,
                region_level_2="종로구",
            )

        self.assertEqual(raised.exception.status_code, 422)

    async def test_rejects_whitespace_only_parent_without_database_query(self):
        with self.assertRaises(HTTPException) as raised:
            await get_weather_locations(
                request_with(None),
                region_level_1="   ",
                region_level_2=None,
            )

        self.assertEqual(raised.exception.status_code, 422)

    async def test_returns_empty_items_for_parent_without_children(self):
        class Connection:
            async def fetch(self, query, *values):
                return []

        result = await get_weather_locations(
            request_with(Connection()),
            region_level_1="이어도",
            region_level_2=None,
        )

        self.assertEqual(result.region_level, 2)
        self.assertEqual(result.parents, ["이어도"])
        self.assertEqual(result.items, [])

    async def test_maps_location_query_failure_to_service_unavailable(self):
        class Connection:
            async def fetch(self, query, *values):
                raise asyncpg.InterfaceError("secret location storage detail")

        with self.assertRaises(HTTPException) as raised:
            await get_weather_locations(
                request_with(Connection()),
                region_level_1=None,
                region_level_2=None,
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret", raised.exception.detail)

    async def test_rejects_invalid_stored_location_option(self):
        class Connection:
            async def fetch(self, query, *values):
                return [{"name": "", "full_name": "secret invalid", "has_children": True}]

        with self.assertRaises(HTTPException) as raised:
            await get_weather_locations(
                request_with(Connection()),
                region_level_1=None,
                region_level_2=None,
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret invalid", raised.exception.detail)


class WeatherForecastTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_region_forecasts_and_normalizes_collector_alias(self):
        second_forecast_at = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)

        class Connection:
            def __init__(self):
                self.calls = []

            async def fetch(self, query, *values):
                self.calls.append((query, values))
                if len(self.calls) == 1:
                    return [
                        location_row(
                            administrative_code="4511356000",
                            region_level_1="전북특별자치도",
                            region_level_2="전주시 완산구",
                            region_level_3="중앙동",
                            nx=63,
                            ny=89,
                            longitude=127.0,
                            latitude=35.8,
                        )
                    ]
                return [
                    forecast_row(nx=63, ny=89),
                    forecast_row(forecast_at=second_forecast_at, nx=63, ny=89),
                ]

        connection = Connection()
        result = await get_weather_forecast(
            request_with(connection),
            region="  전북   전주시  ",
            latitude=None,
            longitude=None,
            hours=12,
        )

        self.assertEqual(connection.calls[0][1], (["%전북특별자치도%", "%전주시%"],))
        self.assertIn("ILIKE ALL($1::TEXT[])", connection.calls[0][0])
        self.assertEqual(connection.calls[1][1], ([63], [89], 12))
        self.assertIn("date_trunc('hour', CURRENT_TIMESTAMP)", connection.calls[1][0])
        self.assertIn("forecast_rank <= $3", connection.calls[1][0])
        self.assertEqual(result.selector, "region")
        self.assertEqual(result.region, "전북특별자치도 전주시")
        self.assertEqual(len(result.items[0].forecasts), 2)
        self.assertEqual(result.items[0].locations[0].region_level_3, "중앙동")

    async def test_returns_coordinate_forecast_for_exact_converted_grid(self):
        class Connection:
            def __init__(self):
                self.calls = []

            async def fetch(self, query, *values):
                self.calls.append((query, values))
                if len(self.calls) == 1:
                    return [location_row()]
                return [forecast_row()]

        connection = Connection()
        result = await get_weather_forecast(
            request_with(connection),
            region=None,
            latitude=37.5704,
            longitude=126.9816,
            hours=24,
        )

        self.assertEqual(connection.calls[0][1], (60, 127))
        self.assertIn("LEFT JOIN weather_locations", connection.calls[0][0])
        self.assertEqual(result.selector, "coordinates")
        self.assertEqual(result.items[0].grid.ny, 127)

    async def test_returns_all_unique_grids_for_a_broad_region(self):
        class Connection:
            def __init__(self):
                self.call_count = 0

            async def fetch(self, query, *values):
                self.call_count += 1
                if self.call_count == 1:
                    return [
                        location_row(),
                        location_row(administrative_code="1111053000", region_level_3="사직동"),
                        location_row(
                            administrative_code="1114052000",
                            region_level_2="중구",
                            region_level_3="소공동",
                            nx=59,
                            ny=127,
                            longitude=126.85,
                        ),
                    ]
                return [forecast_row(), forecast_row(nx=59, ny=127)]

        result = await get_weather_forecast(
            request_with(Connection()),
            region="서울특별시",
            latitude=None,
            longitude=None,
            hours=1,
        )

        self.assertEqual(len(result.items), 2)
        self.assertEqual(len(result.items[0].locations), 2)
        self.assertEqual(result.items[1].grid.nx, 59)

    async def test_rejects_missing_partial_or_conflicting_selectors(self):
        invalid_selectors = (
            (None, None, None),
            (None, 37.5, None),
            ("서울", 37.5, 127.0),
            ("   ", None, None),
        )
        for region, latitude, longitude in invalid_selectors:
            with self.subTest(region=region, latitude=latitude, longitude=longitude):
                with self.assertRaises(HTTPException) as raised:
                    await get_weather_forecast(
                        request_with(None),
                        region=region,
                        latitude=latitude,
                        longitude=longitude,
                        hours=24,
                    )
                self.assertEqual(raised.exception.status_code, 422)

    async def test_returns_not_found_without_starting_collection_for_unknown_region(self):
        class Connection:
            async def fetch(self, query, *values):
                return []

        with self.assertRaises(HTTPException) as raised:
            await get_weather_forecast(
                request_with(Connection()),
                region="없는 지역",
                latitude=None,
                longitude=None,
                hours=24,
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_returns_not_found_when_location_has_no_current_forecast(self):
        class Connection:
            def __init__(self):
                self.call_count = 0

            async def fetch(self, query, *values):
                self.call_count += 1
                return [location_row()] if self.call_count == 1 else []

        with self.assertRaises(HTTPException) as raised:
            await get_weather_forecast(
                request_with(Connection()),
                region="서울 종로구",
                latitude=None,
                longitude=None,
                hours=24,
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_maps_query_failure_to_service_unavailable(self):
        class Connection:
            async def fetch(self, query, *values):
                raise asyncpg.PostgresError("internal table detail")

        with self.assertRaises(HTTPException) as raised:
            await get_weather_forecast(
                request_with(Connection()),
                region="서울",
                latitude=None,
                longitude=None,
                hours=24,
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("internal table detail", raised.exception.detail)

    async def test_rejects_invalid_stored_location_without_exposing_value(self):
        class Connection:
            def __init__(self):
                self.call_count = 0

            async def fetch(self, query, *values):
                self.call_count += 1
                if self.call_count == 1:
                    return [location_row(administrative_code="invalid-secret-code")]
                return [forecast_row()]

        with self.assertRaises(HTTPException) as raised:
            await get_weather_forecast(
                request_with(Connection()),
                region="서울 종로구",
                latitude=None,
                longitude=None,
                hours=24,
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("invalid-secret-code", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
