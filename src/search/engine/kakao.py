from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import certifi
import httpx

from src.search.engine.schema import ProviderDescriptor, ProviderType, QueryAnalysis, SearchCandidate, SourceType
from src.search.engine.retrieval import web_authority_score


KAKAO_WEB_SEARCH_URL = "https://dapi.kakao.com/v2/search/web"
TAG_PATTERN = re.compile(r"<[^>]*>")


class KakaoSearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class KakaoSearchSettings:
    rest_api_key: str
    endpoint: str = KAKAO_WEB_SEARCH_URL
    timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "KakaoSearchSettings | None":
        key = os.getenv("KAKAO_SEARCH_REST_API_KEY", "").strip()
        if not key:
            return None
        return cls(key, timeout_seconds=float(os.getenv("KAKAO_SEARCH_TIMEOUT", "8")))


class KakaoWebSearchProvider:
    descriptor = ProviderDescriptor(
        "kakao_web_search", ProviderType.WEB_SEARCH, frozenset({"general"}),
        frozenset({"keyword_search", "current_data"}), 0.6, 300,
    )

    def __init__(self, settings: KakaoSearchSettings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings, self._client = settings, client

    async def search(self, analysis: QueryAnalysis, limit: int) -> list[SearchCandidate]:
        plan = analysis.domain_extensions.get("provider_query_plan", {})
        query = (plan.get("queries") or [analysis.normalized_query])[0]
        headers = {"Authorization": f"KakaoAK {self.settings.rest_api_key}"}
        params = {"query": query, "size": min(max(limit, 1), 50), "page": 1, "sort": "accuracy"}
        try:
            if self._client is None:
                # Ignore stale SSL_CERT_FILE values commonly left by removed
                # Conda environments on Windows.
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout_seconds,
                    verify=certifi.where(),
                ) as client:
                    response = await client.get(self.settings.endpoint, headers=headers, params=params)
            else:
                response = await self._client.get(self.settings.endpoint, headers=headers, params=params)
            response.raise_for_status()
            documents = response.json().get("documents")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise KakaoSearchError(f"kakao web search failed ({type(exc).__name__})") from exc
        if not isinstance(documents, list):
            raise KakaoSearchError("kakao web search returned an invalid response")
        now = datetime.now(UTC)
        results = []
        for document in documents:
            url = document.get("url") if isinstance(document, dict) else None
            title = self._plain_text(document.get("title")) if isinstance(document, dict) else ""
            if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not title:
                continue
            digest = hashlib.sha256(url.encode()).hexdigest()[:24]
            results.append(SearchCandidate(
                id=f"kakao_web:{digest}", source_type=SourceType.WEB,
                provider_id="kakao_web_search", title=title,
                snippet=self._plain_text(document.get("contents")),
                structured_fields=(
                    {"publication_date": document["datetime"]}
                    if isinstance(document.get("datetime"), str) and document["datetime"].strip()
                    else {}
                ),
                url=url,
                authority_score=web_authority_score(url, self.descriptor.authority), freshness_score=0.5,
                retrieved_at=now,
            ))
        return results

    @staticmethod
    def _plain_text(value: object) -> str:
        return " ".join(html.unescape(TAG_PATTERN.sub("", value)).split()) if isinstance(value, str) else ""
