import unittest

from src.database_init import init_db


class FakeConnection:
    def __init__(self):
        self.queries: list[str] = []

    async def execute(self, query: str):
        self.queries.append(query)


class AcquireContext:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()

    def acquire(self):
        return AcquireContext(self.connection)


class InitDbTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_users_table_with_required_constraints(self):
        pool = FakePool()

        await init_db(pool)

        self.assertEqual(len(pool.connection.queries), 1)
        query = pool.connection.queries[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS users", query)
        self.assertIn("id BIGINT PRIMARY KEY", query)
        self.assertIn("nickname TEXT NOT NULL", query)
        self.assertIn("profile_image TEXT NOT NULL", query)


if __name__ == "__main__":
    unittest.main()
