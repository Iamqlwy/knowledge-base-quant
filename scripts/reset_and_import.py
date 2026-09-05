import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from kbquant.config import settings

# 删除顺序要尊重外键依赖：先删引用表，再删被引用表
TRUNCATE_ORDER = [
    "information_entities",
    "entity_relationships",
    "information_dedups",
    "conflict_detections",
    "importance_rankings",
    "time_validities",
    "processing_queue",
    "node_states",
    "node_attachments",
    "analyses",
    "feedbacks",
    "trading_operations",
    "macro_reports",
    "structured_preferences",
    "industry_cognitions",
    "market_cognitions",
    "raw_information",
    "world_nodes",
    "entities",
]

ES_INDICES = [
    f"{settings.elasticsearch_index_prefix}_raw_info",
    f"{settings.elasticsearch_index_prefix}_analyses",
    f"{settings.elasticsearch_index_prefix}_feedbacks",
    f"{settings.elasticsearch_index_prefix}_nodes",
    f"{settings.elasticsearch_index_prefix}_node_states",
]


def parse_db_url(url: str) -> dict:
    url_clean = re.sub(r"^postgresql\+[^:]+://", "postgresql://", url)
    parsed = urlparse(url_clean)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/").lstrip("/"),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


async def delete_es_indices():
    es_url = settings.elasticsearch_url
    async with httpx.AsyncClient() as client:
        for idx in ES_INDICES:
            resp = await client.delete(f"{es_url}/{idx}")
            if resp.status_code in (200, 404):
                print(f"  ES {idx}: 已删除")
            else:
                print(f"  ES {idx}: 删除失败 {resp.status_code} {resp.text[:200]}")


def main():
    # 1. Delete ES indices
    print("删除 Elasticsearch 索引...")
    asyncio.run(delete_es_indices())

    # 2. Truncate DB tables
    print("\n清空数据库表...")
    db_params = parse_db_url(settings.database_url_sync)
    conn = psycopg2.connect(**db_params)
    cur = conn.cursor()

    for table in TRUNCATE_ORDER:
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
        print(f"  {table}: 已清空")

    conn.commit()
    cur.close()
    conn.close()
    print("清空完成。\n")

    # 3. Re-import entities from data/entities/*.json
    from scripts.import_entities import main as import_main
    import_main()


if __name__ == "__main__":
    main()
