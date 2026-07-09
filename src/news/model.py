from datetime import datetime
from typing import Annotated

import asyncpg
from fastapi import HTTPException, Path, Query, status
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

NewsPageSize = Annotated[
    int,
    Query(
        ge=1,
        le=100,
        description=(
            "한 페이지에 반환할 뉴스 수(1~100). 서버는 다음 페이지 존재 여부를 확인하기 위해 "
            "내부적으로 1건을 더 조회하지만, 응답의 items에는 이 값 이하의 항목만 포함합니다."
        ),
        examples=[20],
    ),
]

NewsCursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=512,
        description=(
            "이전 페이지 응답의 next_cursor 값을 그대로 전달합니다. 첫 페이지를 조회할 때는 "
            "생략합니다. 커서는 최신순 정렬 위치(pub_date, id)를 담은 불투명한 값이므로 "
            "클라이언트에서 해석하거나 수정하지 않아야 합니다. publisher 같은 필터 조건을 "
            "바꿀 때는 기존 커서를 재사용하지 말고 cursor 없이 다시 조회해야 합니다."
        ),
        examples=[
            "eyJpZCI6MTIzLCJwdWJfZGF0ZSI6IjIwMjYtMDctMDlUMTI6MDA6MDArMDk6MDAiLCJ2IjoxfQ"
        ],
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

PublisherPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=200,
        description="조회할 발행자 이름. 앞뒤 공백을 제거한 뒤 정확히 일치하는 발행자를 찾습니다.",
        examples=["한국외대 학보"],
    ),
]


class NewsItemResponse(BaseModel):
    id: int = Field(description="뉴스 항목의 DB ID", examples=[1])
    publisher: str = Field(description="발행자 또는 언론사 이름", examples=["한국외대 학보"])
    feed_title: str = Field(description="RSS 채널 제목")
    title: str = Field(min_length=1, description="RSS item title")
    link: str = Field(min_length=1, description="원문 URL")
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


class NewsPublisherResponse(BaseModel):
    id: int = Field(description="RSS 채널의 DB ID", examples=[1])
    publisher: str = Field(description="발행자 또는 언론사 이름", examples=["한국외대 학보"])
    feed_url: str = Field(description="RSS 채널 URL", examples=["https://example.com/rss.xml"])
    title: str = Field(description="RSS channel title", examples=["한국외대 학보 RSS"])
    link: str = Field(description="발행자 또는 RSS 채널의 대표 URL", examples=["https://example.com"])
    description: str = Field(description="RSS channel description")
    language: str | None = Field(description="RSS channel language")
    copyright: str | None = Field(description="RSS channel copyright")
    managing_editor: str | None = Field(description="RSS channel managingEditor")
    web_master: str | None = Field(description="RSS channel webMaster")
    pub_date: datetime | None = Field(description="RSS channel pubDate")
    last_build_date: datetime | None = Field(description="RSS channel lastBuildDate")
    generator: str | None = Field(description="RSS channel generator")
    docs: str | None = Field(description="RSS specification 문서 URL")
    ttl: int | None = Field(description="RSS channel TTL(분)")
    image: dict | None = Field(description="RSS channel image 원 구조")
    rating: str | None = Field(description="RSS channel rating")
    categories: list[str] = Field(description="RSS channel category 이름 목록")


class NewsPageResponse(BaseModel):
    items: list[NewsItemResponse] = Field(description="현재 페이지에 포함된 뉴스 항목")
    next_cursor: str | None = Field(
        description="다음 페이지를 조회할 때 cursor 파라미터로 전달할 값. 더 조회할 항목이 없으면 null입니다.",
        examples=[
            "eyJpZCI6MTIzLCJwdWJfZGF0ZSI6IjIwMjYtMDctMDlUMTI6MDA6MDArMDk6MDAiLCJ2IjoxfQ"
        ],
    )
    has_more: bool = Field(description="현재 조건으로 다음 페이지가 존재하는지 여부", examples=[True])
    page_size: int = Field(description="요청에 사용된 페이지 크기", examples=[20])
    order: str = Field(
        default="pub_date DESC NULLS LAST, id DESC",
        description="페이지 커서가 기준으로 삼는 고정 정렬 규칙",
        examples=["pub_date DESC NULLS LAST, id DESC"],
    )


def storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("News storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="뉴스 저장소를 일시적으로 사용할 수 없습니다.",
    )
