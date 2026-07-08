from fastapi import APIRouter, HTTPException, Request, status

from src.news.model import (
    DATABASE_ERRORS,
    NewsCount,
    NewsItemResponse,
    Publisher,
    storage_unavailable,
)


router = APIRouter(prefix="/news", tags=["뉴스"])


@router.get(
    "/latest",
    response_model=list[NewsItemResponse],
    summary="최신 뉴스 조회",
    description=(
        "별도 RSS 수집 작업자가 PostgreSQL에 저장한 뉴스 중 최신 항목을 반환합니다. "
        "publisher를 생략하면 모든 발행자를 대상으로 하고, 지정하면 이름이 정확히 일치하는 "
        "발행자의 항목만 조회합니다. 이 API는 RSS를 직접 수집하지 않으며 로그인이 필요하지 않습니다."
    ),
    responses={
        200: {"description": "조건에 맞는 뉴스를 최신 발행순으로 반환함"},
        422: {"description": "조회 개수 또는 발행자 이름이 유효하지 않음"},
        503: {"description": "뉴스 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def get_latest_news(
    request: Request,
    count: NewsCount = 10,
    publisher: Publisher = None,
) -> list[NewsItemResponse]:
    normalized_publisher = publisher.strip() if publisher is not None else None
    if publisher is not None and not normalized_publisher:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="발행자 이름은 공백일 수 없습니다.",
        )

    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT news_items.id,
                       news_feeds.publisher,
                       news_feeds.title AS feed_title,
                       news_items.title,
                       news_items.link,
                       news_items.description,
                       news_items.author,
                       news_items.comments,
                       news_items.enclosure_url,
                       news_items.enclosure_length,
                       news_items.enclosure_type,
                       news_items.guid,
                       news_items.guid_is_permalink,
                       news_items.pub_date,
                       news_items.source_name,
                       news_items.source_url,
                       COALESCE(
                           (
                               SELECT array_agg(category.name ORDER BY category.id)
                               FROM news_item_categories AS category
                               WHERE category.item_id = news_items.id
                           ),
                           ARRAY[]::TEXT[]
                       ) AS categories
                FROM news_items
                         INNER JOIN news_feeds ON news_feeds.id = news_items.feed_id
                WHERE ($2::TEXT IS NULL OR news_feeds.publisher = $2)
                ORDER BY news_items.pub_date DESC NULLS LAST, news_items.id DESC
                LIMIT $1
                """,
                count,
                normalized_publisher,
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("latest", exc) from exc

    return [NewsItemResponse.model_validate(dict(row)) for row in rows]
