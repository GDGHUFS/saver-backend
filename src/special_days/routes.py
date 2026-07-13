from datetime import date

from fastapi import APIRouter, Request

from src.special_days.model import (
    DATABASE_ERRORS,
    SPECIAL_DAY_KIND_BY_CODE,
    SpecialDayResponse,
    YearMonth,
    invalid_stored_data,
    storage_unavailable,
)


router = APIRouter(prefix="/special-days", tags=["특일"])


def _special_day_response(row: object) -> SpecialDayResponse:
    data = dict(row)
    data["date_kind"] = SPECIAL_DAY_KIND_BY_CODE[data["date_kind"]]
    return SpecialDayResponse.model_validate(data)


@router.get(
    "/{year_month}",
    response_model=list[SpecialDayResponse],
    summary="연월별 특일 목록 조회",
    description=(
        "YYYY-MM 형식으로 지정한 연월에 해당하는 특일을 날짜순으로 반환합니다. "
        "별도 특일 수집 API가 PostgreSQL의 `anniversary_special_days` 테이블에 저장한 정보만 "
        "조회하며, `date_kind`는 원 코드 대신 국경일, 기념일, 24절기 또는 잡절로 해석해 "
        "반환합니다. 저장된 항목이 없으면 빈 목록을 반환하고 로그인이 필요하지 않습니다."
    ),
    responses={
        200: {"description": "지정한 연월의 특일을 날짜 및 DB ID 오름차순으로 반환함"},
        422: {"description": "연월이 YYYY-MM 형식이 아니거나 유효한 월이 아님"},
        503: {"description": "특일 저장소를 일시적으로 사용할 수 없거나 저장 데이터가 유효하지 않음"},
    },
)
async def get_special_days(
    request: Request,
    year_month: YearMonth,
) -> list[SpecialDayResponse]:
    month_start = date.fromisoformat(f"{year_month}-01")

    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id,
                       observed_date,
                       date_kind,
                       date_name,
                       is_holiday
                FROM anniversary_special_days
                WHERE observed_date >= $1::DATE
                  AND observed_date < $1::DATE + INTERVAL '1 month'
                ORDER BY observed_date ASC, id ASC
                """,
                month_start,
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("read-month", exc) from exc

    try:
        return [_special_day_response(row) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise invalid_stored_data("read-month", exc) from exc
