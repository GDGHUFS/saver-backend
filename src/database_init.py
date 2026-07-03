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
