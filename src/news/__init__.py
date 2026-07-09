from src.news.model import NewsItemResponse, NewsPageResponse
from src.news.routes import get_latest_news, get_latest_news_page, router as news_router


__all__ = ["NewsItemResponse", "NewsPageResponse", "get_latest_news", "get_latest_news_page", "news_router"]
