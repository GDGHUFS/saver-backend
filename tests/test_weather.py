import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException

from src.weather.grid import latitude_longitude_to_grid
from src.weather.routes import get_nationwide_current_weather, get_weather_forecast


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


class GridConversionTest(unittest.TestCase):
    def test_converts_seoul_coordinates_with_collector_formula(self):
        self.assertEqual(latitude_longitude_to_grid(37.5704, 126.9816), (60, 127))

    def test_rejects_coordinates_outside_world_bounds(self):
        for latitude, longitude in ((91.0, 127.0), (37.5, 181.0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValueError):
                    latitude_longitude_to_grid(latitude, longitude)


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
