# Saver 로컬 실행 및 검색 테스트

## 1. Backend 환경변수

`saver-backend/.env`에 실제 비밀값을 설정한다. `.env`는 Git에 커밋하지 않는다.

```dotenv
KAKAO_KEY=발급받은_카카오_REST_API_키
KAKAO_SECRET=발급받은_카카오_Client_Secret
SESSION_SECRET=32바이트_이상의_충분히_긴_랜덤_문자열

FRONTEND_URL=http://localhost:5173
HOST=http://localhost:5050

PG_HOST=localhost
PG_PORT=5432
PG_USER=saver
PG_PASSWORD=saver
PG_DATABASE=saverdb

REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
# 이 저장소의 로컬 compose는 Redis 인증을 활성화하므로 필수
REDIS_PASSWORD=충분히_긴_Redis_비밀번호

RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_VHOST=/
SEARCH_QUEUE=saver.search.requests
SEARCH_MAGIC_CODE_TTL=60
SEARCH_QUERY_TTL=180
SEARCH_RATE_LIMIT_MAX=10
SEARCH_RATE_LIMIT_WINDOW=60
# 검색어의 외부 전송 정책을 확인한 뒤 worker 실행 환경에서만 true로 설정
SEARCH_EXTERNAL_PROCESSING_ENABLED=true
SEARCH_USE_MOCK_PROVIDERS=false

LLM_API_KEY=발급받은_OpenAI_API_키
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
LLM_TIMEOUT_SECONDS=30

NAVER_SEARCH_CLIENT_ID=발급받은_네이버_Client_ID
NAVER_SEARCH_CLIENT_SECRET=발급받은_네이버_Client_Secret

# 로그인용 KAKAO_KEY와 분리된 검색 전용 키
KAKAO_SEARCH_REST_API_KEY=발급받은_카카오_REST_API_키
```

세션 키는 다음 명령으로 만들 수 있다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 2. Docker 인프라 시작

터미널 1에서 실행한다.

```bash
cd ~/projects/saver/saver-backend
docker compose up -d
docker compose ps
```

`postgres`, `redis`, `rabbitmq`가 모두 실행 중이면 정상이다. 모든 포트는 보안을 위해
`127.0.0.1`에만 열린다. RabbitMQ 관리 화면은 `http://localhost:15672`이며 계정은
`.env`의 `RABBITMQ_USER`와 `RABBITMQ_PASSWORD`를 사용한다.

별도의 비공개 컨테이너 네트워크에서 비밀번호 없이 Redis를 운영한다면 backend와 worker 환경에서
`REDIS_PASSWORD`를 생략한다. 빈 문자열도 무인증 연결로 처리된다. 위 예시는 이 저장소의
`compose.yaml`이 `requirepass`를 활성화하기 때문에 비밀번호가 필요한 경우다.

이 Compose 파일은 로컬 개발 전용이다. 운영 서버에서는 그대로 사용하지 말고 비공개 네트워크,
TLS, 전용 secret 관리 및 최소 권한 계정을 적용한 별도 배포 설정을 사용한다.

최초 실행이거나 DB 테이블을 다시 확인해야 한다면 프로젝트의 기존 DB 초기화 절차도 실행한다.

## 3. Backend HTTP 서버 시작

터미널 1에서 이어서 실행한다. `conda create -n saver python=3.10`, `pip install -r requirements.txt`는 최초 1회만 실행한다.

```bash
conda create -n saver python=3.10
conda activate saver
pip install -r requirements.txt
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
uvicorn src.app:app --reload --host 0.0.0.0 --port 5050 --env-file .env
```

다음 로그가 나오면 backend가 실행된 것이다.

```text
Application startup complete.
```

브라우저에서 `http://localhost:5050/`을 열어 `Hello World` 응답을 확인할 수 있다.

## 4. Python 검색 worker 시작

새 터미널 2에서 실행한다.

```bash
cd ~/projects/saver/saver-backend
conda activate saver
pip install -r requirements.txt # 이전 단계에서 했으면 생략
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
python -m src.search.worker
```

다음 로그가 나오면 RabbitMQ 검색 작업을 받을 준비가 된 것이다.

```text
Search worker is consuming queue saver.search.requests
```

worker는 FastAPI 요청 프로세스와 분리되어 동작한다.

`SEARCH_EXTERNAL_PROCESSING_ENABLED=true`는 사용자의 검색어가 설정된 LLM 및 검색 Provider로
전송됨을 확인한 운영 환경에서만 사용한다. 기본값은 외부 전송을 막는 `false`다.

```text
frontend 검색 요청
→ backend가 Redis 상태 생성
→ backend가 RabbitMQ에 명령 발행
→ Python worker가 지능형 검색 실행
→ worker가 Redis에 결과 저장
→ frontend polling 요청에 backend가 Redis 결과 반환
```

backend만 실행하고 worker를 실행하지 않으면 검색 상태는 `PENDING`으로 남다가 TTL 이후 만료될 수 있다.

완료 응답의 `result.answer`에는 아래 CLI가 출력하는 것과 같은 최종 답변이 들어가고,
`result.data.search`에는 답변의 검색 근거가 들어간다. 로그인한 브라우저에서
`http://localhost:5050/docs`를 열어 `POST /search`로 발급받은 `magicCode`를
`GET /search/{magic_code}`에 입력하면 이 비동기 흐름을 직접 확인할 수 있다.


## 5. 질문 답변을 CLI로 직접 테스트

frontend, backend, Redis 및 RabbitMQ를 거치지 않고 현재 지능형 검색의 답변만 확인하려면 backend
루트에서 CLI를 실행한다.

예시 질문 리스트이고 다른 질문을 시도해 봐도 됩니다.
답변만 확인:

```bash
python -m src.search.engine.cli "서울시 월세 원룸 거주 1인 가구가 지원받을 수 있는 주거 정책 알려줘"
python -m src.search.engine.cli "서울에서 무료로 관람할 수 있는 미술관 알려줘"
```

답변과 실행 메타데이터 및 Provider 검색 결과 확인:

```bash
python -m src.search.engine.cli "서울시 월세 원룸 거주 1인 가구가 지원받을 수 있는 주거 정책 알려줘" --verbose
```

현재 처리 흐름은 다음과 같다.

```text
OpenAI 질의 분석 및 Provider 계획
→ Naver·Kakao 검색
→ URL 정규화 및 중복 제거
→ 순위 결합과 lexical/hybrid ranking
→ 필요한 경우 nugget·knowledge·diversity·eligibility 처리
→ 상위 검색 결과를 근거로 OpenAI가 최종 답변 생성
```

## 6. 종료

backend와 worker는 각각 실행한 터미널에서 `Ctrl+C`로 종료한다.

```bash
cd ~/projects/saver/saver-backend
docker compose down
```

데이터까지 삭제하는 `docker compose down -v`는 PostgreSQL, Redis 및 RabbitMQ 로컬 볼륨을
삭제하므로 필요한 데이터가 없을 때만 사용한다.
