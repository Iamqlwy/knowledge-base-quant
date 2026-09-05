"""
Phase 0: Backup world_nodes, world_node_edges, node_states to CSV files.

Usage:
    uv run python nodes/phase0_backup.py
"""
import asyncio
import csv
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from nodes.common import ensure_backup_dir, get_engine, write_session

BACKUP_TABLES = ["world_nodes", "world_node_edges", "node_states"]


async def backup_table(table_name: str, backup_dir: Path) -> int:
    """Export a table to CSV. Returns row count."""
    engine = get_engine()
    sf = write_session(engine)

    async with sf() as session:
        result = await session.execute(text(f"SELECT * FROM {table_name}"))
        rows = result.fetchall()
        columns = list(result.keys())

    await engine.dispose()

    if not rows:
        print(f"  {table_name}: 0 rows (empty table)")
        return 0

    csv_path = backup_dir / f"{table_name}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)

    print(f"  {table_name}: {len(rows)} rows → {csv_path}")
    return len(rows)


async def main():
    backup_dir = ensure_backup_dir()
    print(f"Phase 0: 备份到 {backup_dir}")
    print("-" * 50)

    total = 0
    for table in BACKUP_TABLES:
        count = await backup_table(table, backup_dir)
        total += count

    print("-" * 50)
    print(f"备份完成: {total} 条记录 (3 张表)")


if __name__ == "__main__":
    asyncio.run(main())
