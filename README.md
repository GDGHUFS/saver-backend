# Saver backend

## 개발 문서

- [특수 검색엔진 개발자를 위한 콘텐츠 API 가이드](docs/special-search-engine-content-api-guide.md): 뉴스(RSS 저장 결과), 블로그, 특일 API를 순회해 별도 검색 색인을 만드는 방법과 새 검색 작업자 연동 전 확인할 계약을 설명한다.

## 검색 API

- `POST /search`: 로그인 사용자의 `{"query": "..."}`를 받아 `202`와 `magicCode`를 반환한다.
- `GET /search/{magicCode}`: 로그인 상태를 확인하고, 처리 중에는 `202`, 완료 시에는 `200`과 Redis 결과를 반환한 뒤 사용한 `magicCode`를 삭제한다.

두 API 모두 유효한 Saver 세션 쿠키와 해당 사용자의 DB 행이 필요하다. 인증된 사용자 ID는 접근
허용 여부 확인에만 사용하며 Redis 키, 검색 상태 또는 RabbitMQ 메시지와 연결하거나 저장하지 않는다.

검색 상태의 유일한 원천은 Redis다. 동일한 정규화 검색어의 완료 결과가 Redis에 있으면
RabbitMQ 발행을 생략하며, 결과가 없으면 durable queue에 persistent 메시지를 publisher confirm과
함께 발행한다. 조회 API는 RabbitMQ 또는 외부 검색 API를 호출하지 않는다.

기본 키와 TTL은 다음과 같다.

- `saver:search:ticket:{magicCode}`: hash, 기본 TTL 60초, 필드 `status`, `query_key`
- `saver:search:query:{sha256}`: hash, 기본 TTL 180초, 필드 `status`, 완료 시 `result`

상태는 `PENDING`, `COMPLETED`, `FAILED` 중 하나다. `result`는 UTF-8 JSON 문자열이어야 한다.
외부 검색 작업자는 기본 queue `saver.search.requests`에서 아래 메시지를 소비한다.

```json
{
  "schemaVersion": 1,
  "jobId": "정규화 검색어의 SHA-256",
  "magicCode": "권한 증표",
  "query": "정규화된 검색어",
  "queryHash": "정규화 검색어의 SHA-256"
}
```

`jobId`는 동일 검색어 재발행 시에도 같으므로 작업자는 이를 기준으로 중복 처리를 안전하게 해야 한다.
Redis 갱신을 완료한 뒤 RabbitMQ 메시지를 ACK하고, 완료 시 query hash의 `status`와 `result`를
각각 `COMPLETED` 및 JSON 문자열로 저장한다. 실패 시 query hash 상태를 `FAILED`로 저장할 수 있다.

연결과 TTL은 `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `RABBITMQ_HOST`,
`RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `RABBITMQ_VHOST`, `SEARCH_QUEUE`,
`SEARCH_MAGIC_CODE_TTL`, `SEARCH_QUERY_TTL` 환경 변수로 설정한다.

## 날씨 API

- `GET /weather/current`: 별도 날씨 수집기가 저장한 전국 고유 격자별 최신 발표본에서 현재
  시각과 가장 가까운 단기예보 한 건을 반환한다.
- `GET /weather/forecast?region=서울특별시%20종로구&hours=24`: 공백 단위 지역명 토큰이 모두
  일치하는 격자의 현재 시간대 이후 최신 단기예보를 반환한다.
- `GET /weather/forecast?latitude=37.5704&longitude=126.9816&hours=24`: 위경도를 기상청
  5km 격자로 변환해 같은 단기예보를 반환한다.

두 API 모두 로그인 없이 호출할 수 있다. backend는 외부 기상 API를 호출하거나 수집을 시작하지
않고 `weather_*` PostgreSQL 테이블에 이미 저장된 결과만 읽는다. 전국 현재 현황은 실황 관측값이
아니므로 응답의 `issued_at`과 `forecast_at`을 함께 확인해야 한다. 지역명과 좌표는 동시에 지정할 수
없고, 좌표는 위도와 경도를 모두 지정해야 한다. `hours`는 격자별 최대 예보 시간대 수이며 1~72,
기본 24이다.

## 운영 정책 및 TODO

- PostgreSQL, Redis, RabbitMQ를 하나의 서비스 묶음으로 취급한다. 시작 시 하나라도 연결할 수 없으면
  backend 전체를 기동하지 않는 fail-fast 정책을 사용한다.
- 카카오 연결 해제 후 로컬 사용자 삭제가 일시적으로 실패하면 짧게 재시도한다. 재시도 후에도 실패하면
  내부 예외 원문은 노출하지 않고 수동 조정 대상을 식별할 `user_id`와 예외 클래스만 포함한 `CRITICAL`
  운영 이벤트를 남긴다. `user_id` 기록은 카카오 연결 해제 후 로컬 삭제가 최종 실패한 경우로 제한한다.
- frontend와 backend의 공개 주소는 각각 `FRONTEND_URL`과 `HOST`로 설정한다. 예를 들어 frontend가
  `https://example.com`, backend가 `https://api.example.com`이면 아래처럼 설정한다.

  ```shell
  FRONTEND_URL=https://example.com
  HOST=https://api.example.com
  ```

  로그인 완료 후에는 `FRONTEND_URL/`, 탈퇴 완료 후에는 `FRONTEND_URL/?withdrawn=true`로 이동한다.
  카카오 개발자 콘솔의 Redirect URI는 frontend가 아니라 backend의 `HOST/redirect`와
  `HOST/auth/withdraw/redirect`를 등록해야 한다.
- `FRONTEND_URL`의 origin은 credential CORS allowlist에 자동으로 포함된다. preview나 로컬 frontend 등
  추가 origin은 `CORS_ALLOWED_ORIGINS`에 쉼표로 구분해 설정한다. wildcard origin은 허용하지 않는다.
  frontend의 API 요청은 브라우저가 API 호스트 전용 HttpOnly 세션 쿠키를 전달하도록 credential 옵션을
  포함해야 한다(예: Fetch API의 `credentials: "include"`). 세션 쿠키를 상위 도메인 전체에 공유할
  필요는 없다.

## 컨테이너 이미지

로컬 이미지는 저장소 루트에서 다음 명령으로 빌드한다.

```shell
podman build --file Containerfile --tag saver-backend:local .
```

로컬 빌드와 실행은 rootless Podman을 기준으로 한다. 컨테이너 내부에서는 별도 사용자를 만들지 않지만,
컨테이너의 root는 호스트의 비특권 사용자 namespace에 매핑된다. 이 이미지를 rootful container runtime으로
실행할 때는 동일한 격리 조건이 적용되지 않으므로 별도의 사용자 또는 runtime 보안 정책이 필요하다.

GitHub Actions의 `Build and push container image` workflow는 표준 Docker Buildx action을 사용해 수동으로
빌드하며, 입력한 태그로 Docker Hub에 push한다. 저장소에 다음 Actions secrets를 설정해야 한다.

- `DOCKERHUB_REGISTRY`: registry hostname. Docker Hub는 `docker.io`
- `DOCKERHUB_USERNAME`: Docker Hub 사용자 ID
- `DOCKERHUB_TOKEN`: 이미지 push 권한이 있는 Docker Hub access token
- `DOCKERHUB_REPOSITORY`: 사용자 계정 아래의 repository 이름(예: `saver-backend`)

컨테이너 실행 시 PostgreSQL, Redis, RabbitMQ, 카카오 인증 및 세션 관련 환경 변수를 주입해야 한다.

TODO: 운영 동시성 기준을 정한 뒤 Gunicorn과 `uvicorn.workers.UvicornWorker`를 사용하는 다중 worker
구성으로 전환한다. 현재 이미지는 Uvicorn 단일 프로세스로 실행한다.
