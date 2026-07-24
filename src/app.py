# 외부 패키지
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import asyncpg
from redis.asyncio import Redis
# 내부 패키지
from src.log import setup_logging
from src.auth import auth_router
from src.database_init import init_db
from src.blog import blog_router
from src.news import news_router
from src.search import search_router
from src.search.publisher import RabbitMQSearchPublisher, RabbitMQSettings
from src.search.store import RedisSearchStore
from src.special_days import special_days_router
from src.weather import weather_router
from src.weather.cache import RedisWeatherCache
# 기본 패키지
from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit


def required_secret_from_env(name: str, *, min_length: int = 1) -> str:
    value = os.getenv(name, "")
    if not value.strip() or len(value.encode("utf-8")) < min_length:
        raise ValueError(f"{name} is required and must be sufficiently long")
    return value


def frontend_url_from_env() -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
    parsed = urlsplit(frontend_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "FRONTEND_URL must be an absolute HTTP(S) URL without credentials, "
            "query, or fragment"
        )
    return frontend_url


def cors_allowed_origins_from_env(frontend_url: str | None = None) -> list[str]:
    origins = [
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if "*" in origins:
        raise ValueError("CORS_ALLOWED_ORIGINS must not contain wildcard origin")

    if frontend_url is not None:
        parsed = urlsplit(frontend_url)
        frontend_origin = f"{parsed.scheme}://{parsed.netloc}"
        origins.insert(0, frontend_origin)
    return list(dict.fromkeys(origins))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 카카오 소셜 로그인
    kakao_client_secret = required_secret_from_env("KAKAO_SECRET")
    kakao_client_key = required_secret_from_env("KAKAO_KEY")
    session_secret = required_secret_from_env("SESSION_SECRET", min_length=32)

    pg_host = os.getenv("PG_HOST", "localhost")
    pg_port = int(os.getenv("PG_PORT", "5432"))
    pg_user = os.getenv("PG_USER", "saver")
    pg_password = os.getenv("PG_PASSWORD", "saver")
    pg_database = os.getenv("PG_DATABASE", "saverdb")
    pool = None
    redis_client = None
    search_publisher = None
    try:
        pool = await asyncpg.create_pool(
            user=pg_user,
            password=pg_password,
            database=pg_database,
            host=pg_host,
            port=pg_port,
        )
        app.state.pool = pool

        redis_client = Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        await redis_client.ping()
        app.state.redis = redis_client
        app.state.search_store = RedisSearchStore(
            redis_client,
            ticket_ttl=int(os.getenv("SEARCH_MAGIC_CODE_TTL", "60")),
            query_ttl=int(os.getenv("SEARCH_QUERY_TTL", "180")),
            rate_limit_secret=session_secret,
            submission_limit=int(os.getenv("SEARCH_RATE_LIMIT_MAX", "10")),
            rate_limit_window=int(os.getenv("SEARCH_RATE_LIMIT_WINDOW", "60")),
        )
        app.state.weather_cache = RedisWeatherCache(
            redis_client,
            current_ttl=int(os.getenv("WEATHER_CURRENT_CACHE_TTL", "300")),
        )

        search_publisher = RabbitMQSearchPublisher(
            RabbitMQSettings(
                host=os.getenv("RABBITMQ_HOST", "localhost"),
                username=os.getenv("RABBITMQ_USER", "guest"),
                password=os.getenv("RABBITMQ_PASSWORD", "guest"),
                port=int(os.getenv("RABBITMQ_PORT", "5672")),
                virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
                queue=os.getenv("SEARCH_QUEUE", "saver.search.requests"),
            )
        )
        await search_publisher.start()
        app.state.search_publisher = search_publisher

        await init_db(app.state.pool)
        app.state.kakao_client_secret = kakao_client_secret
        app.state.kakao_client_key = kakao_client_key
        app.state.host = os.getenv("HOST", "http://localhost:5050").rstrip("/")
        app.state.frontend_url = frontend_url
        app.state.default_profile_image = os.getenv(
            "DEFAULT_PROFILE_IMAGE_URL",
            f"{app.state.host}/assets/default-profile.svg",
        )
        app.state.session_secret = session_secret
        app.state.session_max_age = int(os.getenv("SESSION_MAX_AGE", "604800"))
        if app.state.session_max_age <= 0:
            raise ValueError("SESSION_MAX_AGE must be greater than zero")
        yield
    finally:
        if search_publisher is not None:
            await search_publisher.close()
        if redis_client is not None:
            await redis_client.aclose()
        if pool is not None:
            await pool.close()


app = FastAPI(
    title="Saver Backend API",
    description=(
        "Saver frontend가 사용하는 통합 backend API입니다. "
        "카카오 로그인과 사용자 세션, 검색 접수, 간단한 블로그, 뉴스·특일·날씨 조회 및 "
        "포털 진입점 기능을 제공합니다."
    ),
    version="0.2.1",
    lifespan=lifespan,
)
frontend_url = frontend_url_from_env()
cors_allowed_origins = cors_allowed_origins_from_env(frontend_url)
if cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Location"],
    )
app.include_router(auth_router)
app.include_router(blog_router, prefix="/blog")
app.include_router(search_router)
app.include_router(news_router)
app.include_router(special_days_router)
app.include_router(weather_router)


DEFAULT_PROFILE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect width="256" height="256" fill="#e5e7eb"/>
<circle cx="128" cy="96" r="48" fill="#9ca3af"/>
<path d="M40 232c8-51 39-76 88-76s80 25 88 76" fill="#9ca3af"/>
</svg>"""


@app.get("/assets/default-profile.svg", include_in_schema=False)
async def default_profile_image():
    return Response(content=DEFAULT_PROFILE_SVG, media_type="image/svg+xml")


@app.get(
    "/",
    tags=["서비스"],
    summary="서비스 상태 확인",
    description="Saver backend 프로세스가 HTTP 요청에 응답하는지 확인하는 기본 엔드포인트입니다.",
    responses={200: {"description": "backend가 정상적으로 응답함"}},
)
async def root():
    return {"message": "Hello World"}
