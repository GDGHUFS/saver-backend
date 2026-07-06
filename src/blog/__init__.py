from fastapi import APIRouter

from src.blog.get import get_latest_blog, get_user_blogs, read_blog, router as read_router
from src.blog.model import BlogResponse, BlogWriteRequest, SimpleBlogResponse
from src.blog.modify import delete_blog, router as modify_router, update_blog, write_blog


# 하위 모듈이 각자 라우트를 정의하고, 패키지의 공개 라우터가 이를 한 번만 결합한다.
# app.py는 이 라우터만 등록하면 모든 블로그 엔드포인트를 사용할 수 있다.
blog_router = APIRouter(tags=["블로그"])
blog_router.include_router(read_router)
blog_router.include_router(modify_router)


__all__ = [
    "BlogResponse",
    "BlogWriteRequest",
    "SimpleBlogResponse",
    "blog_router",
    "delete_blog",
    "get_latest_blog",
    "get_user_blogs",
    "read_blog",
    "update_blog",
    "write_blog",
]
