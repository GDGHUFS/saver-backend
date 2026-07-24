from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import certifi
import httpx

from src.search.engine.schema import (
    ProviderDescriptor,
    ProviderType,
    QueryAnalysis,
    SearchCandidate,
    SourceType,
)
from src.search.engine.retrieval import web_authority_score


NAVER_WEB_SEARCH_URL = "https://openapi.naver.com/v1/search/webkr.json"
TAG_PATTERN = re.compile(r"<[^>]*>")


class NaverSearchError(RuntimeError):
    """네이버 검색 API 호출 또는 응답 검증 실패. credential은 포함하지 않는다."""


@dataclass(frozen=True)
class NaverSearchSettings:
    client_id: str
    client_secret: str
    endpoint: str = NAVER_WEB_SEARCH_URL
    timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.client_secret.strip():
            raise ValueError("NAVER_SEARCH_CLIENT_ID and NAVER_SEARCH_CLIENT_SECRET are required")
        if not self.endpoint.startswith("https://openapi.naver.com/"):
            raise ValueError("NAVER_SEARCH_ENDPOINT must use the official HTTPS host")
        if self.timeout_seconds <= 0:
            raise ValueError("NAVER_SEARCH_TIMEOUT must be greater than zero")

    @classmethod
    def from_env(cls) -> NaverSearchSettings | None:
        client_id = os.getenv("NAVER_SEARCH_CLIENT_ID", "").strip()
        client_secret = os.getenv("NAVER_SEARCH_CLIENT_SECRET", "").strip()
        if not client_id and not client_secret:
            return None
        if not client_id or not client_secret:
            raise ValueError("both NAVER_SEARCH_CLIENT_ID and NAVER_SEARCH_CLIENT_SECRET must be set")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            endpoint=os.getenv("NAVER_SEARCH_ENDPOINT", NAVER_WEB_SEARCH_URL).strip(),
            timeout_seconds=float(os.getenv("NAVER_SEARCH_TIMEOUT", "8")),
        )


class NaverWebSearchProvider:
    descriptor = ProviderDescriptor(
        provider_id="naver_web_search",
        provider_type=ProviderType.WEB_SEARCH,
        domains=frozenset({
            "general", "weather", "air_quality", "transport", "culture", "tourism",
            "library", "statistics", "law", "public_administration", "education",
        }),
        capabilities=frozenset({"keyword_search", "current_data"}),
        authority=0.6,
        expected_latency_ms=300,
    )

    def __init__(self, settings: NaverSearchSettings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    async def search(self, analysis: QueryAnalysis, limit: int) -> list[SearchCandidate]:
        display = min(max(limit, 1), 100)
        headers = {
            "X-Naver-Client-Id": self.settings.client_id,
            "X-Naver-Client-Secret": self.settings.client_secret,
            "Accept": "application/json",
        }
        provider_plan = analysis.domain_extensions.get("provider_query_plan", {})
        queries = provider_plan.get("queries", []) or [analysis.normalized_query]
        params = {"query": queries[0], "display": display, "start": 1, "sort": "sim"}
        try:
            if self._client is None:
                # Ignore stale SSL_CERT_FILE values commonly left by removed
                # Conda environments on Windows.
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout_seconds,
                    verify=certifi.where(),
                ) as client:
                    response = await self._request(client, headers, params)
            else:
                response = await self._request(self._client, headers, params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NaverSearchError(f"naver web search failed ({type(exc).__name__})") from exc
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise NaverSearchError("naver web search returned an invalid response")
        retrieved_at = datetime.now(UTC)
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            link = item.get("link")
            title = self._plain_text(item.get("title"))
            if not isinstance(link, str) or not link.startswith(("http://", "https://")) or not title:
                continue
            snippet = self._plain_text(item.get("description"))
            external_id = hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]
            candidates.append(SearchCandidate(
                id=f"naver_web:{external_id}",
                source_type=SourceType.WEB,
                provider_id=self.descriptor.provider_id,
                title=title,
                snippet=snippet,
                content=None,
                structured_fields={},
                url=link,
                authority_score=web_authority_score(link, self.descriptor.authority),
                freshness_score=0.5,
                retrieved_at=retrieved_at,
            ))
        return candidates

    async def _request(self, client: httpx.AsyncClient, headers: dict[str, str], params: dict[str, object]) -> httpx.Response:
        for attempt in range(2):
            try:
                response = await client.get(self.settings.endpoint, headers=headers, params=params)
            except httpx.TransportError:
                if attempt == 1:
                    raise
                continue
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == 1:
                return response
        raise AssertionError("unreachable")

    @staticmethod
    def _plain_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(html.unescape(TAG_PATTERN.sub("", value)).split())
