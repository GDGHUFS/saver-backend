from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.exceptions import HTTPException
import httpx

auth_router = APIRouter()

@auth_router.get("/authorize")
async def authorize(request: Request):
    kakao_client_key = request.app.state.kakao_client_key
    redirect_uri = request.app.state.host + "/redirect"
    url = f"https://kauth.kakao.com/oauth/authorize?response_type=code&client_id={kakao_client_key}&redirect_uri={redirect_uri}"
    return RedirectResponse(url)


@auth_router.get("/redirect")
async def redirect(request: Request):
    # TODO: api 호출 분리 등 정교화하기
    kakao_client_key = request.app.state.kakao_client_key
    kakao_client_secret = request.app.state.kakao_client_secret
    redirect_uri = request.app.state.host + "/redirect"
    pool = request.app.state.pool
    data = {'grant_type': 'authorization_code',
            'client_id': kakao_client_key,
            'redirect_uri': redirect_uri,
            'client_secret': kakao_client_secret,
            'code': request.query_params.get('code')}
    async with httpx.AsyncClient() as client:
        # 토큰 가져오기
        kakao_response = await client.post("https://kauth.kakao.com/oauth/token", data=data)
        if kakao_response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to get access token")
        if "access_token" not in kakao_response.json():
            raise HTTPException(status_code=500, detail="Failed to get access token")
        access_token = kakao_response.json()["access_token"]
        # 프로필 가져오기
        profile_response = await client.get("https://kapi.kakao.com/v2/user/me", headers={"Authorization": f"Bearer {access_token}"})
        if profile_response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to get profile")
        profile = profile_response.json()
        # 데이터베이스에 없으면 생성. 있으면 다른 정보들과 함께 저장
        # TODO: 카카오 프로필 조회 필드에 맞추어 올바르게 넣기: https://developers.kakao.com/docs/ko/kakaologin/rest-api#req-user-info
        user_id = profile["id"]
        async with pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO users (id, name, profile) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET profile = $3", user_id, profile["kakao_account"]["profile"]["nickname"], profile
            )
    response = RedirectResponse(url="/")
    response.set_cookie(key="access_token", value=access_token)
    return response