"""검색 엔진 결과를 사용자에게 보여 줄 최종 답변으로 변환한다."""
from __future__ import annotations

import json
from typing import Any


async def generate_answer(engine: Any, query: str, response: Any) -> str:
    """CLI와 비동기 worker가 공유하는 최종 답변을 생성한다."""
    if not response.results:
        metadata = response.execution_metadata
        if metadata.get("requested_provider_targets") and not metadata.get(
            "selected_provider_targets"
        ):
            return (
                "요청한 외부 검색 서비스가 설정되지 않았습니다. "
                "API 키 설정을 확인해 주세요."
            )
        if metadata.get("failed_providers"):
            return "외부 검색 서비스 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
        return (
            "관련된 신뢰할 수 있는 검색 결과를 찾지 못했습니다. "
            "검색어를 더 구체적으로 입력해 주세요."
        )

    fallback = (
        response.results[0].snippet.strip()
        or response.results[0].title.strip()
        or "검색 결과는 찾았지만 요약 답변을 만들지 못했습니다."
    )
    client = getattr(engine, "_owned_llm_client", None)
    if client is None:
        return fallback

    context = [
        {"title": item.title, "snippet": item.snippet, "url": item.url}
        for item in response.results[:8]
    ]
    completion = await client.chat.completions.create(
        model=engine.analyzer.model,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "한국어 검색 도우미다. 제공된 검색 근거만 사용해 "
                    "짧고 정확하게 답한다."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"question": query, "search_results": context},
                    ensure_ascii=False,
                ),
            },
        ],
    )
    content = completion.choices[0].message.content
    return content.strip() if isinstance(content, str) and content.strip() else fallback
