from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from src.weather.grid import latitude_longitude_to_grid
from src.weather.model import (
    DATABASE_ERRORS,
    ForecastHours,
    Latitude,
    Longitude,
    NationwideCurrentWeatherItemResponse,
    NationwideCurrentWeatherResponse,
    Region,
    WeatherForecastItemResponse,
    WeatherForecastResponse,
    WeatherGridForecastResponse,
    WeatherGridResponse,
    WeatherLocationResponse,
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
    "전남": "전라남도",
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


@router.get(
    "/current",
    response_model=NationwideCurrentWeatherResponse,
    summary="전국 현재 날씨 현황 조회",
    description=(
        "별도 날씨 수집기가 PostgreSQL에 저장한 전국 고유 격자의 최신 단기예보 발표본을 "
        "조회하고, 각 격자에서 현재 시각과 가장 가까운 예보 한 건을 반환합니다. 응답은 실황 "
        "관측값이 아니라 단기예보이므로 `issued_at`과 `forecast_at`을 함께 확인해야 합니다. "
        "현재 시각과 같은 거리에 두 예보가 있으면 이전 시각을 우선합니다. 저장된 예보가 없는 "
        "격자는 생략하며 외부 기상 API를 호출하거나 수집을 시작하지 않습니다. 로그인 없이 "
        "호출할 수 있습니다."
    ),
    responses={
        200: {"description": "예보가 저장된 전국 격자의 현재 시각 최근접 단기예보를 반환함"},
        503: {"description": "날씨 저장소를 사용할 수 없거나 저장 데이터가 유효하지 않음"},
    },
)
async def get_nationwide_current_weather(request: Request) -> NationwideCurrentWeatherResponse:
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
