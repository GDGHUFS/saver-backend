import asyncio
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger
from pydantic import ValidationError
from redis.exceptions import RedisError

from src.weather.cache import InvalidWeatherCacheData, RedisWeatherCache
from src.weather.grid import latitude_longitude_to_grid
from src.weather.model import (
    DATABASE_ERRORS,
    ForecastHours,
    Latitude,
    Longitude,
    NationwideCurrentWeatherItemResponse,
    NationwideCurrentWeatherResponse,
    Region,
    RegionLevel1,
    RegionLevel2,
    WeatherForecastItemResponse,
    WeatherForecastResponse,
    WeatherGridForecastResponse,
    WeatherGridResponse,
    WeatherLocationCatalogResponse,
    WeatherLocationResponse,
    WeatherRegionOptionResponse,
    invalid_stored_data,
    storage_unavailable,
)


router = APIRouter(prefix="/weather", tags=["날씨"])

REGION_ALIASES = {
    "강원도": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전라북도": "전북특별자치도",
    "전남": "전남광주통합특별시",
    "전라남도": "전남광주통합특별시",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주도": "제주특별자치도",
    "세종시": "세종특별자치시",
}

_FORECAST_COLUMNS = """
                       forecast.precipitation_probability,
                       forecast.precipitation_type,
                       forecast.precipitation_amount,
                       forecast.humidity,
                       forecast.snowfall_amount,
                       forecast.sky_status,
                       forecast.temperature,
                       forecast.minimum_temperature,
                       forecast.maximum_temperature,
                       forecast.wind_u_component,
                       forecast.wind_v_component,
                       forecast.wave_height,
                       forecast.wind_direction,
                       forecast.wind_speed
"""
_SKY_STATUS_LABELS = {"1": "맑음", "3": "구름많음", "4": "흐림"}
_PRECIPITATION_TYPE_LABELS = {
    "0": "없음",
    "1": "비",
    "2": "비/눈",
    "3": "눈",
    "4": "소나기",
}
_WEATHER_CACHE_CONNECTION_ERRORS = (RedisError, TimeoutError, OSError)


def _forecast_item(row: object) -> WeatherForecastItemResponse:
    data = dict(row)
    data["sky_status_label"] = _SKY_STATUS_LABELS.get(data["sky_status"])
    data["precipitation_type_label"] = _PRECIPITATION_TYPE_LABELS.get(
        data["precipitation_type"]
    )
    return WeatherForecastItemResponse.model_validate(data)


def _grid(row: object) -> WeatherGridResponse:
    return WeatherGridResponse.model_validate(
        {
            "nx": row["nx"],
            "ny": row["ny"],
            "longitude": row["longitude"],
            "latitude": row["latitude"],
        }
    )


def _normalize_region(region: str) -> tuple[str, list[str]]:
    tokens = [REGION_ALIASES.get(token, token) for token in region.split() if token]
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="지역명은 공백일 수 없습니다.",
        )
    normalized = " ".join(tokens)
    patterns = [f"%{_escape_like(token)}%" for token in tokens]
    return normalized, patterns


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_region_level(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{name}은 공백일 수 없습니다.",
        )
    return normalized


def _selector(
    region: str | None,
    latitude: float | None,
    longitude: float | None,
) -> tuple[str, str | None, list[str] | None, int | None, int | None]:
    has_region = region is not None
    has_any_coordinate = latitude is not None or longitude is not None
    has_both_coordinates = latitude is not None and longitude is not None
    if has_region == has_any_coordinate or (has_any_coordinate and not has_both_coordinates):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "region 하나 또는 latitude와 longitude 쌍 중 정확히 한 방식으로 "
                "위치를 지정해야 합니다."
            ),
        )
    if region is not None:
        normalized_region, patterns = _normalize_region(region)
        return "region", normalized_region, patterns, None, None

    assert latitude is not None and longitude is not None
    try:
        nx, ny = latitude_longitude_to_grid(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="좌표를 기상청 단기예보 격자로 변환할 수 없습니다.",
        ) from exc
    return "coordinates", None, None, nx, ny


def _current_weather_refresh_lock(request: Request) -> asyncio.Lock:
    state = request.app.state
    lock = getattr(state, "weather_current_refresh_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        state.weather_current_refresh_lock = lock
    return lock


async def _read_cached_current_weather(
    cache: RedisWeatherCache | None,
) -> tuple[NationwideCurrentWeatherResponse | None, bool]:
    if cache is None:
        return None, False
    try:
        return await cache.read_current(), True
    except InvalidWeatherCacheData as exc:
        logger.warning(
            "Weather cache operation failed: read-current ({})",
            type(exc).__name__,
        )
        return None, True
    except _WEATHER_CACHE_CONNECTION_ERRORS as exc:
        logger.warning(
            "Weather cache operation failed: read-current ({})",
            type(exc).__name__,
        )
        return None, False


async def _write_cached_current_weather(
    cache: RedisWeatherCache | None,
    response: NationwideCurrentWeatherResponse,
) -> None:
    if cache is None:
        return
    try:
        await cache.write_current(response)
    except (RedisError, InvalidWeatherCacheData, TimeoutError, OSError) as exc:
        logger.warning(
            "Weather cache operation failed: write-current ({})",
            type(exc).__name__,
        )


async def _read_nationwide_current_weather(
    request: Request,
) -> NationwideCurrentWeatherResponse:
    try:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                WITH latest_issues AS (
                    SELECT DISTINCT ON (issue.nx, issue.ny)
                           issue.id,
                           issue.nx,
                           issue.ny,
                           issue.issued_at
                    FROM weather_forecast_issues AS issue
                    ORDER BY issue.nx, issue.ny, issue.issued_at DESC, issue.id DESC
                )
                SELECT issue.nx,
                       issue.ny,
                       grid.longitude,
                       grid.latitude,
                       issue.issued_at,
                       forecast.forecast_at,
                """
                + _FORECAST_COLUMNS
                + """
                FROM latest_issues AS issue
                JOIN weather_grid_points AS grid
                  ON grid.nx = issue.nx AND grid.ny = issue.ny
                JOIN LATERAL (
                    SELECT value.*
                    FROM weather_forecasts AS value
                    WHERE value.forecast_issue_id = issue.id
                    ORDER BY ABS(
                                 EXTRACT(EPOCH FROM (value.forecast_at - CURRENT_TIMESTAMP))
                             ) ASC,
                             value.forecast_at ASC
                    LIMIT 1
                ) AS forecast ON TRUE
                ORDER BY issue.nx ASC, issue.ny ASC
                """
            )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("read-nationwide-current", exc) from exc

    try:
        items = [
            NationwideCurrentWeatherItemResponse(
                grid=_grid(row),
                issued_at=row["issued_at"],
                **_forecast_item(row).model_dump(),
            )
            for row in rows
        ]
        return NationwideCurrentWeatherResponse(
            generated_at=datetime.now(UTC),
            items=items,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise invalid_stored_data("read-nationwide-current", exc) from exc


@router.get(
    "/current",
    response_model=NationwideCurrentWeatherResponse,
    summary="전국 현재 날씨 현황 조회",
    description=(
        "별도 날씨 수집기가 PostgreSQL에 저장한 전국 고유 격자의 최신 단기예보 발표본을 "
        "조회하고, 각 격자에서 현재 시각과 가장 가까운 예보 한 건을 반환합니다. 응답은 실황 "
        "관측값이 아니라 단기예보이므로 `issued_at`과 `forecast_at`을 함께 확인해야 합니다. "
        "현재 시각과 같은 거리에 두 예보가 있으면 이전 시각을 우선합니다. 저장된 예보가 없는 "
        "격자는 생략합니다. PostgreSQL 조회 결과는 Redis에 짧게 캐시하고 같은 backend 프로세스의 "
        "동시 cache miss는 한 번의 DB 조회로 합칩니다. Redis 캐시를 사용할 수 없거나 캐시 데이터가 "
        "유효하지 않으면 DB 조회로 안전하게 대체합니다. 외부 기상 API를 호출하거나 수집을 시작하지 "
        "않으며 로그인 없이 호출할 수 있습니다."
    ),
    responses={
        200: {"description": "예보가 저장된 전국 격자의 현재 시각 최근접 단기예보를 반환함"},
        503: {"description": "날씨 저장소를 사용할 수 없거나 저장 데이터가 유효하지 않음"},
    },
)
async def get_nationwide_current_weather(request: Request) -> NationwideCurrentWeatherResponse:
    cache = getattr(request.app.state, "weather_cache", None)
    cached, cache_available = await _read_cached_current_weather(cache)
    if cached is not None:
        return cached

    async with _current_weather_refresh_lock(request):
        if cache_available:
            cached, cache_available = await _read_cached_current_weather(cache)
            if cached is not None:
                return cached
        response = await _read_nationwide_current_weather(request)
        if cache_available:
            await _write_cached_current_weather(cache, response)
        return response


@router.get(
    "/locations",
    response_model=WeatherLocationCatalogResponse,
    summary="날씨 지역 목록 탐색",
    description=(
        "날씨 수집기가 공식 격자·위경도 XLSX에서 PostgreSQL `weather_locations`로 동기화한 "
        "행정구역 이름을 계층적으로 반환합니다. 파라미터가 없으면 1단계 시·도 목록을, "
        "`region_level_1`만 지정하면 해당 지역의 2단계 시·군·구 목록을, "
        "`region_level_1`과 `region_level_2`를 함께 지정하면 3단계 읍·면·동 목록을 반환합니다. "
        "응답의 `full_name`은 `/weather/forecast`의 `region` 파라미터에 그대로 사용할 수 있습니다. "
        "하위 지역이 없거나 일치하는 상위 지역이 없으면 빈 목록을 반환하며, 외부 파일이나 기상 "
        "API를 요청 시점에 호출하지 않습니다. 로그인 없이 호출할 수 있습니다."
    ),
    responses={
        200: {"description": "요청한 단계의 중복 없는 지역 선택지를 이름순으로 반환함"},
        422: {"description": "상위 지역 파라미터 조합이나 지역명이 유효하지 않음"},
        503: {"description": "날씨 저장소를 사용할 수 없거나 저장 데이터가 유효하지 않음"},
    },
)
async def get_weather_locations(
    request: Request,
    region_level_1: RegionLevel1 = None,
    region_level_2: RegionLevel2 = None,
) -> WeatherLocationCatalogResponse:
    normalized_level_1 = _normalize_region_level(
        region_level_1,
        name="region_level_1",
    )
    normalized_level_2 = _normalize_region_level(
        region_level_2,
        name="region_level_2",
    )
    if normalized_level_2 is not None and normalized_level_1 is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="region_level_2는 region_level_1과 함께 지정해야 합니다.",
        )
    if normalized_level_1 is not None:
        normalized_level_1 = REGION_ALIASES.get(
            normalized_level_1,
            normalized_level_1,
        )

    try:
        async with request.app.state.pool.acquire() as connection:
            if normalized_level_1 is None:
                region_level = 1
                parents = []
                rows = await connection.fetch(
                    """
                    SELECT location.region_level_1 AS name,
                           location.region_level_1 AS full_name,
                           BOOL_OR(location.region_level_2 IS NOT NULL) AS has_children
                    FROM weather_locations AS location
                    GROUP BY location.region_level_1
                    ORDER BY location.region_level_1 ASC
                    """
                )
            elif normalized_level_2 is None:
                region_level = 2
                parents = [normalized_level_1]
                rows = await connection.fetch(
                    """
                    SELECT location.region_level_2 AS name,
                           concat_ws(
                               ' ',
                               location.region_level_1,
                               location.region_level_2
                           ) AS full_name,
                           BOOL_OR(location.region_level_3 IS NOT NULL) AS has_children
                    FROM weather_locations AS location
                    WHERE location.region_level_1 = $1
                      AND location.region_level_2 IS NOT NULL
                    GROUP BY location.region_level_1, location.region_level_2
                    ORDER BY location.region_level_2 ASC
                    """,
                    normalized_level_1,
                )
            else:
                region_level = 3
                parents = [normalized_level_1, normalized_level_2]
                rows = await connection.fetch(
                    """
                    SELECT location.region_level_3 AS name,
                           concat_ws(
                               ' ',
                               location.region_level_1,
                               location.region_level_2,
                               location.region_level_3
                           ) AS full_name,
                           FALSE AS has_children
                    FROM weather_locations AS location
                    WHERE location.region_level_1 = $1
                      AND location.region_level_2 = $2
                      AND location.region_level_3 IS NOT NULL
                    GROUP BY
                        location.region_level_1,
                        location.region_level_2,
                        location.region_level_3
                    ORDER BY location.region_level_3 ASC
                    """,
                    normalized_level_1,
                    normalized_level_2,
                )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("read-locations", exc) from exc

    try:
        return WeatherLocationCatalogResponse(
            region_level=region_level,
            parents=parents,
            items=[WeatherRegionOptionResponse.model_validate(dict(row)) for row in rows],
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise invalid_stored_data("read-locations", exc) from exc


@router.get(
    "/forecast",
    response_model=WeatherForecastResponse,
    summary="지역 단기 날씨예보 조회",
    description=(
        "지역명 `region` 또는 WGS84 `latitude`와 `longitude` 쌍 중 정확히 한 방식으로 위치를 "
        "지정합니다. 지역명은 공백 단위 토큰이 행정구역 1·2·3단계 전체 이름에 모두 포함되는 "
        "격자를 찾으며 수집기와 같은 도 단위 약칭을 지원합니다. 넓거나 중복되는 이름이 여러 "
        "격자에 일치하면 임의의 한 격자를 선택하지 않고 모두 반환합니다. 좌표는 수집기와 같은 "
        "기상청 Lambert 변환식으로 5km 격자에 대응시킵니다. 각 격자의 최신 발표본에서 현재 "
        "시간대 이후 예보를 반환하며, 저장된 위치나 예보가 없으면 새 수집을 시작하지 않고 404를 "
        "반환합니다. 로그인 없이 호출할 수 있습니다."
    ),
    responses={
        200: {"description": "조건에 맞는 고유 격자별 최신 단기예보를 반환함"},
        404: {"description": "지역, 좌표 격자 또는 현재 제공 가능한 저장 예보가 없음"},
        422: {"description": "위치 선택 방식, 지역명, 좌표 또는 hours가 유효하지 않음"},
        503: {"description": "날씨 저장소를 사용할 수 없거나 저장 데이터가 유효하지 않음"},
    },
)
async def get_weather_forecast(
    request: Request,
    region: Region = None,
    latitude: Latitude = None,
    longitude: Longitude = None,
    hours: ForecastHours = 24,
) -> WeatherForecastResponse:
    selector, normalized_region, patterns, nx, ny = _selector(region, latitude, longitude)

    try:
        async with request.app.state.pool.acquire() as connection:
            if selector == "region":
                location_rows = await connection.fetch(
                    """
                    SELECT location.administrative_code,
                           location.region_level_1,
                           location.region_level_2,
                           location.region_level_3,
                           grid.nx,
                           grid.ny,
                           grid.longitude,
                           grid.latitude
                    FROM weather_locations AS location
                    JOIN weather_grid_points AS grid
                      ON grid.nx = location.nx AND grid.ny = location.ny
                    WHERE concat_ws(
                        ' ',
                        location.region_level_1,
                        location.region_level_2,
                        location.region_level_3
                    ) ILIKE ALL($1::TEXT[])
                    ORDER BY grid.nx, grid.ny, location.administrative_code
                    """,
                    patterns,
                )
            else:
                location_rows = await connection.fetch(
                    """
                    SELECT location.administrative_code,
                           location.region_level_1,
                           location.region_level_2,
                           location.region_level_3,
                           grid.nx,
                           grid.ny,
                           grid.longitude,
                           grid.latitude
                    FROM weather_grid_points AS grid
                    LEFT JOIN weather_locations AS location
                      ON location.nx = grid.nx AND location.ny = grid.ny
                    WHERE grid.nx = $1 AND grid.ny = $2
                    ORDER BY location.administrative_code
                    """,
                    nx,
                    ny,
                )

            grid_keys = list(
                dict.fromkeys((row["nx"], row["ny"]) for row in location_rows)
            )
            forecast_rows = []
            if grid_keys:
                forecast_rows = await connection.fetch(
                    """
                    WITH target_grids AS (
                        SELECT *
                        FROM unnest($1::SMALLINT[], $2::SMALLINT[]) AS target(nx, ny)
                    ),
                    latest_issues AS (
                        SELECT DISTINCT ON (issue.nx, issue.ny)
                               issue.id,
                               issue.nx,
                               issue.ny,
                               issue.issued_at
                        FROM target_grids AS target
                        JOIN weather_forecast_issues AS issue
                          ON issue.nx = target.nx AND issue.ny = target.ny
                        ORDER BY issue.nx, issue.ny, issue.issued_at DESC, issue.id DESC
                    ),
                    ranked_forecasts AS (
                        SELECT issue.nx,
                               issue.ny,
                               issue.issued_at,
                               forecast.forecast_at,
                    """
                    + _FORECAST_COLUMNS
                    + """,
                               ROW_NUMBER() OVER (
                                   PARTITION BY issue.nx, issue.ny
                                   ORDER BY forecast.forecast_at ASC
                               ) AS forecast_rank
                        FROM latest_issues AS issue
                        JOIN weather_forecasts AS forecast
                          ON forecast.forecast_issue_id = issue.id
                        WHERE forecast.forecast_at >= date_trunc('hour', CURRENT_TIMESTAMP)
                    )
                    SELECT *
                    FROM ranked_forecasts
                    WHERE forecast_rank <= $3
                    ORDER BY nx, ny, forecast_at
                    """,
                    [key[0] for key in grid_keys],
                    [key[1] for key in grid_keys],
                    hours,
                )
    except DATABASE_ERRORS as exc:
        raise storage_unavailable("read-forecast", exc) from exc

    if not location_rows:
        detail = (
            "요청한 지역을 찾을 수 없습니다."
            if selector == "region"
            else "요청한 좌표에 대응하는 저장 격자를 찾을 수 없습니다."
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if not forecast_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="요청한 위치에 현재 제공할 수 있는 저장 예보가 없습니다.",
        )

    try:
        locations_by_grid: dict[tuple[int, int], list[WeatherLocationResponse]] = defaultdict(list)
        grids_by_key: dict[tuple[int, int], WeatherGridResponse] = {}
        for row in location_rows:
            key = (row["nx"], row["ny"])
            grids_by_key[key] = _grid(row)
            if row["administrative_code"] is not None:
                locations_by_grid[key].append(
                    WeatherLocationResponse.model_validate(dict(row))
                )

        forecast_rows_by_grid: dict[tuple[int, int], list[object]] = defaultdict(list)
        for row in forecast_rows:
            forecast_rows_by_grid[(row["nx"], row["ny"])].append(row)

        items = []
        for key in grid_keys:
            rows = forecast_rows_by_grid[key]
            if not rows:
                continue
            items.append(
                WeatherGridForecastResponse(
                    grid=grids_by_key[key],
                    locations=locations_by_grid[key],
                    issued_at=rows[0]["issued_at"],
                    forecasts=[_forecast_item(row) for row in rows],
                )
            )

        return WeatherForecastResponse(
            selector=selector,
            region=normalized_region,
            latitude=latitude,
            longitude=longitude,
            hours=hours,
            items=items,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise invalid_stored_data("read-forecast", exc) from exc
