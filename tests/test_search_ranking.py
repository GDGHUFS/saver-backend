import unittest

from src.search.engine.config import RetrievalWeights
from src.search.engine.retrieval import HybridRanker, normalize_url, reciprocal_rank_fusion
from src.search.engine.schema import QueryAnalysis, SearchCandidate, SourceType


def candidate(identifier, provider, url, title="GPT-5 가격", snippet="API 가격 안내"):
    return SearchCandidate(identifier, SourceType.WEB, provider, title, snippet, url=url)


class UrlAndFusionTest(unittest.TestCase):
    def test_normalizes_tracking_and_fragment(self):
        self.assertEqual(
            normalize_url("HTTPS://Example.COM:443/docs/?utm_source=x&a=1#price"),
            "https://example.com/docs?a=1",
        )

    def test_rrf_deduplicates_same_url_across_providers(self):
        fused = reciprocal_rank_fusion([
            [candidate("n1", "naver", "https://example.com/doc?utm_source=n")],
            [candidate("k1", "kakao", "https://EXAMPLE.com/doc#top")],
        ])
        self.assertEqual(len(fused), 1)
        self.assertAlmostEqual(fused[0].rrf_score, 2 / 61)

    def test_lightweight_lexical_ranking(self):
        parsed = QueryAnalysis("GPT-5 가격", "GPT-5 가격", [], "factual", "direct_answer")
        exact = candidate("a", "naver", "https://a.test", title="GPT-5 가격")
        unrelated = candidate("b", "kakao", "https://b.test", title="다른 문서", snippet="관련 없음")
        ranked = HybridRanker(RetrievalWeights()).rank(parsed, [unrelated, exact])
        self.assertEqual(ranked[0].id, "a")


if __name__ == "__main__":
    unittest.main()
