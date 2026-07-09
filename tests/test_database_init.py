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

        self.assertEqual(len(pool.connection.queries), 7)
        query = pool.connection.queries[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS users", query)
        self.assertIn("id BIGINT PRIMARY KEY", query)
        self.assertIn("nickname TEXT NOT NULL", query)
        self.assertIn("profile_image TEXT NOT NULL", query)

    async def test_creates_blogs_table_with_author_reference(self):
        pool = FakePool()

        await init_db(pool)

        query = pool.connection.queries[1]
        self.assertIn("CREATE TABLE IF NOT EXISTS blogs", query)
        self.assertIn("author_id BIGINT NOT NULL", query)
        self.assertIn("FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE", query)

    async def test_creates_rss_news_tables_and_indexes(self):
        pool = FakePool()

        await init_db(pool)

        feed_query = pool.connection.queries[2]
        self.assertIn("CREATE TABLE IF NOT EXISTS news_feeds", feed_query)
        self.assertIn("feed_url TEXT NOT NULL UNIQUE", feed_query)
        self.assertIn("publisher TEXT NOT NULL", feed_query)
        self.assertIn("cloud JSONB", feed_query)
        self.assertIn("skip_hours SMALLINT[]", feed_query)
        self.assertIn("extensions JSONB", feed_query)

        feed_category_query = pool.connection.queries[3]
        self.assertIn("CREATE TABLE IF NOT EXISTS news_feed_categories", feed_category_query)
        self.assertIn("REFERENCES news_feeds(id) ON DELETE CASCADE", feed_category_query)

        item_query = pool.connection.queries[4]
        self.assertIn("CREATE TABLE IF NOT EXISTS news_items", item_query)
        self.assertIn("title TEXT NOT NULL CHECK (length(btrim(title)) > 0)", item_query)
        self.assertIn("link TEXT NOT NULL CHECK (length(btrim(link)) > 0)", item_query)
        self.assertIn("enclosure_url TEXT", item_query)
        self.assertIn("guid_is_permalink BOOLEAN", item_query)
        self.assertIn("source_url TEXT", item_query)

        item_category_query = pool.connection.queries[5]
        self.assertIn("CREATE TABLE IF NOT EXISTS news_item_categories", item_category_query)
        self.assertIn("REFERENCES news_items(id) ON DELETE CASCADE", item_category_query)

        index_query = pool.connection.queries[6]
        self.assertIn("news_items_latest_idx", index_query)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS news_feeds_publisher_key", index_query)
        self.assertIn("ADD CONSTRAINT news_feeds_publisher_key", index_query)
        self.assertIn("news_items_feed_guid_idx", index_query)
        self.assertIn("news_items_feed_link_idx", index_query)


if __name__ == "__main__":
    unittest.main()
