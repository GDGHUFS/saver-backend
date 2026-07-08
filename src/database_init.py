import asyncpg


async def init_db(pool: asyncpg.Pool) -> None:
    """Create the database objects required by this service.

    ``CREATE TABLE IF NOT EXISTS`` makes startup safe when multiple backend
    instances start at the same time. Schema changes after the initial release
    should be handled by a migration instead of being added here implicitly.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                nickname TEXT NOT NULL CHECK (length(btrim(nickname)) > 0),
                profile_image TEXT NOT NULL CHECK (length(btrim(profile_image)) > 0)
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blogs (
                id serial,
                title TEXT NOT NULL CHECK (length(btrim(title)) > 0),
                content TEXT NOT NULL CHECK (length(btrim(content)) > 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                author_id BIGINT NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_feeds (
                id BIGSERIAL PRIMARY KEY,
                feed_url TEXT NOT NULL UNIQUE CHECK (length(btrim(feed_url)) > 0),
                publisher TEXT NOT NULL CHECK (length(btrim(publisher)) > 0),
                title TEXT NOT NULL CHECK (length(btrim(title)) > 0),
                link TEXT NOT NULL CHECK (length(btrim(link)) > 0),
                description TEXT NOT NULL,
                language TEXT,
                copyright TEXT,
                managing_editor TEXT,
                web_master TEXT,
                pub_date TIMESTAMPTZ,
                last_build_date TIMESTAMPTZ,
                generator TEXT,
                docs TEXT,
                cloud JSONB,
                ttl INTEGER CHECK (ttl IS NULL OR ttl >= 0),
                image JSONB,
                rating TEXT,
                text_input JSONB,
                skip_hours SMALLINT[] NOT NULL DEFAULT '{}',
                skip_days TEXT[] NOT NULL DEFAULT '{}',
                extensions JSONB NOT NULL DEFAULT '{}'::JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_feed_categories (
                id BIGSERIAL PRIMARY KEY,
                feed_id BIGINT NOT NULL REFERENCES news_feeds(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
                domain TEXT
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_items (
                id BIGSERIAL PRIMARY KEY,
                feed_id BIGINT NOT NULL REFERENCES news_feeds(id) ON DELETE CASCADE,
                title TEXT,
                link TEXT,
                description TEXT,
                author TEXT,
                comments TEXT,
                enclosure_url TEXT,
                enclosure_length BIGINT CHECK (
                    enclosure_length IS NULL OR enclosure_length >= 0
                ),
                enclosure_type TEXT,
                guid TEXT,
                guid_is_permalink BOOLEAN,
                pub_date TIMESTAMPTZ,
                source_name TEXT,
                source_url TEXT,
                extensions JSONB NOT NULL DEFAULT '{}'::JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (
                    (title IS NOT NULL AND length(btrim(title)) > 0)
                    OR (description IS NOT NULL AND length(btrim(description)) > 0)
                )
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_item_categories (
                id BIGSERIAL PRIMARY KEY,
                item_id BIGINT NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
                domain TEXT
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS news_items_latest_idx
                ON news_items (pub_date DESC NULLS LAST, id DESC);
            CREATE INDEX IF NOT EXISTS news_feeds_publisher_idx
                ON news_feeds (publisher);
            CREATE UNIQUE INDEX IF NOT EXISTS news_items_feed_guid_idx
                ON news_items (feed_id, guid)
                WHERE guid IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS news_items_feed_link_idx
                ON news_items (feed_id, link)
                WHERE guid IS NULL AND link IS NOT NULL
            """
        )
