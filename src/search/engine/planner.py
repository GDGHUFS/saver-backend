from src.search.engine.config import SearchFeatures
from src.search.engine.schema import ExecutionPlan, ProviderDescriptor, ProviderType, QueryAnalysis


class RuleBasedPlanner:
    def __init__(self, features: SearchFeatures) -> None:
        self.features = features

    def plan(self, analysis: QueryAnalysis, providers: list[ProviderDescriptor]) -> ExecutionPlan:
        flags = analysis.planner_flags
        simple = flags.get("is_simple_lookup", False)
        requested_steps = set(analysis.domain_extensions.get("analysis", {}).get("required_steps", []))
        requested_providers = analysis.domain_extensions.get("analysis", {}).get("provider_targets", [])
        use_graph = self.features.graph_retrieval and flags.get("needs_graph", False)
        ranked = sorted(providers, key=lambda provider: (-provider.authority, provider.expected_latency_ms))
        by_id = {provider.provider_id: provider for provider in providers}
        selected = [by_id[provider_id] for provider_id in requested_providers if provider_id in by_id]
        if not selected and not requested_providers:
            domains = {domain.name for domain in analysis.domains}
            matching = [p for p in ranked if not domains or domains & set(p.domains)]
            dedicated = [p for p in matching if p.provider_type in {
                ProviderType.PUBLIC_API, ProviderType.INTERNAL_INDEX}]
            selected = dedicated or [p for p in ranked if p.provider_type == ProviderType.WEB_SEARCH]
        if use_graph:
            selected.extend(p for p in ranked if p.provider_type == ProviderType.KNOWLEDGE_GRAPH and p not in selected)
        complex_query = not simple
        use_rules = self.features.rule_reasoning and "rule_reasoning" in requested_steps
        use_fusion = self.features.probabilistic_fusion and flags.get("needs_conflict_resolution", False)
        needs_decomposition = (
            complex_query and self.features.question_decomposition and analysis.query_type == "comparison")
        has_web_search = any(p.provider_type == ProviderType.WEB_SEARCH for p in selected)
        needs_ranking = bool(selected)
        needs_dense = self.features.dense_retrieval and (
            "hybrid_ranking" in requested_steps
            or analysis.query_type in {"semantic", "exploratory", "recommendation"}
            or flags.get("low_lexical_confidence", False)
        )
        needs_reranking = "reranking" in requested_steps
        needs_nuggets = "answer_nugget_extraction" in requested_steps
        needs_diversity = self.features.submodular_selection and "diversity_selection" in requested_steps
        needs_knowledge = "knowledge_selection" in requested_steps
        needs_expansion = "query_expansion" in requested_steps
        steps = ["query_understanding"]
        if needs_expansion:
            steps.append("query_expansion")
        if needs_decomposition:
            steps.append("question_decomposition")
        steps.append("candidate_generation" if not simple else "structured_api_lookup")
        if has_web_search:
            steps.extend(("url_normalization", "duplicate_removal", "rank_fusion"))
        if needs_ranking:
            steps.append("lightweight_lexical_ranking")
        if needs_dense:
            steps.append("dense_similarity")
        if needs_reranking:
            steps.append("reranking")
        if needs_nuggets:
            steps.append("answer_nugget_extraction")
        if needs_knowledge:
            steps.append("knowledge_selection")
        if needs_diversity:
            steps.append("diversity_selection")
        if use_graph:
            steps.append("graph_reasoning")
        if use_rules:
            steps.append("rule_reasoning")
        if use_fusion:
            steps.append("evidence_fusion")
        if flags.get("is_follow_up"):
            steps.insert(1, "coreference_resolution")
        if flags.get("needs_change_detection"):
            steps.append("change_detection")
        steps.append("answer_generation")
        return ExecutionPlan(
            query_complexity="complex" if complex_query else "simple",
            query_type=analysis.query_type, steps=tuple(steps),
            provider_ids=tuple(p.provider_id for p in selected),
            use_graph=use_graph, use_rules=use_rules, use_probabilistic_fusion=use_fusion,
            needs_query_expansion=needs_expansion, needs_decomposition=needs_decomposition,
            needs_hybrid_ranking=needs_ranking, needs_reranking=needs_reranking,
            needs_nuggets=needs_nuggets, needs_knowledge_selection=needs_knowledge,
            needs_diversity=needs_diversity, needs_coreference=flags.get("is_follow_up", False),
            needs_change_detection=flags.get("needs_change_detection", False),
            needs_dense_similarity=needs_dense,
        )
