from dataclasses import dataclass, field
from typing import Any

from src.search.engine.schema import SearchCandidate


@dataclass(frozen=True)
class FieldSchema:
    name: str
    type: str
    questions: tuple[str, ...]


class ExtractionSchemaRegistry:
    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, FieldSchema]] = {}

    def register(self, domain: str, fields: list[FieldSchema]) -> None:
        self._schemas[domain] = {item.name: item for item in fields}

    def requested(self, domain: str, names: list[str]) -> list[FieldSchema]:
        schema = self._schemas.get(domain, {})
        return [schema[name] for name in names if name in schema]


def default_extraction_schemas() -> ExtractionSchemaRegistry:
    registry = ExtractionSchemaRegistry()
    registry.register("culture", [FieldSchema("opening_hours", "temporal", ("이 시설의 운영시간은 언제인가?",)), FieldSchema("price", "monetary", ("이 시설의 입장료는 얼마인가?",))])
    registry.register("welfare_policy", [
        FieldSchema("administering_organization", "organization", ("이 정책의 주관 기관은 어디인가?",)),
        FieldSchema("eligible_age", "range", ("지원 가능한 연령은 어떻게 되는가?",)),
        FieldSchema("application_period", "temporal", ("신청 기간은 언제인가?",)),
        FieldSchema("benefit", "monetary", ("지원 내용은 무엇인가?",)),
        FieldSchema("related_law", "relation", ("관련 법령은 무엇인가?",)),
    ])
    return registry


def extract_fields(candidate: SearchCandidate, schemas: list[FieldSchema]) -> list[dict[str, Any]]:
    results = []
    for schema in schemas:
        if schema.name not in candidate.structured_fields:
            continue
        value = candidate.structured_fields[schema.name]
        results.append({"field": schema.name, "value": value, "evidence_text": candidate.snippet, "source_document_id": candidate.id, "confidence": 1.0, "extraction_method": "structured_field"})
    return results


@dataclass(frozen=True)
class SimilarCase:
    case_id: str
    query: str
    query_type: str
    requested_fields: frozenset[str] = field(default_factory=frozenset)
    domains: frozenset[str] = field(default_factory=frozenset)
    provider_ids: tuple[str, ...] = ()
    quality_score: float = 0.0


class SimilarCaseStore:
    def __init__(self, cases: list[SimilarCase]) -> None:
        self.cases = cases

    def search(self, *, query_type: str, requested_fields: set[str], domains: set[str], limit: int = 1) -> list[SimilarCase]:
        def score(case: SimilarCase) -> float:
            field_union = requested_fields | set(case.requested_fields)
            field_score = len(requested_fields & set(case.requested_fields)) / len(field_union) if field_union else 1.0
            return 0.4 * (case.query_type == query_type) + 0.3 * bool(domains & set(case.domains)) + 0.2 * field_score + 0.1 * case.quality_score
        return sorted(self.cases, key=lambda case: (-score(case), case.case_id))[:limit]
