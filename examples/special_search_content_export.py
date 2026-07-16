#!/usr/bin/env python3
"""Saver 공개 콘텐츠 API를 읽어 검색 색인용 NDJSON으로 변환한다.

이 파일은 콘텐츠 API 사용 예제다. 이 코드는 단순히 예제일 뿐이며 필요에 따라 "백엔드도메인/redoc"을 참고하여 개발하도록 한다.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import date
import json
import os
import random
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class SaverApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class SaverContentClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        if timeout <= 0 or max_attempts <= 0:
            raise ValueError("timeout과 max_attempts는 양수여야 합니다.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts

    def get_json(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "saver-special-search-content-example/1.0",
            },
            method="GET",
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code in RETRYABLE_STATUS_CODES and attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise SaverApiError(
                    f"GET {url} 요청이 HTTP {exc.code}로 실패했습니다.",
                    status_code=exc.code,
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise SaverApiError(
                    f"GET {url} 요청 중 네트워크 오류가 발생했습니다: "
                    f"{type(exc).__name__}"
                ) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SaverApiError(f"GET {url} 응답이 유효한 JSON이 아닙니다.") from exc

        raise AssertionError("재시도 루프가 결과 없이 종료되었습니다.")

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(4.0, 0.25 * (2 ** (attempt - 1))) + random.uniform(0, 0.1)
        time.sleep(delay)

    def iter_news(
        self,
        *,
        page_size: int,
        publisher: str | None,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            params: dict[str, str | int] = {"page_size": page_size}
            if publisher is not None:
                params["publisher"] = publisher
            if cursor is not None:
                params["cursor"] = cursor

            page = self.get_json("/news/latest/page", params)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise SaverApiError("뉴스 페이지 응답 형식이 계약과 다릅니다.")

            for item in page["items"]:
                if not isinstance(item, dict):
                    raise SaverApiError("뉴스 항목 응답 형식이 계약과 다릅니다.")
                yield item

            has_more = page.get("has_more")
            next_cursor = page.get("next_cursor")
            if has_more is not True:
                return
            if not isinstance(next_cursor, str) or not next_cursor:
                raise SaverApiError("다음 뉴스 페이지가 있지만 next_cursor가 없습니다.")
            if next_cursor in seen_cursors:
                raise SaverApiError("뉴스 API가 같은 커서를 반복해 순회를 중단합니다.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def iter_blog_details(self, *, limit: int) -> Iterator[dict[str, Any]]:
        summaries = self.get_json("/blog/latest", {"count": limit})
        if not isinstance(summaries, list):
            raise SaverApiError("블로그 목록 응답 형식이 계약과 다릅니다.")

        for summary in summaries:
            blog_id = summary.get("id") if isinstance(summary, dict) else None
            if not isinstance(blog_id, int):
                raise SaverApiError("블로그 요약 응답에 유효한 id가 없습니다.")
            try:
                detail = self.get_json(f"/blog/{blog_id}")
            except SaverApiError as exc:
                # 목록 조회 직후 글이 삭제되는 정상적인 경쟁 조건은 다음 동기화에서 정리한다.
                if exc.status_code == 404:
                    print(
                        f"경고: blog:{blog_id}가 상세 조회 전에 삭제되어 건너뜁니다.",
                        file=sys.stderr,
                    )
                    continue
                raise
            if not isinstance(detail, dict):
                raise SaverApiError("블로그 상세 응답 형식이 계약과 다릅니다.")
            yield detail

    def get_special_days(self, year_month: str) -> list[dict[str, Any]]:
        items = self.get_json(f"/special-days/{year_month}")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise SaverApiError("특일 목록 응답 형식이 계약과 다릅니다.")
        return items


def news_document(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": f"news:{item['id']}",
        "source": "news",
        "title": item["title"],
        "content": item.get("description") or "",
        "canonical_url": item["link"],
        "api_url": None,
        "published_at": item.get("pub_date"),
        "updated_at": None,
        "metadata": {
            "publisher": item["publisher"],
            "feed_title": item["feed_title"],
            "author": item.get("author"),
            "categories": item.get("categories", []),
            "guid": item.get("guid"),
        },
    }


def blog_document(client: SaverContentClient, item: dict[str, Any]) -> dict[str, Any]:
    blog_id = item["id"]
    return {
        "document_id": f"blog:{blog_id}",
        "source": "blog",
        "title": item["title"],
        "content": item["content"],
        # 사용자에게 보여 줄 frontend URL은 별도 계약이 필요하므로 API 주소만 기록한다.
        "canonical_url": None,
        "api_url": f"{client.base_url}/blog/{blog_id}",
        "published_at": item["created_at"],
        "updated_at": item["updated_at"],
        "metadata": {
            "author_id": item["author_id"],
            "nickname": item["nickname"],
            "profile_image": item["profile_image"],
        },
    }


def special_day_document(
    client: SaverContentClient,
    year_month: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "document_id": f"special-day:{item['id']}",
        "source": "special_day",
        "title": item["date_name"],
        "content": f"{item['observed_date']} {item['date_kind']}",
        "canonical_url": None,
        "api_url": f"{client.base_url}/special-days/{year_month}",
        "published_at": item["observed_date"],
        "updated_at": None,
        "metadata": {
            "date_kind": item["date_kind"],
            "is_holiday": item["is_holiday"],
        },
    }


def year_month(value: str) -> str:
    if len(value) != 7:
        raise argparse.ArgumentTypeError("YYYY-MM 형식이어야 합니다.")
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("유효한 YYYY-MM 값이 아닙니다.") from exc
    if parsed.year < 1000:
        raise argparse.ArgumentTypeError("연도는 네 자리 양수여야 합니다.")
    return value


def positive_int_at_most_100(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("1 이상 100 이하의 정수여야 합니다.")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Saver 콘텐츠 API를 검색 색인용 NDJSON으로 내보냅니다."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("SAVER_API_BASE_URL", "http://localhost:5050"),
        help="Saver backend 기준 URL (기본값: SAVER_API_BASE_URL 또는 localhost:5050)",
    )
    parser.add_argument(
        "--publisher",
        help="이름이 정확히 일치하는 뉴스 발행자만 내보냅니다.",
    )
    parser.add_argument(
        "--news-page-size",
        type=positive_int_at_most_100,
        default=100,
        help="뉴스 페이지 크기 (1~100, 기본값: 100)",
    )
    parser.add_argument(
        "--blog-limit",
        type=positive_int_at_most_100,
        default=100,
        help="가져올 최신 블로그 수 (1~100, 기본값: 100)",
    )
    parser.add_argument(
        "--special-day-month",
        action="append",
        default=[],
        type=year_month,
        metavar="YYYY-MM",
        help="가져올 특일 연월. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument("--skip-news", action="store_true", help="뉴스를 내보내지 않습니다.")
    parser.add_argument("--skip-blogs", action="store_true", help="블로그를 내보내지 않습니다.")
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser


def emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = SaverContentClient(
            args.base_url,
            timeout=args.request_timeout,
            max_attempts=args.max_attempts,
        )

        if not args.skip_news:
            for item in client.iter_news(
                page_size=args.news_page_size,
                publisher=args.publisher,
            ):
                emit(news_document(item))

        if not args.skip_blogs:
            for item in client.iter_blog_details(limit=args.blog_limit):
                emit(blog_document(client, item))

        for month in args.special_day_month:
            for item in client.get_special_days(month):
                emit(special_day_document(client, month, item))
    except (KeyError, TypeError, ValueError, SaverApiError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
