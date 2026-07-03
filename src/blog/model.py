# 기본 패키지
from datetime import datetime
from typing import Annotated
# 외부 패키지
from fastapi import HTTPException, Path, status, Query
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
import asyncpg

# 애플리케이션 오류와 구분할 수 있는 일시적인 저장소 장애만 안정적인 API 오류로 변환한다.
DATABASE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError, OSError)

BlogId = Annotated[
    int,
    Path(
        ge=1,
        description="조회하거나 변경할 블로그 글의 양의 정수 ID",
        examples=[1],
    ),
]

UserId = Annotated[
    int,
    Path(
        ge=1,
        description="블로그 글을 조회할 사용자의 양의 정수 ID",
        examples=[123456789],
    ),
]

Count = Annotated[
    int,
    Query(
        ge=1,
        le=100,
        description="조회할 글의 수",
        examples=[3]
    )
]


class BlogWriteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(
        min_length=1,
        max_length=300,
        description="앞뒤 공백을 제외하고 1자 이상 300자 이하인 제목",
        examples=["Saver 개발 기록"],
    )
    content: str = Field(
        min_length=1,
        description="앞뒤 공백을 제외하고 한 글자 이상인 본문",
        examples=["Saver backend의 블로그 API를 구현했습니다."],
    )


class BlogResponse(BaseModel):
    id: int = Field(description="블로그 글 ID", examples=[1])
    title: str = Field(description="글 제목", examples=["Saver 개발 기록"])
    content: str = Field(description="글 본문")
    created_at: datetime = Field(description="글 생성 시각(ISO 8601)")
    updated_at: datetime = Field(description="마지막 수정 시각(ISO 8601)")
    author_id: int = Field(description="작성자의 DB ID", examples=["123456789"])
    nickname: str = Field(description="작성자의 현재 표시 이름", examples=["Saver 사용자"])
    profile_image: str = Field(
        description="작성자의 현재 프로필 이미지 URL",
        examples=["https://example.com/profile.png"],
    )


class SimpleBlogResponse(BaseModel):
    id: int = Field(description="블로그 글 ID", examples=[1])
    title: str = Field(description="글 제목", examples=["Saver 개발 기록"])
    created_at: datetime = Field(description="글 생성 시각(ISO 8601)")
    updated_at: datetime = Field(description="마지막 수정 시각(ISO 8601)")
    author_id: int = Field(description="작성자의 DB ID", examples=["123456789"])
    nickname: str = Field(description="작성자의 현재 표시 이름", examples=["Saver 사용자"])
    profile_image: str = Field(
        description="작성자의 현재 프로필 이미지 URL",
        examples=["https://example.com/profile.png"],
    )


def _storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    # DB 예외 원문에는 내부 스키마나 접속 정보가 포함될 수 있으므로 그대로 기록하거나 노출하지 않는다.
    logger.error("Blog storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="블로그 저장소를 일시적으로 사용할 수 없습니다.",
    )
