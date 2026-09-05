import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from kbquant.config import settings

DATA_DIR = Path(__file__).parent.parent / "data" / "entities"

SQL = """
INSERT INTO entities (name, entity_type, normalized_name, aliases, metadata)
VALUES %s
ON CONFLICT (normalized_name, entity_type) DO UPDATE SET
    aliases = EXCLUDED.aliases,
    metadata = EXCLUDED.metadata
"""


def parse_db_url(url: str) -> dict:
    """Convert SQLAlchemy URL to psycopg2 connection parameters."""
    url_clean = re.sub(r"^postgresql\+[^:]+://", "postgresql://", url)
    parsed = urlparse(url_clean)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/").lstrip("/"),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def main():
    conn = psycopg2.connect(**parse_db_url(settings.database_url_sync))
    cur = conn.cursor()

    total = 0
    files = sorted(os.listdir(DATA_DIR))
    for filename in files:
        if not filename.endswith(".json"):
            continue

        filepath = DATA_DIR / filename
        with open(filepath, "r", encoding="utf-8") as f:
            entities = json.load(f)

        rows = []
        for e in entities:
            normalized = e["name"].lower().strip()
            aliases = e.get("aliases") or []
            metadata = json.dumps(e.get("metadata") or {}, ensure_ascii=False)
            rows.append((e["name"], e["entity_type"], normalized, aliases, metadata))

        execute_values(cur, SQL, rows, template="(%s, %s, %s, %s::text[], %s::jsonb)")
        total += len(rows)
        print(f"  {filename}: {len(rows)} 条")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n完成，共入库 {total} 条实体。")


if __name__ == "__main__":
    main()
