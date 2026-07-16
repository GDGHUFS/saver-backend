from datetime import datetime
from typing import Annotated, Literal

import asyncpg
from fastapi import HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field

from src.weather.grid import NX, NY


DATABASE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError, OSError)

Region = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=100,
        description=(
            "공백으로 구분한 모든 토큰이 행정구역 전체 이름에 포함되는 지역명. "
            "region을 사용하면 latitude와 longitude는 생략해야 합니다."
        ),
        examples=["서울특별시 종로구"],
    ),
]
Latitude = Annotated[
    float | None,
    Query(
        ge=30,
        le=45,
        description=(
            "한국 영역의 WGS84 위도. 좌표 조회에서는 longitude와 함께 지정해야 하며 "
            "기상청 5km 격자로 변환합니다."
        ),
        examples=[37.5704],
    ),
]
Longitude = Annotated[
    float | None,
    Query(
        ge=120,
        le=140,
        description=(
            "한국 영역의 WGS84 경도. 좌표 조회에서는 latitude와 함께 지정해야 하며 "
            "기상청 5km 격자로 변환합니다."
        ),
        examples=[126.9816],
    ),
]
ForecastHours = Annotated[
    int,
    Query(
        ge=1,
        le=72,
        description="격자별로 반환할 현재 시간대 이후 예보의 최대 개수(1~72)",
        examples=[24],
    ),
]


class WeatherGridResponse(BaseModel):
    nx: int = Field(ge=1, le=NX, description="기상청 격자 X")
    ny: int = Field(ge=1, le=NY, description="기상청 격자 Y")
    longitude: float = Field(ge=120, le=140, description="격자 중심 WGS84 경도")
    latitude: float = Field(ge=30, le=45, description="격자 중심 WGS84 위도")


class WeatherLocationResponse(BaseModel):
    administrative_code: str = Field(
        pattern=r"^[0-9]{10}$",
        description="기상청 위치 기준정보의 10자리 행정구역 코드",
    )
    region_level_1: str = Field(min_length=1, description="시·도 단위 1단계 행정구역명")
    region_level_2: str | None = Field(description="시·군·구 단위 2단계 행정구역명")
    region_level_3: str | None = Field(description="읍·면·동 단위 3단계 행정구역명")


class WeatherValuesResponse(BaseModel):
    precipitation_probability: str | None = Field(description="강수확률 POP 원문 값(%)")
    precipitation_type: str | None = Field(description="강수형태 PTY 원 코드")
    precipitation_type_label: str | None = Field(
        description="강수형태 코드 이름(없음, 비, 비/눈, 눈 또는 소나기)"
    )
    precipitation_amount: str | None = Field(
        description="1시간 강수량 PCP 원문 값. 강수없음, 범위 또는 정성 코드일 수 있습니다."
    )
    humidity: str | None = Field(description="습도 REH 원문 값(%)")
    snowfall_amount: str | None = Field(
        description="1시간 신적설 SNO 원문 값. 적설없음, 범위 또는 정성 코드일 수 있습니다."
    )
    sky_status: str | None = Field(description="하늘상태 SKY 원 코드")
    sky_status_label: str | None = Field(description="하늘상태 코드 이름(맑음, 구름많음 또는 흐림)")
    temperature: str | None = Field(description="1시간 기온 TMP 원문 값(섭씨)")
    minimum_temperature: str | None = Field(description="일 최저기온 TMN 원문 값(섭씨)")
    maximum_temperature: str | None = Field(description="일 최고기온 TMX 원문 값(섭씨)")
    wind_u_component: str | None = Field(description="풍속 동서성분 UUU 원문 값(m/s)")
    wind_v_component: str | None = Field(description="풍속 남북성분 VVV 원문 값(m/s)")
    wave_height: str | None = Field(description="파고 WAV 원문 값(m)")
    wind_direction: str | None = Field(description="풍향 VEC 원문 값(도)")
    wind_speed: str | None = Field(description="풍속 WSD 원문 값(m/s 또는 정성 코드)")


class WeatherForecastItemResponse(WeatherValuesResponse):
    forecast_at: datetime = Field(description="이 값이 예보하는 시각")


class NationwideCurrentWeatherItemResponse(WeatherForecastItemResponse):
    grid: WeatherGridResponse = Field(description="예보 대상 기상청 격자와 중심 좌표")
    issued_at: datetime = Field(description="사용한 기상청 단기예보 발표시각")


class NationwideCurrentWeatherResponse(BaseModel):
    generated_at: datetime = Field(description="backend가 응답을 생성한 시각")
    items: list[NationwideCurrentWeatherItemResponse] = Field(
        description="예보가 저장된 전국 고유 격자의 현재 시각 최근접 예보"
    )


class WeatherGridForecastResponse(BaseModel):
    grid: WeatherGridResponse = Field(description="예보 대상 기상청 격자와 중심 좌표")
    locations: list[WeatherLocationResponse] = Field(
        description="지역명 조건에 일치하거나 좌표 격자를 공유하는 행정구역"
    )
    issued_at: datetime = Field(description="사용한 최신 기상청 단기예보 발표시각")
    forecasts: list[WeatherForecastItemResponse] = Field(
        min_length=1,
        description="현재 시간대부터 예보시각 오름차순으로 정렬한 단기예보",
    )


class WeatherForecastResponse(BaseModel):
    selector: Literal["region", "coordinates"] = Field(description="요청에 사용한 위치 선택 방식")
    region: str | None = Field(description="정규화한 지역명 검색어. 좌표 조회이면 null입니다.")
    latitude: float | None = Field(description="요청한 위도. 지역명 조회이면 null입니다.")
    longitude: float | None = Field(description="요청한 경도. 지역명 조회이면 null입니다.")
    hours: int = Field(ge=1, le=72, description="요청한 격자별 최대 예보 시간대 수")
    items: list[WeatherGridForecastResponse] = Field(
        min_length=1,
        description="조건에 일치하고 최신 예보가 저장된 고유 격자 목록",
    )


def storage_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("Weather storage operation failed: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="날씨 저장소를 일시적으로 사용할 수 없습니다.",
    )


def invalid_stored_data(operation: str, exc: BaseException) -> HTTPException:
    logger.error("Weather data contract violation: {} ({})", operation, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="날씨 정보를 일시적으로 제공할 수 없습니다.",
    )
