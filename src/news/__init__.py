from src.news.model import NewsItemResponse
from src.news.routes import get_latest_news, router as news_router


__all__ = ["NewsItemResponse", "get_latest_news", "news_router"]
