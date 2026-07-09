import base64
import binascii
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from src.news.model import (
    DATABASE_ERRORS,
    NewsCount,
    NewsCursor,
    NewsItemResponse,
    NewsPageResponse,
    NewsPageSize,
    NewsPublisherResponse,
    Publisher,
    PublisherPath,
    storage_unavailable,
)


router = APIRouter(prefix="/news", tags=["뉴스"])

_NEWS_CURSOR_VERSION = 1
_NEWS_ITEM_SELECT = """
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
"""
_NEWS_PUBLISHER_SELECT = """
                SELECT news_feeds.id,
                       news_feeds.publisher,
                       news_feeds.feed_url,
                       news_feeds.title,
                       news_feeds.link,
                       news_feeds.description,
                       news_feeds.language,
                       news_feeds.copyright,
                       news_feeds.managing_editor,
                       news_feeds.web_master,
                       news_feeds.pub_date,
                       news_feeds.last_build_date,
                       news_feeds.generator,
                       news_feeds.docs,
                       news_feeds.ttl,
                       news_feeds.image,
                       news_feeds.rating,
                       COALESCE(
                           (
                               SELECT array_agg(category.name ORDER BY category.id)
                               FROM news_feed_categories AS category
                               WHERE category.feed_id = news_feeds.id
                           ),
                           ARRAY[]::TEXT[]
                       ) AS categories
                FROM news_feeds
"""


def _normalize_publisher(publisher: str | None) -> str | None:
    normalized_publisher = publisher.strip() if publisher is not None else None
    if publisher is not None and not normalized_publisher:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="발행자 이름은 공백일 수 없습니다.",
        )
    return normalized_publisher


def _news_publisher_response(row: dict) -> NewsPublisherResponse:
    data = dict(row)
    image = data["image"]
    if isinstance(image, str):
        data["image"] = json.loads(image)
    return NewsPublisherResponse.model_validate(data)


def _encode_news_cursor(row: dict) -> str:
    pub_date = row["pub_date"]
    payload = {
        "id": row["id"],
        "pub_date": pub_date.isoformat() if pub_date is not None else None,
        "v": _NEWS_CURSOR_VERSION,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.decode("ascii").rstrip("=")


def _decode_news_cursor(cursor: str | None) -> tuple[int | None, datetime | None]:
    if cursor is None:
        return None, None

    try:
        padded_cursor = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_cursor).decode("utf-8"))
        item_id = payload["id"]
        pub_date_value = payload["pub_date"]
        version = payload["v"]
    except (binascii.Error, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise _invalid_news_cursor() from None

    if version != _NEWS_CURSOR_VERSION or type(item_id) is not int or item_id <= 0:
        raise _invalid_news_cursor()
    if pub_date_value is None:
        return item_id, None
    if not isinstance(pub_date_value, str):
        raise _invalid_news_cursor()

    try:
        pub_date = datetime.fromisoformat(pub_date_value)
    except ValueError:
        raise _invalid_news_cursor() from None
    if pub_date.tzinfo is None:
        raise _invalid_news_cursor()

    return item_id, pub_date


def _invalid_news_cursor() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="뉴스 페이지 커서가 유효하지 않습니다.",
    )


@router.get(
    "/publishers",
    response_model=list[NewsPublisherResponse],
    summary="뉴스 발행자 목록 조회",
    description=(
        "별도 RSS 수집 작업자가 PostgreSQL에 저장한 RSS 채널의 발행자 정보를 모두 반환합니다. "
        "발행자 이름은 `news_feeds.publisher`를 기준으로 하며, 이 API는 RSS를 직접 수집하지 "
        "않고 로그인이 필요하지 않습니다."
    ),
    responses={
        200: {"description": "저장된 발행자 정보를 이름순으로 반환함"},
        503: {"description": "뉴스 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def get_news_publishers(request: Request) -> list[NewsPublisherResponse]:
    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                _NEWS_PUBLISHER_SELECT
                + """
                ORDER BY news_feeds.publisher ASC, news_feeds.id ASC
                """
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("publishers", exc) from exc

    return [_news_publisher_response(dict(row)) for row in rows]


@router.get(
    "/publishers/{publisher}",
    response_model=NewsPublisherResponse,
    summary="뉴스 발행자 정보 조회",
    description=(
        "이름이 정확히 일치하는 RSS 채널 발행자 정보를 반환합니다. 경로의 publisher 값은 "
        "앞뒤 공백을 제거한 뒤 `news_feeds.publisher`와 정확히 비교합니다. 저장된 발행자가 "
        "없으면 수집 작업을 시작하지 않고 404를 반환합니다. 이 API는 로그인이 필요하지 않습니다."
    ),
    responses={
        200: {"description": "요청한 발행자 정보를 반환함"},
        404: {"description": "요청한 이름의 발행자가 저장되어 있지 않음"},
        422: {"description": "발행자 이름이 유효하지 않음"},
        503: {"description": "뉴스 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def get_news_publisher(
    request: Request,
    publisher: PublisherPath,
) -> NewsPublisherResponse:
    normalized_publisher = _normalize_publisher(publisher)

    try:
        async with request.app.state.pool.acquire() as connection:
            row = await connection.fetchrow(
                _NEWS_PUBLISHER_SELECT
                + """
                WHERE news_feeds.publisher = $1
                """,
                normalized_publisher,
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("publisher", exc) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="요청한 발행자를 찾을 수 없습니다.",
        )

    return _news_publisher_response(dict(row))


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
    normalized_publisher = _normalize_publisher(publisher)

    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                _NEWS_ITEM_SELECT
                + """
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


@router.get(
    "/latest/page",
    response_model=NewsPageResponse,
    summary="최신 뉴스 페이지 조회",
    description=(
        "별도 RSS 수집 작업자가 PostgreSQL에 저장한 뉴스 항목을 커서 기반 페이지네이션으로 "
        "조회합니다. 정렬 기준은 기존 최신 뉴스 API와 동일하게 "
        "`news_items.pub_date DESC NULLS LAST, news_items.id DESC`입니다. "
        "첫 페이지는 cursor 없이 호출하고, 응답의 `next_cursor`가 null이 아니면 다음 페이지 "
        "요청의 cursor 파라미터로 그대로 전달합니다. offset 방식과 달리 앞 페이지에 새 뉴스가 "
        "추가되어도 이미 받은 커서 이후의 항목을 안정적으로 이어서 조회할 수 있습니다. "
        "publisher를 지정하면 해당 발행자 이름이 정확히 일치하는 항목만 같은 방식으로 페이지 "
        "조회합니다. cursor는 같은 필터 조건에서 이어 보기 위한 값이므로 publisher를 바꿀 때는 "
        "cursor 없이 첫 페이지부터 다시 조회해야 합니다. 이 API는 RSS를 직접 수집하지 않으며 "
        "로그인이 필요하지 않습니다."
    ),
    responses={
        200: {
            "description": (
                "현재 페이지의 뉴스와 다음 페이지 이동용 커서를 반환함. next_cursor가 null이면 "
                "더 조회할 뉴스가 없습니다."
            )
        },
        422: {
            "description": (
                "페이지 크기, 발행자 이름 또는 커서가 유효하지 않음. 커서는 서버가 발급한 "
                "next_cursor 값을 수정 없이 사용해야 합니다."
            )
        },
        503: {"description": "뉴스 저장소를 일시적으로 사용할 수 없음"},
    },
)
async def get_latest_news_page(
    request: Request,
    page_size: NewsPageSize = 20,
    publisher: Publisher = None,
    cursor: NewsCursor = None,
) -> NewsPageResponse:
    normalized_publisher = _normalize_publisher(publisher)
    cursor_id, cursor_pub_date = _decode_news_cursor(cursor)

    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                _NEWS_ITEM_SELECT
                + """
                WHERE ($2::TEXT IS NULL OR news_feeds.publisher = $2)
                  AND (
                      $3::BIGINT IS NULL
                      OR (
                          $4::TIMESTAMPTZ IS NOT NULL
                          AND (
                              news_items.pub_date < $4::TIMESTAMPTZ
                              OR (
                                  news_items.pub_date = $4::TIMESTAMPTZ
                                  AND news_items.id < $3
                              )
                              OR news_items.pub_date IS NULL
                          )
                      )
                      OR (
                          $4::TIMESTAMPTZ IS NULL
                          AND news_items.pub_date IS NULL
                          AND news_items.id < $3
                      )
                  )
                ORDER BY news_items.pub_date DESC NULLS LAST, news_items.id DESC
                LIMIT $1
                """,
                page_size + 1,
                normalized_publisher,
                cursor_id,
                cursor_pub_date,
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("latest-page", exc) from exc

    page_rows = [dict(row) for row in rows[:page_size]]
    items = [NewsItemResponse.model_validate(row) for row in page_rows]
    has_more = len(rows) > page_size
    next_cursor = _encode_news_cursor(page_rows[-1]) if has_more and page_rows else None

    return NewsPageResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        page_size=page_size,
    )
