from fastapi import FastAPI
import asyncpg
import redis
from src.logging import setup_logging
from src.auth import auth_router
from src.database_init import init_db
from contextlib import asynccontextmanager
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 데이터베이스 연결
    pg_host = os.getenv("PG_HOST", "localhost")
    pg_port = os.getenv("PG_PORT", "5432")
    pg_user = os.getenv("PG_USER", "saver")
    pg_password = os.getenv("PG_USER", "saver")
    pg_database = os.getenv("PG_USER", "saverdb")
    app.state.pool = await asyncpg.create_pool(
        user=pg_user,
        password=pg_password,
        database=pg_database,
        host=pg_host,
        port=pg_port,
    )
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    app.state.redis = redis.Redis(host=redis_host, port=redis_port, db=0)
    init_db()
    # 카카오 소셜 로그인
    kakao_client_secret = os.getenv("KAKAO_SECRET")
    kakao_client_key = os.getenv("KAKAO_KEY")
    if kakao_client_secret is None or kakao_client_key is None:
        raise ValueError("KAKAO_SECRET or KAKAO_KEY is not set")
    app.state.kakao_client_secret = kakao_client_secret
    app.state.kakao_client_key = kakao_client_key
    app.state.host = os.getenv("HOST", "http://localhost:5050")

    app.include_router(auth_router)
    yield
    await app.state.pool.close()


app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

