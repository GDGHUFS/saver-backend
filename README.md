# Saver backend

## 검색 API

- `POST /search`: 로그인 사용자의 `{"query": "..."}`를 받아 `202`와 `magicCode`를 반환한다.
- `GET /search/{magicCode}`: 로그인 상태를 확인하고, 처리 중에는 `202`, 완료 시에는 `200`과 Redis 결과를 반환한다.

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
