from datetime import datetime
from typing import Annotated

import asyncpg
from fastapi import HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field


DATABASE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError, OSError)

NewsCount = Annotated[
    int,
    Query(
        ge=1,
        le=100,
        description="반환할 뉴스 수(1~100)",
        examples=[10],
    ),
]

Publisher = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=200,
        description="이름이 정확히 일치하는 발행자만 조회. 생략하면 전체 발행자를 조회합니다.",
        examples=["한국외대 학보"],
    ),
]


class NewsItemResponse(BaseModel):
    id: int = Field(description="뉴스 항목의 DB ID", examples=[1])
    publisher: str = Field(description="발행자 또는 언론사 이름", examples=["한국외대 학보"])
    feed_title: str = Field(description="RSS 채널 제목")
    title: str | None = Field(description="RSS item title")
    link: str | None = Field(description="원문 URL")
    description: str | None = Field(description="RSS item description")
    author: str | None = Field(description="RSS item author")
    comments: str | None = Field(description="댓글 페이지 URL")
    enclosure_url: str | None = Field(description="첨부 미디어 URL")
    enclosure_length: int | None = Field(description="첨부 미디어의 바이트 길이")
    enclosure_type: str | None = Field(description="첨부 미디어의 MIME 타입")
    guid: str | None = Field(description="RSS item GUID")
    guid_is_permalink: bool | None = Field(description="GUID가 영구 링크인지 여부")
    pub_date: datetime | None = Field(description="RSS에 기록된 발행 시각")
    source_name: str | None = Field(description="재배포 항목의 원 출처 이름")
    source_url: str | None = Field(description="재배포 항목의 원 출처 RSS URL")
    categories: list[str] = Field(description="RSS item category 이름 목록")


def storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("News storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="뉴스 저장소를 일시적으로 사용할 수 없습니다.",
    )
