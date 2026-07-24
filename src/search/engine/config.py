import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def boolean_from_env(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().casefold()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "LLMSettings":
        api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            raise ValueError("LLM_API_KEY is required")

        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "LLM_BASE_URL must be an absolute HTTP(S) URL without credentials, "
                "query, or fragment"
            )
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("remote LLM_BASE_URL must use HTTPS")

        model = os.getenv("LLM_MODEL", "gpt-4.1-mini").strip()
        if not model:
            raise ValueError("LLM_MODEL is required")
        timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        if timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be greater than zero")
        return cls(api_key, base_url, model, timeout_seconds)


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
    use_mock_providers: bool = False

    def __post_init__(self) -> None:
        if self.max_candidates <= 0 or self.max_results <= 0:
            raise ValueError("search result limits must be greater than zero")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be greater than zero")

    @classmethod
    def from_env(cls) -> "EngineConfig":
        return cls(
            max_candidates=int(os.getenv("SEARCH_MAX_CANDIDATES", "20")),
            max_results=int(os.getenv("SEARCH_MAX_RESULTS", "10")),
            provider_timeout_seconds=float(os.getenv("SEARCH_PROVIDER_TIMEOUT", "3")),
            use_mock_providers=boolean_from_env(
                "SEARCH_USE_MOCK_PROVIDERS",
                default=False,
            ),
        )
