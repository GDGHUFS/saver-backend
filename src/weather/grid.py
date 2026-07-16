import math


NX = 149
NY = 253

_EARTH_RADIUS_KM = 6371.00877
_GRID_KM = 5.0
_STANDARD_LATITUDE_1 = 30.0
_STANDARD_LATITUDE_2 = 60.0
_ORIGIN_LONGITUDE = 126.0
_ORIGIN_LATITUDE = 38.0
_ORIGIN_X = 210.0 / _GRID_KM
_ORIGIN_Y = 675.0 / _GRID_KM

_DEGREE_TO_RADIAN = math.pi / 180.0
_RE = _EARTH_RADIUS_KM / _GRID_KM
_SLAT1 = _STANDARD_LATITUDE_1 * _DEGREE_TO_RADIAN
_SLAT2 = _STANDARD_LATITUDE_2 * _DEGREE_TO_RADIAN
_OLON = _ORIGIN_LONGITUDE * _DEGREE_TO_RADIAN
_OLAT = _ORIGIN_LATITUDE * _DEGREE_TO_RADIAN
_SN = math.log(math.cos(_SLAT1) / math.cos(_SLAT2)) / math.log(
    math.tan(math.pi * 0.25 + _SLAT2 * 0.5)
    / math.tan(math.pi * 0.25 + _SLAT1 * 0.5)
)
_SF = (
    math.tan(math.pi * 0.25 + _SLAT1 * 0.5) ** _SN
    * math.cos(_SLAT1)
    / _SN
)
_RO = _RE * _SF / math.tan(math.pi * 0.25 + _OLAT * 0.5) ** _SN


def latitude_longitude_to_grid(latitude: float, longitude: float) -> tuple[int, int]:
    """Convert coordinates with the same KMA Lambert formula as the collector."""

    if not -90.0 <= latitude <= 90.0:
        raise ValueError("위도는 -90 이상 90 이하여야 합니다.")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("경도는 -180 이상 180 이하여야 합니다.")

    ra = math.tan(math.pi * 0.25 + latitude * _DEGREE_TO_RADIAN * 0.5)
    if ra <= 0:
        raise ValueError("격자로 변환할 수 없는 위도입니다.")
    ra = _RE * _SF / ra**_SN

    theta = longitude * _DEGREE_TO_RADIAN - _OLON
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= _SN

    nx = int(ra * math.sin(theta) + _ORIGIN_X + 1.5)
    ny = int(_RO - ra * math.cos(theta) + _ORIGIN_Y + 1.5)
    if not 1 <= nx <= NX or not 1 <= ny <= NY:
        raise ValueError("좌표가 기상청 단기예보 격자 범위를 벗어났습니다.")
    return nx, ny
