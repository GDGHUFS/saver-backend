import math
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.search.engine.config import RetrievalWeights
from src.search.engine.schema import QueryAnalysis, SearchCandidate


def minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def token_similarity(query: str, text: str) -> float:
    left = set(re.findall(r"[\w가-힣]+", query.casefold()))
    right = set(re.findall(r"[\w가-힣]+", text.casefold()))
    return len(left & right) / len(left | right) if left and right else 0.0


TRACKING_PARAMETERS = {"fbclid", "gclid", "igshid", "ref", "source"}


def web_authority_score(url: str, default: float = 0.5) -> float:
    """Give official and institutional hosts a small, domain-neutral boost."""
    host = (urlsplit(url).hostname or "").casefold()
    if host.endswith((".go.kr", ".gov", ".gov.kr")):
        return max(default, 0.95)
    if host.endswith((".or.kr", ".ac.kr", ".edu")):
        return max(default, 0.8)
    return default


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").casefold()
        if not host:
            return url.strip()
        port = f":{parts.port}" if parts.port and not (
            parts.scheme == "http" and parts.port == 80) and not (
            parts.scheme == "https" and parts.port == 443) else ""
        query = urlencode(sorted(
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.casefold().startswith("utm_") and k.casefold() not in TRACKING_PARAMETERS))
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parts.scheme.casefold(), host + port, path, query, ""))
    except ValueError:
        return url.strip()


def reciprocal_rank_fusion(
    batches: list[list[SearchCandidate]], *, k: int = 60
) -> list[SearchCandidate]:
    by_url: dict[str, SearchCandidate] = {}
    scores: Counter[str] = Counter()
    for batch in batches:
        seen: set[str] = set()
        for rank, candidate in enumerate(batch, 1):
            key = normalize_url(candidate.url) if candidate.url else candidate.id
            if key in seen:
                continue
            seen.add(key)
            scores[key] += 1.0 / (k + rank)
            current = by_url.get(key)
            if current is None or (
                candidate.authority_score, candidate.freshness_score
            ) > (current.authority_score, current.freshness_score):
                by_url[key] = candidate
                candidate.url = key if candidate.url else candidate.url
    for key, candidate in by_url.items():
        candidate.rrf_score = scores[key]
    return sorted(by_url.values(), key=lambda item: (-item.rrf_score, item.id))


def apply_lightweight_lexical(query: str, candidates: list[SearchCandidate]) -> None:
    terms = list(dict.fromkeys(re.findall(r"[\w가-힣]+", query.casefold())))
    phrase = " ".join(terms)
    for item in candidates:
        title, snippet = item.title.casefold(), item.snippet.casefold()
        title_hits = sum(term in title for term in terms)
        snippet_hits = sum(term in snippet for term in terms)
        coverage = len({term for term in terms if term in title or term in snippet}) / max(len(terms), 1)
        exact = 1.0 if query.casefold().strip() in title else 0.0
        phrase_match = 1.0 if phrase and (phrase in title or phrase in snippet) else 0.0
        item.lexical_score = (
            0.30 * exact + 0.20 * phrase_match
            + 0.25 * title_hits / max(len(terms), 1)
            + 0.10 * snippet_hits / max(len(terms), 1)
            + 0.15 * coverage
        )


def rerank(analysis: QueryAnalysis, candidates: list[SearchCandidate]) -> list[SearchCandidate]:
    query = analysis.normalized_query.casefold()
    entities = {entity.text.casefold() for entity in analysis.entities}
    for item in candidates:
        title = item.title.casefold()
        searchable = (
            f"{title} {item.snippet.casefold()} "
            f"{' '.join(map(str, item.structured_fields.values())).casefold()}"
        )
        exact_title = 1.0 if title == query else 0.6 if query in title or title in query else 0.0
        entity_match = 1.0 if entities and any(entity in searchable for entity in entities) else 0.0
        item.score += 0.12 * exact_title + 0.08 * entity_match
    return sorted(candidates, key=lambda item: (-item.score, -item.authority_score, item.id))


class HybridRanker:
    def __init__(self, weights: RetrievalWeights) -> None:
        self.weights = weights

    def rank(
        self, analysis: QueryAnalysis, candidates: list[SearchCandidate], *,
        use_dense: bool = False, use_semantic_heuristic: bool | None = None,
    ) -> list[SearchCandidate]:
        if use_semantic_heuristic is not None:
            use_dense = use_semantic_heuristic
        apply_lightweight_lexical(analysis.normalized_query, candidates)
        lexical = minmax([item.lexical_score for item in candidates])
        rrf = minmax([item.rrf_score for item in candidates])
        entities = {entity.canonical_value for entity in analysis.entities}
        for index, item in enumerate(candidates):
            item.dense_score = max(
                item.dense_score,
                token_similarity(analysis.normalized_query, f"{item.title} {item.snippet}"),
                float(item.structured_fields.get("concept_match_score", 0.0)),
            ) if use_dense else 0.0
            item.semantic_heuristic_score = item.dense_score
            entity_score = 1.0 if entities & set(map(str, item.structured_fields.values())) else 0.0
            metadata_score = self._constraint_match(analysis, item)
            coverage = len(set(analysis.requested_fields) & set(item.structured_fields)) / max(
                len(analysis.requested_fields), 1)
            lexical_weight = self.weights.lexical + (0.0 if use_dense else self.weights.dense)
            item.score = (
                0.20 * rrf[index]
                + lexical_weight * lexical[index]
                + (self.weights.dense * item.dense_score if use_dense else 0.0)
                + self.weights.entity * entity_score
                + self.weights.metadata * metadata_score
                + self.weights.field_coverage * coverage
                + self.weights.authority * item.authority_score
                + self.weights.freshness * item.freshness_score
            )
        return sorted(candidates, key=lambda item: (-item.score, -item.authority_score, item.id))

    @staticmethod
    def _constraint_match(analysis: QueryAnalysis, candidate: SearchCandidate) -> float:
        comparable = [
            (key, value) for key, value in analysis.constraints.items()
            if key in candidate.structured_fields
        ]
        return sum(
            candidate.structured_fields[key] == value for key, value in comparable
        ) / len(comparable) if comparable else 0.5


def diverse_select(
    candidates: list[SearchCandidate], limit: int, diversity_weight: float = 0.25
) -> list[SearchCandidate]:
    selected: list[SearchCandidate] = []
    provider_counts: Counter[str] = Counter()
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda item: item.score - diversity_weight * provider_counts[item.provider_id],
        )
        selected.append(best)
        provider_counts[best.provider_id] += 1
        remaining.remove(best)
    return selected
