# Saver backend

## 개발 문서

- [특수 검색엔진 개발자를 위한 콘텐츠 API 가이드](docs/special-search-engine-content-api-guide.md): 뉴스(RSS 저장 결과), 블로그, 특일 API를 순회해 별도 검색 색인을 만드는 방법과 새 검색 작업자 연동 전 확인할 계약을 설명한다.

## 검색 API

- `POST /search`: 로그인 사용자의 `{"query": "..."}`를 받아 `202`와 `magicCode`를 반환한다.
- `GET /search/{magicCode}`: intelligent 검색 상태를 조회한다. 처리 중이면 `202`, 완료되면
  같은 결과 객체를 legacy와 intelligent 필드에 함께 넣은 `COMPLETED` 상태의 `200`을 반환한 뒤
  사용한 `magicCode`를 삭제한다.

두 API 모두 유효한 Saver 세션 쿠키와 해당 사용자의 DB 행이 필요하다. 인증된 사용자 ID 원문은
Redis 검색 상태 또는 RabbitMQ 메시지에 연결하거나 저장하지 않는다. 검색 접수 남용 방지를 위해
세션 비밀값으로 HMAC 처리한 비가역 식별자만 별도 rate-limit 키에 사용한다.

검색 상태의 유일한 원천은 Redis다. 두 결과가 모두 캐시되어 있으면 RabbitMQ 발행을 생략한다.
없으면 durable exchange를 통해 intelligent 전용 durable queue에 persistent 메시지를 한 번 발행한다.
legacy 필드는 intelligent 결과 객체를 그대로 가리키는 API 호환용 alias이며, legacy queue에는 신규
메시지를 전달하지 않는다. 조회 API는 RabbitMQ 또는 외부 검색 API를 호출하지 않는다.

기본 키와 TTL은 다음과 같다.

- `saver:search:ticket:{magicCode}`: hash, 기본 TTL 300초, 필드 `status`, `query_key`,
  `intelligent_query_key`
- `saver:search:query:{sha256}`: 기존 키와의 호환을 위해 유지되지만 신규 worker는 사용하지 않음
- `saver:search:query:{sha256}:intelligent`: intelligent worker 상태와 결과, 기본 TTL 600초
- `saver:search:rate:{hmac}`: 사용자별 검색 접수 횟수, 기본 60초

각 작업 상태는 `PENDING`, `COMPLETED`, `FAILED` 중 하나다. legacy 결과는 기존 Kagi 계약을
유지하고, intelligent 결과는 필수 `answer`와 검색 근거를 포함한다.

완료 응답 예시는 다음과 같다.

```json
{
  "magicCode": "발급받은 magicCode",
  "status": "COMPLETED",
  "results": {
    "legacy": {
      "status": "COMPLETED",
      "result": {
        "data": {"related_search": [], "search": []},
        "meta": {"ms": 320}
      }
    },
    "intelligent": {
      "status": "COMPLETED",
      "result": {
        "answer": "검색 근거를 바탕으로 생성한 최종 답변",
        "data": {"related_search": [], "search": []},
        "meta": {"ms": 1234}
      }
    }
  }
}
```

기본 RabbitMQ topology는 다음과 같다.

- fanout exchange: `saver.search.requested.v1`
- legacy queue: `saver.search.legacy.requests`
- intelligent queue: `saver.search.intelligent.requests`

기존 `saver-search` worker의 `SEARCH_QUEUE`는 legacy queue와 같게 설정하고, 이 저장소의
`src.search.worker`는 `SEARCH_INTELLIGENT_QUEUE`를 사용한다. 두 queue는 아래와 같은 메시지를
각각 한 부씩 받는다.

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
각 worker는 자신의 Redis key 갱신을 완료한 뒤 RabbitMQ 메시지를 ACK하고, 완료 시 해당 key의
`status`와 `result`를 각각 `COMPLETED` 및 JSON 문자열로 저장한다. 실패 시 자신의 상태만
`FAILED`로 저장한다.

연결과 TTL은 `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, 선택적인 `REDIS_PASSWORD`, `RABBITMQ_HOST`,
`RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `RABBITMQ_VHOST`, `SEARCH_EXCHANGE`,
`SEARCH_LEGACY_QUEUE`, `SEARCH_INTELLIGENT_QUEUE`, `SEARCH_MAGIC_CODE_TTL`, `SEARCH_QUERY_TTL`,
`SEARCH_RATE_LIMIT_MAX`, `SEARCH_RATE_LIMIT_WINDOW` 환경 변수로 설정한다. 기본 검색 접수 제한은
사용자별 60초당 10회다.

## 날씨 API

- `GET /weather/current`: 별도 날씨 수집기가 저장한 전국 고유 격자별 최신 발표본에서 현재
  시각과 가장 가까운 단기예보 한 건을 반환한다. 결과는 Redis에 기본 300초 동안 캐시한다.
- `GET /weather/forecast?region=서울특별시%20종로구&hours=24`: 공백 단위 지역명 토큰이 모두
  일치하는 격자의 현재 시간대 이후 최신 단기예보를 반환한다.
- `GET /weather/forecast?latitude=37.5704&longitude=126.9816&hours=24`: 위경도를 기상청
  5km 격자로 변환해 같은 단기예보를 반환한다.
- `GET /weather/locations`: `/weather/forecast`에서 사용할 공식 1단계 지역명 목록을 조회한다.
- `GET /weather/locations?region_level_1=서울특별시`: 선택한 1단계 지역의 2단계 목록을 조회한다.
- `GET /weather/locations?region_level_1=서울특별시&region_level_2=종로구`: 선택한 1·2단계
  지역의 3단계 목록을 조회한다. 각 응답의 `full_name`은 forecast의 `region`에 그대로 쓸 수 있다.

날씨 API는 모두 로그인 없이 호출할 수 있다. backend는 외부 기상 API를 호출하거나 수집을 시작하지
않고 `weather_*` PostgreSQL 테이블에 이미 저장된 결과만 읽는다. 전국 현재 현황은 실황 관측값이
아니므로 응답의 `issued_at`과 `forecast_at`을 함께 확인해야 한다. 지역명과 좌표는 동시에 지정할 수
없고, 좌표는 위도와 경도를 모두 지정해야 한다. `hours`는 격자별 최대 예보 시간대 수이며 1~72,
기본 24이다.

전국 현재 날씨 캐시 TTL은 `WEATHER_CURRENT_CACHE_TTL` 환경 변수로 설정한다. Redis 캐시를
일시적으로 사용할 수 없거나 캐시 값이 현재 응답 계약과 맞지 않으면 PostgreSQL 조회로 대체한다.

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
Redis가 비공개 컨테이너 네트워크에서 인증 없이 동작한다면 `REDIS_PASSWORD`를 설정하지 않는다.
비밀번호 인증을 활성화한 Redis에서만 이 값을 backend와 worker에 동일하게 주입한다.
웹 배포에서는 같은 이미지로 두 프로세스를 실행한다. HTTP 서비스는 이미지의 기본 command를 사용하고,
검색 worker 서비스는 command를 `python -m src.search.worker`로 덮어쓴다. 두 프로세스에는 같은
Redis·RabbitMQ 설정을 주입하고, 외부 API 키와 `SEARCH_EXTERNAL_PROCESSING_ENABLED=true`는
검색 worker에만 주입한다. 기존 `saver-search`에는 `SEARCH_QUEUE`를 backend의
`SEARCH_LEGACY_QUEUE`와 같은 값으로 주입한다. 두 worker 중 하나가 없으면 해당 분기는 `PENDING`으로
남고 조회 응답은 `202`를 유지하므로, 두 queue의 consumer 수와 적체량을 함께 감시해야 한다.

임시 비교 구성을 배포할 때는 다음 순서를 권장한다.

1. 기존 `saver-search`를 `SEARCH_QUEUE=saver.search.legacy.requests`로 재기동한다.
2. 이 이미지의 `python -m src.search.worker` 프로세스를
   `SEARCH_INTELLIGENT_QUEUE=saver.search.intelligent.requests`로 기동한다.
3. 마지막으로 HTTP backend를 새 exchange·queue 설정으로 배포한다. backend 시작 과정이 exchange,
   두 queue와 binding을 모두 선언한다.
4. 두 queue의 consumer가 각각 1명인지와 `messages_ready`, `messages_unacknowledged`를 확인한다.

이 변경 전 backend worker가 기본 query key에 저장한 캐시는 legacy 결과와 구분할 수 없다.
배포 시 기존 query TTL이 끝날 때까지 기다리거나, 영향 범위를 확인한 뒤 해당
`saver:search:query:{sha256}` key만 선별적으로 제거한다. Redis 전체 초기화는 하지 않는다.
Frontend 응답 경로도 기존 `result`에서 `results.legacy`와 `results.intelligent`로 변경해야 한다.

TODO: 운영 동시성 기준을 정한 뒤 Gunicorn과 `uvicorn.workers.UvicornWorker`를 사용하는 다중 worker
구성으로 전환한다. 현재 이미지는 Uvicorn 단일 프로세스로 실행한다.
