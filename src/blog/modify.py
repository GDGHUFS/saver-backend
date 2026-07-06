# 기본 패키지
from typing import Annotated
# 외부 패키지
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
# 내부 패키지
from src.auth import get_current_user_id
from src.blog.model import BlogWriteRequest, BlogId, _storage_unavailable, DATABASE_ERRORS


router = APIRouter()


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    summary="블로그 글 작성",
    description=(
            "로그인한 사용자의 새 블로그 글을 작성합니다. 제목과 본문의 앞뒤 공백은 제거되며, "
            "생성된 글의 주소는 응답의 `Location` 헤더로 반환합니다."
    ),
    responses={
        201: {
            "description": "블로그 글이 생성됨",
            "headers": {
                "Location": {
                    "description": "생성된 글의 상대 URL",
                    "schema": {"type": "string", "example": "/blog/1"},
                }
            },
        },
        401: {"description": "로그인 세션이 없거나 유효하지 않음"},
        422: {"description": "제목, 본문 또는 요청 형식이 유효하지 않음"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def write_blog(
        request: Request,
        blog: BlogWriteRequest,
        user_id: Annotated[int, Depends(get_current_user_id)],
):
    try:
        async with request.app.state.pool.acquire() as connection:
            blog_id = await connection.fetchval(
                """
                INSERT INTO blogs (author_id, title, content)
                VALUES ($1, $2, $3) RETURNING id
                """,
                user_id,
                blog.title,
                blog.content,
            )
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("create", exc) from exc

    # INSERT ... RETURNING은 정상 실행 시 반드시 ID를 반환한다. 예상 밖의 값도 성공으로 응답하지 않는다.
    if not isinstance(blog_id, int):
        raise _storage_unavailable("create-result", RuntimeError("missing blog id"))

    logger.info("Blog {} created", blog_id)
    return Response(
        status_code=status.HTTP_201_CREATED,
        headers={"Location": f"/blog/{blog_id}"},
    )





@router.delete(
    "/{blog_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="블로그 글 삭제",
    description=(
            "로그인한 사용자가 자신이 작성한 블로그 글을 삭제합니다. 글이 없거나 작성자가 아니면 "
            "동일하게 404를 반환하며, 성공 응답에는 본문이 없습니다."
    ),
    responses={
        204: {"description": "블로그 글이 삭제됨"},
        401: {"description": "로그인 세션이 없거나 유효하지 않음"},
        404: {"description": "글이 없거나 현재 사용자가 작성자가 아님"},
        422: {"description": "블로그 글 ID가 유효한 양의 정수가 아님"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def delete_blog(
        request: Request,
        blog_id: BlogId,
        user_id: Annotated[int, Depends(get_current_user_id)],
):
    try:
        async with request.app.state.pool.acquire() as connection:
            deleted_id = await connection.fetchval(
                "DELETE FROM blogs WHERE id = $1 AND author_id = $2 RETURNING id",
                blog_id,
                user_id,
            )
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("delete", exc) from exc

    if deleted_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="블로그 글을 찾을 수 없거나 삭제 권한이 없습니다.",
        )

    logger.info("Blog {} deleted", blog_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{blog_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="블로그 글 수정",
    description=(
            "로그인한 사용자가 자신이 작성한 글의 제목과 본문 전체를 교체합니다. 글이 없거나 "
            "작성자가 아니면 동일하게 404를 반환하며, 성공 응답에는 본문이 없습니다."
    ),
    responses={
        204: {"description": "블로그 글이 수정됨"},
        401: {"description": "로그인 세션이 없거나 유효하지 않음"},
        404: {"description": "글이 없거나 현재 사용자가 작성자가 아님"},
        422: {"description": "글 ID, 제목, 본문 또는 요청 형식이 유효하지 않음"},
        503: {"description": "블로그 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def update_blog(
        request: Request,
        blog_id: BlogId,
        blog: BlogWriteRequest,
        user_id: Annotated[int, Depends(get_current_user_id)],
):
    try:
        async with request.app.state.pool.acquire() as connection:
            updated_id = await connection.fetchval(
                """
                UPDATE blogs
                SET title      = $1,
                    content    = $2,
                    updated_at = NOW()
                WHERE id = $3
                  AND author_id = $4 RETURNING id
                """,
                blog.title,
                blog.content,
                blog_id,
                user_id,
            )
    except DATABASE_ERRORS as exc:
        raise _storage_unavailable("update", exc) from exc

    if updated_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="블로그 글을 찾을 수 없거나 수정 권한이 없습니다.",
        )

    logger.info("Blog {} updated", blog_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
