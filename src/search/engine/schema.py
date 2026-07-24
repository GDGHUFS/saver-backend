from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class ProviderType(StrEnum):
    PUBLIC_API = "PublicAPIProvider"
    WEB_SEARCH = "WebSearchProvider"
    INTERNAL_INDEX = "InternalIndexProvider"
    KNOWLEDGE_GRAPH = "KnowledgeGraphProvider"


class SearchCapability(StrEnum):
    NATIVE_FULL_TEXT_SEARCH = "native_full_text_search"
    NATIVE_KEYWORD_SEARCH = "native_keyword_search"
    STRUCTURED_FILTER = "structured_filter"
    PAGINATED_LIST = "paginated_list"
    DETAIL_LOOKUP = "detail_lookup"
    INCREMENTAL_SYNC = "incremental_sync"
    LOCAL_INDEX_SUPPORTED = "local_index_supported"


class SourceType(StrEnum):
    WEB = "web"
    PUBLIC_API = "public_api"
    INTERNAL = "internal"
    KNOWLEDGE_GRAPH = "knowledge_graph"


@dataclass(frozen=True)
class DomainScore:
    name: str
    confidence: float


@dataclass(frozen=True)
class Entity:
    text: str
    canonical_value: str
    type: str
    confidence: float = 1.0


@dataclass
class QueryAnalysis:
    original_query: str
    normalized_query: str
    domains: list[DomainScore]
    query_type: str
    answer_type: str
    entities: list[Entity] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    requested_fields: list[str] = field(default_factory=list)
    temporal_scope: dict[str, Any] = field(default_factory=dict)
    ambiguities: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    domain_extensions: dict[str, dict[str, Any]] = field(default_factory=dict)
    planner_flags: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    provider_type: ProviderType
    domains: frozenset[str]
    capabilities: frozenset[str]
    authority: float = 0.5
    expected_latency_ms: int = 100
    search_capabilities: frozenset[SearchCapability] = field(default_factory=frozenset)


@dataclass
class SearchCandidate:
    id: str
    source_type: SourceType
    provider_id: str
    title: str
    snippet: str
    content: str | None = None
    structured_fields: dict[str, Any] = field(default_factory=dict)
    url: str = ""
    authority_score: float = 0.0
    freshness_score: float = 0.0
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lexical_score: float = 0.0
    rrf_score: float = 0.0
    dense_score: float = 0.0
    semantic_heuristic_score: float = 0.0
    graph_score: float = 0.0
    score: float = 0.0
    demo_data: bool = False


class SearchProvider(Protocol):
    descriptor: ProviderDescriptor

    async def search(self, analysis: QueryAnalysis, limit: int) -> list[SearchCandidate]: ...


@dataclass(frozen=True)
class ExecutionPlan:
    query_complexity: str
    query_type: str
    steps: tuple[str, ...]
    provider_ids: tuple[str, ...]
    use_graph: bool = False
    use_rules: bool = False
    use_probabilistic_fusion: bool = False
    needs_query_expansion: bool = False
    needs_decomposition: bool = False
    needs_hybrid_ranking: bool = False
    needs_reranking: bool = False
    needs_nuggets: bool = False
    needs_knowledge_selection: bool = False
    needs_diversity: bool = False
    needs_coreference: bool = False
    needs_change_detection: bool = False
    needs_dense_similarity: bool = False


@dataclass(frozen=True)
class AnswerNugget:
    id: str
    document_id: str
    text: str
    nugget_type: str
    entities: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    source_span: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class EvidenceEdge:
    subject: str
    predicate: str
    object: str
    source: str


@dataclass
class SearchResponse:
    query_analysis: QueryAnalysis
    results: list[SearchCandidate]
    claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    graph_paths: list[list[EvidenceEdge]] = field(default_factory=list)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
