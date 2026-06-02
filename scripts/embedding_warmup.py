from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.entity_search import (
    CategoryEntityResolver,
    CityEntityResolver,
    StateEntityResolver,
    StoreNameEntityResolver,
    SubclassEntityResolver,
)
from src.llm import run_embedding


def _count_cache_rows(path: Path) -> list[tuple[str, int]]:
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """
                SELECT column_name, COUNT(*)
                FROM entity_embedding_cache
                GROUP BY column_name
                ORDER BY column_name
                """
            ).fetchall()
    except sqlite3.DatabaseError:
        return []
    return [(str(column), int(count)) for column, count in rows]


def _resolver_map(settings):
    common = {
        "cache_path": settings.entity_state_cache_path,
        "cache_ttl_seconds": settings.entity_state_cache_ttl_seconds,
        "similarity_threshold": settings.entity_state_similarity_threshold,
        "embedding_model": settings.embedding_model,
    }
    return {
        "state": StateEntityResolver(**common),
        "city": CityEntityResolver(**common),
        "store": StoreNameEntityResolver(**common),
        "category": CategoryEntityResolver(**common),
        "subclass": SubclassEntityResolver(**common),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test embeddings and warm AI-DA entity caches."
    )
    parser.add_argument(
        "--columns",
        default="state,city,category",
        help=(
            "Comma-separated resolvers to warm. Available: "
            "state,city,store,category,subclass. Default: state,city,category"
        ),
    )
    parser.add_argument(
        "--query",
        default="sales in karnataka for shirts",
        help="Sample text used for embedding and resolver checks.",
    )
    parser.add_argument(
        "--skip-provider-smoke",
        action="store_true",
        help="Skip direct embedding provider smoke test.",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"embedding_model={settings.embedding_model}")
    print(f"entity_cache_path={settings.entity_state_cache_path}")
    print(f"entity_search_enabled={settings.entity_search_enabled}")

    if not settings.entity_search_enabled:
        print("ENTITY_SEARCH_ENABLED=false; nothing to warm.")
        return 0

    if not args.skip_provider_smoke:
        start = time.perf_counter()
        vector = run_embedding(text=args.query, model=settings.embedding_model)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        print(f"provider_smoke=ok dimensions={len(vector)} elapsed_ms={elapsed_ms}")

    requested = [item.strip().lower() for item in args.columns.split(",") if item.strip()]
    resolvers = _resolver_map(settings)
    invalid = sorted(set(requested) - set(resolvers))
    if invalid:
        raise SystemExit(f"Unknown resolver(s): {', '.join(invalid)}")

    for name in requested:
        resolver = resolvers[name]
        start = time.perf_counter()
        match = resolver.resolve(args.query)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if match:
            print(
                f"{name}=ok column={match.column} value={match.value} "
                f"score={match.score:.3f} source={match.source} elapsed_ms={elapsed_ms}"
            )
        else:
            print(f"{name}=no_match elapsed_ms={elapsed_ms}")

    counts = _count_cache_rows(settings.entity_state_cache_path)
    if counts:
        print("cache_counts:")
        for column, count in counts:
            print(f"  {column}: {count}")
    else:
        print("cache_counts: none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
