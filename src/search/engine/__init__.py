"""Domain-neutral intelligent search pipeline."""

__all__ = ["IntelligentSearchEngine"]


def __getattr__(name: str):
    if name == "IntelligentSearchEngine":
        from src.search.engine.pipeline import IntelligentSearchEngine

        return IntelligentSearchEngine
    raise AttributeError(name)
