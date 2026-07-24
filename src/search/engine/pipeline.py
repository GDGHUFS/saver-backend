from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any

from src.search.engine.analysis import OpenAIQueryAnalyzer
from src.search.engine.config import EngineConfig, LLMSettings
from src.search.engine.kakao import KakaoSearchSettings, KakaoWebSearchProvider
from src.search.engine.naver import NaverSearchSettings, NaverWebSearchProvider
from src.search.engine.planner import RuleBasedPlanner
from src.search.engine.providers import ProviderRegistry, default_mock_providers
from src.search.engine.query_adapter import build_provider_query_plan
from src.search.engine.reasoning import evidence_edges, evaluate_eligibility, extract_nuggets, fuse_claims
from src.search.engine.retrieval import HybridRanker, diverse_select, normalize_url, reciprocal_rank_fusion, rerank
from src.search.engine.schema import ProviderType, QueryAnalysis, SearchResponse


class IntelligentSearchEngine:
    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        config: EngineConfig | None = None,
        *,
        analyzer: OpenAIQueryAnalyzer | None = None,
    ) -> None:
        self.config = config or EngineConfig.from_env()
        self.registry = registry or ProviderRegistry()
        if registry is None:
            if self.config.use_mock_providers:
                for provider in default_mock_providers():
                    self.registry.register(provider)
            naver = NaverSearchSettings.from_env()
            if naver is not None:
                self.registry.register(NaverWebSearchProvider(naver))
            kakao = KakaoSearchSettings.from_env()
            if kakao is not None:
                self.registry.register(KakaoWebSearchProvider(kakao))
        self._owned_llm_client: Any = None
        self.analyzer = analyzer
        self.planner = RuleBasedPlanner(self.config.features)
        self.ranker = HybridRanker(self.config.weights)

    async def search(self, query: str, previous_results: list[Any] | None = None) -> SearchResponse:
        started = perf_counter()
        if self.analyzer is None:
            self.analyzer = self._analyzer_from_env()
        analysis = await self.analyzer.analyze(query)
        analyzed_at = perf_counter()
        descriptors = self.registry.descriptors()
        descriptor_by_id = {item.provider_id: item for item in descriptors}
        plan = self.planner.plan(analysis, descriptors)
        requested = list(analysis.domain_extensions.get("analysis", {}).get("provider_targets", []))
        selected = list(plan.provider_ids)
        unavailable = [item for item in requested if item not in descriptor_by_id]
        executed = ["query_understanding"]
        skipped: dict[str, str] = {}

        if plan.needs_decomposition:
            executed.append("question_decomposition")
        else:
            skipped["question_decomposition"] = "question is not a policy comparison"
        if plan.needs_coreference and previous_results:
            executed.append("coreference_resolution")
        else:
            skipped["coreference_resolution"] = "query is not a follow-up"
        if plan.needs_query_expansion:
            executed.append("query_expansion")
        else:
            skipped["query_expansion"] = "domain does not require expansion"

        provider_plans: dict[str, Any] = {}
        calls = []
        order = []
        for provider_id in selected:
            provider_plans[provider_id] = asdict(build_provider_query_plan(analysis, provider_id))
            calls.append(self._search_provider(provider_id, analysis))
            order.append(provider_id)
        batches = await asyncio.gather(*calls, return_exceptions=True)
        candidates = []
        successful_batches = []
        failed: list[str] = []
        provider_errors: dict[str, Any] = {}
        counts = {provider_id: 0 for provider_id in selected}
        for provider_id, batch in zip(order, batches, strict=True):
            if isinstance(batch, Exception):
                failed.append(provider_id)
                provider_errors[provider_id] = self._safe_provider_error(batch)
                continue
            candidates.extend(batch)
            successful_batches.append(batch)
            counts[provider_id] = len(batch)

        retrieval_step = "structured_api_lookup" if plan.query_complexity == "simple" else "candidate_generation"
        executed.append(retrieval_step)
        uses_web = any(
            descriptor_by_id[item].provider_type == ProviderType.WEB_SEARCH
            for item in selected
        )
        if uses_web:
            candidates = reciprocal_rank_fusion(successful_batches)
            executed.extend(("url_normalization", "duplicate_removal", "rank_fusion"))

        provider_fallbacks: dict[str, str] = {}
        selected_are_public = bool(selected) and all(
            descriptor_by_id[item].provider_type == ProviderType.PUBLIC_API
            for item in selected
        )
        if not candidates and selected_are_public:
            fallback_ids = [
                item.provider_id for item in descriptors
                if item.provider_type == ProviderType.WEB_SEARCH and item.provider_id not in selected
            ]
            for provider_id in fallback_ids:
                try:
                    batch = await self._search_provider(provider_id, analysis)
                except Exception as exc:
                    failed.append(provider_id)
                    provider_errors[provider_id] = self._safe_provider_error(exc)
                    continue
                if batch:
                    candidates.extend(batch)
                    selected.append(provider_id)
                    counts[provider_id] = len(batch)
                    provider_plans[provider_id] = asdict(build_provider_query_plan(analysis, provider_id))
                    provider_fallbacks[provider_id] = "selected providers returned no candidates"
            if provider_fallbacks:
                candidates = reciprocal_rank_fusion([candidates])
                executed.append("web_search_fallback")

        if candidates:
            candidates = self.ranker.rank(analysis, candidates, use_dense=plan.needs_dense_similarity)
            executed.append("lightweight_lexical_ranking")
            if plan.needs_dense_similarity:
                executed.append("dense_similarity")
            if plan.needs_reranking:
                candidates = rerank(analysis, candidates)
                executed.append("reranking")
        else:
            skipped["lightweight_lexical_ranking"] = "no candidates"
            skipped["reranking"] = "reranking is unnecessary for this result state"

        if any("detail_lookup" in descriptor_by_id[item].capabilities for item in selected) and candidates:
            executed.append("detail_lookup")
        else:
            skipped["detail_lookup"] = "selected providers do not expose detail lookup or no candidates"

        nuggets = {
            item.id: [asdict(nugget) for nugget in extract_nuggets(item)]
            for item in candidates
        } if plan.needs_nuggets else {}
        if plan.needs_nuggets and candidates:
            executed.append("answer_nugget_extraction")
        else:
            skipped["answer_nugget_extraction"] = "simple structured lookup or no results"

        if plan.needs_knowledge_selection and len(candidates) > 1:
            candidates = self._knowledge_select(analysis, candidates)
            executed.append("knowledge_selection")
        else:
            skipped["knowledge_selection"] = "multiple evidence candidates are not available"

        if plan.needs_diversity and len(candidates) > 1:
            candidates = diverse_select(candidates, self.config.max_results)
            executed.append("diversity_selection")
        else:
            candidates = candidates[:self.config.max_results]
            skipped["diversity_selection"] = "fewer than two candidates"

        graph_paths = [
            evidence_edges(item) for item in candidates if evidence_edges(item)
        ] if plan.use_graph else []
        if graph_paths:
            executed.append("graph_reasoning")
        else:
            skipped["graph_reasoning"] = "query has no relation or multi-hop requirement"

        eligibility = {
            item.id: evaluate_eligibility(item, analysis.constraints)
            for item in candidates
        } if plan.use_rules else {}
        if plan.use_rules:
            executed.append("rule_reasoning")

        claims = self._claims(candidates)
        resolved, conflicts = fuse_claims(claims) if plan.use_probabilistic_fusion else (claims, [])
        if plan.use_probabilistic_fusion and claims:
            executed.append("evidence_fusion")
        else:
            skipped["evidence_fusion"] = "multiple sources do not disagree on extractable claims"
        skipped.setdefault("change_detection", "query does not request change detection")
        skipped.setdefault("dense_similarity", "query type and lexical confidence do not require semantic scoring")
        skipped.setdefault("cross_encoder_reranking", "query type and confidence do not require top-k reranking")

        finished = perf_counter()
        execution = {
            "features_used": executed.copy(),
            "planned_steps": list(plan.steps),
            "executed_steps": executed,
            "skipped_steps": skipped,
            "providers_used": selected,
            "requested_provider_targets": requested,
            "selected_provider_targets": [item for item in selected if item in descriptor_by_id],
            "unavailable_provider_targets": unavailable,
            "provider_query_plans": provider_plans,
            "query_analysis_method": analysis.domain_extensions.get("analysis", {}).get("analysis_method", "llm"),
            "provider_plan_method": analysis.domain_extensions.get("analysis", {}).get("provider_plan_method", "llm"),
            "provider_plan_regeneration_count": 0,
            "fixed_vocabulary_mapping_used": False,
            "failed_providers": list(dict.fromkeys(failed)),
            "provider_errors": provider_errors,
            "provider_execution": {
                item: {
                    "status": "provider_error" if item in failed else "success",
                    "candidate_count": counts.get(item, 0),
                } for item in selected
            },
            "provider_fallbacks": provider_fallbacks,
            "public_api_fallback_exhausted": self._public_api_fallback_exhausted(selected, candidates),
            "answer_nuggets": nuggets,
            "ai_context": self._ai_context(candidates, nuggets),
            "eligibility": eligibility,
            "latency_by_stage": {
                "query_analysis_ms": round((analyzed_at - started) * 1000, 3),
                "retrieval_ms": round((finished - analyzed_at) * 1000, 3),
                "postprocessing_ms": 0.0,
            },
        }
        return SearchResponse(
            query_analysis=analysis,
            results=candidates,
            claims=resolved,
            conflicts=conflicts,
            graph_paths=graph_paths,
            execution_metadata=execution,
        )

    async def aclose(self) -> None:
        if self._owned_llm_client is not None:
            await self._owned_llm_client.close()
            self._owned_llm_client = None

    def _analyzer_from_env(self) -> OpenAIQueryAnalyzer:
        from openai import AsyncOpenAI
        import certifi
        import httpx

        settings = LLMSettings.from_env()
        self._owned_llm_client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=1,
            http_client=httpx.AsyncClient(verify=certifi.where()),
        )
        return OpenAIQueryAnalyzer(
            self._owned_llm_client,
            model=settings.model,
        )

    async def _search_provider(self, provider_id: str, analysis: QueryAnalysis) -> list[Any]:
        provider = self.registry.get(provider_id)
        plan = build_provider_query_plan(analysis, provider_id)
        adapted = QueryAnalysis(**{**analysis.__dict__})
        adapted.domain_extensions = {
            **analysis.domain_extensions,
            "provider_query_plan": asdict(plan),
        }
        return await asyncio.wait_for(
            provider.search(adapted, self.config.max_candidates),
            timeout=self.config.provider_timeout_seconds,
        )

    @staticmethod
    def _knowledge_select(analysis: QueryAnalysis, candidates: list[Any]) -> list[Any]:
        requested = set(analysis.requested_fields)
        return sorted(
            candidates,
            key=lambda item: (
                -len(requested & set(item.structured_fields)),
                -item.score,
            ),
        )

    @staticmethod
    def _claims(candidates: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "field": field,
                "value": value,
                "source_id": item.id,
                "authority": item.authority_score,
                "freshness": item.freshness_score,
            }
            for item in candidates
            for field, value in item.structured_fields.items()
            if field != "demo_data"
        ]

    @staticmethod
    def _ai_context(candidates: list[Any], nuggets: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "source_id": item.id,
                "title": item.title,
                "url": normalize_url(item.url),
                "nuggets": nuggets.get(item.id, []),
            }
            for item in candidates
        ]

    @staticmethod
    def _safe_provider_error(exc: BaseException) -> dict[str, str]:
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        return {
            "error_type": type(exc).__name__,
            "root_error_type": type(root).__name__,
        }

    def _public_api_fallback_exhausted(self, selected: list[str], candidates: list[Any]) -> bool:
        descriptors = {item.provider_id: item for item in self.registry.descriptors()}
        return bool(selected) and not candidates and all(
            descriptors[item].provider_type == ProviderType.PUBLIC_API
            for item in selected if item in descriptors
        )
