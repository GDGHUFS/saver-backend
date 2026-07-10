import unittest
from datetime import date
from types import SimpleNamespace

import asyncpg
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from src.special_days.model import YearMonth
from src.special_days.routes import get_special_days


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


class YearMonthTest(unittest.TestCase):
    def test_accepts_four_digit_year_and_two_digit_month(self):
        self.assertEqual(TypeAdapter(YearMonth).validate_python("2026-06"), "2026-06")

    def test_rejects_invalid_year_month_formats(self):
        adapter = TypeAdapter(YearMonth)

        for value in ("2026-6", "2026-13", "026-06", "0000-01"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                adapter.validate_python(value)


class SpecialDayEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_month_items_with_interpreted_date_kinds(self):
        class Connection:
            async def fetch(self, query, *values):
                self.query = query
                self.values = values
                return [
                    {
                        "id": 1,
                        "observed_date": date(2026, 6, 1),
                        "date_kind": "01",
                        "date_name": "국경일 예시",
                        "is_holiday": True,
                    },
                    {
                        "id": 2,
                        "observed_date": date(2026, 6, 1),
                        "date_kind": "02",
                        "date_name": "의병의 날",
                        "is_holiday": False,
                    },
                    {
                        "id": 3,
                        "observed_date": date(2026, 6, 21),
                        "date_kind": "03",
                        "date_name": "하지",
                        "is_holiday": False,
                    },
                    {
                        "id": 4,
                        "observed_date": date(2026, 6, 25),
                        "date_kind": "04",
                        "date_name": "단오",
                        "is_holiday": False,
                    },
                ]

        connection = Connection()
        result = await get_special_days(request_with(connection), "2026-06")

        self.assertEqual(connection.values, (date(2026, 6, 1),))
        self.assertIn("observed_date >= $1::DATE", connection.query)
        self.assertIn("observed_date < $1::DATE + INTERVAL '1 month'", connection.query)
        self.assertIn("ORDER BY observed_date ASC, id ASC", connection.query)
        self.assertEqual(
            [item.date_kind.value for item in result],
            ["국경일", "기념일", "24절기", "잡절"],
        )
        self.assertEqual(
            [item.model_dump(mode="json")["date_kind"] for item in result],
            ["국경일", "기념일", "24절기", "잡절"],
        )

    async def test_returns_empty_list_when_month_has_no_items(self):
        class Connection:
            async def fetch(self, query, *values):
                return []

        result = await get_special_days(request_with(Connection()), "2026-06")

        self.assertEqual(result, [])

    async def test_maps_connection_acquisition_failure_to_service_unavailable(self):
        class FailingPool:
            def acquire(self):
                raise asyncpg.InterfaceError("secret connection detail")

        with self.assertRaises(HTTPException) as raised:
            await get_special_days(request_with_pool(FailingPool()), "2026-06")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret connection detail", raised.exception.detail)

    async def test_rejects_unknown_stored_date_kind_without_exposing_it(self):
        class Connection:
            async def fetch(self, query, *values):
                return [
                    {
                        "id": 1,
                        "observed_date": date(2026, 6, 1),
                        "date_kind": "99",
                        "date_name": "알 수 없는 특일",
                        "is_holiday": False,
                    }
                ]

        with self.assertRaises(HTTPException) as raised:
            await get_special_days(request_with(Connection()), "2026-06")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("99", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
