import hashlib
import secrets
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError, TimeoutError as RedisTimeoutError

from src.auth import get_current_user_id
from src.search.model import (
    SearchAcceptedResponse,
    SearchPendingResponse,
    SearchRequest,
    SearchResultResponse,
)
from src.search.publisher import SearchPublishError
from src.search.store import InvalidSearchData


REDIS_ERRORS = (RedisConnectionError, RedisTimeoutError, ResponseError, TimeoutError, OSError)
MagicCode = Annotated[
    str,
    Path(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
        description="검색 접수 응답에서 발급한 magicCode",
    ),
]

router = APIRouter(prefix="/search", tags=["검색"])


def normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).split()).casefold()


def hash_query(normalized_query: str) -> str:
    return hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()


def storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("Search storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="검색 저장소를 일시적으로 사용할 수 없습니다.",
    )


@router.post(
    "",
    response_model=SearchAcceptedResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="검색 작업 접수",
    description=(
        "로그인한 사용자의 검색어를 정규화해 Redis에 짧은 수명의 검색 상태를 만들고 magicCode를 발급합니다. "
        "Redis에 완료된 동일 검색 결과가 없을 때만 RabbitMQ에 검색 명령을 발행하며, "
        "외부 검색 결과를 이 응답에 포함하지 않습니다. 사용자 ID는 검색 데이터에 저장하지 않습니다."
    ),
    responses={
        202: {"description": "검색이 접수되고 결과 조회용 magicCode가 발급됨"},
        401: {"description": "로그인 세션이 없거나 유효하지 않음"},
        429: {"description": "사용자별 검색 접수 허용량을 초과함"},
        422: {"description": "검색어 또는 요청 형식이 유효하지 않음"},
        503: {"description": "인증 DB, Redis 또는 RabbitMQ를 일시적으로 사용할 수 없음"},
    },
)
async def submit_search(
    request: Request,
    search: SearchRequest,
    user_id: Annotated[int, Depends(get_current_user_id)],
) -> SearchAcceptedResponse:
    normalized_query = normalize_query(search.query)
    if not normalized_query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="검색어가 비어 있습니다.",
        )
    try:
        allowed = await request.app.state.search_store.allow_submission(user_id)
    except REDIS_ERRORS as exc:
        raise storage_unavailable("rate-limit", exc) from exc
    except InvalidSearchData as exc:
        raise storage_unavailable("rate-limit-result", exc) from exc
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="검색 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            headers={
                "Retry-After": str(
                    request.app.state.search_store.rate_limit_window
                )
            },
        )

    query_hash = hash_query(normalized_query)
    magic_code = secrets.token_urlsafe(32)

    try:
        should_publish = await request.app.state.search_store.create_ticket(
            magic_code,
            query_hash,
        )
    except REDIS_ERRORS as exc:
        raise storage_unavailable("create-ticket", exc) from exc
    except InvalidSearchData as exc:
        raise storage_unavailable("create-ticket-result", exc) from exc

    if should_publish:
        message = {
            "schemaVersion": 1,
            "jobId": query_hash,
            "magicCode": magic_code,
            "query": normalized_query,
            "queryHash": query_hash,
        }
        try:
            await request.app.state.search_publisher.publish(message)
        except SearchPublishError as exc:
            logger.error(
                "Search command publish failed: {} ({})",
                exc.reason_code,
                type(exc).__name__,
            )
            try:
                await request.app.state.search_store.mark_publish_failed(magic_code)
            except REDIS_ERRORS as cleanup_exc:
                logger.error(
                    "Search publish failure state update failed ({})",
                    type(cleanup_exc).__name__,
                )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="검색 작업을 일시적으로 접수할 수 없습니다.",
            ) from exc

    return SearchAcceptedResponse(magicCode=magic_code)


@router.get(
    "/{magic_code}",
    response_model=SearchResultResponse,
    response_model_by_alias=True,
    summary="검색 상태 및 결과 조회",
    description=(
        "로그인한 사용자가 magicCode로 Redis 상태를 확인합니다. 처리 중이면 202, 완료된 경우 Redis의 "
        "최종 답변(`result.answer`)과 검색 근거를 200으로 반환하고 사용한 magicCode를 삭제합니다. "
        "이 API는 외부 검색 호출이나 RabbitMQ 발행을 수행하지 않습니다."
    ),
    responses={
        200: {"description": "Redis에 저장된 최종 답변과 검색 근거가 반환됨"},
        202: {
            "description": "검색 작업이 아직 처리 중임",
            "model": SearchPendingResponse,
        },
        401: {"description": "로그인 세션이 없거나 유효하지 않음"},
        404: {"description": "magicCode가 없거나 만료됨"},
        422: {"description": "magicCode 형식이 유효하지 않음"},
        502: {"description": "검색 작업이 실패했거나 결과 형식이 계약과 맞지 않음"},
        503: {"description": "인증 DB 또는 Redis를 일시적으로 사용할 수 없음"},
    },
)
async def get_search_result(
    request: Request,
    magic_code: MagicCode,
    _user_id: Annotated[int, Depends(get_current_user_id)],
):
    try:
        search_state = await request.app.state.search_store.read(magic_code)
    except REDIS_ERRORS as exc:
        raise storage_unavailable("read", exc) from exc
    except InvalidSearchData as exc:
        logger.error("Search data contract violation ({})", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="검색 결과를 처리할 수 없습니다.",
        ) from exc

    if search_state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검색 작업을 찾을 수 없거나 magicCode가 만료되었습니다.",
        )
    if search_state.status == "PENDING":
        content = SearchPendingResponse(magicCode=magic_code).model_dump(by_alias=True)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=content)
    if search_state.status == "FAILED":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="검색 작업을 완료하지 못했습니다.",
        )
    if search_state.status != "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="검색 상태를 처리할 수 없습니다.",
        )
    if search_state.result is None:
        logger.error("Search data contract violation (MissingCompletedResult)")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="검색 결과를 처리할 수 없습니다.",
        )
    response = SearchResultResponse(magicCode=magic_code, result=search_state.result)
    try:
        deleted = await request.app.state.search_store.delete_ticket(magic_code)
    except REDIS_ERRORS as exc:
        raise storage_unavailable("delete-ticket", exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검색 작업을 찾을 수 없거나 magicCode가 만료되었습니다.",
        )
    return response
