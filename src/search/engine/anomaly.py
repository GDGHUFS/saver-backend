import random
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphAnomaly:
    anomaly_id: str
    node_ids: tuple[str, ...]
    raw_scan_score: float
    calibrated_pvalue: float
    dominant_changes: tuple[str, ...]


def calibrated_change_scan(changes: dict[str, int], baseline: dict[str, list[int]], *, permutations: int = 200, seed: int = 0) -> GraphAnomaly | None:
    if not changes:
        return None
    rng = random.Random(seed)
    standardized = {node: value / max(sum(baseline.get(node, [1])) / max(len(baseline.get(node, [1])), 1), 1) for node, value in changes.items()}
    nodes = tuple(sorted(standardized, key=standardized.get, reverse=True)[:30])
    observed = sum(standardized[node] for node in nodes) / len(nodes)
    pool = [value for values in baseline.values() for value in values] or [0]
    null_scores = [sum(rng.choice(pool) for _ in nodes) / len(nodes) for _ in range(max(permutations, 1))]
    pvalue = (1 + sum(score >= observed for score in null_scores)) / (len(null_scores) + 1)
    return GraphAnomaly(f"change:{seed}:{len(nodes)}", nodes, observed, pvalue, tuple(f"{node} 변경 {changes[node]}건" for node in nodes[:3]))
