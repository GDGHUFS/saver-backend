# 기본 패키지
from typing import List
# 외부 패키지
from fastapi import APIRouter, HTTPException, Request, status
# 내부 패키지
from src.blog.model import (
    BlogId,
    BlogResponse,
    Count,
    DATABASE_ERRORS,
    SimpleBlogResponse,
    UserId,
    _storage_unavailable,
)


router = APIRouter()


@router.get(
    "/latest",
    response_model=List[SimpleBlogResponse],
    summary="최근 몇 개의 글을 반환합니다.",
    description=(
            "블로그 글을 제외한 정보와 글쓴이 정보 꾸러미를 리스트 형태로 반환합니다."
            "count 쿼리 매개변수의 값만큼 글을 반환합니다. count가 블로그 총 개수보다 클 경우 블로그 개수만큼만 반환합니다."
    ),
    responses={
        200: {"description": "블로그 글을 반환함"},
        422: {"description": "블로그 글 ID가 유효한 범위의 정수가 아님"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    }
)
async def get_latest_blog(request: Request, count: Count = 3):
    app = request.app
    try:
        async with app.state.pool.acquire() as connection:
            posts = await connection.fetch("""
                                           SELECT blogs.id,
                                                  blogs.title,
                                                  blogs.created_at,
                                                  blogs.updated_at,
                                                  blogs.author_id,
                                                  users.nickname,
                                                  users.profile_image
                                           FROM blogs
                                                    INNER JOIN users ON blogs.author_id = users.id
                                           ORDER BY blogs.created_at DESC LIMIT $1
                                           """, count)
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("latest", exc) from exc
    return [SimpleBlogResponse.model_validate(dict(row)) for row in posts]


@router.get(
    "/author/{user_id}",
    response_model=List[SimpleBlogResponse],
    summary="특정 사용자의 블로그 글 전체 조회",
    description=(
        "URL 경로로 전달한 사용자 ID가 작성한 모든 블로그 글을 최신 작성순으로 반환합니다. "
        "사용자는 존재하지만 작성한 글이 없으면 빈 목록을 반환하며, 로그인하지 않은 사용자도 호출할 수 있습니다."
    ),
    responses={
        200: {"description": "사용자가 작성한 블로그 글 목록을 반환함"},
        404: {"description": "해당 ID의 사용자가 없음"},
        422: {"description": "사용자 ID가 유효한 양의 정수가 아님"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def get_user_blogs(request: Request, user_id: UserId) -> List[SimpleBlogResponse]:
    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT blogs.id,
                       blogs.title,
                       blogs.created_at,
                       blogs.updated_at,
                       users.id AS author_id,
                       users.nickname,
                       users.profile_image
                FROM users
                         LEFT JOIN blogs ON blogs.author_id = users.id
                WHERE users.id = $1
                ORDER BY blogs.created_at DESC, blogs.id DESC
                """,
                user_id,
            )
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("read-by-author", exc) from exc

    # LEFT JOIN은 사용자가 존재하면 글이 없어도 blogs.id가 NULL인 행 하나를 반환한다.
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="사용자를 찾을 수 없습니다.",
        )
    if rows[0]["id"] is None:
        return []

    return [SimpleBlogResponse.model_validate(dict(row)) for row in rows]


@router.get(
    "/{blog_id}",
    response_model=BlogResponse,
    summary="블로그 글 조회",
    description=(
            "블로그 글과 작성자의 현재 닉네임 및 프로필 이미지를 조회합니다. "
            "로그인하지 않은 사용자도 호출할 수 있습니다."
    ),
    responses={
        200: {"description": "블로그 글을 반환함"},
        404: {"description": "해당 ID의 블로그 글이 없음"},
        422: {"description": "블로그 글 ID가 유효한 양의 정수가 아님"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def read_blog(request: Request, blog_id: BlogId) -> BlogResponse:
    try:
        async with request.app.state.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT blogs.id,
                       blogs.title,
                       blogs.content,
                       blogs.created_at,
                       blogs.updated_at,
                       blogs.author_id,
                       users.nickname,
                       users.profile_image
                FROM blogs
                         INNER JOIN users ON blogs.author_id = users.id
                WHERE blogs.id = $1
                """,
                blog_id,
            )
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("read", exc) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="블로그 글을 찾을 수 없습니다.",
        )
    # asyncpg.Record를 명시적인 응답 모델로 변환해 직렬화 형식과 OpenAPI 스키마를 일치시킨다.
    return BlogResponse.model_validate(dict(row))
