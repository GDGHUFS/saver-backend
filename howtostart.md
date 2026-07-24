# Saver 로컬 실행 및 검색 테스트

이 문서는 Windows에서 **Git Bash와 Conda 환경만 사용하는 실행 순서**를 기준으로 한다. 아래 명령은
모두 Git Bash에 입력하며 PowerShell의 `$env:변수명` 문법은 사용하지 않는다. 기본 실행 순서는
Docker 인프라 → backend HTTP 서버 → Python 검색 worker → frontend다. 지능형 검색 답변만 빠르게
확인할 때는 마지막의 독립 CLI 테스트를 사용할 수 있다.

## 1. 사전 준비

- Docker Desktop을 실행한다.
- Conda 환경 `saver`를 활성화할 수 있어야 한다.
- frontend까지 실행하려면 Node.js와 npm이 필요하다.
- 카카오 로그인 테스트 시 개발자 콘솔에 다음 Redirect URI를 등록한다.

```text
http://localhost:5050/redirect
http://localhost:5050/auth/withdraw/redirect
```

## 2. Backend 환경변수

`saver-backend/.env`에 실제 비밀값을 설정한다. `.env`는 Git에 커밋하지 않는다.

```dotenv
KAKAO_KEY=발급받은_카카오_REST_API_키
KAKAO_SECRET=발급받은_카카오_Client_Secret
SESSION_SECRET=충분히_긴_랜덤_문자열

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

RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_VHOST=/
SEARCH_QUEUE=saver.search.requests
SEARCH_MAGIC_CODE_TTL=60
SEARCH_QUERY_TTL=180

LLM_API_KEY=발급받은_OpenAI_API_키
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini

NAVER_SEARCH_CLIENT_ID=발급받은_네이버_Client_ID
NAVER_SEARCH_CLIENT_SECRET=발급받은_네이버_Client_Secret

# 선택 사항. 없으면 기존 KAKAO_KEY를 검색 키로 사용한다.
KAKAO_SEARCH_REST_API_KEY=발급받은_카카오_REST_API_키
```

세션 키는 다음 명령으로 만들 수 있다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 3. Docker 인프라 시작

터미널 1에서 실행한다.

```bash
cd ~/projects/saver/saver-backend
docker compose up -d
docker compose ps
```

`postgres`, `redis`, `rabbitmq`가 모두 실행 중이면 정상이다. RabbitMQ 관리 화면은
`http://localhost:15672`이며 기본 계정은 `guest` / `guest`다.

최초 실행이거나 DB 테이블을 다시 확인해야 한다면 프로젝트의 기존 DB 초기화 절차도 실행한다.

## 4. Backend HTTP 서버 시작

터미널 1에서 이어서 실행한다.

```bash
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

## 5. Python 검색 worker 시작

새 터미널 2에서 실행한다.

```bash
cd ~/projects/saver/saver-backend
conda activate saver
pip install -r requirements.txt
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
python -m src.search.worker
```

다음 로그가 나오면 RabbitMQ 검색 작업을 받을 준비가 된 것이다.

```text
Search worker is consuming queue saver.search.requests
```

worker는 FastAPI 요청 프로세스와 분리되어 동작한다.

```text
frontend 검색 요청
→ backend가 Redis 상태 생성
→ backend가 RabbitMQ에 명령 발행
→ Python worker가 지능형 검색 실행
→ worker가 Redis에 결과 저장
→ frontend polling 요청에 backend가 Redis 결과 반환
```

backend만 실행하고 worker를 실행하지 않으면 검색 상태는 `PENDING`으로 남다가 TTL 이후 만료될 수 있다.

## 6. Frontend 시작

새 터미널 3에서 실행한다.

```bash
cd ~/projects/saver/saver-frontend
```

`saver-frontend/.env`에 backend 주소를 설정한다.

```dotenv
VITE_API_BASE_URL=http://localhost:5050
```

이후 실행한다.

```bash
npm install
npm run dev
```

브라우저에서 `http://localhost:5173/`을 연다. frontend와 backend는 현재 각 저장소의 `main` 연결
계약을 그대로 사용한다.

## 7. 질문 답변을 CLI로 직접 테스트

frontend, backend, Redis 및 RabbitMQ를 거치지 않고 현재 지능형 검색의 답변만 확인하려면 backend
루트에서 CLI를 실행한다.

답변만 확인:

```bash
python -m src.search.engine.cli "서울시 월세 원룸 거주 1인 가구가 지원받을 수 있는 주거 정책 알려줘"
```

답변과 실행 메타데이터 및 Provider 검색 결과 확인:

```bash
python -m src.search.engine.cli "서울시 월세 원룸 거주 1인 가구가 지원받을 수 있는 주거 정책 알려줘" --verbose
python -m src.search.engine.cli "서울시 도봉구 월세 원룸 거주하는 연소득 1800만원인 1인 가구가 지원받을 수 있는 주거 정책 알려줘" --verbose
```

다른 질문도 같은 방식으로 테스트한다.

```bash
python -m src.search.engine.cli "GPT-5 API 가격 알려줘" --verbose
python -m src.search.engine.cli "서울에서 무료로 관람할 수 있는 미술관 알려줘" --verbose
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

`--verbose`에서는 우선 다음을 확인한다.

- `requested_provider_targets`: OpenAI가 계획한 Provider
- `selected_provider_targets`: 키가 설정되어 실제 선택된 Provider
- `unavailable_provider_targets`: 설정되지 않아 사용할 수 없는 Provider
- `failed_providers`: 선택됐지만 호출에 실패한 Provider
- `provider_errors`: 비밀값을 제외한 오류 종류
- `provider_execution`: Provider별 상태와 후보 개수
- `provider_query_plans`: 실제 계획된 검색어와 필터
- `executed_steps`, `skipped_steps`: 실행 단계와 생략 이유
- `answer_nuggets`, `eligibility`: 해당 단계가 계획된 경우의 결과

현재 복원 버전에는 이후 실험 버전의 `stage_trace`, `result_diagnostics`, `answer_provenance`,
`selected_for_answer`가 없다. 이 필드가 출력되지 않는 것은 정상이다.

## 8. 자주 발생하는 오류

### 요청한 외부 검색 서비스가 설정되지 않음

`.env`의 Naver ID와 Secret이 모두 설정됐는지 확인한다. 카카오 검색은
`KAKAO_SEARCH_REST_API_KEY` 또는 `KAKAO_KEY`를 사용한다.

### 외부 검색 서비스 호출 실패

`--verbose`의 `provider_errors`를 확인한다. API 권한, 호출 한도, 네트워크 상태를 점검하되 키 원문은
로그에 출력하지 않는다.

### `LLM_API_KEY is required`

`.env`가 backend 루트에 있는지와 환경변수 이름이 정확한지 확인한다. 현재 이름은
`LLM_API_KEY`이며 endpoint와 모델은 각각 `LLM_BASE_URL`, `LLM_MODEL`을 사용한다.

### `ModuleNotFoundError: No module named 'openai'`

현재 활성화된 Python 환경에 의존성을 설치한다.

```bash
pip install -r requirements.txt
```

### Windows/Conda Git Bash SSL 인증서 오류

```bash
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
```

Naver, Kakao 및 OpenAI 클라이언트도 현재 Python 환경의 `certifi` 인증서를 사용한다.

## 9. 자동 테스트

전체 테스트:

```bash
python -m unittest discover -s tests -v
```

검색 엔진 테스트:

```bash
python -m unittest \
  tests.test_intelligent_search \
  tests.test_llm_query_analysis \
  tests.test_provider_query_adapter \
  tests.test_search_ranking \
  tests.test_naver_search \
  tests.test_kakao_search -v
```

자동 테스트는 외부 API를 실제 호출하지 않는다. 실제 키, SSL, Naver·Kakao 권한까지 확인하려면 7절의
CLI 명령을 별도로 실행한다.

## 10. 종료

backend와 worker는 각각 실행한 터미널에서 `Ctrl+C`로 종료한다.

```bash
cd ~/projects/saver/saver-backend
docker compose down
```

데이터까지 삭제하는 `docker compose down -v`는 PostgreSQL, Redis 및 RabbitMQ 로컬 볼륨을
삭제하므로 필요한 데이터가 없을 때만 사용한다.
