# 검색엔진 개발자를 위한 Saver 콘텐츠 API 가이드

이 문서는 SAVER 안의 뉴스, 블로그, 특일을 대상으로 하는 특수 검색엔진 개발자를 위한 안내서다. Backend나 FastAPI를 몰라도 HTTP JSON API만으로 콘텐츠를 읽고, 검색용 문서로 변환하고, 로컬 색인을 만드는 데 필요한 현재 계약을 설명한다.

## 목차

1. [먼저 알아야 할 책임 경계](#1-먼저-알아야-할-책임-경계)
2. [빠른 시작](#2-빠른-시작)
3. [주요 API 한눈에 보기](#3-api-한눈에-보기)
   1. [뉴스(RSS) 읽기](#4-뉴스rss-읽기)
   2. [블로그 읽기](#5-블로그-읽기)
   3. [특일 읽기](#6-특일-읽기)
4. [공통 색인 설계 예시](#7-공통-색인-설계-예시)
5. [오류, 재시도, 부하 제어](#8-오류-재시도-부하-제어)
6. [(참고) 구현 확인 위치](#9-참고-구현-확인-위치)

## 1. 먼저 알아야 할 책임 경계

**가능한 한** 특수 검색엔진은 PostgreSQL을 직접 읽거나 RSS 원격 서버를 직접 호출하지 않고 SAVER backend의 공개 조회 API를 사용한다. 새 검색엔진을 만든 후에도 **Backend 저장소를 직접 수정하기 보다는, 별도의 애플리케이션을 새로 만드는 것이 이롭다**(검색엔진만 따로 만들고 나중에 API를 통해 통합).

| 콘텐츠 | 원 데이터의 저장 주체 | 특수 검색엔진이 읽을 API | API가 데이터를 못 찾았을 때 |
| --- | --- | --- | --- |
| 뉴스(RSS) | 별도 RSS 수집 작업자 | `/news/*` | 빈 결과 또는 404를 반환하며 RSS 수집을 시작하지 않음 |
| 블로그 | Saver backend | `/blog/*`의 공개 GET | 빈 결과 또는 404를 반환함 |
| 특일 | 별도 특일 수집기 | `/special-days/*` | 빈 배열을 반환하며 수집을 시작하지 않음 |

`/news/*`는 RSS XML 프록시가 아니다. RSS 수집기가 PostgreSQL에 저장한 채널과 item을 JSON으로 반환한다. 원 RSS의 모든 확장 필드를 노출하지도 않는다. 현재 응답에 없는 필드가 검색에 필요하면 DB에 직접 접근하지 말고 backend API 계약을 확장해야 한다.

다음과 같이 작동하게 만들 것을 권장한다.

1. 주기적인 동기화 프로세스가 공개 콘텐츠 API를 순회한다.
2. `news:123`, `blog:45`처럼 출처가 포함된 안정적인 문서 ID로 자체 색인을 upsert한다.
3. 검색 요청 처리 프로세스는 매 요청마다 전체 API를 훑지 않고 이 색인을 조회한다.

동기화 주기와 삭제 대조 전략은 검색엔진이 정해야 한다.

## 2. 빠른 시작

### 2.1 기준 URL 확인

개발 backend 주소를 운영 담당자에게 받아 환경 변수로 둔다. 로컬 기본 포트는 `5050`이다. 프로덕션 주소는 `https://saverapi.hufstech.com`이다.

```shell
export SAVER_API_BASE_URL=http://localhost:5050
curl --fail-with-body --silent --show-error "$SAVER_API_BASE_URL/"
```

정상이면 다음 응답을 받는다.

```json
{"message":"Hello World"}
```

FastAPI가 생성하는 명세와 대화형 문서도 사용할 수 있다.

- OpenAPI JSON: `$SAVER_API_BASE_URL/openapi.json`
- Swagger UI: `$SAVER_API_BASE_URL/docs`
- ReDoc: `$SAVER_API_BASE_URL/redoc`

로컬 backend 자체를 실행하려면 Python 패키지뿐 아니라 PostgreSQL, Redis, RabbitMQ와 카카오·세션 환경 설정이 모두 필요하다. 이 서비스는 의존 저장소 중 하나라도 연결할 수 없으면 시작하지 않는 정책을 사용한다. 콘텐츠 API만 개발하는 경우에는 준비된 backend 주소를 사용하는 것이 가장 빠르다.

### 2.2 인증

콘텐츠 조회 API는 모두 로그인 없이 호출할 수 있다. `Authorization` 헤더나 `saver_session` 쿠키를 만들지 않는다. 특히 사용자의 HttpOnly 세션 쿠키를 작업자에게 복사해서는 안 된다.

블로그 작성·수정·삭제와 `/search` 접수·결과 조회는 사용자 세션이 필요하지만, 콘텐츠 색인 작업에는 해당 엔드포인트를 사용하지 않는다. 운영망에서 별도 gateway 인증을 추가한다면 그것은 현재 애플리케이션 API와 별도인 배포 계약이다.

### 2.3 한 번에 예제 실행

저장소에는 공개 API를 순회해 통일된 NDJSON 문서로 내보내는 표준 라이브러리 기반 예제가 있다.

```shell
python examples/special_search_content_export.py \
  --base-url "$SAVER_API_BASE_URL" \
  --news-page-size 100 \
  --blog-limit 100 \
  --special-day-month 2026-07 \
  > /tmp/saver-content.ndjson

wc -l /tmp/saver-content.ndjson
head -n 1 /tmp/saver-content.ndjson | python -m json.tool
```

특정 발행자의 뉴스만 확인하려면 정확한 발행자 이름을 추가한다.

```shell
python examples/special_search_content_export.py \
  --base-url "$SAVER_API_BASE_URL" \
  --publisher "전자신문" \
  --skip-blogs \
  > /tmp/hufs-news.ndjson
```

예제의 출력은 API 원본과 검색 색인 사이의 권장 중간 형식일 뿐 공개 API 계약은 아니다. `canonical_url`이 없는 블로그·특일 문서는 frontend의 사용자 이동 URL이 합의되기 전까지 검색 결과로 노출하지 않거나, 별도 URL 매핑 계층을 둬야 한다.

## 3. API 한눈에 보기

모든 성공 응답의 본문은 UTF-8 JSON이다. 현재 URL에는 `/v1` 같은 버전 prefix가 없으므로 배포 전에 `/openapi.json` 변경을 확인한다.

| 메서드와 경로 | 용도 | 주요 입력 | 정렬/범위 | 인증 |
| --- | --- | --- | --- | --- |
| `GET /news/publishers` | RSS 발행자 전체 목록 | 없음 | `publisher ASC, id ASC` | 없음 |
| `GET /news/publishers/{publisher}` | 정확히 일치하는 발행자 상세 | URL 경로의 발행자 이름 | 한 건 | 없음 |
| `GET /news/latest` | 최신 뉴스 일부 조회 | `count=1..100`, `publisher` | 발행 시각 최신순 | 없음 |
| `GET /news/latest/page` | 뉴스 전체 커서 순회 | `page_size=1..100`, `publisher`, `cursor` | 발행 시각 최신순 | 없음 |
| `GET /blog/latest` | 최신 블로그 요약 | `count=1..100` | 생성 시각 최신순 | 없음 |
| `GET /blog/author/{user_id}` | 작성자별 블로그 요약 | 양의 정수 사용자 ID | 생성 시각 최신순 | 없음 |
| `GET /blog/{blog_id}` | 블로그 본문 상세 | 양의 정수 글 ID | 한 건 | 없음 |
| `GET /special-days/{YYYY-MM}` | 월별 특일 | 네 자리 연도와 두 자리 월 | 날짜, ID 오름차순 | 없음 |

블로그의 `POST /blog/`, `PUT /blog/{blog_id}`, `DELETE /blog/{blog_id}`는 로그인한 글 소유자를 위한 기능이다. 검색 작업자는 호출하지 않는다.

## 4. 뉴스(RSS) 읽기

### 4.1 발행자 찾기

먼저 backend가 알고 있는 정확한 발행자 이름을 조회한다.

```shell
curl --fail-with-body --silent --show-error \
  "$SAVER_API_BASE_URL/news/publishers"
```

발행자 응답의 형태는 다음과 같다.

```json
[
  {
    "id": 1,
    "publisher": "한국외대 학보",
    "feed_url": "https://example.com/rss.xml",
    "title": "한국외대 학보 RSS",
    "link": "https://example.com",
    "description": "학보 RSS",
    "language": "ko",
    "copyright": null,
    "managing_editor": null,
    "web_master": null,
    "pub_date": "2026-07-14T09:00:00+09:00",
    "last_build_date": "2026-07-14T09:05:00+09:00",
    "generator": null,
    "docs": null,
    "ttl": 30,
    "image": null,
    "rating": null,
    "categories": ["대학", "학보"]
  }
]
```

`publisher` 필터는 대소문자 변환이나 부분 일치 없이, 앞뒤 공백만 제거한 뒤 정확히 비교한다. 쿼리 문자열은 직접 이어 붙이지 말고 URL 인코딩한다.

```shell
curl --get --fail-with-body --silent --show-error \
  --data-urlencode "count=10" \
  --data-urlencode "publisher=한국외대 학보" \
  "$SAVER_API_BASE_URL/news/latest"
```

`GET /news/publishers/{publisher}`처럼 경로에 이름을 넣는 API를 사용할 때도 반드시 URL path encoding을 적용한다. 목록에서 얻은 이름을 그대로 보관하면 오탈자와 정규화 차이를 피할 수 있다.

### 4.2 소량 미리 보기

```shell
curl --get --fail-with-body --silent --show-error \
  --data-urlencode "count=10" \
  "$SAVER_API_BASE_URL/news/latest"
```

`count` 기본값은 10이고 범위는 1~100이다. 응답은 배열이며 각 뉴스 항목은 다음 필드를 가진다.

| 필드 | 형식 | 색인 시 주의점 |
| --- | --- | --- |
| `id` | 정수 | `news:{id}` 문서 ID의 재료 |
| `publisher`, `feed_title` | 문자열 | 필터·facet 후보 |
| `title`, `link` | 문자열 | 제목과 원문 URL |
| `description` | 문자열 또는 `null` | HTML일 수 있으므로 신뢰하지 말고 색인/표시 정책에 맞게 정제 |
| `author`, `comments` | 문자열 또는 `null` | 부가 정보 |
| `enclosure_url`, `enclosure_length`, `enclosure_type` | nullable | 첨부 미디어 메타데이터 |
| `guid`, `guid_is_permalink` | nullable | RSS 식별 정보. DB `id` 대신 유일하다고 가정하지 않음 |
| `pub_date` | ISO 8601 문자열 또는 `null` | 원본이 파싱되지 않으면 `null`; 현재 시각으로 대체하지 않음 |
| `source_name`, `source_url` | 문자열 또는 `null` | 재배포 항목의 원 출처 |
| `categories` | 문자열 배열 | RSS item category 이름 |

정렬은 최신순으로 반환한다(날짜 내림차순). 같은 발행 시각에서는 큰 ID가 먼저 오고, 발행 시각이 없는 항목은 전체의 뒤쪽에 온다.

### 4.3 전체 뉴스 순회

전체 색인은 `/news/latest`가 아니라 커서 API를 사용한다.

첫 페이지:

```shell
curl --get --fail-with-body --silent --show-error \
  --data-urlencode "page_size=100" \
  "$SAVER_API_BASE_URL/news/latest/page"
```

응답 예시:

```json
{
  "items": [
    {
      "id": 123,
      "publisher": "한국외대 학보",
      "feed_title": "한국외대 학보 RSS",
      "title": "새 소식",
      "link": "https://example.com/articles/123",
      "description": "기사 요약",
      "author": null,
      "comments": null,
      "enclosure_url": null,
      "enclosure_length": null,
      "enclosure_type": null,
      "guid": "article-123",
      "guid_is_permalink": false,
      "pub_date": "2026-07-14T09:00:00+09:00",
      "source_name": null,
      "source_url": null,
      "categories": ["대학"]
    }
  ],
  "next_cursor": "eyJpZCI6MTIzLCJwdWJfZGF0ZSI6IjIwMjYtMDctMTRUMDk6MDA6MDArMDk6MDAiLCJ2IjoxfQ",
  "has_more": true,
  "page_size": 100,
  "order": "pub_date DESC NULLS LAST, id DESC"
}
```

다음 페이지는 응답의 `next_cursor`를 해석하거나 수정하지 말고 그대로 전달한다.

```shell
curl --get --fail-with-body --silent --show-error \
  --data-urlencode "page_size=100" \
  --data-urlencode "cursor=$NEXT_CURSOR" \
  "$SAVER_API_BASE_URL/news/latest/page"
```

다음 규칙을 지킨다.

- `has_more`가 `false`이고 `next_cursor`가 `null`이면 종료한다.
- 발행자 필터를 사용했다면 모든 다음 페이지 요청에도 같은 `publisher`를 보낸다.
- `publisher` 같은 필터 조건을 바꿀 때는 커서를 버리고 첫 페이지부터 시작한다. 페이지 크기는 순회 중 바꾸기보다 한 번의 동기화 동안 고정하는 편이 단순하다.
- 커서는 정렬 위치이지 영구 동기화 토큰이나 권한 증표가 아니다. 내부 구조에 의존해 저장 형식을 만들지 않는다.
- 순회 도중 더 최신인 뉴스가 추가돼도 이미 발급된 커서 뒤의 기존 항목을 이어서 읽을 수 있다. 그러나 일관된 DB snapshot을 보장하는 API는 아니므로 주기적인 upsert와 대조가 필요하다.

빈 `items`는 오류가 아니며 backend가 RSS 수집을 새로 시작하게 만들지 않는다.

## 5. 블로그 읽기

### 5.1 최신 글 ID 찾기

```shell
curl --get --fail-with-body --silent --show-error \
  --data-urlencode "count=100" \
  "$SAVER_API_BASE_URL/blog/latest"
```

이 API는 본문을 제외한 요약을 반환한다.

```json
[
  {
    "id": 45,
    "title": "Saver 개발 기록",
    "created_at": "2026-07-14T00:10:00Z",
    "updated_at": "2026-07-14T01:20:00Z",
    "author_id": 123456789,
    "nickname": "Saver 사용자",
    "profile_image": "https://example.com/profile.png"
  }
]
```

`count` 기본값은 3이고 범위는 1~100이다. 생성 시각 내림차순으로 정렬된다. 본문이 필요하면 각 `id`로 상세 API를 호출한다.

```shell
curl --fail-with-body --silent --show-error \
  "$SAVER_API_BASE_URL/blog/45"
```

상세 응답에는 요약 필드와 `content`가 포함된다.

```json
{
  "id": 45,
  "title": "Saver 개발 기록",
  "content": "Saver backend의 블로그 API를 구현했습니다.",
  "created_at": "2026-07-14T00:10:00Z",
  "updated_at": "2026-07-14T01:20:00Z",
  "author_id": 123456789,
  "nickname": "Saver 사용자",
  "profile_image": "https://example.com/profile.png"
}
```

블로그 글과 목록 조회 사이에 글이 삭제될 수 있다. 목록에서 받은 ID의 상세 조회가 404이면 정상적인 경쟁 조건으로 보고 해당 문서를 건너뛴 뒤 다음 동기화에서 색인을 정리한다.

### 5.2 작성자별 목록

```shell
curl --fail-with-body --silent --show-error \
  "$SAVER_API_BASE_URL/blog/author/123456789"
```

존재하는 사용자에게 글이 없으면 `[]`, 사용자 자체가 없으면 404다. 이 응답도 본문을 포함하지 않는다. 특정 작성자 범위 검색에는 쓸 수 있지만, 전체 사용자 목록 API는 현재 없다.

### 5.3 현재 블로그 API의 색인 제약

현재 API만으로는 100개를 넘는 전체 블로그 코퍼스를 완전하게 순회할 수 없다.

- `/blog/latest`는 최대 100개이며 페이지네이션이 없다.
- 오래된 글이 수정돼도 정렬 기준이 `created_at`이므로 최신 목록에 다시 올라온다고 보장할 수 없다.
- 삭제 tombstone이나 변경 시각 이후 조회 API가 없다.
- 작성자 전체 목록 API가 없으므로 `/blog/author/{user_id}`만으로 모든 작성자를 발견할 수 없다.

따라서 최신 100개 범위의 실험용 검색은 바로 만들 수 있지만, **모든 블로그의 누락 없는 운영 검색**을 출시하기 전에는 backend에 커서 기반 전체 목록과 변경·삭제 동기화 계약을 추가해야 한다. DB를 직접 읽는 것으로 이 제약을 우회하지 않는다.

## 6. 특일 읽기

연월은 정확히 `YYYY-MM` 형식이어야 한다. 연도는 `1000`~`9999`, 월은 `01`~`12` 범위다.

```shell
curl --fail-with-body --silent --show-error \
  "$SAVER_API_BASE_URL/special-days/2026-06"
```

```json
[
  {
    "id": 1,
    "observed_date": "2026-06-06",
    "date_kind": "기념일",
    "date_name": "현충일",
    "is_holiday": true
  }
]
```

결과는 `observed_date ASC, id ASC` 순이다. `date_kind`는 저장 코드가 아니라 `국경일`, `기념일`, `24절기`, `잡절` 중 하나로 반환된다. 해당 월에 데이터가 없으면 `[]`다.

월 단위 API이므로 검색 대상 기간을 먼저 정하고 월별로 순회한다. 예를 들어 오늘 기준 과거 1년과 미래 1년만 검색할지, 전체 보유 기간을 검색할지는 색인 정책이다. 개별 특일을 사용자에게 보여 줄 frontend URL은 현재 API에 없으므로 frontend와 별도 합의가 필요하다.

## 7. 공통 색인 설계 예시

| 필드 | 의미 |
| --- | --- |
| `document_id` | 출처와 DB ID를 합친 색인 키. 예: `news:123`, `blog:45`, `special-day:1` |
| `source` | `news`, `blog`, `special_day` 중 하나 |
| `title`, `content` | 전문 검색 대상 |
| `canonical_url` | 사용자가 이동할 주소. 뉴스는 원문 link, 나머지는 현재 `null` |
| `api_url` | canonical URL이 없을 때 원 데이터를 다시 읽을 API 주소 |
| `published_at`, `updated_at` | 정렬·증분 판정에 쓸 시각. API가 주지 않으면 `null` |
| `metadata` | 발행자, 작성자, category, 특일 분류 같은 필터 후보 |

## 8. 오류, 재시도, 부하 제어

오류가 발생할 때는 대체로 JSON 본문에 `detail` 항목에 상세한 메시지가 적혀져 있다. 그러나 응답 본문의 한국어 `detail` 문자열이 아니라 HTTP 상태 코드로 분기하는 것이 더 올바르다.

| 상태 | 의미와 처리 |
| --- | --- |
| `200` | 성공. 빈 배열도 정상 데이터 |
| `404` | 발행자·블로그·사용자가 없거나 조회 사이에 삭제됨. 새 수집 작업을 만들지 않음 |
| `422` | path/query 형식 오류. 같은 요청을 재시도하지 말고 클라이언트 버그를 수정 |
| `503` | PostgreSQL 등 저장소를 일시적으로 사용할 수 없음. 지수 backoff와 jitter로 제한 재시도 |

Backend가 `429`, `502`, `504`를 반환할 수도 있다. 이때는 요청이 잘못되었을 가능성이 크다. 그러므로 운영 배포 계약에 맞춰 재시도한다. 모든 호출에 연결+읽기 timeout을 두고, 무한 재시도하지 않는다. 예제 스크립트는 네트워크 오류와 재시도 가능한 HTTP 상태에 최대 3회 재시도를 적용을 권장한다.

## 9. (참고) 구현 확인 위치

문서와 실제 동작이 다르게 보이면 OpenAPI와 다음 파일을 기준으로 backend 담당자에게 확인하라.

- 앱과 router 등록: `src/app.py`
- 뉴스 endpoint와 모델: `src/news/routes.py`, `src/news/model.py`
- 블로그 endpoint와 모델: `src/blog/get.py`, `src/blog/modify.py`, `src/blog/model.py`
- 특일 endpoint와 모델: `src/special_days/routes.py`, `src/special_days/model.py`
- 현재 검색 명령과 Redis 계약: `src/search/routes.py`, `src/search/store.py`, `src/search/model.py`
- 따라 실행할 exporter: `examples/special_search_content_export.py`

API를 추가하거나 응답 필드를 바꿀 때는 FastAPI response model과 OpenAPI 설명, 주요 오류 응답, 테스트를 함께 변경한다. 특수 검색엔진은 배포 시 지원하는 `schemaVersion`과 OpenAPI 스냅샷을 기록하고 알 수 없는 필드를 무시하되 필요한 필드가 사라지거나 형식이 달라지면 조용히 잘못 색인하지 말고 동기화를 실패시켜야 한다.
