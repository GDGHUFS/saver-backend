from datetime import date
from enum import Enum
from typing import Annotated

import asyncpg
from fastapi import HTTPException, Path, status
from loguru import logger
from pydantic import BaseModel, Field


DATABASE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError, OSError)

YearMonth = Annotated[
    str,
    Path(
        min_length=7,
        max_length=7,
        pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$",
        description="조회할 연월. 4자리 연도와 2자리 월을 YYYY-MM 형식으로 입력합니다.",
        examples=["2026-06"],
    ),
]


class SpecialDayKind(str, Enum):
    NATIONAL_HOLIDAY = "국경일"
    COMMEMORATION = "기념일"
    SOLAR_TERM = "24절기"
    MISCELLANEOUS = "잡절"


SPECIAL_DAY_KIND_BY_CODE = {
    "01": SpecialDayKind.NATIONAL_HOLIDAY,
    "02": SpecialDayKind.COMMEMORATION,
    "03": SpecialDayKind.SOLAR_TERM,
    "04": SpecialDayKind.MISCELLANEOUS,
}


class SpecialDayResponse(BaseModel):
    id: int = Field(description="특일 항목의 DB ID", examples=[1])
    observed_date: date = Field(description="특일 날짜", examples=["2026-06-06"])
    date_kind: SpecialDayKind = Field(
        description="date_kind 코드를 해석한 특일 분류 이름",
        examples=["국경일"],
    )
    date_name: str = Field(min_length=1, description="특일 이름", examples=["현충일"])
    is_holiday: bool = Field(description="공휴일 여부", examples=[True])


def storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("Special day storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="특일 저장소를 일시적으로 사용할 수 없습니다.",
    )


def invalid_stored_data(operation: str, exc: BaseException) -> HTTPException:
    logger.error("Special day data contract violation: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="특일 정보를 일시적으로 제공할 수 없습니다.",
    )
