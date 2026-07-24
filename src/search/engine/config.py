import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchFeatures:
    query_understanding: bool = True
    similar_case_retrieval: bool = True
    question_decomposition: bool = True
    lexical_retrieval: bool = True
    dense_retrieval: bool = True
    graph_retrieval: bool = True
    adaptive_knowledge_selection: bool = True
    submodular_selection: bool = True
    rule_reasoning: bool = True
    probabilistic_fusion: bool = True
    conversation_coreference: bool = True
    change_anomaly_detection: bool = True


@dataclass(frozen=True)
class RetrievalWeights:
    lexical: float = 0.25
    dense: float = 0.20
    entity: float = 0.15
    metadata: float = 0.10
    field_coverage: float = 0.10
    authority: float = 0.10
    freshness: float = 0.10

    def __post_init__(self) -> None:
        if abs(sum(vars(self).values()) - 1.0) > 1e-9:
            raise ValueError("retrieval weights must sum to 1")


@dataclass(frozen=True)
class EngineConfig:
    features: SearchFeatures = field(default_factory=SearchFeatures)
    weights: RetrievalWeights = field(default_factory=RetrievalWeights)
    max_candidates: int = 20
    max_results: int = 10
    provider_timeout_seconds: float = 3.0
    use_mock_providers: bool = True

    def __post_init__(self) -> None:
        if self.max_candidates <= 0 or self.max_results <= 0:
            raise ValueError("search result limits must be greater than zero")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be greater than zero")

    @classmethod
    def from_env(cls, *, use_mock_providers: bool) -> "EngineConfig":
        return cls(
            max_candidates=int(os.getenv("SEARCH_MAX_CANDIDATES", "20")),
            max_results=int(os.getenv("SEARCH_MAX_RESULTS", "10")),
            provider_timeout_seconds=float(os.getenv("SEARCH_PROVIDER_TIMEOUT", "3")),
            use_mock_providers=use_mock_providers,
        )
