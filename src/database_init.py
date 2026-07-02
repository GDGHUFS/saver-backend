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
