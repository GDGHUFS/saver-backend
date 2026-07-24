from __future__ import annotations

from copy import deepcopy

from src.search.engine.schema import (
    ProviderDescriptor, ProviderType, QueryAnalysis, SearchCandidate, SearchProvider, SourceType,
)


class InMemoryProvider:
    def __init__(self, descriptor: ProviderDescriptor, records: list[SearchCandidate]) -> None:
        self.descriptor = descriptor
        self._records = records

    async def search(self, analysis: QueryAnalysis, limit: int) -> list[SearchCandidate]:
        query_terms = set(analysis.normalized_query.casefold().split())
        region = analysis.constraints.get("region")
        price = analysis.constraints.get("price")
        matches = []
        for source in self._records:
            candidate = deepcopy(source)
            searchable = f"{candidate.title} {candidate.snippet} {candidate.content or ''}".casefold()
            candidate.lexical_score = sum(term.rstrip("을를은는이가의와과") in searchable for term in query_terms) / max(len(query_terms), 1)
            if region and candidate.structured_fields.get("region") not in (None, region):
                continue
            if price and candidate.structured_fields.get("price") != price:
                continue
            matches.append(candidate)
        return sorted(matches, key=lambda item: item.lexical_score, reverse=True)[:limit]


def _candidate(id: str, provider_id: str, title: str, snippet: str, url: str, fields: dict, *, source_type: SourceType = SourceType.PUBLIC_API, authority: float = 0.9) -> SearchCandidate:
    return SearchCandidate(id=id, source_type=source_type, provider_id=provider_id, title=title, snippet=snippet, content=snippet, structured_fields={**fields, "demo_data": True}, url=url, authority_score=authority, freshness_score=0.8, demo_data=True)


def default_mock_providers() -> list[InMemoryProvider]:
    return [
        InMemoryProvider(
            ProviderDescriptor("mock_general", ProviderType.INTERNAL_INDEX, frozenset({"general", "public_administration"}), frozenset({"keyword_search"}), 0.7),
            [_candidate("general:1", "mock_general", "정부24 이용 안내", "공공 행정서비스 이용 안내 문서", "https://example.invalid/general/1", {}, source_type=SourceType.INTERNAL)],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_weather", ProviderType.PUBLIC_API, frozenset({"weather", "air_quality"}), frozenset({"structured_lookup", "location_filter", "date_filter", "current_data"}), 0.95),
            [_candidate("weather:seoul", "mock_weather", "서울특별시 오늘 날씨", "맑음, 최고 28도", "https://example.invalid/weather/seoul", {"region": "서울특별시", "condition": "맑음", "temperature_max_c": 28, "observed_date": "2026-07-13"})],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_culture", ProviderType.PUBLIC_API, frozenset({"culture", "library"}), frozenset({"keyword_search", "structured_lookup", "location_filter", "date_filter"}), 0.9),
            [
                _candidate("culture:1", "mock_culture", "서울시립미술관", "무료로 관람할 수 있는 공공 미술관", "https://example.invalid/culture/1", {"region": "서울특별시", "price": "free", "opening_hours": "10:00-20:00"}),
                _candidate("library:busan", "mock_culture", "부산시민도서관", "부산의 공공도서관", "https://example.invalid/library/busan", {"region": "부산광역시", "opening_hours": "09:00-18:00"}),
            ],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_statistics", ProviderType.PUBLIC_API, frozenset({"statistics"}), frozenset({"structured_lookup", "keyword_search", "date_filter", "current_data"}), 0.96),
            [_candidate("statistics:population", "mock_statistics", "대한민국 인구 통계", "2025년 주민등록 인구 통계", "https://example.invalid/statistics/population", {"region": "대한민국", "year": 2025, "metric": "population", "value": 51200000})],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_welfare", ProviderType.PUBLIC_API, frozenset({"welfare_policy", "employment_support", "business_support"}), frozenset({"keyword_search", "structured_lookup", "location_filter", "date_filter", "constraint_filter", "current_data"}), 0.97),
            [
                _candidate("welfare:youth", "mock_welfare", "서울 청년 구직 지원", "서울 거주 만 19세 이상 34세 이하 미취업 청년에게 구직활동비를 지원한다.", "https://example.invalid/welfare/youth", {"region": "서울특별시", "eligible_age": {"min": 19, "max": 34}, "employment_status": "unemployed", "benefit": "월 50만원", "application_period": {"start": "2026-07-01", "end": "2026-07-31"}, "administering_organization": "서울특별시", "related_law": "청년기본법"}),
                _candidate("welfare:startup", "mock_welfare", "경기 소상공인 초기창업 지원", "경기도에서 창업 3년 이하인 소상공인을 지원한다.", "https://example.invalid/welfare/startup", {"region": "경기도", "business_age_years_max": 3, "business_type": "small_business", "benefit": "사업화 자금", "administering_organization": "경기도경제과학진흥원"}),
            ],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_web_search", ProviderType.WEB_SEARCH, frozenset({"general", "law", "culture", "tourism"}), frozenset({"keyword_search", "current_data"}), 0.65, 250),
            [_candidate("web:1", "mock_web_search", "일반 웹검색 API 결과", "검색 API가 반환한 일반 웹 문서이며 크롤링 데이터가 아니다.", "https://example.invalid/web/1", {}, source_type=SourceType.WEB, authority=0.6)],
        ),
        InMemoryProvider(
            ProviderDescriptor("mock_knowledge_graph", ProviderType.KNOWLEDGE_GRAPH, frozenset({"welfare_policy", "law", "public_administration"}), frozenset({"entity_search", "relation_search"}), 0.9),
            [_candidate("kg:youth-law", "mock_knowledge_graph", "서울 청년 구직 지원—청년기본법", "사업과 관련 법령의 관계", "https://example.invalid/kg/youth-law", {"subject": "서울 청년 구직 지원", "predicate": "RELATED_TO", "object": "청년기본법"}, source_type=SourceType.KNOWLEDGE_GRAPH)],
        ),
    ]


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        if provider.descriptor.provider_id in self._providers:
            raise ValueError("provider_id must be unique")
        self._providers[provider.descriptor.provider_id] = provider

    def descriptors(self) -> list[ProviderDescriptor]:
        return [provider.descriptor for provider in self._providers.values()]

    def get(self, provider_id: str) -> SearchProvider:
        return self._providers[provider_id]
