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
                title TEXT NOT NULL CHECK (length(btrim(title)) > 0),
                link TEXT NOT NULL CHECK (length(btrim(link)) > 0),
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
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            CREATE UNIQUE INDEX IF NOT EXISTS news_feeds_publisher_key
                ON news_feeds (publisher);
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'news_feeds_publisher_key'
                ) THEN
                    ALTER TABLE news_feeds
                        ADD CONSTRAINT news_feeds_publisher_key
                        UNIQUE USING INDEX news_feeds_publisher_key;
                END IF;
            END $$;
            CREATE UNIQUE INDEX IF NOT EXISTS news_items_feed_guid_idx
                ON news_items (feed_id, guid)
                WHERE guid IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS news_items_feed_link_idx
                ON news_items (feed_id, link)
                WHERE guid IS NULL AND link IS NOT NULL
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS anniversary_special_days (
                id BIGSERIAL PRIMARY KEY,
                observed_date DATE NOT NULL,
                date_kind TEXT NOT NULL CHECK (date_kind IN ('01', '02', '03', '04')),
                date_name TEXT NOT NULL CHECK (length(btrim(date_name)) > 0),
                is_holiday BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE UNIQUE INDEX IF NOT EXISTS anniversary_special_days_unique_key
                ON anniversary_special_days (observed_date, date_kind, date_name);

            CREATE INDEX IF NOT EXISTS anniversary_special_days_date_idx
                ON anniversary_special_days (observed_date, id);

            CREATE INDEX IF NOT EXISTS anniversary_special_days_holiday_idx
                ON anniversary_special_days (observed_date, id)
                WHERE is_holiday = TRUE
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_grid_points (
                nx SMALLINT NOT NULL CHECK (nx BETWEEN 1 AND 149),
                ny SMALLINT NOT NULL CHECK (ny BETWEEN 1 AND 253),
                longitude DOUBLE PRECISION NOT NULL
                    CHECK (longitude BETWEEN 120 AND 140),
                latitude DOUBLE PRECISION NOT NULL
                    CHECK (latitude BETWEEN 30 AND 45),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (nx, ny)
            );

            -- 재시도와 페이지 호출을 포함한 실제 요청 횟수를 KST 날짜별로 제한한다.
            CREATE TABLE IF NOT EXISTS weather_api_daily_usage (
                usage_date DATE PRIMARY KEY,
                request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS weather_locations (
                administrative_code TEXT PRIMARY KEY
                    CHECK (administrative_code ~ '^[0-9]{10}$'),
                region_level_1 TEXT NOT NULL
                    CHECK (length(btrim(region_level_1)) > 0),
                region_level_2 TEXT
                    CHECK (region_level_2 IS NULL OR length(btrim(region_level_2)) > 0),
                region_level_3 TEXT
                    CHECK (region_level_3 IS NULL OR length(btrim(region_level_3)) > 0),
                nx SMALLINT NOT NULL,
                ny SMALLINT NOT NULL,
                longitude DOUBLE PRECISION,
                latitude DOUBLE PRECISION,
                source_updated_on DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                FOREIGN KEY (nx, ny) REFERENCES weather_grid_points (nx, ny),
                CHECK (region_level_3 IS NULL OR region_level_2 IS NOT NULL),
                CHECK ((longitude IS NULL) = (latitude IS NULL)),
                CHECK (longitude IS NULL OR longitude BETWEEN 120 AND 140),
                CHECK (latitude IS NULL OR latitude BETWEEN 30 AND 45)
            );

            -- 하나의 발표본은 한 격자와 기상청 발표시각의 조합으로 식별한다.
            -- 같은 발표본을 다시 수집하면 새 행을 만들지 않고 최신 값으로 갱신한다.
            CREATE TABLE IF NOT EXISTS weather_forecast_issues (
                id BIGSERIAL PRIMARY KEY,
                nx SMALLINT NOT NULL,
                ny SMALLINT NOT NULL,
                issued_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                FOREIGN KEY (nx, ny) REFERENCES weather_grid_points (nx, ny),
                UNIQUE (nx, ny, issued_at)
            );

            -- API의 장형 category 응답을 화면 조회에 적합한 예보시각별 한 행으로 묶는다.
            -- 값은 수치뿐 아니라 강수없음, 범위, 장기 구간 정성 코드도 제공되므로
            -- 원문 의미를 잃지 않도록 문자열로 저장한다.
            CREATE TABLE IF NOT EXISTS weather_forecasts (
                forecast_issue_id BIGINT NOT NULL
                    REFERENCES weather_forecast_issues (id) ON DELETE CASCADE,
                forecast_at TIMESTAMPTZ NOT NULL,
                precipitation_probability TEXT,
                precipitation_type TEXT,
                precipitation_amount TEXT,
                humidity TEXT,
                snowfall_amount TEXT,
                sky_status TEXT,
                temperature TEXT,
                minimum_temperature TEXT,
                maximum_temperature TEXT,
                wind_u_component TEXT,
                wind_v_component TEXT,
                wave_height TEXT,
                wind_direction TEXT,
                wind_speed TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (forecast_issue_id, forecast_at),
                CHECK (
                    num_nonnulls(
                        precipitation_probability,
                        precipitation_type,
                        precipitation_amount,
                        humidity,
                        snowfall_amount,
                        sky_status,
                        temperature,
                        minimum_temperature,
                        maximum_temperature,
                        wind_u_component,
                        wind_v_component,
                        wave_height,
                        wind_direction,
                        wind_speed
                    ) > 0
                ),
                CHECK (
                    num_nonnulls(
                        precipitation_probability,
                        precipitation_type,
                        precipitation_amount,
                        humidity,
                        snowfall_amount,
                        sky_status,
                        temperature,
                        minimum_temperature,
                        maximum_temperature,
                        wind_u_component,
                        wind_v_component,
                        wave_height,
                        wind_direction,
                        wind_speed
                    ) = num_nonnulls(
                        NULLIF(btrim(precipitation_probability), ''),
                        NULLIF(btrim(precipitation_type), ''),
                        NULLIF(btrim(precipitation_amount), ''),
                        NULLIF(btrim(humidity), ''),
                        NULLIF(btrim(snowfall_amount), ''),
                        NULLIF(btrim(sky_status), ''),
                        NULLIF(btrim(temperature), ''),
                        NULLIF(btrim(minimum_temperature), ''),
                        NULLIF(btrim(maximum_temperature), ''),
                        NULLIF(btrim(wind_u_component), ''),
                        NULLIF(btrim(wind_v_component), ''),
                        NULLIF(btrim(wave_height), ''),
                        NULLIF(btrim(wind_direction), ''),
                        NULLIF(btrim(wind_speed), '')
                    )
                )
            );

            CREATE INDEX IF NOT EXISTS weather_locations_region_names_idx
                ON weather_locations (
                    region_level_1,
                    region_level_2,
                    region_level_3,
                    administrative_code
                );

            CREATE INDEX IF NOT EXISTS weather_locations_region_level_2_idx
                ON weather_locations (region_level_2, administrative_code)
                WHERE region_level_2 IS NOT NULL;

            CREATE INDEX IF NOT EXISTS weather_locations_region_level_3_idx
                ON weather_locations (region_level_3, administrative_code)
                WHERE region_level_3 IS NOT NULL;

            CREATE INDEX IF NOT EXISTS weather_locations_grid_idx
                ON weather_locations (nx, ny, administrative_code);

            CREATE INDEX IF NOT EXISTS weather_forecast_issues_issued_at_idx
                ON weather_forecast_issues (issued_at);

            CREATE INDEX IF NOT EXISTS weather_forecast_issues_latest_grid_idx
                ON weather_forecast_issues (nx, ny, issued_at DESC, id DESC)
            """
        )
