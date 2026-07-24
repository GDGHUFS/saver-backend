"""Run one intelligent-search query from a shell."""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from dotenv import load_dotenv

from src.search.engine import IntelligentSearchEngine


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Saver intelligent search query runner")
    parser.add_argument("query", help="검색할 자연어 질문")
    parser.add_argument("--verbose", action="store_true", help="실행 메타데이터와 검색 결과를 출력합니다.")
    return parser.parse_args()


async def _answer(engine: IntelligentSearchEngine, query: str, response: Any) -> str:
    if not response.results:
        metadata = response.execution_metadata
        if metadata.get("requested_provider_targets") and not metadata.get("selected_provider_targets"):
            return "요청한 외부 검색 서비스가 설정되지 않았습니다. API 키 설정을 확인해 주세요."
        if metadata.get("failed_providers"):
            return "외부 검색 서비스 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
        return "관련된 신뢰할 수 있는 검색 결과를 찾지 못했습니다. 검색어를 더 구체적으로 입력해 주세요."

    client = engine._owned_llm_client
    if client is None:
        return response.results[0].snippet
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
                "content": "한국어 검색 도우미다. 제공된 검색 근거만 사용해 짧고 정확하게 답한다.",
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
    return completion.choices[0].message.content or response.results[0].snippet


async def _run(query: str, verbose: bool) -> None:
    engine = IntelligentSearchEngine()
    response = await engine.search(query)
    print(await _answer(engine, query, response))
    if verbose:
        print("\n--- 실행 메타데이터 ---")
        print(json.dumps(response.execution_metadata, ensure_ascii=False, indent=2, default=str))
        print("\n--- 검색 결과 / provider ---")
        print(json.dumps([
            {
                "title": item.title,
                "provider": item.provider_id,
                "url": item.url,
                "snippet": item.snippet,
                "score": item.score,
            }
            for item in response.results
        ], ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = _arguments()
    load_dotenv()
    asyncio.run(_run(args.query, args.verbose))


if __name__ == "__main__":
    main()
