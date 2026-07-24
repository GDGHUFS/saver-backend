"""Run one intelligent-search query from a shell."""
from __future__ import annotations

import argparse
import asyncio
import json

from dotenv import load_dotenv

from src.search.engine import IntelligentSearchEngine
from src.search.engine.answer import generate_answer


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Saver intelligent search query runner")
    parser.add_argument("query", help="검색할 자연어 질문")
    parser.add_argument("--verbose", action="store_true", help="실행 메타데이터와 검색 결과를 출력합니다.")
    return parser.parse_args()


async def _run(query: str, verbose: bool) -> None:
    engine = IntelligentSearchEngine()
    try:
        response = await engine.search(query)
        print(await generate_answer(engine, query, response))
        if verbose:
            print("\n--- 실행 메타데이터 ---")
            print(
                json.dumps(
                    response.execution_metadata,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            print("\n--- 검색 결과 / provider ---")
            print(
                json.dumps(
                    [
                        {
                            "title": item.title,
                            "provider": item.provider_id,
                            "url": item.url,
                            "snippet": item.snippet,
                            "score": item.score,
                        }
                        for item in response.results
                    ],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
    finally:
        await engine.aclose()


def main() -> None:
    args = _arguments()
    load_dotenv()
    asyncio.run(_run(args.query, args.verbose))


if __name__ == "__main__":
    main()
