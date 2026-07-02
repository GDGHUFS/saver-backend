import hmac
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, Field

from src.auth.session import (
    SESSION_COOKIE_NAME,
    InvalidSession,
    create_session_cookie,
    read_session_cookie,
)

auth_router = APIRouter(tags=["인증"])


KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_PROFILE_URL = "https://kapi.kakao.com/v2/user/me"
KAKAO_UNLINK_URL = "https://kapi.kakao.com/v1/user/unlink"
OAUTH_STATE_COOKIE = "kakao_oauth_state"
WITHDRAW_STATE_COOKIE = "kakao_withdraw_state"
session_cookie = APIKeyCookie(
    name=SESSION_COOKIE_NAME,
    auto_error=False,
    description="카카오 로그인 완료 후 backend가 발급하는 서명된 Saver 세션 쿠키",
)


class UserResponse(BaseModel):
    id: int = Field(description="카카오에서 발급한 고유 사용자 ID", examples=[123456789])
    nickname: str = Field(description="사용자 표시 이름", examples=["Saver 사용자"])
    profile_image: str = Field(
        description="카카오 프로필 이미지 또는 사용자가 제공을 거부했을 때의 기본 이미지 URL",
        examples=["https://api.example.com/assets/default-profile.svg"],
    )


def _redirect_uri(request: Request) -> str:
    return f"{request.app.state.host}/redirect"


async def _request_kakao_profile(
    client: httpx.AsyncClient,
    *,
    code: str,
    client_key: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[dict[str, Any], str]:
    try:
        token_response = await client.post(
            KAKAO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_key,
                "redirect_uri": redirect_uri,
                "client_secret": client_secret,
                "code": code,
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Kakao token response has no access token")

        profile_response = await client.get(
            KAKAO_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="카카오 사용자 정보를 가져오지 못했습니다.",
        ) from exc

    if not isinstance(profile, dict):
        raise HTTPException(status_code=502, detail="카카오 사용자 정보 형식이 올바르지 않습니다.")
    return profile, access_token


async def _unlink_kakao_user(client: httpx.AsyncClient, access_token: str) -> int:
    try:
        response = await client.post(
            KAKAO_UNLINK_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        unlinked_user_id = int(response.json()["id"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="카카오 계정 연결을 해제하지 못했습니다.",
        ) from exc
    return unlinked_user_id


def _user_values(profile: dict[str, Any], default_profile_image: str) -> tuple[int, str, str]:
    try:
        user_id = int(profile["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="카카오 사용자 ID가 없습니다.") from exc

    account = profile.get("kakao_account")
    account = account if isinstance(account, dict) else {}
    kakao_profile = account.get("profile")
    kakao_profile = kakao_profile if isinstance(kakao_profile, dict) else {}
    properties = profile.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    nickname = kakao_profile.get("nickname") or properties.get("nickname")
    if not isinstance(nickname, str) or not nickname.strip():
        nickname = f"사용자-{user_id}"

    profile_image = (
        kakao_profile.get("profile_image_url")
        or properties.get("profile_image")
        or default_profile_image
    )
    if not isinstance(profile_image, str) or not profile_image.strip():
        profile_image = default_profile_image

    return user_id, nickname.strip(), profile_image.strip()


async def get_current_user_id(
    request: Request,
    cookie: Annotated[str | None, Depends(session_cookie)],
) -> int:
    if cookie is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    try:
        user_id = read_session_cookie(cookie, request.app.state.session_secret)
    except InvalidSession as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 유효하지 않거나 만료되었습니다.",
        ) from exc

    async with request.app.state.pool.acquire() as connection:
        user_exists = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)",
            user_id,
        )
    if not user_exists:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션 사용자를 찾을 수 없습니다.",
        )
    return user_id


@auth_router.get(
    "/authorize",
    summary="카카오 로그인 시작",
    description=(
        "카카오 OAuth 인가 화면으로 이동합니다. CSRF 방지를 위한 일회성 state 값을 생성해 "
        "HttpOnly 쿠키에 10분간 저장합니다."
    ),
    response_class=RedirectResponse,
    responses={
        307: {"description": "카카오 로그인 화면으로 리다이렉트"},
    },
)
async def authorize(request: Request):
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": request.app.state.kakao_client_key,
            "redirect_uri": _redirect_uri(request),
            "state": state,
        }
    )
    response = RedirectResponse(f"{KAKAO_AUTHORIZE_URL}?{query}")
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        secure=request.app.state.host.startswith("https://"),
        samesite="lax",
    )
    return response


@auth_router.get(
    "/redirect",
    summary="카카오 로그인 콜백 처리",
    description=(
        "카카오가 전달한 인가 코드와 OAuth state를 검증하고 사용자 프로필을 저장합니다. "
        "완료되면 카카오 액세스 토큰 대신 사용자 ID와 만료시각만 담은 HMAC 서명 Saver 세션 쿠키를 발급합니다."
    ),
    response_class=RedirectResponse,
    responses={
        307: {"description": "로그인 처리 후 서비스 루트로 리다이렉트"},
        400: {"description": "인가 코드가 없거나 OAuth state가 일치하지 않음"},
        502: {"description": "카카오 토큰 또는 사용자 정보 API 호출 실패"},
    },
)
async def redirect(
    request: Request,
    code: Annotated[str | None, Query(description="카카오가 발급한 일회성 인가 코드")] = None,
    state_value: Annotated[
        str | None,
        Query(alias="state", description="로그인 시작 시 생성한 CSRF 방지 state 값"),
    ] = None,
):
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not code:
        raise HTTPException(status_code=400, detail="카카오 인증 코드가 없습니다.")
    if (
        not state_value
        or not expected_state
        or not hmac.compare_digest(state_value, expected_state)
    ):
        raise HTTPException(status_code=400, detail="유효하지 않은 OAuth state입니다.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        profile, _access_token = await _request_kakao_profile(
            client,
            code=code,
            client_key=request.app.state.kakao_client_key,
            client_secret=request.app.state.kakao_client_secret,
            redirect_uri=_redirect_uri(request),
        )

    user_id, nickname, profile_image = _user_values(
        profile,
        request.app.state.default_profile_image,
    )
    async with request.app.state.pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users (id, nickname, profile_image)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
            SET nickname = EXCLUDED.nickname,
                profile_image = EXCLUDED.profile_image
            """,
            user_id,
            nickname,
            profile_image,
        )

    response = RedirectResponse(url="/")
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_cookie(
            user_id,
            request.app.state.session_secret,
            request.app.state.session_max_age,
        ),
        max_age=request.app.state.session_max_age,
        httponly=True,
        secure=request.app.state.host.startswith("https://"),
        samesite="lax",
    )
    return response


@auth_router.get(
    "/auth/me",
    summary="현재 로그인 사용자 조회",
    description=(
        "서명된 Saver 세션 쿠키를 검증하고 현재 사용자의 ID, 닉네임 및 프로필 이미지 URL을 반환합니다. "
        "쿠키가 변조되었거나 만료된 경우 인증에 실패합니다."
    ),
    response_model=UserResponse,
    responses={
        200: {"description": "현재 로그인한 사용자 정보"},
        401: {"description": "세션 쿠키가 없거나 유효하지 않음"},
    },
)
async def me(
    request: Request,
    user_id: Annotated[int, Depends(get_current_user_id)],
):
    async with request.app.state.pool.acquire() as connection:
        user = await connection.fetchrow(
            "SELECT id, nickname, profile_image FROM users WHERE id = $1",
            user_id,
        )
    if user is None:
        raise HTTPException(status_code=401, detail="세션 사용자를 찾을 수 없습니다.")
    return UserResponse(**dict(user))


@auth_router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃",
    description=(
        "브라우저의 Saver 세션 쿠키를 삭제합니다. stateless 세션이므로 서버 측 세션 데이터는 존재하지 않습니다."
    ),
    responses={204: {"description": "세션 쿠키 삭제 완료"}},
)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@auth_router.get(
    "/auth/withdraw/authorize",
    summary="계정 탈퇴용 카카오 재인증 시작",
    description=(
        "현재 Saver 세션을 검증한 뒤 카카오 계정 연결 끊기에 필요한 일회성 액세스 토큰을 얻기 위해 "
        "카카오 OAuth 인가 화면으로 이동합니다. 이 단계에서는 계정을 삭제하지 않습니다."
    ),
    response_class=RedirectResponse,
    responses={
        307: {"description": "카카오 재인증 화면으로 리다이렉트"},
        401: {"description": "Saver 세션 쿠키가 없거나 유효하지 않음"},
    },
)
async def withdraw_authorize(
    request: Request,
    _user_id: Annotated[int, Depends(get_current_user_id)],
):
    state_value = secrets.token_urlsafe(32)
    redirect_uri = f"{request.app.state.host}/auth/withdraw/redirect"
    query = urlencode(
        {
            "response_type": "code",
            "client_id": request.app.state.kakao_client_key,
            "redirect_uri": redirect_uri,
            "state": state_value,
        }
    )
    response = RedirectResponse(f"{KAKAO_AUTHORIZE_URL}?{query}")
    response.set_cookie(
        key=WITHDRAW_STATE_COOKIE,
        value=state_value,
        max_age=600,
        httponly=True,
        secure=request.app.state.host.startswith("https://"),
        samesite="lax",
    )
    return response


@auth_router.get(
    "/auth/withdraw/redirect",
    summary="카카오 연결 해제 및 계정 탈퇴 완료",
    description=(
        "카카오 재인증 계정이 현재 Saver 사용자와 같은지 확인하고 카카오 연결 끊기 API를 호출합니다. "
        "연결 끊기가 성공한 경우에만 로컬 사용자 행을 즉시 삭제하고 Saver 세션 쿠키를 제거합니다."
    ),
    response_class=RedirectResponse,
    responses={
        307: {"description": "탈퇴 완료 후 서비스 루트로 리다이렉트"},
        400: {"description": "인가 코드가 없거나 탈퇴용 OAuth state가 일치하지 않음"},
        401: {"description": "Saver 세션 쿠키가 없거나 유효하지 않음"},
        409: {"description": "재인증한 카카오 계정이 현재 Saver 사용자와 다름"},
        502: {"description": "카카오 사용자 확인 또는 연결 끊기 API 호출 실패"},
    },
)
async def withdraw_redirect(
    request: Request,
    user_id: Annotated[int, Depends(get_current_user_id)],
    code: Annotated[str | None, Query(description="카카오가 발급한 일회성 인가 코드")] = None,
    state_value: Annotated[
        str | None,
        Query(alias="state", description="탈퇴 시작 시 생성한 CSRF 방지 state 값"),
    ] = None,
):
    expected_state = request.cookies.get(WITHDRAW_STATE_COOKIE)
    if not code:
        raise HTTPException(status_code=400, detail="카카오 인증 코드가 없습니다.")
    if (
        not state_value
        or not expected_state
        or not hmac.compare_digest(state_value, expected_state)
    ):
        raise HTTPException(status_code=400, detail="유효하지 않은 탈퇴 OAuth state입니다.")

    redirect_uri = f"{request.app.state.host}/auth/withdraw/redirect"
    async with httpx.AsyncClient(timeout=10.0) as client:
        profile, access_token = await _request_kakao_profile(
            client,
            code=code,
            client_key=request.app.state.kakao_client_key,
            client_secret=request.app.state.kakao_client_secret,
            redirect_uri=redirect_uri,
        )
        kakao_user_id, _, _ = _user_values(
            profile,
            request.app.state.default_profile_image,
        )
        if kakao_user_id != user_id:
            raise HTTPException(
                status_code=409,
                detail="재인증한 카카오 계정이 현재 사용자와 일치하지 않습니다.",
            )

        unlinked_user_id = await _unlink_kakao_user(client, access_token)
        if unlinked_user_id != user_id:
            raise HTTPException(
                status_code=502,
                detail="카카오 연결 해제 결과의 사용자 ID가 일치하지 않습니다.",
            )

    async with request.app.state.pool.acquire() as connection:
        await connection.execute("DELETE FROM users WHERE id = $1", user_id)

    response = RedirectResponse(url="/?withdrawn=true")
    response.delete_cookie(WITHDRAW_STATE_COOKIE)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
